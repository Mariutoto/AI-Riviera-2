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
                "count(*) FILTER (WHERE document_role='municipal_response') AS responses, "
                "count(*) FILTER (WHERE document_role='consideration_report') AS reports, "
                "count(*) FILTER (WHERE document_role='council_decision') AS decisions, "
                "count(*) FILTER (WHERE document_role='committee_formation') AS committees "
                "FROM documents WHERE document_id LIKE 'blonay-saint-legier\\_postulate\\_%%' ESCAPE '\\'"
            )
            documents = dict(cursor.fetchone())
            cursor.execute(
                "SELECT count(*) AS chunks, count(embedding) AS vectors, "
                "min(vector_dims(embedding)) AS min_dimension, max(vector_dims(embedding)) AS max_dimension "
                "FROM chunks WHERE document_id LIKE 'blonay-saint-legier\\_postulate\\_%%' ESCAPE '\\'"
            )
            chunks = dict(cursor.fetchone())
            cursor.execute(
                "SELECT DISTINCT "
                "metadata->'additional_metadata'->'postulate_metadata'->>'object_id' AS object_id, "
                "metadata->'additional_metadata'->'postulate_metadata'->>'object_title' AS title, "
                "(metadata->'additional_metadata'->'postulate_metadata'->>'has_response')::boolean AS has_response, "
                "metadata->'additional_metadata'->'postulate_metadata'->>'response_status' AS response_status "
                "FROM documents WHERE document_id LIKE 'blonay-saint-legier\\_postulate\\_%%' ESCAPE '\\' "
                "ORDER BY object_id"
            )
            objects = cursor.fetchall()
            cursor.execute(
                "SELECT count(*) AS broken_links FROM ("
                "SELECT DISTINCT jsonb_array_elements_text("
                "d.metadata->'additional_metadata'->'relationships'->'related_document_ids') AS related_id "
                "FROM documents d WHERE d.document_id LIKE 'blonay-saint-legier\\_postulate\\_%%' ESCAPE '\\'"
                ") links LEFT JOIN documents target ON target.document_id=links.related_id "
                "WHERE target.document_id IS NULL"
            )
            broken = cursor.fetchone()["broken_links"]

    from app import pilot_v2_store

    retrieval_specs = [
        ("gouvernance ASR problèmes collaborateurs", "blonay-saint-legier_postulate_2106193_"),
        ("moratoire zones 30 km-h", "blonay-saint-legier_postulate_2153698_"),
        ("déchetterie pragmatique", "blonay-saint-legier_postulate_2605975_"),
        ("arrêts de bus confort sécurité", "blonay-saint-legier_postulate_2822794_"),
    ]
    retrieval = []
    for query, prefix in retrieval_specs:
        rows = pilot_v2_store.search(
            query,
            limit=10,
            filters={"city": "Blonay–Saint-Légier", "doc_type": "postulats"},
        )
        ids = [row["document_id"] for row in rows]
        retrieval.append(
            {"query": query, "expected_prefix": prefix, "matched": any(i.startswith(prefix) for i in ids), "top_document_ids": ids[:5]}
        )

    database = {
        **documents,
        **chunks,
        "political_objects": len(objects),
        "objects_with_response": sum(bool(item["has_response"]) for item in objects),
        "objects_without_response": sum(not bool(item["has_response"]) for item in objects),
        "broken_relationship_links": broken,
    }
    expected = {
        "documents": 16,
        "originals": 7,
        "responses": 5,
        "reports": 1,
        "decisions": 2,
        "committees": 1,
        "chunks": 70,
        "vectors": 70,
        "min_dimension": 1024,
        "max_dimension": 1024,
        "political_objects": 7,
        "objects_with_response": 5,
        "objects_without_response": 2,
        "broken_relationship_links": 0,
    }
    failures = [f"{key}: {database.get(key)} != {value}" for key, value in expected.items() if database.get(key) != value]
    failures.extend(f"retrieval: {row['query']}" for row in retrieval if not row["matched"])
    report = {
        "status": "failed" if failures else "passed",
        "database": database,
        "objects": [dict(item) for item in objects],
        "retrieval": retrieval,
        "failures": failures,
    }
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
