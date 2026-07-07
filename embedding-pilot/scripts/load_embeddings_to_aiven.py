from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INPUT_PATH = ROOT / "output" / "embedding_inputs.jsonl"
VECTOR_PATH = ROOT / "output" / "embeddings" / "mistral_embeddings.jsonl"
MANIFEST_PATH = ROOT / "output" / "embeddings" / "manifest.json"
PROJECT_ROOT = ROOT.parent


def load_env() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(format(value, ".9g") for value in vector) + "]"


def main() -> None:
    load_env()
    url = os.environ.get("POSTGRES_V2_URL", "")
    if not url:
        raise SystemExit("POSTGRES_V2_URL is missing")

    import psycopg

    inputs = rows(INPUT_PATH)
    vector_rows = rows(VECTOR_PATH)
    vectors = {row["chunk_id"]: row for row in vector_rows}
    if set(vectors) != {row["chunk_id"] for row in inputs}:
        raise SystemExit("Embedding IDs do not match input IDs")
    if any(row["dimension"] != 1024 for row in vector_rows):
        raise SystemExit("Unexpected embedding dimension")

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    run_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc)

    documents: dict[str, dict] = {}
    for row in inputs:
        documents.setdefault(row["document_id"], row)

    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO embedding_runs "
                "(run_id, model_name, model_dimension, recipe_version, started_at, status, input_chunks, tokens_used) "
                "VALUES (%s, %s, 1024, %s, %s, 'loading', %s, %s)",
                (
                    run_id, manifest["model"], "pilot-v1", started_at, len(inputs),
                    int(manifest.get("tokens_reported_this_run", 0)),
                ),
            )

            for row in documents.values():
                source_metadata_path = PROJECT_ROOT / str(row.get("source_metadata_file") or "")
                source_record = (
                    json.loads(source_metadata_path.read_text(encoding="utf-8"))
                    if source_metadata_path.is_file() else {}
                )
                metadata = {
                    **(source_record.get("document_metadata") or {}),
                    "embedding_recipe": row.get("embedding_recipe"),
                    "source_metadata_file": row.get("source_metadata_file"),
                    "additional_metadata": {
                        key: value for key, value in source_record.items()
                        if key not in {"document_metadata", "processing"}
                    },
                }
                cur.execute(
                    "INSERT INTO documents (document_id, document_family, category, document_role, title, metadata) "
                    "VALUES (%s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (document_id) DO UPDATE SET document_family=EXCLUDED.document_family, "
                    "category=EXCLUDED.category, document_role=EXCLUDED.document_role, title=EXCLUDED.title, "
                    "metadata=EXCLUDED.metadata",
                    (
                        row["document_id"], row.get("document_family"), row["category"],
                        row.get("document_role"), row["title"], json.dumps(metadata, ensure_ascii=False),
                    ),
                )

            for offset in range(0, len(inputs), 200):
                batch = inputs[offset : offset + 200]
                for row in batch:
                    vector = vectors[row["chunk_id"]]
                    chunk_metadata = {
                        "word_count": row.get("word_count"), "article_title": row.get("article_title"),
                        "response_number": row.get("response_number"), "source_chunk_file": row.get("source_chunk_file"),
                    }
                    cur.execute(
                        "INSERT INTO chunks (chunk_id, document_id, chunk_index, component, content, content_hash, "
                        "embedding_input, embedding, embedding_model, embedding_run_id, metadata) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s::vector, %s, %s, %s) "
                        "ON CONFLICT (chunk_id) DO UPDATE SET document_id=EXCLUDED.document_id, "
                        "chunk_index=EXCLUDED.chunk_index, component=EXCLUDED.component, content=EXCLUDED.content, "
                        "content_hash=EXCLUDED.content_hash, embedding_input=EXCLUDED.embedding_input, "
                        "embedding=EXCLUDED.embedding, embedding_model=EXCLUDED.embedding_model, "
                        "embedding_run_id=EXCLUDED.embedding_run_id, metadata=EXCLUDED.metadata",
                        (
                            row["chunk_id"], row["document_id"], int(row["chunk_index"]), row.get("component"),
                            row["content"], row["content_hash"], row["embedding_input"],
                            vector_literal(vector["embedding"]), vector["model"], run_id,
                            json.dumps(chunk_metadata, ensure_ascii=False),
                        ),
                    )
                print(f"{min(offset + len(batch), len(inputs))}/{len(inputs)} chunks", flush=True)

            cur.execute(
                "UPDATE embedding_runs SET status='completed', completed_at=now() WHERE run_id=%s",
                (run_id,),
            )
            cur.execute("CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw ON chunks USING hnsw (embedding vector_cosine_ops)")
            cur.execute("ANALYZE documents")
            cur.execute("ANALYZE chunks")
        conn.commit()

        with conn.cursor() as cur:
            cur.execute(
                "SELECT (SELECT count(*) FROM documents) AS documents, (SELECT count(*) FROM chunks) AS chunks, "
                "(SELECT count(embedding) FROM chunks) AS vectors, "
                "(SELECT count(*) FROM embedding_runs WHERE status='completed') AS runs"
            )
            print(cur.fetchone())


if __name__ == "__main__":
    main()
