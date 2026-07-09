from __future__ import annotations

import os
import re
from functools import lru_cache

import requests

from app.diagnostics import record_diagnostic
from app.text_cleaning import strip_accents


POSTGRES_V2_URL = os.getenv(
    "POSTGRES_V2_URL",
    "postgresql://pilot:pilot_local_only@127.0.0.1:55432/ai_riviera_embedding_pilot",
)

CATEGORY_MAP = {
    "motions": "motion",
    "postulats": "postulat",
    "interpellations": "interpellation",
    "reglement-conseil-communal": "reglement_conseil_communal",
}

_QUOTE_PATTERNS = [
    re.compile(r'"([^"]{3,200})"'),
    re.compile(r'«\s*([^»]{3,200})\s*»'),
    re.compile(r'“([^”]{3,200})”'),
]


def _masked_target() -> str:
    return re.sub(r"://([^:/]+):[^@]*@", r"://\1:***@", POSTGRES_V2_URL)


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
    except Exception as exc:
        record_diagnostic("pilot_v2", "Pilot V2 readiness check failed", exc, target=_masked_target())
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


def extract_quoted_phrases(query: str) -> list[str]:
    """Pull out title-like phrases the user quoted, e.g. "Zone 50 ? Oui, ...".

    Requires a bit of length and a space so a single quoted word doesn't
    trigger a noisy title match.
    """
    phrases = []
    for pattern in _QUOTE_PATTERNS:
        phrases.extend(match.strip() for match in pattern.findall(query))
    return [phrase for phrase in phrases if len(phrase) >= 6 and " " in phrase]


_COMMON_CAPITALIZED_WORDS = {
    "je", "tu", "il", "elle", "on", "nous", "vous", "ils", "elles",
    "le", "la", "les", "un", "une", "des", "du", "de", "et", "ou", "a",
    "que", "qui", "quoi", "quel", "quelle", "quels", "quelles",
    "combien", "comment", "pourquoi", "quand", "est", "sont",
}


def extract_capitalized_keywords(query: str) -> list[str]:
    """Pull out a bare proper-noun-looking word (e.g. a street or place name
    mentioned without quotes, like "Roussy" in "quelles interpellations
    parlent du Roussy ?"). A capitalized word can score low in pure vector
    similarity when it's a minor detail rather than the query's main topic
    ("Roussy" as one of three streets in a title about illegal camping) —
    this is a cheap way to still surface it, on top of the exact-title-quote
    match above.

    A heuristic, not a gazetteer: skips the first word (sentence-initial
    capitalization doesn't imply a proper noun) and a short list of common
    French function words that sometimes get capitalized by habit.
    """
    words = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ][\w\-']*", query)
    keywords = []
    for index, word in enumerate(words):
        if index == 0 or not word[0].isupper() or len(word) < 4:
            continue
        if strip_accents(word).lower() in _COMMON_CAPITALIZED_WORDS:
            continue
        keywords.append(word)
    return keywords


def _filter_clauses(filters: dict) -> tuple[list[str], list[object]]:
    clauses = []
    params: list[object] = []
    doc_type = str(filters.get("doc_type") or "").lower()
    if doc_type in CATEGORY_MAP:
        clauses.append("d.category = %s")
        params.append(CATEGORY_MAP[doc_type])
    if filters.get("year"):
        clauses.append("coalesce(d.metadata->>'listing_year', d.metadata->>'year') = %s")
        params.append(str(filters["year"]))
    if filters.get("article_number"):
        clauses.append("c.metadata->>'article_number' = %s")
        params.append(str(filters["article_number"]))
    return clauses, params


def _relaxed_filter_stages(filters: dict) -> list[dict]:
    """Progressively drop filters: year first (most error-prone), then all."""
    stages = []
    if filters.get("year"):
        stages.append({key: value for key, value in filters.items() if key != "year"})
    if filters:
        stages.append({})
    deduped: list[dict] = []
    for stage in stages:
        if not deduped or deduped[-1] != stage:
            deduped.append(stage)
    return deduped


def _run_vector_search(vector: str, limit: int, filters: dict) -> list[dict]:
    clauses, params = _filter_clauses(filters)
    where_sql = "WHERE " + " AND ".join(clauses) if clauses else ""
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
        return cursor.fetchall()


def _run_title_search(phrase: str, limit: int) -> list[dict]:
    sql = """
        SELECT c.chunk_id, c.document_id, c.chunk_index, c.component, c.content,
               d.title, d.category, d.document_role, d.metadata,
               1.0::float AS score
        FROM documents d JOIN chunks c USING (document_id)
        WHERE d.title ILIKE %s
        ORDER BY d.document_id, c.chunk_index
        LIMIT %s
    """
    with _connect() as connection, connection.cursor() as cursor:
        cursor.execute(sql, [f"%{phrase}%", limit])
        return cursor.fetchall()


def _run_keyword_search(keyword: str, limit: int, filters: dict, max_chunks_per_document: int = 2) -> list[dict]:
    """Title OR chunk-content match on a single bare keyword (e.g. a street
    name mentioned without quotes) — a weaker signal than an exact quoted
    title match, so it gets a lower synthetic score.

    Applies the same doc_type/year filters as the main vector search — a
    keyword search across the *whole* corpus is exactly the failure mode
    this exists to fix (e.g. "Roussy" is mentioned in dozens of unrelated
    reports; restricting to the already-detected "interpellations" category
    narrows that from ~295 documents to ~50, which is what actually lets the
    target survive the per-document cap below).

    Caps chunks per document (window function, not just LIMIT) — otherwise a
    single document that happens to repeat the keyword many times (e.g. a
    long financial report mentioning a street name several times) eats the
    whole limit budget before other, more relevant documents are ever seen.
    """
    extra_clauses, extra_params = _filter_clauses(filters)
    where_sql = " AND (d.title ILIKE %s OR c.content ILIKE %s)"
    if extra_clauses:
        where_sql = " AND " + " AND ".join(extra_clauses) + where_sql
    sql = f"""
        SELECT chunk_id, document_id, chunk_index, component, content, title, category, document_role, metadata, score
        FROM (
            SELECT c.chunk_id, c.document_id, c.chunk_index, c.component, c.content,
                   d.title, d.category, d.document_role, d.metadata,
                   0.9::float AS score,
                   row_number() OVER (PARTITION BY d.document_id ORDER BY c.chunk_index) AS rn
            FROM documents d JOIN chunks c USING (document_id)
            WHERE TRUE{where_sql}
        ) ranked
        WHERE rn <= %s
        ORDER BY document_id, chunk_index
        LIMIT %s
    """
    with _connect() as connection, connection.cursor() as cursor:
        cursor.execute(sql, [*extra_params, f"%{keyword}%", f"%{keyword}%", max_chunks_per_document, limit])
        return cursor.fetchall()


def fetch_document_chunks(document_id: str, score: float) -> list[dict]:
    """Every chunk of a single document, in the same result shape as search().

    Used to expand a small, already-identified document (e.g. an
    interpellation) to its full text instead of relying on whichever chunks
    happened to score highest against the query embedding — a chunk phrased
    very differently from the question (e.g. the municipal response) can
    otherwise be missed even though the document itself is clearly the right
    one. `score` is the triggering chunk's score, assigned to every sibling
    chunk so they rank sensibly rather than floating unscored.
    """
    sql = """
        SELECT c.chunk_id, c.document_id, c.chunk_index, c.component, c.content,
               d.title, d.category, d.document_role, d.metadata
        FROM chunks c JOIN documents d USING (document_id)
        WHERE c.document_id = %s
        ORDER BY c.chunk_index
    """
    with _connect() as connection, connection.cursor() as cursor:
        cursor.execute(sql, (document_id,))
        rows = cursor.fetchall()
    for row in rows:
        row["score"] = score
    return _rows_to_results(rows, "document_expansion_v2")


def _rows_to_results(rows: list[dict], search_source: str) -> list[dict]:
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
            "_search_source": search_source,
        })
    return output


def aggregate_authors(filters: dict | None = None) -> list[dict]:
    """Real count/enumeration over author metadata (civility, party, category, year) —
    a structured query, not a semantic search over chunk text. Use this for
    "combien de ..." / "liste tous les ..." questions instead of relying on an
    LLM to eyeball a limited set of retrieved passages.
    """
    filters = dict(filters or {})
    clauses = ["category_meta.cat_value ? 'authors'"]
    params: list[object] = []

    doc_type = str(filters.get("doc_type") or "").lower()
    if doc_type in CATEGORY_MAP:
        clauses.append("d.category = %s")
        params.append(CATEGORY_MAP[doc_type])
    if filters.get("year"):
        clauses.append("coalesce(d.metadata->>'listing_year', d.metadata->>'year') = %s")
        params.append(str(filters["year"]))
    if filters.get("civility"):
        clauses.append("author->>'civility' = %s")
        params.append(str(filters["civility"]))

    where_sql = "WHERE " + " AND ".join(clauses)
    sql = f"""
        SELECT DISTINCT d.document_id, d.title, d.category, d.metadata,
               author->>'name' AS author_name, author->>'civility' AS civility, author->>'party' AS party,
               coalesce(d.metadata->>'listing_year', d.metadata->>'year') AS year
        FROM documents d,
             jsonb_each(d.metadata->'additional_metadata') AS category_meta(cat_key, cat_value),
             jsonb_array_elements(category_meta.cat_value->'authors') AS author
        {where_sql}
        ORDER BY year DESC NULLS LAST, author_name
    """
    with _connect() as connection, connection.cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.fetchall()


def search(query: str, limit: int = 50, filters: dict | None = None) -> list[dict]:
    filters = dict(filters or {})
    vector = _vector_literal(embed_query(query))

    rows = _run_vector_search(vector, limit, filters)
    if not rows and filters:
        for relaxed_filters in _relaxed_filter_stages(filters):
            record_diagnostic(
                "pilot_v2",
                "Filtered search returned no rows, retrying with relaxed filters",
                filters=filters,
                relaxed_filters=relaxed_filters,
            )
            rows = _run_vector_search(vector, limit, relaxed_filters)
            if rows:
                break

    title_rows: list[dict] = []
    seen_chunk_ids = {row["chunk_id"] for row in rows}
    for phrase in extract_quoted_phrases(query):
        for row in _run_title_search(phrase, limit=15):
            if row["chunk_id"] not in seen_chunk_ids:
                title_rows.append(row)
                seen_chunk_ids.add(row["chunk_id"])

    keyword_rows: list[dict] = []
    for keyword in extract_capitalized_keywords(query):
        for row in _run_keyword_search(keyword, limit=40, filters=filters):
            if row["chunk_id"] not in seen_chunk_ids:
                keyword_rows.append(row)
                seen_chunk_ids.add(row["chunk_id"])

    results = _rows_to_results(rows, "mistral_pgvector_v2")
    if keyword_rows:
        results = _rows_to_results(keyword_rows, "keyword_match_v2") + results
    if title_rows:
        results = _rows_to_results(title_rows, "title_match_v2") + results

    return results[:limit]
