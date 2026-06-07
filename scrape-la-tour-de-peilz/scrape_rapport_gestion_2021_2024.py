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
SOURCE_PAGE = "https://www.la-tour-de-peilz.ch/politique/rapport-comptes-budget.php"
YEARS = {"2021", "2022", "2023", "2024"}
OUTPUT_ROOT = PROJECT_ROOT / "documents" / "la-tour-de-peilz"
DATA_ROOT = PROJECT_ROOT / "data" / "financial-reports" / "la-tour-de-peilz"
HEADERS = {"User-Agent": "AI-Riviera rapport de gestion importer"}


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
    if "rapport-de-gestion" not in decoded.lower():
        return None
    return full_url


def clean_html_text(raw_html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", raw_html)
    text = html.unescape(text)
    return clean_french_text(re.sub(r"\s+", " ", text)).strip()


def normalize(text: str) -> str:
    return motion_tools.normalize(text)


def slugify(value: str) -> str:
    return motion_tools.slugify(value)


def year_from_text(text: str) -> str | None:
    match = re.search(r"\b(2021|2022|2023|2024)\b", text)
    return match.group(1) if match else None


def safe_filename(pdf_url: str) -> str:
    name = Path(unquote(urlparse(pdf_url).path)).name
    return re.sub(r'[<>:"/\\|?*]', "_", name)


def management_period(year: str) -> dict:
    return {
        "management_year": year,
        "period_start": f"{year}-01-01",
        "period_end": f"{year}-12-31",
        "fiscal_year": year,
    }


def collect_items() -> list[dict]:
    page_html = fetch_text(SOURCE_PAGE)
    items_by_url = {}
    for li in re.findall(r'<li class="prestation[^>]*>([\s\S]*?)</li>', page_html, flags=re.I):
        href_match = re.search(r'href=["\']([^"\']+)["\']', li, flags=re.I)
        if not href_match:
            continue

        title = clean_html_text(li)
        year = year_from_text(title)
        pdf_url = normalize_pdf_url(SOURCE_PAGE, href_match.group(1))
        if not year or year not in YEARS or not pdf_url:
            continue

        lower_title = normalize(title)
        if "rapport de la commission de gestion" not in lower_title or "reponse de la municipalite" not in lower_title:
            continue

        filename = safe_filename(pdf_url)
        items_by_url[pdf_url] = {
            "commune": "La Tour-de-Peilz",
            "type": "rapport_de_gestion",
            "document_type": "rapport_de_gestion_commission_reponse_municipalite",
            "year": year,
            **management_period(year),
            "category": "rapport-de-gestion",
            "legislature": "2021-2026",
            "title": title,
            "object_title": f"Rapport de gestion {year} de la Municipalite",
            "filename": filename,
            "pdf_url": pdf_url,
            "source_page": SOURCE_PAGE,
            "source_collection": "rapport-comptes-budget",
            "canonical_object": True,
            "political_object_id": f"rapport-gestion-{year}",
        }
    return list(sorted(items_by_url.values(), key=lambda item: item["year"]))


def extract_pdf_text(pdf_path: Path) -> str:
    document = fitz.open(pdf_path)
    return clean_french_text("\n".join(page.get_text() for page in document))


def contains_money(text: str) -> bool:
    return bool(re.search(r"\b\d[\d' .]*(?:fr\.|CHF|mio|million)", text, flags=re.I))


def infer_components(filename: str, text: str) -> list[dict]:
    normalized = normalize(f"{filename} {text}")
    components = [{"role": "annual_management_report", "issuing_body": "Municipalite"}]
    if "rapport de la commission de gestion" in normalized or "commission de gestion" in normalized:
        components.append({"role": "commission_report", "issuing_body": "Commission de gestion"})
    if "reponse de la municipalite" in normalized or "reponses de la municipalite" in normalized:
        components.append({"role": "municipality_response", "issuing_body": "Municipalite"})
    if "extrait du proces-verbal" in normalized or ("le conseil communal" in normalized and "decide" in normalized):
        components.append({"role": "council_decision", "issuing_body": "Conseil communal"})
    return components


def infer_document_role(filename: str, text: str) -> str:
    roles = {component["role"] for component in infer_components(filename, text)}
    if {"commission_report", "municipality_response", "council_decision"} & roles:
        return "combined_management_report_commission_response_decision"
    return "annual_management_report"


def extract_table_sections(text: str) -> list[dict]:
    head = text[:3500]
    if "TABLE DES MAT" in head.upper():
        head = head.split("TABLE DES MAT", 1)[0]
    sections = []
    seen = set()
    for raw_line in head.splitlines():
        line = clean_french_text(raw_line).strip(" .")
        if not line or len(line) < 4 or len(line) > 80 or re.search(r"\d", line):
            continue
        normalized = normalize(line)
        if normalized.startswith("rapport de gestion"):
            continue
        if line.upper() != line and not any(term in normalized for term in ["municipalite", "finances", "urbanisme", "famille", "conseil"]):
            continue
        key = slugify(line)
        if not key or key in seen:
            continue
        seen.add(key)
        sections.append({"title": line, "facet": key.replace("-", "_")})
    return sections


def extract_commission_members(text: str) -> list[dict]:
    section = None
    for pattern in [
        r"Commission de gestion\s*([\s\S]{0,900}?)(?:Commission de recours|Commissions intercommunales|CONSEIL COMMUNAL)",
        r"composition de la Commission de gestion[\s\S]{0,500}?est la suivante,?\s*([\s\S]{0,900}?)(?:La Commission|COGEST|$)",
    ]:
        match = re.search(pattern, text, flags=re.I)
        if match:
            section = match.group(1)
            break
    if not section:
        return []

    members = []
    for person in motion_tools.extract_people_with_parties(section) + motion_tools.extract_people(section):
        name = person.get("name")
        if not name or len(name.split()) < 2:
            continue
        member = dict(person)
        line_match = re.search(rf"{re.escape(name)}[^\n]*", section, flags=re.I)
        line = line_match.group(0) if line_match else ""
        member["role"] = "president" if "pres" in normalize(line) else "member"
        members.append(member)
    return motion_tools.merge_people(members)


def extract_commission_report_info(text: str, year: str, members: list[dict]) -> dict:
    normalized = normalize(text)
    info = {
        "members": members,
        "contains_observations": "observation" in normalized,
        "contains_wishes": "voeu" in normalized or "voeux" in normalized,
    }
    meeting_match = re.search(r"Commission de Gestion s[' ]est r[ée]unie\s+(?:le\s+)?([\s\S]{0,120})", text, flags=re.I)
    if meeting_match:
        raw = re.sub(r"\s+", " ", meeting_match.group(1)).strip(" .")
        parsed = motion_tools.parse_french_date(raw, default_year=str(int(year) + 1))
        info["meeting"] = {"raw": raw}
        if parsed:
            info["meeting"]["date"] = parsed
    if "unanimite" in normalized or "unanimite de ses membres" in normalized:
        info["recommendation"] = {"outcome": "approve_management_report", "vote": {"unanimous": True}}
    rapporteur_match = re.search(r"Au nom de la Commission de Gestion\s*:\s*([^\n]+)", text, flags=re.I)
    if rapporteur_match:
        rapporteur = motion_tools.clean_person_name(rapporteur_match.group(1))
        if len(rapporteur.split()) >= 2:
            info["rapporteur"] = rapporteur
    return {key: value for key, value in info.items() if value not in (None, [], {})}


def extract_council_decision(text: str, year: str) -> dict | None:
    match = re.search(r"EXTRAIT\s+du proc[èe]s-verbal[\s\S]{0,1800}", text, flags=re.I)
    if not match:
        return None
    section = match.group(0)
    decision = {"body": "Conseil communal", "outcome": "approved_management_report"}
    session_match = re.search(r"s[ée]ance du Conseil communal[\s\S]{0,120}?du\s+(.{0,80})", section, flags=re.I)
    if session_match:
        parsed = motion_tools.parse_french_date(session_match.group(1), default_year=str(int(year) + 1))
        if parsed:
            decision["session_date"] = parsed
            decision["decision_date"] = parsed
    if "unanimite" in normalize(section):
        decision["vote"] = {"unanimous": True, "raw": "Ainsi adopte a l'unanimite."}
    if "donner decharge" in normalize(section):
        decision["includes_municipality_discharge"] = True
    return decision


def enrich_report_metadata(item: dict, text: str) -> dict:
    year = str(item["year"])
    components = infer_components(item["filename"], text)
    component_roles = {component["role"] for component in components}
    sections = extract_table_sections(text)
    members = extract_commission_members(text)
    commission = extract_commission_report_info(text, year, members)
    decision = extract_council_decision(text, year)
    document_date = decision.get("decision_date") if decision else f"{year}-12-31"
    search_facets = [
        "rapport_de_gestion",
        "rapport_gestion",
        "rapport_annuel",
        "gestion_municipale",
        "conseil_communal",
        "municipalite",
        "commission_de_gestion",
        "cogest",
        "reponse_municipalite" if "municipality_response" in component_roles else None,
        "decision_conseil" if "council_decision" in component_roles else None,
        *[section["facet"] for section in sections],
    ]
    metadata = {
        **item,
        "title": f"Rapport de gestion {year} + rapport de la commission de gestion + reponse de la Municipalite + decision",
        "summary": f"Rapport annuel de gestion de la Municipalite pour l'exercice {year}, avec rapport de la Commission de gestion, reponse de la Municipalite et decision du Conseil communal.",
        "document_date": document_date,
        "document_role": infer_document_role(item["filename"], text),
        "document_components": components,
        "report_sections": sections,
        "contains_budget_data": contains_money(text),
        "contains_staffing_data": any(term in normalize(text) for term in ["ressources humaines", "personnel", "effectif"]),
        "contains_investments": any(term in normalize(text) for term in ["investissement", "travaux", "chantier"]),
        "contains_commission_report": "commission_report" in component_roles,
        "contains_municipal_response": "municipality_response" in component_roles,
        "contains_council_decision": "council_decision" in component_roles,
        "content_kind": "annual_management_report",
        "metadata_version": "metadata-audit-v2",
        "search_facets": sorted(set(facet for facet in search_facets if facet)),
        "political_object": {
            "type": "rapport_de_gestion",
            "object_type": "annual_management_report",
            "object_id": item["political_object_id"],
            "object_title": item["object_title"],
            "management_year": year,
            "period_start": f"{year}-01-01",
            "period_end": f"{year}-12-31",
            "issuing_body": "Municipalite",
            "target_body": "Conseil communal",
            "commission_body": "Commission de gestion",
            "canonical_source": SOURCE_PAGE,
            "status_normalized": "approved" if decision else "published",
        },
        "report": {
            "fiscal_year": year,
            "management_year": year,
            "report_scope": "municipal_activity",
            "issuing_body": "Municipalite",
            "target_body": "Conseil communal",
            "large_document": len(text) > 50000,
            "contains_table_of_contents": bool(sections),
            "contains_commission_report": "commission_report" in component_roles,
            "contains_municipal_response": "municipality_response" in component_roles,
            "contains_council_decision": "council_decision" in component_roles,
        },
        "text_extraction_status": {
            "characters_extracted": len(text),
            "text_available": len(text.strip()) > 20,
            "needs_ocr": len(text.strip()) <= 20,
        },
        "characters_extracted": len(text),
    }
    if commission:
        metadata["commission"] = commission
    if decision:
        metadata["decision"] = decision
        metadata["political_object"]["decision"] = decision
    return metadata


def download_and_extract(item: dict) -> dict:
    year = item["year"]
    filename = item["filename"]
    target_dir = OUTPUT_ROOT / year / "rapport-de-gestion"
    target_dir.mkdir(parents=True, exist_ok=True)

    pdf_path = target_dir / filename
    txt_path = pdf_path.with_suffix(".txt")
    json_path = pdf_path.with_suffix(".json")

    if not pdf_path.exists():
        response = requests.get(item["pdf_url"], headers=HEADERS, timeout=90)
        response.raise_for_status()
        pdf_path.write_bytes(response.content)

    text = extract_pdf_text(pdf_path)
    txt_path.write_text(text + "\n", encoding="utf-8")

    metadata = enrich_report_metadata(item, text)
    metadata["pdf_path"] = str(pdf_path)
    metadata["text_path"] = str(txt_path)
    json_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return metadata


def mark_agenda_linked_report_documents(canonical_results: list[dict]) -> list[dict]:
    updated = []
    for json_path in sorted(OUTPUT_ROOT.glob("*/rapport-de-gestion/*.json")):
        metadata = json.loads(json_path.read_text(encoding="utf-8-sig"))
        if metadata.get("canonical_object"):
            continue
        source_page = str(metadata.get("source_page", ""))
        if "apercu_ordre-du-jour.php" not in source_page:
            continue
        year = str(metadata.get("year") or json_path.parent.parent.name)
        related = next((candidate for candidate in canonical_results if str(candidate.get("year")) == year), None)
        text_path = Path(str(metadata.get("text_path") or json_path.with_suffix(".txt")))
        text = text_path.read_text(encoding="utf-8", errors="ignore") if text_path.exists() else ""
        metadata.update(
            {
                "type": "rapport_de_gestion",
                "document_type": "rapport_de_gestion",
                "category": "rapport-de-gestion",
                **management_period(year),
                "object_title": f"Rapport de gestion {year} de la Municipalite",
                "canonical_object": False,
                "source_collection": "ordre-du-jour-linked-document",
                "linked_to_session": True,
                "metadata_version": "metadata-audit-v2",
                "content_kind": "annual_management_report",
                "document_role": "annual_management_report",
                "document_components": [{"role": "annual_management_report", "issuing_body": "Municipalite"}],
                "document_date": f"{year}-12-31",
                "political_object": {
                    "type": "rapport_de_gestion",
                    "object_type": "annual_management_report_linked_document",
                    "object_id": f"rapport-gestion-{year}-linked-agenda",
                    "object_title": f"Rapport de gestion {year} de la Municipalite",
                    "management_year": year,
                    "period_start": f"{year}-01-01",
                    "period_end": f"{year}-12-31",
                    "issuing_body": "Municipalite",
                    "target_body": "Conseil communal",
                    "canonical_object": False,
                },
                "text_extraction_status": {
                    "characters_extracted": len(text),
                    "text_available": len(text.strip()) > 20,
                    "needs_ocr": len(text.strip()) <= 20,
                },
                "agenda_linked_document": {
                    "source_page": source_page,
                    "role": "annual_management_report_linked_from_agenda",
                },
            }
        )
        if related:
            metadata["related_political_object_id"] = related["political_object_id"]
            metadata["related_canonical_report"] = {
                "political_object_id": related["political_object_id"],
                "title": related["title"],
                "object_title": related["object_title"],
                "filename": related["filename"],
                "pdf_url": related["pdf_url"],
            }
        json_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        updated.append({"path": str(json_path), "related_political_object_id": metadata.get("related_political_object_id")})
    return updated


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    items = collect_items()
    print(f"Found {len(items)} rapport de gestion documents for 2021-2024.")

    results = []
    failures = []
    for index, item in enumerate(items, start=1):
        print(f"[{index}/{len(items)}] {item['year']} {item['filename']}")
        try:
            results.append(download_and_extract(item))
        except Exception as exc:
            failures.append({"pdf_url": item["pdf_url"], "filename": item["filename"], "error": str(exc)})
            print(f"  ERROR: {exc}")

    linked_updates = mark_agenda_linked_report_documents(results)
    manifest = {
        "commune": "La Tour-de-Peilz",
        "legislature": "2021-2026",
        "source_page": SOURCE_PAGE,
        "scope_note": "Rapports de gestion canoniques 2021-2024 incluant rapport de la commission de gestion, reponse de la Municipalite et decision du Conseil communal. Les PDF repris depuis les ordres du jour sont marques comme documents lies, non canoniques.",
        "years": sorted(YEARS),
        "documents_downloaded": len(results),
        "agenda_linked_documents_updated": linked_updates,
        "failures": failures,
        "documents": results,
    }
    manifest_path = DATA_ROOT / "manifest_rapports_gestion_2021_2024.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"Downloaded/extracted: {len(results)}")
    print(f"Agenda-linked updated: {len(linked_updates)}")
    print(f"Failures: {len(failures)}")
    print(f"Manifest: {manifest_path}")


if __name__ == "__main__":
    main()
