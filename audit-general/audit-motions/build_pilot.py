from __future__ import annotations

import hashlib
import html
import json
import math
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

import fitz
import requests


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parents[1]
PILOT = ROOT / "pilot"
SCRAPER_DIR = PROJECT_ROOT / "scrape-la-tour-de-peilz"
sys.path.insert(0, str(SCRAPER_DIR))

import scrape_motions_2021_2026 as scraper


BASE_DIR = PILOT / "document_metadata"
ADDITIONAL_DIR = PILOT / "scraper_metadata"
COMBINED_DIR = PILOT / "combined_metadata_view"
ARTIFACTS_DIR = PILOT / "artifacts"
CLEANING_DIR = PILOT / "cleaning_test"
SELECTED_TEXT_DIR = PILOT / "selected_text"
OCR_DIR = ROOT / "full-audit" / "ocr_overrides"
OVERRIDES_PATH = ROOT / "manual_overrides.json"

FIELD_MAPPINGS = {
    "commune": "commune",
    "category": "category",
    "document_role": "document_role",
    "title": "object_title",
    "source_title": "site_listing_title",
    "source_page_url": "source_page",
    "file_url": "pdf_url",
    "listing_year": "listing_year",
    "legislature": "legislature",
    "document_date": "document_date",
}

USEFUL_MOTION_FIELDS = {
    "authors",
    "status_normalized",
    "report_type",
    "commission",
    "decision",
    "contains_majority_report",
    "contains_minority_report",
}
RELATIONSHIP_FIELDS = {"political_object_id", "political_object", "document_components"}
PROCESSING_FIELDS = {"metadata_version", "text_extraction_status"}
SOURCE_SNAPSHOT_FIELDS = {
    "site_status_raw",
    "site_subject",
    "source_collection",
    "canonical_object",
    "filename",
}
REMOVABLE_DERIVED_FIELDS = {
    "type",
    "document_type",
    "year",
    "summary",
    "title",
    "status",
    "content_kind",
    "search_facets",
    "contains_report",
    "contains_decision",
}


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalized_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def normalize_for_comparison(value):
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        value = value.strip().casefold()
        if value in {"motion", "motions"}:
            return "motion"
    return value


def download(item: dict, target: Path) -> None:
    if target.exists() and target.stat().st_size:
        return
    response = requests.get(item["pdf_url"], headers=scraper.HEADERS, timeout=120)
    response.raise_for_status()
    if not response.content.startswith(b"%PDF"):
        raise ValueError(f"La ressource n'est pas un PDF: {item['pdf_url']}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(response.content)


def extract_native(pdf_path: Path) -> tuple[str, int]:
    with fitz.open(pdf_path) as pdf:
        pages = [scraper.clean_pdf_text(page.get_text("text")) for page in pdf]
        return "\n".join(pages).strip(), len(pdf)


def canonical_record(item: dict, enriched: dict, text: str) -> dict:
    title = enriched.get("object_title") or item.get("summary") or item["site_listing_title"]
    return {
        "document_id": "doc_" + sha256(item["pdf_url"])[:20],
        "commune": "La Tour-de-Peilz",
        "document_family": "political_object",
        "category": "motion",
        "document_role": enriched["document_role"],
        "title": title,
        "source_title": item["site_listing_title"],
        "source_page_url": item["source_page"],
        "file_url": item["pdf_url"],
        "listing_year": int(item["listing_year"]),
        "legislature": item.get("legislature") or "2021-2026",
        "document_date": enriched.get("document_date"),
        "content_hash": sha256(normalized_text(text)),
        "extraction_method": "native_pdf" if text.strip() else "native_pdf_empty",
        "processing_status": "pilot_generated" if text.strip() else "needs_ocr",
    }


def classify_fields(base: dict, additional: dict) -> tuple[list[dict], dict]:
    comparisons = []
    mapped = set(FIELD_MAPPINGS.values())
    for base_field, additional_field in FIELD_MAPPINGS.items():
        left = base.get(base_field)
        right = additional.get(additional_field)
        if additional_field not in additional:
            status = "absent"
        elif normalize_for_comparison(left) == normalize_for_comparison(right):
            status = "doublon"
        else:
            status = "contradictoire_ou_semantique_differente"
        comparisons.append(
            {
                "base_field": base_field,
                "additional_field": additional_field,
                "base_value": left,
                "additional_value": right,
                "classification": status,
            }
        )
    remaining = set(additional) - mapped
    classification = {
        "utile_metadata_motion": sorted(remaining & USEFUL_MOTION_FIELDS),
        "utile_relations_structurees": sorted(remaining & RELATIONSHIP_FIELDS),
        "utile_traitement_hors_metadata": sorted(remaining & PROCESSING_FIELDS),
        "utile_snapshot_source": sorted(remaining & SOURCE_SNAPSHOT_FIELDS),
        "supprimable_ou_recalculable": sorted(remaining & REMOVABLE_DERIVED_FIELDS),
        "a_revoir": sorted(
            remaining
            - USEFUL_MOTION_FIELDS
            - RELATIONSHIP_FIELDS
            - PROCESSING_FIELDS
            - SOURCE_SNAPSHOT_FIELDS
            - REMOVABLE_DERIVED_FIELDS
        ),
    }
    return comparisons, classification


def block_normalize(value: str) -> str:
    value = "".join(
        char for char in unicodedata.normalize("NFD", value.casefold())
        if unicodedata.category(char) != "Mn"
    )
    value = re.sub(r"\d+", "#", value)
    return re.sub(r"\s+", " ", value).strip(" -|_")


def word_count(value: str) -> int:
    return len(re.findall(r"\b\w+[\w'-]*\b", value, flags=re.UNICODE))


def clean_pdf(record: dict, pdf_path: Path) -> dict:
    pages = []
    with fitz.open(pdf_path) as pdf:
        for page_index, page in enumerate(pdf):
            height = page.rect.height
            blocks = []
            for raw in page.get_text("blocks"):
                x0, y0, x1, y1, text, *_ = raw
                text = re.sub(r"[ \t]+", " ", text).strip()
                if text:
                    blocks.append(
                        {
                            "page": page_index + 1,
                            "x0": round(x0, 1), "y0": round(y0, 1),
                            "x1": round(x1, 1), "y1": round(y1, 1),
                            "page_height": round(height, 1),
                            "text": text,
                            "normalized": block_normalize(text),
                        }
                    )
            pages.append(blocks)

    position_candidates = [
        block["normalized"]
        for blocks in pages
        for block in blocks
        if block["y1"] <= block["page_height"] * 0.14
        or block["y0"] >= block["page_height"] * 0.86
    ]
    counts = Counter(value for value in position_candidates if value)
    threshold = max(2, math.ceil(len(pages) * 0.5))
    repeated = {value for value, count in counts.items() if count >= threshold}
    removed = []
    kept_pages = []
    for blocks in pages:
        kept = []
        for block in blocks:
            text = block["text"]
            reason = None
            if block["normalized"] in repeated:
                reason = "repeated_header_or_footer"
            elif re.fullmatch(r"\s*(?:page\s*)?\d+\s*(?:/|sur|\|)\s*\d+\s*", text, flags=re.I):
                reason = "page_number"
            elif (
                re.fullmatch(r"[^\s/\\:]{1,140}\.(?:docx?|pdf)", text.strip(), flags=re.I)
            ):
                reason = "internal_filename"
            elif (
                re.search(r"\b\d{2,3}\s+\d{3}\s+\d{2}\s+\d{2}\b|\b[^\s@]+@[^\s@]+\.[^\s@]+", text, flags=re.I)
                and (block["y1"] <= block["page_height"] * 0.2 or block["y0"] >= block["page_height"] * 0.8)
            ):
                reason = "contact_boilerplate"
            if reason:
                removed.append({k: v for k, v in block.items() if k != "normalized"} | {"reason": reason})
            else:
                kept.append(text)
        kept_pages.append("\n".join(kept).strip())
    raw = "\n\n".join("\n".join(block["text"] for block in blocks) for blocks in pages).strip()
    clean = "\n\n".join(page for page in kept_pages if page).strip()
    return {
        "document_id": record["document_id"],
        "raw_words": word_count(raw),
        "clean_words": word_count(clean),
        "removed_word_count": word_count(raw) - word_count(clean),
        "removed_blocks_count": len(removed),
        "removed_blocks": removed,
        "clean_text": clean,
    }


def build_html(records: list[dict], audit: list[dict], cleaning: list[dict]) -> None:
    by_id = {item["document_id"]: item for item in audit}
    cleaning_by_id = {item["document_id"]: item for item in cleaning}
    rows = []
    for record in records:
        item = by_id[record["document_id"]]
        clean = cleaning_by_id[record["document_id"]]
        counts = Counter(x["classification"] for x in item["comparisons"])
        rows.append(
            "<tr>"
            f"<td>{record['listing_year']}</td>"
            f"<td>{html.escape(record['document_role'])}</td>"
            f"<td>{html.escape(str(record['title']))}</td>"
            f"<td>{counts['doublon']}</td>"
            f"<td>{counts['contradictoire_ou_semantique_differente']}</td>"
            f"<td>{clean['removed_blocks_count']} blocs / {clean['removed_word_count']} mots</td>"
            f"<td><a href='document_metadata/{record['document_id']}.json'>base</a> · "
            f"<a href='scraper_metadata/{record['document_id']}.json'>additionnel</a></td>"
            "</tr>"
        )
    schema = html.escape(json.dumps(minimal_schema(), ensure_ascii=False, indent=2))
    page = f"""<!doctype html><html lang='fr'><head><meta charset='utf-8'><title>Pilote motions</title>
<style>body{{font:14px/1.5 system-ui;margin:28px;color:#172033}}.note{{background:#eef5ff;border-left:4px solid #2477d4;padding:13px}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #d9dfeb;padding:8px;vertical-align:top}}th{{background:#edf1f7}}pre{{background:#f7f9fc;padding:12px;white-space:pre-wrap}}</style></head><body>
<h1>Pilote des motions — 12 documents</h1><p class='note'>Aucune donnée de production n'a été modifiée. Le nettoyage est uniquement une simulation avec conservation de chaque bloc retiré.</p>
<h2>Schéma minimal proposé</h2><pre>{schema}</pre>
<table><thead><tr><th>Année</th><th>Rôle</th><th>Titre</th><th>Doublons</th><th>Conflits</th><th>Nettoyage testé</th><th>JSON</th></tr></thead><tbody>{''.join(rows)}</tbody></table></body></html>"""
    (PILOT / "review.html").write_text(page, encoding="utf-8")


def minimal_schema() -> dict:
    return {
        "document_metadata": {
            "document_id": "doc_…",
            "commune": "La Tour-de-Peilz",
            "document_family": "political_object",
            "category": "motion",
            "document_role": "motion_text | combined_motion_report_decision | …",
            "title": "…",
            "source_title": "…",
            "source_page_url": "…",
            "file_url": "…",
            "listing_year": 2024,
            "legislature": "2021-2026",
            "document_date": "YYYY-MM-DD | null",
            "content_hash": "sha256",
            "extraction_method": "native_pdf",
            "processing_status": "validated",
        },
        "motion_metadata": {
            "authors": [{"name": "…", "party": "…"}],
            "political_status": "…",
            "report_type": "… | null",
            "contains_majority_report": False,
            "contains_minority_report": False,
            "decision_date": "YYYY-MM-DD | null",
            "commission": "objet optionnel si rapport",
            "decision": "objet optionnel si décision",
        },
        "processing": {
            "text_extraction_status": {
                "characters_extracted": 0,
                "text_available": True,
                "needs_ocr": False,
            },
            "header_footer_cleaning": {
                "raw_words": 0,
                "clean_words": 0,
                "removed_blocks": 0,
            },
            "selected_text": {"method": "native_pdf", "words": 0},
        },
    }


def combined_view(record: dict, additional: dict, cleaning: dict, selected_text: str, selected_method: str, manual_review: dict | None = None) -> dict:
    decision = additional.get("decision") or {}
    motion_metadata = {
        "authors": additional.get("authors", []),
        "political_status": additional.get("status_normalized"),
        "report_type": additional.get("report_type"),
        "contains_majority_report": bool(additional.get("contains_majority_report")),
        "contains_minority_report": bool(additional.get("contains_minority_report")),
        "decision_date": decision.get("decision_date"),
    }
    if additional.get("commission"):
        motion_metadata["commission"] = additional["commission"]
    if decision:
        motion_metadata["decision"] = decision

    extraction = additional.get("text_extraction_status") or {}
    skip_ocr = bool(manual_review and manual_review.get("reason") == "non_text_image_ignored")
    if skip_ocr:
        extraction = dict(extraction)
        extraction["needs_ocr"] = False
    selected_method = "ignored_non_text_image" if skip_ocr else selected_method
    processing = {
        "text_extraction_status": extraction,
        "header_footer_cleaning": {
            "raw_words": cleaning["raw_words"],
            "clean_words": cleaning["clean_words"],
            "removed_blocks": cleaning["removed_blocks_count"],
        },
            "selected_text": {
                "method": selected_method,
                "words": word_count(selected_text),
        },
    }
    if manual_review:
        processing["manual_review"] = manual_review
    return {
        "document_metadata": record,
        "motion_metadata": motion_metadata,
        "processing": processing,
    }


def apply_manual_override(filename: str, metadata: dict, overrides: dict) -> tuple[dict, dict | None]:
    override = overrides.get(filename)
    if not override:
        return metadata, None
    corrected = dict(metadata)
    for field in (
        "document_role", "report_type", "contains_report", "contains_decision",
        "contains_majority_report", "contains_minority_report", "status_normalized", "document_date",
    ):
        if field in override:
            corrected[field] = override[field]
    for field in override.get("remove_fields", []):
        corrected.pop(field, None)
    corrected["document_components"] = scraper.extract_document_components(
        "", corrected["document_role"], corrected.get("report_type")
    )
    if isinstance(corrected.get("political_object"), dict):
        corrected["political_object"] = dict(corrected["political_object"])
        corrected["political_object"]["status_normalized"] = corrected.get("status_normalized")
        corrected["political_object"].pop("decision_status", None)
    return corrected, {"status": "corrected", "reason": override["reason"]}


def main() -> None:
    items = scraper.collect_items()
    overrides = json.loads(OVERRIDES_PATH.read_text(encoding="utf-8")) if OVERRIDES_PATH.exists() else {}
    if len(items) != 12:
        print(f"Attention: la source contient maintenant {len(items)} motions (12 lors de la conception du pilote).")
    records = []
    diagnostics = []
    audits = []
    cleaning_report = []
    field_presence = Counter()
    comparison_counts = Counter()

    for index, item in enumerate(items, 1):
        print(f"[{index}/{len(items)}] {item['filename']}")
        artifact = ARTIFACTS_DIR / Path(item["filename"]).stem
        pdf_path = artifact / "document.pdf"
        download(item, pdf_path)
        native_text, pages = extract_native(pdf_path)
        artifact.mkdir(parents=True, exist_ok=True)
        (artifact / "native.txt").write_text(native_text + "\n", encoding="utf-8")
        ocr_path = OCR_DIR / f"{Path(item['filename']).stem}.md"
        selected_text = ocr_path.read_text(encoding="utf-8").strip() if ocr_path.exists() else native_text
        if ocr_path.exists():
            selected_text = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", selected_text).strip()
        selected_method = "mistral_ocr" if ocr_path.exists() else "native_pdf" if native_text.strip() else "native_pdf_empty"
        additional = scraper.enrich_motion_metadata(item, selected_text)
        additional, manual_review = apply_manual_override(item["filename"], additional, overrides)
        record = canonical_record(item, additional, selected_text)
        record["extraction_method"] = selected_method
        if manual_review and manual_review.get("reason") == "non_text_image_ignored":
            record["extraction_method"] = "ignored_non_text_image"
            record["processing_status"] = "validated"
        write_json(BASE_DIR / f"{record['document_id']}.json", record)
        write_json(ADDITIONAL_DIR / f"{record['document_id']}.json", additional)
        comparisons, classification = classify_fields(record, additional)
        comparison_counts.update(x["classification"] for x in comparisons)
        field_presence.update(additional.keys())
        audits.append(
            {
                "document_id": record["document_id"],
                "title": record["title"],
                "comparisons": comparisons,
                "additional_fields": classification,
            }
        )
        clean = clean_pdf(record, pdf_path)
        clean_dir = CLEANING_DIR / record["document_id"]
        clean_dir.mkdir(parents=True, exist_ok=True)
        (clean_dir / "clean.txt").write_text(clean.pop("clean_text") + "\n", encoding="utf-8")
        write_json(clean_dir / "removed_blocks.json", clean["removed_blocks"])
        cleaning_report.append(clean)
        SELECTED_TEXT_DIR.mkdir(parents=True, exist_ok=True)
        (SELECTED_TEXT_DIR / f"{record['document_id']}.txt").write_text(selected_text + "\n", encoding="utf-8")
        write_json(COMBINED_DIR / f"{record['document_id']}.json", combined_view(record, additional, clean, selected_text, selected_method, manual_review))
        records.append(record)
        diagnostics.append(
            {
                "document_id": record["document_id"],
                "filename": item["filename"],
                "pages": pages,
                "words": word_count(selected_text),
                "document_role": record["document_role"],
            }
        )

    write_json(PILOT / "manifest.json", {"schema_version": "motion-document-pilot-v1", "documents": records})
    empty_text = [item["document_id"] for item in diagnostics if item["words"] == 0]
    write_json(
        PILOT / "validation_report.json",
        {
            "documents_checked": len(records),
            "errors": [],
            "warnings": [
                {
                    "code": "native_text_empty",
                    "documents": empty_text,
                    "message": "OCR requis avant l'audit complet pour ces PDF image.",
                }
            ] if empty_text else [],
            "diagnostics": diagnostics,
        },
    )
    write_json(
        PILOT / "metadata_audit.json",
        {
            "documents": len(records),
            "comparison_counts": dict(comparison_counts),
            "additional_field_presence": dict(sorted(field_presence.items())),
            "minimal_schema_proposal": minimal_schema(),
            "documents_audit": audits,
        },
    )
    write_json(CLEANING_DIR / "report.json", cleaning_report)
    build_html(records, audits, cleaning_report)
    print(json.dumps({"documents": len(records), "comparisons": dict(comparison_counts)}, ensure_ascii=False))
    print(PILOT / "review.html")


if __name__ == "__main__":
    main()
