"""Drop the approximate HNSW index on chunks.embedding if it still exists.

bb48f8e1 stopped the loaders from *creating* this index (it was silently
dropping the correct top match on filtered searches, e.g. category =
'interpellation' where only ~3.5% of chunks qualify) but never dropped the
index that was already live on Aiven. Exact search over ~7k chunks costs
~100ms, which is negligible next to the LLM calls already in the pipeline.

Safe to re-run: a no-op if the index is already gone.
"""
from __future__ import annotations

import os


def load_env() -> None:
    from pathlib import Path

    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main() -> None:
    load_env()
    url = os.environ.get("POSTGRES_V2_URL", "")
    if not url:
        raise SystemExit("POSTGRES_V2_URL is missing")

    import psycopg

    with psycopg.connect(url) as conn, conn.cursor() as cur:
        cur.execute("DROP INDEX IF EXISTS chunks_embedding_hnsw")
        conn.commit()
        cur.execute("SELECT indexname FROM pg_indexes WHERE tablename = 'chunks'")
        print("Remaining indexes on chunks:")
        for (name,) in cur.fetchall():
            print(f"  - {name}")


if __name__ == "__main__":
    main()
