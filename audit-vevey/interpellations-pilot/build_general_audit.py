from __future__ import annotations

import hashlib
import html
import json
import re
import unicodedata
from collections import Counter
from pathlib import Path

import fitz


ROOT = Path(__file__).resolve().parent
INVENTORY_PATH = ROOT / "inventory.json"
PDF_DIR = ROOT / "pdfs"
OUTPUT_DIR = ROOT / "general-audit"
METADATA_DIR = OUTPUT_DIR / "metadata"
TEXT_DIR = OUTPUT_DIR / "clean_text"
CHUNKS_DIR = OUTPUT_DIR / "chunks"
DETAIL_DIR = OUTPUT_DIR / "documents"
OCR_DIR = ROOT / "ocr_overrides"
MAX_WORDS = 450
OVERLAP_WORDS = 60


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def compact(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def word_count(text: str) -> int:
    return len(re.findall(r"\S+", text))


def clean_text(text: str) -> str:
    lines = []
    for raw_line in text.splitlines():
        line = compact(raw_line)
        if not line:
            if lines and lines[-1]:
                lines.append("")
            continue
        if re.fullmatch(r"(?:page\s*)?\d+\s*(?:/|sur)\s*\d+", line, re.I):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def extract_pdf(path: Path) -> dict:
    with fitz.open(path) as pdf:
        page_texts = [page.get_text("text") for page in pdf]
        page_images = [len(page.get_images(full=True)) for page in pdf]
    text = "\n\n".join(page_texts)
    cleaned = clean_text(text)
    page_chars = [len(value.strip()) for value in page_texts]
    empty_pages = sum(value == 0 for value in page_chars)
    sparse_image_pages = sum(
        chars < 80 and images > 0
        for chars, images in zip(page_chars, page_images)
    )
    needs_ocr = (
        len(cleaned) < max(200, len(page_texts) * 50)
        or sparse_image_pages >= max(1, (len(page_texts) + 4) // 5)
    )
    return {
        "text": cleaned,
        "page_texts": [clean_text(value) for value in page_texts],
        "page_count": len(page_texts),
        "page_text_chars": page_chars,
        "empty_pages": empty_pages,
        "image_counts": page_images,
        "text_chars": len(cleaned),
        "text_words": word_count(cleaned),
        "needs_ocr": needs_ocr,
        "extraction_method": "native_pdf" if not needs_ocr else "ocr_required",
        "text_hash": hashlib.sha256(cleaned.encode("utf-8")).hexdigest(),
    }


def split_chunks(text: str) -> list[str]:
    tokens = re.findall(r"\S+", text)
    chunks = []
    start = 0
    while start < len(tokens):
        end = min(len(tokens), start + MAX_WORDS)
        chunks.append(" ".join(tokens[start:end]))
        if end == len(tokens):
            break
        start = end - OVERLAP_WORDS
    return chunks


def political_reference(document: dict) -> str:
    return str(document.get("reference") or "").strip()


def is_interpellation_object(document: dict) -> bool:
    return document.get("document_role") == "political_object"


def subject_key(value: str) -> str | None:
    quoted = re.findall(r"[«\"]([^»\"]{8,})[»\"]", value)
    if len(quoted) != 1:
        return None
    folded = "".join(
        character
        for character in unicodedata.normalize("NFKD", quoted[0].casefold())
        if not unicodedata.combining(character)
    )
    return re.sub(r"[^a-z0-9]+", " ", folded).strip() or None


def appended_interpellation_page(page: str) -> bool:
    compact_page = compact(page)
    head = compact_page[:500]
    normalized_head = "".join(
        character
        for character in unicodedata.normalize("NFKD", head.casefold())
        if not unicodedata.combining(character)
    )
    normalized_head = re.sub(r"[^a-z0-9]+", " ", normalized_head).strip()
    heading = re.search(r"\binterpellation\b", head, re.I)
    return bool(
        heading
        and heading.start() < 220
        and not re.search(
            r"\breponses? (?:a|au|aux) l? ?interpellations?\b",
            normalized_head,
        )
    )


def select_interpellation_text(
    document: dict,
    extraction: dict,
    duplicate_of: str | None = None,
) -> tuple[str, str]:
    if is_interpellation_object(document):
        return extraction["text"], "whole_document"
    if duplicate_of:
        return "", "duplicate_of_standalone"
    page_texts = extraction.get("page_texts") or []
    for index, page in enumerate(page_texts):
        if appended_interpellation_page(page):
            return "\n\n".join(page_texts[index:]).strip(), "appended_interpellation"
    if not is_interpellation_object(document) and extraction.get("empty_pages"):
        return "", "appended_interpellation_scanned"
    return "", "not_found"


def general_metadata(
    document: dict,
    extraction: dict,
    selected_text: str,
    component_source: str,
) -> dict:
    included = bool(selected_text) and (
        not extraction["needs_ocr"]
        or component_source == "targeted_mistral_ocr"
    )
    combined = (
        not is_interpellation_object(document)
        and (
            component_source.startswith("appended_interpellation")
            or component_source == "targeted_mistral_ocr"
        )
    )
    component_needs_ocr = component_source == "appended_interpellation_scanned"
    return {
        "document_id": document["document_id"],
        "commune": "Vevey",
        "document_family": "political_object",
        "category": "interpellation",
        "document_role": (
            "interpellation_text"
            if is_interpellation_object(document)
            else "combined_interpellation_response"
            if combined
            else "municipal_response"
        ),
        "title": document.get("title", ""),
        "source_title": document.get("title", ""),
        "source_page_url": document.get("source_page", ""),
        "file_url": document.get("pdf_url", ""),
        "listing_year": int(document.get("listing_year") or 0),
        "legislature": document.get("legislature", ""),
        "document_date": document.get("listing_date", ""),
        "content_hash": (
            hashlib.sha256(
                compact(selected_text).encode("utf-8")
            ).hexdigest()
            if selected_text
            else None
        ),
        "extraction_method": (
            "mistral_ocr"
            if component_source == "targeted_mistral_ocr"
            else "ocr_required"
            if component_needs_ocr
            else extraction["extraction_method"]
        ),
        "processing_status": (
            "validated"
            if included
            else "excluded_duplicate"
            if component_source == "duplicate_of_standalone"
            else "needs_review"
        ),
    }


def interpellation_metadata(document: dict) -> dict:
    reference = political_reference(document)
    return {
        "authors": (
            [{"name": document["author"]}]
            if document.get("author")
            else []
        ),
        "political_status": (
            "response_available"
            if not is_interpellation_object(document)
            else "filed"
        ),
        "interpellation_date": document.get("listing_date") or None,
        "responses": (
            [{
                "response_number": reference or None,
                "response_date": document.get("listing_date") or None,
                "response_type": "municipal_response",
                "municipal_adoption_date": None,
            }]
            if not is_interpellation_object(document)
            else []
        ),
    }


def relationships(document: dict) -> dict:
    reference = political_reference(document)
    return {
        "political_object_id": (
            document["document_id"] if is_interpellation_object(document) else None
        ),
        "response_document_ids": [],
        "response_status": "not_collected_yet",
        "response_source_collection": "vevey-council-annexes",
        "response_candidate_in_same_pdf": (
            document["document_id"]
            if not is_interpellation_object(document)
            else None
        ),
        "matching_keys": {
            "official_reference": reference or None,
            "normalized_title": compact(
                re.sub(r"\.pdf$", "", document.get("title", ""), flags=re.I)
            ).casefold(),
            "author": document.get("author") or None,
            "year": document.get("listing_year"),
        },
    }


def embedding_input(base: dict, content: str) -> str:
    return (
        f"Famille: {base['document_family']}\n"
        f"Catégorie: {base['category']}\n"
        f"Rôle: {base['document_role']}\n"
        f"Titre: {base['title']}\n"
        "Section: Interpellation\n\n"
        f"{content}"
    )


def build_chunks(base: dict, text: str) -> list[dict]:
    if base["processing_status"] != "validated":
        return []
    chunks = []
    for index, content in enumerate(split_chunks(text)):
        chunk_id = f"{base['document_id']}#chunk-{index:03d}"
        issues = []
        count = word_count(content)
        if count > MAX_WORDS:
            issues.append("chunk_too_long")
        if count < 60 and index < len(split_chunks(text)) - 1:
            issues.append("chunk_too_short")
        chunks.append(
            {
                "chunk_id": chunk_id,
                "document_id": base["document_id"],
                "chunk_index": index,
                "section_index": 0,
                "component": "interpellation_text",
                "section_title": "Interpellation",
                "response_number": None,
                "content": content,
                "word_count": count,
                "chunk_hash": hashlib.sha256(
                    content.encode("utf-8")
                ).hexdigest(),
                "embedding_input": embedding_input(base, content),
                "quality": "yellow" if issues else "green",
                "quality_issues": issues,
            }
        )
    return chunks


def document_page(record: dict, extraction: dict, chunks: list[dict]) -> str:
    base = record["document_metadata"]
    specific = record["interpellation_metadata"]
    chunk_cards = "".join(
        f"""<article class="chunk"><h3>{html.escape(chunk["chunk_id"])}</h3>
        <p>{chunk["word_count"]} mots · {html.escape(chunk["quality"])}</p>
        <details><summary>Contenu du chunk</summary><pre>{html.escape(chunk["content"])}</pre></details>
        <details><summary>Entrée envoyée au modèle d'embedding</summary><pre>{html.escape(chunk["embedding_input"])}</pre></details>
        </article>"""
        for chunk in chunks
    )
    if not chunks:
        chunk_cards = (
            '<p class="notice">Aucun embedding prévu pour ce document : '
            f'{html.escape(base["processing_status"])}.</p>'
        )
    return f"""<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(base["title"])}</title><style>{CSS}</style></head><body><main>
<p><a href="../index.html">← Audit général</a></p>
<h1>{html.escape(base["title"])}</h1>
<p><a href="{html.escape(base["file_url"])}">Ouvrir le PDF officiel</a></p>
<section><h2>Métadonnées générales</h2><pre>{html.escape(json.dumps(base, ensure_ascii=False, indent=2))}</pre></section>
<section><h2>Métadonnées d'interpellation</h2><pre>{html.escape(json.dumps(specific, ensure_ascii=False, indent=2))}</pre></section>
<section><h2>Traitement</h2><pre>{html.escape(json.dumps(record["processing"], ensure_ascii=False, indent=2))}</pre></section>
<section><h2>Relations avec les futures réponses</h2><pre>{html.escape(json.dumps(record["relationships"], ensure_ascii=False, indent=2))}</pre></section>
<section><h2>Contrôle PDF et OCR</h2><pre>{html.escape(json.dumps({key: value for key, value in extraction.items() if key not in {"text", "page_texts"}}, ensure_ascii=False, indent=2))}</pre></section>
<section><h2>Texte extrait et nettoyé</h2><pre>{html.escape(extraction["text"])}</pre></section>
<section><h2>Chunks et embeddings</h2>{chunk_cards}</section>
</main></body></html>"""


CSS = """
body{font:15px/1.5 system-ui;margin:0;background:#f4f6f9;color:#172033}
main{max-width:1180px;margin:auto;padding:26px}a{color:#075db5}
section,.hero,.chunk{background:#fff;border:1px solid #d8e0eb;border-radius:12px;padding:18px;margin:14px 0}
.stats{display:grid;grid-template-columns:repeat(5,1fr);gap:10px}
.stat{background:#eaf1fa;padding:12px;border-radius:9px}.stat strong{display:block;font-size:1.7rem}
table{border-collapse:collapse;width:100%;background:#fff}th,td{border:1px solid #d8e0eb;padding:8px;text-align:left}
th{background:#eaf1fa;position:sticky;top:0}.ready{background:#e9f7ed}.review{background:#fff4cc}.excluded{background:#ffe8e8}
pre{white-space:pre-wrap;word-break:break-word;background:#f7f9fc;padding:12px;max-height:620px;overflow:auto}
summary{cursor:pointer;font-weight:650}.notice{padding:12px;background:#fff4cc;border-radius:8px}
@media(max-width:800px){.stats{grid-template-columns:repeat(2,1fr)}}
"""


def main() -> None:
    inventory = json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))
    documents = inventory.get("canonical_documents") or []
    if not documents:
        raise SystemExit("inventory.json ne contient aucun document canonique")
    standalone_by_subject = {
        key: document["document_id"]
        for document in documents
        if is_interpellation_object(document)
        for key in [subject_key(str(document.get("title") or ""))]
        if key
    }
    for directory in (METADATA_DIR, TEXT_DIR, CHUNKS_DIR, DETAIL_DIR):
        directory.mkdir(parents=True, exist_ok=True)

    summaries = []
    totals = Counter()
    missing_pdfs = []
    for document in documents:
        document_id = document["document_id"]
        pdf_path = PDF_DIR / f"{document_id}.pdf"
        if not pdf_path.exists():
            missing_pdfs.append(str(pdf_path))
            continue
        extraction = extract_pdf(pdf_path)
        duplicate_of = None
        if not is_interpellation_object(document):
            duplicate_of = standalone_by_subject.get(
                subject_key(str(document.get("title") or "")) or ""
            )
        selected_text, component_source = select_interpellation_text(
            document, extraction, duplicate_of
        )
        ocr_override = OCR_DIR / f"{document_id}.md"
        if ocr_override.exists() and (
            is_interpellation_object(document)
            or component_source == "appended_interpellation_scanned"
        ):
            selected_text = ocr_override.read_text(encoding="utf-8").strip()
            component_source = "targeted_mistral_ocr"
        base = general_metadata(
            document, extraction, selected_text, component_source
        )
        specific = interpellation_metadata(document)
        component_needs_ocr = (
            component_source == "appended_interpellation_scanned"
        )
        processing = {
            "text_extraction_status": {
                "characters_extracted": len(selected_text),
                "text_available": bool(selected_text),
                "needs_ocr": extraction["needs_ocr"] or component_needs_ocr,
            },
            "header_footer_cleaning": {
                "raw_words": extraction["text_words"],
                "clean_words": word_count(selected_text),
                "removed_blocks": 0,
            },
            "selected_text": {
                "method": base["extraction_method"],
                "words": word_count(selected_text),
            },
        }
        record = {
            "document_metadata": base,
            "interpellation_metadata": specific,
            "processing": processing,
            "relationships": relationships(document),
            "pdf_audit": {
                key: value
                for key, value in extraction.items()
                if key not in {"text", "page_texts"}
            },
        }
        if duplicate_of:
            record["relationships"]["duplicate_of_document_id"] = duplicate_of
        chunks = build_chunks(base, selected_text)
        write_json(METADATA_DIR / f"{document_id}.json", record)
        write_json(CHUNKS_DIR / f"{document_id}.json", chunks)
        (TEXT_DIR / f"{document_id}.txt").write_text(
            selected_text + "\n", encoding="utf-8"
        )
        (DETAIL_DIR / f"{document_id}.html").write_text(
            document_page(record, extraction, chunks), encoding="utf-8"
        )
        status = (
            "ready"
            if base["processing_status"] == "validated"
            else "review"
        )
        totals[status] += 1
        totals["combined_response_candidates"] += int(
            base["document_role"] == "combined_interpellation_response"
        )
        totals["chunks"] += len(chunks)
        totals["ocr"] += int(
            processing["text_extraction_status"]["needs_ocr"]
        )
        totals["missing_authors"] += int(
            not specific["authors"]
        )
        summaries.append(
            {
                "document_id": document_id,
                "title": base["title"],
                "year": base["listing_year"],
                "role": base["document_role"],
                "author": ", ".join(
                    item["name"]
                    for item in specific["authors"]
                ),
                "pages": extraction["page_count"],
                "words": extraction["text_words"],
                "needs_ocr": (
                    processing["text_extraction_status"]["needs_ocr"]
                ),
                "chunks": len(chunks),
                "status": status,
            }
        )

    if missing_pdfs:
        raise SystemExit(
            "PDF manquants. Relancer le scraper avec --download-dir: "
            + ", ".join(missing_pdfs[:3])
        )

    rows = "".join(
        f"""<tr class="{item["status"]}"><td><a href="documents/{item["document_id"]}.html">{html.escape(item["title"])}</a></td>
        <td>{item["year"]}</td><td>{html.escape(item["role"])}</td><td>{html.escape(item["author"] or "À enrichir")}</td>
        <td>{item["pages"]}</td><td>{item["words"]}</td><td>{"Oui" if item["needs_ocr"] else "Non"}</td>
        <td>{item["chunks"]}</td><td>{html.escape(item["status"])}</td></tr>"""
        for item in summaries
    )
    page = f"""<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Audit général - Interpellations Vevey</title><style>{CSS}</style></head><body><main>
<section class="hero"><h1>Audit général - Interpellations de Vevey 2025-2026</h1>
<p>Cet audit prépare uniquement les interpellations. Les réponses municipales seront collectées depuis les Annexes, auditées séparément puis reliées par référence, titre, auteur et année.</p>
<div class="stats"><div class="stat"><strong>{len(summaries)}</strong>PDF uniques</div>
<div class="stat"><strong>{totals["ready"]}</strong>interpellations prêtes</div>
<div class="stat"><strong>{totals["combined_response_candidates"]}</strong>réponse à relier</div>
<div class="stat"><strong>{totals["chunks"]}</strong>chunks prévus</div>
<div class="stat"><strong>{totals["ocr"]}</strong>OCR requis</div></div>
<h2>Règle d'indexation</h2><p>La structure et la recette sont identiques à La Tour-de-Peilz. Le vecteur est calculé à partir de la famille, la catégorie, le rôle, le titre, le composant et le texte du chunk. Les URL, auteurs, dates, hashes, diagnostics OCR et relations restent dans les métadonnées, mais ne sont pas envoyés au modèle d'embedding.</p>
<p><strong>{totals["missing_authors"]}</strong> document(s) ont encore un auteur à enrichir depuis leur PDF.</p></section>
<table><thead><tr><th>Document</th><th>Année</th><th>Rôle</th><th>Auteur</th><th>Pages</th><th>Mots</th><th>OCR</th><th>Chunks</th><th>Statut</th></tr></thead><tbody>{rows}</tbody></table>
</main></body></html>"""
    (OUTPUT_DIR / "index.html").write_text(page, encoding="utf-8")
    write_json(
        OUTPUT_DIR / "audit.json",
        {
            "schema_version": "vevey-interpellations-general-audit-v1",
            "scope": inventory["scope"],
            "summary": dict(totals),
            "chunking": {
                "max_words": MAX_WORDS,
                "overlap_words": OVERLAP_WORDS,
                "embedding_recipe": "political-object-v1",
            },
            "response_linking_plan": {
                "source_collection": "vevey-council-annexes",
                "response_role": "municipal_response",
                "matching_priority": [
                    "official_reference",
                    "normalized_title",
                    "author_and_year",
                    "manual_review",
                ],
                "response_chunks_embedded_separately": True,
            },
            "documents": summaries,
        },
    )
    print(
        json.dumps(
            {"documents": len(summaries), **dict(totals)},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
