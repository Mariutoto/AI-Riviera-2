from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import requests


ROOT = Path(__file__).resolve().parents[1]
CONTAINER = "ai-riviera-embedding-pilot-db"
DATABASE = "ai_riviera_embedding_pilot"
USER = "pilot"


def load_env() -> None:
    for raw_line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if raw_line.strip() and not raw_line.lstrip().startswith("#") and "=" in raw_line:
            key, value = raw_line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Recherche sémantique dans la base pgvector pilote")
    parser.add_argument("query")
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()
    load_env()
    response = requests.post(
        "https://api.mistral.ai/v1/embeddings",
        headers={"Authorization": f"Bearer {os.environ['MISTRAL_API_KEY']}"},
        json={"model": "mistral-embed", "input": [args.query]},
        timeout=60,
    )
    response.raise_for_status()
    vector = response.json()["data"][0]["embedding"]
    vector_text = "[" + ",".join(format(value, ".9g") for value in vector) + "]"
    sql = f"""
        SELECT json_build_object(
            'rank', row_number() OVER (ORDER BY c.embedding <=> '{vector_text}'::vector),
            'score', round((1 - (c.embedding <=> '{vector_text}'::vector))::numeric, 4),
            'category', d.category,
            'title', d.title,
            'component', c.component,
            'chunk_id', c.chunk_id,
            'excerpt', left(regexp_replace(c.content, E'[\\n\\r]+', ' ', 'g'), 220)
        )
        FROM chunks c JOIN documents d USING (document_id)
        ORDER BY c.embedding <=> '{vector_text}'::vector
        LIMIT {max(1, min(args.limit, 50))};
    """
    result = subprocess.run(
        ["docker", "exec", "-i", CONTAINER, "psql", "-X", "-A", "-t", "-U", USER, "-d", DATABASE],
        input=sql, text=True, encoding="utf-8", capture_output=True, check=True,
    )
    for line in result.stdout.splitlines():
        if line.strip():
            print(json.dumps(json.loads(line), ensure_ascii=False))


if __name__ == "__main__":
    main()
