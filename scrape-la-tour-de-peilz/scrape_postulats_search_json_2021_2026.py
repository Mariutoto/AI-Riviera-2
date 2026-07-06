from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

import requests


SCRAPER_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRAPER_DIR))

import scrape_postulats_2021_2026 as legacy
import scrape_motions_search_json_2021_2026 as faceted


ENDPOINT = faceted.ENDPOINT
SOURCE_PAGE = faceted.SOURCE_PAGE
CATEGORY_ID = "6"
YEARS = faceted.YEARS
PAGE_SIZE = 25
HEADERS = {"User-Agent": "AI-Riviera postulats faceted JSON importer"}


def parse_result_html(result_html: str, years: set[str] = YEARS) -> list[dict]:
    records, seen = [], set()
    for match in faceted.RESULT_RE.finditer(result_html):
        title = faceted.clean_html(match.group("title"))
        subject = faceted.clean_html(match.group("subject"))
        category = faceted.clean_html(match.group("category"))
        year = match.group("year")
        pdf_url = faceted.normalize_pdf_url(match.group("href"))
        if year not in years or not re.match(r"^Postulat(?:\s|$)", title, flags=re.I):
            continue
        if category.casefold() != "motions, postulats et interpellations":
            continue
        if not pdf_url or "motions-postulats" not in pdf_url.casefold() or pdf_url in seen:
            continue
        seen.add(pdf_url)
        status_raw, status_normalized = legacy.motion_tools.parse_listing_status(title)
        filename = legacy.safe_filename(pdf_url)
        records.append({
            "commune": "La Tour-de-Peilz", "type": "postulat", "document_type": "postulat",
            "year": year, "listing_year": year, "category": "postulats", "legislature": "2021-2026",
            "title": title, "summary": subject, "filename": filename, "pdf_url": pdf_url,
            "source_page": SOURCE_PAGE, "source_endpoint": ENDPOINT,
            "source_collection": "faceted-search:motions-postulats-interpellations",
            "source_category_id": CATEGORY_ID, "canonical_object": True,
            "political_object_id": f"postulat-{year}-{legacy.slugify(subject or title or filename)}",
            "site_listing_title": title, "site_subject": subject,
            "site_status_raw": status_raw, "status": status_raw,
            "status_normalized": status_normalized, "authors": legacy.parse_authors_from_listing(title),
        })
    return records


def fetch_page(page: int, session: requests.Session | None = None) -> dict:
    client = session or requests.Session()
    response = client.get(ENDPOINT, params={
        "searchdoc": "Postulat", "categories[]": CATEGORY_ID, "sorting": "rang",
        "direction": "DESC", "page": page, "qty": PAGE_SIZE,
    }, headers=HEADERS, timeout=30)
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
            by_url[item["pdf_url"]] = item
    items = sorted(by_url.values(), key=lambda item: (item["listing_year"], item["filename"]))
    return items, {"endpoint_rows": int(first["rows"]), "pages_fetched": pages, "postulats_2021_2026": len(items), "unique_pdf_urls": len(by_url)}


def compare_with_legacy(items: list[dict]) -> dict:
    new_urls = {x["pdf_url"] for x in items}
    old_urls = {x["pdf_url"] for x in legacy.collect_items_legacy_page()}
    return {"new_count": len(new_urls), "legacy_count": len(old_urls), "common": len(new_urls & old_urls), "only_in_new": sorted(new_urls-old_urls), "only_in_legacy": sorted(old_urls-new_urls), "same_url_set": new_urls == old_urls}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--compare-existing", action="store_true")
    args = parser.parse_args()
    items, diagnostics = collect_items()
    report = {"source": ENDPOINT, "diagnostics": diagnostics, "documents": items}
    if args.compare_existing:
        report["legacy_comparison"] = compare_with_legacy(items)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({k:v for k,v in report.items() if k != "documents"}, ensure_ascii=False, indent=2))
    print(f"Documents: {len(items)}")


if __name__ == "__main__":
    main()
