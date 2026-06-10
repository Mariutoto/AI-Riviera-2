from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app.config import DOCUMENTS_ROOT
from app.ingestion_pipeline import load_metadata
from app.postgres_store import _connect, canonical_document_date, canonical_source_url, ensure_schema


def _document_values(text_path: Path, metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        "city": str(metadata.get("commune") or "La Tour-de-Peilz"),
        "source_url": canonical_source_url(metadata, text_path.as_posix()),
        "source_path": text_path.as_posix(),
        "doc_type": str(metadata.get("doc_type") or metadata.get("category") or ""),
        "title": str(metadata.get("title") or metadata.get("filename") or text_path.stem),
        "document_date": canonical_document_date(metadata),
        "metadata": json.dumps(metadata, ensure_ascii=False),
    }


def iter_text_files(root: Path):
    for path in sorted(root.rglob("*.txt")):
        if path.stat().st_size > 0:
            yield path


def sync_postgres_metadata(documents_root: Path = DOCUMENTS_ROOT) -> dict[str, Any]:
    ensure_schema()
    stats = {
        "documents_seen": 0,
        "documents_synced": 0,
        "documents_missing": 0,
        "chunks_synced": 0,
    }

    with _connect() as connection:
        with connection.cursor() as cursor:
            for text_path in iter_text_files(documents_root):
                stats["documents_seen"] += 1
                content = text_path.read_text(encoding="utf-8", errors="ignore")
                metadata = load_metadata(text_path, content=content)
                values = _document_values(text_path, metadata)

                cursor.execute(
                    """
                    UPDATE documents
                    SET
                        city = %(city)s,
                        source_path = %(source_path)s,
                        doc_type = %(doc_type)s,
                        title = %(title)s,
                        document_date = %(document_date)s,
                        metadata = %(metadata)s::jsonb,
                        updated_at = NOW()
                    WHERE source_url = %(source_url)s
                    RETURNING id
                    """,
                    values,
                )
                row = cursor.fetchone()
                if not row:
                    stats["documents_missing"] += 1
                    continue

                document_id = row["id"]
                cursor.execute(
                    """
                    UPDATE document_chunks
                    SET
                        city = %(city)s,
                        doc_type = %(doc_type)s,
                        title = %(title)s,
                        document_date = %(document_date)s,
                        source_url = %(source_url)s,
                        metadata = %(metadata)s::jsonb,
                        search_vector =
                            setweight(to_tsvector('french', coalesce(%(title)s, '')), 'A') ||
                            setweight(to_tsvector('french', coalesce(%(doc_type)s, '')), 'B') ||
                            setweight(to_tsvector('french', coalesce(content, '')), 'C') ||
                            setweight(to_tsvector('french', coalesce(%(metadata_text)s, '')), 'D'),
                        updated_at = NOW()
                    WHERE document_id = %(document_id)s
                    """,
                    {**values, "document_id": document_id, "metadata_text": values["metadata"]},
                )
                stats["documents_synced"] += 1
                stats["chunks_synced"] += cursor.rowcount

        connection.commit()

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync JSON metadata into Postgres without regenerating embeddings.")
    parser.add_argument("--documents-root", type=Path, default=DOCUMENTS_ROOT)
    args = parser.parse_args()
    stats = sync_postgres_metadata(args.documents_root)
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
