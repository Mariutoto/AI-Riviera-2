from app.text_cleaning import strip_accents


POLITICAL_DOCUMENT_CATEGORIES = {
    "motion": "motions",
    "postulat": "postulats",
    "interpellation": "interpellations",
}

POLITICAL_DOCUMENT_CATEGORY_TYPES = {
    "motions": "motion",
    "postulats": "postulat",
    "interpellations": "interpellation",
}


def infer_political_document_type(*values: str) -> str | None:
    """Infer motion/postulat/interpellation from titles, filenames, URLs or categories."""
    haystack = strip_accents(" ".join(value or "" for value in values)).lower()
    haystack = haystack.replace("motions-postulats", " ").replace("motions_postulats", " ")
    if "interpellation" in haystack:
        return "interpellation"
    if "postulat" in haystack:
        return "postulat"
    if "motion" in haystack:
        return "motion"
    return None


def category_for_political_type(object_type: str | None) -> str | None:
    return POLITICAL_DOCUMENT_CATEGORIES.get(object_type or "")


def political_type_for_category(category: str) -> str | None:
    return POLITICAL_DOCUMENT_CATEGORY_TYPES.get(category)


def normalize_document_category(category: str, *values: str) -> str:
    """Split the old motions-postulats bucket when the document itself gives a type."""
    if category in POLITICAL_DOCUMENT_CATEGORY_TYPES:
        return category

    object_type = infer_political_document_type(*values, category)
    if category == "motions-postulats" and object_type:
        return POLITICAL_DOCUMENT_CATEGORIES[object_type]
    if category == "motions-postulats":
        return "autres"
    return category
