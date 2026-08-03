from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import importlib.util
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BASE_SCRIPT = PROJECT_ROOT / "scrape-blonay-saint-legier" / "scrape_interpellations_2021_2026.py"
SPEC = importlib.util.spec_from_file_location("blonay_saint_legier_interpellation_source", BASE_SCRIPT)
assert SPEC and SPEC.loader
base = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(base)

BASE_URL = base.BASE_URL
LISTING_PAGE = base.LISTING_PAGE
LEGISLATURE_START = base.LEGISLATURE_START
LEGISLATURE_END = base.LEGISLATURE_END
OBJECT_CATEGORY = "Postulat"


def parse_authors_with_roles(dd_html: str) -> tuple[list[dict], list[int]]:
    """Unlike interpellations (always a single lone depositor), a postulat's
    "Auteur" field can list commission members (Président·e, Rapporteur) once
    a commission has been formed, instead of — or alongside — the original
    depositor. Keep whatever role label the site actually shows rather than
    assuming everyone listed is the postulant.
    """
    entries = re.findall(
        r'<a href="/_rte/person/(\d+)"[^>]*>\s*([^<]*?)\s*</a>\s*'
        r'(?:\(((?:[^()]|\([^()]*\))*)\))?',
        dd_html,
    )
    if entries:
        authors = [
            {
                "name": base.compact(name),
                "person_id": int(pid),
                "role": base.compact(role) if role else "Auteur/e",
            }
            for pid, name, role in entries
        ]
        return authors, [author["person_id"] for author in authors]
    plain_text = base.strip_tags(dd_html)
    plain_text = re.sub(r"\((?:Auteur/e|Aucune fonction)\s*\)", "", plain_text).strip()
    if plain_text:
        return [{"name": plain_text, "person_id": None, "role": "Auteur/e"}], []
    return [], []


base.parse_authors = parse_authors_with_roles


def classify_document_role(title: str) -> str:
    normalized = base.normalized_words(title)
    if "reponse" in normalized:
        return "municipal_response"
    if "rapport" in normalized and "commission" in normalized:
        return "consideration_report"
    if "extrait" in normalized and re.search(r"\bdeci", normalized):
        return "council_decision"
    if "formation" in normalized and "commission" in normalized:
        return "committee_formation"
    if normalized.startswith("postulat"):
        return "postulate_text"
    return "attachment"


def build_inventory(output_dir: Path) -> dict:
    session = requests.Session()
    index_rows, index_diagnostics = base.collect_index(session)
    postulats = [row for row in index_rows if row["category"] == OBJECT_CATEGORY]
    selected_ids = sorted(row["source_object_id"] for row in postulats)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        details = list(
            pool.map(lambda oid: base.fetch_detail(session, oid), selected_ids)
        )
    details_by_id = {detail["source_object_id"]: detail for detail in details}

    person_ids = sorted({pid for detail in details for pid in detail["author_person_ids"]})
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        parties = dict(
            zip(person_ids, pool.map(lambda pid: base.fetch_person_party(session, pid), person_ids))
        )
    for detail in details:
        for author in detail["authors"]:
            if author["person_id"] is not None:
                author["party"] = parties.get(author["person_id"], "")

    objects: list[dict] = []
    download_jobs: list[dict] = []
    role_to_field = {
        "municipal_response": "response_document_ids",
        "consideration_report": "consideration_document_ids",
        "council_decision": "decision_document_ids",
    }
    for row in sorted(postulats, key=lambda item: item["source_object_id"]):
        source_id = row["source_object_id"]
        detail = details_by_id[source_id]
        deposit_date = detail["deposit_date"] or row["listing_date"]
        object_id = f"blonay-saint-legier-postulate-{source_id}"
        object_record = {
            "source_object_id": source_id,
            "object_id": object_id,
            "nummer": row["nummer"],
            "source_page": detail["source_page"],
            "title": row["title"],
            "authors": detail["authors"],
            "status": detail["status"],
            "status_normalized": detail["status_normalized"],
            "deposit_date": deposit_date,
            "associated_object_ids": detail["associated_object_ids"],
            "response_document_ids": [],
            "consideration_document_ids": [],
            "decision_document_ids": [],
        }
        objects.append(object_record)
        for attachment in detail["attachments"]:
            role = classify_document_role(attachment["title"])
            suffix = {
                "municipal_response": "response",
                "consideration_report": "report",
                "council_decision": "decision",
                "committee_formation": "committee",
                "postulate_text": "text",
                "attachment": "attachment",
            }[role]
            document_id = (
                f"blonay-saint-legier_postulate_{source_id}_{suffix}_"
                f"{attachment['download_id']}"
            )
            download_jobs.append(
                {
                    **attachment,
                    "document_id": document_id,
                    "document_role": role,
                    "political_object_ids": [object_id],
                    "source_page": detail["source_page"],
                    "source_title": row["title"],
                    "document_date": deposit_date,
                }
            )

    def process(spec: dict) -> dict:
        content, source_format = base.download_attachment(spec)
        text, audit = base.pdf_text(content)
        target = output_dir / f"{spec['document_id']}.pdf"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return {
            **spec,
            "local_pdf": str(target.relative_to(PROJECT_ROOT)).replace("\\", "/"),
            "sha256": hashlib.sha256(content).hexdigest(),
            "source_format": source_format,
            "text_audit": audit,
        }

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        documents = list(pool.map(process, download_jobs))

    by_object = {item["object_id"]: item for item in objects}
    for document in documents:
        field = role_to_field.get(document["document_role"])
        if not field:
            continue
        for object_id in document["political_object_ids"]:
            by_object[object_id][field].append(document["document_id"])
    for item in objects:
        item["has_postulate_text"] = any(
            document["political_object_ids"] == [item["object_id"]]
            and document["document_role"] == "postulate_text"
            for document in documents
        )
        item["has_response"] = bool(item["response_document_ids"])
        item["has_consideration_report"] = bool(item["consideration_document_ids"])
        item["has_council_decision"] = bool(item["decision_document_ids"])
        item["is_closed"] = item["has_response"] or item["status_normalized"] in {
            "rejected", "withdrawn"
        }
        item["response_status"] = "written_response" if item["has_response"] else "unanswered"

    diagnostics = {
        "complete": True,
        "total_site_entities": index_diagnostics["total_entities"],
        "legislature_postulates": len(objects),
        "objects_missing_original_text": sum(not item["has_postulate_text"] for item in objects),
        "documents_downloaded": len(documents),
        "documents_by_role": dict(Counter(d["document_role"] for d in documents)),
        "objects_with_response": sum(item["has_response"] for item in objects),
        "objects_without_response": sum(not item["has_response"] for item in objects),
        "documents_needing_ocr": sum(d["text_audit"]["needs_ocr"] for d in documents),
        "objects_with_cross_referenced_associations": sum(
            bool(item["associated_object_ids"]) for item in objects
        ),
    }
    return {
        "schema_version": "blonay-saint-legier-postulates-inventory-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "institution": "Conseil communal de Blonay–Saint-Légier",
            "listing_page": LISTING_PAGE,
        },
        "scope": {
            "commune": "Blonay–Saint-Légier",
            "category": "postulat",
            "legislature": "2021-2026",
            "start_date": LEGISLATURE_START.isoformat(),
            "end_date": LEGISLATURE_END.isoformat(),
            "note": (
                "La commune est issue de la fusion de Blonay et St-Légier-La Chiésaz "
                "au 1er juillet 2023 ; aucune donnée n'existe avant cette date."
            ),
        },
        "automatic_detection_notes": {
            "listing_source": "data-entities JSON embedded in /objets-politiques (client-side DataTable, no pagination)",
            "response_linking": (
                "self-contained: each object's own Document table carries its original text, "
                "any commission report, council decision, and municipal response — no separate "
                "response objects exist on this site"
            ),
            "author_field": (
                "the 'Auteur' field can list commission members (Président·e, Rapporteur) "
                "instead of, or alongside, the original depositor once a commission has been "
                "formed; each person's role label from the site is preserved as-is"
            ),
        },
        "diagnostics": diagnostics,
        "objects": objects,
        "documents": documents,
    }


def main() -> None:
    root = PROJECT_ROOT / "audit-blonay-saint-legier" / "postulats-2021-2026"
    parser = argparse.ArgumentParser(
        description="Scrape les postulats de Blonay-Saint-Légier 2021-2026"
    )
    parser.add_argument("--output", type=Path, default=root / "inventory.json")
    parser.add_argument("--download-dir", type=Path, default=root / "pdfs")
    args = parser.parse_args()
    inventory = build_inventory(args.download_dir)
    base.write_json(args.output, inventory)
    print(json.dumps(inventory["diagnostics"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
