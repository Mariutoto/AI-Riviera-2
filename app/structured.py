import json
import re
from functools import lru_cache

from app.config import STRUCTURED_DATA_DIR
from app.text_cleaning import strip_accents


DEPOSIT_TYPES = {"motion", "postulat", "interpellation"}


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
    return answer_latest_deposits(question)
