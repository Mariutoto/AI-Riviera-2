from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from urllib.parse import unquote

import requests

SCRAPER_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRAPER_DIR))

import scrape_motions_search_json_2021_2026 as faceted
import scrape_budgets_2021_2026 as legacy

ENDPOINT = faceted.ENDPOINT
SOURCE_PAGE = faceted.SOURCE_PAGE
CATEGORY_ID = "4"
PAGE_SIZE = 25
YEARS = {str(year) for year in range(2021, 2027)}
HEADERS = {"User-Agent": "AI-Riviera budget faceted JSON importer"}


def parse_result_html(result_html: str, years: set[str] = YEARS) -> list[dict]:
    records, seen = [], set()
    for match in faceted.RESULT_RE.finditer(result_html):
        source_title = faceted.clean_html(match.group("title"))
        subject = faceted.clean_html(match.group("subject"))
        source_category = faceted.clean_html(match.group("category"))
        listing_year = match.group("year")
        file_url = faceted.normalize_pdf_url(match.group("href"))
        if listing_year not in years or source_title.casefold() != "budget":
            continue
        if source_category.casefold() != "rapport de gestion – comptes – budget".casefold():
            continue
        if not file_url or "/budget/" not in unquote(file_url).casefold() or file_url in seen:
            continue
        seen.add(file_url)
        records.append({
            "commune": "La Tour-de-Peilz",
            "document_family": "financial_plan",
            "category": "budget",
            "document_role": "annual_budget",
            "title": f"Budget {listing_year}",
            "source_title": source_title,
            "source_subject": subject,
            "source_page_url": SOURCE_PAGE,
            "file_url": file_url,
            "filename": legacy.safe_filename(file_url),
            "listing_year": int(listing_year),
            "legislature": "2021-2026",
            "fiscal_year": int(listing_year),
            "period_start": f"{listing_year}-01-01",
            "period_end": f"{listing_year}-12-31",
            "source_endpoint": ENDPOINT,
            "source_collection": "faceted-search:rapport-gestion-comptes-budget",
            "canonical_document": True,
        })
    return records


def fetch_page(page: int, session: requests.Session | None = None) -> dict:
    client = session or requests.Session()
    response = client.get(ENDPOINT, params={
        "searchdoc": "Budget", "categories[]": CATEGORY_ID, "sorting": "rang",
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
            by_url[item["file_url"]] = item
    items = sorted(by_url.values(), key=lambda item: item["fiscal_year"])
    return items, {
        "endpoint_rows_all_years": int(first["rows"]), "pages_fetched": pages,
        "budgets_2021_2026": len(items), "unique_pdf_urls": len(by_url),
        "missing_fiscal_year": sum(not item.get("fiscal_year") for item in items),
    }


def compare_with_legacy(items: list[dict]) -> dict:
    new_urls = {item["file_url"] for item in items}
    old_urls = {item["pdf_url"] for item in legacy.collect_items()}
    return {
        "new_count": len(new_urls), "legacy_count": len(old_urls),
        "common": len(new_urls & old_urls), "only_in_new": sorted(new_urls - old_urls),
        "only_in_legacy": sorted(old_urls - new_urls), "same_url_set": new_urls == old_urls,
    }


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Collecte JSON des budgets 2021-2026")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--compare-existing", action="store_true")
    args = parser.parse_args()
    items, diagnostics = collect_items()
    report = {
        "source": {"page": SOURCE_PAGE, "endpoint": ENDPOINT, "endpoint_category_id": CATEGORY_ID,
                   "note": "L'identifiant 4 sert uniquement à la collecte."},
        "schema": {
            "base_fields": ["commune", "document_family", "category", "document_role", "title",
                            "source_title", "source_page_url", "file_url", "filename", "listing_year", "legislature"],
            "budget_fields_from_endpoint": ["fiscal_year", "period_start", "period_end"],
            "deferred_to_pdf_audit": ["document_date", "components", "total_expenses", "total_revenues",
                                      "projected_surplus_or_deficit", "processing"],
            "cleaning_policy": {
                "remove": ["page_numbers", "repeated_running_headers", "repeated_running_footers", "postal_contact_blocks"],
                "preserve": ["department_headings", "table_titles", "table_rows", "budget_comments", "preavis_text"],
            },
        },
        "diagnostics": diagnostics, "documents": items,
    }
    if args.compare_existing:
        report["legacy_comparison"] = compare_with_legacy(items)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "documents"}, ensure_ascii=False, indent=2))
    print(f"Documents: {len(items)}")


if __name__ == "__main__":
    main()
