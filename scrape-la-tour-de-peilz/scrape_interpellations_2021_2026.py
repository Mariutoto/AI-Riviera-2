import html
import importlib.util
import json
import re
import sys
from pathlib import Path
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import fitz
import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.text_cleaning import clean_french_text
from app.year_metadata import normalize_year_metadata


MOTIONS_SCRAPER_PATH = Path(__file__).with_name("scrape_motions_2021_2026.py")
spec = importlib.util.spec_from_file_location("scrape_motions_2021_2026", MOTIONS_SCRAPER_PATH)
motion_tools = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(motion_tools)


BASE_URL = "https://www.la-tour-de-peilz.ch/"
SOURCE_PAGE = "https://www.la-tour-de-peilz.ch/politique/motions-postulats.php"
YEARS = {str(year) for year in range(2021, 2027)}
OUTPUT_ROOT = PROJECT_ROOT / "documents" / "la-tour-de-peilz"
DATA_ROOT = PROJECT_ROOT / "data" / "interpellations" / "la-tour-de-peilz"
HEADERS = {"User-Agent": "AI-Riviera canonical interpellations importer"}


def fetch_text(url: str) -> str:
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    return response.text


def normalize_pdf_url(page_url: str, href: str) -> str | None:
    href = html.unescape(href)
    full_url = urljoin(page_url, href)
    if "viewer.php" in full_url and "file=" in full_url:
        file_value = parse_qs(urlparse(full_url).query).get("file", [""])[0]
        full_url = urljoin(BASE_URL, unquote(file_value))

    decoded = unquote(full_url)
    if ".pdf" not in decoded.lower():
        return None
    if "motions-postulats" not in decoded.lower():
        return None
    return full_url


def year_from_url(pdf_url: str) -> str | None:
    match = re.search(r"/(20\d{2})/", unquote(urlparse(pdf_url).path))
    if not match:
        return None
    year = match.group(1)
    return year if year in YEARS else None


def safe_filename(pdf_url: str) -> str:
    name = Path(unquote(urlparse(pdf_url).path)).name
    return re.sub(r'[<>:"/\\|?*]', "_", name)


def clean_html_text(raw_html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", raw_html)
    text = html.unescape(text)
    return clean_french_text(re.sub(r"\s+", " ", text)).strip()


def normalize(text: str) -> str:
    return motion_tools.normalize(text)


def slugify(value: str) -> str:
    return motion_tools.slugify(value)


def clean_author_name(name: str) -> str:
    name = re.sub(r"^\s*(?:Mme|M\.|MM\.|Mmes|Madame|Monsieur)\s+", "", name, flags=re.I)
    name = re.sub(r"\s+et\s+consorts\b.*$", "", name, flags=re.I)
    return motion_tools.clean_person_name(name)


def parse_listing_status(label: str) -> tuple[str | None, str]:
    normalized = normalize(label)
    if "reponse" in normalized:
        return "Reponse incluse", "response_available"
    if "retire" in normalized:
        return "Retire", "withdrawn"
    return None, "filed"


def parse_authors_from_listing(label: str) -> list[dict]:
    head = re.sub(r"^\s*Interpellation\s+(?:de\s+|du\s+|des\s+|d')", "", label, flags=re.I)
    head = re.split(r"\s[-â€“]\s|\s\+\s", head, maxsplit=1)[0]
    group_match = re.search(r"\bgroupe\s+([A-Z0-9/+.-]{2,})\b", head, flags=re.I)
    if group_match:
        party = group_match.group(1).upper()
        return [{"name": f"groupe {party}", "party": party, "role": "group"}]

    authors = []
    seen = set()
    shared_party_match = re.search(r"\(([^)]+)\)\s*$", head)
    people_part = re.sub(r"\s*\([^)]+\)\s*$", "", head).strip()
    if shared_party_match and re.match(r"^(?:MM\.|Mmes)\s+", people_part, flags=re.I):
        shared_party = shared_party_match.group(1).strip()
        shared_people = re.sub(r"^(?:MM\.|Mmes)\s+", "", people_part, flags=re.I)
        shared_names = [
            clean_author_name(name)
            for name in re.split(r"\s+et\s+|,\s*", shared_people)
            if name.strip()
        ]
        if len(shared_names) > 1:
            for person_name in shared_names:
                if len(person_name.split()) < 2:
                    continue
                key = (person_name.casefold(), shared_party)
                if key in seen:
                    continue
                seen.add(key)
                authors.append({"name": person_name, "party": shared_party})
            if authors:
                return authors

    for match in re.finditer(r"\b(Mme|M\.|Madame|Monsieur)\s+(.+?)\s*\(([^)]+)\)", head):
        civility, name, party = match.groups()
        name = re.sub(r"^(?:et|&)\s+", "", name, flags=re.I)
        person_name = clean_author_name(name)
        if len(person_name.split()) < 2:
            continue
        party = party.strip()
        key = (person_name.casefold(), party)
        if key in seen:
            continue
        seen.add(key)
        authors.append({"name": person_name, "civility": "Mme" if civility in {"Mme", "Madame"} else "M.", "party": party})
    if authors:
        return authors
    return motion_tools.extract_people(head)


def clean_group_labels(authors: list[dict]) -> list[dict]:
    deduped = []
    seen = set()
    cleaned = []
    for author in authors:
        item = dict(author)
        name = str(item.get("name") or "")
        group_match = re.match(r"groupe\s+([A-Za-z0-9/+.-]+)$", name, flags=re.I)
        if group_match:
            item["name"] = f"groupe {group_match.group(1).upper()}"
            item["party"] = item.get("party") or group_match.group(1).upper()
            item.setdefault("role", "group")
        else:
            item["name"] = clean_author_name(name)
        cleaned.append(item)
    for item in cleaned:
        name = str(item.get("name") or "")
        if len(name.split()) < 2:
            continue
        key = (motion_tools.strip_accents(name).casefold(), str(item.get("party") or "").casefold())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def extract_interpellation_people(text: str) -> list[dict]:
    people = []
    patterns = [
        r"Interpellat(?:eur|rice)(?:s)?\s*:\s*([\s\S]{0,900}?)(?:La Tour-de-Peilz|Interpellation\s*:|Objet|Monsieur|Madame|Question|$)",
        r"Au nom des interpellat(?:eur|rice)(?:s)?\s*,?\s*([\s\S]{0,550})(?:\n\s*\n|$)",
        r"\bInterpellation\s+de\s+([\s\S]{0,450}?)(?:\n|Objet|Interpellation\s*:|Monsieur|Madame|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text[:2500], flags=re.I)
        if match:
            block = match.group(1)
            people = motion_tools.merge_people(people, motion_tools.extract_people_with_parties(block), motion_tools.extract_people(block))
    return people


def extract_interpellation_authors(text: str, listing_authors: list[dict]) -> list[dict]:
    authors = motion_tools.merge_people(listing_authors, extract_interpellation_people(text))
    return clean_group_labels(authors)


def extract_interpellation_object_title(text: str) -> str | None:
    patterns = [
        r"\bInterpellation\s*:\s*([\s\S]{0,280}?)(?:\n\s*\n|Monsieur|Madame|Question|Questions|D[ée]veloppement|$)",
        r"\bObjet\s*:\s*([\s\S]{0,240}?)(?:\n\s*\n|Monsieur|Madame|Question|Questions|$)",
        r"intitul[ée]\s+[«\"]\s*([^»\"]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if not match:
            continue
        title = clean_french_text(re.sub(r"\s+", " ", match.group(1))).strip(" .:-«»\"")
        if title and not normalize(title).startswith(("madame", "monsieur")):
            return title
    return None


def infer_document_role(label: str, filename: str, text: str) -> str:
    normalized = normalize(f"{label} {filename} {text[:3000]}")
    filename_normalized = normalize(filename)
    has_response = "reponse" in normalized or re.search(r"(?:^|[-_])rep(?:[-_.]|$)", filename_normalized) is not None
    has_interpellation = "interpellation" in normalized
    if has_response and has_interpellation:
        return "combined_interpellation_response"
    if has_response:
        return "municipal_response"
    return "interpellation_text"


def extract_document_components(role: str) -> list[dict]:
    components = []
    if role in {"interpellation_text", "combined_interpellation_response"}:
        components.append({"role": "interpellation_text"})
    if role in {"municipal_response", "combined_interpellation_response"}:
        components.append({"role": "municipal_response"})
    return components


def collect_items() -> list[dict]:
    page_html = fetch_text(SOURCE_PAGE)
    items_by_url = {}
    for year_match in re.finditer(
        r'<div class="title c-tmplt">[\s\S]*?</i>\s*(20\d{2})</div>\s*<div class="answer"[\s\S]*?>',
        page_html,
        flags=re.I,
    ):
        listing_year = year_match.group(1)
        if listing_year not in YEARS:
            continue

        block_start = year_match.end()
        next_year = re.search(
            r'<div class="title c-tmplt">[\s\S]*?</i>\s*20\d{2}</div>\s*<div class="answer"',
            page_html[block_start:],
            flags=re.I,
        )
        block_end = block_start + next_year.start() if next_year else len(page_html)
        year_block = page_html[block_start:block_end]

        for li in re.findall(r'<li class="prestation[^"]*">([\s\S]*?)</li>', year_block, flags=re.I):
            href_match = re.search(r'href=["\']([^"\']+)["\']', li, flags=re.I)
            if not href_match:
                continue
            pdf_url = normalize_pdf_url(SOURCE_PAGE, href_match.group(1))
            pdf_year = year_from_url(pdf_url) if pdf_url else None
            if not pdf_url or not pdf_year:
                continue

            title_match = re.search(r"<span[^>]*font-weight:\s*bold;[^>]*>([\s\S]*?)</span>", li, flags=re.I)
            summary_match = re.search(r'<div[^>]*class="txt-14"[^>]*>([\s\S]*?)</div>', li, flags=re.I)
            listing_title = clean_html_text(title_match.group(1)) if title_match else Path(unquote(urlparse(pdf_url).path)).stem
            if not normalize(listing_title).startswith("interpellation "):
                continue

            subject = clean_html_text(summary_match.group(1)) if summary_match else ""
            status_raw, status_normalized = parse_listing_status(listing_title)
            filename = safe_filename(pdf_url)
            object_id = f"interpellation-{listing_year}-{slugify(subject or listing_title or filename)}"

            items_by_url[pdf_url] = {
                "commune": "La Tour-de-Peilz",
                "type": "interpellation",
                "document_type": "interpellation",
                "year": pdf_year,
                "listing_year": listing_year,
                "category": "interpellations",
                "legislature": "2021-2026",
                "title": listing_title,
                "summary": subject,
                "filename": filename,
                "pdf_url": pdf_url,
                "source_page": SOURCE_PAGE,
                "source_collection": "motions-postulats",
                "canonical_object": True,
                "political_object_id": object_id,
                "site_listing_title": listing_title,
                "site_subject": subject,
                "site_status_raw": status_raw,
                "status": status_raw,
                "status_normalized": status_normalized,
                "authors": parse_authors_from_listing(listing_title),
            }
    return list(sorted(items_by_url.values(), key=lambda item: (item["listing_year"], item["filename"])))


def extract_pdf_text(pdf_path: Path) -> str:
    document = fitz.open(pdf_path)
    return "\n".join(motion_tools.clean_pdf_text(page.get_text()) for page in document)


def enrich_interpellation_metadata(item: dict, text: str) -> dict:
    role = infer_document_role(item["site_listing_title"], item["filename"], text)
    contains_response = role in {"municipal_response", "combined_interpellation_response"} or item["status_normalized"] == "response_available"
    document_date = (
        motion_tools.parse_signature_date(text, default_year=item["listing_year"])
        or motion_tools.parse_french_date(text[:2500], default_year=item["listing_year"])
    )
    authors = extract_interpellation_authors(text, item["authors"])
    object_title = extract_interpellation_object_title(text) or item.get("summary") or None
    search_facets = [
        "interpellation",
        "conseil_communal",
        "municipalite",
        item["status_normalized"],
        role,
        "reponse" if contains_response else None,
        *[person.get("party") for person in authors if person.get("party")],
        *re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ]{4,}", (item.get("summary") or "").lower())[:8],
    ]
    search_facets = [slugify(facet).replace("-", "_") for facet in search_facets if facet]

    political_object = {
        "type": "interpellation",
        "object_type": "interpellation",
        "object_id": item["political_object_id"],
        "status_raw": item.get("site_status_raw"),
        "status_normalized": item["status_normalized"],
        "target_body": "Municipalite",
        "decision_body": "Conseil communal",
        "request_type": "question_request",
        "expects_municipal_answer": True,
        "contains_response": contains_response,
        "source_listing_title": item["site_listing_title"],
        "source_subject": item["site_subject"],
        "canonical_source": SOURCE_PAGE,
    }
    if object_title:
        political_object["object_title"] = object_title
    if document_date:
        political_object["document_date"] = document_date

    metadata = {
        **item,
        "authors": authors,
        "object_title": object_title,
        "document_role": role,
        "document_components": extract_document_components(role),
        "contains_response": contains_response,
        "document_date": document_date,
        "political_object": political_object,
        "search_facets": sorted(set(search_facets)),
        "content_kind": "political_interpellation",
        "metadata_version": "metadata-audit-v2",
        "text_extraction_status": {
            "characters_extracted": len(text),
            "text_available": len(text.strip()) > 20,
            "needs_ocr": len(text.strip()) <= 20,
        },
    }

    for key in ["document_date", "site_status_raw", "status"]:
        if metadata.get(key) is None:
            metadata.pop(key, None)
    if metadata["political_object"].get("status_raw") is None:
        metadata["political_object"].pop("status_raw", None)
    return metadata


def download_and_extract(item: dict) -> dict:
    target_dir = OUTPUT_ROOT / item["year"] / "interpellations"
    target_dir.mkdir(parents=True, exist_ok=True)

    pdf_path = target_dir / item["filename"]
    txt_path = pdf_path.with_suffix(".txt")
    json_path = pdf_path.with_suffix(".json")

    if not pdf_path.exists():
        response = requests.get(item["pdf_url"], headers=HEADERS, timeout=90)
        response.raise_for_status()
        pdf_path.write_bytes(response.content)

    text = extract_pdf_text(pdf_path)
    txt_path.write_text(text + "\n", encoding="utf-8")

    metadata = enrich_interpellation_metadata(item, text)
    metadata["pdf_path"] = str(pdf_path)
    metadata["text_path"] = str(txt_path)
    metadata = normalize_year_metadata(metadata, json_path)
    json_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return metadata


def token_set(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]{4,}", slugify(value).replace("-", " "))
        if token not in {"interpellation", "reponse", "rep", "bis", "consorts"}
    }


def find_related_interpellation(metadata: dict, canonical_results: list[dict]) -> dict | None:
    source_tokens = token_set(" ".join(str(metadata.get(key, "")) for key in ["filename", "title", "summary"]))
    best = None
    best_score = 0
    for candidate in canonical_results:
        candidate_tokens = token_set(
            " ".join(str(candidate.get(key, "")) for key in ["filename", "site_listing_title", "site_subject"])
        )
        score = len(source_tokens & candidate_tokens)
        if score > best_score:
            best = candidate
            best_score = score
    return best if best_score >= 2 else None


def mark_agenda_linked_interpellation_documents(canonical_results: list[dict]) -> list[dict]:
    updated = []
    for json_path in sorted(OUTPUT_ROOT.glob("*/interpellations/*.json")):
        metadata = json.loads(json_path.read_text(encoding="utf-8-sig"))
        if metadata.get("canonical_object"):
            continue
        source_page = str(metadata.get("source_page", ""))
        if "apercu_ordre-du-jour.php" not in source_page:
            continue

        related = find_related_interpellation(metadata, canonical_results)
        text = ""
        text_path = Path(str(metadata.get("text_path") or json_path.with_suffix(".txt")))
        if text_path.exists():
            text = text_path.read_text(encoding="utf-8", errors="ignore")
        extracted_authors = extract_interpellation_authors(text, metadata.get("authors") or []) if text else []
        object_title = extract_interpellation_object_title(text) if text else None
        role = infer_document_role(str(metadata.get("title") or ""), str(metadata.get("filename") or json_path.name), text)

        metadata["canonical_object"] = False
        metadata["source_collection"] = "ordre-du-jour-linked-document"
        metadata["linked_to_session"] = True
        metadata["metadata_version"] = "metadata-audit-v2"
        metadata.setdefault("type", "interpellation")
        metadata.setdefault("document_type", "interpellation")
        metadata.setdefault("category", "interpellations")
        metadata["document_role"] = metadata.get("document_role") or role
        metadata["contains_response"] = metadata.get("contains_response") or role in {"municipal_response", "combined_interpellation_response"}
        metadata["agenda_linked_document"] = {
            "source_page": source_page,
            "role": metadata.get("document_role") or "interpellation_related_document",
        }
        if extracted_authors:
            metadata["authors"] = extracted_authors
        if object_title:
            metadata["object_title"] = object_title
            metadata.setdefault("summary", object_title)
            if isinstance(metadata.get("political_object"), dict):
                metadata["political_object"]["object_title"] = object_title
        if related:
            metadata["related_political_object_id"] = related["political_object_id"]
            metadata["related_canonical_interpellation"] = {
                "political_object_id": related["political_object_id"],
                "title": related["site_listing_title"],
                "object_title": related.get("object_title") or related.get("summary"),
                "authors": related.get("authors") or [],
                "filename": related["filename"],
                "pdf_url": related["pdf_url"],
            }
            if related.get("authors"):
                metadata["authors"] = related["authors"]
            if not metadata.get("object_title") and (related.get("object_title") or related.get("summary")):
                metadata["object_title"] = related.get("object_title") or related.get("summary")
        json_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        updated.append({"path": str(json_path), "related_political_object_id": metadata.get("related_political_object_id")})
    return updated


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    items = collect_items()
    print(f"Found {len(items)} canonical interpellations for years 2021-2026.")

    results = []
    failures = []
    for index, item in enumerate(items, start=1):
        print(f"[{index}/{len(items)}] {item['listing_year']} {item['filename']}")
        try:
            results.append(download_and_extract(item))
        except Exception as exc:
            failures.append({"pdf_url": item["pdf_url"], "filename": item["filename"], "error": str(exc)})
            print(f"  ERROR: {exc}")

    linked_updates = mark_agenda_linked_interpellation_documents(results)
    manifest = {
        "commune": "La Tour-de-Peilz",
        "legislature": "2021-2026",
        "source_page": SOURCE_PAGE,
        "scope_note": "Interpellations canoniques extraites de la page officielle motions-postulats.php. Les PDF repris depuis les ordres du jour sont marques comme documents lies, non canoniques.",
        "years": sorted(YEARS),
        "documents_downloaded": len(results),
        "agenda_linked_documents_updated": linked_updates,
        "failures": failures,
        "documents": results,
    }
    manifest_path = DATA_ROOT / "manifest_interpellations_2021_2026.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"Downloaded/extracted: {len(results)}")
    print(f"Failures: {len(failures)}")
    print(f"Manifest: {manifest_path}")


if __name__ == "__main__":
    main()
