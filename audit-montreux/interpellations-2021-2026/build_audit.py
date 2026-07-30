from __future__ import annotations

import hashlib
import html
import json
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import date
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
MONTHS = {
    "janvier": 1,
    "fevrier": 2,
    "mars": 3,
    "avril": 4,
    "mai": 5,
    "juin": 6,
    "juillet": 7,
    "aout": 8,
    "septembre": 9,
    "octobre": 10,
    "novembre": 11,
    "decembre": 12,
}


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def compact(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def ascii_text(value: str) -> str:
    return "".join(
        character
        for character in unicodedata.normalize("NFKD", value.casefold())
        if not unicodedata.combining(character)
    )


def normalized_words(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", ascii_text(value)).strip()


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


def parse_french_date(value: str) -> str:
    match = re.search(
        r"\b(\d{1,2})(?:er)?\s+([a-z]+)\s+(20\d{2})\b",
        ascii_text(value),
    )
    if not match or match.group(2) not in MONTHS:
        return ""
    return date(
        int(match.group(3)),
        MONTHS[match.group(2)],
        int(match.group(1)),
    ).isoformat()


def extract_pdf(document: dict) -> tuple[str, dict]:
    pdf_path = PROJECT_ROOT / document["local_pdf"]
    with fitz.open(pdf_path) as pdf:
        page_texts = [page.get_text("text") for page in pdf]
        image_counts = [len(page.get_images(full=True)) for page in pdf]
    native_text = clean_text("\n\n".join(page_texts))
    needs_ocr = len(native_text) < max(300, len(page_texts) * 100)
    ocr_path = OCR_DIR / f"{document['document_id']}.md"
    if needs_ocr:
        if not ocr_path.is_file():
            raise ValueError(
                f"OCR requis mais absent: {document['document_id']}"
            )
        text = clean_text(ocr_path.read_text(encoding="utf-8"))
        method = "mistral_ocr"
    else:
        text = native_text
        method = "native_pdf"
    if len(text) < 120:
        raise ValueError(
            f"Texte canonique trop court: {document['document_id']}"
        )
    return text, {
        "page_count": len(page_texts),
        "page_text_characters": [len(compact(page)) for page in page_texts],
        "image_counts": image_counts,
        "characters_extracted": len(text),
        "words_extracted": len(re.findall(r"\S+", text)),
        "needs_ocr": needs_ocr,
        "ocr_applied": needs_ocr,
        "extraction_method": method,
    }


def adopted_date(text: str, fallback: str) -> str:
    match = re.search(
        r"\bAinsi\s+adopt[ée]e?\s+le\s+"
        r"(\d{1,2}(?:er)?\s+[A-Za-zÀ-ÿ]+\s+20\d{2})\b",
        text,
        re.I,
    )
    return parse_french_date(match.group(1)) if match else fallback


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


def question_count(value: str) -> int:
    numbered = {
        int(match)
        for match in re.findall(
            r"(?:^|\s)(\d{1,2})\s*[\).:-]\s+(?=[A-ZÀ-ÖØ-Þ])",
            value,
        )
        if 0 < int(match) < 30
    }
    if numbered:
        return max(numbered)
    return len(re.findall(r"\?", value))


def response_body(item: dict) -> str:
    parts = []
    for event in item["response_events"]:
        heading = (
            "Réponse à l'interpellation "
            f"« {item['title']} »"
        )
        if event.get("date_text"):
            heading += f" — {event['date_text']}"
        text = clean_text(str(event.get("text") or ""))
        parts.append(f"{heading}\n\n{text}".strip())
    return "\n\n".join(part for part in parts if part).strip()


def object_status_label(status: str) -> str:
    return {
        "written_response": "Réponse écrite disponible",
        "oral_or_transcribed_response": (
            "Réponse orale ou transcrite sur la fiche officielle"
        ),
        "response_confirmed_missing_source": (
            "Réponse confirmée par l'interpellateur, texte source absent"
        ),
        "empty_response_event_unverified": (
            "Rubrique de réponse vide, réponse non vérifiée"
        ),
        "unanswered": "Aucune réponse publiée",
    }[status]


def document_specs(inventory: dict) -> tuple[list[dict], dict[str, dict]]:
    pdfs_by_object: dict[int, list[dict]] = defaultdict(list)
    for response in inventory["response_documents"]:
        pdfs_by_object[
            int(response["political_object_source_id"])
        ].append(response)
    specs = []
    profiles = {}
    for item in inventory["objects"]:
        source_id = int(item["source_object_id"])
        object_document_id = (
            f"montreux_interpellation_{source_id}_text"
        )
        responses = pdfs_by_object.get(source_id, [])
        if not responses and item["has_response"]:
            html_text = response_body(item)
            if len(html_text) < 40:
                raise ValueError(
                    f"Réponse HTML trop courte pour l'objet {source_id}"
                )
            responses = [
                {
                    "document_id": (
                        f"montreux_interpellation_{source_id}_response_html"
                    ),
                    "document_role": "municipal_response",
                    "source_kind": "official_html_transcript",
                    "pdf_url": item["source_page"],
                    "source_page": item["source_page"],
                    "document_date": item["response_date"],
                    "sha256": hashlib.sha256(
                        compact(html_text).encode("utf-8")
                    ).hexdigest(),
                    "text": html_text,
                    "text_audit": {
                        "needs_ocr": False,
                        "text_characters": len(html_text),
                    },
                    "download_id": "",
                    "filename": "",
                    "displayed_type": "Réponse à l'interpellation",
                    "source_download_aliases": [],
                }
            ]
        response_ids = [row["document_id"] for row in responses]
        response_dates = []
        for response in responses:
            if response.get("local_pdf"):
                response_text, extraction = extract_pdf(response)
                response["text"] = response_text
                response["extraction"] = extraction
                response["source_kind"] = "official_response_pdf"
                response["source_page"] = item["source_page"]
                response["document_date"] = adopted_date(
                    response_text,
                    response.get("attachment_date")
                    or item.get("response_date")
                    or item["deposit_date"],
                )
            else:
                response["extraction"] = {
                    "page_count": 0,
                    "characters_extracted": len(response["text"]),
                    "words_extracted": len(
                        re.findall(r"\S+", response["text"])
                    ),
                    "needs_ocr": False,
                    "ocr_applied": False,
                    "extraction_method": "official_html",
                }
            response_dates.append(response["document_date"])
        official_response_date = (
            max(response_dates) if response_dates else ""
        )
        response_entries = (
            [
                {
                    "response_number": None,
                    "response_date": official_response_date,
                    "response_type": (
                        "written_pdf"
                        if any(
                            row["source_kind"]
                            == "official_response_pdf"
                            for row in responses
                        )
                        else item["response_status"]
                    ),
                    "municipal_adoption_date": (
                        official_response_date
                        if any(
                            row["source_kind"]
                            == "official_response_pdf"
                            for row in responses
                        )
                        else None
                    ),
                }
            ]
            if item["has_response"]
            else []
        )
        profile = {
            "object_id": item["object_id"],
            "object_title": item["title"],
            "authors": item["authors"],
            "deposit_date": item["deposit_date"],
            "status_normalized": item["response_status"],
            "status": object_status_label(item["response_status"]),
            "has_response": item["has_response"],
            "has_dedicated_response": bool(
                any(
                    row["source_kind"] == "official_response_pdf"
                    for row in responses
                )
            ),
            "response_status": item["response_status"],
            "response_date": official_response_date or None,
            "responses": response_entries,
            "is_closed": item["is_closed"],
            "question_count": question_count(item["deposit_text"]),
            "contains_questions": "?" in item["deposit_text"],
            "source_object_id": source_id,
            "source_alias_ids": item["source_alias_ids"],
            "source_page": item["source_page"],
            "session_url": item.get("session_url", ""),
            "response_source_quality": (
                "official_pdf"
                if any(
                    row["source_kind"] == "official_response_pdf"
                    for row in responses
                )
                else (
                    "official_html_transcript"
                    if item["has_response"]
                    else "no_verified_response_source"
                )
            ),
        }
        profiles[item["object_id"]] = profile
        specs.append(
            {
                "document_id": object_document_id,
                "object_id": item["object_id"],
                "document_role": "interpellation_text",
                "title": item["title"],
                "source_title": item["title"],
                "source_page": item["source_page"],
                "file_url": item["source_page"],
                "document_date": item["deposit_date"],
                "source_kind": "official_html_deposit",
                "source_content_hash": hashlib.sha256(
                    compact(item["deposit_text"]).encode("utf-8")
                ).hexdigest(),
                "text": clean_text(item["deposit_text"]),
                "extraction": {
                    "page_count": 0,
                    "characters_extracted": len(item["deposit_text"]),
                    "words_extracted": len(
                        re.findall(r"\S+", item["deposit_text"])
                    ),
                    "needs_ocr": False,
                    "ocr_applied": False,
                    "extraction_method": "official_html",
                },
                "political_object_document_id": object_document_id,
                "response_document_ids": response_ids,
                "related_document_ids": response_ids,
                "source_download_id": "",
                "source_download_aliases": [],
            }
        )
        for response in responses:
            specs.append(
                {
                    **response,
                    "object_id": item["object_id"],
                    "title": (
                        f"Réponse à l'interpellation « {item['title']} »"
                    ),
                    "source_title": response.get("filename")
                    or f"Réponse à l'interpellation « {item['title']} »",
                    "file_url": response["pdf_url"],
                    "source_content_hash": response["sha256"],
                    "political_object_document_id": object_document_id,
                    "response_document_ids": [response["document_id"]],
                    "related_document_ids": [object_document_id],
                    "source_download_id": response.get("download_id", ""),
                }
            )
    return specs, profiles


def metadata_record(document: dict, profile: dict) -> dict:
    text = document["text"]
    general = {
        "document_id": document["document_id"],
        "commune": "Montreux",
        "document_family": "political_object",
        "category": "interpellation",
        "document_role": document["document_role"],
        "title": document["title"],
        "source_title": document["source_title"],
        "source_page_url": document["source_page"],
        "file_url": document["file_url"],
        "listing_year": int(document["document_date"][:4]),
        "legislature": "2021-2026",
        "document_date": document["document_date"],
        "language": "fr",
        "content_hash": hashlib.sha256(
            compact(text).encode("utf-8")
        ).hexdigest(),
        "source_content_hash": document["source_content_hash"],
        "extraction_method": document["extraction"][
            "extraction_method"
        ],
        "processing_status": "validated",
        "canonical": True,
    }
    specific = {
        "type": "interpellation",
        "object_id": profile["object_id"],
        "object_title": profile["object_title"],
        "authors": profile["authors"],
        "deposit_date": profile["deposit_date"],
        "interpellation_date": profile["deposit_date"],
        "target_body": "Municipalité",
        "status_normalized": profile["status_normalized"],
        "political_status": (
            "response_available"
            if profile["has_response"]
            else "unanswered_or_unverified"
        ),
        "status": profile["status"],
        "has_response": profile["has_response"],
        "has_dedicated_response": profile["has_dedicated_response"],
        "response_status": profile["response_status"],
        "response_date": profile["response_date"],
        "responses": profile["responses"],
        "is_closed": profile["is_closed"],
        "contains_questions": profile["contains_questions"],
        "question_count": profile["question_count"],
        "document_component_role": document["document_role"],
        "response_source_quality": profile["response_source_quality"],
    }
    return {
        "document_metadata": general,
        "interpellation_metadata": {
            key: value
            for key, value in specific.items()
            if value is not None
        },
        "relationships": {
            "political_object_id": profile["object_id"],
            "political_object_document_id": document[
                "political_object_document_id"
            ],
            "related_document_ids": document["related_document_ids"],
            "response_document_ids": document["response_document_ids"],
            "official_reference": "",
        },
        "source_tracking": {
            "source_collection": "montreux-council-public-search",
            "source_object_id": profile["source_object_id"],
            "source_object_alias_ids": profile["source_alias_ids"],
            "source_download_id": document["source_download_id"],
            "source_download_aliases": document.get(
                "source_download_aliases", []
            ),
            "source_kind": document["source_kind"],
            "session_url": profile["session_url"],
            "listing_occurrences": [
                {
                    "listing_date": document["document_date"],
                    "author": ", ".join(
                        author["name"] for author in profile["authors"]
                    ),
                    "displayed_type": document.get(
                        "displayed_type", ""
                    ),
                    "title": document["source_title"],
                    "file_url": document["file_url"],
                }
            ],
        },
        "processing": {
            "text_extraction_status": document["extraction"],
            "ocr": {
                "checked": True,
                "applied": document["extraction"].get(
                    "ocr_applied", False
                ),
                "provider": (
                    "mistral-ocr-latest"
                    if document["extraction"].get("ocr_applied")
                    else None
                ),
            },
        },
    }


def chunks_for(record: dict, text: str) -> list[dict]:
    metadata = record["document_metadata"]
    component = (
        "interpellation_text"
        if metadata["document_role"] == "interpellation_text"
        else "municipal_response"
    )
    rows = []
    for index, content in enumerate(split_chunks(text)):
        rows.append(
            {
                "chunk_id": (
                    f"{metadata['document_id']}#chunk-{index:03d}"
                ),
                "document_id": metadata["document_id"],
                "chunk_index": index,
                "section_index": 0,
                "component": component,
                "section_title": metadata["title"],
                "content": content,
                "word_count": len(content.split()),
                "chunk_hash": hashlib.sha256(
                    content.encode("utf-8")
                ).hexdigest(),
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
    specs, profiles = document_specs(inventory)
    if len(profiles) != 155:
        raise SystemExit(
            f"155 interpellations attendues, reçu {len(profiles)}"
        )
    for directory in (METADATA_DIR, TEXT_DIR, CHUNKS_DIR, DETAIL_DIR):
        directory.mkdir(parents=True, exist_ok=True)
        for stale in directory.glob("*"):
            if stale.is_file():
                stale.unlink()

    summaries = []
    totals = Counter()
    for document in specs:
        profile = profiles[document["object_id"]]
        record = metadata_record(document, profile)
        chunks = chunks_for(record, document["text"])
        document_id = document["document_id"]
        write_json(METADATA_DIR / f"{document_id}.json", record)
        write_json(CHUNKS_DIR / f"{document_id}.json", chunks)
        (TEXT_DIR / f"{document_id}.txt").write_text(
            document["text"] + "\n", encoding="utf-8"
        )
        detail = (
            "<!doctype html><html lang='fr'><head><meta charset='utf-8'>"
            f"<title>{html.escape(document['title'])}</title>"
            f"<style>{CSS}</style></head><body><main>"
            "<p><a href='../index.html'>← Audit des interpellations</a></p>"
            f"<h1>{html.escape(document['title'])}</h1>"
            f"<p><a href='{html.escape(document['file_url'])}'>Source officielle</a></p>"
            "<section><h2>Métadonnées</h2><pre>"
            f"{html.escape(json.dumps(record, ensure_ascii=False, indent=2))}"
            "</pre></section><section><h2>Texte retenu</h2><pre>"
            f"{html.escape(document['text'])}</pre></section>"
            "<section><h2>Chunks</h2><pre>"
            f"{html.escape(json.dumps(chunks, ensure_ascii=False, indent=2))}"
            "</pre></section></main></body></html>"
        )
        (DETAIL_DIR / f"{document_id}.html").write_text(
            detail, encoding="utf-8"
        )
        totals["documents"] += 1
        totals["chunks"] += len(chunks)
        totals["ocr_applied"] += int(
            document["extraction"].get("ocr_applied", False)
        )
        totals["original_interpellations"] += int(
            document["document_role"] == "interpellation_text"
        )
        totals["response_documents"] += int(
            document["document_role"] == "municipal_response"
        )
        totals["response_pdfs"] += int(
            document["source_kind"] == "official_response_pdf"
        )
        totals["html_response_documents"] += int(
            document["source_kind"] == "official_html_transcript"
        )
        summaries.append(
            {
                "document_id": document_id,
                "object_id": profile["object_id"],
                "title": document["title"],
                "role": document["document_role"],
                "document_date": document["document_date"],
                "file_url": document["file_url"],
                "chunks": len(chunks),
                "source_kind": document["source_kind"],
            }
        )

    objects = []
    for profile in sorted(
        profiles.values(), key=lambda row: row["deposit_date"]
    ):
        linked = [
            row
            for row in summaries
            if row["object_id"] == profile["object_id"]
        ]
        objects.append(
            {
                "object_id": profile["object_id"],
                "source_object_id": profile["source_object_id"],
                "title": profile["object_title"],
                "authors": profile["authors"],
                "deposit_date": profile["deposit_date"],
                "status_normalized": profile["status_normalized"],
                "status": profile["status"],
                "has_response": profile["has_response"],
                "response_status": profile["response_status"],
                "response_date": profile["response_date"],
                "documents": linked,
            }
        )
    audit = {
        "schema_version": "montreux-interpellations-general-audit-v1",
        "scope": inventory["scope"],
        "source": inventory["source"],
        "summary": {
            **dict(totals),
            "political_objects": len(objects),
            "objects_with_response": sum(
                row["has_response"] for row in objects
            ),
            "objects_without_verified_response": sum(
                not row["has_response"] for row in objects
            ),
            "status_counts": dict(
                Counter(
                    row["status_normalized"] for row in objects
                )
            ),
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
        f"<div class='stat'><strong>{value}</strong>"
        f"{html.escape(key.replace('_', ' '))}</div>"
        for key, value in audit["summary"].items()
        if isinstance(value, int)
    )
    rows = "".join(
        f"<tr class='{'yes' if row['has_response'] else 'no'}'>"
        f"<td>{html.escape(row['title'])}</td>"
        f"<td>{row['deposit_date']}</td>"
        f"<td>{html.escape(row['status'])}</td>"
        f"<td>{'Oui' if row['has_response'] else 'Non'}</td>"
        f"<td>{len(row['documents'])}</td></tr>"
        for row in objects
    )
    page = (
        "<!doctype html><html lang='fr'><head><meta charset='utf-8'>"
        f"<title>Audit des interpellations de Montreux</title><style>{CSS}</style>"
        "</head><body><main><h1>Interpellations de Montreux — "
        "législature 2021–2026</h1>"
        "<p>Fiches HTML officielles, réponses transcrites et PDF municipaux "
        "dédoublonnés par empreinte.</p>"
        f"<section class='stats'>{stats}</section><table><thead><tr>"
        "<th>Interpellation</th><th>Dépôt</th><th>Statut</th>"
        "<th>Réponse</th><th>Documents</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></main></body></html>"
    )
    (OUTPUT_DIR / "index.html").write_text(page, encoding="utf-8")
    print(json.dumps(audit["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
