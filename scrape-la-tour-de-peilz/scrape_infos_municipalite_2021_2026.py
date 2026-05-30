import html
import json
import re
import sys
import unicodedata
from pathlib import Path
from urllib.parse import urljoin

import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.text_cleaning import clean_french_text


BASE_URL = "https://www.la-tour-de-peilz.ch/"
SOURCE_PAGE = "https://www.la-tour-de-peilz.ch/infos-muni/"
YEARS = {str(year) for year in range(2021, 2027)}
OUTPUT_ROOT = PROJECT_ROOT / "documents" / "la-tour-de-peilz"
DATA_ROOT = PROJECT_ROOT / "data" / "infos-municipalite" / "la-tour-de-peilz"
HEADERS = {"User-Agent": "AI-Riviera infos municipalite importer"}


def fetch_text(url: str) -> str:
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    return response.text


def clean_html_text(raw_html: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", raw_html, flags=re.I)
    text = re.sub(r"</p\s*>", "\n", text, flags=re.I)
    text = re.sub(r"</div\s*>", "\n", text, flags=re.I)
    text = re.sub(r"<sup[^>]*>([\s\S]*?)</sup>", r"\1", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return clean_french_text(text).strip()


def safe_slug(value: str, fallback: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", ascii_value).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return slug[:120] or fallback


def parse_date(date_text: str) -> tuple[str, str]:
    match = re.search(r"(\d{1,2})\.(\d{1,2})\.(20\d{2})", date_text)
    if not match:
        raise ValueError(f"Cannot parse date: {date_text!r}")
    day, month, year = match.groups()
    return f"{year}-{int(month):02d}-{int(day):02d}", year


def collect_items() -> list[dict]:
    page_html = fetch_text(SOURCE_PAGE)
    items_by_id = {}

    for match in re.finditer(
        r'<a\s+href=["\'](apercu_infos_municipalite\.php\?id=(\d+))["\'][^>]*>\s*'
        r'<div class="row"[\s\S]*?'
        r'<div class="col-md-1 hidden">\s*([^<]+)</div>[\s\S]*?'
        r'<span style="font-weight:\s*bold;">([\s\S]*?)</span>',
        page_html,
        flags=re.I,
    ):
        href, item_id, date_text, title_html = match.groups()
        publication_date, year = parse_date(date_text)
        if year not in YEARS:
            continue

        title = clean_html_text(title_html)
        detail_url = urljoin(SOURCE_PAGE, href)
        filename = f"{publication_date}-{safe_slug(title, f'info-municipalite-{item_id}')}.txt"

        items_by_id[item_id] = {
            "commune": "La Tour-de-Peilz",
            "type": "info_municipalite",
            "document_type": "info_municipalite",
            "year": year,
            "category": "infos-municipalite",
            "legislature": "2021-2026",
            "title": title,
            "publication_date": publication_date,
            "info_id": int(item_id),
            "filename": filename,
            "url": detail_url,
            "source_page": SOURCE_PAGE,
        }

    return list(sorted(items_by_id.values(), key=lambda item: (item["publication_date"], item["info_id"])))


def extract_detail(item: dict) -> tuple[str, str]:
    detail_html = fetch_text(item["url"])
    start = detail_html.find('<!-- InstanceBeginEditable name="PageContent" -->')
    end = detail_html.find("<!-- InstanceEndEditable -->", start)
    fragment = detail_html[start:end] if start != -1 and end != -1 else detail_html

    title_match = re.search(r"<h3>([\s\S]*?)</h3>", fragment, flags=re.I)
    subtitle_match = re.search(r'<span[^>]*font-size:\s*18px;[^>]*>([\s\S]*?)</span>', fragment, flags=re.I)
    body_match = re.search(
        r'<div class="col-md-10\s+col-xs-12"[^>]*>([\s\S]*?)</div>\s*'
        r'<div class="col-md-1\s+col-xs-12"',
        fragment,
        flags=re.I,
    )

    title = clean_html_text(title_match.group(1)) if title_match else item["title"]
    subtitle = clean_html_text(subtitle_match.group(1)) if subtitle_match else ""
    body = clean_html_text(body_match.group(1) if body_match else fragment)

    parts = [title]
    if subtitle:
        parts.append(subtitle)
    parts.append(f"Date de publication: {item['publication_date']}")
    parts.append(body)
    return clean_french_text("\n\n".join(part for part in parts if part).strip()), title


def save_item(item: dict) -> dict:
    year = item["year"]
    target_dir = OUTPUT_ROOT / year / "infos-municipalite"
    target_dir.mkdir(parents=True, exist_ok=True)

    txt_path = target_dir / item["filename"]
    json_path = txt_path.with_suffix(".json")

    text, detail_title = extract_detail(item)
    txt_path.write_text(text + "\n", encoding="utf-8")

    metadata = {
        **item,
        "detail_title": detail_title,
        "text_path": str(txt_path),
        "characters_extracted": len(text),
    }
    json_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return metadata


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    DATA_ROOT.mkdir(parents=True, exist_ok=True)

    items = collect_items()
    print(f"Found {len(items)} infos de la Municipalite for years 2021-2026.")

    results = []
    failures = []
    for index, item in enumerate(items, start=1):
        print(f"[{index}/{len(items)}] {item['publication_date']} {item['title']}")
        try:
            results.append(save_item(item))
        except Exception as exc:
            failures.append({"url": item["url"], "title": item["title"], "error": str(exc)})
            print(f"  ERROR: {exc}")

    manifest = {
        "commune": "La Tour-de-Peilz",
        "legislature": "2021-2026",
        "source_page": SOURCE_PAGE,
        "scope_note": "Pages HTML de la rubrique officielle Infos de la Municipalite, publiees de 2021 a 2026. Les textes correspondent aux decisions mensuelles de la Municipalite.",
        "years": sorted(YEARS),
        "documents_downloaded": len(results),
        "failures": failures,
        "documents": results,
    }
    manifest_path = DATA_ROOT / "manifest_infos_municipalite_2021_2026.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"Saved/extracted: {len(results)}")
    print(f"Failures: {len(failures)}")
    print(f"Manifest: {manifest_path}")


if __name__ == "__main__":
    main()
