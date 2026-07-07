from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
from collections import Counter
from pathlib import Path

import fitz
import requests


SCRIPT_ROOT = Path(__file__).resolve().parent
ROOT = SCRIPT_ROOT
PROJECT_ROOT = ROOT.parents[2]
SCRAPER_DIR = PROJECT_ROOT / "scrape-la-tour-de-peilz"
sys.path.insert(0, str(SCRAPER_DIR))

import scrape_proces_verbaux_search_json_2021_2026 as endpoint


SELECTED_NUMBERS = {1, 2, 8, 12, 18, 19, 25, 26, 32, 35}
MAX_WORDS = 450
OVERLAP_WORDS = 60
CSS = """body{font:14px/1.45 system-ui;margin:24px;color:#172033;background:#f5f7fa}a{color:#185a9d}table{border-collapse:collapse;width:100%;background:white}th,td{border:1px solid #d8deea;padding:8px;vertical-align:top}th{background:#edf1f7;position:sticky;top:0}.complete,.green{background:#e5f6e9}.review,.orange{background:#ffe7c2}.yellow{background:#fff4bf}.red{background:#ffdada}.tag{display:inline-block;padding:4px 8px;border-radius:8px;margin:2px}pre{white-space:pre-wrap;word-break:break-word;background:#101820;color:#eef5fa;padding:12px;border-radius:8px;max-height:620px;overflow:auto}details summary{cursor:pointer;font-weight:650}article{border:2px solid #ccd5e2;border-radius:10px;padding:14px;margin:14px 0;background:white}article.green{border-color:#58a66d}article.yellow{border-color:#d8a928}article.red{border-color:#d44}footer{margin-top:24px;color:#667}"""


def normalize_line(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def locate_or_download(item: dict) -> Path:
    matches = list((PROJECT_ROOT / "documents" / "la-tour-de-peilz").rglob(item["filename"]))
    if matches:
        return matches[0]
    target = SCRIPT_ROOT / "pdfs" / item["filename"]
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        response = requests.get(item["file_url"], headers=endpoint.HEADERS, timeout=120)
        response.raise_for_status()
        target.write_bytes(response.content)
    return target


def repeated_margin_lines(page_texts: list[str]) -> list[str]:
    counts, originals = Counter(), {}
    for text in page_texts:
        lines = [normalize_line(line) for line in text.splitlines() if normalize_line(line)]
        for line in set(lines[:5] + lines[-5:]):
            key = line.casefold()
            if 3 <= len(key) <= 120:
                counts[key] += 1
                originals.setdefault(key, line)
    threshold = max(3, round(len(page_texts) * 0.35))
    return [originals[key] for key, count in counts.items() if count >= threshold]


def clean_pages(page_texts: list[str], repeated: list[str]) -> tuple[str, int]:
    repeated_keys = {line.casefold() for line in repeated}
    kept, removed = [], 0
    for page in page_texts:
        for raw in page.splitlines():
            line = normalize_line(raw)
            if not line:
                kept.append("")
                continue
            if line.casefold() in repeated_keys or re.fullmatch(r"(?:-|–)?\s*\d{1,3}\s*(?:-|–)?", line):
                removed += 1
                continue
            kept.append(line)
        kept.append("\f")
    text = "\n".join(kept)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip(), removed


def extract_agenda(text: str) -> list[dict]:
    marker = re.search(r"ORDRE DU JOUR", text, flags=re.I)
    if not marker:
        return []
    after = text[marker.end():]
    stop = re.search(r"(?m)^Appel\s*$", after, flags=re.I)
    block = after[:stop.start()] if stop else after[:15000]
    lines = [normalize_line(line) for line in block.splitlines()]
    entries, current_number, current_lines = [], None, []

    def flush() -> None:
        nonlocal current_number, current_lines
        if not current_number:
            return
        title = ""
        for line in current_lines:
            if not line:
                continue
            if title.endswith("-"):
                title = title[:-1] + line
            else:
                title = f"{title} {line}".strip()
        if title:
            entries.append({"number": current_number, "title": title})

    for line in lines:
        match = re.match(r"^(\d+(?:\.\d+)*)\.\s*(.*)$", line)
        if match:
            flush()
            current_number = match.group(1)
            current_lines = [match.group(2)] if match.group(2) else []
        elif current_number:
            current_lines.append(line)
    flush()
    return entries


def extract_session_metadata(text: str, agenda: list[dict]) -> dict:
    head = text[:12000]
    start = re.search(r"\b(?:à|a)\s+(\d{1,2})\s*h\s*(\d{2})\b", head, flags=re.I)
    end = re.search(r"(?:s[ée]ance|session)\s+(?:est\s+)?lev[ée]e?\s+(?:à|a)\s+(\d{1,2})\s*h\s*(\d{2})", text[-10000:], flags=re.I)
    location = re.search(r"(?:Lieu|Salle)\s*:\s*([^\n]{3,120})", head, flags=re.I)
    president = re.search(r"Pr[ée]sidence\s*:\s*([^\n]{3,100})", head, flags=re.I)
    secretary = re.search(r"Secr[ée]taire\s*:\s*([^\n]{3,100})", head, flags=re.I)
    present = re.search(r"(\d+)\s+(?:personnes\s+)?pr[ée]sent(?:e)?s?\s+sur\s+(\d+)\s+(?:membres\s+)?[ée]lu", head, flags=re.I)
    excused_block = re.search(r"Excus[ée](?:e)?s?\s*:\s*([\s\S]{0,1000}?)(?:Absent|Ordre du jour|M\.\s+le Pr[ée]sident|Mme\s+la Pr[ée]sidente)", head, flags=re.I)
    absent_block = re.search(r"Absent(?:e)?s?\s*:\s*([\s\S]{0,700}?)(?:Ordre du jour|M\.\s+le Pr[ée]sident|Mme\s+la Pr[ée]sidente)", head, flags=re.I)

    def people_count(block: re.Match | None) -> int | None:
        if not block:
            return None
        lines = [normalize_line(line).strip("•-,;") for line in block.group(1).splitlines()]
        people = [line for line in lines if 2 <= len(line.split()) <= 7 and not re.search(r"\d", line)]
        return len(people) or None

    contains_votes = bool(re.search(r"\b(?:au vote|mise aux voix|par \d+ voix|à l.unanimit[ée]|majorit[ée])\b", text, flags=re.I))
    return {
        "meeting_start_time": f"{int(start.group(1)):02d}:{start.group(2)}" if start else None,
        "meeting_end_time": f"{int(end.group(1)):02d}:{end.group(2)}" if end else None,
        "location": normalize_line(location.group(1)) if location else None,
        "presiding_officer": normalize_line(president.group(1)) if president else None,
        "secretary": normalize_line(secretary.group(1)) if secretary else None,
        "attendance": {
            "present_count": int(present.group(1)) if present else None,
            "elected_count": int(present.group(2)) if present else None,
            "excused_count": people_count(excused_block),
            "absent_count": people_count(absent_block),
        },
        "agenda_item_count": len(agenda) or None,
        "contains_votes": contains_votes,
    }


def normalized_for_match(value: str) -> str:
    value = value.casefold()
    value = re.sub(r"[^a-zà-ÿ0-9]+", " ", value)
    return normalize_line(value)


def split_components(text: str, agenda: list[dict]) -> list[dict]:
    if not agenda:
        return [{"component": "meeting_minutes", "content": text, "agenda_item_number": None, "agenda_item_title": None}]
    by_number = {item["number"]: item for item in agenda}
    lines = text.splitlines()
    body_start = next((index for index, line in enumerate(lines) if re.fullmatch(r"Appel", normalize_line(line), flags=re.I)), 0)
    boundaries = []
    for index in range(body_start, len(lines)):
        line = normalize_line(lines[index])
        match = re.match(r"^(\d+(?:\.\d+)*)\.\s*(.*)$", line)
        if not match or match.group(1) not in by_number:
            continue
        number = match.group(1)
        candidate_parts = [match.group(2)] if match.group(2) else []
        for following in lines[index + 1:index + 5]:
            following = normalize_line(following)
            if not following or re.match(r"^\d+(?:\.\d+)*\.\s*", following):
                break
            candidate_parts.append(following)
            if len(" ".join(candidate_parts)) >= 90:
                break
        candidate = normalized_for_match(" ".join(candidate_parts))
        expected = normalized_for_match(by_number[number]["title"])
        expected_words = expected.split()[:6]
        if expected_words and all(word in candidate.split()[:18] for word in expected_words[:4]):
            if not boundaries or boundaries[-1][1] != number:
                boundaries.append((index, number))
    if not boundaries:
        return [{"component": "meeting_minutes", "content": text, "agenda_item_number": None, "agenda_item_title": None}]
    parts = []
    overview = "\n".join(lines[:boundaries[0][0]]).strip()
    if overview:
        parts.append({"component": "session_overview", "content": overview, "agenda_item_number": None, "agenda_item_title": None})
    for position, (start, number) in enumerate(boundaries):
        end = boundaries[position + 1][0] if position + 1 < len(boundaries) else len(lines)
        content = "\n".join(lines[start:end]).strip()
        next_number = boundaries[position + 1][1] if position + 1 < len(boundaries) else None
        if next_number and next_number.startswith(number + ".") and len(content.split()) < 20:
            continue
        if content:
            parts.append({
                "component": "agenda_item", "content": content,
                "agenda_item_number": number, "agenda_item_title": by_number[number]["title"],
            })
    return parts


def word_chunks(text: str) -> list[str]:
    words = text.split()
    if len(words) <= MAX_WORDS:
        return [text] if text.strip() else []
    chunks, start = [], 0
    while start < len(words):
        end = min(start + MAX_WORDS, len(words))
        chunks.append(" ".join(words[start:end]))
        if end == len(words):
            break
        start = end - OVERLAP_WORDS
    return chunks


def build_chunks(metadata: dict, text: str, agenda: list[dict]) -> list[dict]:
    base = metadata["document_metadata"]
    chunks, index = [], 0
    for part in split_components(text, agenda):
        component = part["component"]
        for content in word_chunks(part["content"]):
            word_count = len(content.split())
            issues = []
            if word_count < (3 if component == "agenda_item" else 10):
                issues.append("chunk_too_short")
            if word_count > MAX_WORDS:
                issues.append("chunk_too_long")
            embedding_input = (
                f"category: {base['category']}\n"
                f"pv_number: {metadata['minutes_metadata']['pv_number']}\n"
                f"title: {base['title']}\n"
                + f"\n{content}"
            )
            chunks.append({
                "chunk_id": f"{base['document_id']}#chunk-{index:03d}",
                "document_id": base["document_id"], "chunk_index": index,
                "component": component, "content": content, "word_count": word_count,
                "agenda_item_number": part["agenda_item_number"],
                "component_title": part["agenda_item_title"] or "Informations générales de la séance",
                "chunk_hash": hashlib.sha256(content.encode()).hexdigest(),
                "embedding_input": embedding_input,
                "quality": "red" if "chunk_too_long" in issues else "yellow" if issues else "green",
                "quality_issues": issues,
            })
            index += 1
    return chunks


def audit_item(item: dict) -> dict:
    pdf_path = locate_or_download(item)
    document = fitz.open(pdf_path)
    page_texts = [page.get_text("text") for page in document]
    page_stats = [
        {"page": index, "characters": len(text.strip()), "images": len(page.get_images(full=True)), "low_text": len(text.strip()) < 80}
        for index, (page, text) in enumerate(zip(document, page_texts), 1)
    ]
    repeated = repeated_margin_lines(page_texts)
    clean_text, removed = clean_pages(page_texts, repeated)
    coverage = sum(not page["low_text"] for page in page_stats) / max(len(page_stats), 1)
    low_text_image_pages = [page["page"] for page in page_stats if page["low_text"] and page["images"]]
    needs_ocr = len(clean_text) < 500 or coverage < 0.25
    agenda = extract_agenda(clean_text)
    details = extract_session_metadata(clean_text, agenda)
    document_id = "doc_" + hashlib.sha256(item["file_url"].encode()).hexdigest()[:20]
    metadata = {
        "document_metadata": {
            "document_id": document_id, "commune": item["commune"],
            "document_family": item["document_family"], "category": item["category"],
            "document_role": item["document_role"], "title": item["title"],
            "source_title": item["source_title"], "source_page_url": item["source_page_url"],
            "file_url": item["file_url"], "filename": item["filename"],
            "listing_year": item["listing_year"], "legislature": item["legislature"],
            "document_date": item["session_date"], "content_hash": hashlib.sha256(clean_text.encode()).hexdigest(),
            "extraction_method": "native_pdf_pending_ocr" if needs_ocr else "native_pdf",
            "processing_status": "needs_ocr" if needs_ocr else "pilot_audited",
        },
        "minutes_metadata": {
            "pv_number": item["pv_number"], "session_date": item["session_date"],
            "session_type": item["session_type"], **details,
        },
        "processing": {
            "text_extraction_status": {
                "characters_extracted": len(clean_text), "text_available": bool(clean_text),
                "needs_ocr": needs_ocr, "page_text_coverage": round(coverage, 3),
                "low_text_image_pages": low_text_image_pages,
            },
            "header_footer_cleaning": {
                "raw_words": len(" ".join(page_texts).split()), "clean_words": len(clean_text.split()),
                "removed_blocks": removed, "repeated_margin_candidates": repeated,
            },
            "selected_text": {"method": "native_pdf_pending_ocr" if needs_ocr else "native_pdf", "words": len(clean_text.split())},
        },
    }
    base_missing = [key for key in ("document_id", "commune", "document_family", "category", "document_role", "title", "file_url", "filename", "document_date") if metadata["document_metadata"].get(key) in (None, "")]
    additional_missing = [key for key in ("pv_number", "session_date") if metadata["minutes_metadata"].get(key) in (None, "")]
    warnings = ["ocr_required"] if needs_ocr else []
    chunks = build_chunks(metadata, clean_text, agenda)
    for folder in ("metadata", "clean_text", "removed_blocks", "chunks"):
        (ROOT / folder).mkdir(exist_ok=True)
    (ROOT / "metadata" / f"{document_id}.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (ROOT / "clean_text" / f"{document_id}.txt").write_text(clean_text + "\n", encoding="utf-8")
    (ROOT / "removed_blocks" / f"{document_id}.json").write_text(json.dumps({"repeated_margin_candidates": repeated, "removed_blocks": removed}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (ROOT / "chunks" / f"{document_id}.json").write_text(json.dumps(chunks, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "document_id": document_id, "title": item["title"], "pv_number": item["pv_number"],
        "session_date": item["session_date"], "pages": len(page_stats), "coverage": round(coverage, 3),
        "warnings": warnings, "audit": {"base_missing": base_missing, "additional_missing": additional_missing, "warnings": warnings},
        "metadata": metadata, "chunks": len(chunks),
        "green_chunks": sum(chunk["quality"] == "green" for chunk in chunks),
        "yellow_chunks": sum(chunk["quality"] == "yellow" for chunk in chunks),
        "red_chunks": sum(chunk["quality"] == "red" for chunk in chunks),
        "page_stats": page_stats, "preview": clean_text[:6000], "file_url": item["file_url"],
    }


def detail_html(record: dict) -> str:
    rows = "".join(f"<tr><td>{p['page']}</td><td>{p['characters']}</td><td>{p['images']}</td><td>{'Oui' if p['low_text'] else 'Non'}</td></tr>" for p in record["page_stats"])
    return f"""<!doctype html><html lang='fr'><head><meta charset='utf-8'><title>{html.escape(record['title'])}</title><style>{CSS}</style></head><body><p><a href='../audit.html'>← Audit</a> · <a href='../chunk_details/{record['document_id']}.html'>Chunks</a> · <a href='{html.escape(record['file_url'])}'>PDF officiel</a></p><h1>{html.escape(record['title'])}</h1><h2>Contrôles</h2><pre>{html.escape(json.dumps(record['audit'], ensure_ascii=False, indent=2))}</pre><h2>Métadonnées</h2><pre>{html.escape(json.dumps(record['metadata'], ensure_ascii=False, indent=2))}</pre><h2>Pages</h2><table><tr><th>Page</th><th>Caractères</th><th>Images</th><th>Texte faible</th></tr>{rows}</table><h2>Aperçu nettoyé</h2><pre>{html.escape(record['preview'])}</pre></body></html>"""


def chunk_detail_html(record: dict, chunks: list[dict]) -> str:
    cards = "".join(f"<article class='{c['quality']}'><h2>{html.escape(c['chunk_id'])}</h2><p>Composant : {html.escape(c['component'])} · {c['word_count']} mots · {html.escape(', '.join(c['quality_issues']) or 'OK')}</p><details open><summary>Contenu</summary><pre>{html.escape(c['content'])}</pre></details><details><summary>Embedding input</summary><pre>{html.escape(c['embedding_input'])}</pre></details></article>" for c in chunks)
    return f"""<!doctype html><html lang='fr'><head><meta charset='utf-8'><title>Chunks — {html.escape(record['title'])}</title><style>{CSS}</style></head><body><p><a href='../audit.html'>← Audit</a> · <a href='../details/{record['document_id']}.html'>Métadonnées</a></p><h1>{html.escape(record['title'])}</h1>{cards}</body></html>"""


def audit_html(records: list[dict]) -> str:
    rows = []
    for record in records:
        audit = record["audit"]
        css = "review" if audit["base_missing"] or audit["additional_missing"] or audit["warnings"] else "complete"
        meta = record["metadata"]
        minutes = meta["minutes_metadata"]
        processing = meta["processing"]
        rows.append(f"""<tr class='{css}'><td>{record['pv_number']}</td><td>{record['session_date']}</td><td><a href='{html.escape(record['file_url'])}'>{html.escape(record['title'])}</a></td><td>{'À vérifier' if css == 'review' else 'Complet'}<br>{html.escape(', '.join(record['warnings']))}</td><td>{html.escape(', '.join(audit['base_missing']) or 'Aucun')}</td><td>{html.escape(', '.join(audit['additional_missing']) or 'Aucun')}</td><td>Début : {minutes.get('meeting_start_time') or '—'}<br>Fin : {minutes.get('meeting_end_time') or '—'}<br>Présidence : {html.escape(minutes.get('presiding_officer') or '—')}<br>Présents : {minutes['attendance'].get('present_count') or '—'}</td><td>{processing['header_footer_cleaning']['raw_words']} → {processing['header_footer_cleaning']['clean_words']} mots<br>{processing['header_footer_cleaning']['removed_blocks']} blocs retirés</td><td>{'Oui' if processing['text_extraction_status']['needs_ocr'] else 'Non'}<br>Couverture {record['coverage']:.0%}</td><td>{record['chunks']} chunks<br>{record['green_chunks']} verts, {record['yellow_chunks']} jaunes, {record['red_chunks']} rouges</td><td><a href='metadata/{record['document_id']}.json'>JSON</a> · <a href='clean_text/{record['document_id']}.txt'>Texte</a> · <a href='details/{record['document_id']}.html'>Contrôles</a> · <a href='chunk_details/{record['document_id']}.html'>Chunks</a></td></tr>""")
    return f"""<!doctype html><html lang='fr'><head><meta charset='utf-8'><title>Audit — procès-verbaux</title><style>{CSS}</style></head><body><h1>Audit des procès-verbaux — {len(records)} documents</h1><p>Source : endpoint JSON officiel. Les chunks sont structurés par point de l’ordre du jour. <a href='chunks_audit.html'><strong>Voir l’audit global des chunks</strong></a>.</p><table><thead><tr><th>PV</th><th>Date</th><th>Document</th><th>État</th><th>Manque base</th><th>Manque additionnel</th><th>Session</th><th>Nettoyage</th><th>OCR</th><th>Chunks</th><th>Fichiers</th></tr></thead><tbody>{''.join(rows)}</tbody></table></body></html>"""


def chunks_audit_html(records: list[dict]) -> str:
    rows = "".join(
        f"<tr class='{'red' if record['red_chunks'] else 'yellow' if record['yellow_chunks'] else 'green'}'>"
        f"<td>{record['pv_number']}</td><td>{record['session_date']}</td>"
        f"<td><a href='chunk_details/{record['document_id']}.html'>{html.escape(record['title'])}</a></td>"
        f"<td>{record['chunks']}</td><td>{record['green_chunks']}</td><td>{record['yellow_chunks']}</td><td>{record['red_chunks']}</td></tr>"
        for record in records
    )
    return f"""<!doctype html><html lang='fr'><head><meta charset='utf-8'><title>Audit global des chunks — PV</title><style>{CSS}</style></head><body><p><a href='audit.html'>← Audit principal</a></p><h1>Audit global des chunks — {len(records)} procès-verbaux</h1><p>Cliquez sur un document pour contrôler chaque point de l’ordre du jour et son entrée d’embedding.</p><table><tr><th>PV</th><th>Date</th><th>Document</th><th>Chunks</th><th>Verts</th><th>Jaunes</th><th>Rouges</th></tr>{rows}</table></body></html>"""


def main() -> None:
    global ROOT
    parser = argparse.ArgumentParser()
    parser.add_argument("--full", action="store_true", help="Auditer les 35 procès-verbaux")
    args = parser.parse_args()
    ROOT = SCRIPT_ROOT.parent / "full-audit" if args.full else SCRIPT_ROOT
    ROOT.mkdir(parents=True, exist_ok=True)
    payload = json.loads((SCRIPT_ROOT.parent / "scraper-test" / "search-json-test.json").read_text(encoding="utf-8"))
    selected = payload["documents"] if args.full else [item for item in payload["documents"] if item["pv_number"] in SELECTED_NUMBERS]
    if not args.full and len(selected) != 10:
        raise SystemExit(f"Sélection attendue: 10, obtenue: {len(selected)}")
    records = [audit_item(item) for item in selected]
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
    }, ensure_ascii=False, indent=2))
    print(ROOT / "audit.html")


if __name__ == "__main__":
    main()
