import html
import json
import re
import sys
from pathlib import Path
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import fitz
import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.text_cleaning import clean_french_text


BASE_URL = "https://www.la-tour-de-peilz.ch/"
SOURCE_PAGE = "https://www.la-tour-de-peilz.ch/politique/rapport-comptes-budget.php"
YEARS = {str(year) for year in range(2021, 2027)}
OUTPUT_ROOT = PROJECT_ROOT / "documents" / "la-tour-de-peilz"
DATA_ROOT = PROJECT_ROOT / "data" / "financial-reports" / "la-tour-de-peilz"
HEADERS = {"User-Agent": "AI-Riviera budget importer"}


def fetch_text(url: str) -> str:
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    return response.text


def normalize_pdf_url(page_url: str, href: str) -> str | None:
    href = html.unescape(href)
    full_url = urljoin(page_url, href)

    if "viewer.php" in full_url and "file=" in full_url:
        file_value = parse_qs(urlparse(full_url).query).get("file", [""])[0]
        full_url = urljoin(BASE_URL, unquote(file_value))

    decoded = unquote(full_url)
    if ".pdf" not in decoded.lower():
        return None
    if "/budget/" not in decoded.lower():
        return None
    return full_url


def clean_html_text(raw_html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", raw_html)
    text = html.unescape(text)
    return clean_french_text(re.sub(r"\s+", " ", text)).strip()


def year_from_text(text: str) -> str | None:
    match = re.search(r"\b(2021|2022|2023|2024|2025|2026)\b", text)
    return match.group(1) if match else None


def safe_filename(pdf_url: str) -> str:
    name = Path(unquote(urlparse(pdf_url).path)).name
    return re.sub(r'[<>:"/\\|?*]', "_", name)


def collect_items() -> list[dict]:
    page_html = fetch_text(SOURCE_PAGE)
    items_by_url = {}

    for li in re.findall(r'<li class="prestation[^>]*>([\s\S]*?)</li>', page_html, flags=re.I):
        href_match = re.search(r'href=["\']([^"\']+)["\']', li, flags=re.I)
        if not href_match:
            continue

        title = clean_html_text(li)
        pdf_url = normalize_pdf_url(SOURCE_PAGE, href_match.group(1))
        year = year_from_text(f"{title} {href_match.group(1)} {pdf_url or ''}")
        if not year or year not in YEARS or not pdf_url:
            continue

        filename = safe_filename(pdf_url)
        items_by_url[pdf_url] = {
            "commune": "La Tour-de-Peilz",
            "type": "budget",
            "document_type": "budget_communal",
            "year": year,
            "category": "budget",
            "legislature": "2021-2026",
            "title": f"Budget {year}",
            "filename": filename,
            "pdf_url": pdf_url,
            "source_page": SOURCE_PAGE,
        }

    return list(sorted(items_by_url.values(), key=lambda item: item["year"]))


def extract_pdf_text(pdf_path: Path) -> str:
    document = fitz.open(pdf_path)
    return clean_french_text("\n".join(page.get_text() for page in document))


def download_and_extract(item: dict) -> dict:
    year = item["year"]
    filename = item["filename"]
    target_dir = OUTPUT_ROOT / year / "budget"
    target_dir.mkdir(parents=True, exist_ok=True)

    pdf_path = target_dir / filename
    txt_path = pdf_path.with_suffix(".txt")
    json_path = pdf_path.with_suffix(".json")

    if not pdf_path.exists():
        response = requests.get(item["pdf_url"], headers=HEADERS, timeout=90)
        response.raise_for_status()
        pdf_path.write_bytes(response.content)

    text = extract_pdf_text(pdf_path)
    txt_path.write_text(text + "\n", encoding="utf-8")

    metadata = {
        **item,
        "pdf_path": str(pdf_path),
        "text_path": str(txt_path),
        "characters_extracted": len(text),
    }
    json_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return metadata


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    items = collect_items()
    print(f"Found {len(items)} budget documents for 2021-2026.")

    results = []
    failures = []
    for index, item in enumerate(items, start=1):
        print(f"[{index}/{len(items)}] {item['year']} {item['filename']}")
        try:
            results.append(download_and_extract(item))
        except Exception as exc:
            failures.append({"pdf_url": item["pdf_url"], "filename": item["filename"], "error": str(exc)})
            print(f"  ERROR: {exc}")

    manifest = {
        "commune": "La Tour-de-Peilz",
        "legislature": "2021-2026",
        "source_page": SOURCE_PAGE,
        "scope_note": "Budgets communaux 2021-2026 de La Tour-de-Peilz.",
        "years": sorted(YEARS),
        "documents_downloaded": len(results),
        "failures": failures,
        "documents": results,
    }
    manifest_path = DATA_ROOT / "manifest_budgets_2021_2026.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"Downloaded/extracted: {len(results)}")
    print(f"Failures: {len(failures)}")
    print(f"Manifest: {manifest_path}")


if __name__ == "__main__":
    main()
