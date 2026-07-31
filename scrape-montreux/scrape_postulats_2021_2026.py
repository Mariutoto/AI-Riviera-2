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
from urllib.parse import parse_qs, urljoin, urlparse

import lxml.html
import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BASE_SCRAPER = PROJECT_ROOT / "scrape-montreux" / "scrape_interpellations_2021_2026.py"
SPEC = importlib.util.spec_from_file_location("montreux_interpellation_source", BASE_SCRAPER)
assert SPEC and SPEC.loader
base = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(base)

SEARCH_ENDPOINT = base.SEARCH_ENDPOINT
DETAIL_PAGE = base.DETAIL_PAGE
SESSIONS_PAGE = "https://www.conseilmontreux.ch/conseilcommunal/public/ordredujour/"
HEADERS = base.HEADERS
POSTULATE_FILTER = "objectType:4|Postulat"
RESPONSE_FILTERS = (
    "objectType:6|Rapport-préavis",
    "objectType:7|Rapport",
)
LEGISLATURE_START = date(2021, 7, 1)
LEGISLATURE_END = date(2026, 6, 30)

# Four records are present in the official Postulat catalogue but omitted from
# the session-page links. Their dates are fixed from the neighbouring council
# objects; 3198 is additionally stated verbatim in report-preavis 17/2025.
UNLINKED_POSTULATE_DATES = {
    2860: "2022-09-14",
    3181: "2024-12-11",
    3198: "2025-01-29",
    3268: "2025-09-03",
}

# Audited links to standalone municipal responses. Report-preavis 04/2025 has
# two versions on the site; only the final second report (3234) is canonical.
RESPONSE_OBJECT_LINKS = {
    3127: [2923],
    3234: [2721, 3112],
    3247: [2924, 3198],
    3359: [2750],
}


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalized_words(value: str) -> str:
    return base.normalized_words(value)


def collect_index(object_filter: str) -> tuple[list[dict], dict]:
    previous = base.OBJECT_FILTER
    try:
        base.OBJECT_FILTER = object_filter
        return base.collect_index()
    finally:
        base.OBJECT_FILTER = previous


def parse_status(page_html: str) -> str:
    document = lxml.html.fromstring(page_html)
    return base.compact(
        document.xpath(
            "string(//section[contains(@class,'tplArticleTrans')]"
            "//span[contains(@class,'badge')][1])"
        )
    )


def fetch_detail(source_object_id: int) -> dict:
    response = requests.get(
        DETAIL_PAGE,
        params={"id": source_object_id},
        headers=HEADERS,
        timeout=45,
    )
    response.raise_for_status()
    html = response.content.decode("utf-8")
    detail = base.parse_detail_page(html, source_object_id)
    detail["status"] = parse_status(html)
    return detail


def parse_session_date(value: str) -> str:
    parsed = base.parse_french_date(value)
    return parsed.isoformat() if parsed else ""


def collect_session_occurrences() -> tuple[dict[int, list[dict]], dict]:
    listing = requests.get(SESSIONS_PAGE, headers=HEADERS, timeout=45)
    listing.raise_for_status()
    document = lxml.html.fromstring(listing.content.decode("utf-8"))
    urls = sorted(
        {
            urljoin(SESSIONS_PAGE, href)
            for href in document.xpath(
                "//a[contains(@href,'ordredujour/?ID=')]/@href"
            )
        }
    )

    def fetch(url: str) -> tuple[str, str, list[int]]:
        response = requests.get(url, headers=HEADERS, timeout=45)
        response.raise_for_status()
        page = lxml.html.fromstring(response.content.decode("utf-8"))
        heading = base.compact(page.xpath("string(//h1[1])"))
        session_date = parse_session_date(heading)
        source_ids: list[int] = []
        for href in page.xpath(
            "//a[contains(@href,'documents/recherche')]/@href"
        ):
            query = {
                key.lower(): values
                for key, values in parse_qs(urlparse(href).query).items()
            }
            values = query.get("id") or []
            if values and values[0].isdigit():
                source_ids.append(int(values[0]))
        return url, session_date, source_ids

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        sessions = list(pool.map(fetch, urls))
    occurrences: dict[int, list[dict]] = defaultdict(list)
    for url, session_date, source_ids in sessions:
        if not session_date:
            continue
        parsed = date.fromisoformat(session_date)
        if not (LEGISLATURE_START <= parsed <= LEGISLATURE_END):
            continue
        for source_id in source_ids:
            occurrences[source_id].append({"date": session_date, "url": url})
    for rows in occurrences.values():
        rows.sort(key=lambda row: (row["date"], row["url"]))
    return dict(occurrences), {
        "session_pages_fetched": len(urls),
        "legislature_object_ids_mapped": len(occurrences),
    }


def parse_author(value: str) -> dict:
    author = base.parse_author(value)
    author["role"] = "postulant"
    return author


def status_normalized(value: str) -> str:
    normalized = normalized_words(value)
    if normalized.startswith("accepte"):
        return "accepted"
    if normalized.startswith("refuse"):
        return "rejected"
    if normalized.startswith("retire"):
        return "withdrawn"
    if normalized.startswith("classe"):
        return "closed"
    return "pending" if not normalized else normalized.replace(" ", "_")


def document_bytes(url: str) -> bytes:
    response = requests.get(url, headers=HEADERS, timeout=90)
    response.raise_for_status()
    if not response.content.startswith(b"%PDF"):
        raise ValueError(f"Source Montreux non PDF: {url}")
    return response.content


def download_pdf(
    attachment: dict,
    output_dir: Path,
    document_id: str,
    role: str,
    political_object_ids: list[str],
    source_page: str,
    source_title: str,
) -> dict:
    content = document_bytes(attachment["pdf_url"])
    extracted, audit = base.pdf_text(content)
    target = output_dir / f"{document_id}.pdf"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return {
        **attachment,
        "document_id": document_id,
        "document_role": role,
        "political_object_ids": political_object_ids,
        "source_page": source_page,
        "source_title": source_title,
        "document_date": attachment.get("attachment_date") or "",
        "local_pdf": str(target.relative_to(PROJECT_ROOT)).replace("\\", "/"),
        "sha256": hashlib.sha256(content).hexdigest(),
        "text_audit": audit,
        "extracted_text": extracted,
    }


def response_like_attachment(attachment: dict) -> bool:
    name = normalized_words(attachment.get("filename") or "")
    return "reponse" in name and (
        "postulat" in name or not name.startswith("rapport")
    )


def primary_response_attachment(detail: dict) -> dict | None:
    attachments = detail.get("attachments") or []
    if not attachments:
        return None
    preferred = [
        item
        for item in attachments
        if normalized_words(item.get("displayed_type") or "") == "preavis"
    ]
    if not preferred:
        preferred = [
            item
            for item in attachments
            if "rapport" in normalized_words(item.get("filename") or "")
        ]
    return preferred[0] if preferred else attachments[0]


STOPWORDS = {
    "postulat", "pour", "une", "un", "de", "du", "des", "la", "le",
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


def response_match(postulate: dict, response_document: dict) -> dict:
    title_norm = normalized_words(postulate["title"])
    haystack = normalized_words(
        f"{response_document['source_title']} {response_document['extracted_text']}"
    )
    tokens = title_tokens(postulate["title"])
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


def consideration_date_from_response(title: str, text: str) -> str:
    value = f"{title} {text[:8000]}"
    match = re.search(
        r"pris(?:e)?\s+en\s+consid[ée]ration\s+le\s+"
        r"(\d{1,2}(?:er)?\s+[A-Za-zÀ-ÿ]+\s+20\d{2})",
        value,
        re.I,
    )
    return base.iso_date(match.group(1)) if match else ""


def deduplicate_documents(documents: list[dict]) -> tuple[list[dict], int]:
    grouped: dict[tuple, list[dict]] = defaultdict(list)
    for document in documents:
        extracted = normalized_words(document.get("extracted_text") or "")
        content_identity = (
            hashlib.sha256(extracted.encode("utf-8")).hexdigest()
            if len(extracted) >= 120
            else document["sha256"]
        )
        grouped[
            (
                document["document_role"],
                content_identity,
            )
        ].append(document)
    canonical = []
    removed = 0
    for group in grouped.values():
        selected = max(
            group,
            key=lambda item: (
                item.get("attachment_date") or "",
                int(item.get("download_id") or 0),
            ),
        )
        selected["source_download_aliases"] = sorted(
            {
                item.get("download_id") or ""
                for item in group
                if item["document_id"] != selected["document_id"]
            }
            - {""}
        )
        selected["political_object_ids"] = sorted(
            {
                object_id
                for item in group
                for object_id in item["political_object_ids"]
            }
        )
        canonical.append(selected)
        removed += len(group) - 1
    canonical.sort(key=lambda item: item["document_id"])
    return canonical, removed


def build_inventory(output_dir: Path) -> dict:
    postulate_rows, postulate_search = collect_index(POSTULATE_FILTER)
    occurrences, session_diagnostics = collect_session_occurrences()
    selected_rows = [
        row
        for row in postulate_rows
        if row["source_object_id"] >= 2690
        and (
            row["source_object_id"] in occurrences
            or row["source_object_id"] in UNLINKED_POSTULATE_DATES
        )
    ]
    selected_ids = sorted(row["source_object_id"] for row in selected_rows)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        details = list(pool.map(fetch_detail, selected_ids))
    details_by_id = {row["source_object_id"]: row for row in details}

    objects: list[dict] = []
    for row in sorted(selected_rows, key=lambda item: item["source_object_id"]):
        source_id = row["source_object_id"]
        detail = details_by_id[source_id]
        sessions = occurrences.get(source_id) or [
            {
                "date": UNLINKED_POSTULATE_DATES[source_id],
                "url": SESSIONS_PAGE,
                "date_source": "audited_catalogue_override",
            }
        ]
        objects.append(
            {
                "source_object_id": source_id,
                "object_id": f"montreux-postulate-{source_id}",
                "source_page": detail["source_page"],
                "title": detail["title"] or re.sub(r"^Postulat\s+", "", row["title"]),
                "author_text": detail["author_text"] or row["author_text"],
                "authors": [parse_author(detail["author_text"] or row["author_text"])],
                "status": detail["status"],
                "status_normalized": status_normalized(detail["status"]),
                "session_occurrences": sessions,
                "deposit_date": sessions[0]["date"],
                "consideration_date": sessions[-1]["date"],
                "attachments": detail["attachments"],
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
            document_id = f"montreux_postulate_{item['source_object_id']}_{suffix}_{download_id}"
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
            if "postulat" in normalized_words(row["title"])
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
        document_id = f"montreux_postulate_response_{detail['source_object_id']}_{download_id}"
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
        match = re.search(r"montreux_postulate_response_(\d+)_", document["document_id"])
        if not match:
            continue
        response_source_id = int(match.group(1))
        for postulate_source_id in RESPONSE_OBJECT_LINKS.get(response_source_id, []):
            item = by_source_id.get(postulate_source_id)
            if not item:
                continue
            audit_match = response_match(item, document)
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
        "endpoint_results": postulate_search["endpoint_results"],
        "legislature_postulates": len(objects),
        "documents_downloaded": len(documents),
        "consideration_reports": sum(d["document_role"] == "consideration_report" for d in documents),
        "municipal_responses": sum(d["document_role"] == "municipal_response" for d in documents),
        "objects_with_response": sum(item["has_response"] for item in objects),
        "objects_without_response": sum(not item["has_response"] for item in objects),
        "documents_needing_ocr": sum(d["text_audit"]["needs_ocr"] for d in documents),
        "response_matches": len(matches),
        "duplicate_document_rows_removed": duplicates_removed,
        **session_diagnostics,
        "postulate_search": postulate_search,
        "response_searches": response_search_diagnostics,
    }
    return {
        "schema_version": "montreux-postulates-inventory-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "institution": "Conseil communal de Montreux",
            "search_page": DETAIL_PAGE,
            "sessions_page": SESSIONS_PAGE,
        },
        "scope": {
            "commune": "Montreux",
            "category": "postulat",
            "legislature": "2021-2026",
            "start_date": LEGISLATURE_START.isoformat(),
            "end_date": LEGISLATURE_END.isoformat(),
        },
        "automatic_detection_notes": {
            "postulate_filter": POSTULATE_FILTER,
            "response_object_types": list(RESPONSE_FILTERS),
            "session_linking": "official council-session pages",
            "response_linking": "exact title or audited token coverage in official response PDF",
        },
        "diagnostics": diagnostics,
        "response_matches": matches,
        "objects": objects,
        "documents": documents,
    }


def main() -> None:
    root = PROJECT_ROOT / "audit-montreux" / "postulats-2021-2026"
    parser = argparse.ArgumentParser(description="Scrape les postulats de Montreux 2021-2026")
    parser.add_argument("--output", type=Path, default=root / "inventory.json")
    parser.add_argument("--download-dir", type=Path, default=root / "pdfs")
    args = parser.parse_args()
    inventory = build_inventory(args.download_dir)
    write_json(args.output, inventory)
    print(json.dumps(inventory["diagnostics"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
