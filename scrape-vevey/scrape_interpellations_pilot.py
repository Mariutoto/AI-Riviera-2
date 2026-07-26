from __future__ import annotations

import argparse
import html
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from municipal_pipeline.documents import municipal_document
from municipal_pipeline.municipalities import VEVEY
from municipal_pipeline.pdf_audit import audit_pdf_documents


SOURCE_PAGE = (
    "https://www.vevey.ch/vie-politique/conseil-communal/"
    "documents-du-conseil-communal"
)
YEARS = {"2025", "2026"}
PAGE_SIZE = 30
HEADERS = {"User-Agent": "AI-Riviera Vevey interpellations pilot"}
DIV_TOKEN_RE = re.compile(r"<div\b[^>]*>|</div\s*>", re.I)


def clean_html(value: str) -> str:
    without_tags = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", html.unescape(without_tags)).strip()


def extract_teaser_blocks(page_html: str) -> list[str]:
    blocks = []
    position = 0
    while True:
        match = re.search(
            r'<div\b[^>]*class="[^"]*\bteaser-politique\b[^"]*"[^>]*>',
            page_html[position:],
            flags=re.I,
        )
        if not match:
            return blocks
        start = position + match.start()
        depth = 0
        end = None
        for token in DIV_TOKEN_RE.finditer(page_html, start):
            depth += -1 if token.group(0).lower().startswith("</div") else 1
            if depth == 0:
                end = token.end()
                break
        if end is None:
            raise ValueError("Bloc de document Vevey incomplet")
        blocks.append(page_html[start:end])
        position = end


def _download_id(url: str) -> str:
    values = parse_qs(urlparse(url).query).get("d", [])
    return values[0] if values else ""


def parse_teaser(block: str) -> dict:
    left_column = re.search(
        r'<div[^>]*class="[^"]*\bcol-3\b[^"]*"[^>]*>(.*?)</div>',
        block,
        flags=re.I | re.S,
    )
    left_text = clean_html(left_column.group(1) if left_column else "")
    date_match = re.search(r"\b(\d{2}\.\d{2}\.\d{4})\b", left_text)
    if not date_match:
        raise ValueError("Date absente d'un résultat Vevey")
    listing_date = datetime.strptime(date_match.group(1), "%d.%m.%Y").date().isoformat()
    author = left_text.replace(date_match.group(1), "", 1).strip()

    teaser_id = re.search(
        r'<p[^>]*\bteaser-id\b[^>]*>(.*?)</p>',
        block,
        flags=re.I | re.S,
    )
    id_parts = [
        clean_html(part)
        for part in re.findall(r"<span[^>]*>(.*?)</span>", teaser_id.group(1) if teaser_id else "", re.I | re.S)
    ]
    document_type = id_parts[0] if id_parts else ""
    reference = id_parts[1] if len(id_parts) > 1 else ""

    heading = re.search(r"<h4[^>]*>(.*?)</h4>", block, flags=re.I | re.S)
    if not heading:
        raise ValueError("Titre absent d'un résultat Vevey")
    title = clean_html(heading.group(1))
    heading_link = re.search(r'href="([^"]+)"', heading.group(1), flags=re.I)

    links = []
    for match in re.finditer(
        r'<a\b[^>]*href="(?P<href>[^"]+)"[^>]*>(?P<label>.*?)</a>',
        block,
        flags=re.I | re.S,
    ):
        links.append(
            {
                "url": urljoin(SOURCE_PAGE, html.unescape(match.group("href"))),
                "label": clean_html(match.group("label")),
            }
        )
    download = next(
        (link for link in links if "telecharger pdf" in _ascii(link["label"])),
        None,
    )
    pdf_url = (download or {}).get("url") or (
        urljoin(SOURCE_PAGE, html.unescape(heading_link.group(1))) if heading_link else ""
    )
    if not pdf_url:
        raise ValueError("Lien PDF absent d'un résultat Vevey")

    return municipal_document(
        municipality=VEVEY,
        category="interpellation",
        title=title,
        listing_year=listing_date[:4],
        pdf_url=pdf_url,
        source_page=SOURCE_PAGE,
        legislature="2021-2026",
        listing_date=listing_date,
        author=author,
        reference=reference,
        source_collection="vevey-council-documents",
        source_download_id=_download_id(pdf_url),
    )


def _ascii(value: str) -> str:
    import unicodedata

    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(char for char in decomposed if not unicodedata.combining(char)).lower()


def parse_page(page_html: str, years: set[str] | None = YEARS) -> list[dict]:
    return [
        item
        for item in (parse_teaser(block) for block in extract_teaser_blocks(page_html))
        if item["document_type"].casefold() == "interpellation"
        and (years is None or item["listing_year"] in years)
    ]


def result_count(page_html: str) -> int:
    match = re.search(
        r'<h2[^>]*class="[^"]*\bh3\b[^"]*"[^>]*>\s*(\d+)\s+r[ée]sultats?',
        page_html,
        flags=re.I,
    )
    if not match:
        raise ValueError("Nombre de résultats Vevey introuvable")
    return int(match.group(1))


def fetch_page(page: int, session: requests.Session | None = None) -> str:
    client = session or requests.Session()
    response = client.get(
        SOURCE_PAGE,
        params={
            "type-desktop": "Interpellation",
            "submit-desktop": "Appliquer",
            "page": page,
        },
        headers=HEADERS,
        timeout=30,
    )
    response.raise_for_status()
    return response.text


def collect_items(session: requests.Session | None = None) -> tuple[list[dict], dict]:
    first_html = fetch_page(0, session)
    expected = result_count(first_html)
    pages = max(1, (expected + PAGE_SIZE - 1) // PAGE_SIZE)
    all_items = parse_page(first_html, years=None)
    for page in range(1, pages):
        all_items.extend(parse_page(fetch_page(page, session), years=None))

    all_unique_occurrences = {}
    for item in all_items:
        occurrence_key = (
            item["source_download_id"] or item["pdf_url"],
            item["listing_date"],
            item["title"],
        )
        all_unique_occurrences[occurrence_key] = item
    unique_occurrences = {
        key: item
        for key, item in all_unique_occurrences.items()
        if item["listing_year"] in YEARS
    }
    collected = sorted(
        unique_occurrences.values(),
        key=lambda item: (item["listing_date"], item["source_download_id"]),
        reverse=True,
    )
    diagnostics = {
        "endpoint_results": expected,
        "pages_fetched": pages,
        "parsed_endpoint_occurrences": len(all_items),
        "unique_endpoint_occurrences": len(all_unique_occurrences),
        "scoped_2025_2026_occurrences": len(collected),
        "complete": len(all_unique_occurrences) == expected,
    }
    if not diagnostics["complete"]:
        raise ValueError(
            "Collecte Vevey incomplète: "
            f"{len(all_unique_occurrences)} occurrences sur {expected}"
        )
    return collected, diagnostics


def download_audit(
    items: list[dict],
    *,
    session: requests.Session | None = None,
    download_dir: Path | None = None,
) -> tuple[list[dict], dict]:
    return audit_pdf_documents(
        items,
        document_id_prefix=f"{VEVEY.key.replace('-', '_')}_interpellation",
        session=session,
        headers=HEADERS,
        download_dir=download_dir,
        normalize_title=_ascii,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Pilote des interpellations de Vevey pour 2025-2026"
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--download-dir", type=Path)
    parser.add_argument("--audit-downloads", action="store_true")
    args = parser.parse_args()

    items, listing_diagnostics = collect_items()
    report = {
        "schema_version": "vevey-interpellations-pilot-v1",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": SOURCE_PAGE,
        "scope": {
            "city": "Vevey",
            "document_type": "interpellation",
            "years": sorted(YEARS),
            "legislature": "2021-2026",
        },
        "listing_diagnostics": listing_diagnostics,
        "listing_occurrences": items,
    }
    if args.audit_downloads:
        documents, download_diagnostics = download_audit(
            items, download_dir=args.download_dir
        )
        report["download_diagnostics"] = download_diagnostics
        report["canonical_documents"] = documents
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    summary = {key: value for key, value in report.items() if key not in {"listing_occurrences", "canonical_documents"}}
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
