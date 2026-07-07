from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import date
from pathlib import Path
from urllib.parse import unquote

import requests


SCRAPER_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRAPER_DIR))

import scrape_motions_search_json_2021_2026 as faceted
import scrape_proces_verbaux_2021_2026 as legacy


ENDPOINT = faceted.ENDPOINT
SOURCE_PAGE = faceted.SOURCE_PAGE
CATEGORY_ID = "8"
PAGE_SIZE = 25
YEARS = {str(year) for year in range(2021, 2027)}
HEADERS = {"User-Agent": "AI-Riviera proces-verbaux faceted JSON importer"}

ITEM_RE = re.compile(
    r'<div class="ik-callout-info[^>]*>[\s\S]*?'
    r'<a[^>]+href="(?P<href>[^"]+)"[^>]*>[\s\S]*?'
    r'<h4[^>]*>(?P<title>[\s\S]*?)</h4>[\s\S]*?</a>\s*'
    r'<div class="lssrchres">(?P<subject>[\s\S]*?)</div>\s*'
    r'<div[^>]*>\s*(?P<breadcrumb>[\s\S]*?)</div>',
    flags=re.I,
)

MONTHS = {
    "janvier": 1, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6,
    "juillet": 7, "aout": 8, "septembre": 9, "octobre": 10, "novembre": 11, "decembre": 12,
}


def normalize(value: str) -> str:
    return faceted.legacy.strip_accents(faceted.clean_html(value)).casefold()


def parse_title(title: str) -> tuple[int | None, str | None]:
    number_match = re.search(r"N\s*[°o]?\s*(\d{1,3})", title, flags=re.I)
    date_match = re.search(r"(?:seance|s[ée]ance)\s+du\s+(\d{1,2})\s+([A-Za-zÀ-ÿ]+)\s+(20\d{2})", title, flags=re.I)
    number = int(number_match.group(1)) if number_match else None
    session_date = None
    if date_match:
        month = MONTHS.get(normalize(date_match.group(2)))
        if month:
            session_date = date(int(date_match.group(3)), month, int(date_match.group(1))).isoformat()
    return number, session_date


def parse_result_html(result_html: str, years: set[str] = YEARS) -> list[dict]:
    records, seen = [], set()
    for match in ITEM_RE.finditer(result_html):
        source_title = faceted.clean_html(match.group("title"))
        subject = faceted.clean_html(match.group("subject"))
        breadcrumb = faceted.clean_html(match.group("breadcrumb"))
        file_url = faceted.normalize_pdf_url(match.group("href"))
        if "proces verbaux" not in normalize(breadcrumb) or "2021-2026" not in breadcrumb:
            continue
        if not file_url or "proces-verbaux" not in unquote(file_url).casefold() or file_url in seen:
            continue
        filename = legacy.safe_filename(file_url)
        pv_number, session_date = parse_title(source_title)
        filename_data = legacy.parse_pv_filename(filename)
        if pv_number is None and filename_data:
            pv_number = filename_data["pv_number"]
        if session_date is None and filename_data:
            session_date = filename_data["session_date"]
        listing_year = (session_date or "")[:4]
        if listing_year not in years:
            continue
        seen.add(file_url)
        is_installation = "installation" in normalize(source_title + " " + filename)
        records.append({
            "commune": "La Tour-de-Peilz",
            "document_family": "council_session",
            "category": "proces_verbal",
            "document_role": "council_minutes",
            "title": source_title,
            "source_title": source_title,
            "source_page_url": SOURCE_PAGE,
            "file_url": file_url,
            "filename": filename,
            "listing_year": int(listing_year),
            "legislature": "2021-2026",
            "pv_number": pv_number,
            "session_date": session_date,
            "session_type": "installation" if is_installation else "ordinary",
            "is_installation_session": is_installation,
            "source_endpoint": ENDPOINT,
            "source_collection": "faceted-search:proces-verbaux",
            "canonical_document": True,
            "site_subject": subject or None,
        })
    return records


def fetch_page(page: int, session: requests.Session | None = None) -> dict:
    client = session or requests.Session()
    response = client.get(
        ENDPOINT,
        params={
            "searchdoc": "Procès-verbal",
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
    items = sorted(by_url.values(), key=lambda item: (item["session_date"], item["pv_number"] or 999))
    diagnostics = {
        "endpoint_rows_all_legislatures": int(first["rows"]),
        "pages_fetched": pages,
        "proces_verbaux_2021_2026": len(items),
        "unique_pdf_urls": len(by_url),
        "missing_pv_number": sum(item["pv_number"] is None for item in items),
        "missing_session_date": sum(item["session_date"] is None for item in items),
        "installation_sessions": sum(item["is_installation_session"] for item in items),
    }
    return items, diagnostics


def compare_with_legacy(items: list[dict]) -> dict:
    new_urls = {item["file_url"] for item in items}
    old_urls = set(legacy.collect_pv_urls())
    return {
        "new_count": len(new_urls), "legacy_count": len(old_urls),
        "common": len(new_urls & old_urls), "only_in_new": sorted(new_urls - old_urls),
        "only_in_legacy": sorted(old_urls - new_urls), "same_url_set": new_urls == old_urls,
    }


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Collecte JSON des procès-verbaux 2021-2026")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--compare-existing", action="store_true")
    args = parser.parse_args()
    items, diagnostics = collect_items()
    report = {
        "source": {
            "page": SOURCE_PAGE, "endpoint": ENDPOINT, "endpoint_category_id": CATEGORY_ID,
            "note": "L'identifiant 8 sert uniquement à la collecte et n'entre pas dans les métadonnées documentaires.",
        },
        "schema": {
            "base_fields": [
                "commune", "document_family", "category", "document_role", "title", "source_title",
                "source_page_url", "file_url", "filename", "listing_year", "legislature",
            ],
            "minutes_fields_from_endpoint": ["pv_number", "session_date", "session_type"],
            "deferred_to_pdf_audit": [
                "meeting_start_time", "meeting_end_time", "presiding_officer", "secretary",
                "attendance", "agenda_item_count", "contains_votes", "processing",
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
