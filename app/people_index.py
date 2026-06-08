from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config import DOCUMENTS_ROOT
from app.postgres_store import _connect, ensure_schema
from app.text_cleaning import clean_french_text, strip_accents


PERSON_SOURCE_TYPES = {"motion", "postulat", "interpellation"}
PERSON_SOURCE_CATEGORIES = {"motions", "postulats", "interpellations"}
AUTHOR_FIELDS = ("authors", "coauthors")
CIVILITY_RE = re.compile(r"^(mme|m\.?|mm\.?|mmes|monsieur|madame)\s+", re.IGNORECASE)


@dataclass
class PersonAccumulator:
    person_id: str
    canonical_name: str
    normalized_name: str
    city: str = "La Tour-de-Peilz"
    variants: set[str] = field(default_factory=set)
    parties: Counter[str] = field(default_factory=Counter)
    roles: set[str] = field(default_factory=set)
    years: set[str] = field(default_factory=set)
    objects: dict[str, dict[str, Any]] = field(default_factory=dict)


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return None


def clean_value(value: Any) -> Any:
    if isinstance(value, str):
        return clean_french_text(value)
    if isinstance(value, list):
        return [clean_value(item) for item in value]
    if isinstance(value, dict):
        return {key: clean_value(item) for key, item in value.items()}
    return value


def normalize_name(name: str) -> str:
    value = clean_french_text(name)
    value = re.sub(r"\([^)]*\)", " ", value)
    value = CIVILITY_RE.sub("", value.strip())
    value = re.sub(r"\b(et\s+)?consorts?\b", " ", value, flags=re.IGNORECASE)
    value = strip_accents(value).lower()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def person_key(name: str) -> str:
    normalized = normalize_name(name)
    words = []
    for word in normalized.split():
        words.append(word.replace("ae", "a").replace("oe", "o").replace("ue", "u"))
    return " ".join(words)


def alias_keys(name: str) -> set[str]:
    normalized = normalize_name(name)
    keys = {normalized}
    swiss_key = person_key(name)
    if swiss_key:
        keys.add(swiss_key)
    return {key for key in keys if key}


def display_name(name: str) -> str:
    value = clean_french_text(name)
    value = re.sub(r"\([^)]*\)", " ", value)
    value = CIVILITY_RE.sub("", value.strip())
    value = re.sub(r"\b(et\s+)?consorts?\b", " ", value, flags=re.IGNORECASE)
    value = re.sub(r"\s+", " ", value)
    return value.strip(" ,;:-")


def display_score(name: str) -> int:
    return sum(1 for char in name if ord(char) > 127)


def slugify(value: str) -> str:
    slug = strip_accents(value).lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")


def is_person_name(name: str) -> bool:
    normalized = normalize_name(name)
    if not normalized:
        return False
    if normalized.startswith("groupe "):
        return False
    if normalized in {"consorts", "municipalite", "conseil communal", "la tour de peilz libre"}:
        return False
    return len(normalized.split()) >= 2


def split_person_names(name: str) -> list[str]:
    value = clean_french_text(name)
    value = re.sub(r"\b(et\s+)?consorts?\b", "", value, flags=re.IGNORECASE).strip(" ,;")
    if not value:
        return []
    pieces = re.split(r"\s+et\s+|,\s*", value)
    return [piece.strip() for piece in pieces if piece.strip()]


def iter_author_entries(metadata: dict[str, Any]) -> list[tuple[str, str, str]]:
    entries: list[tuple[str, str, str]] = []
    for field_name in AUTHOR_FIELDS:
        raw_entries = metadata.get(field_name) or []
        if isinstance(raw_entries, (str, dict)):
            raw_entries = [raw_entries]
        for raw_entry in raw_entries:
            role = "coauthor" if field_name == "coauthors" else "author"
            if isinstance(raw_entry, dict):
                name = str(raw_entry.get("name") or "").strip()
                party = str(raw_entry.get("party") or "").strip()
                civility = str(raw_entry.get("civility") or "").strip()
                variant = " ".join(part for part in [civility, name] if part).strip()
            else:
                name = str(raw_entry).strip()
                party = ""
                variant = name
            for split_name in split_person_names(name):
                if split_name and is_person_name(split_name):
                    entries.append((split_name, party, role))
            if variant and variant != name:
                for split_variant in split_person_names(variant):
                    if split_variant and is_person_name(split_variant):
                        entries.append((split_variant, party, role))
    return entries


def source_url(metadata: dict[str, Any]) -> str:
    return str(metadata.get("pdf_url") or metadata.get("source_url") or metadata.get("url") or metadata.get("source_page") or "")


def object_record(metadata: dict[str, Any], path: Path, role: str) -> dict[str, Any]:
    political_object = metadata.get("political_object") or {}
    object_id = (
        metadata.get("political_object_id")
        or political_object.get("object_id")
        or f"{metadata.get('type') or metadata.get('category')}-{metadata.get('year')}-{slugify(metadata.get('object_title') or metadata.get('summary') or metadata.get('title') or path.stem)}"
    )
    return {
        "role": role,
        "object_id": str(object_id),
        "object_type": str(metadata.get("type") or political_object.get("type") or metadata.get("document_type") or ""),
        "object_title": str(metadata.get("object_title") or political_object.get("object_title") or metadata.get("summary") or ""),
        "status": str(metadata.get("status_normalized") or political_object.get("status_normalized") or metadata.get("status") or ""),
        "year": str(metadata.get("year") or metadata.get("listing_year") or ""),
        "document_date": str(metadata.get("document_date") or political_object.get("document_date") or "")[:10],
        "document_title": str(metadata.get("title") or ""),
        "document_role": str(metadata.get("document_role") or ""),
        "source_url": source_url(metadata),
        "source_path": str(path),
        "canonical_object": bool(metadata.get("canonical_object")),
    }


def should_use_metadata(metadata: dict[str, Any]) -> bool:
    doc_type = str(metadata.get("type") or metadata.get("document_type") or "").lower()
    category = str(metadata.get("category") or "").lower()
    return doc_type in PERSON_SOURCE_TYPES or category in PERSON_SOURCE_CATEGORIES


def build_people(documents_root: Path = DOCUMENTS_ROOT) -> dict[str, PersonAccumulator]:
    people: dict[str, PersonAccumulator] = {}
    alias_to_person_id: dict[str, str] = {}
    for path in sorted(documents_root.rglob("*.json")):
        metadata = read_json(path)
        if not metadata:
            continue
        metadata = clean_value(metadata)
        if not should_use_metadata(metadata):
            continue
        entries = iter_author_entries(metadata)
        if not entries:
            continue

        for raw_name, party, role in entries:
            canonical_name = display_name(raw_name)
            normalized_name = normalize_name(canonical_name)
            if not normalized_name:
                continue
            aliases = alias_keys(canonical_name)
            person_id = next((alias_to_person_id[key] for key in aliases if key in alias_to_person_id), "")
            if not person_id:
                person_id = slugify(normalized_name)
            if not person_id:
                continue
            person = people.get(person_id)
            if person is None:
                person = PersonAccumulator(
                    person_id=person_id,
                    canonical_name=canonical_name,
                    normalized_name=normalized_name,
                    city=str(metadata.get("commune") or "La Tour-de-Peilz"),
                )
                people[person_id] = person
            for alias in aliases:
                alias_to_person_id[alias] = person_id

            if display_score(canonical_name) > display_score(person.canonical_name):
                person.canonical_name = canonical_name
            person.variants.add(canonical_name)
            person.variants.add(clean_french_text(raw_name))
            if party:
                person.parties[party] += 1
            person.roles.add(role)
            year = str(metadata.get("year") or metadata.get("listing_year") or "")
            if year:
                person.years.add(year)
            record = object_record(metadata, path, role)
            object_key = f"{record['object_id']}#{record['source_url'] or record['source_path']}#{role}"
            person.objects[object_key] = record
    return people


def people_rows(people: dict[str, PersonAccumulator]) -> list[dict[str, Any]]:
    rows = []
    for person in sorted(people.values(), key=lambda item: item.canonical_name):
        parties = sorted(person.parties)
        party_current = person.parties.most_common(1)[0][0] if person.parties else ""
        objects = sorted(
            person.objects.values(),
            key=lambda item: (item.get("year", ""), item.get("object_type", ""), item.get("object_title", ""), item.get("source_url", "")),
        )
        rows.append(
            {
                "person_id": person.person_id,
                "city": person.city,
                "canonical_name": person.canonical_name,
                "normalized_name": person.normalized_name,
                "party_current": party_current,
                "parties": parties,
                "variants": sorted(value for value in person.variants if value),
                "roles": sorted(person.roles),
                "years": sorted(person.years),
                "objects": objects,
                "metadata": {
                    "party_counts": dict(person.parties),
                    "object_count": len(objects),
                    "canonical_source": "documents_metadata",
                },
            }
        )
    return rows


def upsert_people(rows: list[dict[str, Any]]) -> None:
    ensure_schema()
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM people")
            cursor.executemany(
                """
                INSERT INTO people (
                    person_id, city, canonical_name, normalized_name, party_current,
                    parties, variants, roles, years, objects, metadata
                )
                VALUES (
                    %(person_id)s, %(city)s, %(canonical_name)s, %(normalized_name)s, %(party_current)s,
                    %(parties)s::jsonb, %(variants)s::jsonb, %(roles)s::jsonb, %(years)s::jsonb,
                    %(objects)s::jsonb, %(metadata)s::jsonb
                )
                ON CONFLICT (person_id) DO UPDATE
                SET
                    city = EXCLUDED.city,
                    canonical_name = EXCLUDED.canonical_name,
                    normalized_name = EXCLUDED.normalized_name,
                    party_current = EXCLUDED.party_current,
                    parties = EXCLUDED.parties,
                    variants = EXCLUDED.variants,
                    roles = EXCLUDED.roles,
                    years = EXCLUDED.years,
                    objects = EXCLUDED.objects,
                    metadata = EXCLUDED.metadata,
                    updated_at = NOW()
                """,
                [
                    {
                        **row,
                        "parties": json.dumps(row["parties"], ensure_ascii=False),
                        "variants": json.dumps(row["variants"], ensure_ascii=False),
                        "roles": json.dumps(row["roles"], ensure_ascii=False),
                        "years": json.dumps(row["years"], ensure_ascii=False),
                        "objects": json.dumps(row["objects"], ensure_ascii=False),
                        "metadata": json.dumps(row["metadata"], ensure_ascii=False),
                    }
                    for row in rows
                ],
            )
        connection.commit()


def rebuild_people_index(documents_root: Path = DOCUMENTS_ROOT) -> dict[str, Any]:
    people = build_people(documents_root)
    rows = people_rows(people)
    upsert_people(rows)
    return {
        "people": len(rows),
        "objects_linked": sum(len(row["objects"]) for row in rows),
        "parties": sorted({party for row in rows for party in row["parties"]}),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the people index from document metadata.")
    parser.add_argument("--documents-root", type=Path, default=DOCUMENTS_ROOT)
    args = parser.parse_args()
    stats = rebuild_people_index(args.documents_root)
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
