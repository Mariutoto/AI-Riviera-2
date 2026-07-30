from __future__ import annotations

import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parents[1]
REPORT_PATH = ROOT / "general-audit" / "database-validation.json"


def main() -> None:
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    from app.config import config_value

    database_url = (
        config_value("POSTGRES_V2_URL").strip()
        or config_value("POSTGRES_V2_URLS").strip()
    )
    mistral_key = config_value("MISTRAL_API_KEY").strip()
    if not database_url or not mistral_key:
        raise SystemExit("Database or Mistral configuration missing")
    os.environ["POSTGRES_V2_URL"] = database_url
    os.environ["MISTRAL_API_KEY"] = mistral_key

    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) AS documents, "
                "count(*) FILTER (WHERE document_role='motion_text') "
                "AS original_motions "
                "FROM documents WHERE document_id LIKE 'vevey_motion_%%'"
            )
            document_counts = cursor.fetchone()
            cursor.execute(
                "SELECT count(*) AS chunks, count(embedding) AS vectors, "
                "min(vector_dims(embedding)) AS min_dimension, "
                "max(vector_dims(embedding)) AS max_dimension "
                "FROM chunks WHERE document_id LIKE 'vevey_motion_%%'"
            )
            chunk_counts = cursor.fetchone()
            cursor.execute(
                "SELECT DISTINCT "
                "metadata->'additional_metadata'->'motion_metadata'"
                "->>'object_id' AS object_id, "
                "metadata->'additional_metadata'->'motion_metadata'"
                "->>'object_title' AS title, "
                "(metadata->'additional_metadata'->'motion_metadata'"
                "->>'has_response')::boolean AS has_response, "
                "metadata->'additional_metadata'->'motion_metadata'"
                "->>'response_status' AS response_status "
                "FROM documents "
                "WHERE document_id LIKE 'vevey_motion_%%' "
                "ORDER BY object_id"
            )
            objects = cursor.fetchall()
            cursor.execute(
                "SELECT count(*) AS broken_links FROM ("
                "SELECT DISTINCT jsonb_array_elements_text("
                "d.metadata->'additional_metadata'->'relationships'"
                "->'related_document_ids') AS related_id "
                "FROM documents d "
                "WHERE d.document_id LIKE 'vevey_motion_%%'"
                ") links LEFT JOIN documents target "
                "ON target.document_id=links.related_id "
                "WHERE target.document_id IS NULL"
            )
            broken_links = cursor.fetchone()["broken_links"]

    from app import pilot_v2_store

    queries = [
        (
            "congé menstruel ménopause règlement du personnel",
            "vevey_motion_5527_",
        ),
        (
            "diminuer le nombre d'élus du Conseil communal",
            "vevey_motion_5740_",
        ),
        (
            "précarité énergétique locataires fonds d'urgence",
            "vevey_motion_5063_",
        ),
        (
            "Soyons à l'écoute stationnement veveysans",
            "vevey_motion_5692_",
        ),
    ]
    retrieval = []
    for query, expected_prefix in queries:
        results = pilot_v2_store.search(
            query,
            limit=10,
            filters={"city": "Vevey", "doc_type": "motions"},
        )
        returned_ids = [result["document_id"] for result in results]
        matched = any(
            document_id.startswith(expected_prefix)
            for document_id in returned_ids
        )
        retrieval.append(
            {
                "query": query,
                "expected_prefix": expected_prefix,
                "matched": matched,
                "top_document_ids": returned_ids[:5],
            }
        )

    report = {
        "status": "passed",
        "database": {
            **dict(document_counts),
            **dict(chunk_counts),
            "political_objects": len(objects),
            "objects_with_response": sum(
                bool(item["has_response"]) for item in objects
            ),
            "objects_without_response": sum(
                not bool(item["has_response"]) for item in objects
            ),
            "broken_relationship_links": broken_links,
        },
        "objects": [dict(item) for item in objects],
        "retrieval": retrieval,
    }
    expected_database = {
        "documents": 10,
        "original_motions": 4,
        "chunks": 32,
        "vectors": 32,
        "min_dimension": 1024,
        "max_dimension": 1024,
        "political_objects": 4,
        "objects_with_response": 2,
        "objects_without_response": 2,
        "broken_relationship_links": 0,
    }
    failures = [
        f"{key}: {report['database'].get(key)} != {expected}"
        for key, expected in expected_database.items()
        if report["database"].get(key) != expected
    ]
    failures.extend(
        f"retrieval: {item['query']}"
        for item in retrieval
        if not item["matched"]
    )
    if failures:
        report["status"] = "failed"
        report["failures"] = failures
    REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
