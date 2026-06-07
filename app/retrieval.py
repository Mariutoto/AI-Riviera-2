import json
import math
import re
from collections import Counter
from functools import lru_cache

from app.config import CHUNKS_PATH, ENABLE_LEGACY_JSON_FALLBACK, STORAGE_BACKEND
from app.hybrid_search import search_hybrid
from app.postgres_store import search_chunks as search_postgres_chunks
from app.sqlite_index import search_sqlite, sqlite_ready
from app.text_cleaning import strip_accents


STOPWORDS = {
    "alors", "avec", "aux", "ce", "ces", "dans", "de", "des", "du", "elle", "en", "est",
    "et", "il", "la", "le", "les", "leur", "mais", "ou", "où", "par", "pas", "plus",
    "pour", "que", "qui", "sur", "un", "une", "vous", "the", "and", "of", "to", "in",
    "commune", "communal", "communale", "communaux", "conseil", "conseils",
    "quel", "quelle", "quels", "quelles", "sont",
    "combien", "nombre", "fait", "faire", "fais", "deja", "déjà",
    "complet", "complete", "complète",
}

BROAD_LEGISLATURE_CATEGORIES = {
    "ordres-du-jour",
    "proces-verbaux",
    "motions",
    "postulats",
    "interpellations",
    "motions-postulats",
    "preavis-municipaux",
    "communications-municipales",
    "informations-diverses",
    "infos-municipalite",
    "conseil-communal",
}


def tokenize(text: str) -> list[str]:
    text = strip_accents(text)
    tokens = re.findall(r"[a-zA-ZÀ-ÿ0-9]{3,}", text.lower())
    return [token for token in tokens if token not in STOPWORDS]


def is_broad_legislature_query(query: str, query_tokens: Counter) -> bool:
    normalized = strip_accents(query).lower()
    if "legislature" not in normalized:
        return False
    broad_terms = {"bilan", "derniere", "passe", "quoi", "resume", "synthese"}
    return bool(broad_terms.intersection(query_tokens)) or "derniere legislature" in normalized


def is_council_vote_query(query: str) -> bool:
    normalized = strip_accents(query).lower()
    has_vote = any(term in normalized for term in ["vote", "votee", "voter", "votes", "votation"])
    has_popular_vote = any(
        term in normalized
        for term in ["referendum", "scrutin", "initiative", "vote populaire", "citoyen", "citoyenne"]
    )
    return has_vote and not has_popular_vote


def is_council_regulation_query(query: str) -> bool:
    normalized = strip_accents(query).lower()
    has_regulation = any(term in normalized for term in ["reglement", "rcc"])
    has_article = any(term in normalized for term in ["article", "articles"])
    has_council = "conseil" in normalized or "communal" in normalized
    return has_regulation or (has_article and has_council)


@lru_cache(maxsize=1)
def load_chunks() -> list[dict]:
    if not CHUNKS_PATH.exists():
        return []

    chunks = []
    with CHUNKS_PATH.open("r", encoding="utf-8") as file:
        for line in file:
            if line.strip():
                record = json.loads(line)
                record["_tokens"] = Counter(tokenize(record["text"]))
                chunks.append(record)
    return chunks


def search(query: str, limit: int = 6, filters: dict | None = None) -> list[dict]:
    query_tokens = Counter(tokenize(query))
    if not query_tokens:
        return []

    filters = dict(filters or {})
    normalized_query = strip_accents(query).lower()
    council_regulation_query = is_council_regulation_query(query)
    if not filters.get("doc_type"):
        if council_regulation_query:
            filters["doc_type"] = "reglement-conseil-communal"
            if "article" in normalized_query:
                filters["content_kind"] = "regulation_article"
        elif "interpellation" in normalized_query:
            filters["doc_type"] = "interpellations"
        elif "postulat" in normalized_query:
            filters["doc_type"] = "postulats"
        elif "motion" in normalized_query:
            filters["doc_type"] = "motions"
    if (
        council_regulation_query
        and filters.get("doc_type") == "reglement-conseil-communal"
        and "article" in normalized_query
        and not filters.get("content_kind")
    ):
        filters["content_kind"] = "regulation_article"
    if not filters.get("year"):
        year_match = re.search(r"\b(20\d{2})\b", normalized_query)
        if year_match:
            filters["year"] = year_match.group(1)

    broad_legislature_query = is_broad_legislature_query(query, query_tokens)
    council_vote_query = is_council_vote_query(query)
    if council_regulation_query:
        query_tokens.update(["reglement", "rcc", "article", "articles", "conseil", "communal"])
        if any(term in normalized_query for term in ["election", "president", "nomination"]):
            query_tokens.update(["election", "president", "nomination", "bureau"])
    if broad_legislature_query:
        query_tokens.update(
            [
                "2021",
                "2022",
                "2023",
                "2024",
                "2025",
                "2026",
                "seance",
                "proces",
                "verbal",
                "ordre",
                "jour",
                "motion",
                "postulat",
                "interpellation",
                "preavis",
                "communication",
                "objet",
                "divers",
            ]
        )
    if council_vote_query:
        query_tokens.update(
            [
                "vote",
                "conseil",
                "communal",
                "preavis",
                "proces",
                "verbal",
                "seance",
                "adopte",
                "accepte",
                "refuse",
                "conclusions",
                "decision",
                "amendement",
            ]
        )

    if STORAGE_BACKEND in {"sql", "hybrid", "postgres", "opensearch"}:
        hybrid_results = search_hybrid(query, limit=limit, filters=filters)
        if hybrid_results:
            return hybrid_results

        postgres_results = search_postgres_chunks(query, list(query_tokens.elements()), limit=limit, filters=filters)
        if postgres_results:
            return postgres_results

        if not ENABLE_LEGACY_JSON_FALLBACK:
            return []

    if STORAGE_BACKEND in {"json", "sqlite", "legacy"} or ENABLE_LEGACY_JSON_FALLBACK:
        if sqlite_ready():
            sqlite_results = search_sqlite(query, list(query_tokens.elements()), limit=limit)
            if sqlite_results:
                return sqlite_results
    else:
        return []

    chunks = load_chunks()
    total_chunks = max(len(chunks), 1)
    document_frequency = Counter()
    for chunk in chunks:
        for token in chunk["_tokens"]:
            document_frequency[token] += 1

    scored = []
    for chunk in chunks:
        score = 0.0
        for token, query_count in query_tokens.items():
            term_count = chunk["_tokens"].get(token, 0)
            if not term_count:
                continue
            idf = math.log((1 + total_chunks) / (1 + document_frequency[token])) + 1
            score += query_count * (1 + math.log(term_count)) * idf

        metadata = chunk.get("metadata", {})
        metadata_text = " ".join(
            str(metadata.get(key, ""))
            for key in ["title", "institutional_category", "category", "filename", "session_date"]
        )
        metadata_tokens = Counter(tokenize(metadata_text))
        for token, query_count in query_tokens.items():
            if metadata_tokens.get(token, 0):
                score += query_count * 6

        title_tokens = Counter(tokenize(str(metadata.get("title", ""))))
        for token, query_count in query_tokens.items():
            if title_tokens.get(token, 0):
                score += query_count * 18

        if broad_legislature_query:
            year = str(metadata.get("year", ""))
            category = str(metadata.get("category", ""))
            title = strip_accents(str(metadata.get("title", ""))).lower()
            filename = strip_accents(str(metadata.get("filename", ""))).lower()
            if year in {"2021", "2022", "2023", "2024", "2025", "2026"}:
                score += 8
            if category in BROAD_LEGISLATURE_CATEGORIES:
                score += 10
            if "vue ensemble" in title or "couverture-legislature" in filename:
                score += 120

        if score > 0:
            scored.append((score, chunk))

    scored.sort(key=lambda item: item[0], reverse=True)

    results = []
    for score, chunk in scored[:limit]:
        result = dict(chunk)
        result.pop("_tokens", None)
        result["score"] = round(score, 3)
        results.append(result)
    return results
