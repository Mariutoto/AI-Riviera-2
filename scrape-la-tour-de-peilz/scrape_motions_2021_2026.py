import html
import json
import re
import sys
from pathlib import Path
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import fitz
import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.text_cleaning import clean_french_text, strip_accents


BASE_URL = "https://www.la-tour-de-peilz.ch/"
SOURCE_PAGE = "https://www.la-tour-de-peilz.ch/politique/motions-postulats.php"
YEARS = {str(year) for year in range(2021, 2027)}
OUTPUT_ROOT = PROJECT_ROOT / "documents" / "la-tour-de-peilz"
DATA_ROOT = PROJECT_ROOT / "data" / "motions" / "la-tour-de-peilz"
HEADERS = {"User-Agent": "AI-Riviera canonical motions importer"}

MONTHS_FR = {
    "janvier": "01",
    "fevrier": "02",
    "février": "02",
    "mars": "03",
    "avril": "04",
    "mai": "05",
    "juin": "06",
    "juillet": "07",
    "aout": "08",
    "août": "08",
    "septembre": "09",
    "octobre": "10",
    "novembre": "11",
    "decembre": "12",
    "décembre": "12",
}

PARTIES = {"PLR", "LCIVL", "PSDG", "UDC", "LV", "LTDPL", "PS", "VERTS", "HORS PARTI"}
OCR_NAME_FIXES = {
    "Phil ippe": "Philippe",
}


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


def clean_pdf_text(text: str) -> str:
    text = re.sub(r"[\uf000-\uf8ff]", " ", text)
    return clean_french_text(text)


def normalize(text: str) -> str:
    return strip_accents(text).casefold()


def clean_person_name(name: str) -> str:
    name = re.sub(r"\s+", " ", name).strip(" ,")
    name = re.sub(r"\b(?:etant|étant)\s+excus[ée]e?\b.*$", "", name, flags=re.I).strip(" ,")
    for raw, fixed in OCR_NAME_FIXES.items():
        name = name.replace(raw, fixed)
    return name


def parse_french_date(text: str, default_year: str | None = None) -> str | None:
    month_pattern = "|".join(sorted(MONTHS_FR, key=len, reverse=True))
    match = re.search(
        rf"\b(?:le\s+)?(\d{{1,2}}|1er)\s+({month_pattern})(?:\s+(20\d{{2}}))?\b",
        text,
        flags=re.I,
    )
    if not match:
        return None
    day_raw, month_raw, year = match.groups()
    year = year or default_year
    if not year:
        return None
    day = 1 if day_raw.lower() == "1er" else int(day_raw)
    month = MONTHS_FR.get(month_raw.lower())
    if not month:
        return None
    return f"{year}-{month}-{day:02d}"


def parse_signature_date(text: str, default_year: str | None = None) -> str | None:
    for match in re.finditer(r"La Tour-de-Peilz,\s*(?:le\s+)?(.{0,100})", text, flags=re.I):
        parsed = parse_french_date(match.group(1), default_year=default_year)
        if parsed:
            return parsed
    return None


def parse_listing_status(label: str) -> tuple[str | None, str]:
    normalized = normalize(label)
    if "non soutenu" in normalized:
        return "Non soutenu par le Conseil", "not_supported_by_council"
    if "renvoye directement" in normalized and "municipalite" in normalized:
        return "Renvoyé directement à la Municipalité", "referred_directly_to_municipality"
    if "renvoye" in normalized and "municipalite" in normalized:
        return "Renvoyé à la Municipalité", "referred_to_municipality"
    if "rapport" in normalized and "decision" in normalized:
        return "Rapport et décision disponibles", "report_and_decision_available"
    if "rapport" in normalized:
        return "Rapport disponible", "report_available"
    if "decision" in normalized:
        return "Décision disponible", "decision_available"
    return None, "unknown"


def infer_document_role(label: str, filename: str, text: str) -> tuple[str, str | None]:
    haystack = normalize(f"{label} {filename} {text[:2500]}")
    if "rapport de majorite" in haystack or "rapp-maj" in normalize(filename):
        return "commission_report", "majority_report"
    if "rapport de minorite" in haystack or "rapp-min" in normalize(filename):
        return "commission_report", "minority_report"
    has_report = "rapport" in haystack or "rapp" in normalize(filename)
    has_decision = "decision" in haystack or "dec" in normalize(filename)
    has_motion_text = "nous, les soussignes" in haystack or "proposons la motion suivante" in haystack or "motion :" in haystack
    if has_report and has_decision and has_motion_text:
        return "combined_motion_report_decision", "standard_report"
    if has_report and has_motion_text:
        return "combined_motion_report", "standard_report"
    if has_report:
        return "commission_report", "standard_report"
    if has_decision:
        return "council_decision", None
    return "motion_text", None


def extract_people(text: str) -> list[dict]:
    people = []
    seen = set()
    for match in re.finditer(r"\b(Mme|M\.|Madame|Monsieur)\s+([A-ZÉÈÀÂÄÇ][A-Za-zÀ-ÖØ-öø-ÿ' -]+?)(?:\s*,?\s*\(([^)]+)\))?(?=\s*(?:,|$|\n))", text):
        civility, name, party = match.groups()
        name = clean_person_name(name)
        if len(name.split()) < 2:
            continue
        party = party.strip() if party else None
        key = (name.casefold(), party or "")
        if key in seen:
            continue
        seen.add(key)
        person = {"name": name, "civility": "Mme" if civility in {"Mme", "Madame"} else "M."}
        if party:
            person["party"] = party
        people.append(person)
    return people


def merge_people(*groups: list[dict]) -> list[dict]:
    merged = []
    seen = set()
    index_by_name = {}
    for group in groups:
        for person in group or []:
            name = clean_person_name(str(person.get("name") or ""))
            if len(name.split()) < 2:
                continue
            party = str(person.get("party") or "").strip()
            key = (name.casefold(), party)
            if key in seen:
                continue
            name_key = name.casefold()
            if name_key in index_by_name:
                existing_index = index_by_name[name_key]
                existing_party = str(merged[existing_index].get("party") or "").strip()
                if existing_party or not party:
                    continue
                merged[existing_index] = {**person, "name": name, "party": party}
                seen.add(key)
                continue
            seen.add(key)
            cleaned = {**person, "name": name}
            if party:
                cleaned["party"] = party
            index_by_name[name_key] = len(merged)
            merged.append(cleaned)
    return merged


def extract_motionnaire_people(text: str) -> list[dict]:
    match = re.search(r"Motionnaires\s*:\s*([\s\S]{0,900}?)(?:La Tour-de-Peilz|Motion\s*:|Objectif|Monsieur|Madame)", text, flags=re.I)
    if not match:
        return []

    people = []
    for raw_line in match.group(1).splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip(" ,-")
        if not line:
            continue

        group_match = re.search(r"Pour le groupe\s+([A-Z0-9/-]{2,})\s*,?\s*(.+)", line, flags=re.I)
        if group_match:
            party, name = group_match.groups()
            people.append({"name": clean_person_name(name), "party": party.upper(), "role": "group_representative"})
            continue

        party_match = re.search(r"(.+?)\s*\(([A-Z0-9/-]{2,})\)\s*$", line)
        if party_match:
            name, party = party_match.groups()
            people.append({"name": clean_person_name(name), "party": party.strip()})
            continue

        if re.match(r"^[A-ZÉÈÀÂÄÇ][A-Za-zÀ-ÖØ-öø-ÿ' -]+ [A-ZÉÈÀÂÄÇ][A-Za-zÀ-ÖØ-öø-ÿ' -]+$", line):
            people.append({"name": clean_person_name(line)})

    return merge_people(people)


def parse_authors_from_listing(label: str) -> list[dict]:
    head = re.sub(r"^\s*Motion\s+de\s+", "", label, flags=re.I)
    head = re.split(r"\s[-–]\s|\s\+\s", head, maxsplit=1)[0]
    authors = []
    seen = set()
    for match in re.finditer(r"\b(Mme|M\.)\s+(.+?)\s*\(([^)]+)\)", head):
        civility, name, party = match.groups()
        name = re.sub(r"^(?:et|&)\s+", "", name, flags=re.I)
        name = clean_person_name(name)
        if len(name.split()) < 2:
            continue
        key = (name.casefold(), party.strip())
        if key in seen:
            continue
        seen.add(key)
        authors.append({"name": name, "civility": civility, "party": party.strip()})
    return authors or extract_people(head)


def extract_motion_authors(text: str, listing_authors: list[dict]) -> list[dict]:
    match = re.search(r"Motionnaires\s*:\s*([\s\S]{0,700}?)(?:La Tour-de-Peilz|Motion\s*:|Objectif|Monsieur|Madame)", text, flags=re.I)
    authors = merge_people(listing_authors, extract_motionnaire_people(text))
    if match:
        authors = merge_people(authors, extract_people(match.group(1)))

    footer_match = re.search(r"Au nom des motionnaires,\s*([\s\S]{0,500})", text, flags=re.I)
    if footer_match:
        footer_people = extract_people(footer_match.group(1))
        if not footer_people:
            footer_name = re.split(r"\n|Au Conseil|Madame|Monsieur", footer_match.group(1), maxsplit=1)[0]
            footer_name = clean_person_name(footer_name)
            footer_people = [{"name": footer_name}] if len(footer_name.split()) >= 2 else []
        authors = merge_people(authors, footer_people)
    return authors


def extract_motion_object_title(text: str) -> str | None:
    match = re.search(r"\bMotion\s*:\s*([\s\S]{0,260}?)(?:\n\s*\n|Un contexte|Au cours|En consid[ée]ration|$)", text, flags=re.I)
    if not match:
        return None
    title = clean_french_text(re.sub(r"\s+", " ", match.group(1))).strip(" .:-")
    return title or None


def extract_commission_section(text: str) -> str | None:
    patterns = [
        r"La commission compos[ée]e de\s*:\s*([\s\S]{0,1500}?)(?:S[' ]est r[ée]unie|s'est r[ée]unie|S est r[ée]unie)",
        r"Commission compos[ée]e de\s*:\s*([\s\S]{0,1500}?)(?:S[' ]est r[ée]unie|s'est r[ée]unie|S est r[ée]unie)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if match:
            return match.group(1)
    return None


def extract_commission_members(text: str) -> list[dict]:
    section = extract_commission_section(text)
    if not section:
        return []
    members = extract_people(section)
    section_lines = [re.sub(r"\s+", " ", line).strip() for line in section.splitlines() if line.strip()]
    enriched = []
    for member in members:
        role = "member"
        attendance = "present"
        for line in section_lines:
            if member["name"] in line and "président" in line.lower():
                role = "president"
            if member["name"] in line and "rapporteur" in line.lower():
                role = "rapporteur"
            if member["name"] in line and re.search(r"excus[Ã©e]e?", line, flags=re.I):
                attendance = "excused"
        enriched.append({**member, "role": role, "attendance": attendance})
    return enriched


def extract_commission_meeting(text: str, default_year: str) -> dict | None:
    match = re.search(
        r"(?:S[' ]est r[ée]unie|s'est r[ée]unie|S est r[ée]unie)\s+(?:le\s+)?([\s\S]{0,320}?)(?:\.\s|Les membres|La commission|En d[ée]but|\n\n)",
        text,
        flags=re.I,
    )
    if not match:
        return None
    raw = re.sub(r"\s+", " ", match.group(1)).strip()
    date = parse_french_date(raw, default_year=default_year)
    time_match = re.search(r"\b(\d{1,2})h(?:[. ]?(\d{2}))?", raw)
    place_match = re.search(r"\b(?:à|a)\s+(?!\d{1,2}h)(.+)$", raw)
    meeting = {"raw": raw}
    if date:
        meeting["date"] = date
    if time_match:
        meeting["time"] = f"{int(time_match.group(1)):02d}:{time_match.group(2) or '00'}"
    if place_match:
        place = re.sub(r"^\d{1,2}h(?:[. ]?\d{2})?\s+(?:à|a)\s+", "", place_match.group(1).strip(), flags=re.I)
        meeting["place"] = place
    return meeting


def extract_invited_people(text: str) -> list[dict]:
    invited = []
    patterns = [
        r"remercient\s+([\s\S]{0,500}?)(?:d'avoir|pour avoir|qui)",
        r"en pr[ée]sence de\s+([\s\S]{0,500}?)(?:\.|\n)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if not match:
            continue
        invited.extend(extract_people(match.group(1)))
    seen = set()
    unique = []
    for person in invited:
        key = person["name"].casefold()
        if key not in seen:
            seen.add(key)
            unique.append(person)
    return unique


def extract_rapporteur(text: str, members: list[dict]) -> str | None:
    for member in members:
        if member.get("role") == "rapporteur":
            return member["name"]
    match = re.search(r"Pour la commission\s+([A-ZÉÈÀÂÄÇ][A-Za-zÀ-ÖØ-öø-ÿ' -]+)", text, flags=re.I)
    if match:
        return re.sub(r"\s+", " ", match.group(1)).strip()
    return None


def extract_commission_recommendation(text: str) -> dict | None:
    conclusion = re.search(r"Conclusion\s+([\s\S]{0,1400})", text, flags=re.I)
    section = conclusion.group(1) if conclusion else text[-2500:]
    normalized = normalize(section)
    recommendation = None
    if "rejeter" in normalized or "ne pas la renvoyer" in normalized:
        recommendation = "reject_motion"
    elif "prendre en consideration" in normalized or "renvoyer a la municipalite" in normalized:
        recommendation = "support_motion"

    vote = None
    vote_match = re.search(r"\b(?:à|a)\s+l['’]unanimit[ée](?:\s+moins\s+([^,.;]+))?", section, flags=re.I)
    if vote_match:
        vote = {"raw": re.sub(r"\s+", " ", vote_match.group(0)).strip(), "unanimous": True}
        if vote_match.group(1):
            vote["exceptions"] = vote_match.group(1).strip()

    if not recommendation and not vote:
        return None
    result = {}
    if recommendation:
        result["recommendation"] = recommendation
    if vote:
        result["vote"] = vote
    return result


def extract_council_decision(text: str, default_year: str) -> dict | None:
    match = re.search(r"EXTRAIT\s+du proc[èe]s-verbal[\s\S]{0,1800}", text, flags=re.I)
    if not match:
        return None
    section = match.group(0)
    normalized = normalize(section)
    session_date = None
    session_match = re.search(r"s[ée]ance du Conseil communal[\s\S]{0,140}?du\s+(.{0,80})", section, flags=re.I)
    if session_match:
        session_date = parse_french_date(session_match.group(1), default_year=default_year)
    decision_date = parse_signature_date(section, default_year=default_year) or session_date

    outcome = None
    if "rejeter la prise en consideration" in normalized or "ne pas la renvoyer" in normalized:
        outcome = "rejected_not_referred"
    elif "renvoyer a la municipalite" in normalized:
        outcome = "referred_to_municipality"
    elif "adopte" in normalized:
        outcome = "adopted"

    vote = None
    vote_match = re.search(r"Adopt[ée]\s+à\s+(.+?)(?:\.|\n)", section, flags=re.I)
    if vote_match:
        raw_vote = re.sub(r"\s+", " ", vote_match.group(0)).strip()
        vote = {"raw": raw_vote}
        vote_text = normalize(raw_vote)
        if "large majorite" in vote_text:
            vote["result"] = "large_majority"
        contrary_match = re.search(r"\(([^)]*avis contraires?[^)]*)\)", raw_vote, flags=re.I)
        if contrary_match:
            vote["contrary_votes_raw"] = contrary_match.group(1)

    if not any([session_date, decision_date, outcome, vote]):
        return None
    decision = {}
    if session_date:
        decision["session_date"] = session_date
    if decision_date:
        decision["decision_date"] = decision_date
    if outcome:
        decision["outcome"] = outcome
    if vote:
        decision["vote"] = vote
    return decision


def infer_report_flags(label: str, filename: str, text: str, report_type: str | None) -> dict:
    normalized = normalize(f"{label} {filename} {text[:4000]}")
    return {
        "contains_report": "rapport" in normalized or "rapp" in normalize(filename),
        "contains_decision": "decision" in normalized or "-dec" in normalize(filename),
        "contains_majority_report": report_type == "majority_report" or "rapport de majorite" in normalized,
        "contains_minority_report": report_type == "minority_report" or "rapport de minorite" in normalized,
    }


def extract_document_components(text: str, role: str, report_type: str | None) -> list[dict]:
    components = []
    if role in {"motion_text", "combined_motion_report", "combined_motion_report_decision"}:
        components.append({"role": "motion_text"})
    if role in {"commission_report", "combined_motion_report", "combined_motion_report_decision"}:
        component = {"role": "commission_report"}
        if report_type:
            component["report_type"] = report_type
        components.append(component)
    if role in {"council_decision", "combined_motion_report_decision"}:
        components.append({"role": "council_decision"})
    return components


def slugify(value: str) -> str:
    value = normalize(value)
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value[:90].strip("-")


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
            if not normalize(listing_title).startswith("motion "):
                continue

            subject = clean_html_text(summary_match.group(1)) if summary_match else ""
            status_raw, status_normalized = parse_listing_status(listing_title)
            filename = safe_filename(pdf_url)
            authors = parse_authors_from_listing(listing_title)
            object_id = f"motion-{listing_year}-{slugify(subject or listing_title or filename)}"

            items_by_url[pdf_url] = {
                "commune": "La Tour-de-Peilz",
                "type": "motion",
                "document_type": "motion",
                "year": pdf_year,
                "listing_year": listing_year,
                "category": "motions",
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
                "authors": authors,
            }
    return list(sorted(items_by_url.values(), key=lambda item: (item["listing_year"], item["filename"])))


def extract_pdf_text(pdf_path: Path) -> str:
    document = fitz.open(pdf_path)
    return "\n".join(clean_pdf_text(page.get_text()) for page in document)


def enrich_motion_metadata(item: dict, text: str) -> dict:
    role, report_type = infer_document_role(item["site_listing_title"], item["filename"], text)
    flags = infer_report_flags(item["site_listing_title"], item["filename"], text, report_type)
    members = extract_commission_members(text) if flags["contains_report"] else []
    meeting = extract_commission_meeting(text, item["listing_year"]) if flags["contains_report"] else None
    invited = extract_invited_people(text) if flags["contains_report"] else []
    rapporteur = extract_rapporteur(text, members) if flags["contains_report"] else None
    recommendation = extract_commission_recommendation(text) if flags["contains_report"] else None
    council_decision = extract_council_decision(text, item["listing_year"]) if flags["contains_decision"] else None
    document_date = (
        parse_signature_date(text, default_year=item["listing_year"])
        or parse_french_date(text[:2500], default_year=item["listing_year"])
    )
    authors = extract_motion_authors(text, item["authors"])
    object_title = extract_motion_object_title(text) or item.get("summary") or None
    search_facets = [
        "motion",
        "conseil_communal",
        "municipalite",
        item["status_normalized"],
        role,
        report_type,
        *[person.get("party") for person in authors if person.get("party")],
        *re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ]{4,}", item["summary"].lower())[:8],
    ]
    search_facets = [slugify(facet).replace("-", "_") for facet in search_facets if facet]

    political_object = {
        "type": "motion",
        "object_type": "motion",
        "object_id": item["political_object_id"],
        "status_raw": item.get("site_status_raw"),
        "status_normalized": item["status_normalized"],
        "target_body": "Municipalite",
        "decision_body": "Conseil communal",
        "request_type": "binding_request",
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
        "content_kind": "political_motion",
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
    target_dir = OUTPUT_ROOT / item["year"] / "motions"
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

    metadata = enrich_motion_metadata(item, text)
    metadata["pdf_path"] = str(pdf_path)
    metadata["text_path"] = str(txt_path)
    json_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return metadata


def token_set(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]{4,}", slugify(value).replace("-", " "))
        if token not in {"motion", "rapp", "rapport", "decision", "consorts"}
    }


def find_related_motion(metadata: dict, canonical_results: list[dict]) -> dict | None:
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


def mark_agenda_linked_motion_documents(canonical_results: list[dict]) -> list[dict]:
    updated = []
    for json_path in sorted(OUTPUT_ROOT.glob("*/motions/*.json")):
        metadata = json.loads(json_path.read_text(encoding="utf-8-sig"))
        if metadata.get("canonical_object"):
            continue
        source_page = str(metadata.get("source_page", ""))
        if "apercu_ordre-du-jour.php" not in source_page:
            continue

        related = find_related_motion(metadata, canonical_results)
        text = ""
        text_path = Path(str(metadata.get("text_path") or json_path.with_suffix(".txt")))
        if text_path.exists():
            text = text_path.read_text(encoding="utf-8", errors="ignore")
        extracted_authors = extract_motion_authors(text, metadata.get("authors") or []) if text else []
        object_title = extract_motion_object_title(text) if text else None
        metadata["canonical_object"] = False
        metadata["source_collection"] = "ordre-du-jour-linked-document"
        metadata["linked_to_session"] = True
        metadata["metadata_version"] = "metadata-audit-v2"
        metadata.setdefault("type", "motion")
        metadata.setdefault("document_type", "motion")
        metadata.setdefault("category", "motions")
        metadata["agenda_linked_document"] = {
            "source_page": source_page,
            "role": metadata.get("document_role") or metadata.get("type") or "motion_related_document",
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
            metadata["related_canonical_motion"] = {
                "political_object_id": related["political_object_id"],
                "title": related["site_listing_title"],
                "object_title": related.get("object_title") or related.get("summary"),
                "authors": related.get("authors") or [],
                "filename": related["filename"],
                "pdf_url": related["pdf_url"],
            }
            if not metadata.get("authors") and related.get("authors"):
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
    print(f"Found {len(items)} canonical motions for years 2021-2026.")

    results = []
    failures = []
    for index, item in enumerate(items, start=1):
        print(f"[{index}/{len(items)}] {item['listing_year']} {item['filename']}")
        try:
            results.append(download_and_extract(item))
        except Exception as exc:
            failures.append({"pdf_url": item["pdf_url"], "filename": item["filename"], "error": str(exc)})
            print(f"  ERROR: {exc}")

    agenda_linked_updates = mark_agenda_linked_motion_documents(results)

    manifest = {
        "commune": "La Tour-de-Peilz",
        "legislature": "2021-2026",
        "source_page": SOURCE_PAGE,
        "scope_note": "Scraper canonique des motions uniquement. Les postulats, interpellations et documents récupérés depuis les ordres du jour ne sont pas utilisés comme source canonique des motions.",
        "years": sorted(YEARS),
        "documents_downloaded": len(results),
        "failures": failures,
        "agenda_linked_documents_marked_noncanonical": agenda_linked_updates,
        "documents": results,
    }
    manifest_path = DATA_ROOT / "manifest_motions_2021_2026.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Downloaded/extracted: {len(results)}")
    print(f"Failures: {len(failures)}")
    print(f"Manifest: {manifest_path}")


if __name__ == "__main__":
    main()
