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
                "count(*) FILTER (WHERE document_role='motion_text') AS originals, "
                "count(*) FILTER (WHERE document_role='municipal_response') AS responses, "
                "count(*) FILTER (WHERE document_role='resolution') AS resolutions "
                "FROM documents WHERE document_id LIKE 'veytaux\\_motion\\_%%' ESCAPE '\\'"
            )
            documents = dict(cursor.fetchone())
            cursor.execute(
                "SELECT count(*) AS chunks, count(embedding) AS vectors, "
                "min(vector_dims(embedding)) AS min_dimension, max(vector_dims(embedding)) AS max_dimension "
                "FROM chunks WHERE document_id LIKE 'veytaux\\_motion\\_%%' ESCAPE '\\'"
            )
            chunks = dict(cursor.fetchone())
            cursor.execute(
                "SELECT DISTINCT "
                "metadata->'additional_metadata'->'motion_metadata'->>'object_id' AS object_id, "
                "(metadata->'additional_metadata'->'motion_metadata'->>'has_response')::boolean AS has_response "
                "FROM documents WHERE document_id LIKE 'veytaux\\_motion\\_%%' ESCAPE '\\' "
                "ORDER BY object_id"
            )
            objects = cursor.fetchall()
            cursor.execute(
                "SELECT count(*) AS broken_links FROM ("
                "SELECT DISTINCT jsonb_array_elements_text("
                "d.metadata->'additional_metadata'->'relationships'->'related_document_ids') AS related_id "
                "FROM documents d WHERE d.document_id LIKE 'veytaux\\_motion\\_%%' ESCAPE '\\'"
                ") links LEFT JOIN documents target ON target.document_id=links.related_id "
                "WHERE target.document_id IS NULL"
            )
            broken = cursor.fetchone()["broken_links"]

    from app import pilot_v2_store

    retrieval_specs = [
        ("subvention pour la pratique d'une activité sportive des enfants", "veytaux_motion_motion-g-1730801217_"),
        ("règlement participation soins dentaires et orthodontiques", "veytaux_motion_motion-m-1730801205_"),
        ("ECO point ancien hangar à bois place de lavage", "veytaux_motion_motion-de-jean-marc-emery-ecopoint-1651579448_"),
    ]
    retrieval = []
    for query, prefix in retrieval_specs:
        rows = pilot_v2_store.search(
            query,
            limit=10,
            filters={"city": "Veytaux", "doc_type": "motions"},
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
        "documents": 9,
        "originals": 6,
        "responses": 3,
        "resolutions": 0,
        "chunks": 21,
        "vectors": 21,
        "min_dimension": 1024,
        "max_dimension": 1024,
        "political_objects": 6,
        "objects_with_response": 3,
        "objects_without_response": 3,
        "broken_relationship_links": 0,
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
