from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse


POLITICAL_CATEGORIES = {"motions", "postulats", "interpellations"}
POLITICAL_TYPES = {"motion", "postulat", "interpellation"}
SESSION_CATEGORIES = {"ordres-du-jour", "proces-verbaux"}
ANNUAL_CATEGORIES = {"budget", "rapport-de-gestion", "rapport-des-comptes"}
SIMPLE_PUBLICATION_CATEGORIES = {
    "communications-municipales",
    "infos-municipalite",
    "informations-diverses",
}


def first_year(value: Any) -> str:
    match = re.search(r"\b(20\d{2})\b", str(value or ""))
    return match.group(1) if match else ""


def iso_year(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) >= 4 and text[:4].isdigit():
        return text[:4]
    return first_year(text)


def source_url(metadata: dict[str, Any]) -> str:
    return str(metadata.get("pdf_url") or metadata.get("source_url") or metadata.get("url") or "")


def pdf_storage_year(metadata: dict[str, Any], metadata_path: Path | None = None) -> str:
    url = source_url(metadata)
    if url:
        match = re.search(r"/(20\d{2})/", unquote(urlparse(url).path))
        if match:
            return match.group(1)
    if metadata_path:
        for part in metadata_path.parts:
            if re.fullmatch(r"20\d{2}", part):
                return part
    return str(metadata.get("year") or metadata.get("listing_year") or "")


def object_id_year(metadata: dict[str, Any]) -> str:
    political_object = metadata.get("political_object") if isinstance(metadata.get("political_object"), dict) else {}
    object_id = str(
        metadata.get("political_object_id")
        or metadata.get("related_political_object_id")
        or political_object.get("object_id")
        or political_object.get("political_object_id")
        or ""
    )
    match = re.search(r"-(20\d{2})-", object_id)
    return match.group(1) if match else ""


def category(metadata: dict[str, Any]) -> str:
    return str(metadata.get("doc_type") or metadata.get("category") or "").lower()


def object_type(metadata: dict[str, Any]) -> str:
    political_object = metadata.get("political_object") if isinstance(metadata.get("political_object"), dict) else {}
    return str(
        metadata.get("type")
        or metadata.get("document_type")
        or political_object.get("object_type")
        or political_object.get("type")
        or ""
    ).lower()


def is_political_document(metadata: dict[str, Any]) -> bool:
    return category(metadata) in POLITICAL_CATEGORIES or object_type(metadata) in POLITICAL_TYPES


def is_canonical_political_document(metadata: dict[str, Any]) -> bool:
    return is_political_document(metadata) and (
        metadata.get("canonical_object") is True
        or bool(metadata.get("political_object_id"))
        or bool((metadata.get("political_object") or {}).get("object_id"))
    )


def infer_object_year(metadata: dict[str, Any]) -> str:
    political_object = metadata.get("political_object") if isinstance(metadata.get("political_object"), dict) else {}
    for value in [
        metadata.get("object_year"),
        political_object.get("object_year"),
        metadata.get("deposit_date"),
        political_object.get("deposit_date"),
        object_id_year(metadata),
        metadata.get("listing_year"),
        metadata.get("document_date"),
        political_object.get("document_date"),
    ]:
        year = iso_year(value)
        if year:
            return year
    return str(metadata.get("year") or "")


def infer_document_year(metadata: dict[str, Any], metadata_path: Path | None = None) -> str:
    for value in [
        metadata.get("document_year"),
        metadata.get("document_date"),
        metadata.get("publication_date"),
        metadata.get("session_date"),
        metadata.get("date"),
        pdf_storage_year(metadata, metadata_path),
        metadata.get("listing_year"),
        metadata.get("year"),
    ]:
        year = iso_year(value)
        if year:
            return year
    return ""


def infer_publication_date(metadata: dict[str, Any]) -> str:
    for key in ("publication_date", "document_date", "date", "fetch_date", "fetched_at"):
        value = metadata.get(key)
        if value and iso_year(value):
            return str(value)[:10]
    return ""


def linked_political_object_ids(metadata: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for item in metadata.get("linked_canonical_objects") or []:
        if isinstance(item, dict):
            object_id = item.get("political_object_id") or item.get("object_id")
            if object_id:
                ids.append(str(object_id))
    for item in metadata.get("agenda") or []:
        if isinstance(item, dict):
            for linked in item.get("linked_canonical_objects") or []:
                if isinstance(linked, dict):
                    object_id = linked.get("political_object_id") or linked.get("object_id")
                    if object_id:
                        ids.append(str(object_id))
    return sorted(dict.fromkeys(ids))


def years_from_object_ids(object_ids: list[str]) -> list[str]:
    years = []
    for object_id in object_ids:
        match = re.search(r"-(20\d{2})-", object_id)
        if match:
            years.append(match.group(1))
    return sorted(dict.fromkeys(years))


def normalize_year_metadata(metadata: dict[str, Any], metadata_path: Path | None = None) -> dict[str, Any]:
    normalized = dict(metadata)
    cat = category(normalized)
    pdf_year = pdf_storage_year(normalized, metadata_path)
    document_year = infer_document_year(normalized, metadata_path)

    if pdf_year:
        normalized["pdf_storage_year"] = pdf_year
    if document_year:
        normalized["document_year"] = document_year

    if is_political_document(normalized):
        object_year = infer_object_year(normalized)
        if object_year:
            normalized["object_year"] = object_year
        role = str(normalized.get("document_role") or "")
        if role == "municipal_response":
            if normalized.get("contains_response") and normalized.get("document_date"):
                normalized.setdefault("response_date", str(normalized["document_date"])[:10])
        elif role == "combined_interpellation_response":
            document_date = str(normalized.get("document_date") or "")[:10]
            if document_date and normalized.get("response_date") == document_date:
                normalized.pop("response_date", None)
                political_object = normalized.get("political_object")
                if isinstance(political_object, dict) and political_object.get("response_date") == document_date:
                    political_object.pop("response_date", None)
        if normalized.get("decision") and isinstance(normalized["decision"], dict):
            decision_date = normalized["decision"].get("decision_date") or normalized["decision"].get("session_date")
            if decision_date:
                normalized.setdefault("decision_date", str(decision_date)[:10])
        if object_year and pdf_year and object_year != pdf_year:
            normalized["year_mismatch_reason"] = "pdf_published_or_updated_after_political_year"
        else:
            normalized.pop("year_mismatch_reason", None)

        political_object = normalized.get("political_object")
        if isinstance(political_object, dict):
            if object_year:
                political_object["object_year"] = object_year
            if pdf_year:
                political_object["pdf_storage_year"] = pdf_year
            if document_year:
                political_object["document_year"] = document_year
            if normalized.get("response_date"):
                political_object.setdefault("response_date", normalized["response_date"])
            if normalized.get("decision_date"):
                political_object.setdefault("decision_date", normalized["decision_date"])
            normalized["political_object"] = political_object

    elif cat in SESSION_CATEGORIES:
        session_year = iso_year(normalized.get("session_date") or normalized.get("document_date") or normalized.get("listing_year"))
        if session_year:
            normalized["session_year"] = session_year
        object_ids = linked_political_object_ids(normalized)
        if object_ids:
            normalized["linked_political_object_ids"] = object_ids
            normalized["linked_object_years"] = years_from_object_ids(object_ids)

    elif cat in ANNUAL_CATEGORIES:
        fiscal_year = str(normalized.get("fiscal_year") or normalized.get("reporting_year") or normalized.get("year") or "")
        if fiscal_year:
            normalized["fiscal_year"] = fiscal_year
            normalized["reporting_year"] = str(normalized.get("reporting_year") or fiscal_year)
        publication_year = iso_year(normalized.get("publication_date") or normalized.get("document_date") or normalized.get("year"))
        if publication_year:
            normalized["publication_year"] = publication_year

    elif cat in SIMPLE_PUBLICATION_CATEGORIES:
        publication_date = infer_publication_date(normalized)
        if publication_date:
            normalized["publication_date"] = publication_date

    if "listing_year" not in normalized and normalized.get("year"):
        normalized["listing_year"] = str(normalized["year"])

    return normalized
