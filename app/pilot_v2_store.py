from __future__ import annotations

import os
from functools import lru_cache

import requests


POSTGRES_V2_URL = os.getenv(
    "POSTGRES_V2_URL",
    "postgresql://pilot:pilot_local_only@127.0.0.1:55432/ai_riviera_embedding_pilot",
)


def _connect():
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(POSTGRES_V2_URL, row_factory=dict_row)


def ready() -> bool:
    try:
        with _connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT count(*) AS count FROM chunks WHERE embedding IS NOT NULL")
            row = cursor.fetchone()
            return bool(row and row["count"])
    except Exception:
        return False


@lru_cache(maxsize=128)
def embed_query(query: str) -> list[float]:
    api_key = os.getenv("MISTRAL_API_KEY", "")
    if not api_key:
        raise RuntimeError("MISTRAL_API_KEY manque pour la recherche V2")
    response = requests.post(
        "https://api.mistral.ai/v1/embeddings",
        headers={"Authorization": f"Bearer {api_key}"},
        json={"model": "mistral-embed", "input": [query]},
        timeout=60,
    )
    response.raise_for_status()
    return response.json()["data"][0]["embedding"]


def _vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(format(value, ".9g") for value in vector) + "]"


def search(query: str, limit: int = 50, filters: dict | None = None) -> list[dict]:
    filters = dict(filters or {})
    clauses = []
    params: list[object] = []
    doc_type = str(filters.get("doc_type") or "").lower()
    category_map = {
        "motions": "motion",
        "postulats": "postulat",
        "interpellations": "interpellation",
        "reglement-conseil-communal": "reglement_conseil_communal",
    }
    if doc_type in category_map:
        clauses.append("d.category = %s")
        params.append(category_map[doc_type])
    if filters.get("year"):
        clauses.append("coalesce(d.metadata->>'listing_year', d.metadata->>'year') = %s")
        params.append(str(filters["year"]))
    where_sql = "WHERE " + " AND ".join(clauses) if clauses else ""
    vector = _vector_literal(embed_query(query))
    sql = f"""
        SELECT c.chunk_id, c.document_id, c.chunk_index, c.component, c.content,
               d.title, d.category, d.document_role, d.metadata,
               1 - (c.embedding <=> %s::vector) AS score
        FROM chunks c JOIN documents d USING (document_id)
        {where_sql}
        ORDER BY c.embedding <=> %s::vector
        LIMIT %s
    """
    with _connect() as connection, connection.cursor() as cursor:
        cursor.execute(sql, [vector, *params, vector, limit])
        rows = cursor.fetchall()

    output = []
    for row in rows:
        metadata = dict(row["metadata"] or {})
        metadata.update({
            "title": row["title"],
            "category": row["category"],
            "doc_type": row["category"],
            "document_id": row["document_id"],
            "component": row["component"],
            "canonical_object": True,
        })
        source_url = metadata.get("file_url") or metadata.get("source_url") or metadata.get("source_page_url") or ""
        output.append({
            "id": row["chunk_id"],
            "chunk_id": row["chunk_id"],
            "document_id": row["document_id"],
            "chunk_index": row["chunk_index"],
            "component": row["component"],
            "content": row["content"],
            "text": row["content"],
            "title": row["title"],
            "category": row["category"],
            "doc_type": row["category"],
            "source_url": source_url,
            "metadata": metadata,
            "score": round(float(row["score"]), 6),
            "_score": float(row["score"]),
            "_search_source": "mistral_pgvector_v2",
        })
    return output
