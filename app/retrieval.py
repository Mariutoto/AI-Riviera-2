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
        # Check the specific political-object keywords first: a motion or
        # interpellation whose own title happens to mention "règlement du
        # Conseil communal" must not be rerouted to the regulation document
        # itself (is_council_regulation_query is only a fallback for
        # queries that aren't already about one of these object types).
        if "interpellation" in normalized_query:
            filters["doc_type"] = "interpellations"
        elif "postulat" in normalized_query:
            filters["doc_type"] = "postulats"
        elif "motion" in normalized_query:
            filters["doc_type"] = "motions"
        elif is_council_regulation_query(query):
            filters["doc_type"] = "reglement-conseil-communal"

    if not filters.get("year"):
        year_match = re.search(r"\b(20\d{2})\b", normalized_query)
        if year_match:
            filters["year"] = year_match.group(1)

    if not filters.get("article_number"):
        article_match = re.search(r"\barticles?\s+(\d{1,4})\b", normalized_query)
        if article_match:
            filters["article_number"] = article_match.group(1)

    return search_pilot_v2(query, limit=limit, filters=filters)
