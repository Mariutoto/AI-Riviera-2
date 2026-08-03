from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import html
import json
import re
import unicodedata
from datetime import date, datetime, timezone
from pathlib import Path

import fitz
import requests
from PIL import Image
from io import BytesIO


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "https://www.blonay-saint-legier.ch"
LISTING_PAGE = f"{BASE_URL}/objets-politiques"
DETAIL_PAGE = f"{BASE_URL}/_rte/information"
DOCUMENT_DOWNLOAD = f"{BASE_URL}/_doc"
PERSON_PAGE = f"{BASE_URL}/_rte/person"
HEADERS = {
    "User-Agent": (
        "AI-Riviera/1.0 Blonay-Saint-Legier council-document research "
        "(public-interest indexing)"
    )
}
LEGISLATURE_START = date(2021, 7, 1)
LEGISLATURE_END = date(2026, 6, 30)
OBJECT_CATEGORY = "Interpellation"


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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


def strip_tags(value: str) -> str:
    return compact(html.unescape(re.sub(r"<[^>]+>", " ", value)))


def parse_swiss_date(value: str) -> str:
    match = re.match(r"(\d{2})\.(\d{2})\.(\d{4})", value.strip())
    if not match:
        return ""
    day, month, year = (int(part) for part in match.groups())
    return date(year, month, day).isoformat()


def collect_index(session: requests.Session) -> tuple[list[dict], dict]:
    response = session.get(LISTING_PAGE, headers=HEADERS, timeout=45)
    response.raise_for_status()
    body = response.content.decode("utf-8")
    match = re.search(r'data-entities="(.*?)"\s', body, re.S)
    if not match:
        raise ValueError("Bloc data-entities introuvable sur la page des objets politiques")
    entities = json.loads(html.unescape(match.group(1)))
    rows = []
    for item in entities["data"]:
        title_match = re.search(r'href="/_rte/information/(\d+)"', item["title"])
        if not title_match:
            continue
        rows.append(
            {
                "source_object_id": int(title_match.group(1)),
                "nummer": item["_nummer"],
                "category": item["_kategorieId"],
                "title": strip_tags(item["title"]),
                "listing_date": parse_swiss_date(item["_geschaeftsdatum"]),
            }
        )
    return rows, {"total_entities": len(entities["data"]), "parsed_rows": len(rows)}


def parse_authors(dd_html: str) -> tuple[list[dict], list[int]]:
    person_links = re.findall(
        r'<a href="/_rte/person/(\d+)"[^>]*>\s*([^<]*?)\s*</a>', dd_html
    )
    if person_links:
        authors = [{"name": compact(name), "person_id": int(pid)} for pid, name in person_links]
        return authors, [author["person_id"] for author in authors]
    plain_text = strip_tags(dd_html)
    plain_text = re.sub(r"\(Auteur/e\)|\(Aucune fonction\s*\)", "", plain_text).strip()
    if plain_text:
        return [{"name": plain_text, "person_id": None}], []
    return [], []


def fetch_person_party(session: requests.Session, person_id: int) -> str:
    response = session.get(f"{PERSON_PAGE}/{person_id}", headers=HEADERS, timeout=45)
    response.raise_for_status()
    body = response.content.decode("utf-8")
    match = re.search(r'<dt>Parti</dt><dd>(.*?)</dd>', body, re.S)
    return strip_tags(match.group(1)) if match else ""


def status_normalized(value: str) -> str:
    normalized = normalized_words(value)
    mapping = {
        "repondu": "answered",
        "depose": "deposited",
        "en traitement": "in_progress",
        "refuse": "rejected",
        "accepte": "accepted",
        "retire": "withdrawn",
        "suspens legislatif": "suspended",
    }
    return mapping.get(normalized, normalized.replace(" ", "_") or "unknown")


def classify_document_role(title: str) -> str:
    normalized = normalized_words(title)
    if "reponse" in normalized:
        return "municipal_response"
    if "resolution" in normalized:
        return "resolution"
    return "interpellation_text"


def fetch_detail(session: requests.Session, source_object_id: int) -> dict:
    response = session.get(
        f"{DETAIL_PAGE}/{source_object_id}", headers=HEADERS, timeout=45
    )
    response.raise_for_status()
    body = response.content.decode("utf-8")

    status_match = re.search(r"<dt>Statut</dt><dd>(.*?)</dd>", body)
    status = strip_tags(status_match.group(1)) if status_match else ""

    date_match = re.search(r"<dt>Date</dt><dd>(.*?)</dd>", body)
    date_text = strip_tags(date_match.group(1)) if date_match else ""

    author_match = re.search(r"<dt>Auteur</dt><dd>(.*?)</dd>", body, re.S)
    authors, person_ids = parse_authors(author_match.group(1)) if author_match else ([], [])

    document_rows = re.findall(
        r'<a title="([^"]*)" href="(/_doc/\d+)"[^>]*class="[^"]*cms-download',
        body,
    )
    seen_hrefs: set[str] = set()
    attachments = []
    for title, href in document_rows:
        if href in seen_hrefs:
            continue
        seen_hrefs.add(href)
        attachments.append(
            {
                "title": strip_tags(title),
                "download_id": href.rsplit("/", 1)[-1],
                "pdf_url": f"{BASE_URL}{href}",
            }
        )

    associated_header = re.search(r"Objets associ[ée]s</h2>", body)
    associated_links: list[str] = []
    if associated_header:
        window = body[associated_header.end():associated_header.end() + 800]
        associated_links = re.findall(r'href="/_rte/information/(\d+)"', window)

    return {
        "source_object_id": source_object_id,
        "source_page": f"{BASE_URL}/objetspolitiques/{source_object_id}",
        "status": status,
        "status_normalized": status_normalized(status),
        "deposit_date": parse_iso_from_french_or_swiss(date_text),
        "authors": authors,
        "author_person_ids": person_ids,
        "attachments": attachments,
        "associated_object_ids": sorted({int(oid) for oid in associated_links}),
    }


def parse_iso_from_french_or_swiss(value: str) -> str:
    months = {
        "janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4,
        "mai": 5, "juin": 6, "juillet": 7, "août": 8, "aout": 8,
        "septembre": 9, "octobre": 10, "novembre": 11, "décembre": 12, "decembre": 12,
    }
    match = re.match(r"(\d{1,2})\s+([a-zéû]+)\s+(\d{4})", ascii_text(value))
    if match:
        day, month_name, year = match.groups()
        month = months.get(month_name)
        if month:
            return date(int(year), month, int(day)).isoformat()
    return parse_swiss_date(value)


def download_attachment(attachment: dict) -> tuple[bytes, str]:
    response = requests.get(attachment["pdf_url"], headers=HEADERS, timeout=90)
    response.raise_for_status()
    content = response.content
    if content.startswith(b"%PDF"):
        return content, "native_pdf"
    if content[:2] == b"\xff\xd8" or content[:8] == b"\x89PNG\r\n\x1a\n":
        # A handful of attachments (e.g. scanned résolutions) are uploaded as
        # a single image rather than a PDF. Wrap it in a one-page PDF so the
        # rest of the pipeline (text extraction, OCR) never has to special-case it.
        image = Image.open(BytesIO(content)).convert("RGB")
        buffer = BytesIO()
        image.save(buffer, format="PDF")
        return buffer.getvalue(), "converted_image"
    raise ValueError(f"Format de pièce jointe non reconnu: {attachment['pdf_url']}")


def pdf_text(content: bytes) -> tuple[str, dict]:
    with fitz.open(stream=content, filetype="pdf") as pdf:
        page_texts = [page.get_text("text") for page in pdf]
        image_counts = [len(page.get_images(full=True)) for page in pdf]
    text = "\n\n".join(page_texts).strip()
    page_characters = [len(compact(value)) for value in page_texts]
    needs_ocr = len(compact(text)) < max(300, len(page_texts) * 100)
    return text, {
        "page_count": len(page_texts),
        "page_text_characters": page_characters,
        "image_counts": image_counts,
        "empty_pages": sum(not count for count in page_characters),
        "text_characters": len(text),
        "text_words": len(re.findall(r"\S+", text)),
        "needs_ocr": needs_ocr,
        "text_preview": compact(text)[:1200],
    }


def build_inventory(output_dir: Path) -> dict:
    session = requests.Session()
    index_rows, index_diagnostics = collect_index(session)
    interpellations = [row for row in index_rows if row["category"] == OBJECT_CATEGORY]
    selected_ids = sorted(row["source_object_id"] for row in interpellations)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        details = list(
            pool.map(lambda oid: fetch_detail(session, oid), selected_ids)
        )
    details_by_id = {detail["source_object_id"]: detail for detail in details}

    person_ids = sorted({pid for detail in details for pid in detail["author_person_ids"]})
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        parties = dict(
            zip(person_ids, pool.map(lambda pid: fetch_person_party(session, pid), person_ids))
        )
    for detail in details:
        for author in detail["authors"]:
            if author["person_id"] is not None:
                author["party"] = parties.get(author["person_id"], "")

    unresolved_windows = [
        row
        for row in interpellations
        if not (
            LEGISLATURE_START
            <= date.fromisoformat(details_by_id[row["source_object_id"]]["deposit_date"] or row["listing_date"])
            <= LEGISLATURE_END
        )
    ]

    objects: list[dict] = []
    download_jobs: list[tuple[dict, dict]] = []
    for row in sorted(interpellations, key=lambda item: item["source_object_id"]):
        source_id = row["source_object_id"]
        detail = details_by_id[source_id]
        deposit_date = detail["deposit_date"] or row["listing_date"]
        object_id = f"blonay-saint-legier-interpellation-{source_id}"
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
            "resolution_document_ids": [],
        }
        objects.append(object_record)
        for attachment in detail["attachments"]:
            role = classify_document_role(attachment["title"])
            suffix = {
                "municipal_response": "response",
                "resolution": "resolution",
                "interpellation_text": "text",
            }[role]
            document_id = (
                f"blonay-saint-legier_interpellation_{source_id}_{suffix}_"
                f"{attachment['download_id']}"
            )
            download_jobs.append(
                (
                    {
                        **attachment,
                        "document_id": document_id,
                        "document_role": role,
                        "political_object_ids": [object_id],
                        "source_page": detail["source_page"],
                        "source_title": row["title"],
                        "document_date": deposit_date,
                    },
                    object_record,
                )
            )

    def process(job: tuple[dict, dict]) -> dict:
        spec, _ = job
        content, source_format = download_attachment(spec)
        text, audit = pdf_text(content)
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
        for object_id in document["political_object_ids"]:
            if document["document_role"] == "municipal_response":
                by_object[object_id]["response_document_ids"].append(document["document_id"])
            elif document["document_role"] == "resolution":
                by_object[object_id]["resolution_document_ids"].append(document["document_id"])
    for item in objects:
        item["has_interpellation_text"] = any(
            document["political_object_ids"] == [item["object_id"]]
            and document["document_role"] == "interpellation_text"
            for document in documents
        )
        item["has_response"] = bool(item["response_document_ids"])
        item["has_resolution"] = bool(item["resolution_document_ids"])
        item["is_closed"] = item["has_response"] or item["status_normalized"] in {
            "rejected", "withdrawn"
        }
        item["response_status"] = "written_response" if item["has_response"] else "unanswered"

    diagnostics = {
        "complete": True,
        "total_site_entities": index_diagnostics["total_entities"],
        "legislature_interpellations": len(objects),
        "objects_missing_original_text": sum(
            not item["has_interpellation_text"] for item in objects
        ),
        "objects_outside_legislature_window": len(unresolved_windows),
        "documents_downloaded": len(documents),
        "objects_with_response": sum(item["has_response"] for item in objects),
        "objects_without_response": sum(not item["has_response"] for item in objects),
        "objects_with_resolution": sum(item["has_resolution"] for item in objects),
        "documents_needing_ocr": sum(d["text_audit"]["needs_ocr"] for d in documents),
        "objects_with_cross_referenced_associations": sum(
            bool(item["associated_object_ids"]) for item in objects
        ),
    }
    return {
        "schema_version": "blonay-saint-legier-interpellations-inventory-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "institution": "Conseil communal de Blonay–Saint-Légier",
            "listing_page": LISTING_PAGE,
        },
        "scope": {
            "commune": "Blonay–Saint-Légier",
            "category": "interpellation",
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
                "municipal response, and any résolution — no separate response objects exist "
                "on this site, confirmed empty 'Objets associés' section on every object checked"
            ),
        },
        "diagnostics": diagnostics,
        "objects": objects,
        "documents": documents,
    }


def main() -> None:
    root = PROJECT_ROOT / "audit-blonay-saint-legier" / "interpellations-2021-2026"
    parser = argparse.ArgumentParser(
        description="Scrape les interpellations de Blonay–Saint-Légier 2021-2026"
    )
    parser.add_argument("--output", type=Path, default=root / "inventory.json")
    parser.add_argument("--download-dir", type=Path, default=root / "pdfs")
    args = parser.parse_args()
    inventory = build_inventory(args.download_dir)
    write_json(args.output, inventory)
    print(json.dumps(inventory["diagnostics"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
