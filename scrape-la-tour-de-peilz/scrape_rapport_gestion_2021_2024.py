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
YEARS = {"2021", "2022", "2023", "2024"}
OUTPUT_ROOT = PROJECT_ROOT / "documents" / "la-tour-de-peilz"
DATA_ROOT = PROJECT_ROOT / "data" / "financial-reports" / "la-tour-de-peilz"
HEADERS = {"User-Agent": "AI-Riviera rapport de gestion importer"}


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
    if "rapport-de-gestion" not in decoded.lower():
        return None
    return full_url


def clean_html_text(raw_html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", raw_html)
    text = html.unescape(text)
    return clean_french_text(re.sub(r"\s+", " ", text)).strip()


def year_from_text(text: str) -> str | None:
    match = re.search(r"\b(2021|2022|2023|2024)\b", text)
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
        year = year_from_text(title)
        pdf_url = normalize_pdf_url(SOURCE_PAGE, href_match.group(1))
        if not year or year not in YEARS or not pdf_url:
            continue

        lower_title = title.lower()
        if "rapport de la commission de gestion" not in lower_title or "réponse de la municipalité" not in lower_title:
            continue

        filename = safe_filename(pdf_url)
        items_by_url[pdf_url] = {
            "commune": "La Tour-de-Peilz",
            "type": "rapport_de_gestion",
            "document_type": "rapport_de_gestion_commission_reponse_municipalite",
            "year": year,
            "category": "rapport-de-gestion",
            "legislature": "2021-2026",
            "title": title,
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
    target_dir = OUTPUT_ROOT / year / "rapport-de-gestion"
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
    print(f"Found {len(items)} rapport de gestion documents for 2021-2024.")

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
        "scope_note": "Rapports de gestion 2021-2024 incluant rapport de la commission de gestion et réponse de la Municipalité.",
        "years": sorted(YEARS),
        "documents_downloaded": len(results),
        "failures": failures,
        "documents": results,
    }
    manifest_path = DATA_ROOT / "manifest_rapports_gestion_2021_2024.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"Downloaded/extracted: {len(results)}")
    print(f"Failures: {len(failures)}")
    print(f"Manifest: {manifest_path}")


if __name__ == "__main__":
    main()
