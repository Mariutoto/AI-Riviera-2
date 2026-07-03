from __future__ import annotations

import hashlib
import html
import json
import re
import sys
from pathlib import Path

import fitz
import requests


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
SCRAPER_DIR = PROJECT_ROOT / "scrape-la-tour-de-peilz"
sys.path.insert(0, str(SCRAPER_DIR))

import scrape_interpellations_2021_2026 as scraper


DOCUMENT_METADATA_DIR = ROOT / "document_metadata"
SCRAPER_METADATA_DIR = ROOT / "scraper_metadata"
ARTIFACTS_DIR = ROOT / "artifacts"

# Roles are explicitly reviewed for this pilot; they are not copied from the old metadata.
SELECTION = [
    ("Interpellation-Ansermet-Arbres-Rep.pdf", "combined_interpellation_response"),
    ("Interpellation-Arnaud-Camping-cars-Bourg-Quai.pdf", "interpellation_text"),
    ("Interpellation-Holzeisen-Site_internet-Rep.pdf", "combined_interpellation_response"),
    ("Interpellation-Pasche-et-consorts-ASR-maison-securite-Rep.pdf", "combined_interpellation_response"),
    ("Interpellation-Ansermet-30-kmh-Rep.pdf", "combined_interpellation_response"),
    ("Interpellation-Heller-Hebergement-urgence-Rep.pdf", "combined_interpellation_response"),
    ("Interpellation-Heller-Refectoires-Rep.pdf", "combined_interpellation_response"),
    ("Interpellation-Negro-Arret-bus-Lhand-Rep.pdf", "combined_interpellation_response"),
    ("Interpellation-Urech-Zone-30-Rep.pdf", "combined_interpellation_response"),
    ("Interpellation-Ansermet-Communaute-electrique-locale-Rep.pdf", "combined_interpellation_response"),
    ("Interpellation-Urech-travaux-preavis-17-2024-Rep.pdf", "combined_interpellation_response"),
    ("Interpellation-Tobler-Affichage-politique-Rep.pdf", "combined_interpellation_response"),
]

DATE_OVERRIDES = {
    "Interpellation-Urech-travaux-preavis-17-2024-Rep.pdf": "2026-06-24",
}


def normalized_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def download(item: dict, target: Path) -> None:
    if target.exists() and target.stat().st_size:
        return
    response = requests.get(item["pdf_url"], timeout=120)
    response.raise_for_status()
    if not response.content.startswith(b"%PDF"):
        raise ValueError(f"Not a PDF: {item['pdf_url']}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(response.content)


def extract_native(pdf_path: Path) -> tuple[str, int]:
    with fitz.open(pdf_path) as pdf:
        pages = [scraper.motion_tools.clean_pdf_text(page.get_text("text")) for page in pdf]
        return "\n".join(pages).strip(), len(pdf)


def document_date(item: dict, text: str) -> str | None:
    enriched = scraper.enrich_interpellation_metadata(item, text)
    value = enriched.get("document_date")
    return value if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) else None


def canonical_record(item: dict, role: str, text: str) -> dict:
    title = item.get("summary") or item.get("object_title") or item["title"]
    source_title = item.get("site_listing_title")
    if not source_title or str(source_title).lower().endswith(".pdf"):
        source_title = None
    return {
        "document_id": "doc_" + sha256(item["pdf_url"])[:20],
        "commune": "La Tour-de-Peilz",
        "document_family": "political_object",
        "category": "interpellation",
        "document_role": role,
        "title": title,
        "source_title": source_title,
        "source_page_url": item["source_page"],
        "file_url": item["pdf_url"],
        "listing_year": int(item["listing_year"]),
        "legislature": item.get("legislature") or "2021-2026",
        "document_date": DATE_OVERRIDES.get(item["filename"], document_date(item, text)),
        "content_hash": sha256(normalized_text(text)),
        "extraction_method": "native_pdf",
        "processing_status": "validated",
    }


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def source_items() -> dict[str, dict]:
    return {item["filename"]: item for item in scraper.collect_items()}


def build_review(records: list[dict], diagnostics: list[dict]) -> None:
    diagnostic_by_id = {item["document_id"]: item for item in diagnostics}
    rows = []
    for record in records:
        diagnostic = diagnostic_by_id[record["document_id"]]
        rows.append(
            f"<tr><td>{record['listing_year']}</td><td>{html.escape(record['document_role'])}</td>"
            f"<td>{html.escape(record['title'])}</td><td>{diagnostic['pages']}</td><td>{diagnostic['words']}</td>"
            f"<td>{html.escape(record['document_date'] or '—')}</td>"
            f"<td><a href='document_metadata/{record['document_id']}.json'>Document metadata</a></td>"
            f"<td><a href='scraper_metadata/{record['document_id']}.json'>Scraper metadata</a></td></tr>"
        )
    page = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><title>Interpellation canonical pilot</title>
<style>body{{font:15px/1.5 system-ui;margin:28px;color:#172033}}.note{{background:#eef5ff;border-left:4px solid #2477d4;padding:14px}}table{{border-collapse:collapse;width:100%;margin-top:18px}}th,td{{border:1px solid #d9dfeb;padding:9px;text-align:left}}th{{background:#edf1f7}}</style></head><body>
<h1>Interpellation document-metadata pilot — 12 PDFs</h1><p class='note'>One base document-metadata JSON per physical PDF. Existing AI Riviera metadata remains untouched. Intermediate PDFs and extracted texts live under <code>artifacts/</code> and are not document records.</p>
<table><thead><tr><th>Year</th><th>Document role</th><th>Document title</th><th>Pages</th><th>Native words</th><th>Document date</th><th>Base document metadata</th><th>Existing interpellation metadata</th></tr></thead><tbody>{''.join(rows)}</tbody></table></body></html>"""
    (ROOT / "review.html").write_text(page, encoding="utf-8")


def main() -> None:
    items = source_items()
    records = []
    diagnostics = []
    for index, (filename, role) in enumerate(SELECTION, 1):
        print(f"[{index}/{len(SELECTION)}] {filename}")
        item = items.get(filename)
        if not item:
            raise RuntimeError(f"Live listing did not contain {filename}")
        slug = Path(filename).stem
        artifact_dir = ARTIFACTS_DIR / slug
        pdf_path = artifact_dir / "document.pdf"
        download(item, pdf_path)
        text, pages = extract_native(pdf_path)
        (artifact_dir / "native.txt").write_text(text + "\n", encoding="utf-8")
        record = canonical_record(item, role, text)
        scraper_metadata = scraper.enrich_interpellation_metadata(item, text)
        write_json(DOCUMENT_METADATA_DIR / f"{record['document_id']}.json", record)
        write_json(SCRAPER_METADATA_DIR / f"{record['document_id']}.json", scraper_metadata)
        records.append(record)
        diagnostics.append({
            "document_id": record["document_id"], "filename": filename, "pages": pages,
            "words": len(re.findall(r"\b\w+[\w'-]*\b", text, flags=re.UNICODE)),
            "artifact_directory": str(artifact_dir.relative_to(ROOT)),
        })
    write_json(ROOT / "manifest.json", {"schema_version": "document-metadata-interpellation-v1", "documents": records})
    write_json(ROOT / "validation_report.json", {"documents_checked": len(records), "errors": [], "diagnostics": diagnostics})
    build_review(records, diagnostics)
    print(ROOT / "review.html")


if __name__ == "__main__":
    main()
