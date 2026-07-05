from __future__ import annotations

import hashlib
import html
import json
import re
import sys
from collections import Counter
from pathlib import Path

import fitz
import requests


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parents[1]
PILOT = ROOT / "pilot"
SCRAPER_DIR = PROJECT_ROOT / "scrape-la-tour-de-peilz"
sys.path.insert(0, str(SCRAPER_DIR))

import scrape_postulats_2021_2026 as scraper
import scrape_postulats_search_json_2021_2026 as json_scraper


BASE_DIR = PILOT / "document_metadata"
ADDITIONAL_DIR = PILOT / "scraper_metadata"
COMBINED_DIR = PILOT / "combined_metadata_view"
ARTIFACTS_DIR = PILOT / "artifacts"
SELECTED_TEXT_DIR = PILOT / "selected_text"
OCR_DIR = ROOT / "full-audit" / "ocr_overrides"
OVERRIDES_PATH = ROOT / "manual_overrides.json"

FIELD_MAPPINGS = {
    "commune": "commune", "category": "category", "document_role": "document_role",
    "title": "object_title", "source_title": "site_listing_title",
    "source_page_url": "source_page", "file_url": "pdf_url",
    "listing_year": "listing_year", "legislature": "legislature",
    "document_date": "document_date",
}
USEFUL_POSTULAT = {
    "authors", "status_normalized", "report_type", "contains_majority_report",
    "contains_minority_report", "commission", "decision",
}
RELATION_FIELDS = {"political_object_id", "political_object", "document_components"}
PROCESSING_FIELDS = {"metadata_version", "text_extraction_status"}
SOURCE_FIELDS = {"filename", "site_status_raw", "site_subject", "source_collection", "canonical_object", "source_endpoint", "source_category_id"}
REMOVABLE_FIELDS = {
    "type", "document_type", "year", "summary", "title", "status", "content_kind",
    "search_facets", "contains_report", "contains_decision",
}


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_overrides() -> dict:
    return json.loads(OVERRIDES_PATH.read_text(encoding="utf-8")) if OVERRIDES_PATH.exists() else {}


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize(value):
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        result = value.strip().casefold()
        return "postulat" if result in {"postulat", "postulats"} else result
    return value


def word_count(text: str) -> int:
    return len(re.findall(r"\b\w+[\w'-]*\b", text, flags=re.UNICODE))


def download(item: dict, target: Path) -> None:
    if target.exists() and target.stat().st_size:
        return
    response = requests.get(item["pdf_url"], headers=scraper.HEADERS, timeout=120)
    response.raise_for_status()
    if not response.content.startswith(b"%PDF"):
        raise ValueError(f"Ressource non PDF: {item['pdf_url']}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(response.content)


def extract_text(pdf_path: Path) -> tuple[str, int]:
    with fitz.open(pdf_path) as pdf:
        pages = [scraper.motion_tools.clean_pdf_text(page.get_text("text")) for page in pdf]
        return "\n".join(pages).strip(), len(pdf)


def base_record(item: dict, enriched: dict, text: str) -> dict:
    return {
        "document_id": "doc_" + sha256(item["pdf_url"])[:20],
        "commune": "La Tour-de-Peilz", "document_family": "political_object",
        "category": "postulat", "document_role": enriched["document_role"],
        "title": enriched.get("object_title") or item.get("summary") or item["site_listing_title"],
        "source_title": item["site_listing_title"], "source_page_url": item["source_page"],
        "file_url": item["pdf_url"], "listing_year": int(item["listing_year"]),
        "legislature": item.get("legislature") or "2021-2026",
        "document_date": enriched.get("document_date"),
        "content_hash": sha256(re.sub(r"\s+", " ", text).strip()),
        "extraction_method": "native_pdf" if text.strip() else "native_pdf_empty",
        "processing_status": "pilot_generated" if text.strip() else "needs_ocr",
    }


def classify(base: dict, additional: dict) -> tuple[list[dict], dict]:
    comparisons = []
    mapped = set(FIELD_MAPPINGS.values())
    for base_field, additional_field in FIELD_MAPPINGS.items():
        if additional_field not in additional:
            status = "absent"
        elif normalize(base.get(base_field)) == normalize(additional.get(additional_field)):
            status = "doublon"
        else:
            status = "semantique_differente_ou_conflit"
        comparisons.append({
            "base_field": base_field, "additional_field": additional_field,
            "base_value": base.get(base_field), "additional_value": additional.get(additional_field),
            "classification": status,
        })
    remaining = set(additional) - mapped
    groups = {
        "utile_metadata_postulat": sorted(remaining & USEFUL_POSTULAT),
        "utile_relations_structurees": sorted(remaining & RELATION_FIELDS),
        "utile_traitement_hors_metadata": sorted(remaining & PROCESSING_FIELDS),
        "utile_snapshot_source": sorted(remaining & SOURCE_FIELDS),
        "supprimable_ou_recalculable": sorted(remaining & REMOVABLE_FIELDS),
    }
    groups["a_revoir"] = sorted(remaining - set().union(USEFUL_POSTULAT, RELATION_FIELDS, PROCESSING_FIELDS, SOURCE_FIELDS, REMOVABLE_FIELDS))
    return comparisons, groups


def combined_view(base: dict, additional: dict, words: int, manual_review: dict | None = None) -> dict:
    decision = additional.get("decision") or {}
    postulat = {
        "authors": additional.get("authors", []),
        "political_status": additional.get("status_normalized"),
        "report_type": additional.get("report_type"),
        "contains_majority_report": bool(additional.get("contains_majority_report")),
        "contains_minority_report": bool(additional.get("contains_minority_report")),
        "decision_date": decision.get("decision_date"),
    }
    if additional.get("commission"):
        postulat["commission"] = additional["commission"]
    if decision:
        postulat["decision"] = decision
    extraction = additional.get("text_extraction_status") or {}
    processing = {
        "text_extraction_status": extraction,
        "selected_text": {"method": base["extraction_method"], "words": words},
    }
    if manual_review:
        processing["manual_review"] = manual_review
    return {
        "document_metadata": base,
        "postulat_metadata": postulat,
        "processing": processing,
    }


def minimal_schema() -> dict:
    return {
        "document_metadata": {
            "document_id": "doc_…", "commune": "La Tour-de-Peilz",
            "document_family": "political_object", "category": "postulat",
            "document_role": "postulat_text | combined_postulat_report_decision | …",
            "title": "…", "source_title": "…", "source_page_url": "…",
            "file_url": "…", "listing_year": 2024, "legislature": "2021-2026",
            "document_date": "YYYY-MM-DD | null", "content_hash": "sha256",
            "extraction_method": "native_pdf", "processing_status": "validated",
        },
        "postulat_metadata": {
            "authors": [{"name": "…", "party": "…"}],
            "political_status": "…", "report_type": "… | null",
            "contains_majority_report": False, "contains_minority_report": False,
            "decision_date": "YYYY-MM-DD | null", "commission": "optionnel",
            "decision": "optionnel",
        },
        "processing": {"text_extraction_status": {}, "selected_text": {}},
    }


def build_html(records: list[dict], audits: list[dict], diagnostics: list[dict], counts: Counter) -> None:
    by_id = {x["document_id"]: x for x in audits}
    diag = {x["document_id"]: x for x in diagnostics}
    rows = []
    for record in records:
        item = by_id[record["document_id"]]
        local = Counter(x["classification"] for x in item["comparisons"])
        groups = html.escape(json.dumps(item["additional_fields"], ensure_ascii=False, indent=2))
        rows.append(
            f"<tr><td>{record['listing_year']}</td><td>{html.escape(record['title'])}</td><td>{record['document_role']}</td>"
            f"<td>{diag[record['document_id']]['pages']}</td><td>{diag[record['document_id']]['words']}</td>"
            f"<td>{local['doublon']}</td><td>{local['semantique_differente_ou_conflit']}</td>"
            f"<td><details><summary>Voir</summary><pre>{groups}</pre></details></td>"
            f"<td><a href='combined_metadata_view/{record['document_id']}.json'>JSON final</a></td></tr>"
        )
    schema = html.escape(json.dumps(minimal_schema(), ensure_ascii=False, indent=2))
    page = f"""<!doctype html><html lang='fr'><head><meta charset='utf-8'><title>Pilote postulats</title><style>body{{font:14px/1.45 system-ui;margin:24px;color:#172033}}.cards{{display:flex;gap:12px;flex-wrap:wrap}}.card{{border:1px solid #d9dfeb;border-radius:8px;padding:12px 18px}}.card b{{display:block;font-size:24px;color:#1769aa}}table{{border-collapse:collapse;width:100%;margin-top:18px}}th,td{{border:1px solid #d9dfeb;padding:8px;vertical-align:top}}th{{background:#edf1f7}}pre{{white-space:pre-wrap;background:#f5f7fa;padding:10px;max-height:480px;overflow:auto}}summary{{cursor:pointer;font-weight:700}}</style></head><body>
<h1>Pilote et audit des métadonnées — postulats 2021–2026</h1><p>Première phase uniquement : source JSON, base documentaire et métadonnées additionnelles. Aucun audit général des chunks n'est lancé.</p>
<div class='cards'><div class='card'><b>{len(records)}</b>documents</div><div class='card'><b>{counts['doublon']}</b>doublons</div><div class='card'><b>{counts['semantique_differente_ou_conflit']}</b>conflits/sens différents</div><div class='card'><b>{counts['absent']}</b>absents</div></div>
<h2>Schéma minimal proposé</h2><pre>{schema}</pre><h2>Documents</h2><table><thead><tr><th>Année</th><th>Titre</th><th>Rôle</th><th>Pages</th><th>Mots</th><th>Doublons</th><th>Conflits</th><th>Classement des champs</th><th>Vue finale</th></tr></thead><tbody>{''.join(rows)}</tbody></table></body></html>"""
    (PILOT / "review.html").write_text(page, encoding="utf-8")


def main() -> None:
    items, endpoint_diagnostics = json_scraper.collect_items()
    overrides = read_overrides()
    records, audits, diagnostics = [], [], []
    aggregate, presence = Counter(), Counter()
    for index, item in enumerate(items, 1):
        print(f"[{index}/{len(items)}] {item['filename']}")
        artifact = ARTIFACTS_DIR / Path(item["filename"]).stem
        pdf_path = artifact / "document.pdf"
        download(item, pdf_path)
        text, pages = extract_text(pdf_path)
        artifact.mkdir(parents=True, exist_ok=True)
        (artifact / "native.txt").write_text(text + "\n", encoding="utf-8")
        ocr_path = OCR_DIR / f"{Path(item['filename']).stem}.md"
        selected_text = ocr_path.read_text(encoding="utf-8").strip() if ocr_path.exists() else text
        if ocr_path.exists():
            selected_text = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", selected_text).strip()
        selected_method = "mistral_ocr" if ocr_path.exists() else "native_pdf" if text.strip() else "native_pdf_empty"
        additional = scraper.enrich_postulat_metadata(item, selected_text)
        override = overrides.get(item["filename"])
        manual_review = None
        if override:
            for field in ("document_date",):
                if field in override:
                    additional[field] = override[field]
            manual_review = {"status": "corrected", "reason": override["reason"]}
        base = base_record(item, additional, selected_text)
        base["extraction_method"] = selected_method
        comparisons, groups = classify(base, additional)
        aggregate.update(x["classification"] for x in comparisons)
        presence.update(additional.keys())
        write_json(BASE_DIR / f"{base['document_id']}.json", base)
        write_json(ADDITIONAL_DIR / f"{base['document_id']}.json", additional)
        SELECTED_TEXT_DIR.mkdir(parents=True, exist_ok=True)
        (SELECTED_TEXT_DIR / f"{base['document_id']}.txt").write_text(selected_text+"\n", encoding="utf-8")
        write_json(COMBINED_DIR / f"{base['document_id']}.json", combined_view(base, additional, word_count(selected_text), manual_review))
        records.append(base)
        audits.append({"document_id": base["document_id"], "title": base["title"], "comparisons": comparisons, "additional_fields": groups})
        diagnostics.append({"document_id": base["document_id"], "filename": item["filename"], "pages": pages, "words": word_count(selected_text), "needs_ocr": not bool(selected_text.strip()), "selected_method": selected_method})
    write_json(PILOT / "manifest.json", {"schema_version": "postulat-document-pilot-v1", "documents": records})
    write_json(PILOT / "validation_report.json", {"documents_checked": len(records), "endpoint": endpoint_diagnostics, "diagnostics": diagnostics})
    write_json(PILOT / "metadata_audit.json", {"documents": len(records), "comparison_counts": dict(aggregate), "additional_field_presence": dict(sorted(presence.items())), "minimal_schema_proposal": minimal_schema(), "documents_audit": audits})
    build_html(records, audits, diagnostics, aggregate)
    print(json.dumps({"documents": len(records), "comparisons": dict(aggregate), "needs_ocr": sum(x['needs_ocr'] for x in diagnostics)}, ensure_ascii=False))
    print(PILOT / "review.html")


if __name__ == "__main__":
    main()
