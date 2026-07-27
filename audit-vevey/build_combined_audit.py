from __future__ import annotations

import hashlib
import html
import json
import re
from collections import Counter
from pathlib import Path

import fitz


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
OBJECT_AUDIT = ROOT / "interpellations-pilot" / "general-audit"
LINK_AUDIT = ROOT / "interpellation-response-links"
ANNEX_PDFS = ROOT / "annexes-pilot" / "pdfs"
OUTPUT = ROOT / "combined-interpellations-audit"
RESPONSE_METADATA = OUTPUT / "metadata" / "responses"
RESPONSE_CHUNKS = OUTPUT / "chunks" / "responses"
RESPONSE_TEXT = OUTPUT / "clean_text" / "responses"
RESPONSE_PAGES = OUTPUT / "documents" / "responses"
MAX_WORDS = 450
OVERLAP_WORDS = 60


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def word_count(value: str) -> int:
    return len(re.findall(r"\S+", value))


def clean_text(value: str) -> str:
    lines = []
    for raw_line in value.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if re.fullmatch(r"(?:page\s*)?\d+\s*(?:/|sur)\s*\d+", line, re.I):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def extract_pdf(path: Path) -> tuple[str, dict]:
    with fitz.open(path) as pdf:
        page_texts = [page.get_text("text") for page in pdf]
    text = clean_text("\n\n".join(page_texts))
    stats = {
        "page_count": len(page_texts),
        "page_text_characters": [
            len(page.strip()) for page in page_texts
        ],
        "empty_pages": sum(not page.strip() for page in page_texts),
        "characters": len(text),
        "words": word_count(text),
        "needs_ocr": len(text) < max(200, len(page_texts) * 50),
    }
    return text, stats


def split_chunks(value: str) -> list[str]:
    words = re.findall(r"\S+", value)
    chunks = []
    start = 0
    while start < len(words):
        end = min(len(words), start + MAX_WORDS)
        chunks.append(" ".join(words[start:end]))
        if end == len(words):
            break
        start = end - OVERLAP_WORDS
    return chunks


def embedding_input(base: dict, section: str, content: str) -> str:
    return (
        f"Famille: {base['document_family']}\n"
        f"Catégorie: {base['category']}\n"
        f"Rôle: {base['document_role']}\n"
        f"Titre: {base['title']}\n"
        f"Section: {section}\n\n"
        f"{content}"
    )


def build_response_chunks(record: dict, text: str) -> list[dict]:
    base = record["document_metadata"]
    if base["processing_status"] != "validated":
        return []
    response = record["interpellation_metadata"]["responses"][0]
    number = response.get("response_number")
    section = f"Réponse municipale {number}" if number else "Réponse municipale"
    chunks = []
    for index, content in enumerate(split_chunks(text)):
        chunks.append({
            "chunk_id": f"{base['document_id']}#chunk-{index:03d}",
            "document_id": base["document_id"],
            "chunk_index": index,
            "section_index": 0,
            "component": "municipal_response",
            "section_title": section,
            "response_number": number,
            "content": content,
            "word_count": word_count(content),
            "chunk_hash": hashlib.sha256(
                content.encode("utf-8")
            ).hexdigest(),
            "embedding_input": embedding_input(base, section, content),
            "quality": "green",
            "quality_issues": [],
        })
    return chunks


CSS = """
body{font:15px/1.5 system-ui;margin:0;background:#f4f6f9;color:#172033}
main{max-width:1200px;margin:auto;padding:24px}a{color:#075db5}
section,.card{background:white;border:1px solid #d7dfeb;border-radius:11px;padding:16px;margin:12px 0}
.stats{display:grid;grid-template-columns:repeat(6,1fr);gap:9px}.stat{background:#eaf1fa;padding:11px;border-radius:8px}
.stat strong{font-size:1.65rem;display:block}table{border-collapse:collapse;width:100%;background:white}
th,td{border:1px solid #d7dfeb;padding:8px;text-align:left}th{background:#eaf1fa}
.ready{background:#e6f6e9}.review{background:#ffe8e8}pre{white-space:pre-wrap;word-break:break-word;background:#f7f9fc;padding:11px;max-height:600px;overflow:auto}
summary{cursor:pointer;font-weight:650}@media(max-width:850px){.stats{grid-template-columns:repeat(2,1fr)}}
"""


def response_page(record: dict, text: str, stats: dict, chunks: list[dict]) -> str:
    base = record["document_metadata"]
    chunk_html = "".join(
        f"<article class='card'><h3>{html.escape(chunk['chunk_id'])}</h3>"
        f"<p>{chunk['word_count']} mots</p>"
        f"<details><summary>Contenu</summary><pre>{html.escape(chunk['content'])}</pre></details>"
        f"<details><summary>Entrée embedding</summary><pre>{html.escape(chunk['embedding_input'])}</pre></details></article>"
        for chunk in chunks
    ) or "<p>Aucun chunk : rapprochement à vérifier.</p>"
    return f"""<!doctype html><html lang="fr"><head><meta charset="utf-8">
<title>{html.escape(base["title"])}</title><style>{CSS}</style></head><body><main>
<p><a href="../../index.html">← Audit combiné</a></p><h1>{html.escape(base["title"])}</h1>
<p><a href="{html.escape(base["file_url"])}">PDF officiel</a></p>
<section><h2>Métadonnées générales</h2><pre>{html.escape(json.dumps(base, ensure_ascii=False, indent=2))}</pre></section>
<section><h2>Métadonnées d'interpellation</h2><pre>{html.escape(json.dumps(record["interpellation_metadata"], ensure_ascii=False, indent=2))}</pre></section>
<section><h2>Relation</h2><pre>{html.escape(json.dumps(record["relationships"], ensure_ascii=False, indent=2))}</pre></section>
<section><h2>Extraction</h2><pre>{html.escape(json.dumps(stats, ensure_ascii=False, indent=2))}</pre></section>
<section><h2>Texte nettoyé</h2><pre>{html.escape(text)}</pre></section>
<section><h2>Chunks</h2>{chunk_html}</section></main></body></html>"""


def main() -> None:
    for directory in (
        RESPONSE_METADATA,
        RESPONSE_CHUNKS,
        RESPONSE_TEXT,
        RESPONSE_PAGES,
    ):
        directory.mkdir(parents=True, exist_ok=True)
        for stale_path in directory.iterdir():
            if stale_path.is_file():
                stale_path.unlink()

    links = json.loads((LINK_AUDIT / "links.json").read_text(encoding="utf-8"))
    links_by_response = {
        item["response_document_id"]: item for item in links
    }
    response_summaries = []
    totals = Counter()
    for path in sorted((LINK_AUDIT / "metadata").glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        base = record["document_metadata"]
        base["title"] = str(base.get("title") or "").strip()
        document_id = base["document_id"]
        pdf_path = ANNEX_PDFS / f"{document_id}.pdf"
        text, stats = extract_pdf(pdf_path)
        link = links_by_response[document_id]
        validated = (
            not stats["needs_ocr"]
            and link["matching_confidence"] in {"exact", "probable"}
        )
        base["content_hash"] = hashlib.sha256(
            re.sub(r"\s+", " ", text).strip().encode("utf-8")
        ).hexdigest()
        base["extraction_method"] = (
            "native_pdf" if not stats["needs_ocr"] else "ocr_required"
        )
        base["processing_status"] = (
            "validated" if validated else "needs_review"
        )
        record["processing"] = {
            "text_extraction_status": {
                "characters_extracted": len(text),
                "text_available": bool(text),
                "needs_ocr": stats["needs_ocr"],
            },
            "header_footer_cleaning": {
                "raw_words": stats["words"],
                "clean_words": word_count(text),
                "removed_blocks": 0,
            },
            "selected_text": {
                "method": base["extraction_method"],
                "words": word_count(text),
            },
        }
        chunks = build_response_chunks(record, text)
        write_json(RESPONSE_METADATA / f"{document_id}.json", record)
        write_json(RESPONSE_CHUNKS / f"{document_id}.json", chunks)
        (RESPONSE_TEXT / f"{document_id}.txt").write_text(
            text + "\n", encoding="utf-8"
        )
        (RESPONSE_PAGES / f"{document_id}.html").write_text(
            response_page(record, text, stats, chunks),
            encoding="utf-8",
        )
        status = "ready" if validated else "review"
        totals[status] += 1
        totals["response_chunks"] += len(chunks)
        response_summaries.append({
            "document_id": document_id,
            "reference": (
                record["interpellation_metadata"]["responses"][0]
                .get("response_number")
            ),
            "title": base["title"],
            "object_id": link.get("political_object_id"),
            "confidence": link["matching_confidence"],
            "score": link["score"],
            "chunks": len(chunks),
            "status": status,
        })

    object_chunk_files = list((OBJECT_AUDIT / "chunks").glob("*.json"))
    object_chunks = sum(
        len(json.loads(path.read_text(encoding="utf-8")))
        for path in object_chunk_files
    )
    rows = "".join(
        f"<tr class='{item['status']}'><td>{html.escape(str(item['reference'] or ''))}</td>"
        f"<td><a href='documents/responses/{item['document_id']}.html'>{html.escape(item['title'])}</a></td>"
        f"<td><code>{html.escape(str(item['object_id'] or 'Non lié'))}</code></td>"
        f"<td>{item['confidence']}</td><td>{item['score']:.2f}</td><td>{item['chunks']}</td></tr>"
        for item in response_summaries
    )
    summary = {
        "interpellation_documents": len(object_chunk_files),
        "interpellation_chunks": object_chunks,
        "response_documents": len(response_summaries),
        "validated_responses": totals["ready"],
        "responses_needing_review": totals["review"],
        "response_chunks": totals["response_chunks"],
        "embedding_chunks_total": object_chunks + totals["response_chunks"],
        "ocr_required": 0,
    }
    write_json(OUTPUT / "audit.json", {
        "schema_version": "vevey-combined-interpellations-audit-v1",
        "summary": summary,
        "sources": {
            "interpellation_chunks": str(
                (OBJECT_AUDIT / "chunks").relative_to(PROJECT_ROOT)
            ).replace("\\", "/") + "/*.json",
            "interpellation_metadata": str(
                (LINK_AUDIT / "political_objects").relative_to(PROJECT_ROOT)
            ).replace("\\", "/") + "/*.json",
            "response_chunks": str(
                RESPONSE_CHUNKS.relative_to(PROJECT_ROOT)
            ).replace("\\", "/") + "/*.json",
            "response_metadata": str(
                RESPONSE_METADATA.relative_to(PROJECT_ROOT)
            ).replace("\\", "/") + "/*.json",
        },
        "responses": response_summaries,
    })
    stat_cards = "".join(
        f"<div class='stat'><strong>{value}</strong>{html.escape(key.replace('_', ' '))}</div>"
        for key, value in summary.items()
    )
    page = f"""<!doctype html><html lang="fr"><head><meta charset="utf-8">
<title>Audit combiné Vevey</title><style>{CSS}</style></head><body><main>
<h1>Audit combiné - Interpellations et réponses de Vevey</h1>
<p>Les interpellations et réponses utilisent le même contrat que La Tour-de-Peilz. Les quatre réponses ambiguës restent hors embeddings.</p>
<section class="stats">{stat_cards}</section>
<table><thead><tr><th>Référence</th><th>Réponse</th><th>Interpellation liée</th><th>Confiance</th><th>Score</th><th>Chunks</th></tr></thead>
<tbody>{rows}</tbody></table></main></body></html>"""
    (OUTPUT / "index.html").write_text(page, encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
