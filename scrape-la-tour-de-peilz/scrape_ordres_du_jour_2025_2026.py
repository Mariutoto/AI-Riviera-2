import html
import json
import re
import sys
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import requests
import fitz

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.document_categories import normalize_document_category
from app.text_cleaning import clean_french_text


BASE_URL = "https://www.la-tour-de-peilz.ch/"
LIST_URL = "https://www.la-tour-de-peilz.ch/politique/ordre-du-jour.php"
START_DATE = date(2021, 9, 15)
TODAY = date.today()
HEADERS = {"User-Agent": "AI-Riviera agenda importer"}

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DOCUMENTS_ROOT = PROJECT_ROOT / "documents" / "la-tour-de-peilz"
SESSIONS_ROOT = PROJECT_ROOT / "data" / "sessions" / "la-tour-de-peilz"

MONTHS = {
    "janvier": 1,
    "février": 2,
    "fevrier": 2,
    "mars": 3,
    "avril": 4,
    "mai": 5,
    "juin": 6,
    "juillet": 7,
    "août": 8,
    "aout": 8,
    "septembre": 9,
    "octobre": 10,
    "novembre": 11,
    "décembre": 12,
    "decembre": 12,
}

SECTION_HEADINGS = [
    "Réponses aux interpellations",
    "Préavis",
    "Rapports",
    "Communications municipales",
    "Questions, propositions individuelles et divers",
    "Annexes",
    "Le Président",
    "La Secrétaire",
]


class TextAndLinksParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text_parts: list[str] = []
        self.links: list[dict] = []
        self.current_href: str | None = None
        self.current_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        if tag == "a":
            self.current_href = attrs_dict.get("href")
            self.current_text = []
        if tag in {"br", "p", "li", "div", "h1", "h2", "h3", "h4"}:
            self.text_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self.current_href:
            text = clean_spaces("".join(self.current_text))
            self.links.append({"href": self.current_href, "text": text})
            self.current_href = None
            self.current_text = []
        if tag in {"p", "li", "div", "h1", "h2", "h3", "h4"}:
            self.text_parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.text_parts.append(data)
        if self.current_href:
            self.current_text.append(data)


def clean_spaces(text: str) -> str:
    return clean_french_text(text)


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", clean_spaces(text)).casefold()


def slugify(text: str) -> str:
    text = normalize_text(text)
    replacements = {
        "à": "a",
        "â": "a",
        "ä": "a",
        "é": "e",
        "è": "e",
        "ê": "e",
        "ë": "e",
        "î": "i",
        "ï": "i",
        "ô": "o",
        "ö": "o",
        "ù": "u",
        "û": "u",
        "ü": "u",
        "ç": "c",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def fetch_text(url: str) -> str:
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    return response.text


def parse_french_date(label: str) -> date | None:
    match = re.search(r"(\d{1,2})\s+([A-Za-zÀ-ÿ]+)\s+(20\d{2})", label.lower())
    if not match:
        return None
    day = int(match.group(1))
    month = MONTHS[match.group(2)]
    year = int(match.group(3))
    return date(year, month, day)


def collect_session_pages() -> list[dict]:
    page_html = fetch_text(LIST_URL)
    sessions = []
    seen = set()
    for match in re.finditer(
        r"""href=["'](apercu_ordre-du-jour\.php\?id=\d+)["'][\s\S]{0,300}?Séance du\s+([^<]+)""",
        page_html,
        flags=re.I,
    ):
        detail_url = urljoin(LIST_URL, html.unescape(match.group(1)))
        if detail_url in seen:
            continue
        seen.add(detail_url)

        label = clean_spaces(f"Séance du {re.sub(r'<[^>]+>', ' ', match.group(2))}")
        session_date = parse_french_date(label)
        if not session_date:
            continue
        if START_DATE <= session_date <= TODAY:
            sessions.append({"source_url": detail_url, "label": label, "session_date": session_date})

    return sorted(sessions, key=lambda item: item["session_date"], reverse=True)


def normalize_pdf_url(page_url: str, href: str) -> str | None:
    full_url = urljoin(page_url, html.unescape(href))
    if "viewer.php" in full_url and "file=" in full_url:
        file_value = parse_qs(urlparse(full_url).query).get("file", [""])[0]
        full_url = urljoin(BASE_URL, unquote(file_value))
    if ".pdf" not in full_url.lower():
        return None
    return full_url


def infer_year_from_pdf_url(pdf_url: str, fallback_year: str) -> str:
    decoded = unquote(pdf_url)
    path = urlparse(decoded).path
    path_year = re.search(r"/(20\d{2})/", path)
    if path_year:
        return path_year.group(1)

    filename = Path(path).name
    filename_year = re.search(r"(20\d{2})", filename)
    if filename_year:
        return filename_year.group(1)

    date_year = re.search(r"\d{2}[-_.]\d{2}[-_.](20\d{2})", filename)
    if date_year:
        return date_year.group(1)

    return fallback_year


def category_from_pdf_url(pdf_url: str) -> str:
    path = unquote(urlparse(pdf_url).path).lower()
    if "/motions-postulats/" in path:
        filename = Path(unquote(urlparse(pdf_url).path)).name
        return normalize_document_category("motions-postulats", filename, pdf_url)
    if "proces-verbaux" in path:
        return "proces-verbaux"
    if "preavis" in path:
        return "preavis-municipaux"
    if "communications" in path:
        return "communications-municipales"
    if "informations-diverses" in path:
        return "informations-diverses"
    if "rapport-de-gestion" in path:
        return "rapport-de-gestion"
    if "/budget/" in path:
        return "budget"
    return "autres"


def safe_filename(pdf_url: str) -> str:
    filename = Path(unquote(urlparse(pdf_url).path)).name
    return re.sub(r'[<>:"/\\|?*]', "_", filename)


def extract_pdf_text(pdf_path: Path) -> str:
    document = fitz.open(pdf_path)
    return clean_french_text("\n".join(page.get_text() for page in document))


def create_local_document(pdf_url: str, source_page: str, fallback_year: str) -> dict:
    year = infer_year_from_pdf_url(pdf_url, fallback_year)
    category = category_from_pdf_url(pdf_url)
    filename = safe_filename(pdf_url)
    target_dir = DOCUMENTS_ROOT / year / category
    target_dir.mkdir(parents=True, exist_ok=True)

    pdf_path = target_dir / filename
    text_path = pdf_path.with_suffix(".txt")
    metadata_path = pdf_path.with_suffix(".json")

    if not pdf_path.exists():
        response = requests.get(pdf_url, headers=HEADERS, timeout=60)
        response.raise_for_status()
        pdf_path.write_bytes(response.content)

    text = extract_pdf_text(pdf_path)
    text_path.write_text(text, encoding="utf-8")

    metadata = {
        "commune": "La Tour-de-Peilz",
        "year": year,
        "category": category,
        "filename": filename,
        "pdf_url": pdf_url,
        "source_page": source_page,
        "pdf_path": str(pdf_path),
        "text_path": str(text_path),
        "characters_extracted": len(text),
    }
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "category": category,
        "year": year,
        "filename": filename,
        "metadata_path": str(metadata_path),
        "text_path": str(text_path),
        "pdf_path": str(pdf_path),
    }


def extract_main_text(full_text: str) -> str:
    start = full_text.find("COMMUNE DE LA-TOUR-DE-PEILZ")
    end = full_text.find("Législature 2021-2026", start)
    if start == -1:
        start = full_text.find("Ordre du jour")
    if end == -1:
        end = len(full_text)
    return clean_spaces(full_text[start:end])


def parse_agenda_items(main_text: str) -> list[dict]:
    compact = re.sub(r"\s+", " ", main_text)
    pattern = re.compile(r"(?<!\d)(\d+(?:\.\d+)*)\.\s+")
    matches = list(pattern.finditer(compact))
    items = []
    for index, match in enumerate(matches):
        number = match.group(1)
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(compact)
        title = compact[start:end].strip(" -")
        for heading in SECTION_HEADINGS:
            heading_index = title.find(heading)
            if heading_index > 0:
                title = title[:heading_index].strip(" -")
        if title:
            items.append(
                {
                    "number": number,
                    "item_number": number,
                    "title": title,
                    "raw_title": title,
                    "object_type": infer_agenda_object_type(title),
                    "object_number": infer_agenda_object_number(title),
                }
            )
    return items


def infer_agenda_object_type(title: str) -> str | None:
    normalized = normalize_text(title)
    if "preavis" in normalized or "préavis" in normalized:
        return "preavis"
    if "motion" in normalized:
        return "motion"
    if "postulat" in normalized:
        return "postulat"
    if "interpellation" in normalized:
        return "interpellation"
    if "communication" in normalized:
        return "communication_municipale"
    if "rapport de gestion" in normalized or "gestion et comptes" in normalized:
        return "rapport_de_gestion"
    if "budget" in normalized:
        return "budget"
    if "proces-verbal" in normalized or "procès-verbal" in normalized:
        return "proces_verbal"
    return None


def infer_agenda_object_number(title: str) -> str | None:
    match = re.search(r"\bN[°o]\s*([0-9]+(?:/[0-9]{4})?)", title, flags=re.I)
    if match:
        return match.group(1)
    match = re.search(r"\b([0-9]+/[0-9]{4})\b", title)
    return match.group(1) if match else None


def load_local_document_index() -> dict[str, dict]:
    index = {}
    for metadata_path in DOCUMENTS_ROOT.rglob("*.json"):
        if "ordres-du-jour" in metadata_path.parts:
            continue
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError:
            continue

        pdf_url = metadata.get("pdf_url")
        if not pdf_url:
            continue

        index[pdf_url] = {
            "category": metadata.get("category", ""),
            "year": metadata.get("year", ""),
            "filename": metadata.get("filename", ""),
            "metadata_path": str(metadata_path),
            "text_path": str(metadata_path.with_suffix(".txt")),
            "pdf_path": str(metadata_path.with_suffix(".pdf")),
        }
    return index


def canonical_summary(metadata: dict, metadata_path: Path) -> dict:
    return {
        "category": metadata.get("category", ""),
        "doc_type": metadata.get("doc_type") or metadata.get("category", ""),
        "year": metadata.get("year", ""),
        "title": metadata.get("title") or metadata.get("filename", ""),
        "object_title": metadata.get("object_title") or metadata.get("summary") or metadata.get("title"),
        "political_object_id": metadata.get("political_object_id")
        or (metadata.get("political_object") or {}).get("object_id"),
        "pdf_url": metadata.get("pdf_url", ""),
        "filename": metadata.get("filename", ""),
        "metadata_path": str(metadata_path),
        "text_path": str(metadata_path.with_suffix(".txt")),
        "canonical_object": metadata.get("canonical_object"),
    }


def load_canonical_object_index() -> dict:
    by_pdf_url = {}
    by_object_id = {}
    metadata_by_path = {}
    for metadata_path in DOCUMENTS_ROOT.rglob("*.json"):
        if "ordres-du-jour" in metadata_path.parts:
            continue
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError:
            continue

        summary = canonical_summary(metadata, metadata_path)
        metadata_by_path[str(metadata_path)] = metadata
        if metadata.get("canonical_object") is True:
            if metadata.get("pdf_url"):
                by_pdf_url[metadata["pdf_url"]] = summary
            object_id = summary.get("political_object_id")
            if object_id:
                by_object_id[object_id] = summary

    for metadata in metadata_by_path.values():
        if metadata.get("canonical_object") is True:
            continue
        related = (
            metadata.get("related_canonical_motion")
            or metadata.get("related_canonical_postulat")
            or metadata.get("related_canonical_interpellation")
            or metadata.get("related_canonical_report")
        )
        related_id = metadata.get("related_political_object_id") or (related or {}).get("political_object_id")
        if related_id and related_id in by_object_id and metadata.get("pdf_url"):
            by_pdf_url[metadata["pdf_url"]] = by_object_id[related_id]

    return {"by_pdf_url": by_pdf_url, "by_object_id": by_object_id}


def agenda_number_for_link(link_text: str, agenda_items: list[dict]) -> str | None:
    normalized_link = normalize_text(link_text)
    if not normalized_link:
        return None
    if len(normalized_link) < 12 or normalized_link in {"gestion", "rapport", "decision", "décision", "annexe"}:
        return None

    for item in agenda_items:
        normalized_title = normalize_text(item["title"])
        if normalized_link in normalized_title or normalized_title in normalized_link:
            return item["number"]
    return None


def canonical_for_link(pdf_url: str, local_document: dict, canonical_index: dict) -> dict | None:
    if pdf_url in canonical_index["by_pdf_url"]:
        candidate = canonical_index["by_pdf_url"][pdf_url]
        if candidate.get("category") == "rapport-de-gestion" and "rapport-de-gestion" not in unquote(urlparse(pdf_url).path).lower():
            return None
        return candidate
    metadata_path = local_document.get("metadata_path") if local_document else None
    if not metadata_path or not Path(metadata_path).exists():
        return None
    try:
        metadata = json.loads(Path(metadata_path).read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return None
    related = (
        metadata.get("related_canonical_motion")
        or metadata.get("related_canonical_postulat")
        or metadata.get("related_canonical_interpellation")
        or metadata.get("related_canonical_report")
    )
    related_id = metadata.get("related_political_object_id") or (related or {}).get("political_object_id")
    if not related_id:
        return None
    candidate = canonical_index["by_object_id"].get(related_id)
    if candidate and candidate.get("category") == "rapport-de-gestion":
        local_category = local_document.get("category") or ""
        if local_category != "rapport-de-gestion" and "rapport-de-gestion" not in unquote(urlparse(pdf_url).path).lower():
            return None
    return candidate


def dedupe_canonical_links(links: list[dict]) -> list[dict]:
    unique = []
    seen = set()
    for link in links:
        key = link.get("political_object_id") or link.get("pdf_url") or link.get("metadata_path")
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(link)
    return unique


def add_session_to_canonical(canonical: dict, session_data: dict, agenda_item: dict | None) -> None:
    metadata_path = canonical.get("metadata_path")
    if not metadata_path or not Path(metadata_path).exists():
        return
    path = Path(metadata_path)
    try:
        metadata = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return

    session_ref = {
        "session_date": session_data["session_date"],
        "agenda_path": str(
            DOCUMENTS_ROOT
            / session_data["session_date"][:4]
            / "ordres-du-jour"
            / f"{session_data['session_date']}-ordre-du-jour.json"
        ),
        "agenda_source_url": session_data["source_url"],
    }
    if agenda_item:
        session_ref["agenda_item_number"] = agenda_item.get("number")
        session_ref["agenda_item_title"] = agenda_item.get("title")

    scheduled = metadata.get("scheduled_in_sessions") or []
    key = (session_ref["session_date"], session_ref.get("agenda_item_number"))
    existing = {(item.get("session_date"), item.get("agenda_item_number")) for item in scheduled if isinstance(item, dict)}
    if key not in existing:
        scheduled.append(session_ref)
    metadata["scheduled_in_sessions"] = sorted(
        scheduled,
        key=lambda item: (item.get("session_date") or "", item.get("agenda_item_number") or ""),
    )
    metadata["metadata_version"] = "metadata-audit-v2"
    path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def clear_scheduled_session_links() -> None:
    for metadata_path in DOCUMENTS_ROOT.rglob("*.json"):
        if "ordres-du-jour" in metadata_path.parts:
            continue
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError:
            continue
        if "scheduled_in_sessions" not in metadata:
            continue
        metadata.pop("scheduled_in_sessions", None)
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_session(session: dict) -> dict:
    source_url = session["source_url"]
    session_date = session["session_date"]
    page_html = fetch_text(source_url)
    parser = TextAndLinksParser()
    parser.feed(page_html)

    full_text = clean_spaces("".join(parser.text_parts))
    main_text = extract_main_text(full_text)
    agenda_items = parse_agenda_items(main_text)
    local_document_index = load_local_document_index()
    canonical_index = load_canonical_object_index()
    linked_documents = []
    linked_canonical_objects = []
    seen_pdf_urls = set()
    for link in parser.links:
        pdf_url = normalize_pdf_url(source_url, link["href"])
        if not pdf_url or pdf_url in seen_pdf_urls:
            continue
        if normalize_text(link["text"]) not in normalize_text(main_text):
            continue
        seen_pdf_urls.add(pdf_url)
        local_document = local_document_index.get(pdf_url)
        if not local_document:
            local_document = create_local_document(pdf_url, source_url, str(session_date.year))
            local_document_index[pdf_url] = local_document
        agenda_item_number = agenda_number_for_link(link["text"], agenda_items)
        canonical = canonical_for_link(pdf_url, local_document, canonical_index)
        canonical_link = None
        if canonical:
            canonical_link = {
                **canonical,
                "agenda_item_number": agenda_item_number,
                "link_title": link["text"],
            }
            linked_canonical_objects.append(canonical_link)
        linked_documents.append(
            {
                "title": link["text"],
                "pdf_url": pdf_url,
                "filename": Path(unquote(urlparse(pdf_url).path)).name,
                "agenda_item_number": agenda_item_number,
                "local_document": local_document,
                "canonical_object": canonical_link,
            }
        )

    linked_canonical_objects = dedupe_canonical_links(linked_canonical_objects)
    agenda_items_by_number = {item["number"]: item for item in agenda_items}
    for canonical in linked_canonical_objects:
        add_session_to_canonical(
            canonical,
            {"session_date": session_date.isoformat(), "source_url": source_url},
            agenda_items_by_number.get(canonical.get("agenda_item_number")),
        )

    time_match = re.search(r"à\s+(\d{1,2}:\d{2})", main_text)
    place_match = re.search(r"\d{4}\s+à\s+\d{1,2}:\d{2}\s+(.+?)\n\s*Ordre du jour", main_text, re.S)
    place = clean_spaces(place_match.group(1)) if place_match else ""

    return {
        "commune": "La Tour-de-Peilz",
        "type": "ordre_du_jour",
        "session_date": session_date.isoformat(),
        "label": session["label"],
        "time": time_match.group(1) if time_match else "",
        "place": place,
        "source_url": source_url,
        "agenda_items": [
            {
                **item,
                "linked_documents": [
                    document
                    for document in linked_documents
                    if document["agenda_item_number"] == item["number"]
                ],
                "linked_canonical_objects": [
                    canonical
                    for canonical in linked_canonical_objects
                    if canonical.get("agenda_item_number") == item["number"]
                ],
            }
            for item in agenda_items
        ],
        "linked_documents": linked_documents,
        "linked_canonical_objects": linked_canonical_objects,
        "text": main_text,
    }


def write_session_files(session_data: dict) -> None:
    year = session_data["session_date"][:4]
    slug = session_data["session_date"]

    session_dir = SESSIONS_ROOT / year
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / f"{slug}.json").write_text(
        json.dumps(session_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    document_dir = DOCUMENTS_ROOT / year / "ordres-du-jour"
    document_dir.mkdir(parents=True, exist_ok=True)
    text_path = document_dir / f"{slug}-ordre-du-jour.txt"
    json_path = document_dir / f"{slug}-ordre-du-jour.json"
    text_path.write_text(session_data["text"], encoding="utf-8")
    json_path.write_text(
        json.dumps(
            {
                "commune": session_data["commune"],
                "year": year,
                "category": "ordres-du-jour",
                "doc_type": "ordres-du-jour",
                "content_kind": "session_agenda",
                "title": f"Ordre du jour du Conseil communal du {session_data['session_date']}",
                "filename": text_path.name,
                "source_page": session_data["source_url"],
                "session_date": session_data["session_date"],
                "document_date": session_data["session_date"],
                "time": session_data["time"],
                "place": session_data["place"],
                "agenda_items": session_data["agenda_items"],
                "linked_documents_count": len(session_data["linked_documents"]),
                "linked_canonical_objects": session_data.get("linked_canonical_objects", []),
                "linked_canonical_objects_count": len(session_data.get("linked_canonical_objects", [])),
                "session": {
                    "type": "conseil_communal_session",
                    "session_date": session_data["session_date"],
                    "time": session_data["time"],
                    "place": session_data["place"],
                    "agenda_items_count": len(session_data["agenda_items"]),
                    "linked_documents_count": len(session_data["linked_documents"]),
                    "linked_canonical_objects_count": len(session_data.get("linked_canonical_objects", [])),
                },
                "search_facets": sorted(
                    set(
                        facet
                        for facet in [
                            "ordre_du_jour",
                            "session",
                            "conseil_communal",
                            *[
                                slugify(item.get("object_type") or "")
                                for item in session_data["agenda_items"]
                                if item.get("object_type")
                            ],
                        ]
                        if facet
                    )
                ),
                "metadata_version": "metadata-audit-v2",
                "text_path": str(text_path),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    sessions = collect_session_pages()
    results = []
    clear_scheduled_session_links()
    for session in sessions:
        session_data = parse_session(session)
        write_session_files(session_data)
        results.append(
            {
                "session_date": session_data["session_date"],
                "source_url": session_data["source_url"],
                "agenda_items_count": len(session_data["agenda_items"]),
                "linked_documents_count": len(session_data["linked_documents"]),
            }
        )
        print(
            f"{session_data['session_date']} - "
            f"{len(session_data['agenda_items'])} agenda items, "
            f"{len(session_data['linked_documents'])} linked PDFs"
        )

    manifest = {
        "commune": "La Tour-de-Peilz",
        "start_date": START_DATE.isoformat(),
        "end_date": TODAY.isoformat(),
        "excluded_future_sessions": True,
        "sessions_count": len(results),
        "sessions": results,
    }
    SESSIONS_ROOT.mkdir(parents=True, exist_ok=True)
    manifest_path = SESSIONS_ROOT / "manifest_ordres_du_jour_2021_2026.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Manifest: {manifest_path}")


if __name__ == "__main__":
    main()
