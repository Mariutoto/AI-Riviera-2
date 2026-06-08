from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from app.config import POSTGRES_SCHEMA_PATH, POSTGRES_URL
from app.diagnostics import record_diagnostic
from app.text_cleaning import strip_accents


@dataclass
class DocumentRecord:
    city: str
    source_url: str
    source_path: str
    doc_type: str
    title: str
    document_date: str | None
    fetch_date: str | None
    last_processed_at: str | None
    document_hash: str
    content_hash: str
    status: str
    metadata: dict[str, Any]


def _connect():
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(POSTGRES_URL, row_factory=dict_row)


def ensure_schema() -> None:
    schema_sql = POSTGRES_SCHEMA_PATH.read_text(encoding="utf-8")
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(schema_sql)
        connection.commit()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical_source_url(metadata: dict[str, Any], fallback: str) -> str:
    return str(
        metadata.get("source_url")
        or metadata.get("pdf_url")
        or metadata.get("url")
        or metadata.get("source_page")
        or fallback
    )


def canonical_document_date(metadata: dict[str, Any]) -> str | None:
    for key in ("document_date", "session_date", "date"):
        value = metadata.get(key)
        if value:
            return str(value)[:10]
    return None


def canonical_fetch_date(metadata: dict[str, Any]) -> str | None:
    value = metadata.get("fetch_date") or metadata.get("fetched_at")
    return str(value) if value else None


def build_document_hash(metadata: dict[str, Any], content: str) -> tuple[str, str]:
    content_hash = sha256_text(content)
    payload = json.dumps(
        {
            "title": metadata.get("title", ""),
            "filename": metadata.get("filename", ""),
            "year": metadata.get("year", ""),
            "category": metadata.get("category", ""),
            "doc_type": metadata.get("doc_type", metadata.get("category", "")),
            "content_hash": content_hash,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return sha256_text(payload), content_hash


def upsert_document(connection, record: DocumentRecord) -> dict[str, Any]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            DELETE FROM documents
            WHERE city = %s
              AND source_path = %s
              AND source_url <> %s
            """,
            (record.city, record.source_path, record.source_url),
        )
        cursor.execute(
            """
            INSERT INTO documents (
                city, source_url, source_path, doc_type, title, document_date,
                fetch_date, last_processed_at, document_hash, content_hash, status, metadata
            )
            VALUES (
                %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s::jsonb
            )
            ON CONFLICT (source_url) DO UPDATE
            SET
                city = EXCLUDED.city,
                source_path = EXCLUDED.source_path,
                doc_type = EXCLUDED.doc_type,
                title = EXCLUDED.title,
                document_date = EXCLUDED.document_date,
                fetch_date = EXCLUDED.fetch_date,
                last_processed_at = EXCLUDED.last_processed_at,
                document_hash = EXCLUDED.document_hash,
                content_hash = EXCLUDED.content_hash,
                status = EXCLUDED.status,
                metadata = EXCLUDED.metadata,
                updated_at = NOW()
            RETURNING id, document_hash, content_hash, status
            """,
            (
                record.city,
                record.source_url,
                record.source_path,
                record.doc_type,
                record.title,
                record.document_date,
                record.fetch_date,
                record.last_processed_at,
                record.document_hash,
                record.content_hash,
                record.status,
                json.dumps(record.metadata, ensure_ascii=False),
            ),
        )
        return cursor.fetchone()


def get_document_by_source_url(connection, source_url: str) -> dict[str, Any] | None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT d.*
            FROM documents d
            WHERE d.source_url = %s
            """,
            (source_url,),
        )
        return cursor.fetchone()


def ready() -> bool:
    try:
        with _connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) AS count FROM document_chunks")
                row = cursor.fetchone()
        return bool(row and row["count"] > 0)
    except Exception as exc:
        record_diagnostic("postgres", "Postgres readiness check failed", exc)
        return False


def _normalize_search_rows(rows: list[dict[str, Any]], searchable_tokens: list[str], query: str) -> list[dict[str, Any]]:
    query_lower = strip_accents(query).lower()
    regulation_query = (
        "reglement" in query_lower
        or "rcc" in query_lower
        or ("article" in query_lower and ("conseil" in query_lower or "communal" in query_lower))
    )
    president_election_query = any(term in query_lower for term in ["election", "president", "nomination"])
    results = []
    for row in rows:
        content = row["content"] or ""
        title = row["title"] or ""
        doc_type = row["doc_type"] or ""
        score = float(row.get("score") or 0.0)
        metadata = row["metadata"] or {}
        if not isinstance(metadata, dict):
            metadata = {}
        haystacks = {
            "content": strip_accents(content).lower(),
            "title": strip_accents(title).lower(),
            "doc_type": strip_accents(doc_type).lower(),
            "source_url": strip_accents(str(row["source_url"] or "")).lower(),
            "metadata": strip_accents(json.dumps(metadata, ensure_ascii=False)).lower(),
        }
        for token in searchable_tokens:
            token = strip_accents(token).lower()
            score += haystacks["content"].count(token) * 1.0
            score += haystacks["title"].count(token) * 8.0
            score += haystacks["doc_type"].count(token) * 4.0
            score += haystacks["source_url"].count(token) * 2.0
            score += haystacks["metadata"].count(token) * 3.0
        if query_lower and query_lower in haystacks["content"]:
            score += 12.0

        is_regulation = str(doc_type).lower() == "reglement-conseil-communal"
        is_regulation_article = metadata.get("content_kind") == "regulation_article" or bool(metadata.get("article_number"))
        if regulation_query:
            if is_regulation:
                score += 45.0
            if is_regulation_article:
                score += 35.0
            if any(
                token in haystacks["title"] or token in haystacks["metadata"]
                for token in ["election", "president", "nomination", "bureau"]
            ):
                score += 18.0
            if president_election_query and str(metadata.get("article_number")) in {"11", "12"}:
                score += 45.0
            if str(doc_type).lower() in {"ordres-du-jour", "rapport-gestion", "rapports-gestion"}:
                score -= 30.0
        if metadata.get("canonical_object") is False:
            score -= 12.0
        elif metadata.get("canonical_object") is True:
            score += 15.0
        elif metadata.get("source_collection") == "motions-postulats":
            score += 10.0
        if metadata.get("source_collection") == "ordre-du-jour-linked-document":
            score -= 12.0
        metadata = {
            **metadata,
            "city": row["city"] or metadata.get("city") or metadata.get("commune", ""),
            "doc_type": doc_type,
            "title": title,
            "date": str(row["document_date"] or ""),
            "source_url": row["source_url"] or metadata.get("source_url", ""),
            "document_hash": row["document_hash"] or metadata.get("document_hash", ""),
        }
        results.append(
            {
                "id": row["chunk_id"],
                "chunk_id": row["chunk_id"],
                "text": content,
                "content": content,
                "chunk_index": row["chunk_index"],
                "relative_text_path": row["source_path"] or "",
                "source_url": row["source_url"],
                "document_hash": row["document_hash"],
                "metadata": metadata,
                "score": round(score, 3),
            }
        )

    return sorted(results, key=lambda item: item["score"], reverse=True)


def search_chunks(query: str, tokens: list[str], limit: int = 10, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    if not tokens:
        return []

    filters = filters or {}
    raw_tokens = re.findall(r"[a-zA-ZÀ-ÿ0-9]{3,}", query.lower())
    searchable_tokens = [token for token in dict.fromkeys([*tokens, *raw_tokens]) if len(token) >= 3][:16]
    if not searchable_tokens:
        return []

    filter_where = []
    filter_params: list[Any] = []
    if filters.get("city"):
        filter_where.append("LOWER(dc.city) = LOWER(%s)")
        filter_params.append(filters["city"])
    if filters.get("doc_type"):
        filter_where.append("LOWER(dc.doc_type) = LOWER(%s)")
        filter_params.append(filters["doc_type"])
    if filters.get("content_kind"):
        filter_where.append("dc.metadata->>'content_kind' = %s")
        filter_params.append(filters["content_kind"])
    if filters.get("year"):
        filter_where.append("(dc.metadata->>'year' = %s OR d.source_path LIKE %s)")
        filter_params.extend([str(filters["year"]), f"%/{filters['year']}/%"])
    if filters.get("date_from"):
        filter_where.append("dc.document_date >= %s")
        filter_params.append(filters["date_from"])
    if filters.get("date_to"):
        filter_where.append("dc.document_date <= %s")
        filter_params.append(filters["date_to"])

    filters_sql = f" AND {' AND '.join(filter_where)}" if filter_where else ""
    fts_params: list[Any] = [query, *filter_params, max(limit * 8, 50)]
    fts_sql = f"""
        WITH search_query AS (
            SELECT websearch_to_tsquery('french', %s) AS query
        )
        SELECT
            dc.chunk_id,
            dc.content,
            dc.chunk_index,
            dc.doc_type,
            dc.title,
            dc.document_date,
            dc.source_url,
            dc.document_hash,
            dc.content_hash,
            dc.metadata,
            dc.city,
            d.source_path,
            ts_rank_cd(dc.search_vector, search_query.query) * 100 AS score
        FROM document_chunks dc
        JOIN documents d ON d.id = dc.document_id
        CROSS JOIN search_query
        WHERE dc.search_vector @@ search_query.query
        {filters_sql}
        ORDER BY score DESC, dc.updated_at DESC
        LIMIT %s
    """

    try:
        with _connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(fts_sql, fts_params)
                fts_rows = cursor.fetchall()
        if fts_rows:
            return _normalize_search_rows(fts_rows, searchable_tokens, query)[:limit]
    except Exception as exc:
        record_diagnostic(
            "postgres",
            "Postgres full-text search failed; falling back to LIKE search",
            exc,
            filters=filters,
            query=query[:300],
        )

    like_where = []
    like_params: list[Any] = []
    token_clauses = []
    for token in searchable_tokens:
        pattern = f"%{token}%"
        token_clauses.append(
            "(LOWER(dc.content) LIKE %s OR LOWER(dc.title) LIKE %s OR LOWER(dc.doc_type) LIKE %s OR LOWER(dc.source_url) LIKE %s OR LOWER(dc.metadata::text) LIKE %s)"
        )
        like_params.extend([pattern, pattern, pattern, pattern, pattern])
    like_where.append("(" + " OR ".join(token_clauses) + ")")
    like_where.extend(filter_where)

    like_params.extend(filter_params)
    like_params.append(max(limit * 8, 50))
    sql = f"""
        SELECT
            dc.chunk_id,
            dc.content,
            dc.chunk_index,
            dc.doc_type,
            dc.title,
            dc.document_date,
            dc.source_url,
            dc.document_hash,
            dc.content_hash,
            dc.metadata,
            dc.city,
            d.source_path
        FROM document_chunks dc
        JOIN documents d ON d.id = dc.document_id
        WHERE {" AND ".join(like_where)}
        ORDER BY dc.updated_at DESC
        LIMIT %s
    """

    try:
        with _connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, like_params)
                rows = cursor.fetchall()
    except Exception as exc:
        record_diagnostic(
            "postgres",
            "Postgres LIKE search failed",
            exc,
            filters=filters,
            query=query[:300],
        )
        return []

    return _normalize_search_rows(rows, searchable_tokens, query)[:limit]


def insert_chunks(connection, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return

    with connection.cursor() as cursor:
        cursor.executemany(
            """
            INSERT INTO document_chunks (
                chunk_id, document_id, city, chunk_index, doc_type, title,
                document_date, source_url, content, document_hash, content_hash,
                embedding, metadata, search_vector
            ) VALUES (
                %(chunk_id)s, %(document_id)s, %(city)s, %(chunk_index)s, %(doc_type)s, %(title)s,
                %(document_date)s, %(source_url)s, %(content)s, %(document_hash)s, %(content_hash)s,
                %(embedding)s::jsonb,
                %(metadata)s::jsonb,
                setweight(to_tsvector('french', coalesce(%(title)s, '')), 'A') ||
                setweight(to_tsvector('french', coalesce(%(doc_type)s, '')), 'B') ||
                setweight(to_tsvector('french', coalesce(%(content)s, '')), 'C') ||
                setweight(to_tsvector('french', coalesce(%(metadata_text)s, '')), 'D')
            )
            ON CONFLICT (chunk_id) DO UPDATE
            SET
                document_id = EXCLUDED.document_id,
                city = EXCLUDED.city,
                chunk_index = EXCLUDED.chunk_index,
                doc_type = EXCLUDED.doc_type,
                title = EXCLUDED.title,
                document_date = EXCLUDED.document_date,
                source_url = EXCLUDED.source_url,
                content = EXCLUDED.content,
                document_hash = EXCLUDED.document_hash,
                content_hash = EXCLUDED.content_hash,
                embedding = EXCLUDED.embedding,
                metadata = EXCLUDED.metadata,
                search_vector = EXCLUDED.search_vector,
                updated_at = NOW()
            """,
            [
                {
                    **row,
                    "embedding": json.dumps(row["embedding"], ensure_ascii=False),
                    "metadata": json.dumps(row["metadata"], ensure_ascii=False),
                    "metadata_text": json.dumps(row["metadata"], ensure_ascii=False),
                }
                for row in rows
            ],
        )


def delete_chunks_for_document(connection, document_id: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM document_chunks WHERE document_id = %s", (document_id,))


def start_ingestion_run(connection, trigger_name: str = "manual") -> dict[str, Any]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO ingestion_runs (trigger_name)
            VALUES (%s)
            RETURNING id, started_at, status
            """,
            (trigger_name,),
        )
        return cursor.fetchone()


def finish_ingestion_run(connection, run_id: str, status: str, stats: dict[str, Any]) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE ingestion_runs
            SET finished_at = NOW(), status = %s, stats = %s::jsonb
            WHERE id = %s
            """,
            (status, json.dumps(stats, ensure_ascii=False), run_id),
        )


def log_ingestion_event(connection, run_id: str, level: str, message: str, context: dict[str, Any] | None = None) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO ingestion_logs (run_id, level, message, context)
            VALUES (%s, %s, %s, %s::jsonb)
            """,
            (run_id, level, message, json.dumps(context or {}, ensure_ascii=False)),
        )
