from __future__ import annotations

import hashlib
import html
import importlib.util
import json
import re
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parent
POSTULATS_ROOT = ROOT.parent
AUDIT_GENERAL = POSTULATS_ROOT.parent
PILOT = POSTULATS_ROOT / "pilot"
METADATA_DIR, TEXT_DIR = ROOT / "metadata", ROOT / "clean_text"
REMOVED_DIR, CHUNKS_DIR = ROOT / "removed_blocks", ROOT / "chunks"
DETAIL_DIR, CHUNK_DETAIL_DIR = ROOT / "document_details", ROOT / "chunk_details"
MAX_WORDS, OVERLAP_WORDS = 450, 60


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


motion_pilot = load_module("motion_pilot_helpers", AUDIT_GENERAL / "audit-motions" / "build_pilot.py")

BASE_FIELDS = {
    "document_id", "commune", "document_family", "category", "document_role",
    "title", "source_title", "source_page_url", "file_url", "listing_year",
    "legislature", "document_date", "content_hash", "extraction_method", "processing_status",
}
POSTULAT_FIELDS = {
    "authors", "political_status", "contains_majority_report",
    "contains_minority_report", "decision_date",
}

CSS = """body{font:14px/1.45 system-ui;margin:24px;color:#172033}.legend{display:flex;gap:12px;flex-wrap:wrap;margin:14px 0}.tag{padding:8px 12px;border-radius:7px}table{border-collapse:collapse;width:100%}th,td{border:1px solid #d9dfeb;padding:8px;vertical-align:top}th{background:#edf1f7;position:sticky;top:0}tr.base-missing,.red{background:#ffd8d8}tr.additional-missing,.yellow{background:#fff3bf}tr.review,.orange{background:#ffe0b2}tr.complete,.green{background:#e1f5e6}.chunk-green,.chunk-yellow,.chunk-red{padding:8px;border-radius:7px}.chunk-green{background:#c9efd3}.chunk-yellow{background:#ffe69a}.chunk-red{background:#ffb3b3}summary{cursor:pointer;font-weight:650}ul{padding-left:20px}pre{white-space:pre-wrap;word-break:break-word;background:#f5f7fa;padding:10px;max-height:550px;overflow:auto}article{border:2px solid #ccd5e2;border-radius:10px;padding:14px;margin:14px 0}article.green{background:#effaf2}article.yellow{background:#fff8d8}article.red{background:#ffe5e5}"""


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def tokens(text: str) -> list[str]:
    return re.findall(r"\S+", text)


def word_count(text: str) -> int:
    return len(tokens(text))


def split_words(text: str) -> list[str]:
    values, result, start = tokens(text), [], 0
    while start < len(values):
        end = min(len(values), start + MAX_WORDS)
        result.append(" ".join(values[start:end]))
        if end == len(values):
            break
        start = end - OVERLAP_WORDS
    return result


def split_sections(text: str, role: str, report_type: str | None) -> list[dict]:
    if not text.strip():
        return []
    unqualified = "Rapport de majorité" if report_type == "majority_and_minority_reports" else "Rapport de commission"
    patterns = [
        ("commission_report", "Rapport de majorité", r"(?im)^\s*rapport\s+de\s+majorit[ée].*$"),
        ("commission_report", "Rapport de minorité", r"(?im)^\s*rapport\s+de\s+minorit[ée].*$"),
        ("commission_report", unqualified, r"(?im)^\s*(?:#\s*)?(?:\*\*)?(?:rapport\s+(?:de\s+la\s+commission|relatif\s+[àa]\s+la\s+prise)|commission\s+charg[ée]e).*$"),
        ("council_decision", "Décision du Conseil communal", r"(?im)^\s*(?:#\s*)?(?:\*\*)?(?:extrait(?:\s+du\s+proc[èe]s-verbal)?|d[ée]cision\s+du\s+conseil\s+communal).*$"),
        ("postulat_text", "Postulat", r"(?im)^\s*(?:#\s*)?postulat(?:\s+de|\s*:|\s*[-–—]|\s*$).*$"),
    ]
    headings = []
    for component, title, pattern in patterns:
        headings.extend((m.start(), component, title) for m in re.finditer(pattern, text))
    headings.sort()
    deduped = []
    for item in headings:
        if not deduped or item[0] - deduped[-1][0] > 120:
            deduped.append(item)
    headings = deduped
    if not headings:
        component = "postulat_text" if role == "postulat_text" else "commission_report" if role == "commission_report" else "council_decision" if role == "council_decision" else "unknown_component"
        return [{"component": component, "section_title": role, "content": text.strip()}]
    sections = []
    if headings[0][0] > 0:
        prefix = text[:headings[0][0]].strip()
        component = "postulat_text" if role.startswith("combined_postulat") or role == "postulat_text" else "commission_report" if role == "commission_report" else "unknown_component"
        # PDF page numbers occasionally survive extraction before the first real
        # heading. They are layout noise, not standalone semantic chunks.
        if prefix and not re.fullmatch(r"(?:page\s*)?\d{1,3}", prefix, flags=re.I):
            sections.append({"component": component, "section_title": "Début du document", "content": prefix})
    for index, (start, component, title) in enumerate(headings):
        end = headings[index + 1][0] if index + 1 < len(headings) else len(text)
        content = text[start:end].strip()
        if content:
            sections.append({"component": component, "section_title": title, "content": content})
    return sections


def build_chunks(record: dict, text: str) -> list[dict]:
    base, specific = record["document_metadata"], record["postulat_metadata"]
    output = []
    for section_index, section in enumerate(split_sections(text, base["document_role"], specific.get("report_type"))):
        pieces = split_words(section["content"])
        for piece_index, content in enumerate(pieces):
            issues, count = [], word_count(content)
            if count > MAX_WORDS:
                issues.append("chunk_too_long")
            if count < 60 and piece_index < len(pieces)-1:
                issues.append("chunk_too_short")
            if section["component"] == "unknown_component":
                issues.append("component_not_detected")
            quality = "red" if "chunk_too_long" in issues else "yellow" if issues else "green"
            index = len(output)
            embedding = f"Famille: {base['document_family']}\nCatégorie: {base['category']}\nRôle: {base['document_role']}\nTitre: {base['title']}\nSection: {section['section_title']}\n\n{content}"
            output.append({
                "chunk_id": f"{base['document_id']}#chunk-{index:03d}", "document_id": base["document_id"],
                "chunk_index": index, "section_index": section_index, "component": section["component"],
                "section_title": section["section_title"], "content": content, "word_count": count,
                "chunk_hash": hashlib.sha256(re.sub(r"\s+", " ", content).encode()).hexdigest(),
                "embedding_input": embedding, "quality": quality, "quality_issues": issues,
            })
    return output


def metadata_audit(record: dict) -> dict:
    base, specific, processing = record["document_metadata"], record["postulat_metadata"], record["processing"]
    base_missing = sorted(x for x in BASE_FIELDS if base.get(x) in (None, "", [], {}))
    manual_review = processing.get("manual_review") or {}
    accepted_absences = []
    if manual_review.get("reason") == "document_date_not_present" and "document_date" in base_missing:
        base_missing.remove("document_date")
        accepted_absences.append("document_date")
    specific_missing = sorted(x for x in POSTULAT_FIELDS if x not in specific)
    warnings = []
    if not specific.get("authors"):
        warnings.append("authors_missing")
    role = base.get("document_role", "")
    if "report" in role and not (specific.get("commission") or {}).get("members"):
        warnings.append("commission_members_missing_for_report")
    if "decision" in role and not specific.get("decision"):
        warnings.append("decision_missing_for_decision_document")
    if "decision" in role and not specific.get("decision_date"):
        warnings.append("decision_date_missing")
    if processing.get("text_extraction_status", {}).get("needs_ocr"):
        warnings.append("ocr_required")
    if role == "unknown":
        warnings.append("role_unknown")
    return {"base_missing": base_missing, "additional_missing": specific_missing, "accepted_absences": accepted_absences, "warnings": warnings, "valid": not base_missing and not specific_missing and not warnings}


def detail_page(entry: dict, record: dict) -> str:
    base = record["document_metadata"]
    return f"""<!doctype html><html lang='fr'><head><meta charset='utf-8'><title>{html.escape(base['title'])}</title><style>{CSS}</style></head><body><p><a href='../audit.html'>← Audit principal</a> · <a href='../clean_text/{base['document_id']}.txt'>Texte</a> · <a href='../removed_blocks/{base['document_id']}.json'>Blocs retirés</a> · <a href='../chunk_details/{base['document_id']}.html'>Chunks</a></p><h1>{html.escape(base['title'])}</h1><h2>Contrôles</h2><pre>{html.escape(json.dumps(entry['audit'], ensure_ascii=False, indent=2))}</pre><h2>Métadonnées finales</h2><pre>{html.escape(json.dumps(record, ensure_ascii=False, indent=2))}</pre></body></html>"""


def chunk_page(record: dict, chunks: list[dict]) -> str:
    base = record["document_metadata"]
    cards = "".join(f"<article class='{c['quality']}'><h2>{c['chunk_id']}</h2><p><b>{c['component']}</b> · {c['section_title']} · {c['word_count']} mots</p><p>Alertes : {html.escape(', '.join(c['quality_issues']) or 'aucune')}</p><details><summary>Contenu</summary><pre>{html.escape(c['content'])}</pre></details><details><summary>Entrée embedding</summary><pre>{html.escape(c['embedding_input'])}</pre></details></article>" for c in chunks)
    empty = "<p>Aucun chunk : OCR requis.</p>" if not chunks else ""
    return f"<!doctype html><html lang='fr'><head><meta charset='utf-8'><title>Chunks</title><style>{CSS}</style></head><body><p><a href='../audit.html'>← Audit principal</a></p><h1>{html.escape(base['title'])}</h1>{empty}{cards}</body></html>"


def main() -> None:
    cleaning_report, entries = [], []
    for path in sorted((PILOT / "combined_metadata_view").glob("*.json")):
        record = read_json(path)
        base, document_id = record["document_metadata"], record["document_metadata"]["document_id"]
        artifact = PILOT / "artifacts" / Path(base["file_url"]).stem
        text = (artifact / "native.txt").read_text(encoding="utf-8").strip()
        cleaning = motion_pilot.clean_pdf(base, artifact / "document.pdf")
        native_clean = cleaning.pop("clean_text").strip()
        selected_path = PILOT / "selected_text" / f"{document_id}.txt"
        selected = selected_path.read_text(encoding="utf-8").strip() if base.get("extraction_method") == "mistral_ocr" and selected_path.exists() else native_clean
        if not selected and text:
            selected = text
        extraction = record["processing"]["text_extraction_status"]
        extraction["needs_ocr"] = not bool(selected)
        record["processing"]["header_footer_cleaning"] = {
            "raw_words": cleaning["raw_words"], "clean_words": word_count(selected),
            "removed_blocks": cleaning["removed_blocks_count"],
        }
        record["processing"]["selected_text"] = {"method": base.get("extraction_method") if selected else "native_pdf_empty", "words": word_count(selected)}
        chunks = build_chunks(record, selected)
        audit = metadata_audit(record)
        counts = {color: sum(c["quality"] == color for c in chunks) for color in ("green", "yellow", "red")}
        if not chunks:
            counts["red"] = 1
        entry = {"document_id": document_id, "title": base["title"], "year": base["listing_year"], "file_url": base["file_url"], "role": base["document_role"], "audit": audit, "processing": record["processing"], "dates": {"document_date": base.get("document_date"), "commission_date": (record["postulat_metadata"].get("commission") or {}).get("meeting", {}).get("date"), "decision_date": record["postulat_metadata"].get("decision_date")}, "chunks": len(chunks), "chunk_counts": counts}
        write_json(METADATA_DIR / f"{document_id}.json", record)
        TEXT_DIR.mkdir(parents=True, exist_ok=True); (TEXT_DIR / f"{document_id}.txt").write_text(selected+"\n", encoding="utf-8")
        write_json(REMOVED_DIR / f"{document_id}.json", cleaning["removed_blocks"])
        write_json(CHUNKS_DIR / f"{document_id}.json", chunks)
        DETAIL_DIR.mkdir(parents=True, exist_ok=True); (DETAIL_DIR / f"{document_id}.html").write_text(detail_page(entry, record), encoding="utf-8")
        CHUNK_DETAIL_DIR.mkdir(parents=True, exist_ok=True); (CHUNK_DETAIL_DIR / f"{document_id}.html").write_text(chunk_page(record, chunks), encoding="utf-8")
        entries.append(entry); cleaning_report.append(cleaning)

    summary = {
        "documents": len(entries), "complete": sum(x["audit"]["valid"] for x in entries),
        "base_incomplete": sum(bool(x["audit"]["base_missing"]) for x in entries),
        "additional_incomplete": sum(bool(x["audit"]["additional_missing"]) for x in entries),
        "needs_ocr": sum(x["processing"]["text_extraction_status"]["needs_ocr"] for x in entries),
        "chunks": sum(x["chunks"] for x in entries), "green": sum(x["chunk_counts"]["green"] for x in entries),
        "yellow": sum(x["chunk_counts"]["yellow"] for x in entries), "red": sum(x["chunk_counts"]["red"] for x in entries),
        "missing_fields": dict(Counter(v for x in entries for v in x["audit"]["base_missing"] + x["audit"]["additional_missing"])),
        "warnings": dict(Counter(v for x in entries for v in x["audit"]["warnings"])),
    }
    write_json(ROOT / "audit.json", {"documents": entries, "failures": [], "summary": summary})
    write_json(ROOT / "chunks_summary.json", [{"document_id":x["document_id"],"title":x["title"],"chunks":x["chunks"],**x["chunk_counts"]} for x in entries])
    rows, chunk_rows = [], []
    for x in entries:
        if x["audit"]["base_missing"]: css,state="base-missing","Base incomplète"
        elif x["audit"]["additional_missing"]: css,state="additional-missing","Metadata spécifique incomplète"
        elif x["audit"]["warnings"]: css,state="review","À vérifier"
        else: css,state="complete","Complet"
        p=x["processing"]; clean=p["header_footer_cleaning"]; ext=p["text_extraction_status"]; c=x["chunk_counts"]
        verdict="Aucun chunk — OCR requis" if not x["chunks"] else "Problème structurel" if c["red"] else "À vérifier" if c["yellow"] else "Bon structurellement"
        chunk_css="chunk-red" if not x["chunks"] or c["red"] else "chunk-yellow" if c["yellow"] else "chunk-green"
        chunk_items=read_json(CHUNKS_DIR/f"{x['document_id']}.json"); lines="".join(f"<li>#{v['chunk_index']} · {html.escape(v['section_title'])} · {v['word_count']} mots · {html.escape(', '.join(v['quality_issues']) or 'OK')}</li>" for v in chunk_items)
        chunk_html=f"<div class='{chunk_css}'><strong>{verdict}</strong><br>{x['chunks']} chunks : {c['green']} verts, {c['yellow']} jaunes, {c['red']} rouges<details><summary>Voir tous</summary><ul>{lines or '<li>Aucun</li>'}</ul></details></div>"
        d=x["dates"]
        accepted_note = "<br>Date absente du document — validé" if "document_date" in x["audit"].get("accepted_absences", []) else ""
        rows.append(f"<tr class='{css}'><td>{x['year']}</td><td><a href='{html.escape(x['file_url'])}' target='_blank'>{html.escape(x['title'])}</a></td><td>{x['role']}</td><td><strong>{state}</strong><br>{html.escape(', '.join(x['audit']['warnings']))}{accepted_note}</td><td>{html.escape(', '.join(x['audit']['base_missing']) or 'Aucun')}</td><td>{html.escape(', '.join(x['audit']['additional_missing']) or 'Aucun')}</td><td>Document : {d['document_date'] or '—'}<br>Commission : {d['commission_date'] or '—'}<br>Décision : {d['decision_date'] or '—'}</td><td>Natif : {clean['raw_words']} → {clean['clean_words']}<br>{clean['removed_blocks']} blocs retirés<br>Retenu : {p['selected_text']['method']} — {p['selected_text']['words']} mots</td><td>{'Oui' if ext['needs_ocr'] else 'Non'}</td><td>{chunk_html}</td><td><a href='metadata/{x['document_id']}.json'>JSON</a> · <a href='clean_text/{x['document_id']}.txt'>Texte</a> · <a href='removed_blocks/{x['document_id']}.json'>Blocs</a> · <a href='chunk_details/{x['document_id']}.html'>Chunks</a></td></tr>")
        row_css="red" if not x["chunks"] or c["red"] else "yellow" if c["yellow"] else "green"
        chunk_rows.append(f"<tr class='{row_css}'><td><a href='chunk_details/{x['document_id']}.html'>{html.escape(x['title'])}</a></td><td>{x['chunks']}</td><td>{c['green']}</td><td>{c['yellow']}</td><td>{c['red']}</td></tr>")
    page=f"""<!doctype html><html lang='fr'><head><meta charset='utf-8'><title>Audit postulats</title><style>{CSS}</style></head><body><h1>Audit complet des postulats 2021–2026</h1><p>Source JSON officielle. Nettoyage non destructif. <a href='chunks_audit.html'><strong>Voir l’audit global des chunks</strong></a>.</p><div class='legend'><span class='tag red'>Rouge : base incomplète</span><span class='tag yellow'>Jaune : metadata postulat incomplète</span><span class='tag orange'>Orange : contrôle requis</span><span class='tag green'>Vert : complet</span></div><table><thead><tr><th>Année</th><th>Document</th><th>Rôle</th><th>État</th><th>Manque base</th><th>Manque spécifique</th><th>Dates</th><th>Nettoyage</th><th>OCR</th><th>Chunks</th><th>Fichiers</th></tr></thead><tbody>{''.join(rows)}</tbody></table><h2>Échecs</h2><ul><li>Aucun</li></ul></body></html>"""
    (ROOT/"audit.html").write_text(page,encoding="utf-8")
    chunk_page_html=f"<!doctype html><html lang='fr'><head><meta charset='utf-8'><title>Chunks postulats</title><style>{CSS}</style></head><body><p><a href='audit.html'>← Audit principal</a></p><h1>Audit structurel des chunks</h1><p>Vert : taille et section correctes. Jaune : composant à vérifier. Rouge : problème structurel ou OCR requis.</p><table><thead><tr><th>Document</th><th>Chunks</th><th>Verts</th><th>Jaunes</th><th>Rouges</th></tr></thead><tbody>{''.join(chunk_rows)}</tbody></table></body></html>"
    (ROOT/"chunks_audit.html").write_text(chunk_page_html,encoding="utf-8")
    print(json.dumps(summary,ensure_ascii=False,indent=2));print(ROOT/"audit.html")


if __name__ == "__main__":
    main()
