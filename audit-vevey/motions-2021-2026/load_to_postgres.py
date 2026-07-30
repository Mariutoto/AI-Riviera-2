from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parents[1]
EMBEDDING_DIR = ROOT / "general-audit" / "embedding"
INPUT_PATH = EMBEDDING_DIR / "embedding_inputs.jsonl"
VECTOR_PATH = EMBEDDING_DIR / "mistral_embeddings.jsonl"
MANIFEST_PATH = EMBEDDING_DIR / "manifest.json"


def read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(format(value, ".9g") for value in vector) + "]"


def source_record(row: dict) -> dict:
    path = PROJECT_ROOT / str(row.get("source_metadata_file") or "")
    if not path.is_file():
        raise ValueError(f"Missing source metadata: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def document_metadata(row: dict, record: dict) -> dict:
    general = dict(record.get("document_metadata") or {})
    if general.get("commune") != "Vevey":
        raise ValueError(f"Refusing non-Vevey document {row['document_id']}")
    if general.get("category") != "motion":
        raise ValueError(f"Refusing non-motion document {row['document_id']}")
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


def validate_inputs(
    inputs: list[dict], vector_rows: list[dict]
) -> dict[str, dict]:
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
    if any(
        not str(row["document_id"]).startswith("vevey_motion_")
        for row in inputs
    ):
        raise ValueError("Refusing a non-Vevey-motion document ID")
    if any(row.get("category") != "motion" for row in inputs):
        raise ValueError("Refusing a non-motion category")
    return vectors


def main() -> None:
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    from app.config import config_value

    database_url = (
        config_value("POSTGRES_V2_URL").strip()
        or config_value("POSTGRES_V2_URLS").strip()
    )
    if not database_url:
        raise SystemExit("POSTGRES_V2_URL is missing")

    import psycopg

    inputs = read_jsonl(INPUT_PATH)
    vector_rows = read_jsonl(VECTOR_PATH)
    vectors = validate_inputs(inputs, vector_rows)
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if not manifest.get("complete"):
        raise SystemExit("Embedding manifest is incomplete")

    documents: dict[str, tuple[dict, dict, dict]] = {}
    for row in inputs:
        if row["document_id"] not in documents:
            record = source_record(row)
            documents[row["document_id"]] = (
                row,
                document_metadata(row, record),
                record,
            )
    document_ids = sorted(documents)
    chunk_ids = sorted(row["chunk_id"] for row in inputs)
    run_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc)

    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM documents "
                "WHERE NOT (document_id = ANY(%s))",
                (document_ids,),
            )
            unrelated_documents_before = cursor.fetchone()[0]
            cursor.execute(
                "SELECT count(*) FROM chunks "
                "WHERE NOT (document_id = ANY(%s))",
                (document_ids,),
            )
            unrelated_chunks_before = cursor.fetchone()[0]
            cursor.execute(
                "SELECT count(*) FROM documents "
                "WHERE document_id = ANY(%s)",
                (document_ids,),
            )
            target_documents_before = cursor.fetchone()[0]
            cursor.execute(
                "SELECT count(*) FROM chunks "
                "WHERE document_id = ANY(%s)",
                (document_ids,),
            )
            target_chunks_before = cursor.fetchone()[0]

            cursor.execute(
                "INSERT INTO embedding_runs "
                "(run_id, model_name, model_dimension, recipe_version, "
                "started_at, status, input_chunks, tokens_used) "
                "VALUES (%s, %s, 1024, %s, %s, 'loading', %s, %s)",
                (
                    run_id,
                    manifest["model"],
                    "vevey-motions-v1",
                    started_at,
                    len(inputs),
                    int(
                        manifest.get("tokens_reported_this_run", 0)
                    ),
                ),
            )

            for row, metadata, source in documents.values():
                motion = source["motion_metadata"]
                summary = (
                    f"{motion['status']}. "
                    f"{motion.get('requested_action', '')}"
                ).strip()
                cursor.execute(
                    "INSERT INTO documents "
                    "(document_id, document_family, category, document_role, "
                    "title, summary, metadata) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (document_id) DO UPDATE SET "
                    "document_family=EXCLUDED.document_family, "
                    "category=EXCLUDED.category, "
                    "document_role=EXCLUDED.document_role, "
                    "title=EXCLUDED.title, summary=EXCLUDED.summary, "
                    "metadata=EXCLUDED.metadata",
                    (
                        row["document_id"],
                        row["document_family"],
                        row["category"],
                        row["document_role"],
                        row["title"],
                        summary,
                        json.dumps(metadata, ensure_ascii=False),
                    ),
                )

            # Remove only stale chunks belonging to the exact motion
            # documents in this manifest. No other Vevey data is touched.
            cursor.execute(
                "DELETE FROM chunks WHERE document_id = ANY(%s) "
                "AND NOT (chunk_id = ANY(%s))",
                (document_ids, chunk_ids),
            )
            stale_chunks_removed = cursor.rowcount

            for offset in range(0, len(inputs), 100):
                batch = inputs[offset : offset + 100]
                for row in batch:
                    vector = vectors[row["chunk_id"]]
                    chunk_metadata = {
                        "word_count": row.get("word_count"),
                        "source_chunk_file": row.get("source_chunk_file"),
                        "commune": "Vevey",
                        "category": "motion",
                    }
                    cursor.execute(
                        "INSERT INTO chunks "
                        "(chunk_id, document_id, chunk_index, component, "
                        "content, content_hash, embedding_input, embedding, "
                        "embedding_model, embedding_run_id, metadata) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s::vector, "
                        "%s, %s, %s) "
                        "ON CONFLICT (chunk_id) DO UPDATE SET "
                        "document_id=EXCLUDED.document_id, "
                        "chunk_index=EXCLUDED.chunk_index, "
                        "component=EXCLUDED.component, "
                        "content=EXCLUDED.content, "
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
                            row["component"],
                            row["content"],
                            row["content_hash"],
                            row["embedding_input"],
                            vector_literal(vector["embedding"]),
                            vector["model"],
                            run_id,
                            json.dumps(
                                chunk_metadata, ensure_ascii=False
                            ),
                        ),
                    )
                print(
                    f"{min(offset + len(batch), len(inputs))}/"
                    f"{len(inputs)} chunks"
                )

            cursor.execute(
                "SELECT count(*) FROM documents "
                "WHERE document_id = ANY(%s) "
                "AND category='motion' "
                "AND metadata->>'commune'='Vevey'",
                (document_ids,),
            )
            target_documents_after = cursor.fetchone()[0]
            cursor.execute(
                "SELECT count(*), count(embedding) FROM chunks "
                "WHERE document_id = ANY(%s)",
                (document_ids,),
            )
            target_chunks_after, target_vectors_after = cursor.fetchone()
            cursor.execute(
                "SELECT count(*) FROM documents "
                "WHERE NOT (document_id = ANY(%s))",
                (document_ids,),
            )
            unrelated_documents_after = cursor.fetchone()[0]
            cursor.execute(
                "SELECT count(*) FROM chunks "
                "WHERE NOT (document_id = ANY(%s))",
                (document_ids,),
            )
            unrelated_chunks_after = cursor.fetchone()[0]

            expected = (len(documents), len(inputs), len(inputs))
            actual = (
                target_documents_after,
                target_chunks_after,
                target_vectors_after,
            )
            if actual != expected:
                raise RuntimeError(
                    f"Post-load target mismatch: {actual} != {expected}"
                )
            if (
                unrelated_documents_before != unrelated_documents_after
                or unrelated_chunks_before != unrelated_chunks_after
            ):
                raise RuntimeError("Unrelated database rows changed")
            cursor.execute(
                "UPDATE embedding_runs SET status='completed', "
                "completed_at=now() WHERE run_id=%s",
                (run_id,),
            )
            cursor.execute("ANALYZE documents")
            cursor.execute("ANALYZE chunks")
        connection.commit()

    print(
        json.dumps(
            {
                "run_id": run_id,
                "target_before": {
                    "documents": target_documents_before,
                    "chunks": target_chunks_before,
                },
                "target_after": {
                    "documents": target_documents_after,
                    "chunks": target_chunks_after,
                    "vectors": target_vectors_after,
                },
                "stale_target_chunks_removed": stale_chunks_removed,
                "unrelated_rows_preserved": True,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
