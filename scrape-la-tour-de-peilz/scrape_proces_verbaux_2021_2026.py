import html
import json
import re
import sys
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import fitz
import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.text_cleaning import clean_french_text


BASE_URL = "https://www.la-tour-de-peilz.ch/"
SOURCE_PAGE = "https://www.la-tour-de-peilz.ch/politique/proces-verbaux.php"
LEGISLATURE_MARKER = "legislature_2021-2026"
YEARS = {str(year) for year in range(2021, 2027)}
OUTPUT_ROOT = PROJECT_ROOT / "documents" / "la-tour-de-peilz"
HEADERS = {"User-Agent": "AI-Riviera proces-verbaux importer"}


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
    if LEGISLATURE_MARKER not in unquote(full_url):
        return None
    return full_url


def parse_pv_filename(filename: str) -> dict | None:
    normalized = filename.replace("_", "-").replace(".", "-")
    match = re.search(r"PV\s*0*(\d+).*?(\d{2})-(\d{2})-(\d{2,4})", normalized, flags=re.I)
    if not match:
        return None

    pv_number = int(match.group(1))
    day = int(match.group(2))
    month = int(match.group(3))
    year = int(match.group(4))
    if year < 100:
        year += 2000
    if str(year) not in YEARS:
        return None

    return {
        "pv_number": pv_number,
        "session_date": date(year, month, day).isoformat(),
        "year": str(year),
    }


def safe_filename(pdf_url: str) -> str:
    name = Path(unquote(urlparse(pdf_url).path)).name
    return re.sub(r'[<>:"/\\|?*]', "_", name)


def extract_pdf_text(pdf_path: Path) -> str:
    document = fitz.open(pdf_path)
    return clean_french_text("\n".join(page.get_text() for page in document))


def collect_pv_urls() -> dict[str, dict]:
    page_html = fetch_text(SOURCE_PAGE)
    pvs = {}

    for href in re.findall(r"""href=["']([^"']+)["']""", page_html, flags=re.I):
        pdf_url = normalize_pdf_url(SOURCE_PAGE, href)
        if not pdf_url:
            continue

        filename = safe_filename(pdf_url)
        parsed = parse_pv_filename(filename)
        if not parsed:
            continue

        pvs[pdf_url] = parsed

    return dict(sorted(pvs.items(), key=lambda item: item[1]["session_date"]))


def download_and_extract(pdf_url: str, parsed: dict) -> dict:
    year = parsed["year"]
    filename = safe_filename(pdf_url)
    target_dir = OUTPUT_ROOT / year / "proces-verbaux"
    target_dir.mkdir(parents=True, exist_ok=True)

    pdf_path = target_dir / filename
    txt_path = pdf_path.with_suffix(".txt")
    json_path = pdf_path.with_suffix(".json")

    if not pdf_path.exists():
        response = requests.get(pdf_url, headers=HEADERS, timeout=60)
        response.raise_for_status()
        pdf_path.write_bytes(response.content)

    text = extract_pdf_text(pdf_path)
    txt_path.write_text(text + "\n", encoding="utf-8")

    metadata = {
        "commune": "La Tour-de-Peilz",
        "type": "proces_verbal",
        "year": year,
        "category": "proces-verbaux",
        "filename": filename,
        "pv_number": parsed["pv_number"],
        "session_date": parsed["session_date"],
        "legislature": "2021-2026",
        "pdf_url": pdf_url,
        "source_page": SOURCE_PAGE,
        "pdf_path": str(pdf_path),
        "text_path": str(txt_path),
        "characters_extracted": len(text),
    }
    json_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return metadata


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    pv_urls = collect_pv_urls()
    print(f"Found {len(pv_urls)} proces-verbaux for legislature 2021-2026.")

    results = []
    failures = []
    for index, (pdf_url, parsed) in enumerate(pv_urls.items(), start=1):
        print(f"[{index}/{len(pv_urls)}] PV{parsed['pv_number']:02d} {parsed['session_date']} {pdf_url}")
        try:
            results.append(download_and_extract(pdf_url, parsed))
        except Exception as exc:
            failures.append({"pdf_url": pdf_url, "error": str(exc)})
            print(f"  ERROR: {exc}")

    manifest = {
        "commune": "La Tour-de-Peilz",
        "legislature": "2021-2026",
        "source_page": SOURCE_PAGE,
        "years": sorted(YEARS),
        "documents_downloaded": len(results),
        "failures": failures,
        "documents": results,
    }
    manifest_path = OUTPUT_ROOT / "manifest_proces_verbaux_2021_2026.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"Downloaded/extracted: {len(results)}")
    print(f"Failures: {len(failures)}")
    print(f"Manifest: {manifest_path}")


if __name__ == "__main__":
    main()
