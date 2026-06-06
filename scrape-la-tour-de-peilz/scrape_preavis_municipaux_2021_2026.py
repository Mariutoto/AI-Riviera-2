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
SOURCE_PAGE = "https://www.la-tour-de-peilz.ch/politique/preavis-municipaux.php"
YEARS = {str(year) for year in range(2021, 2027)}
OUTPUT_ROOT = PROJECT_ROOT / "documents" / "la-tour-de-peilz"
DATA_ROOT = PROJECT_ROOT / "data" / "preavis-municipaux" / "la-tour-de-peilz"
HEADERS = {"User-Agent": "AI-Riviera preavis municipaux importer"}

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
    if "preavis-municipaux" not in decoded.lower():
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


def parse_preavis_number(label: str, filename: str, object_title: str = "") -> str | None:
    haystack = f"{label} {filename} {object_title}"
    match = re.search(r"\bNr\.\s*(\d{1,2})\s*\|\s*(20\d{2})\b", haystack, flags=re.I)
    if match:
        return f"{int(match.group(1))}/{match.group(2)}"
    match = re.search(r"\bpr[ée]avis(?:\s+municipal)?\s*(?:n[°o]\s*)?(\d{1,2})/(20\d{2})\b", haystack, flags=re.I)
    if match:
        return f"{int(match.group(1))}/{match.group(2)}"
    match = re.search(r"\bPreavis[-_ ]?(\d{1,2})(?:[-_/]|$)", filename, flags=re.I)
    if match:
        year_match = re.search(r"(20\d{2})", haystack)
        if year_match:
            return f"{int(match.group(1))}/{year_match.group(1)}"
    return None


def parse_listing_status(label: str) -> str | None:
    normalized = normalize(label)
    if "retire" in normalized:
        return "withdrawn_by_municipality"
    if "rapport" in normalized and "decision" in normalized:
        return "with_report_and_decision"
    if "rapport" in normalized:
        return "with_report"
    if "decision" in normalized:
        return "with_decision"
    return None


def parse_french_date(text: str) -> str | None:
    match = re.search(
        r"\b(?:le\s+)?(\d{1,2}|1er)\s+"
        r"(janvier|f[eé]vrier|mars|avril|mai|juin|juillet|ao[uû]t|septembre|octobre|novembre|d[eé]cembre)"
        r"\s+(20\d{2})\b",
        text,
        flags=re.I,
    )
    if not match:
        return None
    day_raw, month_raw, year = match.groups()
    day = 1 if day_raw.lower() == "1er" else int(day_raw)
    month = MONTHS_FR.get(month_raw.lower())
    if not month:
        return None
    return f"{year}-{month}-{day:02d}"


def parse_signature_date(text: str) -> str | None:
    for match in re.finditer(r"La Tour-de-Peilz,\s*(?:le\s+)?(.{0,80})", text, flags=re.I):
        parsed = parse_french_date(match.group(1))
        if parsed:
            return parsed
    return None


def first_matching_line(text: str, pattern: str) -> str | None:
    for line in text.splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if re.search(pattern, line, flags=re.I):
            return line
    return None


def page_has_line(text: str, pattern: str) -> bool:
    return first_matching_line(text, pattern) is not None


def infer_document_components(text: str) -> list[dict]:
    components = []
    seen = set()
    for page_number, page_text in enumerate(text.split("\f"), start=1):
        page_norm = normalize(page_text)
        candidates = [
            ("municipal_preavis", None, r"\bpr[ée]avis municipal\s+n[°o]\s*\d{1,2}/20\d{2}\b"),
            ("commission_report", "majority_report", r"\brapport de majorit[ée]\b"),
            ("commission_report", "minority_report", r"\brapport de minorit[ée]\b"),
            ("council_decision", None, r"\bd[ée]cision du conseil communal\b|\ble conseil communal\b[\s\S]{0,400}\bd[ée]cide\b"),
        ]
        for role, report_type, pattern in candidates:
            key = (role, report_type)
            if key in seen or not re.search(pattern, page_norm, flags=re.I):
                continue
            title = title_for_component(page_text, role, report_type)
            component = {"role": role, "start_page": page_number}
            if report_type:
                component["report_type"] = report_type
            if title:
                component["title"] = title
            components.append(component)
            seen.add(key)
    return components


def title_for_component(text: str, role: str, report_type: str | None = None) -> str | None:
    if role == "commission_report" and report_type == "majority_report":
        return first_matching_line(text, r"\brapport de majorit[ée]\b")
    if role == "commission_report" and report_type == "minority_report":
        return first_matching_line(text, r"\brapport de minorit[ée]\b")
    if role == "commission_report":
        return first_matching_line(text, r"\brapport de la commission\b")
    if role == "municipal_preavis":
        return first_matching_line(text, r"\bpr[ée]avis municipal\s+n[°o]\s*\d{1,2}/20\d{2}\b")
    if role == "council_decision":
        return first_matching_line(text, r"\bd[ée]cision du conseil communal\b") or "Décision du Conseil communal"
    return None


def infer_document_role(label: str, filename: str, text: str, components: list[dict]) -> tuple[str, str | None]:
    normalized_label = normalize(label)
    normalized_filename = normalize(filename)
    normalized_text = normalize(text[:4000])

    if "rapport de majorite" in normalized_text or "rapp-maj" in normalized_filename:
        return "commission_report", "majority_report"
    if "rapport de minorite" in normalized_text or "rapp-min" in normalized_filename:
        return "commission_report", "minority_report"
    if "rapport" in normalized_filename and "preavis municipal" not in normalized_text[:500]:
        return "commission_report", "standard_report"
    if "decision" in normalized_filename and "preavis municipal" not in normalized_text[:500]:
        return "council_decision", None

    roles = {component.get("role") for component in components}
    if len(roles) > 1 or "rapport" in normalized_label or "decision" in normalized_label or "rapp-dec" in normalized_filename:
        return "combined_preavis_report_decision", None
    return "municipal_preavis", None


def infer_title(item: dict, text: str, role: str, report_type: str | None, components: list[dict]) -> str:
    if role == "commission_report":
        component_title = title_for_component(text, role, report_type)
        if component_title:
            return component_title
    if role == "municipal_preavis":
        component_title = title_for_component(text, role)
        if component_title:
            return component_title
    if role == "council_decision":
        component_title = title_for_component(text, role)
        if component_title:
            return component_title
    if role == "combined_preavis_report_decision":
        return item["official_listing_label"]
    return item["filename"]


def date_after_pattern(text: str, pattern: str, window_size: int = 500) -> str | None:
    for match in re.finditer(pattern, text, flags=re.I):
        window = text[match.start() : match.end() + window_size]
        parsed = parse_french_date(window)
        if parsed:
            return parsed
    return None


def extract_preavis_date(text: str, preavis_number: str | None) -> str | None:
    expected_number = None
    expected_year = None
    if preavis_number:
        expected_number, expected_year = preavis_number.split("/", 1)

    lines = text.splitlines()
    for index, line in enumerate(lines):
        normalized_line = normalize(line).strip()
        if "preavis municipal" not in normalized_line:
            continue
        if expected_number and expected_year:
            if f"{int(expected_number)}/{expected_year}" not in normalized_line and f"0{int(expected_number)}/{expected_year}" not in normalized_line:
                continue
        window = "\n".join(lines[index : index + 6])
        parsed = parse_french_date(window)
        if parsed:
            return parsed

    return date_after_pattern(text, r"pr[Ã©eée]avis municipal\s+n[Â°o]\s*\d{1,2}/20\d{2}")


def extract_section_date(text: str, start_pattern: str, stop_pattern: str | None = None) -> str | None:
    match = re.search(start_pattern, text, flags=re.I)
    if not match:
        return None
    section_end = len(text)
    if stop_pattern:
        stop_match = re.search(stop_pattern, text[match.end() :], flags=re.I)
        if stop_match:
            section_end = match.end() + stop_match.start()
    section = text[match.start() : section_end]
    return parse_signature_date(section) or parse_french_date(section[:2500])


def extract_report_date(text: str) -> str | None:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        normalized_line = normalize(line).strip()
        if not (
            normalized_line.startswith("rapport de la commission")
            or normalized_line.startswith("rapport de majorite")
            or normalized_line.startswith("rapport de minorite")
        ):
            continue
        section_lines = []
        for section_line in lines[index:]:
            normalized_section_line = normalize(section_line).strip()
            if section_lines and normalized_section_line.startswith("preavis municipal"):
                break
            section_lines.append(section_line)
        section = "\n".join(section_lines)
        parsed = parse_signature_date(section) or parse_french_date(section[:2500])
        if parsed:
            return parsed
    return None


def extract_decision_date(text: str) -> str | None:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        normalized_line = normalize(line).strip()
        if "decision du conseil communal" not in normalized_line and normalized_line != "extrait":
            continue
        section = "\n".join(lines[index : index + 80])
        if normalized_line == "extrait" and "conseil communal" not in normalize(section):
            continue
        parsed = parse_signature_date(section) or parse_french_date(section[:2500])
        if parsed:
            return parsed
    return None


def extract_component_dates(text: str, role: str, preavis_number: str | None) -> dict[str, str]:
    preavis_date = extract_preavis_date(text, preavis_number)
    report_date = extract_report_date(text)
    decision_date = extract_decision_date(text)

    dates = {}
    if preavis_date:
        dates["preavis_date"] = preavis_date
    if report_date:
        dates["report_date"] = report_date
    if decision_date:
        dates["decision_date"] = decision_date

    if preavis_date:
        dates["document_date"] = preavis_date
    elif role == "commission_report" and report_date:
        dates["document_date"] = report_date
    elif role == "council_decision" and decision_date:
        dates["document_date"] = decision_date
    else:
        fallback = parse_signature_date(text) or parse_french_date(text[:2500])
        if fallback:
            dates["document_date"] = fallback
    return dates


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

            label = clean_html_text(title_match.group(1)) if title_match else Path(unquote(urlparse(pdf_url).path)).stem
            object_title = clean_html_text(summary_match.group(1)) if summary_match else ""
            filename = safe_filename(pdf_url)
            preavis_number = parse_preavis_number(label, filename, object_title)

            items_by_url[pdf_url] = {
                "commune": "La Tour-de-Peilz",
                "year": pdf_year,
                "listing_year": listing_year,
                "category": "preavis-municipaux",
                "legislature": "2021-2026",
                "preavis_number": preavis_number,
                "official_listing_label": label,
                "object_title": object_title,
                "listing_status": parse_listing_status(label),
                "listing_contains_report": "rapport" in normalize(label),
                "listing_contains_decision": "decision" in normalize(label),
                "filename": filename,
                "pdf_url": pdf_url,
                "source_page": SOURCE_PAGE,
                "source_kind": "preavis_municipaux_page",
                "canonical_family_source": SOURCE_PAGE,
            }

    return list(sorted(items_by_url.values(), key=lambda item: (item["listing_year"], item.get("preavis_number") or "99/9999", item["filename"])))


def extract_pdf_text(pdf_path: Path) -> tuple[str, str]:
    document = fitz.open(pdf_path)
    page_texts = [clean_pdf_text(page.get_text()) for page in document]
    return "\n".join(page_texts), "\f".join(page_texts)


def download_and_extract(item: dict) -> dict:
    year = item["year"]
    filename = item["filename"]
    target_dir = OUTPUT_ROOT / year / "preavis-municipaux"
    target_dir.mkdir(parents=True, exist_ok=True)

    pdf_path = target_dir / filename
    txt_path = pdf_path.with_suffix(".txt")
    json_path = pdf_path.with_suffix(".json")

    if not pdf_path.exists():
        response = requests.get(item["pdf_url"], headers=HEADERS, timeout=90)
        response.raise_for_status()
        pdf_path.write_bytes(response.content)

    text, paged_text = extract_pdf_text(pdf_path)
    components = infer_document_components(paged_text)
    role, report_type = infer_document_role(item["official_listing_label"], filename, text, components)
    title = infer_title(item, text, role, report_type, components)
    component_dates = extract_component_dates(text, role, item.get("preavis_number"))
    document_date = component_dates.get("document_date")

    txt_path.write_text(text + "\n", encoding="utf-8")

    metadata = {
        **item,
        "title": title,
        "document_role": role,
        "report_type": report_type,
        "document_components": components,
        "contains_preavis": any(component.get("role") == "municipal_preavis" for component in components) or role == "municipal_preavis",
        "contains_report": item["listing_contains_report"] or any(component.get("role") == "commission_report" for component in components) or role == "commission_report",
        "contains_decision": any(component.get("role") == "council_decision" for component in components) or role == "council_decision",
        "contains_majority_report": any(component.get("report_type") == "majority_report" for component in components) or report_type == "majority_report",
        "contains_minority_report": any(component.get("report_type") == "minority_report" for component in components) or report_type == "minority_report",
        "document_date": document_date,
        **component_dates,
        "pdf_path": str(pdf_path),
        "text_path": str(txt_path),
        "text_extraction_status": {
            "characters_extracted": len(text),
            "text_available": bool(text.strip()),
            "needs_ocr": False,
        },
        "metadata_version": "metadata-audit-v2",
    }
    if report_type is None:
        metadata.pop("report_type")
    if document_date is None:
        metadata.pop("document_date")

    json_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return metadata


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    items = collect_items()
    print(f"Found {len(items)} canonical preavis documents for years 2021-2026.")

    results = []
    failures = []
    for index, item in enumerate(items, start=1):
        print(f"[{index}/{len(items)}] listing {item['listing_year']} file {item['year']} {item['filename']}")
        try:
            results.append(download_and_extract(item))
        except Exception as exc:
            failures.append({"pdf_url": item["pdf_url"], "filename": item["filename"], "error": str(exc)})
            print(f"  ERROR: {exc}")

    manifest = {
        "commune": "La Tour-de-Peilz",
        "legislature": "2021-2026",
        "source_page": SOURCE_PAGE,
        "source_kind": "preavis_municipaux_page",
        "scope_note": "Canonical scrape of the official preavis-municipaux.php page for legislature 2021-2026. Agenda pages are not used as title sources.",
        "years": sorted(YEARS),
        "documents_downloaded": len(results),
        "failures": failures,
        "documents": results,
    }
    manifest_path = DATA_ROOT / "manifest_preavis_municipaux_2021_2026.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"Downloaded/extracted: {len(results)}")
    print(f"Failures: {len(failures)}")
    print(f"Manifest: {manifest_path}")


if __name__ == "__main__":
    main()
