import json
import re
from collections import Counter
from functools import lru_cache
from typing import Any

from app.config import STRUCTURED_DATA_DIR
from app.text_cleaning import strip_accents


DEPOSIT_TYPES = {"motion", "postulat", "interpellation"}
DEPOSIT_CATEGORY_BY_TYPE = {
    "motion": "motions",
    "postulat": "postulats",
    "interpellation": "interpellations",
}
DEPOSIT_PREFIX_BY_TYPE = {
    "motion": "Motion",
    "postulat": "Postulat",
    "interpellation": "Interpellation",
}
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


def infer_status_from_document(title: str, filename: str) -> str:
    haystack = normalize(f"{title} {filename}")
    if "renvoye directement" in haystack or "renvoye a la municipalite" in haystack:
        return "renvoye_municipalite"
    if "retire par le postulant" in haystack or "retiree par le postulant" in haystack:
        return "retire"
    if "+ reponse" in haystack or (
        filename.lower().startswith(("interpellation-", "motion-", "postulat-")) and "-rep" in filename.lower()
    ):
        return "depot_avec_reponse"
    return "depot"


def extract_author_party_pairs(title: str) -> list[dict[str, str]]:
    title = str(title or "")
    if not title:
        return []

    intro = re.split(r"\s+[–—-]\s+|\s+«", title, maxsplit=1)[0]
    intro = re.sub(r"^(Motion|Postulat|Interpellation)\s+(de|du|des)\s+", "", intro, flags=re.IGNORECASE).strip()
    intro = re.sub(r"\s+\+\s+R[ée]ponse.*$", "", intro, flags=re.IGNORECASE).strip()
    matches = re.findall(
        r"\b(?:Mme|M\.|MM\.|Mmes)\s+([^()]+?)\s*\(([A-ZÀ-Ÿ0-9/-]{2,})\)",
        intro,
        flags=re.IGNORECASE,
    )
    pairs = []
    for raw_name, party in matches:
        name = display_name(raw_name)
        if name:
            pairs.append({"name": name, "party": party})
    if pairs:
        return pairs

    group_match = re.search(r"\bgroupe\s+([A-ZÀ-Ÿ0-9/-]{2,})\b", intro, flags=re.IGNORECASE)
    if group_match:
        party = group_match.group(1)
        return [{"name": f"groupe {party}", "party": party}]
    return []


def authors_from_title(title: str) -> list[str]:
    return [pair["name"] for pair in extract_author_party_pairs(title)]


def party_from_title(title: str) -> str | None:
    pairs = extract_author_party_pairs(title)
    parties = [pair["party"] for pair in pairs if pair.get("party")]
    return parties[0] if parties else None


def search_deposit_documents_from_postgres(year: str, object_types: set[str]) -> list[dict[str, Any]]:
    try:
        from app.postgres_store import _connect
    except Exception:
        return []

    rows = []
    try:
        with _connect() as connection:
            with connection.cursor() as cursor:
                for object_type in sorted(object_types):
                    category = DEPOSIT_CATEGORY_BY_TYPE.get(object_type)
                    prefix = DEPOSIT_PREFIX_BY_TYPE.get(object_type)
                    if not category or not prefix:
                        continue
                    cursor.execute(
                        """
                        SELECT title, source_url, source_path, doc_type, metadata
                        FROM documents
                        WHERE (metadata->>'year' = %s OR source_path LIKE %s)
                          AND doc_type = %s
                          AND (title ILIKE %s OR metadata->>'filename' ILIKE %s)
                          AND COALESCE(metadata->>'filename', '') NOT ILIKE 'Reponse-%%'
                          AND COALESCE(metadata->>'filename', '') NOT ILIKE '%%-Rapp%%'
                        ORDER BY title, source_url
                        """,
                        (year, f"%/{year}/%", category, f"{prefix}%", f"{prefix}%"),
                    )
                    rows.extend(cursor.fetchall())
    except Exception:
        return []

    deposits = []
    seen = set()
    for row in rows:
        metadata = row.get("metadata") or {}
        if not isinstance(metadata, dict):
            metadata = {}
        filename = str(metadata.get("filename") or row.get("source_path") or "")
        title = str(row.get("title") or metadata.get("title") or filename)
        source_url = str(row.get("source_url") or metadata.get("pdf_url") or "")
        key = source_url or filename or title
        if not key or key in seen:
            continue
        seen.add(key)

        object_type = next(
            (
                candidate
                for candidate, prefix in DEPOSIT_PREFIX_BY_TYPE.items()
                if title.lower().startswith(prefix.lower()) or filename.lower().startswith(prefix.lower())
            ),
            "",
        )
        if object_type not in object_types:
            continue

        pairs = extract_author_party_pairs(title)
        deposits.append(
            {
                "commune": metadata.get("commune", "La Tour-de-Peilz"),
                "session_date": metadata.get("session_date", ""),
                "agenda_item_number": "",
                "object_type": object_type,
                "status": infer_status_from_document(title, filename),
                "title": title,
                "document_title": title,
                "filename": filename,
                "pdf_url": source_url,
                "category": row.get("doc_type") or metadata.get("category", ""),
                "year": year,
                "authors": [pair["name"] for pair in pairs],
                "party": pairs[0]["party"] if pairs else None,
            }
        )
    return sorted(deposits, key=lambda item: (item.get("object_type", ""), item.get("title", ""), item.get("filename", "")))


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
    title_authors = authors_from_title(str(item.get("title") or item.get("document_title") or ""))
    if title_authors:
        return title_authors

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
    postgres_deposits = search_deposit_documents_from_postgres(year, object_types)
    deposits = postgres_deposits or [
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
        authors = item.get("authors") or authors_from_title(title)
        party = item.get("party") or party_from_title(title)
        status = item.get("status", "")
        source = f" [PDF]({pdf_url})" if pdf_url else ""
        if session_date and number:
            prefix = f"{index}. {session_date} - {number} - {object_type}: "
        elif session_date:
            prefix = f"{index}. {session_date} - {object_type}: "
        else:
            prefix = f"{index}. {object_type}: "
        details = []
        if authors:
            details.append(f"depose par {', '.join(authors)}")
        if party:
            details.append(party)
        if status and status != "depot":
            details.append(status.replace("_", " "))
        detail_text = f" ({'; '.join(details)})" if details else ""
        lines.append(f"{prefix}{title}{detail_text}{source}")

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
