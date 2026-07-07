from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import unquote, urlparse

import fitz
import requests


SCRIPT_ROOT = Path(__file__).resolve().parent
ROOT = SCRIPT_ROOT
PROJECT_ROOT = ROOT.parents[2]
SCRAPER_DIR = PROJECT_ROOT / "scrape-la-tour-de-peilz"
sys.path.insert(0, str(SCRAPER_DIR))

import scrape_motions_2021_2026 as motion_tools
import scrape_preavis_municipaux_2021_2026 as legacy
import scrape_preavis_search_json_2021_2026 as endpoint_scraper


SELECTED_FILENAMES = [
    "Preavis_01_2021-Credit-leve-oppositions-Rives-lac-Rap-Dec.pdf",
    "Preavis_Compl-Preavis_01_2021-approb-plans-reponses-oppositions-Rives-lac-rapp-dec.pdf",
    "Preavis-01-2022-Motion-Krahenbuhl-Centre-familial-Rapp-Dec.pdf",
    "Preavis-21-Postulat-Schmidhauser-Campagne-Rossier-Rapp-Dec.pdf",
    "Preavis-12-LTDP-Comptes-2023-Dec.pdf",
    "Preavis-22-Reponse-Postulat-Grutta-Etudiants-Rapport.pdf",
    "Preavis-24-Cautionnement-Manege-Villard-Rapp-Dec.pdf",
    "Preavis-31-reamenagement-avenue-Gare-Rapp-Dec.pdf",
    "Preavis-03-Campagne-Rossier.pdf",
    "Preavis-10-Preavis-Ville-du-jeu-Rapp-Dec.pdf",
]
MANUAL_OVERRIDES = {
    "Preavis_10_2021-CIEHL-Gestion-Comptes-2020-Rapp-Dec.pdf": {
        "force_ocr": True,
    },
    "Preavis_Compl-Preavis_01_2021-approb-plans-reponses-oppositions-Rives-lac-rapp-dec.pdf": {
        "document_date": "2021-09-15",
        "decision_date": "2021-12-08",
        "force_ocr": True,
        "commission_members": [
            {"name": "Marianne Adank", "party": None, "role": "member", "attendance": "present"},
            {"name": "Manon Röthlisberger", "party": None, "role": "member", "attendance": "present"},
            {"name": "Kamiar Aminian", "party": None, "role": "member", "attendance": "present"},
            {"name": "Kurt Egli", "party": None, "role": "president_rapporteur", "attendance": "present"},
            {"name": "Guillaume Jung", "party": None, "role": "member", "attendance": "present"},
            {"name": "Piero Negro", "party": None, "role": "member", "attendance": "present"},
            {"name": "Diego Pasquali", "party": None, "role": "member", "attendance": "present"},
        ],
    }
    ,
    "Preavis-12-LTDP-Comptes-2023-Dec.pdf": {
        "force_ocr": True,
        "decision_date": "2024-06-26",
    },
    "Preavis-22-Reponse-Postulat-Grutta-Etudiants-Rapport.pdf": {
        "commission_members": [
            {"name": "Gabrielle Heller", "party": "LV", "role": "president", "attendance": "present"},
            {"name": "Alessio Grutta", "party": "PLR", "role": "member", "attendance": "present"},
            {"name": "Gabriel Chervet", "party": "PLR", "role": "member", "attendance": "present"},
            {"name": "André Gruaz", "party": "PSDG", "role": "member", "attendance": "present"},
            {"name": "Marisa Pralong", "party": "PSDG", "role": "member", "attendance": "present"},
            {"name": "Pierre-Yves Charpilloz", "party": "LCIVL", "role": "member", "attendance": "present"},
            {"name": "Héraclès Dellas", "party": "UDC", "role": "member", "attendance": "present"},
        ],
    },
    "Preavis-03-Campagne-Rossier.pdf": {
        "force_ocr": True,
    },
    "Preavis-11-PA-Petit-Sully-Rapp-Dec.pdf": {
        "preavis_number": "11/2022",
    },
    "Preavis-31-reamenagement-avenue-Gare-Rapp-Dec.pdf": {
        "force_ocr": True,
        "commission_members": [
            {"name": "Florian Abbet", "party": "LV", "role": "president_rapporteur", "attendance": "present"},
            {"name": "Michel Tobler", "party": "PLR", "role": "member", "attendance": "present"},
            {"name": "Yves Rossier", "party": "PLR", "role": "member", "attendance": "present"},
            {"name": "André Gruaz", "party": "PSDG", "role": "member", "attendance": "present"},
            {"name": "Julien Costanzo", "party": "PSDG", "role": "member", "attendance": "present"},
            {"name": "Jean Wilfrid Fils-Aimé", "party": "LCIVL", "role": "member", "attendance": "present"},
        ],
    },
    "Preavis-10-Preavis-Ville-du-jeu-Rapp-Dec.pdf": {
        "force_ocr": True,
        "commission_members": [
            {"name": "Margareta Brüssow", "party": "LCIVL", "role": "member", "attendance": "present"},
            {"name": "Pierre Cavin", "party": "PLR", "role": "member", "attendance": "present"},
            {"name": "Margaux Dubuis", "party": "PLR", "role": "member", "attendance": "present"},
            {"name": "Maude Froidevaux", "party": "LV", "role": "member", "attendance": "present"},
            {"name": "Amandine Gianini", "party": "LV", "role": "member", "attendance": "present"},
            {"name": "Alexandre Davel", "party": "PSDG", "role": "president_rapporteur", "attendance": "present"},
        ],
    },
}
MAX_WORDS = 450
OVERLAP_WORDS = 60
CSS = """body{font:14px/1.45 system-ui;margin:24px;color:#172033}.legend{display:flex;gap:12px;flex-wrap:wrap;margin:14px 0}.tag{padding:8px 12px;border-radius:7px}table{border-collapse:collapse;width:100%}th,td{border:1px solid #d9dfeb;padding:8px;vertical-align:top}th{background:#edf1f7;position:sticky;top:0}tr.base-missing,.red{background:#ffd8d8}tr.additional-missing,.yellow{background:#fff3bf}tr.review,.orange{background:#ffe0b2}tr.complete,.green{background:#e1f5e6}.chunk-green,.chunk-yellow,.chunk-red{padding:8px;border-radius:7px}.chunk-green{background:#c9efd3}.chunk-yellow{background:#ffe69a}.chunk-red{background:#ffb3b3}summary{cursor:pointer;font-weight:650}ul{padding-left:20px}pre{white-space:pre-wrap;word-break:break-word;background:#f5f7fa;padding:10px;max-height:550px;overflow:auto}article{border:2px solid #ccd5e2;border-radius:10px;padding:14px;margin:14px 0}article.green{background:#effaf2}article.yellow{background:#fff8d8}article.red{background:#ffe5e5}"""


def normalize_line(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def explicit_boilerplate(line: str) -> bool:
    compact = normalize_line(line)
    normalized = legacy.normalize(compact)
    if not compact:
        return False
    if re.fullmatch(r"[\s]+", compact):
        return True
    if re.search(r"\b[\w.+-]+@(?:[\w-]+\.)*la-tour-de-peilz\.ch\b", compact, flags=re.I):
        return True
    if re.search(r"\bwww\.la-tour-de-peilz\.ch\b", compact, flags=re.I):
        return True
    if re.search(r"\.(?:docx?|odt)\s*$", compact, flags=re.I):
        return True
    if re.fullmatch(r"(?:Municipalité|Maison de commune|Grand-Rue 46(?:\s*[-·]\s*CP\s*\d+)?|1814 La Tour-de-Peilz|Au Conseil communal de)", compact, flags=re.I):
        return True
    if re.fullmatch(r"021\s+977\s+01\s+\d{2}.*", compact):
        return True
    return False


def plausible_repeated_margin(line: str) -> bool:
    compact = normalize_line(line)
    normalized = legacy.normalize(compact)
    if explicit_boilerplate(compact):
        return True
    if len(compact) > 100:
        return False
    return bool(re.search(
        r"^(?:page\s+\d+|rapport\s*[-–—]\s*(?:preavis|complement)|preavis municipal n|complement au preavis municipal n)",
        normalized,
    ))


def locate_or_download(item: dict) -> Path:
    matches = list((PROJECT_ROOT / "documents" / "la-tour-de-peilz").rglob(item["filename"]))
    if matches:
        return matches[0]
    target = SCRIPT_ROOT / "pdfs" / item["filename"]
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        response = requests.get(item["file_url"], headers=endpoint_scraper.HEADERS, timeout=120)
        response.raise_for_status()
        target.write_bytes(response.content)
    return target


def repeated_margin_lines(page_texts: list[str]) -> list[str]:
    candidates = Counter()
    original = {}
    for text in page_texts:
        lines = [normalize_line(line) for line in text.splitlines() if normalize_line(line)]
        for line in set(lines[:5] + lines[-5:]):
            key = legacy.normalize(line)
            if 4 <= len(key) <= 140 and plausible_repeated_margin(line):
                candidates[key] += 1
                original.setdefault(key, line)
    return [original[key] for key, count in candidates.items() if count >= 2]


def clean_pages(page_texts: list[str], repeated_lines: list[str]) -> tuple[str, int]:
    repeated = {legacy.normalize(line) for line in repeated_lines}
    kept, removed = [], 0
    for page_text in page_texts:
        for line in page_text.splitlines():
            line = re.sub(r"^\s*#{1,6}\s*", "", line)
            compact = normalize_line(line)
            if (
                explicit_boilerplate(compact)
                or legacy.normalize(compact) in repeated
                or re.fullmatch(r"\s*\d+\s*[|/]\s*\d+\s*", compact)
            ):
                removed += 1
                continue
            kept.append(line)
        kept.append("\f")
    return motion_tools.clean_french_text("\n".join(kept)).strip(), removed


def detect_components(text: str, listing_contains_decision: bool = False) -> list[str]:
    normalized = legacy.normalize(text)
    components = []
    if re.search(r"\bpreavis municipal\s+n[°o]?\s*\d{1,2}/20\d{2}", normalized):
        components.append("municipal_preavis")
    if re.search(r"rapport de majorite(?:\s+de la commission)?", normalized):
        components.append("majority_report")
    if re.search(r"rapport de minorite(?:\s+de la commission)?", normalized):
        components.append("minority_report")
    if re.search(
        r"(?im)^\s*(?:#{1,6}\s*)?Rapport (?:de la commission|de commission)(?:\s+(?:ad hoc|charg[ée]e|des finances)|\s*$)",
        text,
    ):
        components.append("commission_report")
    decision_evidence = re.search(
        r"decision du conseil communal|extrait (?:du pv|du proces-verbal|de la seance)[\s\S]{0,180}conseil communal|\bd\s+e\s+c\s+i\s+d\s+e\b",
        normalized,
    )
    if listing_contains_decision and decision_evidence:
        components.append("council_decision")
    return components


def verified_role(components: list[str]) -> str:
    has_preavis = "municipal_preavis" in components
    has_report = any(value in components for value in ("commission_report", "majority_report", "minority_report"))
    has_decision = "council_decision" in components
    if has_preavis and has_report and has_decision:
        return "combined_preavis_report_decision"
    if has_preavis and has_report:
        return "combined_preavis_report"
    if has_preavis and has_decision:
        return "combined_preavis_decision"
    if has_preavis:
        return "municipal_preavis"
    if has_report and has_decision:
        return "combined_report_decision"
    if has_report:
        return "commission_report"
    if has_decision:
        return "council_decision"
    return "unknown"


def financial_requests(subject: str) -> list[dict]:
    results = []
    for match in re.finditer(r"(?:Fr\.|CHF)\s*([\d'’ ]+(?:[.,]\d+)?)", subject, flags=re.I):
        raw = match.group(1).replace("'", "").replace("’", "").replace(" ", "").replace(",", ".")
        try:
            amount = float(raw)
        except ValueError:
            continue
        results.append({"amount_chf": int(amount) if amount.is_integer() else amount, "request_type": "unspecified"})
    return results


def component_boundaries(text: str, document_role: str) -> list[tuple[int, str]]:
    patterns = [
        ("appendix", r"(?im)^\s*(?:#{1,6}\s*)?Annexes?\s*:.*$"),
        ("majority_report", r"(?im)^\s*(?:#{1,6}\s*)?Rapport de majorit[ée](?:\s+de la commission)?.*$"),
        ("minority_report", r"(?im)^\s*(?:#{1,6}\s*)?Rapport de minorit[ée](?:\s+de la commission)?.*$"),
        (
            "commission_report",
            r"(?im)^\s*(?:#{1,6}\s*)?Rapport (?:de la commission|de commission|de la Commission des finances).*$",
        ),
        (
            "council_decision",
            r"(?im)^\s*(?:#{1,6}\s*)?(?:Décision(?:\s+du Conseil communal)?|EXTRAIT[\s\S]{0,140}?(?:procès-verbal|PV)[\s\S]{0,140}?Conseil communal).*$",
        ),
    ]
    candidates = []
    for component, pattern in patterns:
        for match in re.finditer(pattern, text):
            candidates.append((match.start(), component))
    candidates.sort()
    boundaries: list[tuple[int, str]] = [(0, "municipal_preavis")]
    for position, component in candidates:
        # Ignore headings quoted in a table of contents or immediately repeated.
        if position < 150 or (boundaries and position - boundaries[-1][0] < 120):
            continue
        current = boundaries[-1][1]
        if document_role == "municipal_preavis" and component != "appendix":
            continue
        if current == "appendix" and component == "council_decision":
            continue
        if current != component:
            boundaries.append((position, component))

    # Some decisions only start with a spaced "d é c i d e". In a combined
    # listing, the final occurrence is a useful fallback if it is near the end.
    if "decision" in document_role and not any(component == "council_decision" for _, component in boundaries):
        matches = list(re.finditer(r"(?im)^\s*d\s+é\s+c\s+i\s+d\s+e\s*:?.*$", text))
        if matches and matches[-1].start() > len(text) * 0.65:
            boundaries.append((matches[-1].start(), "council_decision"))
            boundaries.sort()
    return boundaries


def split_sections(text: str, document_role: str) -> list[dict]:
    boundaries = component_boundaries(text, document_role)
    sections = []
    for index, (start, component) in enumerate(boundaries):
        end = boundaries[index + 1][0] if index + 1 < len(boundaries) else len(text)
        content = text[start:end].strip()
        if content:
            sections.append({"component": component, "content": content})
    return sections


def split_words(text: str) -> list[str]:
    tokens = re.findall(r"\S+", text)
    chunks, start = [], 0
    while start < len(tokens):
        end = min(len(tokens), start + MAX_WORDS)
        chunks.append(" ".join(tokens[start:end]))
        if end == len(tokens):
            break
        start = end - OVERLAP_WORDS
    return chunks


def build_chunks(metadata: dict, text: str) -> list[dict]:
    base = metadata["document_metadata"]
    chunks = []
    for section_index, section in enumerate(split_sections(text, base["document_role"])):
        pieces = split_words(section["content"])
        for piece_index, content in enumerate(pieces):
            chunk_index = len(chunks)
            issues = []
            word_count = len(content.split())
            if word_count > MAX_WORDS:
                issues.append("chunk_too_long")
            if word_count < 30 and piece_index < len(pieces) - 1:
                issues.append("chunk_too_short")
            if re.fullmatch(r"\s*\d+\s*[|/]\s*\d+\s*", content):
                issues.append("isolated_page_number")
            embedding_input = (
                f"document_family: {base['document_family']}\n"
                f"category: {base['category']}\n"
                f"document_role: {base['document_role']}\n"
                f"title: {base['title']}\n"
                f"component: {section['component']}\n\n{content}"
            )
            chunks.append({
                "chunk_id": f"{base['document_id']}#chunk-{chunk_index:03d}",
                "document_id": base["document_id"],
                "chunk_index": chunk_index,
                "section_index": section_index,
                "component": section["component"],
                "content": content,
                "word_count": word_count,
                "content_hash": hashlib.sha256(content.encode()).hexdigest(),
                "embedding_input": embedding_input,
                "quality": "red" if "chunk_too_long" in issues else "yellow" if issues else "green",
                "quality_issues": issues,
            })
    return chunks


def audit_item(item: dict) -> dict:
    override = MANUAL_OVERRIDES.get(item["filename"], {})
    preavis_number = override.get("preavis_number") or item["preavis_number"]
    pdf_path = locate_or_download(item)
    document = fitz.open(pdf_path)
    page_texts = [page.get_text("text") for page in document]
    page_stats = []
    for number, (page, text) in enumerate(zip(document, page_texts), 1):
        chars = len(text.strip())
        page_stats.append({"page": number, "characters": chars, "images": len(page.get_images(full=True)), "low_text": chars < 80})
    native_text = "\f".join(page_texts)
    ocr_path = SCRIPT_ROOT / "ocr_overrides" / f"{Path(item['filename']).stem}.md"
    ocr_text = ocr_path.read_text(encoding="utf-8").strip() if ocr_path.exists() else ""
    ocr_applied = bool(ocr_text)
    selected_page_texts = ocr_text.split("\f") if ocr_applied else page_texts
    selected_raw_text = "\f".join(selected_page_texts)
    repeated_lines = repeated_margin_lines(selected_page_texts)
    clean_text, removed_blocks = clean_pages(selected_page_texts, repeated_lines)
    coverage = sum(page["characters"] >= 80 for page in page_stats) / max(len(page_stats), 1)
    full_ocr = len(native_text.strip()) < 500 or coverage < 0.25 or bool(override.get("force_ocr"))
    low_image_pages = [page["page"] for page in page_stats if page["low_text"] and page["images"]]
    pages_with_images = [page["page"] for page in page_stats if page["images"]]
    total_images = sum(page["images"] for page in page_stats)
    image_page_ratio = len(pages_with_images) / max(len(page_stats), 1)
    # Une image répétée (logo, signature) sur chaque page ne suffit pas à
    # recommander un OCR. Le volume signale les dossiers graphiquement riches ;
    # les pages image avec peu de texte restent affichées séparément.
    many_images = total_images >= 50
    needs_ocr = full_ocr and not ocr_applied
    if ocr_applied:
        extraction_recommendation = "mistral_ocr_selected"
    elif full_ocr:
        extraction_recommendation = "selective_ocr_required" if native_text.strip() else "ocr_required"
    elif low_image_pages:
        extraction_recommendation = "native_pdf_ignore_non_text_images"
    else:
        extraction_recommendation = "native_pdf"
    components = detect_components(clean_text, item.get("listing_contains_decision", False))
    detected_role = verified_role(components)
    # Un préavis retiré peut embarquer des annexes historiques contenant des
    # rapports et décisions antérieurs. Elles ne changent pas son rôle principal.
    final_role = item["document_role"] if item.get("listing_withdrawn") else detected_role
    component_dates = legacy.extract_component_dates(clean_text, final_role, preavis_number)
    members = motion_tools.extract_commission_members(clean_text) if "report" in final_role else []
    if override.get("commission_members"):
        members = override["commission_members"]
    document_id = "doc_" + hashlib.sha256(item["file_url"].encode()).hexdigest()[:20]
    metadata = {
        "document_metadata": {
            "document_id": document_id,
            "commune": item["commune"],
            "document_family": item["document_family"],
            "category": item["category"],
            "document_role": final_role,
            "title": item["title"],
            "source_title": item["source_title"],
            "source_page_url": item["source_page_url"],
            "file_url": item["file_url"],
            "filename": item["filename"],
            "listing_year": item["listing_year"],
            "legislature": item["legislature"],
            "document_date": override.get("document_date") or component_dates.get("document_date"),
            "content_hash": hashlib.sha256(clean_text.encode()).hexdigest(),
            "extraction_method": "mistral_ocr" if ocr_applied else "native_pdf_pending_ocr" if needs_ocr else "native_pdf",
            "processing_status": "needs_ocr" if needs_ocr else "pilot_audited",
        },
        "preavis_metadata": {
            "preavis_number": preavis_number,
            "political_status": item["political_status"],
            "decision_date": override.get("decision_date") or component_dates.get("decision_date"),
            "contains_majority_report": "majority_report" in components,
            "contains_minority_report": "minority_report" in components,
            "commission": {"members": members},
            "decision": {"outcome": None, "vote_result": None},
            "financial_requests": financial_requests(item["title"]),
        },
        "processing": {
            "text_extraction_status": {
                "characters_extracted": len(selected_raw_text.strip()),
                "native_characters": len(native_text.strip()),
                "ocr_characters": len(ocr_text) if ocr_applied else None,
                "text_available": bool(selected_raw_text.strip()),
                "needs_ocr": needs_ocr,
                "ocr_applied": ocr_applied,
                "page_text_coverage": round(coverage, 3),
                "low_text_image_pages": low_image_pages,
                "recommendation": extraction_recommendation,
            },
            "header_footer_cleaning": {
                "raw_words": len(selected_raw_text.split()),
                "clean_words": len(clean_text.split()),
                "removed_blocks": removed_blocks,
                "repeated_margin_candidates": repeated_lines,
            },
            "selected_text": {"method": "mistral_ocr" if ocr_applied else "native_pdf_pending_ocr" if needs_ocr else "native_pdf", "words": len(clean_text.split())},
        },
    }
    metadata["document_metadata"] = {key: value for key, value in metadata["document_metadata"].items() if value is not None}
    warnings = []
    if item["document_role"] != final_role:
        warnings.append("listing_role_differs_from_pdf")
    if item.get("listing_withdrawn") and detected_role != final_role:
        warnings.append("withdrawn_preavis_contains_historical_appendices")
    if needs_ocr:
        warnings.append("ocr_required")
    if item.get("source_number_conflict"):
        warnings.append("source_number_conflict")
    if "report" in final_role and not members:
        warnings.append("commission_members_not_detected")
    base_required = (
        "document_id", "commune", "document_family", "category", "document_role", "title",
        "source_page_url", "file_url", "filename", "listing_year", "legislature",
        "document_date", "content_hash", "extraction_method", "processing_status",
    )
    base_missing = [field for field in base_required if metadata["document_metadata"].get(field) in (None, "")]
    additional_missing = []
    has_report = "report" in final_role
    has_decision = "decision" in final_role
    if has_report and not members:
        additional_missing.append("commission.members")
    if has_decision and not metadata["preavis_metadata"].get("decision_date"):
        additional_missing.append("decision_date")
    audit = {
        "valid": not base_missing and not additional_missing and not warnings,
        "base_missing": base_missing,
        "additional_missing": additional_missing,
        "warnings": warnings,
    }
    output_dir = ROOT / "metadata"
    text_dir = ROOT / "clean_text"
    removed_dir = ROOT / "removed_blocks"
    output_dir.mkdir(exist_ok=True)
    text_dir.mkdir(exist_ok=True)
    removed_dir.mkdir(exist_ok=True)
    (output_dir / f"{document_id}.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (text_dir / f"{document_id}.txt").write_text(clean_text + "\n", encoding="utf-8")
    (removed_dir / f"{document_id}.json").write_text(
        json.dumps({"repeated_margin_candidates": repeated_lines, "removed_blocks": removed_blocks}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    chunks = build_chunks(metadata, clean_text)
    chunks_dir = ROOT / "chunks"
    chunks_dir.mkdir(exist_ok=True)
    (chunks_dir / f"{document_id}.json").write_text(
        json.dumps(chunks, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {
        "document_id": document_id,
        "title": item["title"],
        "preavis_number": preavis_number,
        "listing_year": item["listing_year"],
        "listing_role": item["document_role"],
        "verified_role": final_role,
        "components": components,
        "pages": len(page_stats),
        "native_characters": len(native_text.strip()),
        "coverage": round(coverage, 3),
        "ocr": extraction_recommendation,
        "low_text_image_pages": low_image_pages,
        "image_diagnostic": {
            "many_images": many_images,
            "total_images": total_images,
            "pages_with_images": len(pages_with_images),
            "page_ratio": round(image_page_ratio, 3),
        },
        "commission_members": len(members),
        "warnings": warnings,
        "audit": audit,
        "chunks": len(chunks),
        "green_chunks": sum(chunk["quality"] == "green" for chunk in chunks),
        "yellow_chunks": sum(chunk["quality"] == "yellow" for chunk in chunks),
        "red_chunks": sum(chunk["quality"] == "red" for chunk in chunks),
        "metadata": metadata,
        "page_stats": page_stats,
        "preview": clean_text[:5000],
        "file_url": item["file_url"],
    }


def detail_html(record: dict) -> str:
    metadata_json = html.escape(json.dumps(record["metadata"], ensure_ascii=False, indent=2))
    audit_json = html.escape(json.dumps(record["audit"], ensure_ascii=False, indent=2))
    page_rows = "".join(
        f"<tr><td>{page['page']}</td><td>{page['characters']}</td><td>{page['images']}</td><td>{'⚠️' if page['low_text'] else 'OK'}</td></tr>"
        for page in record["page_stats"]
    )
    return f"""<!doctype html><html lang='fr'><head><meta charset='utf-8'><title>{html.escape(record['title'])}</title><style>{CSS}</style></head><body>
    <p><a href='../audit.html'>← Audit principal</a> · <a href='../clean_text/{record['document_id']}.txt'>Texte</a> · <a href='../removed_blocks/{record['document_id']}.json'>Blocs retirés</a> · <a href='../chunk_details/{record['document_id']}.html'>Chunks</a> · <a href='{html.escape(record['file_url'])}'>PDF officiel</a></p>
    <h1>{html.escape(record['title'])}</h1><p>Préavis {record['preavis_number']} · rôle listing <code>{record['listing_role']}</code> · rôle vérifié <code>{record['verified_role']}</code></p>
    <h2>Contrôles</h2><pre>{audit_json}</pre><h2>Métadonnées finales</h2><pre>{metadata_json}</pre>
    <h2>Diagnostic des pages</h2><table><tr><th>Page</th><th>Caractères</th><th>Images</th><th>Texte faible</th></tr>{page_rows}</table>
    <h2>Texte nettoyé — aperçu</h2><pre>{html.escape(record['preview'])}</pre></body></html>"""


def build_html(records: list[dict]) -> str:
    rows = []
    for record in records:
        audit = record["audit"]
        if audit["base_missing"]:
            css, state = "base-missing", "Base incomplète"
        elif audit["additional_missing"]:
            css, state = "additional-missing", "Metadata spécifique incomplète"
        elif audit["warnings"]:
            css, state = "review", "À vérifier"
        else:
            css, state = "complete", "Complet"
        processing = record["metadata"]["processing"]
        cleaning = processing["header_footer_cleaning"]
        extraction = processing["text_extraction_status"]
        counts = {"green": record["green_chunks"], "yellow": record["yellow_chunks"], "red": record["red_chunks"]}
        verdict = "Problème structurel" if counts["red"] else "À vérifier" if counts["yellow"] else "Bon structurellement"
        chunk_css = "chunk-red" if counts["red"] else "chunk-yellow" if counts["yellow"] else "chunk-green"
        chunks = json.loads((ROOT / "chunks" / f"{record['document_id']}.json").read_text(encoding="utf-8"))
        lines = "".join(
            f"<li>#{chunk['chunk_index']} · {html.escape(chunk['component'])} · {chunk['word_count']} mots · {html.escape(', '.join(chunk['quality_issues']) or 'OK')}</li>"
            for chunk in chunks
        )
        chunk_html = f"<div class='{chunk_css}'><strong>{verdict}</strong><br>{record['chunks']} chunks : {counts['green']} verts, {counts['yellow']} jaunes, {counts['red']} rouges<details><summary>Voir tous</summary><ul>{lines}</ul></details></div>"
        base = record["metadata"]["document_metadata"]
        specific = record["metadata"]["preavis_metadata"]
        rows.append(f"""<tr class='{css}'><td>{record['listing_year']}</td><td><a href='{html.escape(record['file_url'])}' target='_blank'>{html.escape(record['title'])}</a><br>N° {record['preavis_number']}</td>
        <td>{record['verified_role']}</td><td><strong>{state}</strong><br>{html.escape(', '.join(audit['warnings']))}</td>
        <td>{html.escape(', '.join(audit['base_missing']) or 'Aucun')}</td><td>{html.escape(', '.join(audit['additional_missing']) or 'Aucun')}</td>
        <td>Document : {base.get('document_date','—')}<br>Décision : {specific.get('decision_date') or '—'}</td>
        <td>Natif : {cleaning['raw_words']} → {cleaning['clean_words']}<br>{cleaning['removed_blocks']} blocs retirés<br>Retenu : {processing['selected_text']['method']} — {processing['selected_text']['words']} mots</td>
        <td>{'Oui' if extraction['needs_ocr'] else 'Non'}<br>{html.escape(extraction['recommendation'])}</td>
        <td>{'Oui' if record['image_diagnostic']['many_images'] else 'Non'}<br>{record['image_diagnostic']['total_images']} images sur {record['image_diagnostic']['pages_with_images']}/{record['pages']} pages<br>{len(record['low_text_image_pages'])} pages image avec texte faible</td><td>{chunk_html}</td>
        <td><a href='metadata/{record['document_id']}.json'>JSON</a> · <a href='clean_text/{record['document_id']}.txt'>Texte</a> · <a href='removed_blocks/{record['document_id']}.json'>Blocs</a> · <a href='details/{record['document_id']}.html'>Contrôles</a> · <a href='chunk_details/{record['document_id']}.html'>Chunks</a></td></tr>""")
    ocr_count = sum("ocr_required" in record["ocr"] for record in records)
    return f"""<!doctype html><html lang='fr'><head><meta charset='utf-8'><title>Audit préavis</title><style>{CSS}</style></head><body>
    <h1>Audit des préavis municipaux — {len(records)} documents</h1><p>Source JSON officielle. Nettoyage non destructif. <a href='chunks_audit.html'><strong>Voir l’audit global des chunks</strong></a>.</p>
    <div class='legend'><span class='tag red'>Rouge : base incomplète</span><span class='tag yellow'>Jaune : metadata préavis incomplète</span><span class='tag orange'>Orange : contrôle requis</span><span class='tag green'>Vert : complet</span></div>
    <table><thead><tr><th>Année</th><th>Document</th><th>Rôle</th><th>État</th><th>Manque base</th><th>Manque spécifique</th><th>Dates</th><th>Nettoyage</th><th>OCR</th><th>Beaucoup d'images</th><th>Chunks</th><th>Fichiers</th></tr></thead><tbody>{''.join(rows)}</tbody></table><h2>Échecs</h2><ul><li>Aucun</li></ul></body></html>"""


def chunk_detail_html(record: dict, chunks: list[dict]) -> str:
    cards = []
    for chunk in chunks:
        issues = ", ".join(chunk["quality_issues"]) or "aucune"
        cards.append(f"""<article class='{chunk['quality']}'><h2>{html.escape(chunk['chunk_id'])}</h2>
        <p><strong>Composant :</strong> {html.escape(chunk['component'])} · <strong>{chunk['word_count']} mots</strong> · Alertes : {html.escape(issues)}</p>
        <details open><summary>Contenu du chunk</summary><pre>{html.escape(chunk['content'])}</pre></details>
        <details><summary>Entrée prévue pour l’embedding</summary><pre>{html.escape(chunk['embedding_input'])}</pre></details></article>""")
    return f"""<!doctype html><html lang='fr'><head><meta charset='utf-8'><title>Chunks — {html.escape(record['title'])}</title><style>
    body{{font:14px/1.5 system-ui;margin:24px;color:#172033;max-width:1200px}}article{{border:2px solid;padding:14px;margin:14px 0;border-radius:10px}}article.green{{border-color:#58a66d;background:#effaf2}}article.yellow{{border-color:#d8a928;background:#fff8d8}}article.red{{border-color:#d44;background:#ffe5e5}}pre{{white-space:pre-wrap;word-break:break-word;background:white;padding:12px;max-height:600px;overflow:auto}}summary{{cursor:pointer;font-weight:700}}</style></head><body>
    <p><a href='../audit.html'>← Audit principal</a> · <a href='../chunks_audit.html'>Audit global des chunks</a> · <a href='../details/{record['document_id']}.html'>Métadonnées du document</a></p>
    <h1>{html.escape(record['title'])}</h1><p>{len(chunks)} chunks · limite {MAX_WORDS} mots · chevauchement {OVERLAP_WORDS} mots.</p>{''.join(cards)}</body></html>"""


def chunks_audit_html(records: list[dict]) -> str:
    rows = []
    for record in records:
        color = "red" if record["red_chunks"] else "yellow" if record["yellow_chunks"] else "green"
        rows.append(f"""<tr class='{color}'><td><a href='chunk_details/{record['document_id']}.html'>{html.escape(record['title'])}</a></td>
        <td>{record['chunks']}</td><td>{record['green_chunks']}</td><td>{record['yellow_chunks']}</td><td>{record['red_chunks']}</td>
        <td>{html.escape(', '.join(record['components']))}</td></tr>""")
    return f"""<!doctype html><html lang='fr'><head><meta charset='utf-8'><title>Audit des chunks — préavis</title><style>
    body{{font:14px/1.5 system-ui;margin:24px;color:#172033}}table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #d8deea;padding:8px}}th{{background:#edf1f7}}tr.green{{background:#e8f7ec}}tr.yellow{{background:#fff6d8}}tr.red{{background:#ffe3e3}}</style></head><body>
    <p><a href='audit.html'>← Audit principal</a></p><h1>Audit global des chunks — {len(records)} préavis</h1>
    <p>Cliquez sur un document pour examiner chaque chunk et son entrée d’embedding.</p>
    <table><tr><th>Document</th><th>Chunks</th><th>Verts</th><th>Jaunes</th><th>Rouges</th><th>Composants</th></tr>{''.join(rows)}</table></body></html>"""


def main() -> None:
    global ROOT
    parser = argparse.ArgumentParser()
    parser.add_argument("--full", action="store_true", help="Auditer tous les préavis du scraper")
    args = parser.parse_args()
    ROOT = SCRIPT_ROOT.parent / "full-audit" if args.full else SCRIPT_ROOT
    ROOT.mkdir(parents=True, exist_ok=True)
    source_path = SCRIPT_ROOT.parent / "scraper-test" / "search-json-test.json"
    payload = json.loads(source_path.read_text(encoding="utf-8"))
    by_filename = {item["filename"]: item for item in payload["documents"]}
    selected_filenames = list(by_filename) if args.full else SELECTED_FILENAMES
    missing = [name for name in selected_filenames if name not in by_filename]
    if missing:
        raise SystemExit(f"Documents absents du scraper: {missing}")
    records = [audit_item(by_filename[name]) for name in selected_filenames]
    details = ROOT / "details"
    details.mkdir(exist_ok=True)
    for record in records:
        (details / f"{record['document_id']}.html").write_text(detail_html(record), encoding="utf-8")
    chunk_details = ROOT / "chunk_details"
    chunk_details.mkdir(exist_ok=True)
    summaries = []
    for record in records:
        chunks = json.loads((ROOT / "chunks" / f"{record['document_id']}.json").read_text(encoding="utf-8"))
        (chunk_details / f"{record['document_id']}.html").write_text(
            chunk_detail_html(record, chunks), encoding="utf-8"
        )
        summaries.append({key: record[key] for key in (
            "document_id", "title", "preavis_number", "chunks", "green_chunks", "yellow_chunks", "red_chunks", "components"
        )})
    (ROOT / "audit.json").write_text(json.dumps({"documents": records}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (ROOT / "audit.html").write_text(build_html(records), encoding="utf-8")
    (ROOT / "chunks_audit.html").write_text(chunks_audit_html(records), encoding="utf-8")
    (ROOT / "chunks_summary.json").write_text(json.dumps(summaries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "documents": len(records),
        "ocr_required": sum("ocr_required" in record["ocr"] for record in records),
        "ocr_selected": sum(record["ocr"] == "mistral_ocr_selected" for record in records),
        "native_pdf": sum(record["ocr"] in {"native_pdf", "native_pdf_ignore_non_text_images"} for record in records),
        "chunks": sum(record["chunks"] for record in records),
        "yellow_chunks": sum(record["yellow_chunks"] for record in records),
        "red_chunks": sum(record["red_chunks"] for record in records),
        "warnings": dict(Counter(warning for record in records for warning in record["warnings"])),
    }, ensure_ascii=False, indent=2))
    print(ROOT / "audit.html")


if __name__ == "__main__":
    main()
