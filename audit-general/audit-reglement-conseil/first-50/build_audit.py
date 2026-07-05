from __future__ import annotations

import hashlib
import html
import json
import re
import shutil
import sys
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
FULL_AUDIT = "--all" in sys.argv
HERE = Path(__file__).resolve().parent.parent / "full-audit" if FULL_AUDIT else Path(__file__).resolve().parent
SCOPE_LABEL = "tous les articles" if FULL_AUDIT else "50 premiers articles"
SOURCE_DIR = ROOT / "documents" / "la-tour-de-peilz" / "institutionnel" / "conseil-communal"
ARTICLE_DIR = ROOT / "documents" / "la-tour-de-peilz" / "institutionnel" / "reglements" / "reglement-conseil-communal"
DETAIL_DIR = HERE / "chunk_details"
CHUNK_DIR = HERE / "chunks"
TEXT_DIR = HERE / "clean_text"
METADATA_DIR = HERE / "metadata"
REMOVED_DIR = HERE / "removed_blocks"
DOCUMENT_DETAIL_DIR = HERE / "document_details"

CSS = """body{font:14px/1.45 system-ui;margin:24px;color:#172033}a{color:#185a9d}.legend{display:flex;gap:12px;flex-wrap:wrap;margin:14px 0}.tag{padding:8px 12px;border-radius:7px}table{border-collapse:collapse;width:100%}th,td{border:1px solid #d9dfeb;padding:8px;vertical-align:top}th{background:#edf1f7;position:sticky;top:0}.green{background:#e1f5e6}.yellow{background:#fff3bf}.red{background:#ffd8d8}pre{white-space:pre-wrap;word-break:break-word;background:#f5f7fa;padding:12px;max-height:650px;overflow:auto}article{border:2px solid #ccd5e2;border-radius:10px;padding:14px;margin:14px 0}code{font-family:Consolas,monospace}summary{cursor:pointer;font-weight:650}"""


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def words(text: str) -> int:
    return len(re.findall(r"\S+", text))


def normalized_heading(value: str | None) -> bool:
    return bool(value and " - " in value and len(value.split(" - ", 1)[1].strip()) > 2)


def make_base(source: dict, pdf_bytes: bytes) -> dict:
    digest = hashlib.sha256(pdf_bytes).hexdigest()
    return {
        "document_id": f"doc_{digest[:20]}",
        "commune": "La Tour-de-Peilz",
        "document_family": "regulation",
        "category": "reglement_conseil_communal",
        "document_role": "regulation_text",
        "title": "Règlement du Conseil communal",
        "source_title": "Règlement du Conseil communal de La Tour-de-Peilz 2017",
        "source_page_url": source["source_page"],
        "file_url": source["pdf_url"],
        "document_date": "2017-10-25",
        "content_hash": digest,
        "extraction_method": "native_pdf",
        "processing_status": "audit_generated",
    }


def audit_article(article: dict, document_id: str, index: int) -> dict:
    text = article.get("article_text", "").strip()
    chunk_metadata = {
        "chunk_type": "regulation_article",
        "article_number": article.get("article_number"),
        "article_title": re.sub(r"\s+", " ", article.get("article_title") or "").strip() or None,
        "title_heading": article.get("title_heading"),
        "chapter": article.get("chapter"),
    }
    issues = []
    if not text:
        issues.append("article_text_missing")
    if not chunk_metadata["article_number"]:
        issues.append("article_number_missing")
    if not chunk_metadata["article_title"]:
        issues.append("article_title_missing")
    if not normalized_heading(chunk_metadata["title_heading"]):
        issues.append("title_heading_incomplete")
    if not normalized_heading(chunk_metadata["chapter"]):
        issues.append("chapter_incomplete")
    count = words(text)
    if count and count < 8:
        issues.append("article_very_short")
    if count > 450:
        issues.append("article_over_450_words")
    contamination = []
    if re.search(r"(?im)^\s*page\s+\d+\s*$", text):
        contamination.append("page_label")
    if re.search(r"(?im)^\s*table des mati[èe]res\s*$", text):
        contamination.append("table_of_contents")
    issues.extend(f"text_contamination:{item}" for item in contamination)
    fatal = {"article_text_missing", "article_number_missing", "article_title_missing"}
    quality = "red" if fatal.intersection(issues) else "yellow" if issues else "green"
    context = [
        "document_family: regulation",
        f"article_number: {chunk_metadata['article_number']}",
        f"article_title: {chunk_metadata['article_title']}",
    ]
    return {
        "chunk_id": f"{document_id}#chunk-{index:03d}",
        "chunk_index": index,
        "chunk_metadata": chunk_metadata,
        "content": text,
        "embedding_input": "\n".join(context) + "\n\n" + text,
        "word_count": count,
        "quality": quality,
        "quality_issues": issues,
    }


def chunks_detail_page(base: dict, chunks: list[dict]) -> str:
    cards = []
    for chunk in chunks:
        meta = chunk["chunk_metadata"]
        cards.append(
            f"<article id='chunk-{chunk['chunk_index']}' class='{chunk['quality']}'>"
            f"<h2>Chunk #{chunk['chunk_index']} · Art. {html.escape(str(meta['article_number']))} — {html.escape(str(meta['article_title']))}</h2>"
            f"<p><b>{chunk['word_count']} mots</b> · Alertes : {html.escape(', '.join(chunk['quality_issues']) or 'aucune')}</p>"
            f"<details open><summary>Métadonnées du chunk</summary><pre>{html.escape(json.dumps(meta, ensure_ascii=False, indent=2))}</pre></details>"
            f"<details><summary>Contenu complet</summary><pre>{html.escape(chunk['content'])}</pre></details>"
            f"<details><summary>Entrée embedding</summary><pre>{html.escape(chunk['embedding_input'])}</pre></details></article>"
        )
    return f"""<!doctype html><html lang='fr'><head><meta charset='utf-8'><title>Articles — règlement</title><style>{CSS}</style></head><body>
<p><a href='../audit.html'>← Audit principal</a> · <a href='../chunks_audit.html'>Vue globale des articles</a></p>
<h1>{html.escape(base['title'])} — {SCOPE_LABEL}</h1>{''.join(cards)}</body></html>"""


def main() -> None:
    for directory in (DETAIL_DIR, CHUNK_DIR, TEXT_DIR, METADATA_DIR, REMOVED_DIR, DOCUMENT_DETAIL_DIR):
        if directory.exists():
            shutil.rmtree(directory)
        directory.mkdir(parents=True)
    source = read_json(SOURCE_DIR / "reglement-conseil-communal.json")
    pdf_bytes = (SOURCE_DIR / "reglement-conseil-communal.pdf").read_bytes()
    base = make_base(source, pdf_bytes)
    articles = [read_json(path) for path in ARTICLE_DIR.glob("art-*.json")]
    articles.sort(key=lambda value: (float(value.get("sort_key", 9999)), value.get("article_id", "")))
    selected = articles if FULL_AUDIT else articles[:50]
    chunks = [audit_article(article, base["document_id"], index) for index, article in enumerate(selected)]

    seen = Counter(chunk["chunk_metadata"]["article_number"] for chunk in chunks)
    duplicates = sorted(number for number, count in seen.items() if count > 1)
    for chunk in chunks:
        if chunk["chunk_metadata"]["article_number"] in duplicates:
            chunk["quality_issues"].append("duplicate_article_number")
            if chunk["quality"] == "green":
                chunk["quality"] = "yellow"
    summary = {
        "documents": 1,
        "complete": 1,
        "base_incomplete": 0,
        "additional_incomplete": 0,
        "needs_ocr": 0,
        "chunks": len(chunks),
        "green": sum(item["quality"] == "green" for item in chunks),
        "yellow": sum(item["quality"] == "yellow" for item in chunks),
        "red": sum(item["quality"] == "red" for item in chunks),
        "duplicate_article_numbers": duplicates,
        "issues": dict(Counter(issue for item in chunks for issue in item["quality_issues"])),
        "missing_fields": dict(Counter(
            field
            for item in chunks
            for field, value in item["chunk_metadata"].items()
            if value in (None, "", [], {})
        )),
    }
    raw_text = (SOURCE_DIR / "reglement-conseil-communal.txt").read_text(encoding="utf-8")
    selected_text = "\n\n".join(
        f"Article {item['chunk_metadata']['article_number']} — {item['chunk_metadata']['article_title']}\n{item['content']}"
        for item in chunks
    )
    processing = {
        "text_extraction_status": {"characters_extracted": len(raw_text), "text_available": True, "needs_ocr": False},
        "header_footer_cleaning": {"raw_words": words(raw_text), "clean_words": words(selected_text), "removed_blocks": 0},
        "selected_text": {"method": "native_pdf", "words": words(selected_text)},
    }
    record = {"document_metadata": base, "processing": processing}
    base_required = {
        "document_id", "commune", "document_family", "category", "document_role", "title",
        "source_title", "source_page_url", "file_url", "document_date", "content_hash",
        "extraction_method", "processing_status",
    }
    base_missing = sorted(field for field in base_required if base.get(field) in (None, "", [], {}))
    chunk_required = {"chunk_type", "article_number", "article_title", "title_heading", "chapter"}
    additional_missing = sorted({
        field for item in chunks for field in chunk_required if item["chunk_metadata"].get(field) in (None, "", [], {})
    })
    audit_entry = {
        "document_id": base["document_id"], "title": base["title"], "role": base["document_role"],
        "audit": {"base_missing": base_missing, "additional_missing": additional_missing, "warnings": []},
        "processing": processing, "chunks": len(chunks),
        "chunk_counts": {color: summary[color] for color in ("green", "yellow", "red")},
    }
    write_json(HERE / "audit.json", {"documents": [audit_entry], "failures": [], "summary": summary})
    write_json(HERE / "chunks_summary.json", [{"document_id": base["document_id"], "title": base["title"], "chunks": len(chunks), **audit_entry["chunk_counts"]}])
    write_json(METADATA_DIR / f"{base['document_id']}.json", record)
    write_json(CHUNK_DIR / f"{base['document_id']}.json", chunks)
    write_json(REMOVED_DIR / f"{base['document_id']}.json", [])
    (TEXT_DIR / f"{base['document_id']}.txt").write_text(selected_text + "\n", encoding="utf-8")
    (DETAIL_DIR / f"{base['document_id']}.html").write_text(chunks_detail_page(base, chunks), encoding="utf-8")
    document_detail = f"""<!doctype html><html lang='fr'><head><meta charset='utf-8'><title>{html.escape(base['title'])}</title><style>{CSS}</style></head><body><p><a href='../audit.html'>← Audit principal</a> · <a href='../chunk_details/{base['document_id']}.html'>Articles</a></p><h1>{html.escape(base['title'])}</h1><h2>Métadonnées finales</h2><pre>{html.escape(json.dumps(record, ensure_ascii=False, indent=2))}</pre></body></html>"""
    (DOCUMENT_DETAIL_DIR / f"{base['document_id']}.html").write_text(document_detail, encoding="utf-8")

    rows = []
    for chunk in chunks:
        meta = chunk["chunk_metadata"]
        rows.append(
            f"<tr class='{chunk['quality']}'><td>{chunk['chunk_index'] + 1}</td>"
            f"<td><a href='chunk_details/{base['document_id']}.html#chunk-{chunk['chunk_index']}'>Art. {html.escape(str(meta['article_number']))}</a></td>"
            f"<td>{html.escape(str(meta['article_title']))}</td><td>{html.escape(str(meta['title_heading']))}</td>"
            f"<td>{html.escape(str(meta['chapter']))}</td><td>{chunk['word_count']}</td>"
            f"<td>{html.escape(', '.join(chunk['quality_issues']) or 'aucune')}</td></tr>"
        )
    chunk_verdict = "À vérifier" if summary["yellow"] else "Bon structurellement"
    report = f"""<!doctype html><html lang='fr'><head><meta charset='utf-8'><title>Audit règlement</title><style>{CSS}</style></head><body>
<h1>Audit général — Règlement du Conseil communal, {SCOPE_LABEL}</h1><p>Extraction native et métadonnées minimales. <a href='chunks_audit.html'><strong>Voir l’audit des articles</strong></a> · <a href='embeddings.html'><strong>Voir les embeddings</strong></a> · <a href='../../NOTES_EMBEDDINGS.html'><strong>Notes sur la stratégie</strong></a>.</p>
<div class='legend'><span class='tag red'>Rouge : base incomplète</span><span class='tag yellow'>Jaune : contrôle requis</span><span class='tag green'>Vert : complet</span></div>
<table><thead><tr><th>Document</th><th>Rôle</th><th>État</th><th>Manque base</th><th>Manque spécifique</th><th>Date</th><th>Nettoyage</th><th>OCR</th><th>Articles</th><th>Fichiers</th></tr></thead><tbody>
<tr class='complete'><td><a href='{html.escape(base['file_url'])}' target='_blank'>{html.escape(base['title'])}</a></td><td>{base['document_role']}</td><td><strong>{'Complet' if not base_missing and not additional_missing else 'Incomplet'}</strong></td><td>{html.escape(', '.join(base_missing) or 'Aucun')}</td><td>{html.escape(', '.join(additional_missing) or 'Aucun')}</td><td>{base['document_date']}</td><td>Natif : {processing['header_footer_cleaning']['raw_words']} mots<br>Retenu : {processing['selected_text']['words']} mots</td><td>Non</td><td><div class='yellow'><strong>{chunk_verdict}</strong><br>{len(chunks)} articles : {summary['green']} verts, {summary['yellow']} jaunes, {summary['red']} rouges</div></td><td><a href='metadata/{base['document_id']}.json'>JSON</a> · <a href='clean_text/{base['document_id']}.txt'>Texte</a> · <a href='removed_blocks/{base['document_id']}.json'>Blocs</a> · <a href='chunk_details/{base['document_id']}.html'>Articles</a></td></tr>
</tbody></table><h2>Échecs</h2><ul><li>Aucun</li></ul></body></html>"""
    (HERE / "audit.html").write_text(report, encoding="utf-8")
    chunks_audit = f"""<!doctype html><html lang='fr'><head><meta charset='utf-8'><title>Articles du règlement</title><style>{CSS}</style></head><body><p><a href='audit.html'>← Audit principal</a></p><h1>Audit structurel des articles</h1><p>Cliquer sur chaque article pour consulter son texte et ses cinq métadonnées.</p><table><thead><tr><th>Document</th><th>Articles</th><th>Verts</th><th>Jaunes</th><th>Rouges</th></tr></thead><tbody><tr class='yellow'><td><a href='chunk_details/{base['document_id']}.html'>{html.escape(base['title'])}</a></td><td>{len(chunks)}</td><td>{summary['green']}</td><td>{summary['yellow']}</td><td>{summary['red']}</td></tr></tbody></table><h2>Accès direct aux articles</h2><table><thead><tr><th>#</th><th>Article</th><th>Titre</th><th>Titre hiérarchique</th><th>Chapitre</th><th>Mots</th><th>Alertes</th></tr></thead><tbody>{''.join(rows)}</tbody></table></body></html>"""
    (HERE / "chunks_audit.html").write_text(chunks_audit, encoding="utf-8")
    embedding_cards = "".join(
        f"<article><h2>Chunk #{item['chunk_index']} · Article {html.escape(str(item['chunk_metadata']['article_number']))}</h2>"
        f"<p><a href='chunk_details/{base['document_id']}.html#chunk-{item['chunk_index']}'>Voir le chunk</a></p>"
        f"<pre>{html.escape(item['embedding_input'])}</pre></article>" for item in chunks
    )
    embeddings_page = f"""<!doctype html><html lang='fr'><head><meta charset='utf-8'><title>Embeddings règlement</title><style>{CSS}</style></head><body><p><a href='audit.html'>← Audit principal</a> · <a href='../../NOTES_EMBEDDINGS.html'>Notes sur la stratégie</a></p><h1>Entrées embedding — {SCOPE_LABEL}</h1><p>Recette validée : <code>document_family + article_number + article_title + contenu</code>.</p>{embedding_cards}</body></html>"""
    (HERE / "embeddings.html").write_text(embeddings_page, encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
