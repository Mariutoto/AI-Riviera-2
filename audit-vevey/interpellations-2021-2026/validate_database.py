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
    if not database_url:
        raise SystemExit("Database configuration missing")
    os.environ["POSTGRES_V2_URL"] = database_url

    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) AS documents, "
                "count(*) FILTER (WHERE document_role='interpellation_text') AS originals, "
                "count(*) FILTER (WHERE document_role='combined_interpellation_response') AS combined, "
                "count(*) FILTER (WHERE document_role='municipal_response') AS responses "
                "FROM documents WHERE category='interpellation' "
                "AND metadata->>'commune'='Vevey'"
            )
            document_counts = dict(cursor.fetchone())
            cursor.execute(
                "SELECT count(*) AS chunks, count(embedding) AS vectors, "
                "min(vector_dims(embedding)) AS min_dimension, "
                "max(vector_dims(embedding)) AS max_dimension "
                "FROM chunks c JOIN documents d USING (document_id) "
                "WHERE d.category='interpellation' "
                "AND d.metadata->>'commune'='Vevey'"
            )
            chunk_counts = dict(cursor.fetchone())
            cursor.execute(
                "SELECT metadata->>'listing_year' AS listing_year, count(*) AS documents "
                "FROM documents WHERE category='interpellation' "
                "AND metadata->>'commune'='Vevey' "
                "AND document_role<>'municipal_response' "
                "GROUP BY 1 ORDER BY 1"
            )
            objects_by_year = {
                row["listing_year"]: row["documents"]
                for row in cursor.fetchall()
            }
            cursor.execute(
                "SELECT count(*) AS broken FROM documents response "
                "LEFT JOIN documents object ON object.document_id="
                "response.metadata->'additional_metadata'->'relationships'"
                "->>'political_object_id' "
                "WHERE response.category='interpellation' "
                "AND response.document_role='municipal_response' "
                "AND response.metadata->>'commune'='Vevey' "
                "AND response.metadata->'additional_metadata'->'relationships'"
                "->>'political_object_id' IS NOT NULL "
                "AND object.document_id IS NULL"
            )
            broken_relationships = cursor.fetchone()["broken"]

    from app.pilot_v2_store import answered_interpellations

    answered_by_year = {
        year: len(
            answered_interpellations(
                {"city": "Vevey", "response_year": year}
            )
        )
        for year in ("2021", "2022", "2023", "2024", "2025", "2026")
    }
    report = {
        "status": "passed",
        "database": {
            **document_counts,
            **chunk_counts,
            "broken_relationships": broken_relationships,
        },
        "objects_by_year": objects_by_year,
        "answered_by_year": answered_by_year,
    }
    expected = {
        "documents": 199,
        "originals": 110,
        "combined": 14,
        "responses": 75,
        "chunks": 516,
        "vectors": 516,
        "min_dimension": 1024,
        "max_dimension": 1024,
        "broken_relationships": 0,
    }
    failures = [
        f"{key}: {report['database'].get(key)} != {value}"
        for key, value in expected.items()
        if report["database"].get(key) != value
    ]
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
