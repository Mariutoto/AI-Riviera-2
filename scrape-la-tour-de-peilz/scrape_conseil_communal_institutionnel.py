import json
import re
import sys
from html.parser import HTMLParser
from html import unescape
from pathlib import Path
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import fitz
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.text_cleaning import clean_french_text


BASE_URL = "https://www.la-tour-de-peilz.ch/"
MAIN_URL = "https://www.la-tour-de-peilz.ch/politique/politique.php?mnbid=27"
MEMBERS_URL = "https://www.la-tour-de-peilz.ch/politique/membres-conseil-communal.php"
REGLEMENT_VIEWER_URL = (
    "https://www.la-tour-de-peilz.ch/tools/pdf-viewer/web/viewer.php?"
    "file=/doc_uploads/images/politique/conseil-communal/pdf/reglement_CC-Version_finale.pdf"
)
HEADERS = {"User-Agent": "AI-Riviera institutional importer"}

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DOCUMENTS_ROOT = PROJECT_ROOT / "documents" / "la-tour-de-peilz"
DATA_ROOT = PROJECT_ROOT / "data" / "institutionnel" / "la-tour-de-peilz"
CATEGORY = "conseil-communal"
YEAR = "institutionnel"

PARTY_ABBREVIATIONS = {
    "PLR.Les Libéraux Radicaux (PLR)": "PLR",
    "Parti Socialiste et Divers de Gauche (PSDG)": "PSDG",
    "Les Vert·e·s (LV)": "LV",
    "Le Centre + Indépendants plus vert´libéraux (LCIVL)": "LCIVL",
    "La Tour-de-Peilz Libre (LTDPL)": "LTDPL",
    "Hors parti": "Hors parti",
}


class TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"br", "p", "li", "div", "h1", "h2", "h3", "h4", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"p", "li", "div", "h1", "h2", "h3", "h4", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def clean_text(text: str) -> str:
    return clean_french_text(text)


def slugify(text: str) -> str:
    text = text.lower()
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
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def fetch_text(url: str) -> str:
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    return response.text


def html_to_text(fragment: str) -> str:
    parser = TextParser()
    parser.feed(fragment)
    return clean_text("".join(parser.parts))


def extract_accordion_blocks(page_html: str) -> list[dict]:
    blocks = []
    pattern = re.compile(
        r"""<div class="question[^"]*"[^>]*>\s*<div class="title[^"]*"[^>]*>.*?</i>(?P<title>.*?)</div>\s*<div class="answer"[^>]*>(?P<body>.*?)</div>\s*</div>""",
        flags=re.I | re.S,
    )
    for match in pattern.finditer(page_html):
        title = html_to_text(match.group("title"))
        body = html_to_text(match.group("body"))
        if title and body:
            blocks.append({"title": title, "text": body})
    return blocks


def party_abbreviation(label: str) -> str:
    label = clean_text(label)
    if label in PARTY_ABBREVIATIONS:
        return PARTY_ABBREVIATIONS[label]
    match = re.search(r"\(([A-Z0-9]+)\)\s*$", label)
    return match.group(1) if match else label


def extract_council_members(page_html: str) -> list[dict]:
    members: list[dict] = []
    pattern = re.compile(
        r"""<div class="question[^"]*"[^>]*>\s*<div class="title[^"]*"[^>]*>.*?</i>(?P<party>.*?)</div>\s*<div class="answer"[^>]*>(?P<body>.*?)</div>\s*</div>""",
        flags=re.I | re.S,
    )
    row_pattern = re.compile(
        r"""<li class="prestation row table-striped"[^>]*>\s*<div[^>]*>(?P<last>.*?)</div>\s*<div[^>]*>(?P<first>.*?)</div>""",
        flags=re.I | re.S,
    )
    for block in pattern.finditer(page_html):
        party_label = html_to_text(block.group("party"))
        party = party_abbreviation(party_label)
        if party not in {"PLR", "PSDG", "LV", "LCIVL", "LTDPL", "Hors parti"}:
            continue
        for row in row_pattern.finditer(block.group("body")):
            last_name = clean_text(re.sub(r"<[^>]+>", " ", unescape(row.group("last"))))
            first_name = clean_text(re.sub(r"<[^>]+>", " ", unescape(row.group("first"))))
            if not last_name or not first_name:
                continue
            members.append(
                {
                    "name": f"{first_name} {last_name}",
                    "first_name": first_name,
                    "last_name": last_name,
                    "party": party,
                    "party_label": party_label,
                }
            )
    return members


def write_council_members_data(members: list[dict]) -> Path:
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    path = DATA_ROOT / "conseil_communal_members.json"
    payload = {
        "commune": "La Tour-de-Peilz",
        "source_page": MEMBERS_URL,
        "legislature": "2021-2026",
        "party_abbreviations": PARTY_ABBREVIATIONS,
        "members_count": len(members),
        "members": members,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def extract_members_text() -> str:
    page_html = fetch_text(MEMBERS_URL)
    text = html_to_text(page_html)
    start = text.find("Liste des membres")
    if start == -1:
        start = text.find("Conseil communal")
    if start == -1:
        start = 0
    footer = text.find("©", start)
    if footer == -1:
        footer = len(text)
    return clean_text(text[start:footer])


def pdf_url_from_viewer(viewer_url: str) -> str:
    file_value = parse_qs(urlparse(viewer_url).query).get("file", [""])[0]
    return urljoin(BASE_URL, unquote(file_value))


def extract_pdf_text(pdf_path: Path) -> str:
    document = fitz.open(pdf_path)
    return clean_text("\n".join(page.get_text() for page in document))


def write_document(slug: str, title: str, text: str, source_url: str, extra: dict | None = None) -> dict:
    document_dir = DOCUMENTS_ROOT / YEAR / CATEGORY
    document_dir.mkdir(parents=True, exist_ok=True)
    text_path = document_dir / f"{slug}.txt"
    json_path = document_dir / f"{slug}.json"
    text_path.write_text(text, encoding="utf-8")

    metadata = {
        "commune": "La Tour-de-Peilz",
        "year": YEAR,
        "category": CATEGORY,
        "institutional_category": slug,
        "title": title,
        "filename": text_path.name,
        "source_page": source_url,
        "text_path": str(text_path),
        "characters_extracted": len(text),
    }
    if extra:
        metadata.update(extra)
    json_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return metadata


def write_reglement() -> dict:
    pdf_url = pdf_url_from_viewer(REGLEMENT_VIEWER_URL)
    document_dir = DOCUMENTS_ROOT / YEAR / CATEGORY
    document_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = document_dir / "reglement-conseil-communal.pdf"
    text_path = document_dir / "reglement-conseil-communal.txt"
    json_path = document_dir / "reglement-conseil-communal.json"

    response = requests.get(pdf_url, headers=HEADERS, timeout=60)
    response.raise_for_status()
    pdf_path.write_bytes(response.content)
    text = extract_pdf_text(pdf_path)
    text_path.write_text(text, encoding="utf-8")

    metadata = {
        "commune": "La Tour-de-Peilz",
        "year": YEAR,
        "category": CATEGORY,
        "institutional_category": "reglement-conseil-communal",
        "title": "Règlement du Conseil communal",
        "filename": pdf_path.name,
        "pdf_url": pdf_url,
        "source_page": REGLEMENT_VIEWER_URL,
        "pdf_path": str(pdf_path),
        "text_path": str(text_path),
        "characters_extracted": len(text),
    }
    json_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return metadata


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    main_html = fetch_text(MAIN_URL)
    records = []

    wanted = {
        "Admissions": "admissions",
        "Bureau du conseil communal": "bureau-du-conseil-communal",
        "Compétences": "competences",
    }

    for block in extract_accordion_blocks(main_html):
        for title, slug in wanted.items():
            if title.casefold() == block["title"].casefold():
                records.append(write_document(slug, title, block["text"], MAIN_URL))

    members_html = fetch_text(MEMBERS_URL)
    members = extract_council_members(members_html)
    members_data_path = write_council_members_data(members)
    members_text = html_to_text(members_html)
    start = members_text.find("Liste des membres")
    if start == -1:
        start = members_text.find("Liste des membres pour")
    if start == -1:
        start = 0
    members_text = clean_text(members_text[start:])
    records.append(
        write_document(
            "liste-des-membres-par-parti",
            "Liste des membres par parti",
            members_text,
            MEMBERS_URL,
            {"structured_data_path": str(members_data_path), "members_count": len(members)},
        )
    )
    records.append(write_reglement())

    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    manifest = {
        "commune": "La Tour-de-Peilz",
        "source_page": MAIN_URL,
        "documents_count": len(records),
        "documents": records,
    }
    manifest_path = DATA_ROOT / "manifest_conseil_communal_institutionnel.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    for record in records:
        print(f"{record['institutional_category']} - {record['characters_extracted']} chars")
    print(f"Manifest: {manifest_path}")


if __name__ == "__main__":
    main()
