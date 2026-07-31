from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PILOT_ROOT = PROJECT_ROOT / "embedding-pilot"
INPUT_PATH = (
    PROJECT_ROOT
    / "audit-vevey"
    / "combined-interpellations-audit"
    / "embedding"
    / "embedding_inputs.jsonl"
)
VECTOR_PATH = INPUT_PATH.with_name("mistral_embeddings.jsonl")
MANIFEST_PATH = INPUT_PATH.with_name("manifest.json")


def load_local_environment() -> None:
    for env_path in (PILOT_ROOT / ".env",):
        if not env_path.exists():
            continue
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(format(value, ".9g") for value in vector) + "]"


def source_record(row: dict) -> dict:
    source_path = PROJECT_ROOT / str(row.get("source_metadata_file") or "")
    if not source_path.is_file():
        raise ValueError(f"Missing source metadata: {source_path}")
    return json.loads(source_path.read_text(encoding="utf-8"))


def document_metadata(row: dict, record: dict) -> dict:
    general = dict(record.get("document_metadata") or {})
    if general.get("commune") != "Vevey":
        raise ValueError(
            f"Refusing non-Vevey document {row['document_id']}: "
            f"{general.get('commune')!r}"
        )
    if general.get("category") != "interpellation":
        raise ValueError(
            f"Refusing non-interpellation document {row['document_id']}: "
            f"{general.get('category')!r}"
        )
    if general.get("document_id") != row["document_id"]:
        raise ValueError(f"Document ID mismatch for {row['document_id']}")
    return {
        **general,
        "embedding_recipe": row.get("embedding_recipe"),
        "source_metadata_file": row.get("source_metadata_file"),
        "additional_metadata": {
            key: value
            for key, value in record.items()
            if key not in {"document_metadata", "processing"}
        },
    }


def validate_inputs(inputs: list[dict], vector_rows: list[dict]) -> dict[str, dict]:
    if not inputs:
        raise ValueError("No embedding inputs")
    vectors = {row["chunk_id"]: row for row in vector_rows}
    input_ids = {row["chunk_id"] for row in inputs}
    if set(vectors) != input_ids:
        raise ValueError("Embedding IDs do not match input IDs")
    if any(row.get("dimension") != 1024 for row in vector_rows):
        raise ValueError("Unexpected embedding dimension")
    if len(input_ids) != len(inputs):
        raise ValueError("Duplicate chunk IDs")
    if any(not str(row["document_id"]).startswith("vevey_") for row in inputs):
        raise ValueError("Refusing a non-Vevey document ID")
    return vectors


def main() -> None:
    load_local_environment()
    url = os.environ.get("POSTGRES_V2_URL", "")
    if not url:
        raise SystemExit("POSTGRES_V2_URL is missing")

    import psycopg

    inputs = read_jsonl(INPUT_PATH)
    vector_rows = read_jsonl(VECTOR_PATH)
    vectors = validate_inputs(inputs, vector_rows)
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if not manifest.get("complete"):
        raise SystemExit("Embedding manifest is incomplete")

    documents: dict[str, tuple[dict, dict]] = {}
    for row in inputs:
        if row["document_id"] not in documents:
            record = source_record(row)
            documents[row["document_id"]] = (row, document_metadata(row, record))
    # Une copie d'un texte peut être dédupliquée au niveau des chunks tout en
    # restant la cible officielle d'une relation réponse -> interpellation.
    # Conserve alors sa fiche documentaire (sans dupliquer ses vecteurs).
    linked_object_ids = {
        str(
            (source_record(row).get("relationships") or {}).get(
                "political_object_id"
            )
            or ""
        )
        for row in inputs
        if row.get("document_role") == "municipal_response"
    }
    for object_id in sorted(linked_object_ids - set(documents) - {""}):
        metadata_path = (
            PROJECT_ROOT
            / "audit-vevey"
            / "interpellation-response-links-2021-2026"
            / "political_objects"
            / f"{object_id}.json"
        )
        if not metadata_path.is_file():
            raise ValueError(f"Missing linked political object: {object_id}")
        record = json.loads(metadata_path.read_text(encoding="utf-8"))
        base = record["document_metadata"]
        synthetic_row = {
            "document_id": object_id,
            "document_family": base["document_family"],
            "category": base["category"],
            "document_role": base["document_role"],
            "title": base["title"],
            "embedding_recipe": "political_object",
            "source_metadata_file": str(
                metadata_path.relative_to(PROJECT_ROOT)
            ).replace("\\", "/"),
        }
        documents[object_id] = (
            synthetic_row,
            document_metadata(synthetic_row, record),
        )
    target_document_ids = sorted(documents)

    run_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc)

    with psycopg.connect(url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM documents "
                "WHERE category='interpellation' "
                "AND metadata->>'commune' = 'Vevey'"
            )
            before_documents = cursor.fetchone()[0]
            cursor.execute(
                "SELECT count(*) FROM chunks c JOIN documents d USING (document_id) "
                "WHERE d.category='interpellation' "
                "AND d.metadata->>'commune' = 'Vevey'"
            )
            before_chunks = cursor.fetchone()[0]

            cursor.execute(
                "INSERT INTO embedding_runs "
                "(run_id, model_name, model_dimension, recipe_version, started_at, "
                "status, input_chunks, tokens_used) "
                "VALUES (%s, %s, 1024, %s, %s, 'loading', %s, %s)",
                (
                    run_id,
                    manifest["model"],
                    "vevey-interpellations-v1",
                    started_at,
                    len(inputs),
                    int(manifest.get("tokens_reported_this_run", 0)),
                ),
            )

            for row, metadata in documents.values():
                cursor.execute(
                    "INSERT INTO documents "
                    "(document_id, document_family, category, document_role, title, metadata) "
                    "VALUES (%s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (document_id) DO UPDATE SET "
                    "document_family=EXCLUDED.document_family, "
                    "category=EXCLUDED.category, "
                    "document_role=EXCLUDED.document_role, "
                    "title=EXCLUDED.title, metadata=EXCLUDED.metadata",
                    (
                        row["document_id"],
                        row.get("document_family"),
                        row["category"],
                        row.get("document_role"),
                        row["title"],
                        json.dumps(metadata, ensure_ascii=False),
                    ),
                )

            # Recharge exactement les chunks du corpus courant et retire les
            # anciennes interpellations Vevey devenues doublons ou hors scope.
            cursor.execute(
                "DELETE FROM chunks WHERE document_id = ANY(%s)",
                (target_document_ids,),
            )
            cursor.execute(
                "SELECT document_id FROM documents "
                "WHERE category='interpellation' "
                "AND metadata->>'commune'='Vevey' "
                "AND NOT (document_id = ANY(%s))",
                (target_document_ids,),
            )
            stale_document_ids = [row[0] for row in cursor.fetchall()]
            if stale_document_ids:
                cursor.execute(
                    "DELETE FROM chunks WHERE document_id = ANY(%s)",
                    (stale_document_ids,),
                )
                cursor.execute(
                    "DELETE FROM documents WHERE document_id = ANY(%s)",
                    (stale_document_ids,),
                )

            for offset in range(0, len(inputs), 100):
                batch = inputs[offset : offset + 100]
                for row in batch:
                    vector = vectors[row["chunk_id"]]
                    chunk_metadata = {
                        "word_count": row.get("word_count"),
                        "article_title": row.get("article_title"),
                        "article_number": (row.get("embedding_fields") or {}).get(
                            "article_number"
                        ),
                        "response_number": row.get("response_number"),
                        "source_chunk_file": row.get("source_chunk_file"),
                        "commune": "Vevey",
                    }
                    cursor.execute(
                        "INSERT INTO chunks "
                        "(chunk_id, document_id, chunk_index, component, content, "
                        "content_hash, embedding_input, embedding, embedding_model, "
                        "embedding_run_id, metadata) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s::vector, %s, %s, %s) "
                        "ON CONFLICT (chunk_id) DO UPDATE SET "
                        "document_id=EXCLUDED.document_id, "
                        "chunk_index=EXCLUDED.chunk_index, "
                        "component=EXCLUDED.component, content=EXCLUDED.content, "
                        "content_hash=EXCLUDED.content_hash, "
                        "embedding_input=EXCLUDED.embedding_input, "
                        "embedding=EXCLUDED.embedding, "
                        "embedding_model=EXCLUDED.embedding_model, "
                        "embedding_run_id=EXCLUDED.embedding_run_id, "
                        "metadata=EXCLUDED.metadata",
                        (
                            row["chunk_id"],
                            row["document_id"],
                            int(row["chunk_index"]),
                            row.get("component"),
                            row["content"],
                            row["content_hash"],
                            row["embedding_input"],
                            vector_literal(vector["embedding"]),
                            vector["model"],
                            run_id,
                            json.dumps(chunk_metadata, ensure_ascii=False),
                        ),
                    )
                print(f"{min(offset + len(batch), len(inputs))}/{len(inputs)} chunks")

            cursor.execute(
                "UPDATE embedding_runs SET status='completed', completed_at=now() "
                "WHERE run_id=%s",
                (run_id,),
            )
            cursor.execute("ANALYZE documents")
            cursor.execute("ANALYZE chunks")
        connection.commit()

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM documents "
                "WHERE category='interpellation' "
                "AND metadata->>'commune' = 'Vevey'"
            )
            after_documents = cursor.fetchone()[0]
            cursor.execute(
                "SELECT count(*) FROM chunks c JOIN documents d USING (document_id) "
                "WHERE d.category='interpellation' "
                "AND d.metadata->>'commune' = 'Vevey'"
            )
            after_chunks = cursor.fetchone()[0]
            cursor.execute(
                "SELECT count(*) FROM chunks c JOIN documents d USING (document_id) "
                "WHERE d.category='interpellation' "
                "AND d.metadata->>'commune' = 'Vevey' "
                "AND c.embedding IS NOT NULL"
            )
            after_vectors = cursor.fetchone()[0]

    expected_documents = len(documents)
    expected_chunks = len(inputs)
    if (after_documents, after_chunks, after_vectors) != (
        expected_documents,
        expected_chunks,
        expected_chunks,
    ):
        raise SystemExit(
            "Post-load count mismatch: "
            f"documents={after_documents}/{expected_documents}, "
            f"chunks={after_chunks}/{expected_chunks}, "
            f"vectors={after_vectors}/{expected_chunks}"
        )
    print(
        json.dumps(
            {
                "run_id": run_id,
                "before": {
                    "documents": before_documents,
                    "chunks": before_chunks,
                },
                "after": {
                    "documents": after_documents,
                    "chunks": after_chunks,
                    "vectors": after_vectors,
                },
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
