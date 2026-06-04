from __future__ import annotations

import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from app.text_cleaning import strip_accents


POLITICAL_TYPES = {
    "motions": ("motion", "political_motion"),
    "postulats": ("postulat", "political_postulate"),
    "interpellations": ("interpellation", "political_interpellation"),
}

CATEGORY_CONTENT_KIND = {
    "budgets": "municipal_budget",
    "budget": "municipal_budget",
    "preavis-municipaux": "municipal_preavis",
    "proces-verbaux": "council_minutes",
    "ordres-du-jour": "council_agenda",
    "communications-municipales": "municipal_communication",
    "informations-diverses": "misc_information",
    "infos-municipalite": "municipality_news",
    "rapport-de-gestion": "annual_management_report",
    "rapport-gestion": "annual_management_report",
    "rapport-des-comptes": "annual_accounts_report",
    "conseil-communal": "institutional_document",
    "institutionnel": "institutional_document",
    "autres": "other_document",
}

CATEGORY_TEMPLATE_SOURCE = {
    "budget": "01-budget.enriched.json",
    "budgets": "01-budget.enriched.json",
    "preavis-municipaux": "02-preavis-municipal.enriched.json",
    "proces-verbaux": "03-proces-verbal.enriched.json",
    "ordres-du-jour": "04-ordre-du-jour.enriched.json",
    "communications-municipales": "05-communication-municipale.enriched.json",
    "informations-diverses": "06-information-diverse.enriched.json",
    "infos-municipalite": "07-info-municipalite.enriched.json",
    "motions": "08-motion.enriched.json",
    "postulats": "09-postulat.enriched.json",
    "interpellations": "10-interpellation.enriched.json",
    "rapport-de-gestion": "11-rapport-gestion.enriched.json",
    "rapport-gestion": "11-rapport-gestion.enriched.json",
    "rapport-des-comptes": "11-rapport-gestion.enriched.json",
    "conseil-communal": "12-institutionnel.enriched.json",
    "institutionnel": "12-institutionnel.enriched.json",
    "autres": "12-institutionnel.enriched.json",
}

CATEGORY_SEARCH_FACETS = {
    "budgets": ["budget", "finances"],
    "budget": ["budget", "finances"],
    "preavis-municipaux": ["preavis", "municipalite", "conseil_communal"],
    "proces-verbaux": ["proces_verbal", "conseil_communal", "seance"],
    "ordres-du-jour": ["ordre_du_jour", "conseil_communal", "seance"],
    "communications-municipales": ["communication_municipale", "municipalite"],
    "informations-diverses": ["information_diverse"],
    "infos-municipalite": ["infos_municipalite", "municipalite"],
    "rapport-de-gestion": ["rapport_gestion", "rapport_annuel", "municipalite"],
    "rapport-gestion": ["rapport_gestion", "rapport_annuel", "municipalite"],
    "rapport-des-comptes": ["rapport_comptes", "comptes", "finances"],
    "conseil-communal": ["institutionnel", "conseil_communal", "reglement"],
    "institutionnel": ["institutionnel", "reglement"],
    "autres": ["document", "information"],
    "motions": ["motion", "conseil_communal", "municipalite"],
    "postulats": ["postulat", "conseil_communal", "municipalite"],
    "interpellations": ["interpellation", "conseil_communal", "municipalite"],
}


def normalize(text: str) -> str:
    return strip_accents(str(text or "")).lower()


def display_name(raw_name: str) -> str:
    name = re.sub(r"\s+", " ", raw_name).strip(" ,.;:-")
    name = re.sub(r"^(M\.|Mme|MM\.|Mmes)\s+", "", name, flags=re.IGNORECASE)
    return name.strip()


def extract_author_party_pairs(title: str) -> list[dict[str, str | None]]:
    title = str(title or "")
    if not title:
        return []

    intro = re.split(r"\s+-\s+|\s+\"", title, maxsplit=1)[0]
    intro = re.sub(r"^(Motion|Postulat|Interpellation)\s+(de|du|des)\s+", "", intro, flags=re.IGNORECASE).strip()
    intro = re.sub(r"\s+\+\s+R[e\u00e9]ponse.*$", "", intro, flags=re.IGNORECASE).strip()
    matches = re.findall(
        r"\b(?:Mme|M\.|MM\.|Mmes)\s+([^()]+?)\s*\(([A-Z0-9/-]{2,})\)",
        intro,
        flags=re.IGNORECASE,
    )
    pairs = []
    for raw_name, party in matches:
        name = display_name(raw_name)
        if name:
            pairs.append({"name": name, "party": party})
    if pairs:
        return pairs

    group_match = re.search(r"\bgroupe\s+([A-Z0-9/-]{2,})\b", intro, flags=re.IGNORECASE)
    if group_match:
        party = group_match.group(1)
        return [{"name": f"groupe {party}", "party": party}]
    return []


def infer_legislature(year: Any) -> str | None:
    try:
        numeric_year = int(str(year))
    except (TypeError, ValueError):
        return None
    if 2021 <= numeric_year <= 2026:
        return "2021-2026"
    return None


def infer_status(title: str, filename: str) -> str | None:
    haystack = normalize(f"{title} {filename}")
    if "renvoye directement" in haystack or "renvoye a la municipalite" in haystack:
        return "renvoye_municipalite"
    if "retire par le postulant" in haystack or "retiree par le postulant" in haystack:
        return "retire"
    if "+ reponse" in haystack or (
        filename.lower().startswith(("interpellation-", "motion-", "postulat-")) and "-rep" in filename.lower()
    ):
        return "depot_avec_reponse"
    if filename.lower().startswith("reponse-"):
        return "reponse"
    return "depot"


def infer_category(metadata: dict[str, Any], text_path: Path | None = None) -> str:
    category = str(metadata.get("category") or metadata.get("doc_type") or "")
    if category:
        return category
    if text_path and len(text_path.parts) >= 2:
        return text_path.parts[-2]
    return ""


def infer_content_kind(metadata: dict[str, Any], category: str) -> str:
    filename = normalize(metadata.get("filename", ""))
    explicit_type = normalize(metadata.get("type", ""))
    if category in POLITICAL_TYPES:
        return POLITICAL_TYPES[category][1]
    if explicit_type in {"motion", "postulat", "interpellation"}:
        return f"political_{explicit_type}"
    if "rapport" in filename and "gestion" in filename:
        return "annual_management_report"
    return CATEGORY_CONTENT_KIND.get(category, "municipal_document")


def merge_unique(existing: Any, additions: list[str]) -> list[str]:
    values = []
    for value in existing if isinstance(existing, list) else []:
        if isinstance(value, str) and value not in values:
            values.append(value)
    for value in additions:
        if value and value not in values:
            values.append(value)
    return values


def enrich_metadata(metadata: dict[str, Any], text_path: Path | None = None, content: str | None = None) -> dict[str, Any]:
    enriched = deepcopy(metadata)
    category = infer_category(enriched, text_path)
    filename = str(enriched.get("filename") or (text_path.with_suffix(".pdf").name if text_path else ""))
    title = str(enriched.get("title") or filename)
    year = enriched.get("year") or (text_path.parts[-3] if text_path and len(text_path.parts) >= 3 else "")
    content_kind = infer_content_kind(enriched, category)

    enriched.setdefault("metadata_version", "metadata-audit-v1")
    enriched.setdefault("metadata_template", CATEGORY_TEMPLATE_SOURCE.get(category))
    enriched.setdefault("commune", "La Tour-de-Peilz")
    enriched.setdefault("category", category)
    enriched.setdefault("content_kind", content_kind)
    enriched.setdefault("language", "fr")
    enriched.setdefault("year", str(year) if year != "" else "")
    enriched.setdefault("listing_year", enriched.get("year"))
    enriched.setdefault("filename", filename)
    enriched.setdefault("title", title)

    legislature = infer_legislature(enriched.get("year"))
    if legislature:
        enriched.setdefault("legislature", legislature)

    facets = CATEGORY_SEARCH_FACETS.get(category, [])
    enriched["search_facets"] = merge_unique(enriched.get("search_facets"), facets)

    characters_extracted = enriched.get("characters_extracted")
    if characters_extracted is None and content is not None:
        characters_extracted = len(content)
        enriched["characters_extracted"] = characters_extracted
    enriched.setdefault(
        "text_extraction_status",
        {
            "characters_extracted": characters_extracted,
            "text_available": bool(characters_extracted),
            "needs_ocr": False,
        },
    )

    if category in POLITICAL_TYPES:
        object_type, _ = POLITICAL_TYPES[category]
        status = enriched.get("status") or infer_status(title, filename)
        authors = enriched.get("authors") or extract_author_party_pairs(title)
        enriched["authors"] = authors
        enriched.setdefault(
            "political_object",
            {
                "type": object_type,
                "status": status,
                "target_body": "Municipalite",
                "decision_body": "Conseil communal",
            },
        )
        if isinstance(enriched["political_object"], dict):
            enriched["political_object"].setdefault("type", object_type)
            enriched["political_object"].setdefault("status", status)
            enriched["political_object"].setdefault("target_body", "Municipalite")
            enriched["political_object"].setdefault("decision_body", "Conseil communal")

    if content_kind == "annual_management_report":
        enriched.setdefault(
            "report",
            {
                "fiscal_year": enriched.get("year"),
                "report_scope": "municipal_activity",
                "issuing_body": "Municipalite",
                "target_body": "Conseil communal",
                "large_document": True,
            },
        )

    return enriched
