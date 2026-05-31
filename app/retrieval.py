import json
import math
import re
from collections import Counter
from functools import lru_cache

from app.config import CHUNKS_PATH
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


def search(query: str, limit: int = 6) -> list[dict]:
    query_tokens = Counter(tokenize(query))
    if not query_tokens:
        return []

    broad_legislature_query = is_broad_legislature_query(query, query_tokens)
    council_vote_query = is_council_vote_query(query)
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

    if sqlite_ready():
        sqlite_results = search_sqlite(query, list(query_tokens.elements()), limit=limit)
        if sqlite_results:
            return sqlite_results

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
