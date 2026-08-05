import re

from app.pilot_v2_store import search as search_pilot_v2
from app.text_cleaning import strip_accents
from municipal_pipeline.municipalities import MUNICIPALITIES

# (normalized name, commune label) pairs, longest normalized name first so a
# substring like "vevey" doesn't win over "corsier-sur-vevey" when both would
# match. Built once from the municipality registry rather than hardcoded so
# renamed/added communes stay in sync automatically.
_CITY_NAME_LOOKUP = sorted(
    (
        (strip_accents(name).lower(), municipality.label)
        for municipality in MUNICIPALITIES.values()
        if municipality.search_enabled
        for name in (municipality.label, *municipality.aliases)
    ),
    key=lambda pair: len(pair[0]),
    reverse=True,
)


def _detect_city(normalized_query: str) -> str | None:
    for normalized_name, label in _CITY_NAME_LOOKUP:
        if re.search(rf"\b{re.escape(normalized_name)}\b", normalized_query):
            return label
    return None


def is_council_regulation_query(query: str) -> bool:
    normalized = strip_accents(query).lower()
    has_regulation = any(term in normalized for term in ["reglement", "rcc"])
    has_article = any(term in normalized for term in ["article", "articles"])
    has_council = "conseil" in normalized or "communal" in normalized
    return has_regulation or (has_article and has_council)


def _detect_doc_type(query: str, normalized_query: str) -> str | None:
    # Check the specific political-object keywords first: a motion or
    # interpellation whose own title happens to mention "règlement du
    # Conseil communal" must not be rerouted to the regulation document
    # itself (is_council_regulation_query is only a fallback for
    # queries that aren't already about one of these object types).
    if "interpellation" in normalized_query:
        return "interpellations"
    if "postulat" in normalized_query:
        return "postulats"
    if "motion" in normalized_query:
        return "motions"
    if "budget" in normalized_query:
        return "budget"
    if is_council_regulation_query(query):
        return "reglement-conseil-communal"
    return None


def _detect_year(normalized_query: str) -> str | None:
    year_match = re.search(r"\b(20\d{2})\b", normalized_query)
    return year_match.group(1) if year_match else None


_AGGREGATE_MARKERS = ("combien de", "combien d'", "liste tous", "liste toutes", "quel est le nombre de")
_CIVILITY_MARKERS = {"femmes": "Mme", "femme": "Mme", "hommes": "M.", "homme": "M."}
# Only these doc_types are "countable" in the sense this function means —
# political objects with many instances to count/list by author. "budget" and
# "reglement-conseil-communal" are also detected doc_types (for regular
# retrieval filtering) but there's exactly one document per year/one
# document total for those, so "combien de ..." near one of those keywords is
# almost always a monetary or article-count question ("combien de charges",
# "combien d'articles"), not a request to enumerate documents.
_COUNTABLE_DOC_TYPES = {"interpellations", "postulats", "motions"}


def detect_answered_interpellations_query(query: str) -> dict | None:
    """Detect an enumeration of interpellations that actually received a response.

    This needs a structured relationship query, not semantic retrieval: a top-K
    sample can contain unanswered interpellations and an LLM can attach the PDF
    citation from one municipality to an object from another.

    A year in this wording qualifies the response date (for example, "ont reçu
    une réponse en 2025"), not merely the filing year of the interpellation.
    """
    normalized_query = strip_accents(query).lower()
    enumeration = re.search(
        r"\b(?:quel(?:le)?s?|liste(?:r)?|combien)\b.*\binterpellations?\b",
        normalized_query,
    )
    answered = any(
        marker in normalized_query
        for marker in (
            "ont recu une reponse",
            "ayant recu une reponse",
            "avec une reponse",
            "avec reponse",
            "reponse disponible",
            "reponses disponibles",
            "reponse fournie",
            "reponses fournies",
        )
    )
    if not enumeration or not answered:
        return None

    filters: dict = {
        "doc_type": "interpellations",
        "response_available": True,
    }
    year = _detect_year(normalized_query)
    if year:
        filters["response_year"] = year
    city = _detect_city(normalized_query)
    if city:
        filters["city"] = city
    return filters


def detect_answered_postulates_query(query: str) -> dict | None:
    """Detect an enumeration of postulates with an actual linked response."""
    normalized_query = strip_accents(query).lower()
    enumeration = re.search(
        r"\b(?:quel(?:le)?s?|liste(?:r)?|combien)\b.*\bpostulats?\b",
        normalized_query,
    )
    answered = any(
        marker in normalized_query
        for marker in (
            "ont recu une reponse",
            "ayant recu une reponse",
            "avec une reponse",
            "avec reponse",
            "reponse disponible",
            "reponses disponibles",
            "reponse fournie",
            "reponses fournies",
        )
    )
    if not enumeration or not answered:
        return None
    filters: dict = {
        "doc_type": "postulats",
        "response_available": True,
    }
    year = _detect_year(normalized_query)
    if year:
        filters["response_year"] = year
    city = _detect_city(normalized_query)
    if city:
        filters["city"] = city
    return filters


def detect_aggregate_query(query: str) -> dict | None:
    """Detect "combien de ..." / "liste tous les ..." questions that need a real
    count/enumeration over structured metadata rather than semantic search over
    chunk text — a top-K passage sample can't answer these reliably.

    Returns None if the question isn't this shape; otherwise a filters dict
    (doc_type/year/civility, whichever apply) for pilot_v2_store.aggregate_authors.
    """
    normalized_query = strip_accents(query).lower()
    political_enumeration = re.search(
        r"\b(?:quel|quels|quelle|quelles)\b.*"
        r"\b(?:interpellations|postulats|motions)\b",
        normalized_query,
    )
    if (
        not any(marker in normalized_query for marker in _AGGREGATE_MARKERS)
        and not political_enumeration
    ):
        return None

    filters: dict = {}
    for word, civility in _CIVILITY_MARKERS.items():
        if re.search(rf"\b{word}\b", normalized_query):
            filters["civility"] = civility
            break

    doc_type = _detect_doc_type(query, normalized_query)
    if doc_type in _COUNTABLE_DOC_TYPES:
        filters["doc_type"] = doc_type

    city = _detect_city(normalized_query)
    if city:
        filters["city"] = city

    if "civility" not in filters and "doc_type" not in filters:
        # Without a recognized countable entity, "combien de/liste tous" is
        # almost always a different kind of question (an amount, a
        # percentage, an article count...) that a document-count/enumeration
        # can't answer — fall through to normal search instead of silently
        # counting every document that matches the year.
        return None

    year = _detect_year(normalized_query)
    if year:
        filters["year"] = year

    return filters


def search(query: str, limit: int = 6, filters: dict | None = None) -> list[dict]:
    filters = dict(filters or {})
    normalized_query = strip_accents(query).lower()

    if not filters.get("doc_type"):
        doc_type = _detect_doc_type(query, normalized_query)
        if doc_type:
            filters["doc_type"] = doc_type

    if not filters.get("year"):
        year = _detect_year(normalized_query)
        if year:
            filters["year"] = year

    if not filters.get("article_number"):
        article_match = re.search(r"\barticles?\s+(\d{1,4})\b", normalized_query)
        if article_match:
            filters["article_number"] = article_match.group(1)

    return search_pilot_v2(query, limit=limit, filters=filters)
