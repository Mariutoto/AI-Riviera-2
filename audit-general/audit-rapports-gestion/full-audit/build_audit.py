from __future__ import annotations

import hashlib
import html
import json
import re
import sys
from collections import Counter
from pathlib import Path

import fitz
import requests


ROOT = Path(__file__).resolve().parent
OCR_DIR = ROOT / "ocr_comparison_2021"
PROJECT_ROOT = ROOT.parents[2]
SCRAPER_DIR = PROJECT_ROOT / "scrape-la-tour-de-peilz"
sys.path.insert(0, str(SCRAPER_DIR))

import scrape_rapports_gestion_search_json_2021_2026 as endpoint
import scrape_rapport_gestion_2021_2024 as legacy


MAX_WORDS = 450
OVERLAP_WORDS = 60
CSS = """body{font:14px/1.45 system-ui;margin:24px;color:#172033;background:#f5f7fa}a{color:#185a9d}table{border-collapse:collapse;width:100%;background:white}th,td{border:1px solid #d8deea;padding:8px;vertical-align:top}th{background:#edf1f7;position:sticky;top:0}.complete,.green{background:#e5f6e9}.review,.orange{background:#ffe7c2}.yellow{background:#fff4bf}.red{background:#ffdada}pre{white-space:pre-wrap;word-break:break-word;background:#101820;color:#eef5fa;padding:12px;border-radius:8px;max-height:650px;overflow:auto}details summary{cursor:pointer;font-weight:650}article{border:2px solid #ccd5e2;border-radius:10px;padding:14px;margin:14px 0;background:white}article.green{border-color:#58a66d}article.yellow{border-color:#d8a928}article.red{border-color:#d44}.tag{display:inline-block;padding:4px 8px;border-radius:8px;margin:2px}"""


def normalize_line(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def locate_or_download(item: dict) -> Path:
    matches = list((PROJECT_ROOT / "documents" / "la-tour-de-peilz").rglob(item["filename"]))
    if matches:
        return matches[0]
    target = ROOT / "pdfs" / item["filename"]
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        response = requests.get(item["file_url"], headers=endpoint.HEADERS, timeout=180)
        response.raise_for_status()
        target.write_bytes(response.content)
    return target


def repeated_margins(page_texts: list[str]) -> list[str]:
    counts, original = Counter(), {}
    for text in page_texts:
        lines = [normalize_line(line) for line in text.splitlines() if normalize_line(line)]
        for line in set(lines[:5] + lines[-5:]):
            key = line.casefold()
            if 2 <= len(key) <= 140:
                counts[key] += 1
                original.setdefault(key, line)
    threshold = max(3, round(len(page_texts) * 0.20))
    return [original[key] for key, count in counts.items() if count >= threshold]


def explicit_boilerplate(line: str) -> bool:
    line = re.sub(r"^#{1,6}\s*", "", line).strip()
    return bool(
        re.fullmatch(r"(?:-|–)?\s*\d{1,4}\s*(?:-|–)?", line)
        or re.fullmatch(r"(?:Conseil communal|Municipalité|Maison de commune|Grand-Rue 46(?:\s*[-·]\s*CP\s*\d+)?|1814 La Tour-de-Peilz)", line, flags=re.I)
        or re.search(r"\bwww\.la-tour-de-peilz\.ch\b", line, flags=re.I)
        or re.search(r"\b[\w.+-]+@(?:[\w-]+\.)*la-tour-de-peilz\.ch\b", line, flags=re.I)
        or re.fullmatch(r"021\s+977\s+01\s+\d{2}.*", line)
    )


def clean_ocr_pages(page_texts: list[str]) -> tuple[str, int, list[dict], list[str]]:
    margins = repeated_margins(page_texts)
    margin_keys = {line.casefold() for line in margins}
    kept, blocks = [], []
    for page_number, page in enumerate(page_texts, 1):
        for raw in page.splitlines():
            line = raw.strip()
            normalized = normalize_line(line)
            if re.fullmatch(r"!\[[^]]*]\([^)]*\)", normalized):
                blocks.append({"page": page_number, "text": normalized, "reason": "ocr_image_marker"})
                continue
            if normalized and normalized.casefold() in margin_keys:
                blocks.append({"page": page_number, "text": normalized, "reason": "repeated_margin"})
                continue
            if normalized and explicit_boilerplate(normalized):
                blocks.append({"page": page_number, "text": normalized, "reason": "boilerplate"})
                continue
            # Preserve Markdown headings, lists and tables instead of flattening them.
            kept.append(line.rstrip())
        kept.append("\f")
    text = "\n".join(kept)
    text = re.sub(r"[ \t]+$", "", text, flags=re.M)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip(), len(blocks), blocks, margins


def clean_pages(page_texts: list[str], margins: list[str]) -> tuple[str, int, list[dict]]:
    margin_keys = {line.casefold() for line in margins}
    kept, removed, blocks = [], 0, []
    for page_number, page in enumerate(page_texts, 1):
        for raw in page.splitlines():
            line = normalize_line(raw)
            if line and (line.casefold() in margin_keys or explicit_boilerplate(line)):
                removed += 1
                blocks.append({"page": page_number, "text": line, "reason": "repeated_margin" if line.casefold() in margin_keys else "boilerplate"})
                continue
            kept.append(line)
        kept.append("\f")
    text = "\n".join(kept)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip(), removed, blocks


def detect_components(text: str) -> list[str]:
    normalized = legacy.normalize(text)
    components = ["annual_management_report"]
    if "rapport de la commission de gestion" in normalized:
        components.append("commission_report")
    if "reponse municipale" in normalized or "reponse de la municipalite" in normalized or "reponses de la municipalite" in normalized:
        components.append("municipal_response")
    if "extrait du proces-verbal" in normalized:
        components.append("council_decision")
    return components


def role_from_components(components: list[str]) -> str:
    suffixes = {
        "annual_management_report": "management_report",
        "commission_report": "commission",
        "municipal_response": "response",
        "council_decision": "decision",
    }
    present = [suffixes[name] for name in suffixes if name in components]
    return present[0] if len(present) == 1 else "combined_" + "_".join(present)


def split_components(text: str) -> list[dict]:
    markers = []
    patterns = [
        ("commission_report", r"(?im)^\s*(?:#{1,6}\s*)?RAPPORT\s+(?:DE\s+LA\s+)?COMMISSION\s+DE\s+GESTION\b"),
        ("municipal_response", r"(?im)^\s*(?:#{1,6}\s*)?R[ÉE]PONSE(?:S?\s+DE\s+LA)?\s+MUNICIPAL(?:IT[ÉE]|E)\b"),
        ("council_decision", r"(?im)^\s*(?:#{1,6}\s*)?EXTRAIT\s+DU\s+PROC[ÈE]S-VERBAL\b"),
    ]
    for component, pattern in patterns:
        for match in re.finditer(pattern, text):
            markers.append((match.start(), component))
    markers.sort()
    deduped = []
    for marker in markers:
        if not deduped or marker[0] - deduped[-1][0] > 200:
            deduped.append(marker)
    parts = []
    first = deduped[0][0] if deduped else len(text)
    if text[:first].strip():
        parts.append({"component": "annual_management_report", "content": text[:first].strip()})
    for index, (start, component) in enumerate(deduped):
        end = deduped[index + 1][0] if index + 1 < len(deduped) else len(text)
        content = text[start:end].strip()
        if content:
            parts.append({"component": component, "content": content})
    return parts or [{"component": "annual_management_report", "content": text}]


def word_chunks(text: str) -> list[str]:
    words = list(re.finditer(r"\S+", text))
    if len(words) <= MAX_WORDS:
        return [text] if text.strip() else []
    chunks, start = [], 0
    while start < len(words):
        end = min(start + MAX_WORDS, len(words))
        # Slice the source text itself so Markdown tables, headings and lists
        # retain their line breaks inside the embedding input.
        chunks.append(text[words[start].start():words[end - 1].end()].strip())
        if end == len(words):
            break
        start = end - OVERLAP_WORDS
    return chunks


def build_chunks(metadata: dict, text: str) -> list[dict]:
    base, specific = metadata["document_metadata"], metadata["management_report_metadata"]
    chunks = []
    for part in split_components(text):
        for content in word_chunks(part["content"]):
            index = len(chunks)
            words = len(content.split())
            issues = []
            if words > MAX_WORDS:
                issues.append("chunk_too_long")
            if words < 10:
                issues.append("chunk_too_short")
            embedding_input = (
                f"category: {base['category']}\ntitle: {base['title']}\n"
                f"component: {part['component']}\n\n{content}"
            )
            chunks.append({
                "chunk_id": f"{base['document_id']}#chunk-{index:03d}", "document_id": base["document_id"],
                "chunk_index": index, "component": part["component"], "content": content,
                "word_count": words, "chunk_hash": hashlib.sha256(content.encode()).hexdigest(),
                "embedding_input": embedding_input, "quality_issues": issues,
                "quality": "red" if "chunk_too_long" in issues else "yellow" if issues else "green",
            })
    return chunks


def extract_counts(text: str) -> tuple[int | None, int | None]:
    prefix = r"(?im)^\s*(?:#{1,6}\s*)?(?:\*{1,2})?"
    suffix = r"(?:\*{1,2})?\s*(?:N[°O]\s*)?(\d+)"
    observations = {int(value) for value in re.findall(prefix + r"OBSERVATION" + suffix, text)}
    wishes = {int(value) for value in re.findall(prefix + r"(?:V[ŒO]UX?|VOEUX?)" + suffix, text)}
    return max(observations) if observations else None, max(wishes) if wishes else None


def audit_item(item: dict) -> dict:
    pdf_path = locate_or_download(item)
    document = fitz.open(pdf_path)
    page_texts = [page.get_text("text") for page in document]
    page_stats = [
        {"page": index, "characters": len(text.strip()), "images": len(page.get_images(full=True)), "low_text": len(text.strip()) < 80}
        for index, (page, text) in enumerate(zip(document, page_texts), 1)
    ]
    native_margins = repeated_margins(page_texts)
    native_clean_text, native_removed_count, native_removed_blocks = clean_pages(page_texts, native_margins)
    ocr_path = OCR_DIR / f"{pdf_path.stem}.md"
    if ocr_path.exists():
        ocr_raw = ocr_path.read_text(encoding="utf-8")
        ocr_pages = ocr_raw.split("\f")
        if ocr_pages and not ocr_pages[-1].strip():
            ocr_pages.pop()
        clean_text, removed_count, removed_blocks, margins = clean_ocr_pages(ocr_pages)
        selected_method = "mistral_ocr"
        selected_page_texts = ocr_pages
    else:
        clean_text, removed_count, removed_blocks, margins = native_clean_text, native_removed_count, native_removed_blocks, native_margins
        selected_method = "native_pdf"
        selected_page_texts = page_texts
    coverage = sum(bool(text.strip()) for text in selected_page_texts) / max(len(selected_page_texts), 1)
    low_text_image_pages = [page["page"] for page in page_stats if page["low_text"] and page["images"]]
    needs_ocr = selected_method != "mistral_ocr" and (len(clean_text) < 500 or coverage < 0.25)
    components = list(dict.fromkeys(part["component"] for part in split_components(clean_text)))
    observations_count, wishes_count = extract_counts(clean_text)
    members = legacy.extract_commission_members(clean_text) if "commission_report" in components else []
    decision = legacy.extract_council_decision(clean_text, str(item["management_year"])) if "council_decision" in components else None
    document_id = "doc_" + hashlib.sha256(item["file_url"].encode()).hexdigest()[:20]
    role = role_from_components(components)
    metadata = {
        "document_metadata": {
            "document_id": document_id, "commune": item["commune"], "document_family": item["document_family"],
            "category": item["category"], "document_role": role, "title": item["title"],
            "source_title": item["source_title"], "source_page_url": item["source_page_url"],
            "file_url": item["file_url"], "filename": item["filename"], "listing_year": item["listing_year"],
            "legislature": item["legislature"], "document_date": (decision or {}).get("decision_date"),
            "content_hash": hashlib.sha256(clean_text.encode()).hexdigest(),
            "extraction_method": selected_method,
            "processing_status": "needs_ocr" if needs_ocr else "audited",
        },
        "management_report_metadata": {
            "management_year": item["management_year"], "period_start": item["period_start"],
            "period_end": item["period_end"], "decision_date": (decision or {}).get("decision_date"),
            "components": components,
            "commission": {"members": members, "observations_count": observations_count, "wishes_count": wishes_count},
        },
        "processing": {
            "text_extraction_status": {
                "characters_extracted": len(clean_text), "text_available": bool(clean_text), "needs_ocr": needs_ocr,
                "page_text_coverage": round(coverage, 3), "low_text_image_pages": low_text_image_pages,
            },
            "header_footer_cleaning": {
                "raw_words": len(" ".join(selected_page_texts).split()), "clean_words": len(clean_text.split()),
                "removed_blocks": removed_count, "repeated_margin_candidates": margins,
            },
            "selected_text": {
                "method": selected_method, "words": len(clean_text.split()),
                "native_pdf_words": len(native_clean_text.split()),
            },
        },
    }
    base_missing = [field for field in ("document_id", "commune", "document_family", "category", "document_role", "title", "file_url", "filename", "document_date") if metadata["document_metadata"].get(field) in (None, "")]
    additional_missing = [field for field in ("management_year", "period_start", "period_end") if metadata["management_report_metadata"].get(field) in (None, "")]
    warnings = []
    if needs_ocr:
        warnings.append("ocr_required")
    if "commission_report" in components and not members:
        warnings.append("commission_members_not_detected")
    chunks = build_chunks(metadata, clean_text)
    for folder in ("metadata", "clean_text", "removed_blocks", "chunks"):
        (ROOT / folder).mkdir(exist_ok=True)
    (ROOT / "metadata" / f"{document_id}.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (ROOT / "clean_text" / f"{document_id}.txt").write_text(clean_text + "\n", encoding="utf-8")
    (ROOT / "removed_blocks" / f"{document_id}.json").write_text(json.dumps({"blocks": removed_blocks, "repeated_margin_candidates": margins}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (ROOT / "chunks" / f"{document_id}.json").write_text(json.dumps(chunks, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "document_id": document_id, "title": item["title"], "management_year": item["management_year"],
        "pages": len(page_stats), "coverage": round(coverage, 3), "components": components,
        "commission_members": len(members), "warnings": warnings,
        "audit": {"base_missing": base_missing, "additional_missing": additional_missing, "warnings": warnings},
        "metadata": metadata, "chunks": len(chunks),
        "green_chunks": sum(chunk["quality"] == "green" for chunk in chunks),
        "yellow_chunks": sum(chunk["quality"] == "yellow" for chunk in chunks),
        "red_chunks": sum(chunk["quality"] == "red" for chunk in chunks),
        "page_stats": page_stats, "preview": clean_text[:6000], "file_url": item["file_url"],
    }


def detail_html(record: dict) -> str:
    rows = "".join(f"<tr><td>{p['page']}</td><td>{p['characters']}</td><td>{p['images']}</td><td>{'Oui' if p['low_text'] else 'Non'}</td></tr>" for p in record["page_stats"])
    return f"""<!doctype html><html lang='fr'><head><meta charset='utf-8'><title>{html.escape(record['title'])}</title><style>{CSS}</style></head><body><p><a href='../audit.html'>← Audit</a> · <a href='../chunk_details/{record['document_id']}.html'>Chunks</a> · <a href='{html.escape(record['file_url'])}'>PDF officiel</a></p><h1>{html.escape(record['title'])}</h1><h2>Contrôles</h2><pre>{html.escape(json.dumps(record['audit'], ensure_ascii=False, indent=2))}</pre><h2>Métadonnées</h2><pre>{html.escape(json.dumps(record['metadata'], ensure_ascii=False, indent=2))}</pre><h2>Diagnostic des pages</h2><table><tr><th>Page</th><th>Caractères</th><th>Images</th><th>Texte faible</th></tr>{rows}</table><h2>Aperçu nettoyé</h2><pre>{html.escape(record['preview'])}</pre></body></html>"""


def chunk_detail_html(record: dict, chunks: list[dict]) -> str:
    cards = "".join(f"<article class='{c['quality']}'><h2>{html.escape(c['chunk_id'])}</h2><p>Composant : {html.escape(c['component'])} · {c['word_count']} mots · {html.escape(', '.join(c['quality_issues']) or 'OK')}</p><details open><summary>Contenu</summary><pre>{html.escape(c['content'])}</pre></details><details><summary>Embedding input</summary><pre>{html.escape(c['embedding_input'])}</pre></details></article>" for c in chunks)
    return f"""<!doctype html><html lang='fr'><head><meta charset='utf-8'><title>Chunks — {html.escape(record['title'])}</title><style>{CSS}</style></head><body><p><a href='../audit.html'>← Audit</a> · <a href='../details/{record['document_id']}.html'>Métadonnées</a></p><h1>{html.escape(record['title'])}</h1>{cards}</body></html>"""


def audit_html(records: list[dict]) -> str:
    rows = []
    for record in records:
        audit, metadata = record["audit"], record["metadata"]
        css = "review" if audit["base_missing"] or audit["additional_missing"] or audit["warnings"] else "complete"
        specific, processing = metadata["management_report_metadata"], metadata["processing"]
        rows.append(f"""<tr class='{css}'><td>{record['management_year']}</td><td><a href='{html.escape(record['file_url'])}'>{html.escape(record['title'])}</a></td><td>{html.escape(', '.join(record['components']))}</td><td>{'À vérifier' if css == 'review' else 'Complet'}<br>{html.escape(', '.join(record['warnings']))}</td><td>{html.escape(', '.join(audit['base_missing']) or 'Aucun')}</td><td>{html.escape(', '.join(audit['additional_missing']) or 'Aucun')}</td><td>Décision : {specific.get('decision_date') or '—'}<br>Commission : {record['commission_members']} membres<br>Observations : {specific['commission'].get('observations_count') or '—'}<br>Vœux : {specific['commission'].get('wishes_count') or '—'}</td><td>{processing['header_footer_cleaning']['raw_words']} → {processing['header_footer_cleaning']['clean_words']} mots<br>{processing['header_footer_cleaning']['removed_blocks']} blocs retirés</td><td>{html.escape(processing['selected_text']['method'])}<br>Couverture {record['coverage']:.0%}</td><td>{record['chunks']} chunks<br>{record['green_chunks']} verts, {record['yellow_chunks']} jaunes, {record['red_chunks']} rouges</td><td><a href='metadata/{record['document_id']}.json'>JSON</a> · <a href='clean_text/{record['document_id']}.txt'>Texte</a> · <a href='removed_blocks/{record['document_id']}.json'>Blocs</a> · <a href='details/{record['document_id']}.html'>Contrôles</a> · <a href='chunk_details/{record['document_id']}.html'>Chunks</a></td></tr>""")
    return f"""<!doctype html><html lang='fr'><head><meta charset='utf-8'><title>Audit — rapports de gestion</title><style>{CSS}</style></head><body><h1>Audit des rapports de gestion — 5 documents</h1><p>Source JSON officielle. Nettoyage des marges répétées sans suppression des titres de services ou tableaux. <a href='chunks_audit.html'><strong>Voir l’audit global des chunks</strong></a>.</p><table><thead><tr><th>Année</th><th>Document</th><th>Composants</th><th>État</th><th>Manque base</th><th>Manque additionnel</th><th>Rapport</th><th>Nettoyage</th><th>OCR</th><th>Chunks</th><th>Fichiers</th></tr></thead><tbody>{''.join(rows)}</tbody></table></body></html>"""


def chunks_audit_html(records: list[dict]) -> str:
    rows = "".join(f"<tr class='{'red' if r['red_chunks'] else 'yellow' if r['yellow_chunks'] else 'green'}'><td>{r['management_year']}</td><td><a href='chunk_details/{r['document_id']}.html'>{html.escape(r['title'])}</a></td><td>{r['chunks']}</td><td>{r['green_chunks']}</td><td>{r['yellow_chunks']}</td><td>{r['red_chunks']}</td><td>{html.escape(', '.join(r['components']))}</td></tr>" for r in records)
    return f"""<!doctype html><html lang='fr'><head><meta charset='utf-8'><title>Chunks — rapports de gestion</title><style>{CSS}</style></head><body><p><a href='audit.html'>← Audit principal</a></p><h1>Audit global des chunks — 5 rapports</h1><table><tr><th>Année</th><th>Document</th><th>Chunks</th><th>Verts</th><th>Jaunes</th><th>Rouges</th><th>Composants</th></tr>{rows}</table></body></html>"""


def main() -> None:
    payload = json.loads((ROOT.parent / "scraper-test" / "search-json-test.json").read_text(encoding="utf-8"))
    records = [audit_item(item) for item in payload["documents"]]
    for folder in ("details", "chunk_details"):
        (ROOT / folder).mkdir(exist_ok=True)
    for record in records:
        chunks = json.loads((ROOT / "chunks" / f"{record['document_id']}.json").read_text(encoding="utf-8"))
        (ROOT / "details" / f"{record['document_id']}.html").write_text(detail_html(record), encoding="utf-8")
        (ROOT / "chunk_details" / f"{record['document_id']}.html").write_text(chunk_detail_html(record, chunks), encoding="utf-8")
    (ROOT / "audit.json").write_text(json.dumps({"documents": records}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (ROOT / "audit.html").write_text(audit_html(records), encoding="utf-8")
    (ROOT / "chunks_audit.html").write_text(chunks_audit_html(records), encoding="utf-8")
    print(json.dumps({
        "documents": len(records), "needs_ocr": sum("ocr_required" in r["warnings"] for r in records),
        "chunks": sum(r["chunks"] for r in records), "yellow_chunks": sum(r["yellow_chunks"] for r in records),
        "red_chunks": sum(r["red_chunks"] for r in records),
        "warnings": dict(Counter(warning for record in records for warning in record["warnings"])),
    }, ensure_ascii=False, indent=2))
    print(ROOT / "audit.html")


if __name__ == "__main__":
    main()
