from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config import DOCUMENTS_ROOT
from app.people_index import display_name, is_person_name, normalize_name, person_key, slugify, split_person_names
from app.postgres_store import _connect, ensure_schema
from app.text_cleaning import clean_french_text, strip_accents


POLITICAL_OBJECT_TYPES = {"motion", "postulat", "interpellation"}
POLITICAL_OBJECT_CATEGORIES = {"motions", "postulats", "interpellations"}


@dataclass
class PoliticalObjectAccumulator:
    object_id: str
    city: str = "La Tour-de-Peilz"
    legislature: str = ""
    object_type: str = ""
    object_title: str = ""
    status_raw: str = ""
    status_normalized: str = ""
    deposit_date: str = ""
    decision_date: str = ""
    year: str = ""
    canonical_source_url: str = ""
    canonical_document_source_url: str = ""
    canonical_document_path: str = ""
    authors: dict[str, dict[str, Any]] = field(default_factory=dict)
    documents: dict[str, dict[str, Any]] = field(default_factory=dict)
    scheduled_sessions: dict[str, dict[str, Any]] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return None


def clean_value(value: Any) -> Any:
    if isinstance(value, str):
        return clean_french_text(value)
    if isinstance(value, list):
        return [clean_value(item) for item in value]
    if isinstance(value, dict):
        return {key: clean_value(item) for key, item in value.items()}
    return value


def source_url(metadata: dict[str, Any]) -> str:
    return str(metadata.get("pdf_url") or metadata.get("source_url") or metadata.get("url") or metadata.get("source_page") or "")


def object_type_from_metadata(metadata: dict[str, Any]) -> str:
    political_object = metadata.get("political_object") or {}
    value = (
        metadata.get("type")
        or metadata.get("document_type")
        or political_object.get("object_type")
        or political_object.get("type")
        or ""
    )
    return str(value).lower()


def should_use_metadata(metadata: dict[str, Any]) -> bool:
    object_type = object_type_from_metadata(metadata)
    category = str(metadata.get("category") or "").lower()
    if object_type in POLITICAL_OBJECT_TYPES or category in POLITICAL_OBJECT_CATEGORIES:
        return bool(metadata.get("political_object_id") or metadata.get("related_political_object_id") or metadata.get("canonical_object"))
    return False


def object_title_from_metadata(metadata: dict[str, Any]) -> str:
    political_object = metadata.get("political_object") or {}
    return str(
        metadata.get("object_title")
        or political_object.get("object_title")
        or metadata.get("site_subject")
        or political_object.get("source_subject")
        or metadata.get("summary")
        or metadata.get("title")
        or ""
    )


def infer_object_id(metadata: dict[str, Any], path: Path) -> str:
    political_object = metadata.get("political_object") or {}
    value = (
        metadata.get("political_object_id")
        or political_object.get("object_id")
        or metadata.get("related_political_object_id")
        or political_object.get("political_object_id")
    )
    if value:
        return str(value)
    object_type = object_type_from_metadata(metadata) or str(metadata.get("category") or "political-object")
    year = str(metadata.get("listing_year") or metadata.get("year") or path.parent.parent.name or "")
    return f"{object_type}-{year}-{slugify(object_title_from_metadata(metadata) or path.stem)}"


def normalize_author(raw_author: Any) -> list[dict[str, Any]]:
    if isinstance(raw_author, dict):
        raw_name = str(raw_author.get("name") or "").strip()
        party = str(raw_author.get("party") or "").strip()
        role = str(raw_author.get("role") or "author").strip() or "author"
        civility = str(raw_author.get("civility") or "").strip()
    else:
        raw_name = str(raw_author or "").strip()
        party = ""
        role = "author"
        civility = ""
    authors = []
    for split_name in split_person_names(raw_name):
        if not split_name or not is_person_name(split_name):
            continue
        name = display_name(split_name)
        person_id = slugify(normalize_name(name))
        if not person_id:
            continue
        authors.append(
            {
                "person_id": person_id,
                "name": name,
                "normalized_name": normalize_name(name),
                "party": party,
                "role": role,
                "civility": civility,
                "alias_key": person_key(name),
            }
        )
    return authors


def merge_authors(accumulator: PoliticalObjectAccumulator, metadata: dict[str, Any]) -> None:
    for raw_author in metadata.get("authors") or []:
        for author in normalize_author(raw_author):
            key = author["alias_key"]
            existing = accumulator.authors.get(key)
            if existing is None:
                accumulator.authors[key] = {
                    "person_id": author["person_id"],
                    "name": author["name"],
                    "normalized_name": author["normalized_name"],
                    "parties": [],
                    "roles": [],
                    "variants": [],
                }
                existing = accumulator.authors[key]
            if author["party"] and author["party"] not in existing["parties"]:
                existing["parties"].append(author["party"])
            if author["role"] and author["role"] not in existing["roles"]:
                existing["roles"].append(author["role"])
            if author["name"] and author["name"] not in existing["variants"]:
                existing["variants"].append(author["name"])


def document_record(metadata: dict[str, Any], path: Path, document_id_by_source_url: dict[str, str]) -> dict[str, Any]:
    url = source_url(metadata)
    return {
        "document_id": document_id_by_source_url.get(url, ""),
        "source_url": url,
        "source_path": str(path),
        "pdf_path": str(metadata.get("pdf_path") or ""),
        "text_path": str(metadata.get("text_path") or ""),
        "title": str(metadata.get("title") or ""),
        "filename": str(metadata.get("filename") or path.with_suffix(".pdf").name),
        "document_role": str(metadata.get("document_role") or ""),
        "document_components": metadata.get("document_components") or [],
        "document_date": str(metadata.get("document_date") or "")[:10],
        "canonical_object": bool(metadata.get("canonical_object")),
        "source_collection": str(metadata.get("source_collection") or ""),
        "contains_report": bool(metadata.get("contains_report")),
        "contains_decision": bool(metadata.get("contains_decision")),
        "contains_response": bool(metadata.get("contains_response")),
        "commission": metadata.get("commission") or {},
        "decision": metadata.get("decision") or {},
    }


def scheduled_session_records(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    records = []
    for item in metadata.get("scheduled_in_sessions") or []:
        if not isinstance(item, dict):
            continue
        records.append(
            {
                "session_date": str(item.get("session_date") or "")[:10],
                "agenda_item_number": str(item.get("agenda_item_number") or ""),
                "agenda_item_title": str(item.get("agenda_item_title") or ""),
                "agenda_source_url": str(item.get("agenda_source_url") or ""),
                "agenda_path": str(item.get("agenda_path") or ""),
            }
        )
    return records


def choose_text(current: str, candidate: str, prefer: bool = False) -> str:
    if prefer and candidate:
        return candidate
    if current:
        return current
    return candidate


def date_min(current: str, candidate: str) -> str:
    if not candidate:
        return current
    if not current:
        return candidate
    return min(current, candidate)


def merge_metadata(
    accumulator: PoliticalObjectAccumulator,
    metadata: dict[str, Any],
    path: Path,
    document_id_by_source_url: dict[str, str],
) -> None:
    political_object = metadata.get("political_object") or {}
    is_canonical = bool(metadata.get("canonical_object"))
    object_type = object_type_from_metadata(metadata)
    object_title = object_title_from_metadata(metadata)
    url = source_url(metadata)
    document = document_record(metadata, path, document_id_by_source_url)
    document_key = url or str(path)

    accumulator.city = choose_text(accumulator.city, str(metadata.get("commune") or "La Tour-de-Peilz"), is_canonical)
    accumulator.legislature = choose_text(accumulator.legislature, str(metadata.get("legislature") or ""), is_canonical)
    accumulator.object_type = choose_text(accumulator.object_type, object_type, is_canonical)
    accumulator.object_title = choose_text(accumulator.object_title, object_title, is_canonical)
    accumulator.status_raw = choose_text(
        accumulator.status_raw,
        str(metadata.get("site_status_raw") or political_object.get("status_raw") or metadata.get("status") or ""),
        is_canonical,
    )
    accumulator.status_normalized = choose_text(
        accumulator.status_normalized,
        str(metadata.get("status_normalized") or political_object.get("status_normalized") or ""),
        is_canonical,
    )
    accumulator.year = choose_text(accumulator.year, str(metadata.get("listing_year") or metadata.get("year") or ""), is_canonical)
    accumulator.canonical_source_url = choose_text(
        accumulator.canonical_source_url,
        str(political_object.get("canonical_source") or metadata.get("source_page") or ""),
        is_canonical,
    )
    if is_canonical:
        accumulator.canonical_document_source_url = url or accumulator.canonical_document_source_url
        accumulator.canonical_document_path = str(path)

    document_date = str(metadata.get("document_date") or political_object.get("document_date") or "")[:10]
    accumulator.deposit_date = date_min(accumulator.deposit_date, document_date)
    decision = metadata.get("decision") or {}
    decision_date = str(decision.get("decision_date") or decision.get("session_date") or "")[:10]
    accumulator.decision_date = choose_text(accumulator.decision_date, decision_date, bool(decision_date))

    merge_authors(accumulator, metadata)
    accumulator.documents[document_key] = document
    for session in scheduled_session_records(metadata):
        session_key = "#".join(
            [
                session.get("session_date", ""),
                session.get("agenda_item_number", ""),
                session.get("agenda_source_url", ""),
            ]
        )
        accumulator.scheduled_sessions[session_key] = session

    accumulator.metadata.update(
        {
            "has_canonical_document": bool(accumulator.canonical_document_source_url),
            "contains_report": bool(accumulator.metadata.get("contains_report") or metadata.get("contains_report")),
            "contains_decision": bool(accumulator.metadata.get("contains_decision") or metadata.get("contains_decision")),
            "contains_response": bool(accumulator.metadata.get("contains_response") or metadata.get("contains_response")),
            "contains_majority_report": bool(accumulator.metadata.get("contains_majority_report") or metadata.get("contains_majority_report")),
            "contains_minority_report": bool(accumulator.metadata.get("contains_minority_report") or metadata.get("contains_minority_report")),
        }
    )


def load_document_ids_by_source_url() -> dict[str, str]:
    try:
        with _connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT id::text, source_url FROM documents")
                return {str(row["source_url"]): str(row["id"]) for row in cursor.fetchall() if row.get("source_url")}
    except Exception:
        return {}


def build_political_objects(documents_root: Path = DOCUMENTS_ROOT) -> dict[str, PoliticalObjectAccumulator]:
    document_id_by_source_url = load_document_ids_by_source_url()
    objects: dict[str, PoliticalObjectAccumulator] = {}
    for path in sorted(documents_root.rglob("*.json")):
        metadata = read_json(path)
        if not metadata:
            continue
        metadata = clean_value(metadata)
        if not should_use_metadata(metadata):
            continue
        object_id = infer_object_id(metadata, path)
        if not object_id:
            continue
        accumulator = objects.get(object_id)
        if accumulator is None:
            accumulator = PoliticalObjectAccumulator(object_id=object_id)
            objects[object_id] = accumulator
        merge_metadata(accumulator, metadata, path, document_id_by_source_url)
    return objects


def political_object_rows(objects: dict[str, PoliticalObjectAccumulator]) -> list[dict[str, Any]]:
    rows = []
    for item in sorted(objects.values(), key=lambda value: (value.year, value.object_type, value.object_title)):
        authors = [
            {
                **author,
                "parties": sorted(author.get("parties") or []),
                "roles": sorted(author.get("roles") or []),
                "variants": sorted(author.get("variants") or []),
            }
            for author in item.authors.values()
        ]
        documents = sorted(
            item.documents.values(),
            key=lambda document: (
                not document.get("canonical_object"),
                document.get("document_date", ""),
                document.get("document_role", ""),
                document.get("source_url", ""),
            ),
        )
        sessions = sorted(
            item.scheduled_sessions.values(),
            key=lambda session: (session.get("session_date", ""), session.get("agenda_item_number", "")),
        )
        metadata = {
            **item.metadata,
            "author_count": len(authors),
            "document_count": len(documents),
            "scheduled_session_count": len(sessions),
            "canonical_source": "documents_metadata",
        }
        rows.append(
            {
                "object_id": item.object_id,
                "city": item.city,
                "legislature": item.legislature,
                "object_type": item.object_type,
                "object_title": item.object_title,
                "status_raw": item.status_raw,
                "status_normalized": item.status_normalized,
                "deposit_date": item.deposit_date or None,
                "decision_date": item.decision_date or None,
                "year": item.year,
                "canonical_source_url": item.canonical_source_url,
                "canonical_document_source_url": item.canonical_document_source_url,
                "canonical_document_path": item.canonical_document_path,
                "authors": authors,
                "documents": documents,
                "scheduled_sessions": sessions,
                "metadata": metadata,
            }
        )
    return rows


def upsert_political_objects(rows: list[dict[str, Any]]) -> None:
    ensure_schema()
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM political_objects")
            cursor.executemany(
                """
                INSERT INTO political_objects (
                    object_id, city, legislature, object_type, object_title,
                    status_raw, status_normalized, deposit_date, decision_date, year,
                    canonical_source_url, canonical_document_source_url, canonical_document_path,
                    authors, documents, scheduled_sessions, metadata
                )
                VALUES (
                    %(object_id)s, %(city)s, %(legislature)s, %(object_type)s, %(object_title)s,
                    %(status_raw)s, %(status_normalized)s, %(deposit_date)s, %(decision_date)s, %(year)s,
                    %(canonical_source_url)s, %(canonical_document_source_url)s, %(canonical_document_path)s,
                    %(authors)s::jsonb, %(documents)s::jsonb, %(scheduled_sessions)s::jsonb, %(metadata)s::jsonb
                )
                ON CONFLICT (object_id) DO UPDATE
                SET
                    city = EXCLUDED.city,
                    legislature = EXCLUDED.legislature,
                    object_type = EXCLUDED.object_type,
                    object_title = EXCLUDED.object_title,
                    status_raw = EXCLUDED.status_raw,
                    status_normalized = EXCLUDED.status_normalized,
                    deposit_date = EXCLUDED.deposit_date,
                    decision_date = EXCLUDED.decision_date,
                    year = EXCLUDED.year,
                    canonical_source_url = EXCLUDED.canonical_source_url,
                    canonical_document_source_url = EXCLUDED.canonical_document_source_url,
                    canonical_document_path = EXCLUDED.canonical_document_path,
                    authors = EXCLUDED.authors,
                    documents = EXCLUDED.documents,
                    scheduled_sessions = EXCLUDED.scheduled_sessions,
                    metadata = EXCLUDED.metadata,
                    updated_at = NOW()
                """,
                [
                    {
                        **row,
                        "authors": json.dumps(row["authors"], ensure_ascii=False),
                        "documents": json.dumps(row["documents"], ensure_ascii=False),
                        "scheduled_sessions": json.dumps(row["scheduled_sessions"], ensure_ascii=False),
                        "metadata": json.dumps(row["metadata"], ensure_ascii=False),
                    }
                    for row in rows
                ],
            )
        connection.commit()


def rebuild_political_objects_index(documents_root: Path = DOCUMENTS_ROOT) -> dict[str, Any]:
    objects = build_political_objects(documents_root)
    rows = political_object_rows(objects)
    upsert_political_objects(rows)
    return {
        "political_objects": len(rows),
        "by_type": {
            object_type: sum(1 for row in rows if row["object_type"] == object_type)
            for object_type in sorted({row["object_type"] for row in rows})
        },
        "documents_linked": sum(len(row["documents"]) for row in rows),
        "scheduled_sessions_linked": sum(len(row["scheduled_sessions"]) for row in rows),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the political objects index from document metadata.")
    parser.add_argument("--documents-root", type=Path, default=DOCUMENTS_ROOT)
    args = parser.parse_args()
    stats = rebuild_political_objects_index(args.documents_root)
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
