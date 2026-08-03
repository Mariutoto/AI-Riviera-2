from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import fitz
import lxml.html
import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LISTING_PAGE = "https://veytaux.ch/motions-postulats-interpellations"
HEADERS = {
    "User-Agent": (
        "AI-Riviera/1.0 Veytaux council-document research "
        "(public-interest indexing)"
    )
}
LEGISLATURE_START = date(2021, 7, 1)
LEGISLATURE_END = date(2026, 6, 30)

MONTHS = {
    "janvier": 1, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6,
    "juillet": 7, "aout": 8, "septembre": 9, "octobre": 10, "novembre": 11,
    "decembre": 12,
}

# The site is a single hand-edited rich-text page, not a database-backed
# listing: no object ids, no status field, no structured author field.
# The parser below only relies on the two regularities that actually hold
# across every entry: a "<category>(s) ... du <date>" paragraph groups a
# batch of objects deposited the same day, and each <li> under it starts
# with the original text as its first link, followed by zero or more
# follow-up links (response / resolution / préavis) in document order.


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


def parse_french_date(value: str) -> str:
    match = re.search(
        r"(\d{1,2})(?:er)?\s+([a-z]+)\s+(20\d{2})", ascii_text(value)
    )
    if not match or match.group(2) not in MONTHS:
        return ""
    day, month_name, year = match.groups()
    return date(int(year), MONTHS[month_name], int(day)).isoformat()


def category_and_date(paragraph_text: str) -> tuple[str, str]:
    text = compact(paragraph_text)
    match = re.match(r"(Interpellations?|Motions?|Postulats?)\b", text)
    category = match.group(1).lower().rstrip("s") if match else ""
    dates = re.findall(
        r"\bdu\s+(\d{1,2}(?:er)?\s+[A-Za-zéûôîâ]+\s+20\d{2})", text
    )
    deposit_date = parse_french_date(dates[-1]) if dates else ""
    return category, deposit_date


def classify_document_role(title: str) -> str:
    normalized = normalized_words(title)
    if "resolution" in normalized:
        return "resolution"
    if "reponse" in normalized or "preavis" in normalized:
        return "municipal_response"
    return None  # only ever used for the first (original-text) link


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", ascii_text(value)).strip("-")[:60]


def fetch_pdf(url: str) -> tuple[bytes, dict]:
    response = requests.get(url, headers=HEADERS, timeout=90)
    response.raise_for_status()
    content = response.content
    if not content.startswith(b"%PDF"):
        raise ValueError(f"Ressource non PDF: {url}")
    with fitz.open(stream=content, filetype="pdf") as pdf:
        page_texts = [page.get_text("text") for page in pdf]
    text = "\n\n".join(page_texts).strip()
    needs_ocr = len(compact(text)) < max(300, len(page_texts) * 100)
    return content, {
        "page_count": len(page_texts),
        "text_characters": len(text),
        "needs_ocr": needs_ocr,
    }


def collect_objects() -> list[dict]:
    response = requests.get(LISTING_PAGE, headers=HEADERS, timeout=45)
    response.raise_for_status()
    document = lxml.html.fromstring(response.content.decode("utf-8"))
    content_block = document.xpath("//div[contains(@class,'page-custom-content')]")[0]

    objects: list[dict] = []
    current_category = ""
    current_date = ""
    for node in content_block:
        tag = node.tag
        if tag == "p":
            text = node.text_content()
            if not compact(text):
                continue
            current_category, current_date = category_and_date(text)
            continue
        if tag != "ul" or not current_category:
            continue
        if not (LEGISLATURE_START.isoformat() <= (current_date or "9999") <= LEGISLATURE_END.isoformat()):
            continue
        for item in node.xpath("./li"):
            anchors = item.xpath(".//a[@href]")
            if not anchors:
                continue
            main = anchors[0]
            main_href = urljoin(LISTING_PAGE, main.get("href"))
            main_title = compact(main.text_content())
            object_key = slugify(Path(main_href).stem) or hashlib.sha1(main_href.encode()).hexdigest()[:10]
            documents = [
                {
                    "role": f"{current_category}_text",
                    "title": main_title,
                    "pdf_url": main_href,
                    "document_date": current_date,
                }
            ]
            for extra in anchors[1:]:
                href = urljoin(LISTING_PAGE, extra.get("href"))
                title = compact(extra.text_content())
                role = classify_document_role(title) or "attachment"
                response_date = parse_french_date(title) or current_date
                documents.append(
                    {
                        "role": role,
                        "title": title,
                        "pdf_url": href,
                        "document_date": response_date,
                        "doc_key": slugify(Path(href).stem),
                    }
                )
            objects.append(
                {
                    "object_key": object_key,
                    "object_id": f"veytaux-{current_category}-{object_key}",
                    "category": current_category,
                    "title": main_title,
                    "deposit_date": current_date,
                    "source_page": LISTING_PAGE,
                    "documents": documents,
                }
            )
    return objects


def build_inventory(category: str, output_dir: Path, all_objects: list[dict]) -> dict:
    scoped = [item for item in all_objects if item["category"] == category]
    documents: list[dict] = []
    for item in scoped:
        for spec in item["documents"]:
            suffix = {
                f"{category}_text": "text",
                "municipal_response": "response",
                "resolution": "resolution",
                "attachment": "attachment",
            }[spec["role"]]
            document_id = (
                f"veytaux_{category}_{item['object_key']}_{suffix}"
                if suffix == "text"
                else f"veytaux_{category}_{item['object_key']}_{suffix}_{spec['doc_key']}"
            )
            content, audit = fetch_pdf(spec["pdf_url"])
            target = output_dir / f"{document_id}.pdf"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            documents.append(
                {
                    "document_id": document_id,
                    "document_role": spec["role"],
                    "political_object_ids": [item["object_id"]],
                    "title": spec["title"],
                    "source_title": item["title"],
                    "source_page": item["source_page"],
                    "pdf_url": spec["pdf_url"],
                    "document_date": spec["document_date"] or item["deposit_date"],
                    "local_pdf": str(target.relative_to(PROJECT_ROOT)).replace("\\", "/"),
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "text_audit": audit,
                }
            )

    objects = []
    for item in scoped:
        response_ids = [
            d["document_id"] for d in documents
            if d["political_object_ids"] == [item["object_id"]] and d["document_role"] == "municipal_response"
        ]
        resolution_ids = [
            d["document_id"] for d in documents
            if d["political_object_ids"] == [item["object_id"]] and d["document_role"] == "resolution"
        ]
        objects.append(
            {
                "object_id": item["object_id"],
                "title": item["title"],
                "deposit_date": item["deposit_date"],
                "source_page": item["source_page"],
                "authors": [],
                "response_document_ids": response_ids,
                "resolution_document_ids": resolution_ids,
                "has_response": bool(response_ids),
                "has_resolution": bool(resolution_ids),
                "response_status": "written_response" if response_ids else "unanswered",
                "is_closed": bool(response_ids),
            }
        )

    return {
        "schema_version": f"veytaux-{category}s-inventory-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "institution": "Conseil communal de Veytaux",
            "listing_page": LISTING_PAGE,
        },
        "scope": {
            "commune": "Veytaux",
            "category": category,
            "legislature": "2021-2026",
            "start_date": LEGISLATURE_START.isoformat(),
            "end_date": LEGISLATURE_END.isoformat(),
            "note": (
                "Page éditée manuellement par la commune (pas de base de "
                "données) : aucun identifiant d'objet, statut ou auteur "
                "structuré n'est publié — seuls le titre du lien, le "
                "regroupement par date de dépôt et l'ordre des documents "
                "sont exploitables de façon fiable."
            ),
        },
        "diagnostics": {
            "complete": True,
            f"legislature_{category}s": len(objects),
            "documents_downloaded": len(documents),
            "objects_with_response": sum(o["has_response"] for o in objects),
            "objects_without_response": sum(not o["has_response"] for o in objects),
            "documents_needing_ocr": sum(d["text_audit"]["needs_ocr"] for d in documents),
        },
        "objects": objects,
        "documents": documents,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape les objets politiques de Veytaux 2021-2026")
    parser.add_argument("--categories", nargs="+", default=["interpellation", "motion"])
    args = parser.parse_args()

    all_objects = collect_objects()
    for category in args.categories:
        root = PROJECT_ROOT / "audit-veytaux" / f"{category}s-2021-2026"
        inventory = build_inventory(category, root / "pdfs", all_objects)
        write_json(root / "inventory.json", inventory)
        print(category, json.dumps(inventory["diagnostics"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
