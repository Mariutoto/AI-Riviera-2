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
SOURCE_PAGE = "https://www.la-tour-de-peilz.ch/politique/preavis-municipaux.php"
YEARS = {str(year) for year in range(2021, 2027)}
OUTPUT_ROOT = PROJECT_ROOT / "documents" / "la-tour-de-peilz"
DATA_ROOT = PROJECT_ROOT / "data" / "preavis-municipaux" / "la-tour-de-peilz"
HEADERS = {"User-Agent": "AI-Riviera preavis municipaux importer"}


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
    if "preavis-municipaux" not in decoded.lower():
        return None
    return full_url


def year_from_url(pdf_url: str) -> str | None:
    match = re.search(r"/(20\d{2})/", unquote(urlparse(pdf_url).path))
    if not match:
        return None
    year = match.group(1)
    return year if year in YEARS else None


def safe_filename(pdf_url: str) -> str:
    name = Path(unquote(urlparse(pdf_url).path)).name
    return re.sub(r'[<>:"/\\|?*]', "_", name)


def clean_html_text(raw_html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", raw_html)
    text = html.unescape(text)
    return clean_french_text(re.sub(r"\s+", " ", text)).strip()


def preavis_number(title: str, filename: str) -> int | None:
    match = re.search(r"(?:Préavis|Preavis)[^\d]*(\d{1,2})", f"{title} {filename}", flags=re.I)
    return int(match.group(1)) if match else None


def collect_items() -> list[dict]:
    page_html = fetch_text(SOURCE_PAGE)
    items_by_url = {}

    for year_match in re.finditer(
        r'<div class="title c-tmplt">[\s\S]*?</i>\s*(20\d{2})</div>\s*<div class="answer"[\s\S]*?>',
        page_html,
        flags=re.I,
    ):
        listing_year = year_match.group(1)
        if listing_year not in YEARS:
            continue

        block_start = year_match.end()
        next_year = re.search(
            r'<div class="title c-tmplt">[\s\S]*?</i>\s*20\d{2}</div>\s*<div class="answer"',
            page_html[block_start:],
            flags=re.I,
        )
        block_end = block_start + next_year.start() if next_year else len(page_html)
        year_block = page_html[block_start:block_end]

        for li in re.findall(r'<li class="prestation[^"]*">([\s\S]*?)</li>', year_block, flags=re.I):
            href_match = re.search(r'href=["\']([^"\']+)["\']', li, flags=re.I)
            if not href_match:
                continue

            pdf_url = normalize_pdf_url(SOURCE_PAGE, href_match.group(1))
            pdf_year = year_from_url(pdf_url) if pdf_url else None
            if not pdf_url or not pdf_year:
                continue

            title_match = re.search(r"<span[^>]*font-weight:\s*bold;[^>]*>([\s\S]*?)</span>", li, flags=re.I)
            summary_match = re.search(r'<div[^>]*class="txt-14"[^>]*>([\s\S]*?)</div>', li, flags=re.I)

            title = clean_html_text(title_match.group(1)) if title_match else Path(unquote(urlparse(pdf_url).path)).stem
            summary = clean_html_text(summary_match.group(1)) if summary_match else ""
            filename = safe_filename(pdf_url)

            items_by_url[pdf_url] = {
                "commune": "La Tour-de-Peilz",
                "type": "preavis_municipal",
                "document_type": "preavis_municipal",
                "year": pdf_year,
                "listing_year": listing_year,
                "category": "preavis-municipaux",
                "legislature": "2021-2026",
                "title": title,
                "summary": summary,
                "preavis_number": preavis_number(title, filename),
                "filename": filename,
                "pdf_url": pdf_url,
                "source_page": SOURCE_PAGE,
            }

    return list(sorted(items_by_url.values(), key=lambda item: (item["listing_year"], item.get("preavis_number") or 999, item["filename"])))


def extract_pdf_text(pdf_path: Path) -> str:
    document = fitz.open(pdf_path)
    return clean_french_text("\n".join(page.get_text() for page in document))


def download_and_extract(item: dict) -> dict:
    year = item["year"]
    filename = item["filename"]
    target_dir = OUTPUT_ROOT / year / "preavis-municipaux"
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
    print(f"Found {len(items)} preavis municipaux for years 2021-2026.")

    results = []
    failures = []
    for index, item in enumerate(items, start=1):
        print(f"[{index}/{len(items)}] listing {item['listing_year']} file {item['year']} {item['filename']}")
        try:
            results.append(download_and_extract(item))
        except Exception as exc:
            failures.append({"pdf_url": item["pdf_url"], "filename": item["filename"], "error": str(exc)})
            print(f"  ERROR: {exc}")

    manifest = {
        "commune": "La Tour-de-Peilz",
        "legislature": "2021-2026",
        "source_page": SOURCE_PAGE,
        "scope_note": "Préavis municipaux des rubriques 2021 à 2026 de la page officielle preavis-municipaux.php.",
        "years": sorted(YEARS),
        "documents_downloaded": len(results),
        "failures": failures,
        "documents": results,
    }
    manifest_path = DATA_ROOT / "manifest_preavis_municipaux_2021_2026.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"Downloaded/extracted: {len(results)}")
    print(f"Failures: {len(failures)}")
    print(f"Manifest: {manifest_path}")


if __name__ == "__main__":
    main()
