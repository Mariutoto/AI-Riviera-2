from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests


ROOT = Path(__file__).resolve().parents[1]
INPUT_PATH = ROOT / "output" / "embedding_inputs.jsonl"
OUTPUT_DIR = ROOT / "output" / "embeddings"
OUTPUT_PATH = OUTPUT_DIR / "mistral_embeddings.jsonl"
MANIFEST_PATH = OUTPUT_DIR / "manifest.json"
API_URL = "https://api.mistral.ai/v1/embeddings"
MODEL = "mistral-embed"


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


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def existing_ids() -> set[str]:
    if not OUTPUT_PATH.exists():
        return set()
    return {row["chunk_id"] for row in read_jsonl(OUTPUT_PATH)}


def request_batch(api_key: str, rows: list[dict], retries: int = 5) -> tuple[list[list[float]], dict]:
    payload = {"model": MODEL, "input": [row["embedding_input"] for row in rows]}
    for attempt in range(retries):
        response = requests.post(
            API_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=120,
        )
        if response.ok:
            body = response.json()
            ordered = sorted(body["data"], key=lambda item: item["index"])
            return [item["embedding"] for item in ordered], body.get("usage", {})
        if response.status_code not in {429, 500, 502, 503, 504} or attempt == retries - 1:
            raise RuntimeError(f"Mistral API error {response.status_code}: {response.text[:500]}")
        time.sleep(2**attempt)
    raise RuntimeError("Mistral API request failed")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=16)
    args = parser.parse_args()
    load_env()
    api_key = os.environ.get("MISTRAL_API_KEY", "")
    if not api_key or api_key == "REMPLACE_PAR_TA_CLE_MISTRAL":
        raise SystemExit("MISTRAL_API_KEY is missing")

    inputs = read_jsonl(INPUT_PATH)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    done = existing_ids()
    pending = [row for row in inputs if row["chunk_id"] not in done]
    total_tokens = 0
    started_at = datetime.now(timezone.utc).isoformat()

    with OUTPUT_PATH.open("a", encoding="utf-8") as output:
        for offset in range(0, len(pending), args.batch_size):
            batch = pending[offset : offset + args.batch_size]
            vectors, usage = request_batch(api_key, batch)
            if len(vectors) != len(batch):
                raise RuntimeError("Mistral returned an unexpected vector count")
            for row, vector in zip(batch, vectors):
                output.write(json.dumps({
                    "chunk_id": row["chunk_id"],
                    "document_id": row["document_id"],
                    "content_hash": row["content_hash"],
                    "model": MODEL,
                    "dimension": len(vector),
                    "embedding": vector,
                }, ensure_ascii=False) + "\n")
            output.flush()
            total_tokens += int(usage.get("total_tokens", usage.get("prompt_tokens", 0)) or 0)
            completed = len(done) + min(offset + len(batch), len(pending))
            print(f"{completed}/{len(inputs)} chunks", flush=True)

    results = read_jsonl(OUTPUT_PATH)
    dimensions = sorted({row["dimension"] for row in results})
    manifest = {
        "model": MODEL,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "started_at": started_at,
        "input_file": str(INPUT_PATH.relative_to(ROOT)),
        "output_file": str(OUTPUT_PATH.relative_to(ROOT)),
        "input_chunks": len(inputs),
        "embedded_chunks": len(results),
        "unique_chunk_ids": len({row["chunk_id"] for row in results}),
        "dimensions": dimensions,
        "tokens_reported_this_run": total_tokens,
        "complete": len(results) == len(inputs),
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
