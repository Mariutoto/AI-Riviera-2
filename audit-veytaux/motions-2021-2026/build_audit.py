from __future__ import annotations

import hashlib
import html
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


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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


def extract_pdf(document: dict) -> tuple[str, dict]:
    pdf_path = PROJECT_ROOT / document["local_pdf"]
    with fitz.open(pdf_path) as pdf:
        pages = [page.get_text("text") for page in pdf]
        images = [len(page.get_images(full=True)) for page in pdf]
    needs_ocr = bool(document["text_audit"]["needs_ocr"])
    if needs_ocr:
        ocr_path = OCR_DIR / f"{document['document_id']}.md"
        if not ocr_path.is_file():
            raise ValueError(f"OCR manquant: {document['document_id']}")
        text = clean_text(ocr_path.read_text(encoding="utf-8"))
        method = "mistral_ocr"
    else:
        text = clean_text("\n\n".join(pages))
        method = "native_pdf"
    if len(text) < 120:
        text = clean_text(
            f"Pièce jointe officielle à la motion : {document['source_title']}\n"
            f"Nom du fichier : {document['title']}\n"
            f"Source : {document['pdf_url']}\n\n{text}"
        )
    return text, {
        "page_count": len(pages),
        "image_counts": images,
        "characters_extracted": len(text),
        "words_extracted": len(text.split()),
        "needs_ocr": needs_ocr,
        "ocr_applied": needs_ocr,
        "extraction_method": method,
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


def role_component(role: str) -> str:
    return {
        "motion_text": "motion_text",
        "municipal_response": "municipal_response",
        "resolution": "resolution",
    }[role]


def metadata_record(spec: dict, objects: dict[str, dict]) -> dict:
    linked = [objects[object_id] for object_id in spec["political_object_ids"]]
    primary = linked[0]
    general = {
        "document_id": spec["document_id"],
        "commune": "Veytaux",
        "document_family": "political_object",
        "category": "motion",
        "document_role": spec["document_role"],
        "title": spec["title"],
        "source_title": spec["source_title"],
        "source_page_url": spec["source_page"],
        "file_url": spec["file_url"],
        "listing_year": int(spec["document_date"][:4]),
        "legislature": "2021-2026",
        "document_date": spec["document_date"],
        "language": "fr",
        "content_hash": hashlib.sha256(compact(spec["text"]).encode("utf-8")).hexdigest(),
        "source_content_hash": spec["source_content_hash"],
        "extraction_method": spec["extraction"]["extraction_method"],
        "processing_status": "validated",
        "canonical": True,
    }
    specific = {
        "type": "motion",
        "object_id": primary["object_id"],
        "object_ids": [item["object_id"] for item in linked],
        "object_title": primary["title"],
        "authors": primary["authors"],
        "deposit_date": primary["deposit_date"],
        "status_normalized": "unknown",
        "status": "",
        "has_response": primary["has_response"],
        "response_status": primary["response_status"],
        "is_closed": primary["is_closed"],
        "has_resolution": primary["has_resolution"],
        "document_component_role": spec["document_role"],
    }
    original_ids = [f"veytaux_motion_{item['object_id'].split('veytaux-motion-', 1)[-1]}_text" for item in linked]
    response_ids = sorted({rid for item in linked for rid in item["response_document_ids"]})
    resolution_ids = sorted({rid for item in linked for rid in item["resolution_document_ids"]})
    related_ids = sorted(set(original_ids + response_ids + resolution_ids) - {spec["document_id"]})
    return {
        "document_metadata": general,
        "motion_metadata": specific,
        "relationships": {
            "political_object_id": primary["object_id"],
            "political_object_ids": [item["object_id"] for item in linked],
            "political_object_document_id": original_ids[0],
            "political_object_document_ids": original_ids,
            "related_document_ids": related_ids,
            "response_document_ids": response_ids,
            "resolution_document_ids": resolution_ids,
        },
        "source_tracking": {
            "source_collection": "Conseil communal de Veytaux",
            "source_kind": spec["source_kind"],
        },
        "processing": {
            "text_extraction_status": spec["extraction"],
            "ocr": {
                "checked": True,
                "applied": spec["extraction"]["ocr_applied"],
                "provider": "mistral-ocr-latest" if spec["extraction"]["ocr_applied"] else None,
            },
        },
    }


def main() -> None:
    inventory = json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))
    if len(inventory["objects"]) != 6:
        raise SystemExit(f"Périmètre inattendu: {len(inventory['objects'])} motions")
    for directory in (METADATA_DIR, TEXT_DIR, CHUNKS_DIR, DETAIL_DIR):
        directory.mkdir(parents=True, exist_ok=True)
        for stale in directory.iterdir():
            if stale.is_file():
                stale.unlink()
    objects = {item["object_id"]: item for item in inventory["objects"]}

    specs = []
    for document in inventory["documents"]:
        text, extraction = extract_pdf(document)
        linked_titles = [objects[object_id]["title"] for object_id in document["political_object_ids"]]
        role_label = {
            "municipal_response": "Réponse municipale",
            "resolution": "Résolution",
            "motion_text": None,
        }[document["document_role"]]
        title = f"{role_label} — {' / '.join(linked_titles)}" if role_label else document["title"]
        specs.append(
            {
                **document,
                "title": title,
                "file_url": document["pdf_url"],
                "source_kind": f"official_{document['document_role']}_pdf",
                "source_content_hash": document["sha256"],
                "text": text,
                "extraction": extraction,
            }
        )

    summaries = []
    total_chunks = 0
    for spec in specs:
        record = metadata_record(spec, objects)
        chunks = []
        for index, content in enumerate(split_chunks(spec["text"])):
            chunks.append(
                {
                    "chunk_id": f"{spec['document_id']}#chunk-{index:03d}",
                    "document_id": spec["document_id"],
                    "chunk_index": index,
                    "section_index": 0,
                    "component": role_component(spec["document_role"]),
                    "section_title": spec["title"],
                    "content": content,
                    "word_count": len(content.split()),
                    "chunk_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    "quality": "green",
                    "quality_issues": [],
                }
            )
        write_json(METADATA_DIR / f"{spec['document_id']}.json", record)
        write_json(CHUNKS_DIR / f"{spec['document_id']}.json", chunks)
        (TEXT_DIR / f"{spec['document_id']}.txt").write_text(spec["text"] + "\n", encoding="utf-8")
        (DETAIL_DIR / f"{spec['document_id']}.html").write_text(
            "<!doctype html><html lang='fr'><meta charset='utf-8'><body>"
            f"<h1>{html.escape(spec['title'])}</h1><p><a href='{html.escape(spec['file_url'])}'>Source officielle</a></p>"
            f"<pre>{html.escape(spec['text'])}</pre></body></html>",
            encoding="utf-8",
        )
        total_chunks += len(chunks)
        summaries.append(
            {
                "document_id": spec["document_id"],
                "political_object_ids": spec["political_object_ids"],
                "title": spec["title"],
                "role": spec["document_role"],
                "document_date": spec["document_date"],
                "file_url": spec["file_url"],
                "chunks": len(chunks),
                "source_kind": spec["source_kind"],
            }
        )
    summary = {
        "political_objects": len(objects),
        "documents": len(specs),
        "original_motions": sum(s["document_role"] == "motion_text" for s in specs),
        "municipal_responses": sum(s["document_role"] == "municipal_response" for s in specs),
        "resolutions": sum(s["document_role"] == "resolution" for s in specs),
        "objects_with_response": sum(item["has_response"] for item in objects.values()),
        "objects_without_response": sum(not item["has_response"] for item in objects.values()),
        "objects_with_resolution": sum(item["has_resolution"] for item in objects.values()),
        "ocr_applied": sum(s["extraction"]["ocr_applied"] for s in specs),
        "chunks": total_chunks,
    }
    audit = {
        "schema_version": "veytaux-motions-general-audit-v1",
        "scope": inventory["scope"],
        "source": inventory["source"],
        "summary": summary,
        "chunking": {"max_words": MAX_WORDS, "overlap_words": OVERLAP_WORDS, "embedding_model": "mistral-embed"},
        "political_objects": list(objects.values()),
        "documents": summaries,
    }
    write_json(OUTPUT_DIR / "audit.json", audit)
    rows = "".join(
        f"<tr><td>{html.escape(item['title'])}</td><td>{item['deposit_date']}</td>"
        f"<td>{'Oui' if item['has_response'] else 'Non'}</td></tr>"
        for item in objects.values()
    )
    (OUTPUT_DIR / "index.html").write_text(
        "<!doctype html><html lang='fr'><meta charset='utf-8'><body>"
        "<h1>Motions de Veytaux — législature 2021–2026</h1>"
        f"<pre>{html.escape(json.dumps(summary, ensure_ascii=False, indent=2))}</pre>"
        f"<table><tr><th>Motion</th><th>Dépôt</th><th>Réponse</th></tr>{rows}</table></body></html>",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
