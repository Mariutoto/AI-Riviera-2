from __future__ import annotations

import re
import json
from datetime import date
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.text_cleaning import strip_accents


POLITICAL_TYPES = {
    "motions": ("motion", "political_motion"),
    "postulats": ("postulat", "political_postulate"),
    "interpellations": ("interpellation", "political_interpellation"),
}

PROJECT_ROOT = Path(__file__).resolve().parents[1]
COUNCIL_MEMBERS_PATH = PROJECT_ROOT / "data" / "institutionnel" / "la-tour-de-peilz" / "conseil_communal_members.json"
PARTY_ALIASES = {
    "plr": "PLR",
    "plr les liberaux radicaux": "PLR",
    "les liberaux radicaux": "PLR",
    "parti socialiste et divers de gauche": "PSDG",
    "psdg": "PSDG",
    "les vert e s": "LV",
    "les vertes": "LV",
    "les verts": "LV",
    "les": "LV",
    "lv": "LV",
    "le centre independants plus vert liberaux": "LCIVL",
    "le centre independants plus vertliberaux": "LCIVL",
    "lcivl": "LCIVL",
    "la tour de peilz libre": "LTDPL",
    "ltdpl": "LTDPL",
    "hors parti": "Hors parti",
}

CATEGORY_CONTENT_KIND = {
    "budgets": "municipal_budget",
    "budget": "municipal_budget",
    "preavis-municipaux": "municipal_preavis",
    "proces-verbaux": "council_minutes",
    "ordres-du-jour": "council_agenda",
    "communications-municipales": "municipal_communication",
    "informations-diverses": "misc_information",
    "infos-municipalite": "municipality_news",
    "rapport-de-gestion": "annual_management_report",
    "rapport-gestion": "annual_management_report",
    "rapport-des-comptes": "annual_accounts_report",
    "conseil-communal": "institutional_document",
    "institutionnel": "institutional_document",
    "autres": "other_document",
}

FUTURE_TABLES_BY_CATEGORY = {
    "budget": ["financial_summary_tables", "financial_account_lines"],
    "budgets": ["financial_summary_tables", "financial_account_lines"],
    "preavis-municipaux": ["council_decisions", "preavis_objects", "commission_reports"],
    "proces-verbaux": ["council_session_items", "council_votes", "speaker_interventions"],
    "ordres-du-jour": ["council_session_items", "document_links"],
    "communications-municipales": ["municipal_communications", "people_mentions"],
    "informations-diverses": ["council_calendar_events", "administrative_deadlines"],
    "infos-municipalite": ["municipal_decisions", "financial_amount_mentions", "event_authorizations"],
    "rapport-de-gestion": ["annual_report_sections", "department_activity_summaries", "annual_indicators"],
    "rapport-gestion": ["annual_report_sections", "department_activity_summaries", "annual_indicators"],
    "rapport-des-comptes": ["annual_account_sections", "financial_summary_tables"],
    "conseil-communal": ["institutional_references", "council_members"],
    "institutionnel": ["institutional_references", "council_members"],
    "autres": ["document_links", "misc_information"],
}

CATEGORY_TEMPLATE_SOURCE = {
    "budget": "01-budget.enriched.json",
    "budgets": "01-budget.enriched.json",
    "preavis-municipaux": "02-preavis-municipal.enriched.json",
    "proces-verbaux": "03-proces-verbal.enriched.json",
    "ordres-du-jour": "04-ordre-du-jour.enriched.json",
    "communications-municipales": "05-communication-municipale.enriched.json",
    "informations-diverses": "06-information-diverse.enriched.json",
    "infos-municipalite": "07-info-municipalite.enriched.json",
    "motions": "08-motion.enriched.json",
    "postulats": "09-postulat.enriched.json",
    "interpellations": "10-interpellation.enriched.json",
    "rapport-de-gestion": "11-rapport-gestion.enriched.json",
    "rapport-gestion": "11-rapport-gestion.enriched.json",
    "rapport-des-comptes": "11-rapport-gestion.enriched.json",
    "conseil-communal": "12-institutionnel.enriched.json",
    "institutionnel": "12-institutionnel.enriched.json",
    "autres": "12-institutionnel.enriched.json",
}

CATEGORY_SEARCH_FACETS = {
    "budgets": ["budget", "finances"],
    "budget": ["budget", "finances"],
    "preavis-municipaux": ["preavis", "municipalite", "conseil_communal"],
    "proces-verbaux": ["proces_verbal", "conseil_communal", "seance"],
    "ordres-du-jour": ["ordre_du_jour", "conseil_communal", "seance"],
    "communications-municipales": ["communication_municipale", "municipalite"],
    "informations-diverses": ["information_diverse"],
    "infos-municipalite": ["infos_municipalite", "municipalite"],
    "rapport-de-gestion": ["rapport_gestion", "rapport_annuel", "municipalite"],
    "rapport-gestion": ["rapport_gestion", "rapport_annuel", "municipalite"],
    "rapport-des-comptes": ["rapport_comptes", "comptes", "finances"],
    "conseil-communal": ["institutionnel", "conseil_communal", "reglement"],
    "institutionnel": ["institutionnel", "reglement"],
    "autres": ["document", "information"],
    "motions": ["motion", "conseil_communal", "municipalite"],
    "postulats": ["postulat", "conseil_communal", "municipalite"],
    "interpellations": ["interpellation", "conseil_communal", "municipalite"],
}


def normalize(text: str) -> str:
    return strip_accents(str(text or "")).lower()


def normalize_party(value: str | None) -> str | None:
    if not value:
        return None
    compact = re.sub(r"[^a-z0-9]+", " ", normalize(value)).strip()
    if compact in PARTY_ALIASES:
        return PARTY_ALIASES[compact]
    upper = str(value).strip().upper()
    if upper in {"PLR", "PSDG", "LV", "LCIVL", "LTDPL"}:
        return upper
    return str(value).strip() or None


@lru_cache(maxsize=1)
def council_member_party_lookup() -> dict[str, str]:
    if not COUNCIL_MEMBERS_PATH.exists():
        return {}
    try:
        payload = json.loads(COUNCIL_MEMBERS_PATH.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    lookup = {}
    for member in payload.get("members", []):
        if not isinstance(member, dict):
            continue
        party = normalize_party(member.get("party"))
        names = [
            member.get("name"),
            f"{member.get('first_name', '')} {member.get('last_name', '')}",
            f"{member.get('last_name', '')} {member.get('first_name', '')}",
            member.get("last_name"),
        ]
        for name in names:
            key = normalize(str(name or "")).strip()
            if key and party:
                lookup[key] = party
    return lookup


def party_for_author(name: str, current_party: str | None = None) -> str | None:
    party = normalize_party(current_party)
    if party:
        return party
    lookup = council_member_party_lookup()
    normalized_name = normalize(name).strip()
    if normalized_name in lookup:
        return lookup[normalized_name]
    pieces = normalized_name.split()
    if pieces:
        return lookup.get(pieces[-1])
    return None


def display_name(raw_name: str) -> str:
    name = re.sub(r"\s+", " ", raw_name).strip(" ,.;:-")
    name = re.sub(r"^(M\.|Mme|MM\.|Mmes)\s+", "", name, flags=re.IGNORECASE)
    return name.strip()


def extract_author_party_pairs(title: str) -> list[dict[str, str | None]]:
    title = str(title or "")
    if not title:
        return []

    intro = re.split(r"\s+-\s+|\s+\"", title, maxsplit=1)[0]
    intro = re.sub(r"^(Motion|Postulat|Interpellation)\s+(de|du|des)\s+", "", intro, flags=re.IGNORECASE).strip()
    intro = re.sub(r"\s+\+\s+R[e\u00e9]ponse.*$", "", intro, flags=re.IGNORECASE).strip()
    matches = re.findall(
        r"\b(?:Mme|M\.|MM\.|Mmes)\s+([^()]+?)\s*\(([A-Z0-9/-]{2,})\)",
        intro,
        flags=re.IGNORECASE,
    )
    pairs = []
    for raw_name, party in matches:
        name = display_name(raw_name)
        if name:
            pairs.append({"name": name, "party": party_for_author(name, party)})
    if pairs:
        return pairs

    group_match = re.search(r"\bgroupe\s+([A-Z0-9/-]{2,})\b", intro, flags=re.IGNORECASE)
    if group_match:
        party = group_match.group(1)
        return [{"name": f"groupe {party}", "party": normalize_party(party)}]
    return []


def infer_legislature(year: Any) -> str | None:
    try:
        numeric_year = int(str(year))
    except (TypeError, ValueError):
        return None
    if 2021 <= numeric_year <= 2026:
        return "2021-2026"
    return None


def infer_status(title: str, filename: str) -> str | None:
    haystack = normalize(f"{title} {filename}")
    if "renvoye directement" in haystack or "renvoye a la municipalite" in haystack:
        return "renvoye_municipalite"
    if "retire par le postulant" in haystack or "retiree par le postulant" in haystack:
        return "retire"
    if "+ reponse" in haystack or (
        filename.lower().startswith(("interpellation-", "motion-", "postulat-")) and "-rep" in filename.lower()
    ):
        return "depot_avec_reponse"
    if filename.lower().startswith("reponse-"):
        return "reponse"
    return "depot"


def infer_category(metadata: dict[str, Any], text_path: Path | None = None) -> str:
    category = str(metadata.get("category") or metadata.get("doc_type") or "")
    if category:
        return category
    if text_path and len(text_path.parts) >= 2:
        return text_path.parts[-2]
    return ""


def infer_content_kind(metadata: dict[str, Any], category: str) -> str:
    filename = normalize(metadata.get("filename", ""))
    explicit_type = normalize(metadata.get("type", ""))
    if category in POLITICAL_TYPES:
        return POLITICAL_TYPES[category][1]
    if explicit_type in {"motion", "postulat", "interpellation"}:
        return f"political_{explicit_type}"
    if "rapport" in filename and "gestion" in filename:
        return "annual_management_report"
    return CATEGORY_CONTENT_KIND.get(category, "municipal_document")


def merge_unique(existing: Any, additions: list[str]) -> list[str]:
    values = []
    for value in existing if isinstance(existing, list) else []:
        if isinstance(value, str) and value not in values:
            values.append(value)
    for value in additions:
        if value and value not in values:
            values.append(value)
    return values


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


LEGAL_REFERENCE_PATTERNS = [
    r"\b(?:art\.?|article)\s+\d+[a-z]?(?:\s*,?\s*(?:al\.?|alin[eé]a)\s*\d+)?\s*(?:[A-Z]{2,}|du\s+r[eè]glement\s+communal)?",
    r"\b(?:Loi|R[eè]glement|Ordonnance|Prescriptions)[^.\n;]{0,90}(?:\([A-Z]{2,}\)|[A-Z]{2,})",
    r"\b(?:LPR|RLPR|OCR|OSR|LAT|LATC|LRou|DETEC|OFROU|UCV|ACS|UVS)\b",
]


STOPWORD_FACETS = {
    "2021", "2022", "2023", "2024", "2025", "2026",
    "afin", "ainsi", "alors", "avec", "aux", "cette", "ces", "ceux",
    "dans", "des", "directement", "donc", "elle", "elles", "entre",
    "est", "etre", "faire", "fait", "font", "leur", "leurs", "lors",
    "mais", "nous", "par", "plus", "pour", "quand", "que", "quel",
    "quels", "quelle", "quelles", "qui", "renvoye", "sans", "ses",
    "son", "sont", "sur", "tous", "tout", "toute", "toutes", "une",
    "vers", "votre", "commune", "communal", "communale", "communaux",
    "conseil", "municipalite", "seances", "chf",
}

CANONICAL_FACET_PHRASES = {
    "frais de garde": "frais_de_garde",
    "remboursement des frais de garde": "frais_de_garde",
    "participation politique": "participation_politique",
    "conseil communal": "conseil_communal",
    "reponse ecrite": "reponse_ecrite",
    "réponse écrite": "reponse_ecrite",
    "revêtement phonoabsorbant": "revetement_phonoabsorbant",
    "revetement phonoabsorbant": "revetement_phonoabsorbant",
    "bruit routier": "bruit_routier",
    "zone 30": "zone_30",
    "30 km": "zone_30",
    "30km": "zone_30",
    "affichage politique": "affichage_politique",
    "affichage publicitaire": "affichage_publicitaire",
    "mobilite douce": "mobilite_douce",
    "mobilité douce": "mobilite_douce",
    "transition energetique": "transition_energetique",
    "transition énergétique": "transition_energetique",
    "developpement durable": "developpement_durable",
    "développement durable": "developpement_durable",
    "securite routiere": "securite_routiere",
    "sécurité routière": "securite_routiere",
    "accueil de jour": "accueil_de_jour",
}


def parse_french_date(text: str) -> str | None:
    match = re.search(
        r"\b(?:La Tour-de-Peilz,?\s*)?(?:le\s+)?(\d{1,2})\s+"
        r"(janvier|f[eé]vrier|mars|avril|mai|juin|juillet|ao[uû]t|septembre|octobre|novembre|d[eé]cembre)"
        r"\s+(20\d{2})\b",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    day, month_name, year = match.groups()
    month = MONTHS_FR.get(month_name.lower())
    if not month:
        return None
    return f"{year}-{month}-{int(day):02d}"


def extract_iso_dates(content: str) -> list[str]:
    dates = []
    for match in re.finditer(
        r"\b(\d{1,2})[./-](\d{1,2})[./-](20\d{2}|\d{2})\b",
        content,
    ):
        day, month, year = match.groups()
        if len(year) == 2:
            year = f"20{year}"
        try:
            parsed_date = date(int(year), int(month), int(day))
        except ValueError:
            continue
        value = parsed_date.isoformat()
        if value not in dates:
            dates.append(value)

    for match in re.finditer(
        r"\b(\d{1,2})\s+"
        r"(janvier|f[eÃ©]vrier|mars|avril|mai|juin|juillet|ao[uÃ»]t|septembre|octobre|novembre|d[eÃ©]cembre)"
        r"\s+(20\d{2})\b",
        content,
        flags=re.IGNORECASE,
    ):
        parsed = parse_french_date(match.group(0))
        if parsed and parsed not in dates:
            dates.append(parsed)
    return dates[:30]


def valid_iso_date(value: Any) -> bool:
    try:
        date.fromisoformat(str(value)[:10])
        return True
    except (TypeError, ValueError):
        return False


def has_money(content: str) -> bool:
    return bool(re.search(r"\b(?:CHF|Fr\.?|francs?)\s*[-'\d ]{3,}|[-'\d ]{3,}\s*(?:CHF|Fr\.?|francs?)\b", content, flags=re.IGNORECASE))


def count_pdf_links(content: str) -> int:
    return len(re.findall(r"https?://\S+\.pdf\b|\b[\w.-]+\.pdf\b", content, flags=re.IGNORECASE))


def extract_number(pattern: str, text: str) -> str | None:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    return match.group(1).strip() if match else None


def generic_topic_facets(title: str, summary: str, content: str) -> list[str]:
    return keyword_facets(title, summary, content)[:10]


def clean_line(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" \t\r\n-•●:;.")


def political_request_section(content: str) -> str:
    markers = [
        r"\bR[ÉE]PONSE MUNICIPALE\b",
        r"\bREPONSE MUNICIPALE\b",
        r"\bRÃ‰PONSE MUNICIPALE\b",
        r"\bR[eé]ponse\s+[àa]\s+l['’]interpellation\b",
        r"\bRÃ©ponse\s+Ã\s+l'interpellation\b",
    ]
    section = content
    for marker in markers:
        match = re.search(marker, section, flags=re.IGNORECASE)
        if match:
            section = section[: match.start()]
    return section


def extract_question_lines(content: str) -> list[str]:
    content = political_request_section(content)
    lines = [clean_line(line) for line in content.splitlines()]
    questions: list[str] = []
    collecting = False
    current = ""
    bullet_pattern = r"^(?:[a-z]\)|\d+[\).]|[-*•●]|â\S{1,3})\s*"

    for line in lines:
        if not line:
            continue
        normalized = normalize(line)
        if "questions suivantes" in normalized or "questions susmentionnees" in normalized:
            collecting = True
            current = ""
            continue
        if collecting and re.match(r"^(remerci|merci|nous remercions|m\.|mme|references|références)\b", normalized):
            break
        if collecting and re.match(bullet_pattern, line, flags=re.IGNORECASE):
            if current:
                questions.append(current)
            current = clean_line(re.sub(bullet_pattern, "", line, flags=re.IGNORECASE))
            if line.endswith("?"):
                questions.append(current)
                current = ""
            continue
        if collecting and current:
            current = clean_line(f"{current} {line}")
            if line.endswith("?"):
                questions.append(current)
                current = ""
            continue
        if collecting:
            current = clean_line(re.sub(bullet_pattern, "", line, flags=re.IGNORECASE))
            if line.endswith("?"):
                questions.append(current)
                current = ""
            continue
        if collecting and "?" in line:
            questions.append(line)

    if current:
        questions.append(current)

    if not questions:
        questions = [clean_line(match.group(0)) for match in re.finditer(r"[^?\n]{20,220}\?", content)]

    unique = []
    for question in questions:
        if question and question not in unique:
            unique.append(question)
    return unique


def extract_legal_references(content: str) -> list[str]:
    content = political_request_section(content)
    references: list[str] = []
    for index, pattern in enumerate(LEGAL_REFERENCE_PATTERNS):
        flags = re.IGNORECASE if index != 1 else 0
        for match in re.finditer(pattern, content, flags=flags):
            value = clean_line(match.group(0))
            if value and value not in references:
                references.append(value)
    return references[:20]


def extract_content_authors(content: str) -> list[dict[str, str | None]]:
    candidates: list[dict[str, str | None]] = []
    tail = "\n".join(content.splitlines()[-45:])
    patterns = [
        r"\b(Mme|M\.)\s+([A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÿ' -]{2,})\s*\(([^)]+)\)",
        r"\b([A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÿ' -]{2,})\s*,\s*(?:les\s+Vert\.e\.s|Les\s+Vert[·.e]*s|PLR|PSDG|UDC|LV|LCIVL|LTDPL|VLRB|VERTS?)",
        r"^\s*(M\.|Mme)\s+([A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÿ' -]{2,})\s*$",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, tail, flags=re.IGNORECASE | re.MULTILINE):
            groups = match.groups()
            if len(groups) == 3:
                name = display_name(groups[1])
                party = clean_line(groups[2])
            else:
                name = display_name(groups[-1])
                full = match.group(0)
                party_match = re.search(r"\(([^)]+)\)|,\s*([A-Z]{2,}|les\s+Vert\.e\.s|Les\s+Vert[·.e]*s)", full, flags=re.IGNORECASE)
                party = clean_line((party_match.group(1) or party_match.group(2)) if party_match else "") or None
            if name and not any(author["name"] == name for author in candidates):
                candidates.append({"name": name, "party": party_for_author(name, party)})
    return candidates


def slug_facet(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", normalize(value)).strip("_")
    return slug


def keyword_facets(title: str, summary: str, content: str) -> list[str]:
    source = f"{title} {summary}"
    words = re.findall(r"(?:[^\W_]|[0-9]){4,}", source, flags=re.UNICODE)
    facets = []
    for word in words:
        normalized = normalize(word)
        if normalized in STOPWORD_FACETS:
            continue
        facet = slug_facet(normalized)
        if facet and facet not in facets:
            facets.append(facet)
    acronyms = re.findall(r"\b[A-Z]{2,}\b", content)
    for acronym in acronyms:
        if acronym not in facets:
            facets.append(acronym)
    return facets[:16]


def author_name_tokens(authors: Any) -> set[str]:
    tokens: set[str] = set()
    if not isinstance(authors, list):
        return tokens
    for author in authors:
        if not isinstance(author, dict):
            continue
        for token in re.findall(r"[^\W\d_]{3,}", str(author.get("name") or ""), flags=re.UNICODE):
            tokens.add(normalize(token))
    return tokens


def clean_search_facets(metadata: dict[str, Any], content: str | None = None, limit: int = 14) -> list[str]:
    content = content or ""
    title = str(metadata.get("title") or "")
    summary = str(metadata.get("summary") or "")
    category = str(metadata.get("category") or "")
    object_type = str(metadata.get("type") or "")
    authors = metadata.get("authors")
    author_tokens = author_name_tokens(authors)
    haystack = normalize(f"{title} {summary} {content[:3000]}")
    cleaned: list[str] = []

    def add(value: Any) -> None:
        if value is None:
            return
        facet = slug_facet(str(value))
        if not facet or len(facet) < 3:
            return
        if facet in STOPWORD_FACETS or facet in author_tokens:
            return
        if facet not in cleaned:
            cleaned.append(facet)

    if category:
        add(category.rstrip("s"))
    if object_type:
        add(object_type)

    for base in CATEGORY_SEARCH_FACETS.get(category, []):
        add(base)

    for phrase, facet in CANONICAL_FACET_PHRASES.items():
        if normalize(phrase) in haystack:
            add(facet)

    if isinstance(authors, list):
        for author in authors:
            if isinstance(author, dict):
                add(author.get("party"))

    for facet in metadata.get("search_facets") if isinstance(metadata.get("search_facets"), list) else []:
        add(facet)

    for facet in keyword_facets(title, summary, content):
        add(facet)

    return cleaned[:limit]


def clean_final_metadata(enriched: dict[str, Any]) -> dict[str, Any]:
    enriched.pop("extraction_notes", None)
    enriched.pop("metadata_template", None)
    enriched.pop("language", None)
    enriched.pop("characters_extracted", None)

    policy_details = enriched.get("policy_details")
    if isinstance(policy_details, dict):
        policy_details.pop("legal_references", None)
        if not policy_details:
            enriched.pop("policy_details", None)

    text_status = enriched.get("text_extraction_status")
    if isinstance(text_status, dict):
        text_status.setdefault("text_available", bool(text_status.get("characters_extracted")))
        text_status.setdefault("needs_ocr", False)

    return enriched


def infer_request_type(object_type: str, content: str) -> str | None:
    normalized = normalize(content)
    if object_type == "interpellation":
        if "reponse ecrite" in normalized or "par ecrit" in normalized:
            return "written_answer"
        return "answer"
    if object_type == "postulat":
        if "etudier" in normalized or "rapport" in normalized:
            return "study_and_report"
        return "study"
    if object_type == "motion":
        return "binding_request"
    return None


def enrich_political_fields(enriched: dict[str, Any], object_type: str, content: str | None) -> None:
    content = content or ""
    title = str(enriched.get("title") or "")
    summary = str(enriched.get("summary") or "")
    political_object = enriched.setdefault("political_object", {})
    if not isinstance(political_object, dict):
        political_object = {}
        enriched["political_object"] = political_object

    date = parse_french_date(content)
    if date:
        enriched.setdefault("document_date", date)
        political_object["document_date"] = date

    political_object["request_type"] = infer_request_type(object_type, content)
    political_object["contains_budget_impact"] = bool(re.search(r"\b(budget|co[uû]t|cr[eé]dit|francs?|CHF|fr\.)\b", content, flags=re.IGNORECASE))

    if object_type == "interpellation":
        questions = extract_question_lines(content)
        political_object["contains_questions"] = bool(questions)
        political_object["question_count"] = len(questions)
        political_object["expects_municipal_answer"] = True

    legal_references = extract_legal_references(content)
    if legal_references:
        policy_details = enriched.setdefault("policy_details", {})
        if isinstance(policy_details, dict):
            policy_details["legal_references"] = legal_references
            if object_type == "interpellation":
                questions = extract_question_lines(content)
                if questions:
                    policy_details["question_topics"] = questions[:8]

    if not enriched.get("authors"):
        enriched["authors"] = extract_content_authors(content)

    base_notes = [
        "object type",
        "authors and parties",
        "status",
        "document date",
        "target body",
        "request type",
    ]
    if object_type == "interpellation":
        base_notes.extend(["question count", "question topics", "expected municipal answer"])
    enriched["search_facets"] = merge_unique(
        enriched.get("search_facets"),
        [object_type, *keyword_facets(title, summary, content)],
    )


def enrich_category_specific_fields(enriched: dict[str, Any], category: str, content: str | None) -> None:
    content = content or ""
    title = str(enriched.get("title") or "")
    summary = str(enriched.get("summary") or "")
    filename = str(enriched.get("filename") or "")
    normalized = normalize(f"{title} {summary} {filename} {content[:2500]}")
    dates = extract_iso_dates(content)

    if category in {"budget", "budgets"}:
        fiscal_year = str(enriched.get("year") or extract_number(r"\bBudget[_ -]?(20\d{2})\b", filename) or "")
        enriched.pop("currency", None)
        enriched.setdefault("financial_document_type", "budget")
        enriched.setdefault("fiscal_year", fiscal_year)
        enriched.setdefault("financial_document", {
            "type": "budget",
            "fiscal_year": fiscal_year,
            "contains_financial_summary_tables": True,
            "contains_financial_account_lines": True,
            "contains_amounts": True,
        })
        enriched["search_facets"] = merge_unique(enriched.get("search_facets"), ["budget", "finances"])

    elif category == "preavis-municipaux":
        preavis_number = (
            enriched.get("preavis_number")
            or extract_number(r"\bpr[ée]avis(?:\s+municipal)?\s*(?:n[°o]\s*)?([0-9]+/[0-9]{4}|[0-9]+)\b", content)
            or extract_number(r"\bPreavis[-_ ]?([0-9]+)", filename)
        )
        document_role = enriched.get("document_role") or "municipal_preavis"
        enriched.setdefault("preavis_reference", {
            "number": preavis_number,
            "object_title": enriched.get("object_title"),
            "official_listing_label": enriched.get("official_listing_label"),
        })
        if document_role in {"municipal_preavis", "combined_preavis_report_decision"} or enriched.get("contains_preavis"):
            enriched.setdefault("municipal_document", {
                "type": "preavis_municipal",
                "number": preavis_number,
                "issuing_body": "Municipalite",
                "target_body": "Conseil communal",
                "contains_financial_decision": has_money(content),
            })
        enriched.setdefault("decision_signals", {
            "contains_vote": "vote" in normalized or "adopte" in normalized or "refuse" in normalized,
            "contains_commission_report": bool(enriched.get("contains_report")) or "rapport" in normalized or filename.lower().endswith("-rapp.pdf"),
            "contains_decision": bool(enriched.get("contains_decision")) or "decision" in normalized or filename.lower().endswith("-dec.pdf"),
            "contains_minority_report": bool(enriched.get("contains_minority_report")) or "minorite" in normalized,
        })
        enriched["search_facets"] = merge_unique(enriched.get("search_facets"), ["preavis", "decision", "rapport"] if "rapport" in normalized else ["preavis"])

    elif category == "proces-verbaux":
        session_date = enriched.get("session_date") or (dates[0] if dates else None)
        pv_number = enriched.get("pv_number") or extract_number(r"\bPV\s*0*([0-9]+)\b", filename) or extract_number(r"\bproc[eè]s-verbal\s*(?:no|n[°o])?\s*0*([0-9]+)\b", content)
        enriched.setdefault("session", {
            "body": "Conseil communal",
            "pv_number": pv_number,
            "date": session_date,
            "legislature": enriched.get("legislature"),
        })
        enriched.setdefault("content_signals", {
            "contains_agenda": "ordre du jour" in normalized,
            "contains_votes": any(term in normalized for term in ["vote", "adopte", "refuse", "abstention"]),
            "contains_attendance": any(term in normalized for term in ["appel", "excuse", "absent", "present"]),
            "contains_political_objects": any(term in normalized for term in ["motion", "postulat", "interpellation", "preavis"]),
        })

    elif category == "ordres-du-jour":
        session_date = enriched.get("session_date") or (dates[0] if dates else None)
        linked_count = enriched.get("linked_documents_count")
        if linked_count is None:
            linked_count = count_pdf_links(content)
        enriched.setdefault("session", {
            "body": "Conseil communal",
            "date": session_date,
            "legislature": enriched.get("legislature"),
            "public_session": True,
        })
        enriched["linked_documents"] = {
            "linked_documents_count": linked_count,
            "contains_linked_documents": linked_count > 0,
            "should_link_to_documents_table": True,
        }
        enriched.setdefault("agenda_structure", {
            "contains_numbered_items": bool(re.search(r"^\s*\d+[.)]", content, flags=re.MULTILINE)),
            "contains_political_objects": any(term in normalized for term in ["motion", "postulat", "interpellation", "preavis"]),
        })

    elif category == "communications-municipales":
        number = extract_number(r"\b(?:communication|comm\.?)\s*(?:municipale)?\s*(?:n[°o]\s*)?([0-9]+/[0-9]{4}|[0-9]+)\b", content) or extract_number(r"\bComm(?:unication)?[-_ ]?0*([0-9]+)", filename)
        enriched.setdefault("communication", {
            "number": number,
            "issuing_body": "Municipalite",
            "target_body": "Conseil communal",
            "document_date": dates[0] if dates else enriched.get("document_date"),
        })
        enriched.setdefault("content_signals", {
            "contains_person_names": bool(re.search(r"\b[A-Z][^\W\d_]+ [A-Z][^\W\d_]+", content, flags=re.UNICODE)),
            "contains_financial_amounts": has_money(content),
            "contains_public_works": any(term in normalized for term in ["travaux", "chantier", "amenagement", "assainissement"]),
        })

    elif category == "informations-diverses":
        if enriched.get("metadata_version") != "metadata-audit-v2":
            enriched.setdefault("misc_information", {
                "contains_calendar": any(term in normalized for term in ["agenda", "calendrier", "seance", "echeance"]),
                "contains_linked_documents": count_pdf_links(content) > 0,
                "contains_financial_amounts": has_money(content),
            })

    elif category == "infos-municipalite":
        publication_date = enriched.get("date") or enriched.get("document_date") or (dates[0] if dates else None)
        enriched.setdefault("digest", {
            "issuing_body": "Municipalite",
            "publication_date": publication_date,
            "contains_decisions": any(term in normalized for term in ["decide", "decision", "autorise", "adjuge", "accorde"]),
        })
        enriched.setdefault("content_signals", {
            "contains_financial_amounts": has_money(content),
            "contains_event_authorizations": "autorisation" in normalized,
            "contains_public_works_awards": any(term in normalized for term in ["adjudication", "travaux"]),
            "contains_subsidies": any(term in normalized for term in ["subvention", "soutien financier", "aide financiere"]),
        })

    elif category in {"rapport-de-gestion", "rapport-gestion", "rapport-des-comptes"}:
        fiscal_year = str(enriched.get("year") or "")
        report_scope = "annual_accounts" if category == "rapport-des-comptes" else "municipal_activity"
        enriched.setdefault("report", {
            "fiscal_year": fiscal_year,
            "report_scope": report_scope,
            "issuing_body": "Municipalite",
            "target_body": "Conseil communal",
            "large_document": (enriched.get("characters_extracted") or len(content)) > 50000,
            "contains_table_of_contents": "table des matieres" in normalized or "sommaire" in normalized,
            "contains_commission_report": "commission" in normalized,
            "contains_municipal_response": "reponse de la municipalite" in normalized,
        })

    elif category in {"conseil-communal", "institutionnel"}:
        enriched.setdefault("institutional_document", {
            "body": "Conseil communal",
            "institutional_category": enriched.get("institutional_category"),
            "contains_council_members": "membre" in normalized or bool(enriched.get("members_count")),
            "members_count": enriched.get("members_count"),
            "contains_regulation": "reglement" in normalized,
        })

    elif category == "autres":
        enriched.setdefault("misc_information", {
            "contains_linked_documents": count_pdf_links(content) > 0,
            "contains_financial_amounts": has_money(content),
            "contains_dates": bool(dates),
        })

    if dates and not enriched.get("document_date") and category not in {
        "budget",
        "budgets",
        "proces-verbaux",
        "ordres-du-jour",
        "rapport-de-gestion",
        "rapport-gestion",
        "rapport-des-comptes",
    }:
        enriched["document_date"] = dates[0]

    enriched["search_facets"] = merge_unique(enriched.get("search_facets"), generic_topic_facets(title, summary, content))

def enrich_metadata(metadata: dict[str, Any], text_path: Path | None = None, content: str | None = None) -> dict[str, Any]:
    enriched = deepcopy(metadata)
    if enriched.get("document_date") and not valid_iso_date(enriched.get("document_date")):
        enriched.pop("document_date", None)
    category = infer_category(enriched, text_path)
    filename = str(enriched.get("filename") or (text_path.with_suffix(".pdf").name if text_path else ""))
    title = str(enriched.get("title") or filename)
    year = enriched.get("year") or (text_path.parts[-3] if text_path and len(text_path.parts) >= 3 else "")
    content_kind = infer_content_kind(enriched, category)

    enriched.setdefault("metadata_version", "metadata-audit-v1")
    enriched.setdefault("metadata_template", CATEGORY_TEMPLATE_SOURCE.get(category))
    enriched.setdefault("commune", "La Tour-de-Peilz")
    enriched.setdefault("category", category)
    enriched.setdefault("content_kind", content_kind)
    enriched.setdefault("language", "fr")
    enriched.setdefault("year", str(year) if year != "" else "")
    enriched.setdefault("listing_year", enriched.get("year"))
    enriched.setdefault("filename", filename)
    enriched.setdefault("title", title)

    legislature = infer_legislature(enriched.get("year"))
    if legislature:
        enriched.setdefault("legislature", legislature)

    facets = CATEGORY_SEARCH_FACETS.get(category, [])
    enriched["search_facets"] = merge_unique(enriched.get("search_facets"), facets)

    characters_extracted = enriched.get("characters_extracted")
    if characters_extracted is None and content is not None:
        characters_extracted = len(content)
        enriched["characters_extracted"] = characters_extracted
    enriched.setdefault(
        "text_extraction_status",
        {
            "characters_extracted": characters_extracted,
            "text_available": bool(characters_extracted),
            "needs_ocr": False,
        },
    )

    if category in POLITICAL_TYPES:
        object_type, _ = POLITICAL_TYPES[category]
        status = enriched.get("status") or infer_status(title, filename)
        authors = enriched.get("authors") or extract_author_party_pairs(title)
        if isinstance(authors, list):
            normalized_authors = []
            for author in authors:
                if isinstance(author, dict):
                    name = str(author.get("name") or "")
                    normalized_authors.append({**author, "party": party_for_author(name, author.get("party"))})
            authors = normalized_authors
        enriched["authors"] = authors
        enriched.setdefault(
            "political_object",
            {
                "type": object_type,
                "status": status,
                "target_body": "Municipalite",
                "decision_body": "Conseil communal",
            },
        )
        if isinstance(enriched["political_object"], dict):
            enriched["political_object"].setdefault("type", object_type)
            enriched["political_object"].setdefault("status", status)
            enriched["political_object"].setdefault("target_body", "Municipalite")
            enriched["political_object"].setdefault("decision_body", "Conseil communal")
        enrich_political_fields(enriched, object_type, content)
    else:
        enrich_category_specific_fields(enriched, category, content)

    if content_kind == "annual_management_report":
        enriched.setdefault(
            "report",
            {
                "fiscal_year": enriched.get("year"),
                "report_scope": "municipal_activity",
                "issuing_body": "Municipalite",
                "target_body": "Conseil communal",
                "large_document": True,
            },
        )

    enriched["search_facets"] = clean_search_facets(enriched, content)
    enriched = clean_final_metadata(enriched)
    return enriched
