from __future__ import annotations

import hashlib
import html
import json
import re
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parent
MOTIONS_ROOT = ROOT.parent
PILOT = MOTIONS_ROOT / "pilot"
METADATA_DIR = ROOT / "metadata"
TEXT_DIR = ROOT / "clean_text"
CHUNKS_DIR = ROOT / "chunks"
REMOVED_DIR = ROOT / "removed_blocks"
DETAIL_DIR = ROOT / "document_details"
CHUNK_DETAIL_DIR = ROOT / "chunk_details"
MAX_WORDS = 450
OVERLAP_WORDS = 60

BASE_REQUIRED = {
    "document_id", "commune", "document_family", "category", "document_role",
    "title", "source_title", "source_page_url", "file_url", "listing_year", "legislature", "document_date",
    "content_hash", "extraction_method", "processing_status",
}
MOTION_REQUIRED = {
    "authors", "political_status", "contains_majority_report",
    "contains_minority_report", "decision_date",
}


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def word_tokens(text: str) -> list[str]:
    return re.findall(r"\S+", text)


def word_count(text: str) -> int:
    return len(word_tokens(text))


def comparable_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def chunk_hash(text: str) -> str:
    return hashlib.sha256(comparable_text(text).encode("utf-8")).hexdigest()


def artifact_for(record: dict) -> Path:
    return PILOT / "artifacts" / Path(record["document_metadata"]["file_url"]).stem


def split_sections(text: str, role: str, report_type: str | None = None) -> list[dict]:
    if not text.strip():
        return []
    headings = []
    unqualified_report_title = "Rapport de majorité" if report_type == "majority_and_minority_reports" else "Rapport de commission"
    patterns = [
        ("commission_report", "Rapport de majorité", r"(?im)^\s*rapport\s+de\s+majorit[ée].*$"),
        ("commission_report", "Rapport de minorité", r"(?im)^\s*rapport\s+de\s+minorit[ée].*$"),
        ("commission_report", unqualified_report_title, r"(?im)^\s*(?:rapport\s+de\s+la\s+commission|commission\s+charg[ée]e).*$"),
        ("council_decision", "Décision du Conseil communal", r"(?im)^\s*(?:extrait\s+du\s+proc[èe]s-verbal|d[ée]cision\s+du\s+conseil\s+communal).*$"),
        ("motion_text", "Motion", r"(?im)^\s*(?:motion\s*:|motionnaires\s*:|nous,?\s+les\s+soussign[ée]s).*$"),
    ]
    for component, title, pattern in patterns:
        for match in re.finditer(pattern, text):
            headings.append((match.start(), component, title))
    headings.sort()
    deduped = []
    for item in headings:
        if not deduped or item[0] - deduped[-1][0] > 120:
            deduped.append(item)
    headings = deduped
    if not headings:
        default_component = {
            "motion_text": "motion_text",
            "commission_report": "commission_report",
            "council_decision": "council_decision",
        }.get(role, "combined_motion_document" if role.startswith("combined_") else "unknown_component")
        return [{"component": default_component, "section_title": role, "content": text.strip()}]
    sections = []
    if headings[0][0] > 0:
        # In a combined dossier, content before the first report/decision is
        # the original motion.
        default = "motion_text" if role in {"motion_text", "combined_motion_report", "combined_motion_report_decision"} else "commission_report" if role == "commission_report" else "combined_motion_document"
        sections.append({"component": default, "section_title": "Début du document", "content": text[:headings[0][0]].strip()})
    for index, (start, component, title) in enumerate(headings):
        end = headings[index + 1][0] if index + 1 < len(headings) else len(text)
        content = text[start:end].strip()
        if content:
            sections.append({"component": component, "section_title": title, "content": content})
    return [section for section in sections if section["content"]]


def split_words(text: str) -> list[str]:
    tokens = word_tokens(text)
    chunks = []
    start = 0
    while start < len(tokens):
        end = min(len(tokens), start + MAX_WORDS)
        chunks.append(" ".join(tokens[start:end]))
        if end == len(tokens):
            break
        start = end - OVERLAP_WORDS
    return chunks


def build_chunks(record: dict, text: str) -> list[dict]:
    base = record["document_metadata"]
    chunks = []
    report_type = record.get("motion_metadata", {}).get("report_type")
    for section_index, section in enumerate(split_sections(text, base["document_role"], report_type)):
        pieces = split_words(section["content"])
        for piece_index, content in enumerate(pieces):
            issues = []
            count = word_count(content)
            if count > MAX_WORDS:
                issues.append("chunk_too_long")
            if count < 60 and piece_index < len(pieces) - 1:
                issues.append("chunk_too_short")
            if section["component"] in {"unknown_component", "combined_motion_document"}:
                issues.append("component_needs_review")
            quality = "red" if "chunk_too_long" in issues else "yellow" if issues else "green"
            index = len(chunks)
            embedding_input = (
                f"Famille: {base['document_family']}\nCatégorie: {base['category']}\n"
                f"Rôle: {base['document_role']}\nTitre: {base['title']}\n"
                f"Section: {section['section_title']}\n\n{content}"
            )
            chunks.append(
                {
                    "chunk_id": f"{base['document_id']}#chunk-{index:03d}",
                    "document_id": base["document_id"],
                    "chunk_index": index,
                    "section_index": section_index,
                    "component": section["component"],
                    "section_title": section["section_title"],
                    "content": content,
                    "word_count": count,
                    "chunk_hash": chunk_hash(content),
                    "embedding_input": embedding_input,
                    "quality": quality,
                    "quality_issues": issues,
                }
            )
    return chunks


def audit_metadata(record: dict) -> dict:
    base = record["document_metadata"]
    motion = record["motion_metadata"]
    processing = record["processing"]
    missing_base = sorted(field for field in BASE_REQUIRED if base.get(field) in (None, "", []))
    missing_motion = sorted(field for field in MOTION_REQUIRED if field not in motion)
    warnings = []
    if not base.get("document_date"):
        warnings.append("document_date_missing")
    if not motion.get("authors"):
        warnings.append("authors_missing")
    role = base.get("document_role", "")
    if "report" in role and not motion.get("commission"):
        warnings.append("commission_missing_for_report")
    if "decision" in role and not motion.get("decision"):
        warnings.append("decision_missing_for_decision_document")
    if "decision" in role and not motion.get("decision_date"):
        warnings.append("decision_date_missing")
    extraction = processing.get("text_extraction_status", {})
    if extraction.get("needs_ocr"):
        warnings.append("ocr_required")
    return {
        "missing_base_fields": missing_base,
        "missing_motion_fields": missing_motion,
        "warnings": warnings,
        "valid": not missing_base and not missing_motion and not warnings,
    }


def document_detail(entry: dict, record: dict, chunks: list[dict]) -> str:
    base = record["document_metadata"]
    metadata_json = html.escape(json.dumps(record, ensure_ascii=False, indent=2))
    issues_json = html.escape(json.dumps(entry["metadata_audit"], ensure_ascii=False, indent=2))
    chunk_rows = "".join(
        f"<tr class='{chunk['quality']}'><td>{chunk['chunk_id']}</td><td>{chunk['component']}</td><td>{chunk['word_count']}</td><td>{html.escape(', '.join(chunk['quality_issues']) or 'aucune')}</td></tr>"
        for chunk in chunks
    )
    return f"""<!doctype html><html lang='fr'><head><meta charset='utf-8'><title>{html.escape(base['title'])}</title><style>{CSS}</style></head><body>
<p><a href='../audit.html'>← Audit principal</a> · <a href='../clean_text/{base['document_id']}.txt'>Texte nettoyé</a> · <a href='../removed_blocks/{base['document_id']}.json'>Blocs retirés</a> · <a href='../chunk_details/{base['document_id']}.html'>Voir les chunks</a></p><h1>{html.escape(base['title'])}</h1>
<div class='cards'><div class='card'><b>{entry['text_words']}</b><span>mots</span></div><div class='card'><b>{len(chunks)}</b><span>chunks</span></div><div class='card'><b>{entry['chunk_counts']['green']}</b><span>verts</span></div><div class='card'><b>{len(entry['metadata_audit']['warnings'])}</b><span>alertes métadonnées</span></div></div>
<h2>Alertes</h2><pre>{issues_json}</pre><h2>Métadonnées finales</h2><pre>{metadata_json}</pre>
<h2>Chunks</h2><table><thead><tr><th>ID</th><th>Composant</th><th>Mots</th><th>Alertes</th></tr></thead><tbody>{chunk_rows}</tbody></table></body></html>"""


def chunk_detail(record: dict, chunks: list[dict]) -> str:
    base = record["document_metadata"]
    cards = []
    for chunk in chunks:
        cards.append(
            f"<article class='{chunk['quality']}'><h2>{chunk['chunk_id']}</h2><p><b>{chunk['component']}</b> · {chunk['section_title']} · {chunk['word_count']} mots</p>"
            f"<p>Alertes : {html.escape(', '.join(chunk['quality_issues']) or 'aucune')}</p>"
            f"<details><summary>Contenu</summary><pre>{html.escape(chunk['content'])}</pre></details>"
            f"<details><summary>Entrée embedding</summary><pre>{html.escape(chunk['embedding_input'])}</pre></details></article>"
        )
    needs_ocr = bool(record["processing"].get("text_extraction_status", {}).get("needs_ocr"))
    empty = (
        "<p class='badbox'>Aucun chunk : OCR requis avant indexation.</p>"
        if not chunks and needs_ocr
        else "<p>Aucun chunk : image sans texte utile, ignorée après contrôle manuel.</p>"
        if not chunks
        else ""
    )
    return f"""<!doctype html><html lang='fr'><head><meta charset='utf-8'><title>Chunks — {html.escape(base['title'])}</title><style>{CSS}</style></head><body><p><a href='../audit.html'>← Audit principal</a></p><h1>{html.escape(base['title'])}</h1>{empty}{''.join(cards)}</body></html>"""


CSS = """
body{font:14px/1.5 system-ui;margin:26px;color:#172033;background:#fbfcfe}a{color:#1769aa}table{border-collapse:collapse;width:100%;background:white}th,td{border:1px solid #d9dfeb;padding:8px;text-align:left;vertical-align:top}th{background:#edf1f7}.cards{display:flex;gap:12px;flex-wrap:wrap;margin:18px 0}.card{background:white;border:1px solid #d9dfeb;border-radius:9px;padding:12px 18px;min-width:110px}.card b{display:block;font-size:24px;color:#1769aa}.card span{color:#5c6878}pre{white-space:pre-wrap;word-break:break-word;background:#f5f7fa;padding:12px;max-height:560px;overflow:auto}tr.green,article.green{background:#effaf2}tr.yellow,article.yellow{background:#fff8d8}tr.red,article.red{background:#ffe5e5}.badge{display:inline-block;border-radius:999px;padding:3px 8px;font-weight:700}.badge.green{background:#d9f2e1;color:#176b35}.badge.yellow{background:#fff0b4;color:#7a5900}.badge.red{background:#ffd5d5;color:#922}article{border:2px solid #ccd5e2;border-radius:10px;padding:14px;margin:14px 0}.badbox{background:#ffe5e5;border-left:4px solid #c33;padding:13px}summary{cursor:pointer;font-weight:700}
"""


def main() -> None:
    combined_paths = sorted((PILOT / "combined_metadata_view").glob("*.json"))
    cleaning = {item["document_id"]: item for item in read_json(PILOT / "cleaning_test" / "report.json")}
    entries = []
    for path in combined_paths:
        record = read_json(path)
        base = record["document_metadata"]
        document_id = base["document_id"]
        clean_path = PILOT / "selected_text" / f"{document_id}.txt"
        text = clean_path.read_text(encoding="utf-8").strip()
        chunks = build_chunks(record, text)
        metadata_audit = audit_metadata(record)
        counts = {color: sum(chunk["quality"] == color for chunk in chunks) for color in ("green", "yellow", "red")}
        extraction_needs_ocr = bool(record["processing"].get("text_extraction_status", {}).get("needs_ocr"))
        if not chunks and extraction_needs_ocr:
            counts["red"] = 1
        clean_stats = cleaning[document_id]
        entry = {
            "document_id": document_id,
            "title": base["title"],
            "year": base["listing_year"],
            "role": base["document_role"],
            "file_url": base["file_url"],
            "base_missing": metadata_audit["missing_base_fields"],
            "additional_missing": metadata_audit["missing_motion_fields"],
            "processing": record["processing"],
            "dates": {
                "document_date": base.get("document_date"),
                "decision_date": record["motion_metadata"].get("decision_date"),
                "commission_date": (record["motion_metadata"].get("commission") or {}).get("meeting", {}).get("date"),
            },
            "metadata_audit": metadata_audit,
            "text_words": word_count(text),
            "raw_words": clean_stats["raw_words"],
            "removed_words": clean_stats["removed_word_count"],
            "chunks": len(chunks),
            "chunk_counts": counts,
        }
        write_json(METADATA_DIR / f"{document_id}.json", record)
        TEXT_DIR.mkdir(parents=True, exist_ok=True)
        (TEXT_DIR / f"{document_id}.txt").write_text(text + "\n", encoding="utf-8")
        write_json(CHUNKS_DIR / f"{document_id}.json", chunks)
        write_json(REMOVED_DIR / f"{document_id}.json", clean_stats.get("removed_blocks", []))
        DETAIL_DIR.mkdir(parents=True, exist_ok=True)
        CHUNK_DETAIL_DIR.mkdir(parents=True, exist_ok=True)
        (DETAIL_DIR / f"{document_id}.html").write_text(document_detail(entry, record, chunks), encoding="utf-8")
        (CHUNK_DETAIL_DIR / f"{document_id}.html").write_text(chunk_detail(record, chunks), encoding="utf-8")
        entries.append(entry)

    summary = {
        "documents": len(entries),
        "valid_metadata": sum(item["metadata_audit"]["valid"] for item in entries),
        "documents_with_warnings": sum(bool(item["metadata_audit"]["warnings"]) for item in entries),
        "ocr_required": sum("ocr_required" in item["metadata_audit"]["warnings"] for item in entries),
        "chunks": sum(item["chunks"] for item in entries),
        "green_chunks": sum(item["chunk_counts"]["green"] for item in entries),
        "yellow_chunks": sum(item["chunk_counts"]["yellow"] for item in entries),
        "red_chunks_or_documents": sum(item["chunk_counts"]["red"] for item in entries),
        "missing_field_counts": dict(Counter(field for item in entries for field in item["metadata_audit"]["missing_base_fields"] + item["metadata_audit"]["missing_motion_fields"])),
        "warning_counts": dict(Counter(warning for item in entries for warning in item["metadata_audit"]["warnings"])),
    }
    write_json(ROOT / "audit.json", {"documents": entries, "failures": [], "summary": summary})
    write_json(ROOT / "chunks_summary.json", [{"document_id": x["document_id"], "title": x["title"], "chunks": x["chunks"], **x["chunk_counts"]} for x in entries])

    rows = []
    chunk_rows = []
    for item in entries:
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
        counts = item["chunk_counts"]
        if not item["chunks"] and extraction["needs_ocr"]:
            chunk_css, chunk_verdict = "chunk-red", "Aucun chunk — OCR requis"
        elif not item["chunks"]:
            chunk_css, chunk_verdict = "chunk-green", "Aucun texte utile — image ignorée"
        elif counts["red"]:
            chunk_css, chunk_verdict = "chunk-red", "Problème structurel"
        elif counts["yellow"]:
            chunk_css, chunk_verdict = "chunk-yellow", "À vérifier"
        else:
            chunk_css, chunk_verdict = "chunk-green", "Bon structurellement"
        chunk_items = read_json(CHUNKS_DIR / f"{item['document_id']}.json")
        chunk_lines = "".join(
            f"<li>#{chunk['chunk_index']} · {html.escape(chunk['section_title'])} · {chunk['word_count']} mots · {html.escape(', '.join(chunk['quality_issues']) or 'OK')}</li>"
            for chunk in chunk_items
        )
        chunk_html = (
            f"<div class='{chunk_css}'><strong>{chunk_verdict}</strong><br>{item['chunks']} chunks : "
            f"{counts['green']} verts, {counts['yellow']} jaunes, {counts['red']} rouges"
            f"<details><summary>Voir tous les chunks</summary><ul>{chunk_lines or '<li>Aucun</li>'}</ul></details></div>"
        )
        dates = item["dates"]
        rows.append(
            f"<tr class='{css}'><td>{item['year']}</td><td><a href='{html.escape(item['file_url'])}' target='_blank'>{html.escape(item['title'])}</a></td>"
            f"<td>{html.escape(item['role'])}</td><td><strong>{state}</strong></td>"
            f"<td>{html.escape(', '.join(item['base_missing']) or 'Aucun')}</td><td>{html.escape(', '.join(item['additional_missing']) or 'Aucun')}</td>"
            f"<td>Document : {html.escape(dates.get('document_date') or '—')}<br>Commission : {html.escape(dates.get('commission_date') or '—')}<br>Décision : {html.escape(dates.get('decision_date') or '—')}</td>"
            f"<td>Natif : {clean['raw_words']} → {clean['clean_words']}<br>{clean['removed_blocks']} blocs retirés<br>Retenu : {html.escape(process['selected_text']['method'])} — {process['selected_text']['words']} mots</td>"
            f"<td>{'Oui' if extraction['needs_ocr'] else 'Non'}</td><td>{chunk_html}</td>"
            f"<td><a href='metadata/{item['document_id']}.json' target='_blank'>JSON</a> · <a href='clean_text/{item['document_id']}.txt' target='_blank'>Texte</a> · <a href='removed_blocks/{item['document_id']}.json' target='_blank'>Blocs retirés</a> · <a href='chunk_details/{item['document_id']}.html' target='_blank'>Chunks</a></td></tr>"
        )
        chunk_row_css = "red" if counts["red"] else "yellow" if counts["yellow"] else "green"
        chunk_rows.append(
            f"<tr class='{chunk_row_css}'><td><a href='chunk_details/{item['document_id']}.html'>{html.escape(item['title'])}</a></td><td>{item['chunks']}</td><td>{counts['green']}</td><td>{counts['yellow']}</td><td>{counts['red']}</td></tr>"
        )
    audit_css = """body{font:14px/1.45 system-ui;margin:24px;color:#172033}.legend{display:flex;gap:12px;flex-wrap:wrap;margin:14px 0}.tag{padding:8px 12px;border-radius:7px}table{border-collapse:collapse;width:100%}th,td{border:1px solid #d9dfeb;padding:8px;vertical-align:top}th{background:#edf1f7;position:sticky;top:0}tr.base-missing,.red{background:#ffd8d8}tr.additional-missing,.yellow{background:#fff3bf}tr.review,.orange{background:#ffe0b2}tr.complete,.green{background:#e1f5e6}.chunk-green,.chunk-yellow,.chunk-red{padding:8px;border-radius:7px}.chunk-green{background:#c9efd3}.chunk-yellow{background:#ffe69a}.chunk-red{background:#ffb3b3}summary{cursor:pointer;font-weight:650}ul{padding-left:20px}"""
    page = f"""<!doctype html><html lang='fr'><head><meta charset='utf-8'><title>Audit motions 2021–2026</title><style>{audit_css}</style></head><body>
<h1>Audit complet des motions 2021–2026</h1><p>Source JSON : <a href='https://www.la-tour-de-peilz.ch/srch/' target='_blank'>https://www.la-tour-de-peilz.ch/srch/</a>. Le nettoyage est non destructif. <a href='chunks_audit.html'><strong>Voir l’audit global des chunks</strong></a>.</p>
<div class='legend'><span class='tag red'>Rouge : base metadata incomplète</span><span class='tag yellow'>Jaune : metadata motion incomplète</span><span class='tag orange'>Orange : rôle à vérifier</span><span class='tag green'>Vert : complet</span></div>
<table><thead><tr><th>Année</th><th>Document</th><th>Rôle</th><th>État</th><th>Manque base</th><th>Manque spécifique</th><th>Dates détectées</th><th>Nettoyage</th><th>OCR recommandé</th><th>Qualité des chunks</th><th>Fichiers</th></tr></thead><tbody>{''.join(rows)}</tbody></table><h2>Échecs</h2><ul><li>Aucun</li></ul></body></html>"""
    (ROOT / "audit.html").write_text(page, encoding="utf-8")
    chunk_page = f"""<!doctype html><html lang='fr'><head><meta charset='utf-8'><title>Audit des chunks</title><style>{audit_css}</style></head><body><p><a href='audit.html'>← Audit principal</a></p><h1>Audit structurel des chunks</h1><p>Vert : taille et section correctes. Jaune : chunk court ou composant à vérifier. Rouge : chunk trop long ou document sans texte exploitable.</p><table><thead><tr><th>Document</th><th>Chunks</th><th>Verts</th><th>Jaunes</th><th>Rouges</th></tr></thead><tbody>{''.join(chunk_rows)}</tbody></table></body></html>"""
    (ROOT / "chunks_audit.html").write_text(chunk_page, encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(ROOT / "audit.html")


if __name__ == "__main__":
    main()
