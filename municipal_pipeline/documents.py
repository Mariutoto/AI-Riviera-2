from __future__ import annotations

from typing import Any

from municipal_pipeline.municipalities import Municipality


REQUIRED_DOCUMENT_FIELDS = (
    "city",
    "city_key",
    "category",
    "document_type",
    "title",
    "listing_year",
    "pdf_url",
    "source_page",
)


def municipal_document(
    *,
    municipality: Municipality,
    category: str,
    title: str,
    listing_year: str,
    pdf_url: str,
    source_page: str,
    **metadata: Any,
) -> dict[str, Any]:
    document = {
        "city": municipality.label,
        "commune": municipality.label,
        "city_key": municipality.key,
        "category": category,
        "document_type": category,
        "title": title.strip(),
        "listing_year": str(listing_year),
        "pdf_url": pdf_url.strip(),
        "source_page": source_page.strip(),
        **metadata,
    }
    validate_document(document)
    return document


def validate_document(document: dict[str, Any]) -> None:
    missing = [
        field for field in REQUIRED_DOCUMENT_FIELDS if not document.get(field)
    ]
    if missing:
        raise ValueError(
            "Métadonnées documentaires incomplètes: " + ", ".join(missing)
        )
