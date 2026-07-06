from __future__ import annotations

import json
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INPUT_PATH = ROOT / "output" / "embedding_inputs.jsonl"
VECTOR_PATH = ROOT / "output" / "embeddings" / "mistral_embeddings.jsonl"
MANIFEST_PATH = ROOT / "output" / "embeddings" / "manifest.json"
CONTAINER = "ai-riviera-embedding-pilot-db"
DATABASE = "ai_riviera_embedding_pilot"
USER = "pilot"


def rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def literal(value: object) -> str:
    if value is None:
        return "NULL"
    return "'" + str(value).replace("'", "''") + "'"


def json_literal(value: object) -> str:
    return literal(json.dumps(value, ensure_ascii=False, separators=(",", ":"))) + "::jsonb"


def execute_sql(statements) -> str:
    process = subprocess.Popen(
        ["docker", "exec", "-i", CONTAINER, "psql", "-v", "ON_ERROR_STOP=1", "-U", USER, "-d", DATABASE],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
    )
    assert process.stdin is not None
    try:
        for statement in statements:
            process.stdin.write(statement)
        process.stdin.close()
        output = process.stdout.read() if process.stdout else ""
        return_code = process.wait()
    except Exception:
        process.kill()
        raise
    if return_code:
        raise RuntimeError(output[-4000:])
    return output


def build_statements(inputs: list[dict], vectors: dict[str, dict], manifest: dict, run_id: str):
    now = datetime.now(timezone.utc).isoformat()
    yield "BEGIN;\n"
    yield (
        "INSERT INTO embedding_runs "
        "(run_id,model_name,model_dimension,recipe_version,started_at,status,input_chunks,tokens_used) VALUES ("
        f"{literal(run_id)}::uuid,{literal(manifest['model'])},1024,'pilot-v1',{literal(now)}::timestamptz,"
        f"'loading',{len(inputs)},{int(manifest.get('tokens_reported_this_run', 0))});\n"
    )
    documents: dict[str, dict] = {}
    for row in inputs:
        documents.setdefault(row["document_id"], row)
    for row in documents.values():
        metadata = {"embedding_recipe": row.get("embedding_recipe"), "source_metadata_file": row.get("source_metadata_file")}
        yield (
            "INSERT INTO documents (document_id,document_family,category,document_role,title,metadata) VALUES ("
            f"{literal(row['document_id'])},{literal(row['document_family'])},{literal(row['category'])},"
            f"{literal(row.get('document_role'))},{literal(row['title'])},{json_literal(metadata)}) "
            "ON CONFLICT (document_id) DO UPDATE SET document_family=EXCLUDED.document_family,"
            "category=EXCLUDED.category,document_role=EXCLUDED.document_role,title=EXCLUDED.title,metadata=EXCLUDED.metadata;\n"
        )
    for row in inputs:
        vector = vectors[row["chunk_id"]]
        metadata = {
            "word_count": row.get("word_count"), "article_title": row.get("article_title"),
            "response_number": row.get("response_number"), "source_chunk_file": row.get("source_chunk_file"),
        }
        vector_text = "[" + ",".join(format(value, ".9g") for value in vector["embedding"]) + "]"
        yield (
            "INSERT INTO chunks (chunk_id,document_id,chunk_index,component,content,content_hash,"
            "embedding_input,embedding,embedding_model,embedding_run_id,metadata) VALUES ("
            f"{literal(row['chunk_id'])},{literal(row['document_id'])},{int(row['chunk_index'])},"
            f"{literal(row.get('component'))},{literal(row['content'])},{literal(row['content_hash'])},"
            f"{literal(row['embedding_input'])},{literal(vector_text)}::vector,{literal(vector['model'])},"
            f"{literal(run_id)}::uuid,{json_literal(metadata)}) "
            "ON CONFLICT (chunk_id) DO UPDATE SET document_id=EXCLUDED.document_id,chunk_index=EXCLUDED.chunk_index,"
            "component=EXCLUDED.component,content=EXCLUDED.content,content_hash=EXCLUDED.content_hash,"
            "embedding_input=EXCLUDED.embedding_input,embedding=EXCLUDED.embedding,embedding_model=EXCLUDED.embedding_model,"
            "embedding_run_id=EXCLUDED.embedding_run_id,metadata=EXCLUDED.metadata;\n"
        )
    yield f"UPDATE embedding_runs SET status='completed',completed_at=now() WHERE run_id={literal(run_id)}::uuid;\n"
    yield "COMMIT;\n"
    yield "CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw ON chunks USING hnsw (embedding vector_cosine_ops);\n"
    yield "ANALYZE documents; ANALYZE chunks;\n"


def main() -> None:
    inputs = rows(INPUT_PATH)
    vector_rows = rows(VECTOR_PATH)
    vectors = {row["chunk_id"]: row for row in vector_rows}
    if set(vectors) != {row["chunk_id"] for row in inputs}:
        raise SystemExit("Embedding IDs do not match input IDs")
    if any(row["dimension"] != 1024 for row in vector_rows):
        raise SystemExit("Unexpected embedding dimension")
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    run_id = str(uuid.uuid4())
    execute_sql(build_statements(inputs, vectors, manifest, run_id))
    query = (
        "SELECT json_build_object('documents',(SELECT count(*) FROM documents),"
        "'chunks',(SELECT count(*) FROM chunks),'vectors',(SELECT count(embedding) FROM chunks),"
        "'runs',(SELECT count(*) FROM embedding_runs WHERE status='completed'))::text;\n"
    )
    print(execute_sql([query]).strip())


if __name__ == "__main__":
    main()
