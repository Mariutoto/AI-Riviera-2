import re

from app.pilot_v2_store import search as search_pilot_v2
from app.text_cleaning import strip_accents


def is_council_regulation_query(query: str) -> bool:
    normalized = strip_accents(query).lower()
    has_regulation = any(term in normalized for term in ["reglement", "rcc"])
    has_article = any(term in normalized for term in ["article", "articles"])
    has_council = "conseil" in normalized or "communal" in normalized
    return has_regulation or (has_article and has_council)


def search(query: str, limit: int = 6, filters: dict | None = None) -> list[dict]:
    filters = dict(filters or {})
    normalized_query = strip_accents(query).lower()

    if not filters.get("doc_type"):
        if is_council_regulation_query(query):
            filters["doc_type"] = "reglement-conseil-communal"
        elif "interpellation" in normalized_query:
            filters["doc_type"] = "interpellations"
        elif "postulat" in normalized_query:
            filters["doc_type"] = "postulats"
        elif "motion" in normalized_query:
            filters["doc_type"] = "motions"

    if not filters.get("year"):
        year_match = re.search(r"\b(20\d{2})\b", normalized_query)
        if year_match:
            filters["year"] = year_match.group(1)

    return search_pilot_v2(query, limit=limit, filters=filters)
