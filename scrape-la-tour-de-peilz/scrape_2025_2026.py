import html
import json
import re
import sys
from pathlib import Path
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import fitz
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.text_cleaning import clean_french_text


BASE_URL = "https://www.la-tour-de-peilz.ch/"
YEARS = {"2025", "2026"}

PAGES = {
    "motions-postulats": "https://www.la-tour-de-peilz.ch/politique/motions-postulats.php",
    "proces-verbaux": "https://www.la-tour-de-peilz.ch/politique/proces-verbaux.php",
    "preavis-municipaux": "https://www.la-tour-de-peilz.ch/politique/preavis-municipaux.php",
    "communications-municipales": "https://www.la-tour-de-peilz.ch/politique/communications-municipales.php",
    "informations-diverses": "https://www.la-tour-de-peilz.ch/politique/informations-diverses.php",
    "rapport-comptes-budget": "https://www.la-tour-de-peilz.ch/politique/rapport-comptes-budget.php",
    "ordre-du-jour": "https://www.la-tour-de-peilz.ch/politique/ordre-du-jour.php",
}

OUTPUT_ROOT = Path(__file__).resolve().parents[1] / "documents" / "la-tour-de-peilz"
HEADERS = {"User-Agent": "AI-Riviera document importer"}


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

    if ".pdf" not in full_url.lower():
        return None

    return full_url


def extract_pdf_links(page_url: str, page_html: str) -> set[str]:
    links = set()

    for href in re.findall(r"""href=["']([^"']+)["']""", page_html, flags=re.I):
        pdf_url = normalize_pdf_url(page_url, href)
        if pdf_url:
            links.add(pdf_url)

    for match in re.findall(r"""(?:https?:)?//[^"' <>()]+?\.pdf|/[^"' <>()]+?\.pdf""", page_html, flags=re.I):
        pdf_url = normalize_pdf_url(page_url, match)
        if pdf_url:
            links.add(pdf_url)

    return links


def extract_order_detail_pages(page_html: str) -> set[str]:
    detail_pages = set()
    for match in re.finditer(
        r"""href=["'](apercu_ordre-du-jour\.php\?id=\d+)["'][\s\S]{0,500}?(2025|2026)""",
        page_html,
        flags=re.I,
    ):
        detail_pages.add(urljoin(PAGES["ordre-du-jour"], html.unescape(match.group(1))))
    return detail_pages


def year_from_url(pdf_url: str) -> str | None:
    decoded = unquote(pdf_url)
    path = urlparse(decoded).path
    path_year = re.search(r"/(20\d{2})/", path)
    if path_year:
        year = path_year.group(1)
        return year if year in YEARS else None

    for year in YEARS:
        if year in Path(path).name:
            return year

    date_match = re.search(r"(?:^|[-_/])(\d{2})[-_.](\d{2})[-_.](2025|2026)(?:[-_.]|$)", decoded)
    if date_match:
        return date_match.group(3)

    return None


def category_from_url(pdf_url: str) -> str:
    path = unquote(urlparse(pdf_url).path).lower()
    for category in PAGES:
        if category in path:
            return category
    if "proces-verbaux" in path:
        return "proces-verbaux"
    if "preavis" in path:
        return "preavis-municipaux"
    if "communications" in path:
        return "communications-municipales"
    if "/budget/" in path:
        return "budget"
    return "autres"


def safe_filename(pdf_url: str) -> str:
    name = Path(unquote(urlparse(pdf_url).path)).name
    return re.sub(r'[<>:"/\\|?*]', "_", name)


def extract_text(pdf_path: Path) -> str:
    document = fitz.open(pdf_path)
    return clean_french_text("\n".join(page.get_text() for page in document))


def collect_pdf_urls() -> dict[str, str]:
    pdf_sources = {}

    for category, page_url in PAGES.items():
        print(f"Scanning {category}...")
        page_html = fetch_text(page_url)

        for pdf_url in extract_pdf_links(page_url, page_html):
            if year_from_url(pdf_url):
                pdf_sources[pdf_url] = page_url

        if category == "ordre-du-jour":
            for detail_url in extract_order_detail_pages(page_html):
                detail_html = fetch_text(detail_url)
                for pdf_url in extract_pdf_links(detail_url, detail_html):
                    if year_from_url(pdf_url):
                        pdf_sources[pdf_url] = detail_url

    return dict(sorted(pdf_sources.items()))


def download_and_extract(pdf_url: str, source_page: str) -> dict:
    year = year_from_url(pdf_url)
    if not year:
        raise ValueError(f"No year found for {pdf_url}")

    category = category_from_url(pdf_url)
    filename = safe_filename(pdf_url)
    target_dir = OUTPUT_ROOT / year / category
    target_dir.mkdir(parents=True, exist_ok=True)

    pdf_path = target_dir / filename
    txt_path = pdf_path.with_suffix(".txt")
    json_path = pdf_path.with_suffix(".json")

    response = requests.get(pdf_url, headers=HEADERS, timeout=60)
    response.raise_for_status()
    pdf_path.write_bytes(response.content)

    text = extract_text(pdf_path)
    txt_path.write_text(text, encoding="utf-8")

    metadata = {
        "commune": "La Tour-de-Peilz",
        "year": year,
        "category": category,
        "filename": filename,
        "pdf_url": pdf_url,
        "source_page": source_page,
        "pdf_path": str(pdf_path),
        "text_path": str(txt_path),
        "characters_extracted": len(text),
    }
    json_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return metadata


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    pdf_sources = collect_pdf_urls()

    print(f"\nFound {len(pdf_sources)} PDF files for 2025/2026.\n")
    results = []
    failures = []

    for index, (pdf_url, source_page) in enumerate(pdf_sources.items(), start=1):
        print(f"[{index}/{len(pdf_sources)}] {pdf_url}")
        try:
            results.append(download_and_extract(pdf_url, source_page))
        except Exception as exc:
            failures.append({"pdf_url": pdf_url, "source_page": source_page, "error": str(exc)})
            print(f"  ERROR: {exc}")

    manifest = {
        "commune": "La Tour-de-Peilz",
        "years": sorted(YEARS),
        "documents_downloaded": len(results),
        "failures": failures,
        "documents": results,
    }
    manifest_path = OUTPUT_ROOT / "manifest_2025_2026.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\nDownloaded: {len(results)}")
    print(f"Failures: {len(failures)}")
    print(f"Manifest: {manifest_path}")


if __name__ == "__main__":
    main()
