from __future__ import annotations

import base64
import html
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import fitz
import requests
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
SCRAPER_DIR = PROJECT_ROOT / "scrape-la-tour-de-peilz"
sys.path.insert(0, str(SCRAPER_DIR))

import scrape_interpellations_2021_2026 as interpellations
import scrape_motions_2021_2026 as motions
import scrape_postulats_2021_2026 as postulats


OUTPUT = ROOT / "focused_five_test"
MODEL = "mistral-ocr-latest"
OCR_URL = "https://api.mistral.ai/v1/ocr"
DOCS = [
    ("postulat", "Postulat-Huber-Chervet-Quai-roussy.pdf"),
    ("motion", "Motion-Roethlisberger-Frais-garde.pdf"),
    ("interpellation", "Interpellation-Urech-travaux-preavis-17-2024-Rep.pdf"),
    ("interpellation", "Interpellation-Tobler-Affichage-politique-Rep.pdf"),
    ("interpellation", "Interpellation-Ansermet-Communaute-electrique-locale-Rep.pdf"),
    ("postulat", "Postulat-Ansermet-1aout-Rapp-Dec.pdf"),
]
MODULES = {"motion": motions, "postulat": postulats, "interpellation": interpellations}
ENRICHERS = {
    "motion": motions.enrich_motion_metadata,
    "postulat": postulats.enrich_postulat_metadata,
    "interpellation": interpellations.enrich_interpellation_metadata,
}
FIELDS = (
    "title", "summary", "category", "status_normalized", "authors", "object_title",
    "document_role", "contains_response", "document_components", "document_date",
    "report_type", "contains_report", "contains_decision", "decision", "commission",
    "content_kind", "political_object", "search_facets",
)


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def display(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, indent=2)
    return str(value)


def word_count(text: str) -> int:
    return len(re.findall(r"\b\w+[\w'-]*\b", text, flags=re.UNICODE))


def download(item: dict, directory: Path) -> Path:
    path = directory / "document.pdf"
    if path.exists() and path.stat().st_size:
        return path
    response = requests.get(item["pdf_url"], timeout=120)
    response.raise_for_status()
    if not response.content.startswith(b"%PDF"):
        raise ValueError("Downloaded response is not a PDF")
    path.write_bytes(response.content)
    return path


def native_extract(path: Path, module) -> tuple[str, list[dict]]:
    pages = []
    with fitz.open(path) as pdf:
        for page in pdf:
            raw = page.get_text("text")
            cleaned = motions.clean_pdf_text(raw)
            pages.append({"page": page.number + 1, "characters": len(cleaned), "words": word_count(cleaned), "text": cleaned})
    return "\n".join(page["text"] for page in pages).strip(), pages


def ocr_extract(path: Path, api_key: str, cache: Path) -> tuple[str, dict, list[dict]]:
    if cache.exists():
        data = json.loads(cache.read_text(encoding="utf-8"))
    else:
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        response = requests.post(
            OCR_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": MODEL,
                "document": {"type": "document_url", "document_url": f"data:application/pdf;base64,{encoded}"},
                "table_format": "markdown",
                "extract_header": True,
                "extract_footer": True,
                "confidence_scores_granularity": "page",
            },
            timeout=600,
        )
        response.raise_for_status()
        data = response.json()
        dump(cache, data)
    pages = []
    for page in data.get("pages", []):
        text = page.get("markdown", "")
        confidence = (page.get("confidence_scores") or {}).get("average_page_confidence_score")
        pages.append({
            "page": int(page.get("index", 0)) + 1,
            "characters": len(text), "words": word_count(text), "confidence": confidence,
            "header": page.get("header"), "footer": page.get("footer"), "text": text,
        })
    return "\n\n".join(page["text"] for page in pages).strip(), data, pages


def recommended_record(item: dict, native_text: str, ocr_text: str, native_meta: dict, ocr_meta: dict) -> dict:
    native_words = word_count(native_text)
    ocr_words = word_count(ocr_text)
    method = "mistral_ocr" if native_words < 100 or ocr_words > native_words * 1.20 else "native_pdf"
    chosen_meta = ocr_meta if method == "mistral_ocr" else native_meta
    return {
        "document": {
            "document_id": item["political_object_id"] + ":" + Path(item["filename"]).stem,
            "commune": item["commune"], "category": item["category"], "title": item["title"],
            "summary": item["summary"], "listing_year": item["listing_year"],
            "source_page_url": item["source_page"], "file_url": item["pdf_url"], "filename": item["filename"],
        },
        "processing": {
            "selected_extraction": method, "native_words": native_words, "ocr_words": ocr_words,
            "ocr_model": MODEL if method == "mistral_ocr" else None,
            "reason": "OCR recovered substantially more text" if method == "mistral_ocr" else "Native extraction appears complete",
        },
        "specific_metadata": {key: chosen_meta.get(key) for key in FIELDS if chosen_meta.get(key) not in (None, "", [], {})},
        "storage": {
            "raw_pdf": "document.pdf", "native_text": "native.txt", "ocr_text": "ocr.md",
            "scraped_listing": "01_scraped_listing.json", "native_metadata": "02_metadata_from_native.json",
            "ocr_metadata": "03_metadata_from_ocr.json",
        },
    }


def detail_page(record: dict, directory: Path) -> None:
    item, native_meta, ocr_meta = record["item"], record["native_meta"], record["ocr_meta"]
    rows = []
    for field in FIELDS:
        rows.append(
            f"<tr><th>{html.escape(field)}</th><td><pre>{html.escape(display(item.get(field)))}</pre></td>"
            f"<td><pre>{html.escape(display(native_meta.get(field)))}</pre></td>"
            f"<td><pre>{html.escape(display(ocr_meta.get(field)))}</pre></td></tr>"
        )
    native_pages = "".join(f"<tr><td>{p['page']}</td><td>{p['words']}</td><td>{p['characters']}</td><td>—</td></tr>" for p in record["native_pages"])
    ocr_pages = "".join(f"<tr><td>{p['page']}</td><td>{p['words']}</td><td>{p['characters']}</td><td>{p['confidence'] if p['confidence'] is not None else '—'}</td></tr>" for p in record["ocr_pages"])
    final_json = json.dumps(record["final"], ensure_ascii=False, indent=2)
    page = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><title>{html.escape(item['filename'])}</title>
<style>body{{font:14px/1.45 system-ui;margin:24px;color:#172033}}.hero,.box{{border:1px solid #d9dfeb;border-radius:12px;padding:16px;margin:14px 0}}.hero{{background:#eef5ff}}table{{border-collapse:collapse;width:100%;table-layout:fixed}}th,td{{border:1px solid #d9dfeb;padding:8px;vertical-align:top}}th{{background:#edf1f7}}pre{{white-space:pre-wrap;word-break:break-word;font:12px/1.4 ui-monospace,monospace;max-height:420px;overflow:auto}}.flow{{display:flex;gap:8px;align-items:center;flex-wrap:wrap}}.step{{padding:9px 12px;border-radius:8px;background:#f2f4f8}}details{{margin:12px 0}}</style></head><body>
<p><a href='dashboard.html'>← Back to focused dashboard</a></p><div class='hero'><h1>{html.escape(item['title'])}</h1><p>{html.escape(item['summary'])}</p><div class='flow'><span class='step'>Municipal listing</span>→<span class='step'>PDF</span>→<span class='step'>Native + OCR</span>→<span class='step'>Metadata processor</span>→<span class='step'>Final record</span></div></div>
<div class='box'><h2>Metadata comparison</h2><table><thead><tr><th>Field</th><th>Scraped listing</th><th>From native text</th><th>From OCR text</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>
<div class='box'><h2>Text by page</h2><h3>Native</h3><table><tr><th>Page</th><th>Words</th><th>Characters</th><th>Confidence</th></tr>{native_pages}</table><h3>OCR</h3><table><tr><th>Page</th><th>Words</th><th>Characters</th><th>Confidence</th></tr>{ocr_pages}</table></div>
<div class='box'><h2>How the final record is stored</h2><pre>{html.escape(final_json)}</pre></div>
<details><summary>Full scraped listing JSON</summary><pre>{html.escape(json.dumps(item, ensure_ascii=False, indent=2))}</pre></details><details><summary>Full metadata from native text</summary><pre>{html.escape(json.dumps(native_meta, ensure_ascii=False, indent=2))}</pre></details><details><summary>Full metadata from OCR text</summary><pre>{html.escape(json.dumps(ocr_meta, ensure_ascii=False, indent=2))}</pre></details></body></html>"""
    (directory / "detail.html").write_text(page, encoding="utf-8")


def dashboard(records: list[dict]) -> None:
    rows = []
    for record in records:
        item, final = record["item"], record["final"]
        processing = final["processing"]
        rows.append(
            f"<tr><td>{html.escape(item['type'])}</td><td><a href='{html.escape(record['slug'])}/detail.html'>{html.escape(item['title'])}</a></td>"
            f"<td>{html.escape(item['status_normalized'])}</td><td>{'yes' if record['native_meta'].get('contains_response') else 'no'}</td>"
            f"<td>{'yes' if record['ocr_meta'].get('contains_response') else 'no'}</td><td>{processing['native_words']}</td><td>{processing['ocr_words']}</td><td>{html.escape(processing['selected_extraction'])}</td></tr>"
        )
    page = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><title>Focused document test</title><style>body{{font:15px/1.5 system-ui;margin:28px;color:#172033}}.note{{background:#eef5ff;border-left:4px solid #2477d4;padding:14px}}table{{border-collapse:collapse;width:100%;margin-top:18px}}th,td{{border:1px solid #d9dfeb;padding:9px;text-align:left}}th{{background:#edf1f7}}</style></head><body><h1>Focused scraper + OCR pipeline</h1><p class='note'>The scraper supplies listing context such as title, subject, status and author. Native extraction and OCR supply document content. The same metadata processor is then run on both versions. Click a title to inspect every layer and the final stored JSON.</p><table><thead><tr><th>Type</th><th>Document</th><th>Listing status</th><th>Response from native</th><th>Response from OCR</th><th>Native words</th><th>OCR words</th><th>Selected</th></tr></thead><tbody>{''.join(rows)}</tbody></table></body></html>"""
    (OUTPUT / "dashboard.html").write_text(page, encoding="utf-8")


def main() -> None:
    load_dotenv(ROOT / ".env")
    api_key = os.getenv("MISTRAL_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("MISTRAL_API_KEY is missing")
    all_items = {kind: {item["filename"]: item for item in MODULES[kind].collect_items()} for kind in MODULES}
    records = []
    for index, (kind, filename) in enumerate(DOCS, 1):
        print(f"[{index}/{len(DOCS)}] {filename}")
        item = all_items[kind].get(filename)
        if not item:
            raise RuntimeError(f"The live scraper did not find {filename}")
        slug = Path(filename).stem
        directory = OUTPUT / slug
        directory.mkdir(parents=True, exist_ok=True)
        pdf = download(item, directory)
        native_text, native_pages = native_extract(pdf, MODULES[kind])
        ocr_text, _, ocr_pages = ocr_extract(pdf, api_key, directory / "ocr_raw.json")
        native_meta = ENRICHERS[kind](item, native_text)
        ocr_meta = ENRICHERS[kind](item, ocr_text)
        final = recommended_record(item, native_text, ocr_text, native_meta, ocr_meta)
        (directory / "native.txt").write_text(native_text + "\n", encoding="utf-8")
        (directory / "ocr.md").write_text(ocr_text + "\n", encoding="utf-8")
        dump(directory / "01_scraped_listing.json", item)
        dump(directory / "02_metadata_from_native.json", native_meta)
        dump(directory / "03_metadata_from_ocr.json", ocr_meta)
        dump(directory / "04_final_recommended.json", final)
        record = {"slug": slug, "item": item, "native_meta": native_meta, "ocr_meta": ocr_meta, "native_pages": native_pages, "ocr_pages": ocr_pages, "final": final}
        detail_page(record, directory)
        records.append(record)
    dashboard(records)
    print(OUTPUT / "dashboard.html")


if __name__ == "__main__":
    main()
