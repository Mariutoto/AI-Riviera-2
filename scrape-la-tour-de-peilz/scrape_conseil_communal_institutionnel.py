import json
import re
import shutil
import sys
from html import unescape
from html.parser import HTMLParser
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
REGLEMENT_DOC_TYPE = "reglement-conseil-communal"
REGLEMENT_ARTICLES_CATEGORY = "reglements"

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
    text = normalize_for_key(text)
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def normalize_for_key(text: str) -> str:
    text = clean_text(text).casefold()
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
    return text


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


def pdf_url_from_viewer(viewer_url: str) -> str:
    file_value = parse_qs(urlparse(viewer_url).query).get("file", [""])[0]
    return urljoin(BASE_URL, unquote(file_value))


def extract_pdf_text(pdf_path: Path) -> str:
    document = fitz.open(pdf_path)
    pages = []
    for page in document:
        lines = page.get_text().splitlines()
        last_content = next((index for index in range(len(lines) - 1, -1, -1) if lines[index].strip()), None)
        # Printed page numbers are isolated at the physical bottom of each
        # page. Remove them before concatenating pages so they cannot become
        # article content; numbers occurring inside a paragraph are retained.
        if last_content is not None and re.fullmatch(r"\d{1,3}", lines[last_content].strip()):
            lines.pop(last_content)
        pages.append("\n".join(lines))
    return clean_text("\n".join(pages))


def normalize_article_number(raw: str) -> str:
    raw = clean_text(raw).strip().lower()
    if raw.startswith("article premier"):
        return "1"
    raw = re.sub(r"^art\.\s*", "", raw)
    raw = raw.strip(" .-")
    raw = raw.replace(".bis", "bis").replace(".ter", "ter")
    return re.sub(r"\s+", "", raw)


def parse_reglement_toc(text: str) -> dict[str, str]:
    lines = [clean_text(line).strip() for line in text.splitlines()]
    actual_start = next((i for i, line in enumerate(lines) if re.match(r"^Article premier\.-", line)), len(lines))
    toc_lines = lines[:actual_start]
    titles = {}
    article_pattern = re.compile(r"^(Article premier|Art\.\s*\d+[a-z]*(?:\.(?:bis|ter))?)\s*\.?$", flags=re.I)
    for index, line in enumerate(toc_lines):
        match = article_pattern.match(line)
        if not match:
            continue
        number = normalize_article_number(match.group(1))
        for candidate in toc_lines[index + 1 : index + 5]:
            if not candidate or article_pattern.match(candidate):
                continue
            if re.match(r"^(Titre|CHAPITRE|Section)\b", candidate, flags=re.I):
                continue
            if re.match(r"^(ANNEXE|Page\s+\d+)\b", candidate, flags=re.I):
                break
            titles[number] = candidate.strip(" .")
            break
    return titles


def article_sort_key(article_number: str) -> float:
    match = re.fullmatch(r"(\d+)([a-z]+)?", article_number.casefold())
    if not match:
        return 9999.0
    base = int(match.group(1))
    suffix = match.group(2) or ""
    if not suffix:
        return float(base)
    if len(suffix) == 1 and "a" <= suffix <= "z":
        return base + (ord(suffix) - ord("a") + 1) / 100
    conventional = {"bis": 0.20, "ter": 0.30, "quater": 0.40}
    if suffix in conventional:
        return base + conventional[suffix]
    # Stable fallback for uncommon alphabetic suffixes. It remains between
    # the base article and the next integer article.
    lexical = sum((ord(char) - ord("a") + 1) / (100 ** (index + 1)) for index, char in enumerate(suffix))
    return base + min(lexical, 0.99)


def reglement_topic_facets(article_title: str, article_text: str, path: str) -> list[str]:
    haystack = normalize_for_key(" ".join([article_title, article_text, path]))
    topics = []
    topic_terms = {
        "commissions": ["commission"],
        "bureau": ["bureau"],
        "president_conseil": ["president"],
        "secretaire": ["secretaire"],
        "scrutateurs": ["scrutateur"],
        "huissier": ["huissier"],
        "convocation": ["convocation", "convoque"],
        "quorum": ["quorum"],
        "vote": ["vote", "votation", "scrutin"],
        "election": ["election", "nomination"],
        "motion_postulat_interpellation": ["motion", "postulat", "interpellation"],
        "budget_comptes": ["budget", "comptes"],
        "secret_fonction": ["secret de fonction", "confidentialite"],
        "archives": ["archives"],
        "referendum": ["referendum"],
        "ordre_du_jour": ["ordre du jour"],
        "proces_verbal": ["proces-verbal", "proces verbal"],
    }
    for facet, terms in topic_terms.items():
        if any(term in haystack for term in terms):
            topics.append(facet)
    return topics


def flexible_title_pattern(title: str) -> str:
    parts = [re.escape(part) for part in re.split(r"\s+", title.strip()) if part]
    pattern = r"\s+".join(parts)
    pattern = pattern.replace(r"\-", r"[-–—]")
    return pattern


def split_reglement_articles(text: str) -> list[dict]:
    article_titles = parse_reglement_toc(text)
    raw_lines = [clean_text(line).strip() for line in text.splitlines()]
    lines = []
    index = 0
    while index < len(raw_lines):
        line = raw_lines[index]
        if line.casefold() == "art." and index + 1 < len(raw_lines) and re.match(r"^\d+[a-z]*(?:\.(?:bis|ter))?\s*\.?-", raw_lines[index + 1], flags=re.I):
            lines.append(f"Art. {raw_lines[index + 1]}")
            index += 2
            continue
        lines.append(line)
        index += 1
    first_article_index = next((i for i, line in enumerate(lines) if re.match(r"^Article premier\.-", line)), 0)
    start_index = next(
        (i for i in range(first_article_index, -1, -1) if re.match(r"^Titre\s+I\b", lines[i], flags=re.I)),
        first_article_index,
    )
    current_title = None
    current_chapter = None
    current_section = None
    current_article = None
    pending_hierarchy: str | None = None
    printed_article_titles: dict[str, str] = {}
    articles = []
    article_pattern = re.compile(r"^(Article premier|Art\.\s*\d+[a-z]*(?:\.(?:bis|ter))?)\s*\.?-?\s*(.*)$", flags=re.I)

    def trim_trailing_next_heading(body_lines: list[str], next_number: str | None = None) -> list[str]:
        if not next_number:
            return body_lines
        next_title = article_titles.get(next_number)
        if not next_title:
            return body_lines
        cleaned = list(body_lines)
        while cleaned and not cleaned[-1].strip():
            cleaned.pop()
        normalized_next = normalize_for_key(next_title)
        # In this PDF, the marginal title and legal reference of the next
        # article are extracted before its ``Art. N.-`` marker. Detect that
        # typographic block, even when the table of contents uses a slightly
        # different connector (for example "et" instead of "de").
        content_end = len(cleaned)
        while content_end and re.fullmatch(r"\s*\((?:art\.|articles?).*\)\s*", cleaned[content_end - 1], flags=re.I):
            content_end -= 1
        stopwords = {"de", "du", "des", "d", "et", "la", "le", "les"}
        next_words = {word for word in re.findall(r"[a-zà-ÿ]+", normalized_next) if word not in stopwords}
        for count in range(min(4, content_end), 0, -1):
            candidate = " ".join(cleaned[content_end - count : content_end])
            candidate_words = {
                word for word in re.findall(r"[a-zà-ÿ]+", normalize_for_key(candidate)) if word not in stopwords
            }
            if next_words and candidate_words and (
                candidate_words == next_words
                or len(candidate_words & next_words) / max(len(candidate_words), len(next_words)) >= 0.75
            ):
                printed_article_titles[next_number] = re.sub(r"\s+", " ", candidate).strip()
                return cleaned[: content_end - count]
        if cleaned:
            last_line = cleaned[-1]
            escaped_title = re.escape(next_title)
            suffix_match = re.search(rf"\s+(?:\d{{1,3}}\s+)?{escaped_title}(?:\s*\([^)]*\))?\s*$", last_line, flags=re.I)
            if suffix_match and suffix_match.start() > 0:
                cleaned[-1] = last_line[: suffix_match.start()].rstrip()
                while cleaned and not cleaned[-1].strip():
                    cleaned.pop()
                return cleaned
        for count in range(min(4, len(cleaned)), 0, -1):
            tail = " ".join(line.strip() for line in cleaned[-count:])
            normalized_tail = normalize_for_key(tail)
            matches_next_title = normalized_tail and (
                normalized_next.startswith(normalized_tail)
                or normalized_tail.startswith(normalized_next)
                or normalized_next in normalized_tail
            )
            if not matches_next_title:
                continue
            prefix = cleaned[:-count]
            while prefix and not prefix[-1].strip():
                prefix.pop()
            if not prefix or not re.fullmatch(r"\d{1,3}", prefix[-1].strip()):
                return body_lines
            prefix.pop()
            while prefix and not prefix[-1].strip():
                prefix.pop()
            return prefix
        return cleaned

    def flush_article(next_number: str | None = None) -> None:
        nonlocal current_article
        if not current_article:
            return
        current_article["body_lines"] = trim_trailing_next_heading(current_article["body_lines"], next_number)
        body = clean_text("\n".join(current_article["body_lines"])).strip()
        if next_number and article_titles.get(next_number):
            next_title_pattern = flexible_title_pattern(article_titles[next_number])
            body = re.sub(rf"\s+\d{{1,3}}\s+{next_title_pattern}(?:\s*\([^)]*\))?\s*$", "", body, flags=re.I).strip()
            body = re.sub(rf"\s+{next_title_pattern}(?:\s*\([^)]*\))?\s*$", "", body, flags=re.I).strip()
        if not body:
            current_article = None
            return
        number = current_article["article_number"]
        title = current_article.get("article_title") or article_titles.get(number) or f"Article {number}"
        title = re.sub(r"\s+", " ", title).strip()
        path_parts = [part for part in [current_article.get("title_heading"), current_article.get("chapter"), current_article.get("section")] if part]
        current_article.update(
            {
                "article_title": title,
                "article_text": body,
                "title_path": " > ".join(path_parts + [f"Art. {number} - {title}"]),
                "sort_key": article_sort_key(number),
            }
        )
        current_article.pop("body_lines", None)
        articles.append(current_article)
        current_article = None

    for line in lines[start_index:]:
        if not line:
            if current_article:
                current_article["body_lines"].append("")
            continue
        is_hierarchy = bool(re.match(r"^(Titre\s+[IVXLC]+|CHAPITRE\b|Section\b)", line, flags=re.I))
        is_article = bool(article_pattern.match(line))
        if pending_hierarchy and not is_hierarchy and not is_article:
            if pending_hierarchy == "title":
                current_title = f"{current_title} - {line}"
            elif pending_hierarchy == "chapter":
                current_chapter = f"{current_chapter} - {line}"
            pending_hierarchy = None
            continue
        if re.match(r"^Ainsi adopté\b", line, flags=re.I):
            flush_article()
            break
        if re.match(r"^Titre\s+[IVXLC]+\b", line, flags=re.I):
            flush_article()
            current_title = line
            current_chapter = None
            current_section = None
            pending_hierarchy = "title"
            continue
        if re.match(r"^CHAPITRE\b", line, flags=re.I):
            flush_article()
            current_chapter = line
            current_section = None
            pending_hierarchy = "chapter"
            continue
        if re.match(r"^Section\b", line, flags=re.I):
            flush_article()
            current_section = line
            pending_hierarchy = None
            continue
        match = article_pattern.match(line)
        if match:
            pending_hierarchy = None
            source_number = normalize_article_number(match.group(1))
            number = source_number
            # The source PDF prints Art. 140 twice. Its table of contents and
            # marginal heading identify the second occurrence as Art. 141
            # (Amortissements). Repair this kind of consecutive source typo
            # before splitting, so identifiers and citations remain unique.
            if (
                current_article
                and source_number == current_article.get("article_number")
                and source_number.isdigit()
            ):
                expected_number = str(int(source_number) + 1)
                if expected_number in article_titles:
                    number = expected_number
            flush_article(next_number=number)
            first_text = clean_text(match.group(2)).strip(" .-")
            current_article = {
                "article_number": number,
                "article_title": printed_article_titles.get(number) or article_titles.get(number),
                "title_heading": current_title,
                "chapter": current_chapter,
                "section": current_section,
                "body_lines": [first_text] if first_text else [],
            }
            continue
        if current_article:
            current_article["body_lines"].append(line)

    flush_article()
    return sorted(articles, key=lambda item: item["sort_key"])


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


def write_reglement_articles(text: str, pdf_url: str, pdf_path: Path) -> list[dict]:
    articles = split_reglement_articles(text)
    article_dir = DOCUMENTS_ROOT / YEAR / REGLEMENT_ARTICLES_CATEGORY / REGLEMENT_DOC_TYPE
    if article_dir.exists():
        shutil.rmtree(article_dir)
    article_dir.mkdir(parents=True, exist_ok=True)

    records = []
    seen_numbers: dict[str, int] = {}
    for article in articles:
        number = article["article_number"]
        occurrence = seen_numbers.get(number, 0) + 1
        seen_numbers[number] = occurrence
        article_id = f"art-{number}" if occurrence == 1 else f"art-{number}-{occurrence}"
        filename_stem = slugify(article_id)
        title = f"Règlement du Conseil communal - Art. {number} - {article['article_title']}"
        body = f"{title}\n{article['title_path']}\n\n{article['article_text']}\n"
        text_path = article_dir / f"{filename_stem}.txt"
        json_path = article_dir / f"{filename_stem}.json"
        text_path.write_text(body, encoding="utf-8")
        facets = [
            "reglement",
            "reglement_conseil_communal",
            "texte_normatif",
            "article",
            f"article_{number.replace('.', '_')}",
            *reglement_topic_facets(article["article_title"], article["article_text"], article["title_path"]),
        ]
        metadata = {
            "commune": "La Tour-de-Peilz",
            "year": YEAR,
            "category": REGLEMENT_ARTICLES_CATEGORY,
            "doc_type": REGLEMENT_DOC_TYPE,
            "institutional_category": REGLEMENT_DOC_TYPE,
            "content_kind": "regulation_article",
            "title": title,
            "filename": text_path.name,
            "pdf_url": pdf_url,
            "source_url": f"{pdf_url}#{article_id}",
            "article_id": article_id,
            "source_page": REGLEMENT_VIEWER_URL,
            "source_pdf_path": str(pdf_path),
            "text_path": str(text_path),
            "document_date": "2013-06-09",
            "regulation_name": "Règlement du Conseil communal",
            "body": "Conseil communal",
            "jurisdiction": "La Tour-de-Peilz",
            "is_normative": True,
            "contains_articles": True,
            "article_number": number,
            "article_title": article["article_title"],
            "article_text": article["article_text"],
            "title_heading": article.get("title_heading"),
            "chapter": article.get("chapter"),
            "section": article.get("section"),
            "title_path": article["title_path"],
            "sort_key": article["sort_key"],
            "search_facets": sorted(set(facets)),
            "metadata_version": "metadata-audit-v2",
            "text_extraction_status": {
                "characters_extracted": len(body),
                "text_available": bool(body.strip()),
                "needs_ocr": False,
            },
            "regulation": {
                "name": "Règlement du Conseil communal",
                "doc_type": REGLEMENT_DOC_TYPE,
                "article_number": number,
                "article_title": article["article_title"],
                "title_path": article["title_path"],
                "is_article_chunk": True,
            },
        }
        json_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        records.append(metadata)
    return records


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
    article_records = write_reglement_articles(text, pdf_url, pdf_path)

    metadata = {
        "commune": "La Tour-de-Peilz",
        "year": YEAR,
        "category": REGLEMENT_ARTICLES_CATEGORY,
        "doc_type": REGLEMENT_DOC_TYPE,
        "institutional_category": REGLEMENT_DOC_TYPE,
        "content_kind": "legal_normative_text",
        "title": "Règlement du Conseil communal",
        "filename": pdf_path.name,
        "pdf_url": pdf_url,
        "source_page": REGLEMENT_VIEWER_URL,
        "pdf_path": str(pdf_path),
        "text_path": str(text_path),
        "document_date": "2013-06-09",
        "regulation_name": "Règlement du Conseil communal",
        "body": "Conseil communal",
        "jurisdiction": "La Tour-de-Peilz",
        "is_normative": True,
        "contains_articles": True,
        "article_count": len(article_records),
        "article_index_path": str(DOCUMENTS_ROOT / YEAR / REGLEMENT_ARTICLES_CATEGORY / REGLEMENT_DOC_TYPE),
        "search_facets": [
            "conseil_communal",
            "institutionnel",
            "reglement",
            "reglement_conseil_communal",
            "texte_normatif",
            "articles",
            "commissions",
            "bureau",
            "president_conseil",
            "vote",
            "quorum",
            "motion_postulat_interpellation",
            "budget_comptes",
        ],
        "regulation": {
            "name": "Règlement du Conseil communal",
            "doc_type": REGLEMENT_DOC_TYPE,
            "adopted_date": "2013-06-09",
            "effective_year": "2017",
            "body": "Conseil communal",
            "jurisdiction": "La Tour-de-Peilz",
            "article_count": len(article_records),
            "article_numbers": [record["article_number"] for record in article_records],
        },
        "institutional_document": {
            "body": "Conseil communal",
            "institutional_category": REGLEMENT_DOC_TYPE,
            "contains_regulation": True,
            "contains_articles": True,
            "article_count": len(article_records),
        },
        "metadata_version": "metadata-audit-v2",
        "text_extraction_status": {
            "characters_extracted": len(text),
            "text_available": len(text.strip()) > 20,
            "needs_ocr": False,
        },
        "characters_extracted": len(text),
    }
    json_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
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
    article_dir = DOCUMENTS_ROOT / YEAR / REGLEMENT_ARTICLES_CATEGORY / REGLEMENT_DOC_TYPE
    manifest = {
        "commune": "La Tour-de-Peilz",
        "source_page": MAIN_URL,
        "documents_count": len(records),
        "regulation_articles_count": len(list(article_dir.glob("*.json"))) if article_dir.exists() else 0,
        "documents": records,
    }
    manifest_path = DATA_ROOT / "manifest_conseil_communal_institutionnel.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    for record in records:
        print(f"{record['institutional_category']} - {record['characters_extracted']} chars")
    print(f"Regulation articles: {manifest['regulation_articles_count']}")
    print(f"Manifest: {manifest_path}")


if __name__ == "__main__":
    main()
