from __future__ import annotations

import hashlib
import html
import json
import math
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

import fitz
import requests


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scrape-la-tour-de-peilz"))

import scrape_interpellations_2021_2026 as scraper


PDF_DIR = ROOT / "pdfs"
TEXT_DIR = ROOT / "clean_text"
REMOVED_DIR = ROOT / "removed_blocks"
RECORD_DIR = ROOT / "metadata"
YEARS = {str(year) for year in range(2021, 2027)}

BASE_FIELDS = (
    "document_id", "commune", "document_family", "category", "document_role", "title",
    "source_title", "source_page_url", "file_url", "listing_year", "legislature",
    "document_date", "content_hash", "extraction_method", "processing_status",
)
ADDITIONAL_FIELDS = ("authors", "political_status")

def sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize(value: str) -> str:
    value = "".join(c for c in unicodedata.normalize("NFD", value.lower()) if unicodedata.category(c) != "Mn")
    value = re.sub(r"\d+", "#", value)
    return re.sub(r"\s+", " ", value).strip(" -|_")


def words(value: str) -> int:
    return len(re.findall(r"\b\w+[\w'-]*\b", value, flags=re.UNICODE))


MONTHS = {
    "janvier": 1, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6,
    "juillet": 7, "aout": 8, "septembre": 9, "octobre": 10, "novembre": 11, "decembre": 12,
}


def strip_accents(value: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", value) if unicodedata.category(c) != "Mn")


def iso_date(day: str, month: str, year: str) -> str | None:
    try:
        month_number = int(month) if month.isdigit() else MONTHS[strip_accents(month.lower())]
        return f"{int(year):04d}-{month_number:02d}-{int(day):02d}"
    except (ValueError, KeyError):
        return None


def first_french_date(value: str) -> str | None:
    pattern = r"(\d{1,2})(?:er)?\s+(janvier|f[ée]vrier|mars|avril|mai|juin|juillet|ao[ûu]t|septembre|octobre|novembre|d[ée]cembre)\s+(20\d{2})"
    match = re.search(pattern, value, flags=re.I)
    return iso_date(*match.groups()) if match else None


def first_month_year(value: str) -> str | None:
    pattern = r"\b(janvier|f[ée]vrier|mars|avril|mai|juin|juillet|ao[ûu]t|septembre|octobre|novembre|d[ée]cembre)\s+(20\d{2})\b"
    match = re.search(pattern, value, flags=re.I)
    if not match:
        return None
    month = MONTHS.get(strip_accents(match.group(1).lower()))
    return f"{int(match.group(2)):04d}-{month:02d}" if month else None


def extract_document_dates(raw_text: str) -> dict:
    normalized = re.sub(r"[ \t]+", " ", raw_text)
    response_headers = list(re.finditer(
        r"R[ÉE]PONSE\s+MUNICIPALE(?:\s+ORALE)?(?:\s+N[°ºO]\s*(\d+(?:bis)?/\d{4}))?",
        normalized,
        flags=re.I,
    ))
    response_start = response_headers[0].start() if response_headers else len(normalized)
    initial_part = normalized[:response_start]
    responses = []
    for index, header in enumerate(response_headers):
        end = response_headers[index + 1].start() if index + 1 < len(response_headers) else len(normalized)
        section = normalized[header.start():end]
        number = header.group(1)
        if not number:
            internal_reference = re.search(r"Reponse[-_](\d+)[_/](\d{4})", section[:900], flags=re.I)
            if internal_reference:
                number = f"{int(internal_reference.group(1))}/{internal_reference.group(2)}"
        adoption = re.search(r"Adopt[ée]\s+par\s+la\s+Municipalit[ée]\s*:?.{0,100}", section, flags=re.I | re.S)
        responses.append({
            "response_number": number,
            "response_date": first_french_date(section[:700]),
            "response_type": (
                "supplemental_response"
                if re.search(r"compl[ée]ment\s+de\s+r[ée]ponse", section[:1200], flags=re.I)
                else "oral_response"
                if re.search(r"r[ée]ponse\s+municipale\s+orale", section[:1200], flags=re.I)
                else "municipal_response"
            ),
            "municipal_adoption_date": first_french_date(adoption.group(0)) if adoption else None,
        })
    deduplicated_responses = {}
    for index, response in enumerate(responses):
        if not response.get("response_number") and deduplicated_responses:
            current = next(reversed(deduplicated_responses.values()))
            if not current.get("municipal_adoption_date") and response.get("municipal_adoption_date"):
                current["municipal_adoption_date"] = response["municipal_adoption_date"]
            continue
        number = response["response_number"].lower() if response.get("response_number") else f"unnumbered-{index}"
        if number not in deduplicated_responses:
            deduplicated_responses[number] = response
            continue
        current = deduplicated_responses[number]
        for field in ("response_date", "municipal_adoption_date"):
            if not current.get(field) and response.get(field):
                current[field] = response[field]
        if response.get("response_type") == "supplemental_response":
            current["response_type"] = "supplemental_response"
    responses = list(deduplicated_responses.values())
    if any(response.get("response_number") for response in responses):
        responses = [
            response for response in responses
            if response.get("response_number")
            or response.get("response_date")
            or response.get("municipal_adoption_date")
        ]

    interpellation_date = None
    deposited = re.search(r"d[ée]pos[ée]e?.{0,160}?s[ée]ance.{0,80}?du\s+(\d{1,2}\s+\w+\s+20\d{2})", normalized, flags=re.I | re.S)
    if deposited:
        interpellation_date = first_french_date(deposited.group(1))
    if not interpellation_date:
        numeric_candidates = []
        for line in initial_part.splitlines():
            compact = line.strip()
            if len(compact) > 80:
                continue
            match = re.search(r"\b(\d{1,2})[./](\d{1,2})[./](20\d{2})\b", compact)
            if match:
                parsed = iso_date(*match.groups())
                if parsed:
                    numeric_candidates.append(parsed)
        if numeric_candidates:
            interpellation_date = numeric_candidates[0]
    if not interpellation_date:
        interpellation_date = scraper.motion_tools.parse_signature_date(initial_part)
    if not interpellation_date:
        interpellation_date = first_month_year(initial_part[:1500])

    return {
        "interpellation_date": interpellation_date,
        "responses": responses,
    }


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def download(item: dict) -> Path:
    path = PDF_DIR / item["listing_year"] / item["filename"]
    if path.exists() and path.stat().st_size:
        return path
    response = requests.get(item["pdf_url"], timeout=120)
    response.raise_for_status()
    if not response.content.startswith(b"%PDF"):
        raise ValueError("Downloaded content is not a PDF")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(response.content)
    return path


def extract_blocks(pdf_path: Path) -> tuple[list[list[dict]], list[dict]]:
    pages, page_stats = [], []
    with fitz.open(pdf_path) as pdf:
        for page_index, page in enumerate(pdf):
            blocks = []
            height = page.rect.height
            raw_text = page.get_text("text")
            page_stats.append({
                "page": page_index + 1,
                "native_words": words(raw_text),
                "images": len(page.get_images(full=True)),
            })
            for block in page.get_text("blocks"):
                x0, y0, x1, y1, text, *_ = block
                text = re.sub(r"[ \t]+", " ", text).strip()
                if text:
                    blocks.append({
                        "page": page_index + 1, "x0": round(x0, 1), "y0": round(y0, 1),
                        "x1": round(x1, 1), "y1": round(y1, 1), "page_height": round(height, 1),
                        "text": text, "normalized": normalize(text),
                    })
            pages.append(blocks)
    return pages, page_stats


def clean_pdf(pdf_path: Path, title: str) -> dict:
    pages, page_stats = extract_blocks(pdf_path)
    candidates = []
    for blocks in pages:
        for block in blocks:
            h = block["page_height"]
            if block["y1"] <= h * 0.14 or block["y0"] >= h * 0.86:
                candidates.append(block["normalized"])
    counts = Counter(value for value in candidates if value)
    repeated = {value for value, count in counts.items() if count >= max(2, math.ceil(len(pages) * 0.5))}
    title_normalized = normalize(title)
    removed, kept_pages = [], []
    for page_number, blocks in enumerate(pages, 1):
        kept = []
        for block in blocks:
            text, norm, h = block["text"], block["normalized"], block["page_height"]
            reason = None
            if norm in repeated:
                reason = "repeated_header_or_footer"
            elif re.search(r"\b\d{2,3}\s+\d{3}\s+\d{2}\s+\d{2}\b|@|www\.", text, flags=re.I) and (block["y1"] <= h * 0.2 or block["y0"] >= h * 0.8):
                reason = "contact_boilerplate"
            elif re.search(r"\.docx\b", text, flags=re.I):
                reason = "internal_filename"
            elif re.fullmatch(r"\s*(?:page\s*)?\d+\s*(?:/|sur|\|)\s*\d+\s*", text, flags=re.I):
                reason = "page_number"
            elif page_number == 1 and block["y1"] <= h * 0.38 and re.search(r"R[ÉE]PONSE\s+MUNICIPALE\s+N[°ºO]", text, flags=re.I):
                reason = "semantic_response_header_saved_as_metadata"
            elif page_number == 1 and block["y1"] <= h * 0.38 and title_normalized and (title_normalized in norm or norm in title_normalized) and len(norm) > 25:
                reason = "document_title_already_in_metadata"
            if reason:
                removed.append({key: value for key, value in block.items() if key != "normalized"} | {"reason": reason})
            else:
                kept.append(text)
        kept_pages.append("\n".join(kept).strip())
    raw_text = "\n\n".join("\n".join(block["text"] for block in blocks) for blocks in pages).strip()
    clean_text = "\n\n".join(value for value in kept_pages if value).strip()
    suspicious_pages = [p for p in page_stats if p["native_words"] < 50 and p["images"] > 0]
    needs_ocr = words(clean_text) < 100 or len(suspicious_pages) >= max(1, math.ceil(len(page_stats) * 0.2))
    return {
        "raw_text": raw_text, "clean_text": clean_text, "removed_blocks": removed,
        "page_stats": page_stats, "needs_ocr": needs_ocr,
    }


def detect_role(item: dict, text: str) -> tuple[str, str]:
    if item.get("status_normalized") != "response_available" and not re.search(r"(?:^|[-_])rep(?:[-_.]|$)", item["filename"], flags=re.I):
        return "interpellation_text", "listing_confirmed"
    role = scraper.infer_document_role(item["site_listing_title"], item["filename"], text)
    if role not in {"interpellation_text", "municipal_response", "combined_interpellation_response"}:
        return "unknown", "needs_review"
    return role, "auto_detected"


def missing(record: dict, fields: tuple[str, ...]) -> list[str]:
    return [field for field in fields if record.get(field) in (None, "", [], {}) or record.get(field) == "unknown"]


def missing_additional(metadata: dict, role: str) -> list[str]:
    required = list(ADDITIONAL_FIELDS)
    if role in {"interpellation_text", "combined_interpellation_response"}:
        required.append("interpellation_date")
    if role in {"municipal_response", "combined_interpellation_response"}:
        required.append("responses")
    return missing(metadata, tuple(required))


def load_manual_overrides() -> dict:
    path = ROOT / "manual_overrides.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def main() -> None:
    items = [item for item in scraper.collect_items() if item["listing_year"] in YEARS]
    manual_overrides = load_manual_overrides()
    results, failures = [], []
    for index, item in enumerate(items, 1):
        print(f"[{index}/{len(items)}] {item['listing_year']} {item['filename']}")
        try:
            pdf_path = download(item)
            cleaning = clean_pdf(pdf_path, item["summary"])
            ocr_override = ROOT / "ocr_overrides" / f"{Path(item['filename']).stem}.md"
            if ocr_override.exists():
                clean_text = ocr_override.read_text(encoding="utf-8").strip()
                extraction_method = "mistral_ocr"
            else:
                clean_text = cleaning["clean_text"]
                extraction_method = "native_pdf"
            enriched = scraper.enrich_interpellation_metadata(item, clean_text)
            role, role_status = detect_role(item, clean_text)
            dates = extract_document_dates(cleaning["raw_text"])
            overrides = manual_overrides.get(item["filename"], {})
            if "interpellation_date" in overrides:
                dates["interpellation_date"] = overrides["interpellation_date"]
            if "responses" in overrides:
                dates["responses"] = overrides["responses"]
            source_title = item.get("site_listing_title") or None
            record_id = "doc_" + sha256(item["pdf_url"])[:20]
            base = {
                "document_id": record_id,
                "commune": "La Tour-de-Peilz",
                "document_family": "political_object",
                "category": "interpellation",
                "document_role": role,
                "title": item.get("summary") or None,
                "source_title": source_title,
                "source_page_url": scraper.SOURCE_PAGE,
                "file_url": item["pdf_url"],
                "listing_year": int(item["listing_year"]),
                "legislature": item.get("legislature") or "2021-2026",
                "document_date": (
                    next((response.get("response_date") for response in reversed(dates["responses"]) if response.get("response_date")), None)
                    or next((response.get("municipal_adoption_date") for response in reversed(dates["responses"]) if response.get("municipal_adoption_date")), None)
                    or dates["interpellation_date"]
                ),
                "content_hash": sha256(re.sub(r"\s+", " ", clean_text).strip()),
                "extraction_method": extraction_method,
                "processing_status": "needs_review" if role_status == "needs_review" else "validated",
            }
            additional = {
                "authors": enriched.get("authors") or [],
                "political_status": item.get("status_normalized") or "unknown",
                **dates,
            }
            processing = {
                "text_extraction_status": {
                    "characters_extracted": len(clean_text),
                    "text_available": bool(clean_text),
                    "needs_ocr": False if extraction_method == "mistral_ocr" else cleaning["needs_ocr"],
                },
                "header_footer_cleaning": {
                    "raw_words": words(cleaning["raw_text"]),
                    "clean_words": words(cleaning["clean_text"]),
                    "removed_blocks": len(cleaning["removed_blocks"]),
                },
                "selected_text": {
                    "method": extraction_method,
                    "words": words(clean_text),
                },
            }
            combined = {"document_metadata": base, "interpellation_metadata": additional, "processing": processing}
            base_missing = missing(base, BASE_FIELDS)
            additional_missing = missing_additional(additional, role)
            TEXT_DIR.mkdir(parents=True, exist_ok=True)
            (TEXT_DIR / f"{record_id}.txt").write_text(clean_text + "\n", encoding="utf-8")
            write_json(REMOVED_DIR / f"{record_id}.json", cleaning["removed_blocks"])
            write_json(RECORD_DIR / f"{record_id}.json", combined)
            results.append({
                "document_id": record_id, "title": base["title"], "year": base["listing_year"],
                "file_url": base["file_url"], "role": base["document_role"],
                "base_missing": base_missing, "additional_missing": additional_missing,
                "processing": processing,
                "dates": dates,
            })
        except Exception as exc:
            failures.append({"filename": item["filename"], "error": str(exc)})
            print(f"  ERROR: {exc}")

    write_json(ROOT / "audit.json", {"documents": results, "failures": failures})
    build_html(results, failures)
    print(json.dumps({
        "documents": len(results), "failures": len(failures),
        "missing_base": sum(bool(item["base_missing"]) for item in results),
        "missing_additional": sum(bool(item["additional_missing"]) for item in results),
        "needs_ocr": sum(item["processing"]["text_extraction_status"]["needs_ocr"] for item in results),
    }, ensure_ascii=False))


def build_html(results: list[dict], failures: list[dict]) -> None:
    chunk_summary_path = ROOT / "chunks_summary.json"
    chunk_summaries = {
        item["document_id"]: item
        for item in json.loads(chunk_summary_path.read_text(encoding="utf-8"))
    } if chunk_summary_path.exists() else {}
    rows = []
    for item in results:
        if item["base_missing"]:
            css, state = "base-missing", "Base incomplète"
        elif item["additional_missing"]:
            css, state = "additional-missing", "Metadata spécifique incomplète"
        elif item["role"] == "unknown":
            css, state = "review", "Rôle à vérifier"
        else:
            css, state = "complete", "Complet"
        process = item["processing"]
        clean = process["header_footer_cleaning"]
        extraction = process["text_extraction_status"]
        chunk_summary = chunk_summaries.get(item["document_id"])
        if chunk_summary:
            if chunk_summary["red"]:
                chunk_css, chunk_verdict = "chunk-red", "Problème structurel"
            elif chunk_summary["yellow"]:
                chunk_css, chunk_verdict = "chunk-yellow", "À vérifier"
            else:
                chunk_css, chunk_verdict = "chunk-green", "Bon structurellement"
            chunk_file = ROOT / "chunks" / f"{item['document_id']}.json"
            chunk_items = json.loads(chunk_file.read_text(encoding="utf-8")) if chunk_file.exists() else []
            chunk_lines = "".join(
                f"<li>#{chunk['chunk_index']} · {html.escape(chunk['section_title'])} · {chunk['word_count']} mots · {html.escape(', '.join(chunk['quality_issues']) or 'OK')}</li>"
                for chunk in chunk_items
            )
            chunk_html = (
                f"<div class='{chunk_css}'><strong>{chunk_verdict}</strong><br>"
                f"{chunk_summary['chunks']} chunks : {chunk_summary['green']} verts, {chunk_summary['yellow']} jaunes, {chunk_summary['red']} rouges"
                f"<details><summary>Voir tous les chunks</summary><ul>{chunk_lines}</ul></details></div>"
            )
        else:
            chunk_html = "Chunks non générés"
        rows.append(
            f"<tr class='{css}'><td>{item['year']}</td><td><a href='{html.escape(item['file_url'])}' target='_blank'>{html.escape(item['title'] or 'Sans titre')}</a></td>"
            f"<td>{html.escape(item['role'])}</td><td><strong>{state}</strong></td>"
            f"<td>{html.escape(', '.join(item['base_missing']) or 'Aucun')}</td>"
            f"<td>{html.escape(', '.join(item['additional_missing']) or 'Aucun')}</td>"
            f"<td>Interpellation: {html.escape(item['dates'].get('interpellation_date') or '—')}<br>"
            f"Réponses: {html.escape('; '.join((r.get('response_number') or '?') + ' — ' + (r.get('response_date') or 'date ?') for r in item['dates'].get('responses', [])) or '—')}</td>"
            f"<td>Natif: {clean['raw_words']} → {clean['clean_words']}<br>{clean['removed_blocks']} blocs retirés<br>Retenu: {html.escape(process.get('selected_text', {}).get('method', 'native_pdf'))} — {process.get('selected_text', {}).get('words', clean['clean_words'])} mots</td>"
            f"<td>{'Oui' if extraction['needs_ocr'] else 'Non'}</td>"
            f"<td>{chunk_html}</td>"
            f"<td><a href='metadata/{item['document_id']}.json' target='_blank'>JSON</a> · <a href='clean_text/{item['document_id']}.txt' target='_blank'>Texte</a> · <a href='removed_blocks/{item['document_id']}.json' target='_blank'>Blocs retirés</a> · <a href='chunk_details/{item['document_id']}.html' target='_blank'>Chunks</a></td></tr>"
        )
    failure_rows = "".join(f"<li>{html.escape(x['filename'])}: {html.escape(x['error'])}</li>" for x in failures) or "<li>Aucun</li>"
    page = f"""<!doctype html><html lang='fr'><head><meta charset='utf-8'><title>Audit interpellations 2021–2026</title>
<style>body{{font:14px/1.45 system-ui;margin:24px;color:#172033}}.legend{{display:flex;gap:12px;flex-wrap:wrap;margin:14px 0}}.tag{{padding:8px 12px;border-radius:7px}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #d9dfeb;padding:8px;vertical-align:top}}th{{background:#edf1f7;position:sticky;top:0}}tr.base-missing,.red{{background:#ffd8d8}}tr.additional-missing,.yellow{{background:#fff3bf}}tr.review,.orange{{background:#ffe0b2}}tr.complete,.green{{background:#e1f5e6}}.chunk-green,.chunk-yellow,.chunk-red{{padding:8px;border-radius:7px}}.chunk-green{{background:#c9efd3}}.chunk-yellow{{background:#ffe69a}}.chunk-red{{background:#ffb3b3}}summary{{cursor:pointer;font-weight:650}}ul{{padding-left:20px}}</style></head><body>
<h1>Audit complet des interpellations 2021–2026</h1><p>Source unique : <a href='{scraper.SOURCE_PAGE}' target='_blank'>{scraper.SOURCE_PAGE}</a>. Les PDF ont été retéléchargés ; le nettoyage est non destructif. <a href='chunks_audit.html'><strong>Voir l’audit global des chunks</strong></a>.</p>
<div class='legend'><span class='tag red'>Rouge : base metadata incomplète</span><span class='tag yellow'>Jaune : metadata interpellation incomplète</span><span class='tag orange'>Orange : rôle à vérifier</span><span class='tag green'>Vert : complet</span></div>
<table><thead><tr><th>Année</th><th>Document</th><th>Rôle</th><th>État</th><th>Manque base</th><th>Manque spécifique</th><th>Dates détectées</th><th>Nettoyage</th><th>OCR recommandé</th><th>Qualité des chunks</th><th>Fichiers</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
<h2>Échecs</h2><ul>{failure_rows}</ul></body></html>"""
    (ROOT / "audit.html").write_text(page, encoding="utf-8")


if __name__ == "__main__":
    main()
