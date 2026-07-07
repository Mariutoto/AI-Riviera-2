from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from urllib.parse import unquote

import requests


SCRAPER_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRAPER_DIR))

import scrape_motions_search_json_2021_2026 as faceted
import scrape_preavis_municipaux_2021_2026 as legacy


ENDPOINT = faceted.ENDPOINT
SOURCE_PAGE = faceted.SOURCE_PAGE
CATEGORY_ID = "3"
YEARS = faceted.YEARS
PAGE_SIZE = 25
HEADERS = {"User-Agent": "AI-Riviera preavis faceted JSON importer"}


def infer_listing_role(title: str) -> str:
    normalized = legacy.normalize(title)
    has_report = "rapport" in normalized
    has_decision = "decision" in normalized
    if has_report and has_decision:
        return "combined_preavis_report_decision"
    if has_report:
        return "combined_preavis_report"
    if has_decision:
        return "combined_preavis_decision"
    return "municipal_preavis"


def parse_filename_number(filename: str, listing_year: str) -> str | None:
    match = re.search(r"Pr[ée]avis[-_ ]?(\d{1,2})(?:[-_]|$)", filename, flags=re.I)
    return f"{int(match.group(1))}/{listing_year}" if match else None


def parse_result_html(result_html: str, years: set[str] = YEARS) -> list[dict]:
    records, seen = [], set()
    for match in faceted.RESULT_RE.finditer(result_html):
        source_title = faceted.clean_html(match.group("title"))
        subject = faceted.clean_html(match.group("subject"))
        source_category = faceted.clean_html(match.group("category"))
        listing_year = match.group("year")
        file_url = faceted.normalize_pdf_url(match.group("href"))
        if listing_year not in years:
            continue
        if not re.match(
            r"^(?:Pr[ée]avis municipal|Compl[ée]ment au pr[ée]avis(?: municipal)?)\b",
            source_title,
            flags=re.I,
        ):
            continue
        if source_category.casefold() != "préavis municipaux":
            continue
        if not file_url or "preavis-municipaux" not in unquote(file_url).casefold() or file_url in seen:
            continue
        seen.add(file_url)
        filename = legacy.safe_filename(file_url)
        preavis_number = legacy.parse_preavis_number(source_title, filename, subject)
        filename_preavis_number = parse_filename_number(filename, listing_year)
        listing_status = legacy.parse_listing_status(source_title)
        normalized_title = legacy.normalize(source_title)
        records.append({
            # Socle documentaire commun.
            "commune": "La Tour-de-Peilz",
            "document_family": "municipal_proposal",
            "category": "preavis_municipal",
            "document_role": infer_listing_role(source_title),
            "title": subject or source_title,
            "source_title": source_title,
            "source_page_url": SOURCE_PAGE,
            "file_url": file_url,
            "filename": filename,
            "listing_year": int(listing_year),
            "legislature": "2021-2026",
            # Métadonnées propres aux préavis, issues du listing uniquement.
            "preavis_number": preavis_number,
            "listing_preavis_number": preavis_number,
            "filename_preavis_number": filename_preavis_number,
            "source_number_conflict": bool(
                filename_preavis_number and preavis_number != filename_preavis_number
            ),
            "political_status": listing_status,
            "listing_contains_report": "rapport" in normalized_title,
            "listing_contains_decision": "decision" in normalized_title,
            "listing_withdrawn": listing_status == "withdrawn_by_municipality",
            "is_complement": legacy.normalize(source_title).startswith("complement au preavis"),
            # Provenance de collecte. L'identifiant de catégorie reste au niveau du scraper.
            "source_endpoint": ENDPOINT,
            "source_collection": "faceted-search:preavis-municipaux",
            "canonical_document": True,
        })
    return records


def fetch_page(page: int, session: requests.Session | None = None) -> dict:
    client = session or requests.Session()
    response = client.get(
        ENDPOINT,
        params={
            "searchdoc": "Préavis",
            "categories[]": CATEGORY_ID,
            "sorting": "rang",
            "direction": "DESC",
            "page": page,
            "qty": PAGE_SIZE,
        },
        headers=HEADERS,
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    for field in ("result", "rows", "qty", "active"):
        if field not in payload:
            raise ValueError(f"Champ JSON absent: {field}")
    return payload


def collect_items(session: requests.Session | None = None) -> tuple[list[dict], dict]:
    first = fetch_page(1, session)
    pages = max(1, math.ceil(int(first["rows"]) / int(first["qty"])))
    payloads = [first] + [fetch_page(page, session) for page in range(2, pages + 1)]
    by_url = {}
    for payload in payloads:
        for item in parse_result_html(payload["result"]):
            by_url[item["file_url"]] = item
    items = sorted(
        by_url.values(),
        key=lambda item: (item["listing_year"], item.get("preavis_number") or "99/9999", item["filename"]),
    )
    diagnostics = {
        "endpoint_rows_all_years": int(first["rows"]),
        "pages_fetched": pages,
        "preavis_2021_2026": len(items),
        "unique_pdf_urls": len(by_url),
        "with_report": sum(item["listing_contains_report"] for item in items),
        "with_decision": sum(item["listing_contains_decision"] for item in items),
        "withdrawn": sum(item["listing_withdrawn"] for item in items),
        "missing_preavis_number": sum(not item["preavis_number"] for item in items),
        "source_number_conflicts": sum(item["source_number_conflict"] for item in items),
    }
    return items, diagnostics


def compare_with_legacy(items: list[dict]) -> dict:
    new_urls = {item["file_url"] for item in items}
    old_urls = {item["pdf_url"] for item in legacy.collect_items()}
    return {
        "new_count": len(new_urls),
        "legacy_count": len(old_urls),
        "common": len(new_urls & old_urls),
        "only_in_new": sorted(new_urls - old_urls),
        "only_in_legacy": sorted(old_urls - new_urls),
        "same_url_set": new_urls == old_urls,
    }


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Collecte JSON des préavis municipaux 2021-2026")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--compare-existing", action="store_true")
    args = parser.parse_args()
    items, diagnostics = collect_items()
    report = {
        "source": {
            "page": SOURCE_PAGE,
            "endpoint": ENDPOINT,
            "endpoint_category_id": CATEGORY_ID,
            "note": "L'identifiant 3 sert à la requête et n'entre pas dans les métadonnées documentaires.",
        },
        "schema": {
            "base_fields": [
                "commune", "document_family", "category", "document_role", "title", "source_title",
                "source_page_url", "file_url", "filename", "listing_year", "legislature",
            ],
            "preavis_fields": [
                "preavis_number", "political_status", "listing_contains_report",
                "listing_contains_decision", "listing_withdrawn", "is_complement",
            ],
            "deferred_to_pdf_audit": [
                "document_date", "decision_date", "commission", "financial_amounts",
                "contains_majority_report", "contains_minority_report", "processing",
            ],
        },
        "diagnostics": diagnostics,
        "documents": items,
    }
    if args.compare_existing:
        report["legacy_comparison"] = compare_with_legacy(items)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "documents"}, ensure_ascii=False, indent=2))
    print(f"Documents: {len(items)}")


if __name__ == "__main__":
    main()
