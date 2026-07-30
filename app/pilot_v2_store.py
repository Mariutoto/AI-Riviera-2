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
    "preavis-municipaux": "preavis_municipal",
    "proces-verbaux": "proces_verbal",
    "rapports-comptes": "rapport_comptes",
    "rapports-gestion": "rapport_gestion",
    "reglement-conseil-communal": "reglement_conseil_communal",
    "budget": "budget",
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
    "commune", "municipalite", "ville", "conseil",
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
    words = re.findall(r"[^\W\d_][\w\-']*", query, flags=re.UNICODE)
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
    city = str(filters.get("city") or "").strip()
    if city and city.lower() != "all":
        clauses.append("d.metadata->>'commune' = %s")
        params.append(city)
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
    """Progressively drop optional filters while always preserving city scope."""
    stages = []
    city_filter = {"city": filters["city"]} if filters.get("city") else {}
    if filters.get("year"):
        stages.append({key: value for key, value in filters.items() if key != "year"})
    if filters:
        stages.append(city_filter)
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
               d.title, d.category, d.document_role, d.summary, d.metadata,
               1 - (c.embedding <=> %s::vector) AS score
        FROM chunks c JOIN documents d USING (document_id)
        {where_sql}
        ORDER BY c.embedding <=> %s::vector
        LIMIT %s
    """
    with _connect() as connection, connection.cursor() as cursor:
        cursor.execute(sql, [vector, *params, vector, limit])
        return cursor.fetchall()


def _run_title_search(phrase: str, limit: int, filters: dict | None = None) -> list[dict]:
    clauses, params = _filter_clauses(
        {"city": filters["city"]} if filters and filters.get("city") else {}
    )
    city_sql = " AND " + " AND ".join(clauses) if clauses else ""
    sql = f"""
        SELECT c.chunk_id, c.document_id, c.chunk_index, c.component, c.content,
               d.title, d.category, d.document_role, d.summary, d.metadata,
               1.0::float AS score
        FROM documents d JOIN chunks c USING (document_id)
        WHERE d.title ILIKE %s{city_sql}
        ORDER BY d.document_id, c.chunk_index
        LIMIT %s
    """
    with _connect() as connection, connection.cursor() as cursor:
        cursor.execute(sql, [f"%{phrase}%", *params, limit])
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
        SELECT chunk_id, document_id, chunk_index, component, content, title, category, document_role, summary, metadata, score
        FROM (
            SELECT c.chunk_id, c.document_id, c.chunk_index, c.component, c.content,
                   d.title, d.category, d.document_role, d.summary, d.metadata,
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
               d.title, d.category, d.document_role, d.summary, d.metadata
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
        if row.get("summary"):
            metadata["summary"] = row["summary"]
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
    clauses = [
        "category_meta.cat_value ? 'authors'",
        "coalesce(d.document_role, '') NOT IN "
        "('municipal_response', 'response', 'status_report', "
        "'council_decision', 'consideration_report', 'commission_report')",
    ]
    params: list[object] = []

    city = str(filters.get("city") or "").strip()
    if city and city.lower() != "all":
        clauses.append("d.metadata->>'commune' = %s")
        params.append(city)

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
        SELECT DISTINCT d.document_id, d.title, d.category, d.summary, d.metadata,
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


def _response_entries(metadata: dict) -> list[dict]:
    additional = metadata.get("additional_metadata") or {}
    interpellation = additional.get("interpellation_metadata") or {}
    return [
        response
        for response in (interpellation.get("responses") or [])
        if isinstance(response, dict)
    ]


def _response_document_ids(metadata: dict) -> list[str]:
    additional = metadata.get("additional_metadata") or {}
    relationships = additional.get("relationships") or {}
    return [
        str(document_id)
        for document_id in (relationships.get("response_document_ids") or [])
        if document_id
    ]


_FRENCH_MONTH_NUMBERS = {
    "janvier": 1,
    "fevrier": 2,
    "mars": 3,
    "avril": 4,
    "mai": 5,
    "juin": 6,
    "juillet": 7,
    "aout": 8,
    "septembre": 9,
    "octobre": 10,
    "novembre": 11,
    "decembre": 12,
}


def _date_in_filename(title: str) -> str:
    match = re.search(
        r"(?<!\d)(20\d{2})-(\d{2})-(\d{2})(?!\d)",
        title,
    )
    return match.group(0) if match else ""


def _response_letter_date(response_document: dict | None) -> str:
    """Read the municipal letter date, not the later council/listing date."""
    if not response_document:
        return ""
    if response_document.get("document_role") != "municipal_response":
        return ""

    text = strip_accents(str(response_document.get("content") or "")).lower()
    header = " ".join(text[:1800].split())
    match = re.search(
        r"\b(?:vevey|la tour-de-peilz|montreux)\s*,?\s+le\s+"
        r"(\d{1,2})\s+"
        r"(janvier|fevrier|mars|avril|mai|juin|juillet|aout|"
        r"septembre|octobre|novembre|decembre)\s+"
        r"(20\d{2})\b",
        header,
    )
    if not match:
        return ""
    day, month_name, year = match.groups()
    month = _FRENCH_MONTH_NUMBERS[month_name]
    return f"{year}-{month:02d}-{int(day):02d}"


def _official_response_date(
    response: dict,
    response_document: dict | None,
) -> str:
    return (
        _response_letter_date(response_document)
        or str(response.get("municipal_adoption_date") or "")
        or str(response.get("response_date") or "")
    )


def _response_year(response: dict, response_document: dict | None) -> str:
    response_date = _official_response_date(response, response_document)
    if re.match(r"^20\d{2}", response_date):
        return response_date[:4]
    if response_document:
        metadata = response_document.get("metadata") or {}
        document_date = str(
            metadata.get("document_date")
            or metadata.get("listing_date")
            or ""
        )
        if re.match(r"^20\d{2}", document_date):
            return document_date[:4]
        listing_year = str(metadata.get("listing_year") or "")
        if re.fullmatch(r"20\d{2}", listing_year):
            return listing_year
    number = str(response.get("response_number") or "")
    match = re.search(r"\b(20\d{2})\b", number)
    return match.group(1) if match else ""


def _political_object_date(metadata: dict, title: str) -> str:
    additional = metadata.get("additional_metadata") or {}
    interpellation = additional.get("interpellation_metadata") or {}
    return (
        _date_in_filename(title)
        or str(interpellation.get("deposit_date") or "")
        or str(interpellation.get("interpellation_date") or "")
        or str(metadata.get("document_date") or "")
        or str(metadata.get("listing_date") or "")
    )


def _author_display(author: dict) -> str:
    name = str(author.get("name") or "").strip()
    if not name:
        return ""
    civility = str(author.get("civility") or "").strip()
    party = str(author.get("party") or "").strip()
    display = name
    if civility and not display.lower().startswith(civility.lower()):
        display = f"{civility} {display}"
    if party and f"({party})".lower() not in display.lower():
        display = f"{display} ({party})"
    return display


def _response_reference(
    response: dict,
    response_document: dict | None,
) -> str:
    metadata = (response_document or {}).get("metadata") or {}
    additional = metadata.get("additional_metadata") or {}
    relationships = additional.get("relationships") or {}
    official = str(relationships.get("official_reference") or "").strip()
    if official:
        return official
    source_tracking = additional.get("source_tracking") or {}
    for occurrence in source_tracking.get("listing_occurrences") or []:
        reference = str(occurrence.get("reference") or "").strip()
        if reference:
            return reference
    return str(response.get("response_number") or "").strip()


def select_answered_interpellations(
    objects: list[dict],
    response_documents: dict[str, dict],
    filters: dict | None = None,
) -> list[dict]:
    """Return one canonical row per answered interpellation.

    The political object remains in storage for provenance. For presentation,
    the official response PDF is preferred when it is a separate linked
    document (Vevey); a combined interpellation-response PDF remains the
    canonical source when the municipality publishes one (La Tour-de-Peilz).
    """
    filters = dict(filters or {})
    requested_city = str(filters.get("city") or "").strip()
    requested_year = str(filters.get("response_year") or "").strip()
    selected = []

    for document in objects:
        metadata = dict(document.get("metadata") or {})
        commune = str(metadata.get("commune") or "")
        if (
            requested_city
            and requested_city.lower() != "all"
            and commune != requested_city
        ):
            continue

        entries = _response_entries(metadata)
        linked_ids = _response_document_ids(metadata)
        linked_documents = [
            response_documents[document_id]
            for document_id in linked_ids
            if document_id in response_documents
        ]

        # Some legacy objects have a linked response document but no copied
        # response entry. Recover the entry from the response metadata.
        if not entries:
            for response_document in linked_documents:
                entries.extend(
                    _response_entries(response_document.get("metadata") or {})
                )

        matching_entries: list[tuple[dict, dict | None]] = []
        for response in entries:
            response_number = str(response.get("response_number") or "")
            response_document = next(
                (
                    candidate
                    for candidate in linked_documents
                    if not response_number
                    or any(
                        str(item.get("response_number") or "")
                        == response_number
                        for item in _response_entries(
                            candidate.get("metadata") or {}
                        )
                    )
                ),
                linked_documents[0] if len(linked_documents) == 1 else None,
            )
            if (
                requested_year
                and _response_year(response, response_document)
                != requested_year
            ):
                continue
            matching_entries.append((response, response_document))

        if not matching_entries:
            continue

        response, response_document = matching_entries[0]
        source_document = response_document or document
        source_metadata = dict(source_document.get("metadata") or {})
        original_title = str(document.get("title") or "")
        title = original_title
        response_title = str(
            source_metadata.get("source_title")
            or (response_document or {}).get("title")
            or ""
        ).strip()
        if (
            response_title
            and (
                title.lower().endswith(".pdf")
                or "_" in title
                or title.lower().startswith(("int_", "interpellation_"))
            )
        ):
            title = response_title
        source_url = (
            source_metadata.get("file_url")
            or source_metadata.get("source_url")
            or metadata.get("file_url")
            or metadata.get("source_url")
            or ""
        )
        additional = metadata.get("additional_metadata") or {}
        interpellation = additional.get("interpellation_metadata") or {}
        authors = [
            _author_display(author)
            for author in (interpellation.get("authors") or [])
            if isinstance(author, dict) and _author_display(author)
        ]
        if not authors and response_document:
            response_additional = (
                source_metadata.get("additional_metadata") or {}
            )
            source_tracking = (
                response_additional.get("source_tracking") or {}
            )
            for occurrence in (
                source_tracking.get("listing_occurrences") or []
            ):
                author = str(occurrence.get("author") or "").strip()
                if author and author not in authors:
                    authors.append(author)
        selected.append(
            {
                "document_id": document["document_id"],
                "source_document_id": source_document["document_id"],
                "title": title,
                "category": document["category"],
                "document_role": document.get("document_role"),
                "summary": document.get("summary"),
                "metadata": metadata,
                "commune": commune,
                "authors": authors,
                "response_number": response.get("response_number"),
                "response_reference": _response_reference(
                    response,
                    response_document,
                ),
                "political_date": _political_object_date(
                    metadata,
                    original_title,
                ),
                "response_date": _official_response_date(
                    response,
                    response_document,
                ),
                "response_url": source_url,
            }
        )

    return sorted(
        selected,
        key=lambda row: (
            row.get("commune") or "",
            row.get("response_date") or "",
            row.get("title") or "",
        ),
    )


def answered_interpellations(filters: dict | None = None) -> list[dict]:
    """Enumerate interpellations with a verified linked/combined response."""
    filters = dict(filters or {})
    with _connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT document_id, title, category, document_role, summary, "
            "metadata FROM documents "
            "WHERE category = 'interpellation' "
            "AND document_role <> 'municipal_response'"
        )
        objects = cursor.fetchall()
        linked_ids = sorted(
            {
                document_id
                for document in objects
                for document_id in _response_document_ids(
                    document.get("metadata") or {}
                )
            }
        )
        response_documents: dict[str, dict] = {}
        if linked_ids:
            cursor.execute(
                "SELECT document_id, title, category, document_role, summary, "
                "metadata, coalesce(("
                "SELECT string_agg(c.content, ' ' ORDER BY c.chunk_index) "
                "FROM chunks c WHERE c.document_id = documents.document_id"
                "), '') AS content "
                "FROM documents WHERE document_id = ANY(%s)",
                (linked_ids,),
            )
            response_documents = {
                row["document_id"]: row for row in cursor.fetchall()
            }
    return select_answered_interpellations(
        objects, response_documents, filters
    )


def answered_postulates(filters: dict | None = None) -> list[dict]:
    """Enumerate postulates with a verified linked municipal response."""
    filters = dict(filters or {})
    requested_city = str(filters.get("city") or "").strip()
    requested_year = str(filters.get("response_year") or "").strip()
    clauses = [
        "category = 'postulat'",
        "document_role = 'postulate_text'",
        "coalesce((metadata->'additional_metadata'->'postulate_metadata'"
        "->>'has_response')::boolean, false)",
    ]
    params: list[object] = []
    if requested_city and requested_city.lower() != "all":
        clauses.append("metadata->>'commune' = %s")
        params.append(requested_city)
    with _connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT document_id, title, category, document_role, summary, "
            "metadata FROM documents WHERE " + " AND ".join(clauses),
            params,
        )
        originals = cursor.fetchall()
        linked_ids = sorted(
            {
                document_id
                for original in originals
                for document_id in _response_document_ids(
                    original.get("metadata") or {}
                )
            }
        )
        response_documents: dict[str, dict] = {}
        if linked_ids:
            cursor.execute(
                "SELECT document_id, title, category, document_role, summary, "
                "metadata, coalesce(("
                "SELECT string_agg(c.content, ' ' ORDER BY c.chunk_index) "
                "FROM chunks c WHERE c.document_id = documents.document_id"
                "), '') AS content "
                "FROM documents WHERE document_id = ANY(%s)",
                (linked_ids,),
            )
            response_documents = {
                row["document_id"]: row for row in cursor.fetchall()
            }

    selected = []
    for original in originals:
        metadata = dict(original.get("metadata") or {})
        additional = metadata.get("additional_metadata") or {}
        specific = additional.get("postulate_metadata") or {}
        relationships = additional.get("relationships") or {}
        response_document = next(
            (
                response_documents[document_id]
                for document_id in (
                    relationships.get("response_document_ids") or []
                )
                if document_id in response_documents
            ),
            None,
        )
        if not response_document:
            continue
        response_date = (
            _response_letter_date(response_document)
            or str(specific.get("response_date") or "")
        )
        if requested_year and not response_date.startswith(requested_year):
            continue
        response_metadata = response_document.get("metadata") or {}
        authors = [
            _author_display(item)
            for item in (specific.get("authors") or [])
            if isinstance(item, dict) and _author_display(item)
        ]
        selected.append(
            {
                "document_id": original["document_id"],
                "source_document_id": response_document["document_id"],
                "title": original["title"],
                "category": "postulat",
                "document_role": original.get("document_role"),
                "summary": original.get("summary"),
                "metadata": metadata,
                "commune": str(metadata.get("commune") or ""),
                "authors": authors,
                "response_reference": _response_reference(
                    {},
                    response_document,
                ),
                "political_date": str(specific.get("deposit_date") or ""),
                "response_date": response_date,
                "response_url": (
                    response_metadata.get("file_url")
                    or response_metadata.get("source_url")
                    or ""
                ),
            }
        )
    return sorted(
        selected,
        key=lambda row: (
            row.get("commune") or "",
            row.get("response_date") or "",
            row.get("title") or "",
        ),
    )


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
        for row in _run_title_search(phrase, limit=15, filters=filters):
            if row["chunk_id"] not in seen_chunk_ids:
                title_rows.append(row)
                seen_chunk_ids.add(row["chunk_id"])

    keyword_rows: list[dict] = []
    keyword_document_ids: set[str] = set()
    city_words = {
        strip_accents(word).lower()
        for word in re.findall(
            r"[^\W\d_][\w\-']*",
            str(filters.get("city") or ""),
            flags=re.UNICODE,
        )
    }
    for keyword in extract_capitalized_keywords(query):
        if strip_accents(keyword).lower() in city_words:
            continue
        for row in _run_keyword_search(keyword, limit=40, filters=filters):
            if (
                row["chunk_id"] not in seen_chunk_ids
                and row["document_id"] not in keyword_document_ids
            ):
                keyword_rows.append(row)
                seen_chunk_ids.add(row["chunk_id"])
                keyword_document_ids.add(row["document_id"])

    results = _rows_to_results(rows, "mistral_pgvector_v2")
    if keyword_rows:
        # A query can contain several capitalized words (for example a title
        # plus a street). Keep keyword recall without allowing generic terms
        # such as "Avenue" to displace every semantic/title result.
        keyword_cap = max(1, limit // 2)
        results = (
            _rows_to_results(keyword_rows[:keyword_cap], "keyword_match_v2")
            + results
        )
    if title_rows:
        results = _rows_to_results(title_rows, "title_match_v2") + results

    return results[:limit]
