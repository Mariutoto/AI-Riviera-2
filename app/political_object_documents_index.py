from __future__ import annotations

import argparse
import json
from typing import Any

from app.political_objects_index import rebuild_political_objects_index
from app.postgres_store import _connect, ensure_schema


def infer_relation_type(document: dict[str, Any]) -> str:
    if document.get("canonical_object"):
        document_role = str(document.get("document_role") or "").strip()
        return f"canonical_{document_role}" if document_role else "canonical"

    document_role = str(document.get("document_role") or "").strip()
    if document_role:
        return document_role

    components = document.get("document_components") or []
    if isinstance(components, list):
        component_roles = [str(component.get("role") or "") for component in components if isinstance(component, dict)]
        if "council_decision" in component_roles and "commission_report" in component_roles:
            return "report_decision"
        if "municipal_response" in component_roles:
            return "response"
        if "commission_report" in component_roles:
            return "commission_report"

    if document.get("contains_response"):
        return "response"
    if document.get("contains_decision") and document.get("contains_report"):
        return "report_decision"
    if document.get("contains_decision"):
        return "decision"
    if document.get("contains_report"):
        return "commission_report"
    if str(document.get("source_collection") or "") == "ordre-du-jour-linked-document":
        return "agenda_linked_document"
    return "supporting_document"


def load_political_objects() -> list[dict[str, Any]]:
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT object_id, object_type, object_title, year, documents
                FROM political_objects
                ORDER BY year, object_type, object_title, object_id
                """
            )
            return list(cursor.fetchall())


def relation_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for political_object in load_political_objects():
        documents = political_object.get("documents") or []
        if not isinstance(documents, list):
            continue
        for index, document in enumerate(documents):
            if not isinstance(document, dict):
                continue
            relation_type = infer_relation_type(document)
            rows.append(
                {
                    "object_id": str(political_object["object_id"]),
                    "document_id": str(document.get("document_id") or "") or None,
                    "relation_type": relation_type,
                    "source_url": str(document.get("source_url") or ""),
                    "source_path": str(document.get("source_path") or ""),
                    "title": str(document.get("title") or ""),
                    "filename": str(document.get("filename") or ""),
                    "document_date": str(document.get("document_date") or "")[:10] or None,
                    "order_index": index,
                    "metadata": {
                        "object_type": political_object.get("object_type", ""),
                        "object_title": political_object.get("object_title", ""),
                        "year": political_object.get("year", ""),
                        "pdf_path": document.get("pdf_path", ""),
                        "text_path": document.get("text_path", ""),
                        "document_role": document.get("document_role", ""),
                        "document_components": document.get("document_components") or [],
                        "canonical_object": bool(document.get("canonical_object")),
                        "source_collection": document.get("source_collection", ""),
                        "contains_report": bool(document.get("contains_report")),
                        "contains_decision": bool(document.get("contains_decision")),
                        "contains_response": bool(document.get("contains_response")),
                        "canonical_source": "political_objects.documents",
                    },
                }
            )
    return rows


def upsert_relations(rows: list[dict[str, Any]]) -> None:
    ensure_schema()
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM political_object_documents")
            cursor.executemany(
                """
                INSERT INTO political_object_documents (
                    object_id, document_id, relation_type, source_url, source_path,
                    title, filename, document_date, order_index, metadata
                )
                VALUES (
                    %(object_id)s, %(document_id)s, %(relation_type)s, %(source_url)s, %(source_path)s,
                    %(title)s, %(filename)s, %(document_date)s, %(order_index)s, %(metadata)s::jsonb
                )
                ON CONFLICT (object_id, source_url, source_path, relation_type) DO UPDATE
                SET
                    document_id = EXCLUDED.document_id,
                    title = EXCLUDED.title,
                    filename = EXCLUDED.filename,
                    document_date = EXCLUDED.document_date,
                    order_index = EXCLUDED.order_index,
                    metadata = EXCLUDED.metadata,
                    updated_at = NOW()
                """,
                [
                    {
                        **row,
                        "metadata": json.dumps(row["metadata"], ensure_ascii=False),
                    }
                    for row in rows
                ],
            )
        connection.commit()


def rebuild_political_object_documents_index(rebuild_objects: bool = False) -> dict[str, Any]:
    ensure_schema()
    if rebuild_objects:
        rebuild_political_objects_index()
    rows = relation_rows()
    upsert_relations(rows)
    return {
        "political_object_documents": len(rows),
        "relation_types": {
            relation_type: sum(1 for row in rows if row["relation_type"] == relation_type)
            for relation_type in sorted({row["relation_type"] for row in rows})
        },
        "with_document_id": sum(1 for row in rows if row["document_id"]),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build political object/document relations from political_objects.")
    parser.add_argument("--rebuild-objects", action="store_true", help="Rebuild political_objects before relations.")
    args = parser.parse_args()
    stats = rebuild_political_object_documents_index(rebuild_objects=args.rebuild_objects)
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
