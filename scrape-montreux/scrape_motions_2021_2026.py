from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import importlib.util
import json
import re
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BASE_SCRAPER = PROJECT_ROOT / "scrape-montreux" / "scrape_interpellations_2021_2026.py"
SPEC = importlib.util.spec_from_file_location("montreux_interpellation_source", BASE_SCRAPER)
assert SPEC and SPEC.loader
base = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(base)

POSTULATS_SCRAPER = PROJECT_ROOT / "scrape-montreux" / "scrape_postulats_2021_2026.py"
POSTULATS_SPEC = importlib.util.spec_from_file_location("montreux_postulate_source", POSTULATS_SCRAPER)
assert POSTULATS_SPEC and POSTULATS_SPEC.loader
postulats = importlib.util.module_from_spec(POSTULATS_SPEC)
POSTULATS_SPEC.loader.exec_module(postulats)

SEARCH_ENDPOINT = base.SEARCH_ENDPOINT
DETAIL_PAGE = base.DETAIL_PAGE
SESSIONS_PAGE = postulats.SESSIONS_PAGE
HEADERS = base.HEADERS
MOTION_FILTER = "objectType:0|Motion"
RESPONSE_FILTERS = (
    "objectType:6|Rapport-préavis",
    "objectType:7|Rapport",
)
LEGISLATURE_START = date(2021, 7, 1)
LEGISLATURE_END = date(2026, 6, 30)

# The site re-lists a motion under a new source_object_id when the council
# votes on the rapport-préavis that answers it. 3362 is such a re-listing of
# 3024 ("Aménager sans attendre des points de baignades attractifs entre
# Territet et Clarens") for the 2026 final-decision sessions; its own
# commission-report attachment is folded into the canonical object 3024
# instead of creating a second political object for the same motion.
DUPLICATE_OBJECT_IDS = {3362: 3024}

# Audited from the full text of each response document (see
# consideration_date_from_response / manual verification): rapport-préavis
# 17/2025 (3247) answers two motions together, so it links to both.
RESPONSE_OBJECT_LINKS = {
    2859: [2736],
    3281: [2813],
    2962: [2828],
    3247: [2663, 3024],
}


def write_json(path: Path, value: object) -> None:
    postulats.write_json(path, value)


def normalized_words(value: str) -> str:
    return base.normalized_words(value)


def collect_index(object_filter: str) -> tuple[list[dict], dict]:
    previous = base.OBJECT_FILTER
    try:
        base.OBJECT_FILTER = object_filter
        return base.collect_index()
    finally:
        base.OBJECT_FILTER = previous


def fetch_detail(source_object_id: int) -> dict:
    return postulats.fetch_detail(source_object_id)


def collect_session_occurrences() -> tuple[dict[int, list[dict]], dict]:
    return postulats.collect_session_occurrences()


def parse_author(value: str) -> dict:
    author = base.parse_author(value)
    author["role"] = "motionnaire"
    return author


def status_normalized(value: str) -> str:
    return postulats.status_normalized(value)


def download_pdf(*args, **kwargs) -> dict:
    return postulats.download_pdf(*args, **kwargs)


def response_like_attachment(attachment: dict) -> bool:
    name = normalized_words(attachment.get("filename") or "")
    return "reponse" in name and (
        "motion" in name or not name.startswith("rapport")
    )


def primary_response_attachment(detail: dict) -> dict | None:
    return postulats.primary_response_attachment(detail)


STOPWORDS = {
    "motion", "pour", "une", "un", "de", "du", "des", "la", "le",
    "les", "et", "au", "aux", "en", "sur", "dans", "relatif", "relative",
    "reponse", "rapport", "preavis", "municipalite", "montreux", "commune",
    "monsieur", "madame", "conseiller", "conseillere", "communal", "communale",
    "intitule", "intitulee", "prise", "pris", "consideration",
}


def title_tokens(value: str) -> set[str]:
    return {
        token
        for token in normalized_words(value).split()
        if len(token) >= 3 and token not in STOPWORDS
    }


def response_match(motion: dict, response_document: dict) -> dict:
    title_norm = normalized_words(motion["title"])
    haystack = normalized_words(
        f"{response_document['source_title']} {response_document['extracted_text']}"
    )
    tokens = title_tokens(motion["title"])
    haystack_tokens = set(haystack.split())
    shared = tokens & haystack_tokens
    coverage = len(shared) / max(1, len(tokens))
    exact = bool(title_norm and title_norm in haystack)
    required = min(4, max(2, len(tokens)))
    matched = exact or (len(shared) >= required and coverage >= 0.72)
    return {
        "matched": matched,
        "exact_title": exact,
        "token_coverage": round(coverage, 4),
        "shared_tokens": sorted(shared),
    }


def deduplicate_documents(documents: list[dict]) -> tuple[list[dict], int]:
    return postulats.deduplicate_documents(documents)


def build_inventory(output_dir: Path) -> dict:
    motion_rows, motion_search = collect_index(MOTION_FILTER)
    occurrences, session_diagnostics = collect_session_occurrences()
    selected_rows = [
        row
        for row in motion_rows
        if row["source_object_id"] >= 2650
        and row["source_object_id"] in occurrences
    ]
    selected_ids = sorted(row["source_object_id"] for row in selected_rows)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        details = list(pool.map(fetch_detail, selected_ids))
    details_by_id = {row["source_object_id"]: row for row in details}

    canonical_rows = [
        row for row in selected_rows
        if row["source_object_id"] not in DUPLICATE_OBJECT_IDS
    ]
    objects: list[dict] = []
    for row in sorted(canonical_rows, key=lambda item: item["source_object_id"]):
        source_id = row["source_object_id"]
        detail = details_by_id[source_id]
        sessions = occurrences[source_id]
        attachments = list(detail["attachments"])
        alias_ids = [
            duplicate_id
            for duplicate_id, canonical_id in DUPLICATE_OBJECT_IDS.items()
            if canonical_id == source_id
        ]
        for alias_id in alias_ids:
            attachments.extend(details_by_id[alias_id]["attachments"])
        objects.append(
            {
                "source_object_id": source_id,
                "object_id": f"montreux-motion-{source_id}",
                "source_page": detail["source_page"],
                "title": detail["title"] or re.sub(r"^Motion\s+", "", row["title"]),
                "author_text": detail["author_text"] or row["author_text"],
                "authors": [parse_author(detail["author_text"] or row["author_text"])],
                "status": detail["status"],
                "status_normalized": status_normalized(detail["status"]),
                "session_occurrences": sessions,
                "deposit_date": sessions[0]["date"],
                "consideration_date": sessions[-1]["date"],
                "source_alias_ids": alias_ids,
                "attachments": attachments,
                "response_document_ids": [],
                "consideration_document_ids": [],
            }
        )

    documents: list[dict] = []
    download_jobs: list[tuple] = []
    for item in objects:
        for attachment in item["attachments"]:
            if not str(attachment.get("filename") or "").lower().endswith(".pdf"):
                continue
            download_id = attachment.get("download_id") or hashlib.sha1(
                attachment["pdf_url"].encode("utf-8")
            ).hexdigest()[:10]
            direct_response = response_like_attachment(attachment)
            role = "municipal_response" if direct_response else "consideration_report"
            suffix = "response" if direct_response else "report"
            document_id = f"montreux_motion_{item['source_object_id']}_{suffix}_{download_id}"
            download_jobs.append(
                (
                    attachment,
                    output_dir,
                    document_id,
                    role,
                    [item["object_id"]],
                    item["source_page"],
                    item["title"],
                )
            )
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        documents.extend(pool.map(lambda args: download_pdf(*args), download_jobs))

    response_rows: list[dict] = []
    response_search_diagnostics = []
    for response_filter in RESPONSE_FILTERS:
        rows, diagnostics = collect_index(response_filter)
        response_search_diagnostics.append({"filter": response_filter, **diagnostics})
        response_rows.extend(
            row
            for row in rows
            if "motion" in normalized_words(row["title"])
            and row["source_object_id"] >= min(selected_ids)
        )
    response_ids = sorted(
        {
            row["source_object_id"]
            for row in response_rows
            if row["source_object_id"] in RESPONSE_OBJECT_LINKS
        }
    )
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        response_details = list(pool.map(fetch_detail, response_ids))
    response_jobs: list[tuple] = []
    for detail in response_details:
        attachment = primary_response_attachment(detail)
        if not attachment or not str(attachment.get("filename") or "").lower().endswith(".pdf"):
            continue
        download_id = attachment.get("download_id") or str(detail["source_object_id"])
        document_id = f"montreux_motion_response_{detail['source_object_id']}_{download_id}"
        response_jobs.append(
            (
                attachment,
                output_dir,
                document_id,
                "municipal_response",
                [],
                detail["source_page"],
                detail["title"],
            )
        )
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        candidate_responses = list(pool.map(lambda args: download_pdf(*args), response_jobs))

    matches: list[dict] = []
    by_source_id = {item["source_object_id"]: item for item in objects}
    for document in candidate_responses:
        match = re.search(r"montreux_motion_response_(\d+)_", document["document_id"])
        if not match:
            continue
        response_source_id = int(match.group(1))
        for motion_source_id in RESPONSE_OBJECT_LINKS.get(response_source_id, []):
            canonical_id = DUPLICATE_OBJECT_IDS.get(motion_source_id, motion_source_id)
            item = by_source_id.get(canonical_id)
            if not item:
                continue
            audit_match = response_match(item, document)
            if item["object_id"] not in document["political_object_ids"]:
                document["political_object_ids"].append(item["object_id"])
            item["response_document_ids"].append(document["document_id"])
            matches.append(
                {
                    "object_id": item["object_id"],
                    "response_document_id": document["document_id"],
                    "audited_override": True,
                    **audit_match,
                }
            )
    candidate_responses = [
        document for document in candidate_responses if document["political_object_ids"]
    ]
    documents.extend(candidate_responses)
    documents, duplicates_removed = deduplicate_documents(documents)

    by_object = {item["object_id"]: item for item in objects}
    for document in documents:
        for object_id in document["political_object_ids"]:
            if document["document_role"] == "consideration_report":
                by_object[object_id]["consideration_document_ids"].append(document["document_id"])
            elif document["document_id"] not in by_object[object_id]["response_document_ids"]:
                by_object[object_id]["response_document_ids"].append(document["document_id"])
    for item in objects:
        item["response_document_ids"] = sorted(set(item["response_document_ids"]))
        item["consideration_document_ids"] = sorted(set(item["consideration_document_ids"]))
        linked = [
            document for document in documents
            if document["document_id"] in item["response_document_ids"]
        ]
        item["has_response"] = bool(linked)
        item["is_closed"] = item["has_response"] or item["status_normalized"] in {
            "rejected", "withdrawn", "closed"
        }
        item["response_status"] = "written_response" if linked else "unanswered"
        item["response_date"] = max(
            (document["document_date"] for document in linked if document["document_date"]),
            default="",
        )

    # Remove full extracted text from the inventory; it is reproducibly extracted again by build_audit.
    for document in documents:
        document.pop("extracted_text", None)
    diagnostics = {
        "complete": True,
        "endpoint_results": motion_search["endpoint_results"],
        "legislature_motions": len(objects),
        "duplicate_site_listings_folded": len(DUPLICATE_OBJECT_IDS),
        "documents_downloaded": len(documents),
        "consideration_reports": sum(d["document_role"] == "consideration_report" for d in documents),
        "municipal_responses": sum(d["document_role"] == "municipal_response" for d in documents),
        "objects_with_response": sum(item["has_response"] for item in objects),
        "objects_without_response": sum(not item["has_response"] for item in objects),
        "documents_needing_ocr": sum(d["text_audit"]["needs_ocr"] for d in documents),
        "response_matches": len(matches),
        "duplicate_document_rows_removed": duplicates_removed,
        **session_diagnostics,
        "motion_search": motion_search,
        "response_searches": response_search_diagnostics,
    }
    return {
        "schema_version": "montreux-motions-inventory-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "institution": "Conseil communal de Montreux",
            "search_page": DETAIL_PAGE,
            "sessions_page": SESSIONS_PAGE,
        },
        "scope": {
            "commune": "Montreux",
            "category": "motion",
            "legislature": "2021-2026",
            "start_date": LEGISLATURE_START.isoformat(),
            "end_date": LEGISLATURE_END.isoformat(),
        },
        "automatic_detection_notes": {
            "motion_filter": MOTION_FILTER,
            "response_object_types": list(RESPONSE_FILTERS),
            "session_linking": "official council-session pages",
            "response_linking": "audited: response titles name the motion author/title and "
                "consideration date explicitly; each link verified against the response PDF's "
                "own preamble text before being added to RESPONSE_OBJECT_LINKS",
            "duplicate_listings": "audited: the site re-lists a motion under a new "
                "source_object_id once its rapport-préavis reaches a final council vote; "
                "folded via DUPLICATE_OBJECT_IDS into the original object",
        },
        "diagnostics": diagnostics,
        "response_matches": matches,
        "objects": objects,
        "documents": documents,
    }


def main() -> None:
    root = PROJECT_ROOT / "audit-montreux" / "motions-2021-2026"
    parser = argparse.ArgumentParser(description="Scrape les motions de Montreux 2021-2026")
    parser.add_argument("--output", type=Path, default=root / "inventory.json")
    parser.add_argument("--download-dir", type=Path, default=root / "pdfs")
    args = parser.parse_args()
    inventory = build_inventory(args.download_dir)
    write_json(args.output, inventory)
    print(json.dumps(inventory["diagnostics"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
