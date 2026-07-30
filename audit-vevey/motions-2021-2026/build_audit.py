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
MAX_WORDS = 450
OVERLAP_WORDS = 60


OBJECTS = {
    "sandra-marques-conseil": {
        "object_id": "vevey-motion-2025-sandra-marques-conseil",
        "object_title": (
            "Diminuer le nombre d'élues/élus communaux pour permettre "
            "la rénovation de la salle du Conseil communal"
        ),
        "authors": [
            {
                "name": "Sandra Marques",
                "civility": "Mme",
                "party": "PLR",
                "role": "motionnaire",
            }
        ],
        "document_date": "2025-03-27",
        "deposit_date": "2025-03-27",
        "requested_action": (
            "Réduire de 100 à 80 le nombre de membres du Conseil communal "
            "dès la législature débutant en juillet 2026."
        ),
        "status_normalized": "rejected",
        "status_label": "Prise en considération refusée",
        "decision_date": "2025-03-27",
        "has_response": False,
        "has_dedicated_response": False,
        "response_status": "no_response_rejected_at_deposit",
        "is_closed": True,
    },
    "precarite-energetique": {
        "object_id": "vevey-motion-2023-precarite-energetique",
        "object_title": (
            "Précarité énergétique : urgence et responsabilité de notre commune"
        ),
        "authors": [
            {
                "name": "Jimmy Schüler",
                "civility": "M.",
                "party": "da.",
                "role": "présentateur selon le procès-verbal",
            }
        ],
        "author_attribution": {
            "official_listing": "Alain Gonthier",
            "council_minutes": "Jimmy Schüler",
            "motion_text": "Groupe décroissance alternatives",
            "conflict_reviewed": True,
        },
        "document_date": "2023-01-30",
        "deposit_date": "2023-03-16",
        "requested_action": (
            "Mettre en place des aides urgentes pour les locataires touchés "
            "par la précarité énergétique et créer un compte d'aide dédié."
        ),
        "status_normalized": "closed_after_status_response",
        "status_label": "Classée après réponse dans le rapport 2025/P13",
        "decision_date": "2025-05-15",
        "response_date": "2025-05-15",
        "has_response": True,
        "has_dedicated_response": False,
        "response_status": "answered_in_status_report",
        "is_closed": True,
    },
    "conge-menstruel-menopause": {
        "object_id": "vevey-motion-2024-conge-menstruel-menopause",
        "object_title": (
            "Pour un congé menstruel et de ménopause intégré dans le "
            "règlement du personnel"
        ),
        "authors": [
            {
                "name": "Joëlle Minacci",
                "civility": "Mme",
                "party": "da.",
                "role": "motionnaire",
            },
            {
                "name": "Céline Amiguet",
                "civility": "Mme",
                "party": "PS",
                "role": "cosignataire",
            },
            {
                "name": "Emmanuelle Evéquoz",
                "civility": "Mme",
                "party": "Les Vert.e.s",
                "role": "cosignataire",
            },
        ],
        "document_date": "2024-06-20",
        "deposit_date": "2024-06-20",
        "requested_action": (
            "Reconnaître un droit au congé menstruel et de ménopause dans "
            "le règlement du personnel, précédé si possible d'une phase test."
        ),
        "status_normalized": "pending",
        "status_label": "En traitement",
        "decision_date": "2024-06-20",
        "current_deadline": "2027-03-31",
        "has_response": False,
        "has_dedicated_response": False,
        "response_status": "no_response_pending",
        "is_closed": False,
    },
    "soyons-ecoute": {
        "object_id": "vevey-motion-2025-soyons-ecoute",
        "object_title": "Soyons à l’écoute des veveysannes et des veveysans",
        "authors": [
            {
                "name": "Patrick Bertschy",
                "civility": "M.",
                "party": "PLR/Interpartis",
                "role": "motionnaire",
            }
        ],
        "supporters": [
            "Comité référendaire",
            "PLR Vevey",
            "Le Centre et Verts-Libéraux",
            "UDC",
            "En Avant Vevey",
            "Vevey Libre",
        ],
        "document_date": "2025-02-06",
        "deposit_date": "2025-02-06",
        "requested_action": (
            "Modifier les horaires et jours de perception des taxes de "
            "stationnement en surface à Vevey."
        ),
        "status_normalized": "answered_and_closed",
        "status_label": "Transformée en postulat, réponse 2026/RP18 disponible",
        "decision_date": "2025-06-19",
        "response_date": "2026-04-20",
        "current_object_type": "postulat",
        "transformation": {
            "from": "motion",
            "to": "postulat",
            "date": "2025-06-19",
        },
        "has_response": True,
        "has_dedicated_response": True,
        "response_status": "dedicated_report_preavis_available",
        "is_closed": True,
    },
}


SELECTIONS = {
    "consideration_report_only": (
        r"\A",
        r"\bAnnexe\s*:\s*mentionnée\b",
    ),
    "response_report_only": (
        r"\A",
        r"\bAnnexe\s*:\s*Postulat de M\.\s+Patrick Bertschy",
    ),
    "sandra_decision": (
        r"13\.2\.?\s+Motion de Mme Sandra Marques",
        r"\b14\.\s+Questions",
    ),
    "precarite_status": (
        r"Motion 3\.\s+M\.\s+Jimmy Schüler",
        r"Postulat 4\.\s+Mme Isabel Jerbia",
    ),
    "conge_status": (
        r"Motion 3\s*-\s*Mme Joëlle Minacci",
        r"Postulat 8\s*-\s*Mme Isabel Jerbia",
    ),
    "soyons_decision": (
        r"9\.1\.?\s+Prise en considération de la motion de M\.\s+Patrick Bertschy",
        r"\b9\.2\.",
    ),
}


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def compact(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def clean_text(value: str) -> str:
    lines: list[str] = []
    for raw_line in value.splitlines():
        line = compact(raw_line)
        if not line:
            if lines and lines[-1]:
                lines.append("")
            continue
        if re.fullmatch(r"(?:page\s*)?\d+\s*(?:/|sur)\s*\d+", line, re.I):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def extract_pdf(path: Path) -> tuple[str, dict]:
    with fitz.open(path) as pdf:
        page_texts = [page.get_text("text") for page in pdf]
        image_counts = [len(page.get_images(full=True)) for page in pdf]
    text = clean_text("\n\n".join(page_texts))
    page_chars = [len(compact(page)) for page in page_texts]
    needs_ocr = len(text) < max(200, len(page_texts) * 50)
    return text, {
        "page_count": len(page_texts),
        "page_text_characters": page_chars,
        "image_counts": image_counts,
        "empty_pages": sum(not count for count in page_chars),
        "characters_extracted": len(text),
        "words_extracted": len(re.findall(r"\S+", text)),
        "needs_ocr": needs_ocr,
        "extraction_method": "native_pdf" if not needs_ocr else "ocr_required",
    }


def selected_text(text: str, method: str) -> str:
    if method == "whole_document":
        return text
    try:
        start_pattern, end_pattern = SELECTIONS[method]
    except KeyError as error:
        raise ValueError(f"Méthode de sélection inconnue: {method}") from error
    starts = list(re.finditer(start_pattern, text, re.I))
    if not starts:
        raise ValueError(f"Début de section introuvable pour {method}")
    # Council minutes repeat the agenda near the beginning. The final
    # occurrence is the substantive debate/decision section.
    start = starts[-1]
    end = re.search(end_pattern, text[start.end() :], re.I)
    if not end:
        raise ValueError(f"Fin de section introuvable pour {method}")
    return text[start.start() : start.end() + end.start()].strip()


def split_chunks(value: str) -> list[str]:
    words = re.findall(r"\S+", value)
    chunks: list[str] = []
    start = 0
    while start < len(words):
        end = min(len(words), start + MAX_WORDS)
        chunks.append(" ".join(words[start:end]))
        if end == len(words):
            break
        start = end - OVERLAP_WORDS
    return chunks


def component_for_role(role: str) -> str:
    return {
        "motion_text": "motion_text",
        "consideration_report": "commission_report",
        "council_decision": "council_decision",
        "municipal_response": "municipal_response",
        "status_report": "status_update",
    }[role]


def relationships(
    document: dict,
    all_documents: list[dict],
    profile: dict,
) -> dict:
    linked = [
        item
        for item in all_documents
        if item["political_object_key"] == document["political_object_key"]
    ]
    return {
        "political_object_id": profile["object_id"],
        "political_object_document_id": next(
            item["document_id"]
            for item in linked
            if item["document_role"] == "motion_text"
        ),
        "related_document_ids": [
            item["document_id"]
            for item in linked
            if item["document_id"] != document["document_id"]
        ],
        "response_document_ids": [
            item["document_id"]
            for item in linked
            if (
                item["document_role"] == "municipal_response"
                or (
                    document["political_object_key"]
                    == "precarite-energetique"
                    and item["document_role"] == "status_report"
                )
            )
        ],
        "decision_document_ids": [
            item["document_id"]
            for item in linked
            if item["document_role"] == "council_decision"
        ],
        "consideration_report_ids": [
            item["document_id"]
            for item in linked
            if item["document_role"] == "consideration_report"
        ],
        "status_report_ids": [
            item["document_id"]
            for item in linked
            if item["document_role"] == "status_report"
        ],
    }


def metadata_record(
    document: dict,
    all_documents: list[dict],
    extraction: dict,
    text: str,
) -> dict:
    profile = OBJECTS[document["political_object_key"]]
    source_title = str(document.get("title") or "").strip()
    title = (
        profile["object_title"]
        if document["document_role"] == "motion_text"
        else source_title
    )
    base = {
        "document_id": document["document_id"],
        "commune": "Vevey",
        "document_family": "political_object",
        "category": "motion",
        "document_role": document["document_role"],
        "title": title,
        "source_title": source_title,
        "source_page_url": document["source_page"],
        "file_url": document["pdf_url"],
        "listing_year": int(document["listing_year"]),
        "legislature": "2021-2026",
        "document_date": document["document_date"],
        "language": "fr",
        "content_hash": hashlib.sha256(
            compact(text).encode("utf-8")
        ).hexdigest(),
        "source_content_hash": document["sha256"],
        "extraction_method": extraction["extraction_method"],
        "processing_status": (
            "needs_ocr" if extraction["needs_ocr"] else "validated"
        ),
        "canonical": True,
    }
    motion_metadata = {
        "type": "motion",
        "object_id": profile["object_id"],
        "object_title": profile["object_title"],
        "authors": profile["authors"],
        "deposit_date": profile["deposit_date"],
        "requested_action": profile["requested_action"],
        "status_normalized": profile["status_normalized"],
        "status": profile["status_label"],
        "decision_date": profile.get("decision_date"),
        "response_date": profile.get("response_date"),
        "current_deadline": profile.get("current_deadline"),
        "current_object_type": profile.get("current_object_type", "motion"),
        "transformation": profile.get("transformation"),
        "has_response": profile["has_response"],
        "has_dedicated_response": profile["has_dedicated_response"],
        "response_status": profile["response_status"],
        "is_closed": profile["is_closed"],
        "document_component_role": document["document_role"],
        "supporters": profile.get("supporters", []),
        "author_attribution": profile.get("author_attribution"),
    }
    motion_metadata = {
        key: value
        for key, value in motion_metadata.items()
        if value not in (None, [], {})
    }
    return {
        "document_metadata": base,
        "motion_metadata": motion_metadata,
        "relationships": relationships(document, all_documents, profile),
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
                    "title": source_title,
                    "file_url": document["pdf_url"],
                }
            ],
        },
        "processing": {
            "text_extraction_status": extraction,
            "selected_text": {
                "method": document["selection"],
                "characters": len(text),
                "words": len(re.findall(r"\S+", text)),
            },
            "ocr": {
                "checked": True,
                "required": extraction["needs_ocr"],
                "provider": (
                    "mistral-ocr-latest"
                    if extraction["needs_ocr"]
                    else None
                ),
            },
        },
    }


def chunks_for(record: dict, text: str) -> list[dict]:
    base = record["document_metadata"]
    component = component_for_role(base["document_role"])
    chunks = []
    for index, content in enumerate(split_chunks(text)):
        chunks.append(
            {
                "chunk_id": f"{base['document_id']}#chunk-{index:03d}",
                "document_id": base["document_id"],
                "chunk_index": index,
                "section_index": 0,
                "component": component,
                "section_title": base["title"],
                "content": content,
                "word_count": len(re.findall(r"\S+", content)),
                "chunk_hash": hashlib.sha256(
                    content.encode("utf-8")
                ).hexdigest(),
                "quality": "green",
                "quality_issues": [],
            }
        )
    return chunks


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


def detail_page(record: dict, text: str, chunks: list[dict]) -> str:
    base = record["document_metadata"]
    return f"""<!doctype html><html lang="fr"><head><meta charset="utf-8">
<title>{html.escape(base["title"])}</title><style>{CSS}</style></head><body><main>
<p><a href="../index.html">← Audit des motions</a></p>
<h1>{html.escape(base["title"])}</h1>
<p><a href="{html.escape(base["file_url"])}">PDF officiel</a></p>
<section><h2>Métadonnées</h2><pre>{html.escape(json.dumps(record, ensure_ascii=False, indent=2))}</pre></section>
<section><h2>Texte retenu</h2><pre>{html.escape(text)}</pre></section>
<section><h2>Chunks</h2><pre>{html.escape(json.dumps(chunks, ensure_ascii=False, indent=2))}</pre></section>
</main></body></html>"""


def main() -> None:
    inventory = json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))
    documents = inventory["documents"]
    if len(documents) != 10:
        raise SystemExit(f"10 documents canoniques attendus, reçu {len(documents)}")

    for directory in (METADATA_DIR, TEXT_DIR, CHUNKS_DIR, DETAIL_DIR):
        directory.mkdir(parents=True, exist_ok=True)
        for stale in directory.glob("*"):
            if stale.is_file():
                stale.unlink()

    summaries = []
    totals = Counter()
    for document in documents:
        pdf_path = Path(document["local_pdf"])
        if not pdf_path.is_absolute():
            pdf_path = PROJECT_ROOT / pdf_path
        raw_text, extraction = extract_pdf(pdf_path)
        text = selected_text(raw_text, document["selection"])
        record = metadata_record(document, documents, extraction, text)
        chunks = chunks_for(record, text)
        document_id = document["document_id"]
        write_json(METADATA_DIR / f"{document_id}.json", record)
        write_json(CHUNKS_DIR / f"{document_id}.json", chunks)
        (TEXT_DIR / f"{document_id}.txt").write_text(
            text + "\n", encoding="utf-8"
        )
        (DETAIL_DIR / f"{document_id}.html").write_text(
            detail_page(record, text, chunks), encoding="utf-8"
        )
        motion = record["motion_metadata"]
        totals["documents"] += 1
        totals["chunks"] += len(chunks)
        totals["ocr_required"] += int(extraction["needs_ocr"])
        totals["motion_texts"] += int(
            document["document_role"] == "motion_text"
        )
        summaries.append(
            {
                "document_id": document_id,
                "object_id": motion["object_id"],
                "title": record["document_metadata"]["title"],
                "role": document["document_role"],
                "document_date": document["document_date"],
                "has_response": motion["has_response"],
                "response_status": motion["response_status"],
                "status": motion["status"],
                "file_url": document["pdf_url"],
                "chunks": len(chunks),
                "needs_ocr": extraction["needs_ocr"],
            }
        )

    object_rows = []
    for key, profile in OBJECTS.items():
        linked = [
            item
            for item in summaries
            if item["object_id"] == profile["object_id"]
        ]
        object_rows.append(
            {
                "object_key": key,
                "object_id": profile["object_id"],
                "title": profile["object_title"],
                "authors": profile["authors"],
                "status_normalized": profile["status_normalized"],
                "status": profile["status_label"],
                "has_response": profile["has_response"],
                "has_dedicated_response": profile["has_dedicated_response"],
                "response_status": profile["response_status"],
                "response_date": profile.get("response_date"),
                "current_deadline": profile.get("current_deadline"),
                "documents": [
                    {
                        "document_id": item["document_id"],
                        "role": item["role"],
                        "file_url": item["file_url"],
                    }
                    for item in linked
                ],
            }
        )
    audit = {
        "schema_version": "vevey-motions-general-audit-v1",
        "scope": inventory["scope"],
        "source": inventory["source"],
        "summary": {
            **dict(totals),
            "political_objects": len(OBJECTS),
            "objects_with_response": sum(
                profile["has_response"] for profile in OBJECTS.values()
            ),
            "objects_without_response": sum(
                not profile["has_response"]
                for profile in OBJECTS.values()
            ),
            "dedicated_responses": sum(
                profile["has_dedicated_response"]
                for profile in OBJECTS.values()
            ),
            "catalogue_false_positives": inventory[
                "listing_diagnostics"
            ]["false_positive_occurrences"],
        },
        "chunking": {
            "max_words": MAX_WORDS,
            "overlap_words": OVERLAP_WORDS,
            "embedding_recipe": "political_object",
            "embedding_model": "mistral-embed",
        },
        "political_objects": object_rows,
        "documents": summaries,
    }
    write_json(OUTPUT_DIR / "audit.json", audit)
    stats = "".join(
        f"<div class='stat'><strong>{value}</strong>{html.escape(key.replace('_', ' '))}</div>"
        for key, value in audit["summary"].items()
        if isinstance(value, int)
    )
    rows = "".join(
        f"<tr class='{'yes' if row['has_response'] else 'no'}'>"
        f"<td>{html.escape(row['title'])}</td><td>{html.escape(row['status'])}</td>"
        f"<td>{'Oui' if row['has_response'] else 'Non'}</td>"
        f"<td>{html.escape(row['response_status'])}</td>"
        f"<td>{len(row['documents'])}</td></tr>"
        for row in object_rows
    )
    page = f"""<!doctype html><html lang="fr"><head><meta charset="utf-8">
<title>Audit des motions de Vevey</title><style>{CSS}</style></head><body><main>
<h1>Motions de Vevey — législature 2021–2026</h1>
<p>Les faux positifs du filtre officiel sont exclus. Une réponse dédiée, une réponse dans un rapport d'état, une décision de refus et un dossier encore pendant sont distingués.</p>
<section class="stats">{stats}</section>
<table><thead><tr><th>Motion</th><th>Statut</th><th>Réponse</th><th>Type de réponse</th><th>Documents</th></tr></thead>
<tbody>{rows}</tbody></table></main></body></html>"""
    (OUTPUT_DIR / "index.html").write_text(page, encoding="utf-8")
    print(json.dumps(audit["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
