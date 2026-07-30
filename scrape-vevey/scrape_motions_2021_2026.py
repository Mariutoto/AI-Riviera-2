from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
import unicodedata
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

import fitz
import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from municipal_pipeline.municipalities import VEVEY


SOURCE_PAGE = (
    "https://www.vevey.ch/vie-politique/conseil-communal/"
    "documents-du-conseil-communal"
)
DOWNLOAD_URL = "https://conseil.vevey.ch/ConseilCommunal/download.asp?d={download_id}"
YEARS = {"2021", "2022", "2023", "2024", "2025", "2026"}
PAGE_SIZE = 30
HEADERS = {"User-Agent": "AI-Riviera Vevey motions 2021-2026"}
DIV_TOKEN_RE = re.compile(r"<div\b[^>]*>|</div\s*>", re.I)

# The Vevey catalogue duplicates documents under several labels and URLs.
# These are the verified canonical follow-ups for motions filed in the
# 2021-2026 legislature. Large council minutes/status reports are later
# reduced to the section concerning the linked motion.
FOLLOW_UPS = [
    {
        "download_id": "5815",
        "political_object_key": "sandra-marques-conseil",
        "document_role": "council_decision",
        "reference": "PV 27.03.2025",
        "document_date": "2025-03-27",
        "title": (
            "Décision sur la motion « Diminuer le nombre d'élues/élus "
            "communaux pour permettre la rénovation de la salle du Conseil communal »"
        ),
        "selection": "sandra_decision",
    },
    {
        "download_id": "5793",
        "political_object_key": "precarite-energetique",
        "document_role": "status_report",
        "reference": "2025/P13",
        "document_date": "2025-05-15",
        "title": (
            "Classement de la motion « Précarité énergétique : urgence "
            "et responsabilité de notre commune »"
        ),
        "selection": "precarite_status",
    },
    {
        "download_id": "6109",
        "political_object_key": "conge-menstruel-menopause",
        "document_role": "status_report",
        "reference": "2026/P07",
        "document_date": "2026-05-07",
        "title": (
            "État de la motion « Pour un congé menstruel et de ménopause "
            "intégré dans le règlement du personnel »"
        ),
        "selection": "conge_status",
    },
    {
        "download_id": "5794",
        "political_object_key": "soyons-ecoute",
        "document_role": "consideration_report",
        "reference": "2025/R15",
        "document_date": "2025-05-15",
        "title": (
            "Prise en considération de la motion « Soyons à l’écoute "
            "des veveysannes et des veveysans »"
        ),
        "selection": "consideration_report_only",
    },
    {
        "download_id": "5903",
        "political_object_key": "soyons-ecoute",
        "document_role": "council_decision",
        "reference": "PV 19.06.2025",
        "document_date": "2025-06-19",
        "title": (
            "Décision transformant la motion « Soyons à l’écoute des "
            "veveysannes et des veveysans » en postulat"
        ),
        "selection": "soyons_decision",
    },
    {
        "download_id": "6189",
        "political_object_key": "soyons-ecoute",
        "document_role": "municipal_response",
        "reference": "2026/RP18",
        "document_date": "2026-04-20",
        "title": (
            "Rapport-préavis en réponse à « Soyons à l’écoute des "
            "veveysannes et des veveysans »"
        ),
        "selection": "response_report_only",
    },
]


def ascii_fold(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(
        character
        for character in decomposed
        if not unicodedata.combining(character)
    ).casefold()


def clean_html(value: str) -> str:
    without_tags = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", html.unescape(without_tags)).strip()


def extract_teaser_blocks(page_html: str) -> list[str]:
    blocks: list[str] = []
    position = 0
    while True:
        match = re.search(
            r'<div\b[^>]*class="[^"]*\bteaser-politique\b[^"]*"[^>]*>',
            page_html[position:],
            flags=re.I,
        )
        if not match:
            return blocks
        start = position + match.start()
        depth = 0
        end = None
        for token in DIV_TOKEN_RE.finditer(page_html, start):
            depth += -1 if token.group(0).lower().startswith("</div") else 1
            if depth == 0:
                end = token.end()
                break
        if end is None:
            raise ValueError("Bloc de document Vevey incomplet")
        blocks.append(page_html[start:end])
        position = end


def download_id(url: str) -> str:
    values = parse_qs(urlparse(url).query).get("d", [])
    return values[0] if values else ""


def parse_teaser(block: str) -> dict:
    left_column = re.search(
        r'<div[^>]*class="[^"]*\bcol-3\b[^"]*"[^>]*>(.*?)</div>',
        block,
        flags=re.I | re.S,
    )
    left_text = clean_html(left_column.group(1) if left_column else "")
    date_match = re.search(r"\b(\d{2}\.\d{2}\.\d{4})\b", left_text)
    if not date_match:
        raise ValueError("Date absente d'un résultat Vevey")
    listing_date = datetime.strptime(
        date_match.group(1), "%d.%m.%Y"
    ).date().isoformat()
    listing_author = left_text.replace(date_match.group(1), "", 1).strip()

    teaser_id = re.search(
        r'<p[^>]*\bteaser-id\b[^>]*>(.*?)</p>',
        block,
        flags=re.I | re.S,
    )
    id_parts = [
        clean_html(part)
        for part in re.findall(
            r"<span[^>]*>(.*?)</span>",
            teaser_id.group(1) if teaser_id else "",
            re.I | re.S,
        )
    ]
    displayed_type = id_parts[0] if id_parts else ""
    reference = id_parts[1] if len(id_parts) > 1 else ""

    heading = re.search(r"<h4[^>]*>(.*?)</h4>", block, flags=re.I | re.S)
    if not heading:
        raise ValueError("Titre absent d'un résultat Vevey")
    title = clean_html(heading.group(1))
    heading_link = re.search(r'href="([^"]+)"', heading.group(1), flags=re.I)
    links = [
        {
            "url": urljoin(SOURCE_PAGE, html.unescape(match.group("href"))),
            "label": clean_html(match.group("label")),
        }
        for match in re.finditer(
            r'<a\b[^>]*href="(?P<href>[^"]+)"[^>]*>(?P<label>.*?)</a>',
            block,
            flags=re.I | re.S,
        )
    ]
    pdf = next(
        (
            link
            for link in links
            if "telecharger pdf" in ascii_fold(link["label"])
        ),
        None,
    )
    pdf_url = (pdf or {}).get("url") or (
        urljoin(SOURCE_PAGE, html.unescape(heading_link.group(1)))
        if heading_link
        else ""
    )
    if not pdf_url:
        raise ValueError("Lien PDF absent d'un résultat Vevey")
    return {
        "city": VEVEY.label,
        "municipality_key": VEVEY.key,
        "displayed_type": displayed_type,
        "reference": reference,
        "title": title,
        "listing_author": listing_author,
        "listing_date": listing_date,
        "listing_year": listing_date[:4],
        "pdf_url": pdf_url,
        "source_download_id": download_id(pdf_url),
        "source_page": SOURCE_PAGE,
        "source_collection": "vevey-council-documents",
        "legislature": "2021-2026",
    }


def result_count(page_html: str) -> int:
    match = re.search(
        r'<h2[^>]*class="[^"]*\bh3\b[^"]*"[^>]*>\s*(\d+)\s+r[ée]sultats?',
        page_html,
        flags=re.I,
    )
    if not match:
        raise ValueError("Nombre de résultats Vevey introuvable")
    return int(match.group(1))


def fetch_page(page: int, session: requests.Session) -> str:
    response = session.get(
        SOURCE_PAGE,
        params={
            "type-desktop": "Motion",
            "submit-desktop": "Appliquer",
            "page": page,
        },
        headers=HEADERS,
        timeout=30,
    )
    response.raise_for_status()
    return response.text


def collect_catalogue(
    session: requests.Session | None = None,
) -> tuple[list[dict], list[dict], dict]:
    client = session or requests.Session()
    first_page = fetch_page(0, client)
    expected = result_count(first_page)
    pages = max(1, (expected + PAGE_SIZE - 1) // PAGE_SIZE)
    occurrences = [
        parse_teaser(block) for block in extract_teaser_blocks(first_page)
    ]
    for page in range(1, pages):
        occurrences.extend(
            parse_teaser(block)
            for block in extract_teaser_blocks(fetch_page(page, client))
        )
    unique = {
        (
            item["source_download_id"] or item["pdf_url"],
            item["listing_date"],
            item["title"],
        ): item
        for item in occurrences
    }
    scoped = [
        item
        for item in unique.values()
        if item["listing_year"] in YEARS
    ]
    candidates = [
        item
        for item in scoped
        if re.match(r"^\s*motion\b", ascii_fold(item["title"]))
    ]
    rejected = [
        {
            **item,
            "exclusion_reason": "catalogue_motion_filter_false_positive",
        }
        for item in scoped
        if item not in candidates
    ]
    diagnostics = {
        "endpoint_results": expected,
        "pages_fetched": pages,
        "parsed_occurrences": len(occurrences),
        "unique_occurrences": len(unique),
        "legislature_filter_occurrences": len(scoped),
        "motion_title_candidates": len(candidates),
        "false_positive_occurrences": len(rejected),
        "complete": len(unique) == expected,
    }
    if not diagnostics["complete"]:
        raise ValueError(
            f"Collecte incomplète: {len(unique)} occurrences sur {expected}"
        )
    return (
        sorted(candidates, key=lambda item: item["listing_date"]),
        sorted(rejected, key=lambda item: item["listing_date"]),
        diagnostics,
    )


def pdf_text(content: bytes) -> tuple[str, dict]:
    with fitz.open(stream=content, filetype="pdf") as pdf:
        pages = [page.get_text("text") for page in pdf]
        image_counts = [len(page.get_images(full=True)) for page in pdf]
    text = "\n\n".join(pages).strip()
    chars = len(re.sub(r"\s+", " ", text).strip())
    needs_ocr = chars < max(200, len(pages) * 50)
    return text, {
        "page_count": len(pages),
        "characters_extracted": chars,
        "image_counts": image_counts,
        "needs_ocr": needs_ocr,
        "extraction_method": "native_pdf" if not needs_ocr else "ocr_required",
    }


def download_documents(
    originals: list[dict],
    output_dir: Path,
    session: requests.Session | None = None,
) -> tuple[list[dict], dict]:
    client = session or requests.Session()
    output_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict] = []
    specs = [
        {
            **item,
            "political_object_key": {
                "5740": "sandra-marques-conseil",
                "5692": "soyons-ecoute",
                "5527": "conge-menstruel-menopause",
                "5063": "precarite-energetique",
            }.get(item["source_download_id"], ""),
            "document_role": "motion_text",
            "document_date": item["listing_date"],
            "selection": "whole_document",
        }
        for item in originals
    ] + [
        {
            **follow_up,
            "displayed_type": "",
            "listing_author": "",
            "listing_date": follow_up["document_date"],
            "listing_year": follow_up["document_date"][:4],
            "pdf_url": DOWNLOAD_URL.format(
                download_id=follow_up["download_id"]
            ),
            "source_download_id": follow_up["download_id"],
            "source_page": SOURCE_PAGE,
            "source_collection": "vevey-council-documents",
            "legislature": "2021-2026",
        }
        for follow_up in FOLLOW_UPS
    ]
    hashes: dict[str, str] = {}
    for spec in specs:
        download = client.get(
            spec["pdf_url"], headers=HEADERS, timeout=90
        )
        download.raise_for_status()
        content = download.content
        if not content.startswith(b"%PDF"):
            raise ValueError(
                f"Le téléchargement {spec['source_download_id']} n'est pas un PDF"
            )
        sha256 = hashlib.sha256(content).hexdigest()
        if sha256 in hashes:
            raise ValueError(
                "Doublon canonique inattendu entre "
                f"{hashes[sha256]} et {spec['source_download_id']}"
            )
        hashes[sha256] = spec["source_download_id"]
        _, extraction = pdf_text(content)
        document_id = (
            f"vevey_motion_{spec['source_download_id']}_"
            f"{spec['political_object_key'].replace('-', '_')}"
        )
        target = output_dir / f"{document_id}.pdf"
        target.write_bytes(content)
        try:
            stored_pdf_path = target.resolve().relative_to(
                PROJECT_ROOT.resolve()
            )
        except ValueError:
            stored_pdf_path = target.resolve()
        records.append(
            {
                **spec,
                "document_id": document_id,
                "sha256": sha256,
                "local_pdf": str(stored_pdf_path).replace("\\", "/"),
                "text_audit": extraction,
            }
        )
    diagnostics = {
        "documents": len(records),
        "original_motions": sum(
            item["document_role"] == "motion_text" for item in records
        ),
        "follow_up_documents": sum(
            item["document_role"] != "motion_text" for item in records
        ),
        "documents_needing_ocr": sum(
            bool(item["text_audit"]["needs_ocr"]) for item in records
        ),
        "unique_pdf_hashes": len(hashes),
        "complete": len(records) == len(specs),
    }
    return records, diagnostics


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scrape les motions de Vevey de la législature 2021-2026"
    )
    default_root = (
        PROJECT_ROOT / "audit-vevey" / "motions-2021-2026"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=default_root / "inventory.json",
    )
    parser.add_argument(
        "--download-dir",
        type=Path,
        default=default_root / "pdfs",
    )
    args = parser.parse_args()

    originals, false_positives, listing_diagnostics = collect_catalogue()
    expected_ids = {"5740", "5692", "5527", "5063"}
    actual_ids = {item["source_download_id"] for item in originals}
    if actual_ids != expected_ids:
        raise SystemExit(
            "La liste officielle des motions a changé; revue requise. "
            f"Attendu={sorted(expected_ids)}, reçu={sorted(actual_ids)}"
        )
    documents, download_diagnostics = download_documents(
        originals, args.download_dir
    )
    payload = {
        "schema_version": "vevey-motions-inventory-v1",
        "generated_at": datetime.now().astimezone().isoformat(
            timespec="seconds"
        ),
        "source": SOURCE_PAGE,
        "scope": {
            "city": "Vevey",
            "category": "motion",
            "years": sorted(YEARS),
            "legislature": "2021-2026",
        },
        "listing_diagnostics": listing_diagnostics,
        "download_diagnostics": download_diagnostics,
        "canonical_originals": originals,
        "catalogue_false_positives": false_positives,
        "documents": documents,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                **listing_diagnostics,
                **download_diagnostics,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
