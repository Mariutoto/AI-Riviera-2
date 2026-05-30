import json
import re
import sys
from collections import Counter
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.text_cleaning import clean_french_text


SESSIONS_ROOT = PROJECT_ROOT / "data" / "sessions" / "la-tour-de-peilz"
OUTPUT_ROOT = PROJECT_ROOT / "data" / "structured" / "la-tour-de-peilz"


POLITICAL_TYPES = [
    ("interpellation", "interpellation"),
    ("postulat", "postulat"),
    ("motion", "motion"),
    ("preavis", "preavis"),
    ("préavis", "preavis"),
    ("communication", "communication"),
    ("rapport", "rapport"),
    ("réponse", "reponse"),
    ("reponse", "reponse"),
]


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def clean_value(value):
    if isinstance(value, str):
        return clean_french_text(value)
    if isinstance(value, list):
        return [clean_value(item) for item in value]
    if isinstance(value, dict):
        return {key: clean_value(item) for key, item in value.items()}
    return value


def session_paths() -> list[Path]:
    return sorted(
        path
        for path in SESSIONS_ROOT.rglob("*.json")
        if path.name.startswith("20") and path.parent.name.startswith("20")
    )


def infer_object_type(title: str, category: str = "") -> str:
    title_haystack = title.lower()
    for marker, object_type in POLITICAL_TYPES:
        if marker in title_haystack:
            return object_type

    category_haystack = category.lower()
    for marker, object_type in POLITICAL_TYPES:
        if marker in category_haystack and category_haystack != "motions-postulats":
            return object_type

    if category_haystack == "motions-postulats":
        return "objet_politique"
    return "objet"


def is_deposit_agenda_item(number: str, object_type: str) -> bool:
    return number.startswith("7.") and object_type in {"motion", "postulat", "interpellation"}


def is_structured_object(object_type: str) -> bool:
    return object_type in {"motion", "postulat", "interpellation", "preavis", "communication", "rapport", "reponse"}


def object_status(number: str, object_type: str) -> str:
    if is_deposit_agenda_item(number, object_type):
        return "depot"
    if object_type == "reponse":
        return "reponse"
    if object_type == "rapport":
        return "rapport"
    return "traite"


def extract_party(title: str) -> str | None:
    matches = re.findall(r"\(([A-ZÀ-Ÿ0-9/-]{2,})\)", title)
    return matches[0] if matches else None


def extract_authors(title: str) -> list[str]:
    before_dash = re.split(r"\s[-–]\s", title, maxsplit=1)[0]
    before_dash = re.sub(
        r"^(Motion|Postulat|Interpellation|Réponse municipale|Reponse municipale|Préavis municipal|Preavis municipal)\s+(de|du|des|municipal)?\s*",
        "",
        before_dash,
        flags=re.I,
    ).strip()
    before_dash = re.sub(r"\([A-ZÀ-Ÿ0-9/-]{2,}\)", "", before_dash).strip()
    before_dash = re.sub(r"\b(consorts|et consorts)\b", "", before_dash, flags=re.I).strip()
    if not before_dash or re.search(r"N[°º]\s*\d", before_dash):
        return []
    parts = re.split(r"\s+et\s+|,\s*", before_dash)
    return [part.strip() for part in parts if part.strip()]


def flatten_document(item: dict, session: dict, linked_document: dict) -> dict:
    local = linked_document.get("local_document", {}) or {}
    title = linked_document.get("title") or item.get("title", "")
    category = local.get("category", "")
    return {
        "commune": session.get("commune", "La Tour-de-Peilz"),
        "session_date": session.get("session_date", ""),
        "agenda_item_number": item.get("number", ""),
        "agenda_item_title": item.get("title", ""),
        "title": title,
        "filename": linked_document.get("filename", ""),
        "category": category,
        "year": local.get("year", ""),
        "pdf_url": linked_document.get("pdf_url", ""),
        "metadata_path": local.get("metadata_path", ""),
        "text_path": local.get("text_path", ""),
        "object_type": infer_object_type(title or item.get("title", ""), category),
    }


def build() -> dict:
    sessions = []
    documents = []
    political_objects = []
    seen_documents = set()

    for path in session_paths():
        session = clean_value(read_json(path))
        agenda_items = session.get("agenda_items", [])
        linked_documents = session.get("linked_documents", [])

        session_record = {
            "commune": session.get("commune", "La Tour-de-Peilz"),
            "session_date": session.get("session_date", ""),
            "label": session.get("label", ""),
            "time": session.get("time", ""),
            "place": session.get("place", ""),
            "source_url": session.get("source_url", ""),
            "agenda_items_count": len(agenda_items),
            "linked_documents_count": len(linked_documents),
            "data_path": str(path.relative_to(PROJECT_ROOT)),
        }
        sessions.append(session_record)

        for item in agenda_items:
            item_title = item.get("title", "")
            item_number = item.get("number", "")
            item_docs = item.get("linked_documents", [])
            item_type = infer_object_type(item_title)
            for linked_document in item_docs:
                document = flatten_document(item, session, linked_document)
                document_key = document["pdf_url"] or f"{session_record['session_date']}#{item_number}#{document['filename']}"
                if document_key not in seen_documents:
                    seen_documents.add(document_key)
                    documents.append(document)

                object_type = infer_object_type(item_title, document.get("category", ""))
                if is_structured_object(object_type):
                    political_objects.append(
                        {
                            "commune": session_record["commune"],
                            "session_date": session_record["session_date"],
                            "session_label": session_record["label"],
                            "session_source_url": session_record["source_url"],
                            "agenda_item_number": item_number,
                            "object_type": object_type,
                            "status": object_status(item_number, object_type),
                            "title": item_title,
                            "document_title": document["title"],
                            "filename": document["filename"],
                            "pdf_url": document["pdf_url"],
                            "category": document["category"],
                            "year": document["year"],
                            "authors": extract_authors(item_title),
                            "party": extract_party(item_title),
                        }
                    )

            if not item_docs and is_structured_object(item_type):
                political_objects.append(
                    {
                        "commune": session_record["commune"],
                        "session_date": session_record["session_date"],
                        "session_label": session_record["label"],
                        "session_source_url": session_record["source_url"],
                        "agenda_item_number": item_number,
                        "object_type": item_type,
                        "status": object_status(item_number, item_type),
                        "title": item_title,
                        "document_title": "",
                        "filename": "",
                        "pdf_url": "",
                        "category": "",
                        "year": session_record["session_date"][:4],
                        "authors": extract_authors(item_title),
                        "party": extract_party(item_title),
                    }
                )

    sessions = sorted(sessions, key=lambda item: item["session_date"])
    documents = sorted(documents, key=lambda item: (item["session_date"], item["agenda_item_number"], item["filename"]))
    political_objects = sorted(
        political_objects,
        key=lambda item: (item["session_date"], item["agenda_item_number"], item["object_type"], item["filename"]),
    )

    stats = {
        "sessions": len(sessions),
        "documents": len(documents),
        "political_objects": len(political_objects),
        "political_objects_by_type": dict(Counter(item["object_type"] for item in political_objects)),
        "first_session": sessions[0]["session_date"] if sessions else "",
        "last_session": sessions[-1]["session_date"] if sessions else "",
    }

    write_json(OUTPUT_ROOT / "sessions.json", sessions)
    write_json(OUTPUT_ROOT / "documents.json", documents)
    write_json(OUTPUT_ROOT / "political_objects.json", political_objects)
    write_json(OUTPUT_ROOT / "stats.json", stats)
    return stats


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(build(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
