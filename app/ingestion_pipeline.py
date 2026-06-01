from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

from app.config import DOCUMENTS_ROOT, POSTGRES_URL
from app.embeddings import embed_texts
from app.opensearch_store import delete_document, index_chunks
from app.postgres_store import (
    DocumentRecord,
    build_document_hash,
    canonical_document_date,
    canonical_fetch_date,
    canonical_source_url,
    delete_chunks_for_document,
    ensure_schema,
    finish_ingestion_run,
    get_document_by_source_url,
    get_or_create_city,
    insert_chunks,
    log_ingestion_event,
    sha256_text,
    start_ingestion_run,
    upsert_document,
)
from app.text_cleaning import clean_french_text


CHUNK_SIZE = 1200
CHUNK_OVERLAP = 180


def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    text = clean_french_text(text)
    if not text:
        return []

    chunks = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            boundary = max(text.rfind("\n", start, end), text.rfind(". ", start, end))
            if boundary > start + size // 2:
                end = boundary + 1

        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)

        if end >= len(text):
            break
        start = max(0, end - overlap)

    return chunks


def load_metadata(text_path: Path) -> dict[str, Any]:
    metadata_path = text_path.with_suffix(".json")
    if metadata_path.exists():
        try:
            return json.loads(metadata_path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError:
            pass

    return {
        "commune": "La Tour-de-Peilz",
        "year": text_path.parts[-3] if len(text_path.parts) >= 3 else "",
        "category": text_path.parts[-2] if len(text_path.parts) >= 2 else "",
        "filename": text_path.with_suffix(".pdf").name,
        "pdf_url": "",
        "source_page": "",
        "text_path": str(text_path),
    }


def iter_text_files(root: Path):
    for path in sorted(root.rglob("*.txt")):
        if path.stat().st_size > 0:
            yield path


def _document_payload(text_path: Path, metadata: dict[str, Any], content: str) -> dict[str, Any]:
    document_hash, content_hash = build_document_hash(metadata, content)
    city_name = metadata.get("commune", "La Tour-de-Peilz")
    source_url = canonical_source_url(metadata, text_path.as_posix())
    doc_type = str(metadata.get("doc_type") or metadata.get("category") or "")
    title = str(metadata.get("title") or metadata.get("filename") or text_path.stem)
    document_date = canonical_document_date(metadata)
    fetch_date = canonical_fetch_date(metadata) or datetime.now(timezone.utc).isoformat()
    last_processed_at = datetime.now(timezone.utc).isoformat()
    return {
        "city": city_name,
        "source_url": source_url,
        "source_path": text_path.as_posix(),
        "doc_type": doc_type,
        "title": title,
        "document_date": document_date,
        "fetch_date": fetch_date,
        "last_processed_at": last_processed_at,
        "document_hash": document_hash,
        "content_hash": content_hash,
        "metadata": metadata,
    }


def _searchable_text(metadata: dict[str, Any], chunk: str) -> str:
    parts = [
        f"City: {metadata.get('commune', '')}",
        f"Year: {metadata.get('year', '')}",
        f"Category: {metadata.get('category', '')}",
        f"Title: {metadata.get('title', '')}",
        f"Filename: {metadata.get('filename', '')}",
        f"Source URL: {metadata.get('pdf_url') or metadata.get('url') or metadata.get('source_page') or ''}",
    ]
    if metadata.get("session_date"):
        parts.append(f"Date: {metadata['session_date']}")
    prefix = "\n".join(part for part in parts if part.split(": ", 1)[-1])
    return f"{prefix}\n\n{chunk}".strip()


def ingest_documents(documents_root: Path = DOCUMENTS_ROOT, trigger_name: str = "manual") -> dict[str, Any]:
    ensure_schema()

    import psycopg
    from psycopg.rows import dict_row

    stats = {
        "documents_root": str(documents_root),
        "documents_seen": 0,
        "documents_indexed": 0,
        "documents_skipped": 0,
        "chunks_indexed": 0,
    }

    with psycopg.connect(POSTGRES_URL, row_factory=dict_row) as connection:
        run = start_ingestion_run(connection, trigger_name=trigger_name)
        run_id = str(run["id"])
        connection.commit()
        try:
            log_ingestion_event(connection, run_id, "info", "Ingestion started", {"documents_root": str(documents_root)})

            for text_path in iter_text_files(documents_root):
                stats["documents_seen"] += 1
                metadata = load_metadata(text_path)
                content = text_path.read_text(encoding="utf-8", errors="ignore")
                payload = _document_payload(text_path, metadata, content)
                city = get_or_create_city(connection, payload["city"])

                existing = get_document_by_source_url(connection, payload["source_url"])
                if existing and existing["document_hash"] == payload["document_hash"]:
                    with connection.cursor() as cursor:
                        cursor.execute(
                            """
                            UPDATE documents
                            SET last_processed_at = NOW(), status = 'unchanged', updated_at = NOW()
                            WHERE id = %s
                            """,
                            (existing["id"],),
                        )
                    stats["documents_skipped"] += 1
                    continue

                document_row = upsert_document(
                    connection,
                    DocumentRecord(
                        city_id=str(city["id"]),
                        city=payload["city"],
                        source_url=payload["source_url"],
                        source_path=payload["source_path"],
                        doc_type=payload["doc_type"],
                        title=payload["title"],
                        document_date=payload["document_date"],
                        fetch_date=payload["fetch_date"],
                        last_processed_at=payload["last_processed_at"],
                        document_hash=payload["document_hash"],
                        content_hash=payload["content_hash"],
                        status="processed",
                        metadata=payload["metadata"],
                    ),
                )

                document_id = str(document_row["id"])
                delete_chunks_for_document(connection, document_id)
                delete_document(document_id)

                chunks = chunk_text(content)
                if not chunks:
                    continue

                searchable_chunks = [_searchable_text(metadata, chunk) for chunk in chunks]
                embeddings = embed_texts(searchable_chunks)
                chunk_rows = []
                opensearch_rows = []
                for index, (chunk, embedding) in enumerate(zip(searchable_chunks, embeddings)):
                    chunk_id = f"{text_path.relative_to(documents_root).as_posix()}#{index}"
                    chunk_hash = sha256_text(chunk)
                    chunk_rows.append(
                        {
                            "chunk_id": chunk_id,
                            "document_id": document_id,
                            "city_id": str(city["id"]),
                            "chunk_index": index,
                            "doc_type": payload["doc_type"],
                            "title": payload["title"],
                            "document_date": payload["document_date"],
                            "source_url": payload["source_url"],
                            "content": chunk,
                            "document_hash": payload["document_hash"],
                            "content_hash": chunk_hash,
                            "embedding": embedding,
                            "metadata": payload["metadata"],
                        }
                    )
                    opensearch_rows.append(
                        {
                            "chunk_id": chunk_id,
                            "document_id": document_id,
                            "city": payload["city"],
                            "doc_type": payload["doc_type"],
                            "title": payload["title"],
                            "date": payload["document_date"],
                            "source_url": payload["source_url"],
                            "content": chunk,
                            "document_hash": payload["document_hash"],
                            "content_hash": chunk_hash,
                            "chunk_index": index,
                            "fetch_date": payload["fetch_date"],
                            "last_processed_at": payload["last_processed_at"],
                            "embedding": embedding,
                        }
                    )

                insert_chunks(connection, chunk_rows)
                index_chunks(opensearch_rows)

                stats["documents_indexed"] += 1
                stats["chunks_indexed"] += len(chunk_rows)
                log_ingestion_event(
                    connection,
                    run_id,
                    "info",
                    "Document processed",
                    {"source_url": payload["source_url"], "chunks": len(chunk_rows), "document_hash": payload["document_hash"]},
                )

            finish_ingestion_run(connection, run_id, "success", stats)
            connection.commit()
            return stats
        except Exception as exc:
            connection.rollback()
            finish_ingestion_run(connection, run_id, "failed", {**stats, "error": str(exc)})
            connection.commit()
            raise


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Run the AI Riviera ingestion pipeline.")
    parser.add_argument("--documents-root", type=Path, default=DOCUMENTS_ROOT)
    parser.add_argument("--trigger-name", type=str, default="manual")
    args = parser.parse_args()

    stats = ingest_documents(documents_root=args.documents_root, trigger_name=args.trigger_name)
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
