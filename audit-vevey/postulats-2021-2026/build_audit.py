from __future__ import annotations

import hashlib
import html
import importlib.util
import json
import re
from collections import Counter
from pathlib import Path

import fitz


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parents[1]
INVENTORY_PATH = ROOT / "inventory.json"
OUTPUT_DIR = ROOT / "general-audit"
METADATA_DIR = OUTPUT_DIR / "metadata"
TEXT_DIR = OUTPUT_DIR / "clean_text"
CHUNKS_DIR = OUTPUT_DIR / "chunks"
DETAIL_DIR = OUTPUT_DIR / "documents"
OCR_DIR = OUTPUT_DIR / "ocr_overrides"
MAX_WORDS = 450
OVERLAP_WORDS = 60

SCRAPER = PROJECT_ROOT / "scrape-vevey" / "scrape_postulats_2021_2026.py"
SPEC = importlib.util.spec_from_file_location("vevey_postulates", SCRAPER)
assert SPEC and SPEC.loader
source = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(source)
OBJECTS = source.OBJECTS


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def compact(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def clean_text(value: str) -> str:
    lines = []
    for raw in value.splitlines():
        line = compact(raw)
        if not line:
            if lines and lines[-1]:
                lines.append("")
            continue
        if re.fullmatch(r"(?:page\s*)?\d+\s*(?:/|sur)\s*\d+", line, re.I):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def extract_document(document: dict) -> tuple[str, dict]:
    pdf_path = PROJECT_ROOT / document["local_pdf"]
    with fitz.open(pdf_path) as pdf:
        all_pages = [page.get_text("text") for page in pdf]
        image_counts = [len(page.get_images(full=True)) for page in pdf]
    selected_pages = document.get("selection_pages")
    if selected_pages:
        start, end = selected_pages
        page_texts = all_pages[start - 1 : end]
    else:
        page_texts = all_pages
    native_text = clean_text("\n\n".join(page_texts))
    ocr_path = OCR_DIR / f"{document['document_id']}.md"
    used_ocr = bool(document["text_audit"]["needs_ocr"])
    if used_ocr:
        if not ocr_path.is_file():
            raise ValueError(f"OCR manquant: {document['document_id']}")
        text = clean_text(ocr_path.read_text(encoding="utf-8"))
    else:
        text = native_text
    if len(text) < 120:
        raise ValueError(f"Texte canonique trop court: {document['document_id']}")
    return text, {
        "page_count": len(all_pages),
        "selected_pages": selected_pages or [1, len(all_pages)],
        "image_counts": image_counts,
        "characters_extracted": len(text),
        "words_extracted": len(re.findall(r"\S+", text)),
        "needs_ocr": False,
        "ocr_applied": used_ocr,
        "extraction_method": "mistral_ocr" if used_ocr else "native_pdf",
    }


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


def component(role: str) -> str:
    return {
        "postulate_text": "postulate_text",
        "municipal_response": "municipal_response",
        "consideration_report": "commission_report",
    }[role]


def relationships(document: dict, documents: list[dict], profile: dict) -> dict:
    linked = [
        row
        for row in documents
        if row["political_object_key"] == document["political_object_key"]
    ]
    original = next(row for row in linked if row["document_role"] == "postulate_text")
    return {
        "political_object_id": profile["object_id"],
        "political_object_document_id": original["document_id"],
        "related_document_ids": [
            row["document_id"]
            for row in linked
            if row["document_id"] != document["document_id"]
        ],
        "response_document_ids": [
            row["document_id"]
            for row in linked
            if row["document_role"] == "municipal_response"
        ],
        "consideration_report_ids": [
            row["document_id"]
            for row in linked
            if row["document_role"] == "consideration_report"
        ],
        "official_reference": document.get("reference", ""),
    }


def metadata_record(document: dict, documents: list[dict], extraction: dict, text: str) -> dict:
    profile = OBJECTS[document["political_object_key"]]
    document_title = (
        profile["object_title"]
        if document["document_role"] == "postulate_text"
        else str(document.get("title") or profile["object_title"])
    )
    general = {
        "document_id": document["document_id"],
        "commune": "Vevey",
        "document_family": "political_object",
        "category": "postulat",
        "document_role": document["document_role"],
        "title": document_title,
        "source_title": str(document.get("title") or ""),
        "source_page_url": document["source_page"],
        "file_url": document["pdf_url"],
        "listing_year": int(document["document_date"][:4]),
        "legislature": "2021-2026",
        "document_date": document["document_date"],
        "language": "fr",
        "content_hash": hashlib.sha256(compact(text).encode("utf-8")).hexdigest(),
        "source_content_hash": document["sha256"],
        "extraction_method": extraction["extraction_method"],
        "processing_status": "validated",
        "canonical": True,
    }
    specific = {
        "type": "postulat",
        "object_id": profile["object_id"],
        "object_title": profile["object_title"],
        "authors": profile["authors"],
        "deposit_date": profile["deposit_date"],
        "status_normalized": profile["status_normalized"],
        "status": profile["status"],
        "response_date": profile.get("response_date"),
        "has_response": profile["has_response"],
        "has_dedicated_response": profile["has_dedicated_response"],
        "response_status": profile["response_status"],
        "is_closed": profile["is_closed"],
        "document_component_role": document["document_role"],
    }
    specific = {key: value for key, value in specific.items() if value is not None}
    return {
        "document_metadata": general,
        "postulate_metadata": specific,
        "relationships": relationships(document, documents, profile),
        "source_tracking": {
            "source_collection": document["source_collection"],
            "source_download_id": document["source_download_id"],
            "official_reference": document.get("reference", ""),
            "listing_occurrences": [
                {
                    "listing_date": document["listing_date"],
                    "listing_author": document.get("listing_author", ""),
                    "displayed_type": document.get("displayed_type", ""),
                    "reference": document.get("reference", ""),
                    "title": str(document.get("title") or ""),
                    "file_url": document["pdf_url"],
                }
            ],
        },
        "processing": {
            "text_extraction_status": extraction,
            "selection_pages": document.get("selection_pages"),
            "ocr": {
                "checked": True,
                "applied": extraction["ocr_applied"],
                "provider": "mistral-ocr-latest" if extraction["ocr_applied"] else None,
            },
        },
    }


def chunks_for(record: dict, text: str) -> list[dict]:
    metadata = record["document_metadata"]
    rows = []
    for index, content in enumerate(split_chunks(text)):
        rows.append(
            {
                "chunk_id": f"{metadata['document_id']}#chunk-{index:03d}",
                "document_id": metadata["document_id"],
                "chunk_index": index,
                "section_index": 0,
                "component": component(metadata["document_role"]),
                "section_title": metadata["title"],
                "content": content,
                "word_count": len(content.split()),
                "chunk_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                "quality": "green",
                "quality_issues": [],
            }
        )
    return rows


CSS = """
body{font:14px/1.5 system-ui;margin:0;background:#f4f6f9;color:#172033}
main{max-width:1200px;margin:auto;padding:24px}a{color:#075db5}
section{background:#fff;border:1px solid #d7dfeb;border-radius:10px;padding:16px;margin:12px 0}
.stats{display:grid;grid-template-columns:repeat(6,1fr);gap:9px}
.stat{background:#eaf1fa;padding:11px;border-radius:8px}.stat strong{font-size:1.5rem;display:block}
table{border-collapse:collapse;width:100%;background:#fff}
th,td{border:1px solid #d7dfeb;padding:8px;text-align:left}th{background:#eaf1fa}
.yes{background:#e4f5e8}.no{background:#fff2da}pre{white-space:pre-wrap;word-break:break-word}
@media(max-width:850px){.stats{grid-template-columns:repeat(2,1fr)}}
"""


def main() -> None:
    inventory = json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))
    documents = inventory["documents"]
    if len(documents) != 44 or len(OBJECTS) != 30:
        raise SystemExit(
            f"Inventaire inattendu: {len(documents)} documents, {len(OBJECTS)} objets"
        )
    for directory in (METADATA_DIR, TEXT_DIR, CHUNKS_DIR, DETAIL_DIR):
        directory.mkdir(parents=True, exist_ok=True)
        for stale in directory.glob("*"):
            if stale.is_file():
                stale.unlink()

    summaries = []
    totals = Counter()
    for document in documents:
        text, extraction = extract_document(document)
        record = metadata_record(document, documents, extraction, text)
        chunks = chunks_for(record, text)
        document_id = document["document_id"]
        write_json(METADATA_DIR / f"{document_id}.json", record)
        write_json(CHUNKS_DIR / f"{document_id}.json", chunks)
        (TEXT_DIR / f"{document_id}.txt").write_text(text + "\n", encoding="utf-8")
        detail = (
            "<!doctype html><html lang='fr'><head><meta charset='utf-8'>"
            f"<title>{html.escape(record['document_metadata']['title'])}</title>"
            f"<style>{CSS}</style></head><body><main>"
            "<p><a href='../index.html'>← Audit des postulats</a></p>"
            f"<h1>{html.escape(record['document_metadata']['title'])}</h1>"
            f"<p><a href='{html.escape(document['pdf_url'])}'>PDF officiel</a></p>"
            f"<section><h2>Métadonnées</h2><pre>{html.escape(json.dumps(record, ensure_ascii=False, indent=2))}</pre></section>"
            f"<section><h2>Texte retenu</h2><pre>{html.escape(text)}</pre></section>"
            f"<section><h2>Chunks</h2><pre>{html.escape(json.dumps(chunks, ensure_ascii=False, indent=2))}</pre></section>"
            "</main></body></html>"
        )
        (DETAIL_DIR / f"{document_id}.html").write_text(detail, encoding="utf-8")
        totals["documents"] += 1
        totals["chunks"] += len(chunks)
        totals["ocr_applied"] += int(extraction["ocr_applied"])
        totals["original_postulates"] += int(document["document_role"] == "postulate_text")
        summaries.append(
            {
                "document_id": document_id,
                "object_id": record["postulate_metadata"]["object_id"],
                "title": record["document_metadata"]["title"],
                "role": document["document_role"],
                "document_date": document["document_date"],
                "file_url": document["pdf_url"],
                "chunks": len(chunks),
                "ocr_applied": extraction["ocr_applied"],
            }
        )

    objects = []
    for key, profile in OBJECTS.items():
        linked = [row for row in summaries if row["object_id"] == profile["object_id"]]
        objects.append(
            {
                "object_key": key,
                "object_id": profile["object_id"],
                "title": profile["object_title"],
                "authors": profile["authors"],
                "deposit_date": profile["deposit_date"],
                "status_normalized": profile["status_normalized"],
                "status": profile["status"],
                "has_response": profile["has_response"],
                "response_status": profile["response_status"],
                "response_date": profile.get("response_date"),
                "documents": linked,
            }
        )
    audit = {
        "schema_version": "vevey-postulates-general-audit-v1",
        "scope": inventory["scope"],
        "source": inventory["source"],
        "summary": {
            **dict(totals),
            "political_objects": len(objects),
            "objects_with_response": sum(row["has_response"] for row in objects),
            "objects_without_response": sum(not row["has_response"] for row in objects),
            "municipal_responses": sum(row["role"] == "municipal_response" for row in summaries),
            "consideration_reports": sum(row["role"] == "consideration_report" for row in summaries),
        },
        "chunking": {
            "max_words": MAX_WORDS,
            "overlap_words": OVERLAP_WORDS,
            "embedding_recipe": "political_object",
            "embedding_model": "mistral-embed",
        },
        "political_objects": objects,
        "documents": summaries,
    }
    write_json(OUTPUT_DIR / "audit.json", audit)
    stats = "".join(
        f"<div class='stat'><strong>{value}</strong>{html.escape(key.replace('_', ' '))}</div>"
        for key, value in audit["summary"].items()
    )
    rows = "".join(
        f"<tr class='{'yes' if row['has_response'] else 'no'}'>"
        f"<td>{html.escape(row['title'])}</td><td>{row['deposit_date']}</td>"
        f"<td>{html.escape(row['status'])}</td>"
        f"<td>{'Oui' if row['has_response'] else 'Non'}</td>"
        f"<td>{len(row['documents'])}</td></tr>"
        for row in objects
    )
    page = (
        "<!doctype html><html lang='fr'><head><meta charset='utf-8'>"
        f"<title>Audit des postulats de Vevey</title><style>{CSS}</style></head><body><main>"
        "<h1>Postulats de Vevey — législature 2021–2026</h1>"
        "<p>Inventaire complet fondé sur les PDF autonomes, les annexes aux procès-verbaux et les rapports officiels liés.</p>"
        f"<section class='stats'>{stats}</section><table><thead><tr>"
        "<th>Postulat</th><th>Date</th><th>Statut</th><th>Réponse</th><th>Documents</th>"
        f"</tr></thead><tbody>{rows}</tbody></table></main></body></html>"
    )
    (OUTPUT_DIR / "index.html").write_text(page, encoding="utf-8")
    print(json.dumps(audit["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
