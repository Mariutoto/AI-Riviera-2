from __future__ import annotations

import hashlib
import html
import importlib.util
import json
import re
import sys
from collections import Counter
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parent
OCR_DIR = ROOT / "ocr_outputs"
PROJECT_ROOT = ROOT.parents[2]
ACCOUNTS_BUILDER = PROJECT_ROOT / "audit-general" / "audit-rapports-comptes" / "full-audit" / "build_audit.py"
SPEC = importlib.util.spec_from_file_location("accounts_audit_helpers", ACCOUNTS_BUILDER)
helpers = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(helpers)

SCRAPER_DIR = PROJECT_ROOT / "scrape-la-tour-de-peilz"
sys.path.insert(0, str(SCRAPER_DIR))
import scrape_budgets_search_json_2021_2026 as endpoint

MAX_WORDS = 450
OVERLAP_WORDS = 60
CSS = helpers.CSS


def locate_pdf(item: dict) -> Path:
    matches = list((PROJECT_ROOT / "documents" / "la-tour-de-peilz").rglob(item["filename"]))
    return matches[0] if matches else ROOT / "pdfs" / item["filename"]


def normalized_heading(line: str) -> str:
    return helpers.normalize(re.sub(r"^#{1,6}\s*", "", line)).replace("\u00a0", " ")


def component_parts(text: str) -> list[dict]:
    lines = text.splitlines(keepends=True)
    offsets, cursor = [], 0
    for line in lines:
        offsets.append(cursor)
        cursor += len(line)
    markers = []
    preavis_seen = False
    for index, line in enumerate(lines):
        heading = normalized_heading(line)
        if re.fullmatch(r"preavis municipal n[°o]?\s*\d+/\d{4}", heading):
            markers.append((offsets[index], "municipal_budget_preavis"))
            preavis_seen = True
        elif preavis_seen and heading == "recapitulation par services" and line.strip().upper() == line.strip():
            markers.append((offsets[index], "operating_budget"))
        elif preavis_seen and heading.startswith("plan des investissements et entretien") and line.strip().upper() == line.strip():
            markers.append((offsets[index], "investment_plan"))
    markers.sort()
    deduped = []
    for marker in markers:
        if not deduped or marker[1] != deduped[-1][1]:
            deduped.append(marker)
    parts = []
    first = deduped[0][0] if deduped else len(text)
    front = text[:first].strip()
    for pos, (start, component) in enumerate(deduped):
        end = deduped[pos + 1][0] if pos + 1 < len(deduped) else len(text)
        content = text[start:end].strip()
        if content:
            parts.append({"component": component, "content": content})
    if front and parts:
        parts[0]["content"] = front + "\n\n" + parts[0]["content"]
    return parts or [{"component": "municipal_budget_preavis", "content": text}]


def chunks_for(metadata: dict, text: str) -> list[dict]:
    base, chunks = metadata["document_metadata"], []
    for part in component_parts(text):
        words = list(re.finditer(r"\S+", part["content"]))
        start = 0
        while start < len(words):
            end = min(start + MAX_WORDS, len(words))
            content = part["content"][words[start].start():words[end - 1].end()].strip()
            idx = len(chunks)
            count = len(re.findall(r"\S+", content))
            issues = ["chunk_too_short"] if count < 10 else []
            chunks.append({
                "chunk_id": f"{base['document_id']}#chunk-{idx:03d}", "document_id": base["document_id"],
                "chunk_index": idx, "component": part["component"], "content": content,
                "word_count": count, "chunk_hash": hashlib.sha256(content.encode()).hexdigest(),
                "embedding_input": f"category: budget\ntitle: {base['title']}\ncomponent: {part['component']}\n\n{content}",
                "quality_issues": issues, "quality": "yellow" if issues else "green",
            })
            if end == len(words):
                break
            start = end - OVERLAP_WORDS
    return chunks


def amount(value: str) -> float:
    return float(value.replace("'", "").replace("’", "").replace(".--", "").replace(".-", ""))


def extract_budget_values(text: str) -> dict:
    summary = text[: min(len(text), 80000)]
    charges = re.search(r"(?im)^\|\s*Charges\s*\|\s*\**(-?[0-9][0-9'’.]*)", summary)
    revenues = re.search(r"(?im)^\|\s*Revenus\s*\|\s*\**(-?[0-9][0-9'’.]*)", summary)
    result = re.search(r"(?im)^\|\s*R[ée]sultats?\s*\|\s*\**(-?[0-9][0-9'’.]*)", summary)
    investments = re.search(r"(?im)^\|\s*Investissements\s+planifi[ée]s(?:\s+en\s+N)?\s*\|\s*\**([0-9][0-9'’.]*)", text)
    if not investments:
        investments = re.search(r"investissements\s+(?:sont\s+)?(?:planifi[ée]s|pr[ée]vus)[\s\S]{0,800}?soit\s+un\s+total\s+de\s+Fr\.\s*([0-9][0-9'’.]*)\s*(mio|million)?", summary, re.I)
    investment_value = amount(investments.group(1)) if investments else None
    if investments and investments.lastindex and investments.lastindex >= 2 and investments.group(2):
        investment_value *= 1_000_000
    return {
        "total_charges": amount(charges.group(1)) if charges else None,
        "total_revenues": amount(revenues.group(1)) if revenues else None,
        "projected_surplus_or_deficit": amount(result.group(1)) if result else None,
        "planned_investment_total": investment_value,
    }


def extract_preavis_number(text: str) -> str | None:
    match = re.search(r"PR[ÉE]AVIS\s+MUNICIPAL\s+N[°ºO]\s*(\d+/\d{4})", text, re.I)
    return match.group(1) if match else None


def audit_item(item: dict) -> dict:
    pdf_path = locate_pdf(item)
    with fitz.open(pdf_path) as pdf:
        page_texts = [page.get_text("text") for page in pdf]
        page_stats = [{"page": i, "characters": len(text.strip()), "images": len(page.get_images(full=True)), "low_text": len(text.strip()) < 80}
                      for i, (page, text) in enumerate(zip(pdf, page_texts), 1)]
    native_margins = helpers.repeated_margins(page_texts)
    native_clean, native_removed, native_blocks = helpers.clean_pages(page_texts, native_margins)
    ocr_path = OCR_DIR / f"{pdf_path.stem}.md"
    if ocr_path.exists():
        ocr_pages = ocr_path.read_text(encoding="utf-8").split("\f")
        if ocr_pages and not ocr_pages[-1].strip():
            ocr_pages.pop()
        clean_text, removed_count, removed_blocks, margins = helpers.clean_ocr_pages(ocr_pages)
        selected_method = "mistral_ocr"
        selected_pages = ocr_pages
    else:
        clean_text, removed_count, removed_blocks, margins = native_clean, native_removed, native_blocks, native_margins
        selected_method = "native_pdf"
        selected_pages = page_texts
    parts = component_parts(clean_text)
    components = list(dict.fromkeys(part["component"] for part in parts))
    values = extract_budget_values(clean_text)
    document_id = "doc_" + hashlib.sha256(item["file_url"].encode()).hexdigest()[:20]
    document_date = helpers.extract_document_date(clean_text)
    metadata = {
        "document_metadata": {
            "document_id": document_id, "commune": item["commune"], "document_family": item["document_family"],
            "category": item["category"], "document_role": "combined_" + "_".join(components), "title": item["title"],
            "source_title": item["source_title"], "source_page_url": item["source_page_url"], "file_url": item["file_url"],
            "filename": item["filename"], "listing_year": item["listing_year"], "legislature": item["legislature"],
            "document_date": document_date, "content_hash": hashlib.sha256(clean_text.encode()).hexdigest(),
            "extraction_method": selected_method, "processing_status": "audited",
        },
        "budget_metadata": {
            "fiscal_year": item["fiscal_year"], "period_start": item["period_start"], "period_end": item["period_end"],
            "preavis_number": extract_preavis_number(clean_text), "components": components, **values,
        },
        "processing": {
            "text_extraction_status": {"characters_extracted": len(clean_text), "text_available": bool(clean_text),
                                       "needs_ocr": False, "page_text_coverage": round(sum(bool(p.strip()) for p in selected_pages) / len(selected_pages), 3),
                                       "low_text_image_pages": [p["page"] for p in page_stats if p["low_text"] and p["images"]]},
            "header_footer_cleaning": {"raw_words": len(" ".join(selected_pages).split()), "clean_words": len(clean_text.split()),
                                       "removed_blocks": removed_count, "repeated_margin_candidates": margins},
            "selected_text": {"method": selected_method, "words": len(clean_text.split()), "native_pdf_words": len(native_clean.split())},
        },
    }
    warnings = [f"missing:{key}" for key, value in values.items() if value is None]
    chunks = chunks_for(metadata, clean_text)
    for folder in ("metadata", "clean_text", "removed_blocks", "chunks", "details", "chunk_details"):
        (ROOT / folder).mkdir(exist_ok=True)
    (ROOT / "metadata" / f"{document_id}.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (ROOT / "clean_text" / f"{document_id}.txt").write_text(clean_text + "\n", encoding="utf-8")
    (ROOT / "removed_blocks" / f"{document_id}.json").write_text(json.dumps({"blocks": removed_blocks, "repeated_margin_candidates": margins}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (ROOT / "chunks" / f"{document_id}.json").write_text(json.dumps(chunks, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"document_id": document_id, "title": item["title"], "fiscal_year": item["fiscal_year"], "pages": len(page_stats),
            "components": components, "warnings": warnings, "metadata": metadata, "chunks": chunks, "page_stats": page_stats,
            "file_url": item["file_url"], "preview": clean_text[:5000]}


def render(records: list[dict]) -> str:
    rows = []
    for r in records:
        m, b, p = r["metadata"]["document_metadata"], r["metadata"]["budget_metadata"], r["metadata"]["processing"]
        rows.append(f"<tr class='{'review' if r['warnings'] else 'complete'}'><td>{r['fiscal_year']}</td><td><a href='{html.escape(r['file_url'])}'>{html.escape(r['title'])}</a></td><td>{html.escape(', '.join(r['components']))}</td><td>{html.escape(', '.join(r['warnings']) or 'Complet')}</td><td>{m.get('document_date') or '—'}</td><td>{b.get('preavis_number') or '—'}</td><td>Charges : {b.get('total_charges') or '—'}<br>Revenus : {b.get('total_revenues') or '—'}<br>Résultat : {b.get('projected_surplus_or_deficit') or '—'}<br>Investissements : {b.get('planned_investment_total') or '—'}</td><td>{p['header_footer_cleaning']['raw_words']} → {p['header_footer_cleaning']['clean_words']}<br>{p['header_footer_cleaning']['removed_blocks']} blocs</td><td>{len(r['chunks'])}</td><td><a href='metadata/{r['document_id']}.json'>JSON</a> · <a href='clean_text/{r['document_id']}.txt'>Texte</a> · <a href='removed_blocks/{r['document_id']}.json'>Blocs</a> · <a href='chunk_details/{r['document_id']}.html'>Chunks</a></td></tr>")
    return f"<!doctype html><html lang='fr'><head><meta charset='utf-8'><title>Audit budgets</title><style>{CSS}</style></head><body><h1>Audit des budgets — {len(records)} documents</h1><p>Audit natif préalable à l'OCR et aux embeddings. Les titres de tableaux doivent rester conservés.</p><table><tr><th>Exercice</th><th>Document</th><th>Composants</th><th>Contrôles</th><th>Date</th><th>Préavis</th><th>Montants</th><th>Nettoyage</th><th>Chunks</th><th>Fichiers</th></tr>{''.join(rows)}</table></body></html>"


def main() -> None:
    payload = json.loads((ROOT.parent / "scraper-test" / "search-json-test.json").read_text(encoding="utf-8"))
    records = [audit_item(item) for item in payload["documents"]]
    for r in records:
        cards = "".join(f"<article class='{c['quality']}'><h2>{html.escape(c['chunk_id'])}</h2><p>{html.escape(c['component'])} · {c['word_count']} mots</p><pre>{html.escape(c['content'])}</pre><details><summary>Embedding test</summary><pre>{html.escape(c['embedding_input'])}</pre></details></article>" for c in r["chunks"])
        (ROOT / "chunk_details" / f"{r['document_id']}.html").write_text(f"<!doctype html><html lang='fr'><head><meta charset='utf-8'><style>{CSS}</style></head><body><a href='../audit.html'>← Audit</a><h1>{html.escape(r['title'])}</h1>{cards}</body></html>", encoding="utf-8")
    serializable = [{**r, "chunks": len(r["chunks"])} for r in records]
    (ROOT / "audit.json").write_text(json.dumps({"documents": serializable}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (ROOT / "audit.html").write_text(render(records), encoding="utf-8")
    print(json.dumps({"documents": len(records), "chunks": sum(len(r["chunks"]) for r in records),
                      "warnings": dict(Counter(w for r in records for w in r["warnings"]))}, ensure_ascii=False, indent=2))
    print(ROOT / "audit.html")


if __name__ == "__main__":
    main()
