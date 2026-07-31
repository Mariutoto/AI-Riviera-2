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

    database_url = config_value("POSTGRES_V2_URL").strip() or config_value("POSTGRES_V2_URLS").strip()
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
                "count(*) FILTER (WHERE document_role='postulate_text') AS originals, "
                "count(*) FILTER (WHERE document_role='consideration_report') AS reports, "
                "count(*) FILTER (WHERE document_role='municipal_response') AS responses "
                "FROM documents WHERE document_id LIKE 'montreux_postulate_%%'"
            )
            documents = dict(cursor.fetchone())
            cursor.execute(
                "SELECT count(*) AS chunks, count(embedding) AS vectors, "
                "min(vector_dims(embedding)) AS min_dimension, max(vector_dims(embedding)) AS max_dimension "
                "FROM chunks WHERE document_id LIKE 'montreux_postulate_%%'"
            )
            chunks = dict(cursor.fetchone())
            cursor.execute(
                "SELECT count(*) AS broken_links FROM ("
                "SELECT DISTINCT jsonb_array_elements_text("
                "d.metadata->'additional_metadata'->'relationships'->'related_document_ids') AS related_id "
                "FROM documents d WHERE d.document_id LIKE 'montreux_postulate_%%'"
                ") links LEFT JOIN documents target ON target.document_id=links.related_id "
                "WHERE target.document_id IS NULL"
            )
            broken = cursor.fetchone()["broken_links"]

    from app import pilot_v2_store

    originals = pilot_v2_store.aggregate_authors({"city": "Montreux", "doc_type": "postulats"})
    answered = pilot_v2_store.answered_postulates({"city": "Montreux"})
    retrieval_specs = [
        ("Harcèlement de rue à Montreux", "montreux_postulate_2750_"),
        ("inventaire numérique patrimoine Montreux", "montreux_postulate_3287_"),
        ("gestion prévisionnelle centralisée personnel communal", "montreux_postulate_2923_"),
    ]
    retrieval = []
    for query, prefix in retrieval_specs:
        rows = pilot_v2_store.search(
            query,
            limit=10,
            filters={"city": "Montreux", "doc_type": "postulats"},
        )
        ids = [row["document_id"] for row in rows]
        retrieval.append(
            {"query": query, "expected_prefix": prefix, "matched": any(i.startswith(prefix) for i in ids), "top_document_ids": ids[:5]}
        )
    database = {
        **documents,
        **chunks,
        "broken_relationship_links": broken,
        "aggregate_originals": len(originals),
        "answered_postulates": len(answered),
        "answered_with_source_url": sum(bool(row.get("response_url")) for row in answered),
    }
    expected = {
        "documents": 144,
        "originals": 49,
        "reports": 90,
        "responses": 5,
        "chunks": 512,
        "vectors": 512,
        "min_dimension": 1024,
        "max_dimension": 1024,
        "broken_relationship_links": 0,
        "aggregate_originals": 49,
        "answered_postulates": 7,
        "answered_with_source_url": 7,
    }
    failures = [f"{key}: {database.get(key)} != {value}" for key, value in expected.items() if database.get(key) != value]
    failures.extend(f"retrieval: {row['query']}" for row in retrieval if not row["matched"])
    report = {
        "status": "failed" if failures else "passed",
        "database": database,
        "retrieval": retrieval,
        "failures": failures,
    }
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
