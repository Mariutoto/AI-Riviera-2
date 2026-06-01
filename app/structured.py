import json
import re
from collections import Counter
from functools import lru_cache

from app.config import STRUCTURED_DATA_DIR
from app.text_cleaning import strip_accents


DEPOSIT_TYPES = {"motion", "postulat", "interpellation"}
YEAR_PATTERN = re.compile(r"\b(20\d{2})\b")
MOST_PATTERN = re.compile(r"\b(qui|quel|quelle|quels|quelles)\b.*\b(plus|maximum|le plus)\b")


def normalize(text: str) -> str:
    return strip_accents(text).lower()


@lru_cache(maxsize=1)
def load_structured_data() -> dict:
    def read(name: str) -> list[dict]:
        path = STRUCTURED_DATA_DIR / name
        if not path.exists():
            return []
        return json.loads(path.read_text(encoding="utf-8-sig"))

    return {
        "sessions": read("sessions.json"),
        "political_objects": read("political_objects.json"),
        "documents": read("documents.json"),
    }


def latest_session(sessions: list[dict]) -> dict | None:
    dated_sessions = [session for session in sessions if session.get("session_date")]
    if not dated_sessions:
        return None
    return sorted(dated_sessions, key=lambda item: item["session_date"])[-1]


def wants_latest_session(question: str) -> bool:
    normalized = normalize(question)
    return any(term in normalized for term in ["derniere seance", "derniere séance", "plus recente", "plus recente"])


def wants_deposit_count(question: str) -> bool:
    normalized = normalize(question)
    has_count = any(term in normalized for term in ["combien", "nombre", "nb", "liste", "quels", "quelles"])
    has_deposit = any(term in normalized for term in ["depot", "depose", "deposes", "depots", "motion", "postulat", "interpellation"])
    return has_count and has_deposit


def extract_year(question: str) -> str | None:
    match = YEAR_PATTERN.search(normalize(question))
    return match.group(1) if match else None


def requested_object_types(question: str) -> set[str]:
    normalized = normalize(question)
    requested = set()
    if any(term in normalized for term in ["interpellation", "interpellations"]):
        requested.add("interpellation")
    if any(term in normalized for term in ["postulat", "postulats"]):
        requested.add("postulat")
    if any(term in normalized for term in ["motion", "motions"]):
        requested.add("motion")
    return requested or set(DEPOSIT_TYPES)


def wants_most_deposits(question: str) -> bool:
    normalized = normalize(question)
    has_deposit_verb = any(term in normalized for term in ["depose", "deposes", "depos", "depot", "dépose", "déposés"])
    has_year = bool(extract_year(question))
    has_more = bool(MOST_PATTERN.search(normalized)) or "le plus" in normalized or "plus de" in normalized
    return has_deposit_verb and has_year and has_more


def object_label(object_type: str) -> str:
    labels = {
        "motion": "motion",
        "postulat": "postulat",
        "interpellation": "interpellation",
    }
    return labels.get(object_type, object_type)


def count_by_type(objects: list[dict]) -> dict[str, int]:
    counts = {"motion": 0, "postulat": 0, "interpellation": 0}
    for item in objects:
        object_type = item.get("object_type", "")
        if object_type in counts:
            counts[object_type] += 1
    return counts


def display_name(raw_name: str) -> str:
    name = raw_name.strip()
    name = re.sub(r"\s*\([^)]*\)\s*$", "", name)
    name = re.sub(r"^(mme|m|mmes|mrs|mr)\.\s*", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\s+", " ", name)
    return name.strip(" ,;:")


def authors_for_item(item: dict) -> list[str]:
    title = str(item.get("title") or item.get("document_title") or "")
    raw_authors = title
    if raw_authors:
        raw_authors = re.split(r"\s*[«»–\-—]\s*", raw_authors, maxsplit=1)[0]
    title_match = re.search(r"^(?:Postulat|Motion|Interpellation)\s+de\s+(.+)$", raw_authors, flags=re.IGNORECASE)
    if title_match:
        raw_authors = title_match.group(1)
        raw_authors = re.sub(r"\s+et\s+consorts?\b", "", raw_authors, flags=re.IGNORECASE)
        pieces = re.split(r"\s+et\s+|\s*,\s*", raw_authors)
        cleaned = [display_name(piece) for piece in pieces if piece.strip()]
        if cleaned:
            return cleaned

    authors = item.get("authors") or []
    if isinstance(authors, str):
        authors = [authors]
    cleaned = [display_name(str(author)) for author in authors if str(author).strip()]
    return cleaned


def answer_most_deposits(question: str) -> str | None:
    if not wants_most_deposits(question):
        return None

    data = load_structured_data()
    year = extract_year(question)
    if not year:
        return None

    object_types = requested_object_types(question)
    deposits = [
        item
        for item in data["political_objects"]
        if str(item.get("year", "")) == year
        and item.get("object_type") in object_types
        and item.get("status") == "depot"
    ]
    if not deposits:
        return f"Je ne trouve aucun dépôt correspondant en {year}."

    counts: Counter[str] = Counter()
    examples: dict[str, list[str]] = {}
    for item in deposits:
        item_authors = authors_for_item(item)
        if not item_authors:
            continue
        unique_authors = list(dict.fromkeys(item_authors))
        for author in unique_authors:
            counts[author] += 1
            examples.setdefault(author, []).append(str(item.get("title", "")))

    if not counts:
        return (
            f"Je trouve bien des dépôts en {year}, mais les sources structurées ne permettent pas d'identifier clairement les auteurs."
        )

    top_count = max(counts.values())
    leaders = sorted(author for author, count in counts.items() if count == top_count)
    object_label_text = ", ".join(sorted(object_types))
    leader_text = ", ".join(leaders)

    lines = [
        f"En **{year}**, pour les **{object_label_text}** déposés, la personne ou le groupe le plus actif est **{leader_text}** avec **{top_count} dépôt(s)**.",
        "",
        "Détail des principaux dépôts:",
    ]

    for author in leaders[:3]:
        titles = examples.get(author, [])[:3]
        if titles:
            lines.append(f"- {author}: {', '.join(titles)}")

    return "\n".join(lines)


def answer_deposits_by_year(question: str) -> str | None:
    if not wants_deposit_count(question):
        return None

    data = load_structured_data()
    year = extract_year(question)
    if not year:
        return None

    object_types = requested_object_types(question)
    deposits = [
        item
        for item in data["political_objects"]
        if str(item.get("year", "")) == year
        and item.get("object_type") in object_types
        and item.get("status") == "depot"
    ]
    deposits = sorted(
        deposits,
        key=lambda item: (item.get("session_date", ""), item.get("agenda_item_number", ""), item.get("title", "")),
    )
    counts = count_by_type(deposits)

    if not deposits:
        return (
            f"Pour {year}, je ne trouve aucun objet politique déposé correspondant dans les données structurées."
        )

    total = len(deposits)
    lines = [
        f"En **{year}**, je trouve **{total} dépôt(s)** dans les données structurées:",
        f"- {counts['interpellation']} interpellation(s)",
        f"- {counts['motion']} motion(s)",
        f"- {counts['postulat']} postulat(s)",
        "",
    ]

    for index, item in enumerate(deposits, start=1):
        title = item.get("title", "")
        object_type = object_label(item.get("object_type", ""))
        number = item.get("agenda_item_number", "")
        session_date = item.get("session_date", "")
        pdf_url = item.get("pdf_url", "")
        source = f" [PDF]({pdf_url})" if pdf_url else ""
        prefix = f"{index}. {session_date} - {number} - {object_type}: " if number else f"{index}. {session_date} - {object_type}: "
        lines.append(f"{prefix}{title}{source}")

    source_urls = sorted({item.get("session_source_url", "") for item in deposits if item.get("session_source_url")})
    if source_urls:
        lines.append("")
        lines.append("Sources séances:")
        for source_url in source_urls[:8]:
            lines.append(f"- {source_url}")
    return "\n".join(lines)


def answer_latest_deposits(question: str) -> str | None:
    if not (wants_latest_session(question) and wants_deposit_count(question)):
        return None

    data = load_structured_data()
    session = latest_session(data["sessions"])
    if not session:
        return None

    session_date = session["session_date"]
    deposits = [
        item
        for item in data["political_objects"]
        if item.get("session_date") == session_date
        and item.get("object_type") in DEPOSIT_TYPES
        and item.get("status") == "depot"
    ]
    deposits = sorted(deposits, key=lambda item: item.get("agenda_item_number", ""))
    counts = count_by_type(deposits)

    if not deposits:
        return (
            f"Pour la dernière séance indexée ({session_date}), je ne trouve pas de motion, "
            "postulat ou interpellation déposés dans les données structurées."
        )

    total = len(deposits)
    lines = [
        f"Pour la dernière séance indexée, le **{session_date}**, il y a **{total} dépôt(s)** dans les données structurées:",
        f"- {counts['interpellation']} interpellation(s)",
        f"- {counts['motion']} motion(s)",
        f"- {counts['postulat']} postulat(s)",
        "",
    ]

    for index, item in enumerate(deposits, start=1):
        title = item.get("title", "")
        object_type = object_label(item.get("object_type", ""))
        number = item.get("agenda_item_number", "")
        pdf_url = item.get("pdf_url", "")
        source = f" [PDF]({pdf_url})" if pdf_url else ""
        prefix = f"{index}. {number} - {object_type}: " if number else f"{index}. {object_type}: "
        lines.append(f"{prefix}{title}{source}")

    if session.get("source_url"):
        lines.extend(["", f"Source séance: [ordre du jour du {session_date}]({session['source_url']})"])
    return "\n".join(lines)


def answer_structured_question(question: str) -> str | None:
    return answer_most_deposits(question) or answer_deposits_by_year(question) or answer_latest_deposits(question)
