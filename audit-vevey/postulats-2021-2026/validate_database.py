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
        raise SystemExit("Configuration PostgreSQL ou Mistral absente")
    os.environ["POSTGRES_V2_URL"] = database_url
    os.environ["MISTRAL_API_KEY"] = mistral_key

    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) AS documents, "
                "count(*) FILTER (WHERE document_role='postulate_text') "
                "AS original_postulates "
                "FROM documents WHERE document_id LIKE 'vevey_postulate_%%'"
            )
            document_counts = cursor.fetchone()
            cursor.execute(
                "SELECT count(*) AS chunks, count(embedding) AS vectors, "
                "min(vector_dims(embedding)) AS min_dimension, "
                "max(vector_dims(embedding)) AS max_dimension "
                "FROM chunks WHERE document_id LIKE 'vevey_postulate_%%'"
            )
            chunk_counts = cursor.fetchone()
            cursor.execute(
                "SELECT DISTINCT "
                "metadata->'additional_metadata'->'postulate_metadata'->>'object_id' "
                "AS object_id, "
                "metadata->'additional_metadata'->'postulate_metadata'->>'object_title' "
                "AS title, "
                "(metadata->'additional_metadata'->'postulate_metadata'->>'has_response')::boolean "
                "AS has_response "
                "FROM documents WHERE document_id LIKE 'vevey_postulate_%%' "
                "ORDER BY object_id"
            )
            objects = cursor.fetchall()
            cursor.execute(
                "SELECT count(*) AS broken_links FROM ("
                "SELECT DISTINCT jsonb_array_elements_text("
                "d.metadata->'additional_metadata'->'relationships'->'related_document_ids'"
                ") AS related_id FROM documents d "
                "WHERE d.document_id LIKE 'vevey_postulate_%%'"
                ") links LEFT JOIN documents target ON target.document_id=links.related_id "
                "WHERE target.document_id IS NULL"
            )
            broken_links = cursor.fetchone()["broken_links"]

    from app import pilot_v2_store

    all_rows = pilot_v2_store.aggregate_authors(
        {"city": "Vevey", "doc_type": "postulats"}
    )
    rows_2024 = pilot_v2_store.aggregate_authors(
        {"city": "Vevey", "doc_type": "postulats", "year": "2024"}
    )
    answered_2025 = pilot_v2_store.answered_postulates(
        {"city": "Vevey", "response_year": "2025"}
    )
    retrieval_specs = [
        ("précarité menstruelle protections réutilisables", "vevey_postulate_4736_"),
        ("non-recours aux prestations sociales", "vevey_postulate_4834_"),
        ("micro-forêts urbaines réchauffement climatique", "vevey_postulate_5289_"),
        ("Potaclos pôle urbain Avenue de Blonay", "vevey_postulate_6048_"),
    ]
    retrieval = []
    for query, expected_prefix in retrieval_specs:
        results = pilot_v2_store.search(
            query,
            limit=10,
            filters={"city": "Vevey", "doc_type": "postulats"},
        )
        document_ids = [row["document_id"] for row in results]
        retrieval.append(
            {
                "query": query,
                "expected_prefix": expected_prefix,
                "matched": any(row.startswith(expected_prefix) for row in document_ids),
                "top_document_ids": document_ids[:5],
            }
        )

    database = {
        **dict(document_counts),
        **dict(chunk_counts),
        "political_objects": len(objects),
        "objects_with_response": sum(bool(row["has_response"]) for row in objects),
        "objects_without_response": sum(not bool(row["has_response"]) for row in objects),
        "broken_relationship_links": broken_links,
        "aggregate_originals": len(all_rows),
        "aggregate_2024": len(rows_2024),
        "answered_in_2025": len(answered_2025),
        "answered_2025_response_pdfs": sum(
            bool(row.get("response_url")) for row in answered_2025
        ),
    }
    expected = {
        "documents": 44,
        "original_postulates": 30,
        "chunks": 185,
        "vectors": 185,
        "min_dimension": 1024,
        "max_dimension": 1024,
        "political_objects": 30,
        "objects_with_response": 5,
        "objects_without_response": 25,
        "broken_relationship_links": 0,
        "aggregate_originals": 30,
        "aggregate_2024": 4,
        "answered_in_2025": 3,
        "answered_2025_response_pdfs": 3,
    }
    failures = [
        f"{key}: {database.get(key)} != {value}"
        for key, value in expected.items()
        if database.get(key) != value
    ]
    failures.extend(
        f"retrieval: {row['query']}" for row in retrieval if not row["matched"]
    )
    report = {
        "status": "failed" if failures else "passed",
        "database": database,
        "objects": [dict(row) for row in objects],
        "retrieval": retrieval,
        "failures": failures,
    }
    REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
