from __future__ import annotations

import argparse
import html
import json
import math
import re
import sys
from pathlib import Path
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

import scrape_motions_2021_2026 as legacy


BASE_URL = "https://www.la-tour-de-peilz.ch/"
ENDPOINT = "https://www.la-tour-de-peilz.ch/srch/faceted.php"
SOURCE_PAGE = "https://www.la-tour-de-peilz.ch/srch/"
CATEGORY_ID = "6"
YEARS = {str(year) for year in range(2021, 2027)}
PAGE_SIZE = 25
HEADERS = {"User-Agent": "AI-Riviera motions faceted JSON importer"}

RESULT_RE = re.compile(
    r'<div class="ik-callout-info[^>]*>[\s\S]*?'
    r'<a[^>]+href="(?P<href>[^"]+)"[^>]*>[\s\S]*?'
    r'<h4[^>]*>(?P<title>[\s\S]*?)</h4>[\s\S]*?</a>\s*'
    r'<div class="lssrchres">(?P<subject>[\s\S]*?)</div>\s*'
    r'<div[^>]*>\s*(?P<category>[^<]*?)\s*/\s*(?P<year>20\d{2})\s*</div>',
    flags=re.I,
)


def clean_html(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", html.unescape(value)).strip()
    return legacy.clean_french_text(value)


def normalize_pdf_url(href: str) -> str | None:
    full_url = urljoin(BASE_URL, html.unescape(href))
    if "viewer.php" in full_url:
        file_value = parse_qs(urlparse(full_url).query).get("file", [""])[0]
        if file_value:
            full_url = urljoin(BASE_URL, unquote(file_value))
    return full_url if urlparse(full_url).path.lower().endswith(".pdf") else None


def parse_result_html(result_html: str, years: set[str] = YEARS) -> list[dict]:
    records = []
    seen_urls = set()
    for match in RESULT_RE.finditer(result_html):
        title = clean_html(match.group("title"))
        subject = clean_html(match.group("subject"))
        category = clean_html(match.group("category"))
        year = match.group("year")
        pdf_url = normalize_pdf_url(match.group("href"))
        if year not in years:
            continue
        if not re.match(r"^Motion\s", title, flags=re.I):
            continue
        if category.casefold() != "motions, postulats et interpellations":
            continue
        if not pdf_url or "motions-postulats" not in unquote(pdf_url).casefold():
            continue
        if pdf_url in seen_urls:
            continue
        seen_urls.add(pdf_url)
        status_raw, status_normalized = legacy.parse_listing_status(title)
        filename = legacy.safe_filename(pdf_url)
        records.append(
            {
                "commune": "La Tour-de-Peilz",
                "type": "motion",
                "document_type": "motion",
                "year": year,
                "listing_year": year,
                "category": "motions",
                "legislature": "2021-2026",
                "title": title,
                "summary": subject,
                "filename": filename,
                "pdf_url": pdf_url,
                "source_page": SOURCE_PAGE,
                "source_endpoint": ENDPOINT,
                "source_collection": "faceted-search:motions-postulats-interpellations",
                "source_category_id": CATEGORY_ID,
                "canonical_object": True,
                "political_object_id": f"motion-{year}-{legacy.slugify(subject or title or filename)}",
                "site_listing_title": title,
                "site_subject": subject,
                "site_status_raw": status_raw,
                "status": status_raw,
                "status_normalized": status_normalized,
                "authors": legacy.parse_authors_from_listing(title),
            }
        )
    return records


def fetch_page(page: int, session: requests.Session | None = None) -> dict:
    client = session or requests.Session()
    response = client.get(
        ENDPOINT,
        params={
            "searchdoc": "Motion",
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
    for required in ("result", "rows", "qty", "active"):
        if required not in payload:
            raise ValueError(f"Champ JSON absent: {required}")
    return payload


def collect_items(session: requests.Session | None = None) -> tuple[list[dict], dict]:
    first = fetch_page(1, session=session)
    total_rows = int(first["rows"])
    page_size = int(first["qty"])
    page_count = max(1, math.ceil(total_rows / page_size))
    payloads = [first]
    for page in range(2, page_count + 1):
        payloads.append(fetch_page(page, session=session))

    by_url = {}
    for payload in payloads:
        for item in parse_result_html(payload["result"]):
            by_url[item["pdf_url"]] = item
    items = sorted(by_url.values(), key=lambda item: (item["listing_year"], item["filename"]))
    diagnostics = {
        "endpoint_rows": total_rows,
        "pages_fetched": page_count,
        "motions_2021_2026": len(items),
        "unique_pdf_urls": len(by_url),
    }
    return items, diagnostics


def compare_with_legacy(items: list[dict]) -> dict:
    current = {item["pdf_url"] for item in items}
    previous = {item["pdf_url"] for item in legacy.collect_items()}
    return {
        "new_count": len(current),
        "legacy_count": len(previous),
        "common": len(current & previous),
        "only_in_new": sorted(current - previous),
        "only_in_legacy": sorted(previous - current),
        "same_url_set": current == previous,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Collecte JSON des motions 2021-2026")
    parser.add_argument("--output", type=Path, help="Écrire le manifeste JSON à cet emplacement")
    parser.add_argument("--compare-existing", action="store_true", help="Comparer avec l'ancien scraper")
    args = parser.parse_args()

    items, diagnostics = collect_items()
    report = {"source": ENDPOINT, "diagnostics": diagnostics, "documents": items}
    if args.compare_existing:
        report["legacy_comparison"] = compare_with_legacy(items)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "documents"}, ensure_ascii=False, indent=2))
    print(f"Documents: {len(items)}")


if __name__ == "__main__":
    main()
