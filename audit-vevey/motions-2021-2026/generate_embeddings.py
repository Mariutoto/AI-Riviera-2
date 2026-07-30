from __future__ import annotations

import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parents[1]
EMBEDDING_DIR = ROOT / "general-audit" / "embedding"
INPUT_PATH = EMBEDDING_DIR / "embedding_inputs.jsonl"
OUTPUT_PATH = EMBEDDING_DIR / "mistral_embeddings.jsonl"
MANIFEST_PATH = EMBEDDING_DIR / "manifest.json"
MODEL = "mistral-embed"


def rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def main() -> None:
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    from app.config import config_value

    api_key = config_value("MISTRAL_API_KEY").strip()
    if not api_key:
        raise SystemExit("MISTRAL_API_KEY missing")
    inputs = rows(INPUT_PATH)
    if not inputs:
        raise SystemExit("No embedding inputs")
    if any(record["validation_issues"] for record in inputs):
        raise SystemExit("Embedding inputs still contain validation issues")

    previous = {
        row["chunk_id"]: row for row in rows(OUTPUT_PATH)
    }
    retained: dict[str, dict] = {}
    for record in inputs:
        cached = previous.get(record["chunk_id"])
        expected_input_hash = hashlib.sha256(
            record["embedding_input"].encode("utf-8")
        ).hexdigest()
        if (
            cached
            and cached.get("content_hash") == record["content_hash"]
            and cached.get("embedding_input_hash") == expected_input_hash
            and cached.get("dimension") == 1024
        ):
            retained[record["chunk_id"]] = cached
    pending = [
        record
        for record in inputs
        if record["chunk_id"] not in retained
    ]
    results = dict(retained)
    total_tokens = 0
    started_at = datetime.now(timezone.utc).isoformat()
    for offset in range(0, len(pending), 16):
        batch = pending[offset : offset + 16]
        response = None
        for attempt in range(5):
            response = requests.post(
                "https://api.mistral.ai/v1/embeddings",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": MODEL,
                    "input": [
                        record["embedding_input"] for record in batch
                    ],
                },
                timeout=120,
            )
            if response.ok:
                break
            if response.status_code not in {429, 500, 502, 503, 504}:
                response.raise_for_status()
            if attempt == 4:
                response.raise_for_status()
            time.sleep(2**attempt)
        assert response is not None
        vectors = sorted(
            response.json()["data"], key=lambda item: item["index"]
        )
        if len(vectors) != len(batch):
            raise RuntimeError("Unexpected Mistral vector count")
        for record, vector in zip(batch, vectors):
            results[record["chunk_id"]] = {
                "chunk_id": record["chunk_id"],
                "document_id": record["document_id"],
                "content_hash": record["content_hash"],
                "embedding_input_hash": hashlib.sha256(
                    record["embedding_input"].encode("utf-8")
                ).hexdigest(),
                "model": MODEL,
                "dimension": len(vector["embedding"]),
                "embedding": vector["embedding"],
            }
        usage = response.json().get("usage") or {}
        total_tokens += int(
            usage.get("total_tokens", usage.get("prompt_tokens", 0)) or 0
        )
        print(
            f"{len(retained) + min(offset + len(batch), len(pending))}/"
            f"{len(inputs)} chunks",
            flush=True,
        )

    with OUTPUT_PATH.open("w", encoding="utf-8", newline="\n") as stream:
        for record in inputs:
            stream.write(
                json.dumps(
                    results[record["chunk_id"]], ensure_ascii=False
                )
                + "\n"
            )
    dimensions = sorted({row["dimension"] for row in results.values()})
    manifest = {
        "model": MODEL,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "started_at": started_at,
        "input_chunks": len(inputs),
        "embedded_chunks": len(results),
        "reused_chunks": len(retained),
        "new_chunks": len(pending),
        "unique_chunk_ids": len(results),
        "dimensions": dimensions,
        "tokens_reported_this_run": total_tokens,
        "complete": len(results) == len(inputs) and dimensions == [1024],
    }
    MANIFEST_PATH.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
