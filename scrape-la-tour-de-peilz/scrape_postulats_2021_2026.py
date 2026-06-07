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


MOTIONS_SCRAPER_PATH = Path(__file__).with_name("scrape_motions_2021_2026.py")
spec = importlib.util.spec_from_file_location("scrape_motions_2021_2026", MOTIONS_SCRAPER_PATH)
motion_tools = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(motion_tools)


BASE_URL = "https://www.la-tour-de-peilz.ch/"
SOURCE_PAGE = "https://www.la-tour-de-peilz.ch/politique/motions-postulats.php"
YEARS = {str(year) for year in range(2021, 2027)}
OUTPUT_ROOT = PROJECT_ROOT / "documents" / "la-tour-de-peilz"
DATA_ROOT = PROJECT_ROOT / "data" / "postulats" / "la-tour-de-peilz"
HEADERS = {"User-Agent": "AI-Riviera canonical postulats importer"}


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


def parse_authors_from_listing(label: str) -> list[dict]:
    head = re.sub(r"^\s*Postulat\s+de\s+", "", label, flags=re.I)
    head = re.split(r"\s[-–]\s|\s\+\s", head, maxsplit=1)[0]
    group_match = re.search(r"\bgroupe\s+([A-Z0-9/-]{2,})\b", head, flags=re.I)
    if group_match:
        party = group_match.group(1).upper()
        return [{"name": f"groupe {party}", "party": party, "role": "group"}]

    authors = []
    seen = set()
    party_match = re.search(r"\(([^)]+)\)\s*$", head)
    shared_party = party_match.group(1).strip() if party_match else ""
    people_part = re.sub(r"\s*\([^)]+\)\s*$", "", head).strip()
    person_matches = list(
        re.finditer(
            r"\b(Mme|M\.|MM\.|Mmes)\s+(.+?)(?=\s+(?:et\s+)?(?:Mme|M\.|MM\.|Mmes)\s+|\s+et\s+consorts|$)",
            people_part,
            flags=re.I,
        )
    )
    if person_matches and shared_party:
        for match in person_matches:
            civility, name = match.groups()
            person_name = motion_tools.clean_person_name(name)
            if len(person_name.split()) < 2:
                continue
            key = (person_name.casefold(), shared_party)
            if key in seen:
                continue
            seen.add(key)
            authors.append({"name": person_name, "civility": civility if civility in {"Mme", "M."} else "", "party": shared_party})

    if authors:
        return authors
    for match in re.finditer(r"\b(Mme|M\.)\s+(.+?)\s*\(([^)]+)\)", head):
        civility, name, party = match.groups()
        person_name = motion_tools.clean_person_name(name)
        if len(person_name.split()) < 2:
            continue
        key = (person_name.casefold(), party.strip())
        if key in seen:
            continue
        seen.add(key)
        authors.append({"name": person_name, "civility": civility, "party": party.strip()})
    if authors:
        return authors
    if re.search(r"\bLa Tour-de-Peilz Libre\b|\bLTDPL\b", head, flags=re.I):
        return [{"name": "La Tour-de-Peilz Libre", "party": "LTDPL", "role": "group"}]
    return motion_tools.extract_people(head)


def clean_group_labels(authors: list[dict]) -> list[dict]:
    cleaned = []
    for author in authors:
        item = dict(author)
        name = str(item.get("name") or "")
        group_match = re.match(r"groupe\s+([A-Za-z0-9/-]+)$", name, flags=re.I)
        if group_match:
            item["name"] = f"groupe {group_match.group(1).upper()}"
        cleaned.append(item)
    return cleaned


def extract_postulat_people(text: str) -> list[dict]:
    blocks = []
    for pattern in [
        r"Postulant(?:e|s|es)?\s*:\s*([\s\S]{0,900}?)(?:La Tour-de-Peilz|Postulat\s*:|Objet|Objectif|Monsieur|Madame)",
        r"Postulat\s*:\s*.*?(?:\n|$)([\s\S]{0,450}?)(?:Monsieur|Madame|La Tour-de-Peilz|Par ce postulat|Nous demandons|Consid[ée]rant|Objectif|$)",
    ]:
        match = re.search(pattern, text[:2200], flags=re.I)
        if match:
            blocks.append(match.group(1))
    people = []
    for block in blocks:
        people = motion_tools.merge_people(people, motion_tools.extract_people_with_parties(block), motion_tools.extract_people(block))
    return people


def extract_group_postulat_authors(text: str) -> list[dict]:
    party_match = re.search(r"\bpostulat d[ée]pos[ée]\s+par\s+le\s+groupe\s+([A-Z0-9/-]{2,})\b", text[:500], flags=re.I)
    footer_match = re.search(r"Au nom du groupe\s+([A-Z0-9/-]{2,})\s+([A-ZÉÈÀÂÄÇ][A-Za-zÀ-ÖØ-öø-ÿ' -]+)", text, flags=re.I)
    people = []
    if party_match:
        party = party_match.group(1).upper()
        people.append({"name": f"groupe {party}", "party": party, "role": "group"})
    if footer_match:
        party, name = footer_match.groups()
        name = re.sub(r"\bPr[ée]sident(?:e)?\b.*$", "", name, flags=re.I)
        people.append({"name": motion_tools.clean_person_name(name), "party": party.upper(), "role": "group_representative"})
    return motion_tools.merge_people(people)


def extract_postulat_authors(text: str, listing_authors: list[dict]) -> list[dict]:
    authors = motion_tools.merge_people(listing_authors, extract_group_postulat_authors(text), extract_postulat_people(text))
    footer_match = re.search(r"Au nom des postulant(?:e|s|es)?\s*,?\s*([\s\S]{0,500})", text, flags=re.I)
    if footer_match:
        authors = motion_tools.merge_people(authors, motion_tools.extract_people_with_parties(footer_match.group(1)), motion_tools.extract_people(footer_match.group(1)))
    return clean_group_labels(authors)


def extract_postulat_object_title(text: str) -> str | None:
    patterns = [
        r"\bPostulat\s*:\s*([\s\S]{0,260}?)(?:\n\s*\n|Monsieur|Madame|Objectif|Contexte|Consid[ée]rant|$)",
        r"intitul[ée]\s+[«\"]\s*([^»\"]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if match:
            title = clean_french_text(re.sub(r"\s+", " ", match.group(1))).strip(" .:-«»\"")
            if title:
                return title
    return None


def infer_document_role(label: str, filename: str, text: str) -> tuple[str, str | None]:
    role, report_type = motion_tools.infer_document_role(label, filename, text)
    return role.replace("motion", "postulat"), report_type


def extract_document_components(text: str, role: str, report_type: str | None) -> list[dict]:
    components = []
    if "postulat_text" in role:
        components.append({"role": "postulat_text"})
    if "commission_report" in role:
        component = {"role": "commission_report"}
        if report_type:
            component["report_type"] = report_type
        components.append(component)
    if "council_decision" in role or role == "combined_postulat_report_decision":
        components.append({"role": "council_decision"})
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
            if not normalize(listing_title).startswith("postulat "):
                continue

            subject = clean_html_text(summary_match.group(1)) if summary_match else ""
            status_raw, status_normalized = motion_tools.parse_listing_status(listing_title)
            filename = safe_filename(pdf_url)
            object_id = f"postulat-{listing_year}-{slugify(subject or listing_title or filename)}"

            items_by_url[pdf_url] = {
                "commune": "La Tour-de-Peilz",
                "type": "postulat",
                "document_type": "postulat",
                "year": pdf_year,
                "listing_year": listing_year,
                "category": "postulats",
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


def enrich_postulat_metadata(item: dict, text: str) -> dict:
    role, report_type = infer_document_role(item["site_listing_title"], item["filename"], text)
    flags = motion_tools.infer_report_flags(item["site_listing_title"], item["filename"], text, report_type)
    members = motion_tools.extract_commission_members(text) if flags["contains_report"] else []
    meeting = motion_tools.extract_commission_meeting(text, item["listing_year"]) if flags["contains_report"] else None
    invited = motion_tools.extract_invited_people(text) if flags["contains_report"] else []
    rapporteur = motion_tools.extract_rapporteur(text, members) if flags["contains_report"] else None
    recommendation = motion_tools.extract_commission_recommendation(text) if flags["contains_report"] else None
    council_decision = motion_tools.extract_council_decision(text, item["listing_year"]) if flags["contains_decision"] else None
    document_date = (
        motion_tools.parse_signature_date(text, default_year=item["listing_year"])
        or motion_tools.parse_french_date(text[:2500], default_year=item["listing_year"])
    )
    authors = extract_postulat_authors(text, item["authors"])
    object_title = extract_postulat_object_title(text) or item.get("summary") or None
    search_facets = [
        "postulat",
        "conseil_communal",
        "municipalite",
        item["status_normalized"],
        role,
        report_type,
        *[person.get("party") for person in authors if person.get("party")],
        *re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ]{4,}", (item.get("summary") or "").lower())[:8],
    ]
    search_facets = [slugify(facet).replace("-", "_") for facet in search_facets if facet]

    political_object = {
        "type": "postulat",
        "object_type": "postulat",
        "object_id": item["political_object_id"],
        "status_raw": item.get("site_status_raw"),
        "status_normalized": item["status_normalized"],
        "target_body": "Municipalite",
        "decision_body": "Conseil communal",
        "request_type": "non_binding_request",
        "source_listing_title": item["site_listing_title"],
        "source_subject": item["site_subject"],
        "canonical_source": SOURCE_PAGE,
    }
    if object_title:
        political_object["object_title"] = object_title
    if document_date:
        political_object["document_date"] = document_date
    if council_decision and council_decision.get("outcome"):
        political_object["decision_status"] = council_decision["outcome"]

    metadata = {
        **item,
        "authors": authors,
        "object_title": object_title,
        "document_role": role,
        "report_type": report_type,
        "document_components": extract_document_components(text, role, report_type),
        **flags,
        "document_date": document_date,
        "political_object": political_object,
        "search_facets": sorted(set(search_facets)),
        "content_kind": "political_postulat",
        "metadata_version": "metadata-audit-v2",
        "text_extraction_status": {
            "characters_extracted": len(text),
            "text_available": len(text.strip()) > 20,
            "needs_ocr": len(text.strip()) <= 20,
        },
    }
    if flags["contains_report"]:
        commission = {
            "members": members,
            "meeting": meeting,
            "invited_people": invited,
            "rapporteur": rapporteur,
            "recommendation": recommendation,
            "contains_majority_report": flags["contains_majority_report"],
            "contains_minority_report": flags["contains_minority_report"],
        }
        metadata["commission"] = {key: value for key, value in commission.items() if value not in (None, [], {})}
    if council_decision:
        metadata["decision"] = council_decision

    for key in ["report_type", "document_date", "site_status_raw", "status"]:
        if metadata.get(key) is None:
            metadata.pop(key, None)
    if metadata["political_object"].get("status_raw") is None:
        metadata["political_object"].pop("status_raw", None)
    return metadata


def download_and_extract(item: dict) -> dict:
    target_dir = OUTPUT_ROOT / item["year"] / "postulats"
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

    metadata = enrich_postulat_metadata(item, text)
    metadata["pdf_path"] = str(pdf_path)
    metadata["text_path"] = str(txt_path)
    json_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return metadata


def token_set(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]{4,}", slugify(value).replace("-", " "))
        if token not in {"postulat", "rapp", "rapport", "decision", "consorts", "reponse"}
    }


def find_related_postulat(metadata: dict, canonical_results: list[dict]) -> dict | None:
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


def mark_agenda_linked_postulat_documents(canonical_results: list[dict]) -> list[dict]:
    updated = []
    for json_path in sorted(OUTPUT_ROOT.glob("*/postulats/*.json")):
        metadata = json.loads(json_path.read_text(encoding="utf-8-sig"))
        if metadata.get("canonical_object"):
            continue
        source_page = str(metadata.get("source_page", ""))
        if "apercu_ordre-du-jour.php" not in source_page:
            continue

        related = find_related_postulat(metadata, canonical_results)
        text = ""
        text_path = Path(str(metadata.get("text_path") or json_path.with_suffix(".txt")))
        if text_path.exists():
            text = text_path.read_text(encoding="utf-8", errors="ignore")
        extracted_authors = extract_postulat_authors(text, metadata.get("authors") or []) if text else []
        object_title = extract_postulat_object_title(text) if text else None
        metadata["canonical_object"] = False
        metadata["source_collection"] = "ordre-du-jour-linked-document"
        metadata["linked_to_session"] = True
        metadata["metadata_version"] = "metadata-audit-v2"
        metadata.setdefault("type", "postulat")
        metadata.setdefault("document_type", "postulat")
        metadata.setdefault("category", "postulats")
        metadata["agenda_linked_document"] = {
            "source_page": source_page,
            "role": metadata.get("document_role") or metadata.get("type") or "postulat_related_document",
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
            metadata["related_canonical_postulat"] = {
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
    print(f"Found {len(items)} canonical postulats for years 2021-2026.")

    results = []
    failures = []
    for index, item in enumerate(items, start=1):
        print(f"[{index}/{len(items)}] {item['listing_year']} {item['filename']}")
        try:
            results.append(download_and_extract(item))
        except Exception as exc:
            failures.append({"pdf_url": item["pdf_url"], "filename": item["filename"], "error": str(exc)})
            print(f"  ERROR: {exc}")

    linked_updates = mark_agenda_linked_postulat_documents(results)
    manifest = {
        "commune": "La Tour-de-Peilz",
        "legislature": "2021-2026",
        "source_page": SOURCE_PAGE,
        "scope_note": "Postulats canoniques extraits de la page officielle motions-postulats.php. Les PDF repris depuis les ordres du jour sont marqués comme documents liés, non canoniques.",
        "years": sorted(YEARS),
        "documents_downloaded": len(results),
        "agenda_linked_documents_updated": linked_updates,
        "failures": failures,
        "documents": results,
    }
    manifest_path = DATA_ROOT / "manifest_postulats_2021_2026.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"Downloaded/extracted: {len(results)}")
    print(f"Failures: {len(failures)}")
    print(f"Manifest: {manifest_path}")


if __name__ == "__main__":
    main()
