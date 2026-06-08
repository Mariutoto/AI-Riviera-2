from __future__ import annotations

import argparse
import json
from typing import Any

from app.political_objects_index import rebuild_political_objects_index
from app.postgres_store import _connect, ensure_schema


def load_people_lookup() -> tuple[dict[str, str], set[str], dict[str, str]]:
    aliases: dict[str, str] = {}
    person_ids: set[str] = set()
    party_by_person_id: dict[str, str] = {}
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT person_id, normalized_name, party_current, variants FROM people")
            for row in cursor.fetchall():
                person_id = str(row["person_id"])
                person_ids.add(person_id)
                party_by_person_id[person_id] = str(row["party_current"] or "")
                normalized_name = str(row["normalized_name"] or "")
                if normalized_name:
                    aliases[normalized_name] = person_id
                for variant in row["variants"] or []:
                    variant_text = str(variant or "")
                    if variant_text:
                        from app.people_index import normalize_name, person_key

                        aliases[normalize_name(variant_text)] = person_id
                        aliases[person_key(variant_text)] = person_id
    return aliases, person_ids, party_by_person_id


def load_political_objects() -> list[dict[str, Any]]:
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT object_id, object_type, object_title, year, authors
                FROM political_objects
                ORDER BY year, object_type, object_title, object_id
                """
            )
            return list(cursor.fetchall())


def resolve_person_id(author: dict[str, Any], aliases: dict[str, str], person_ids: set[str]) -> str:
    person_id = str(author.get("person_id") or "")
    if person_id in person_ids:
        return person_id

    normalized_name = str(author.get("normalized_name") or "")
    if normalized_name in aliases:
        return aliases[normalized_name]

    from app.people_index import normalize_name, person_key

    name = str(author.get("name") or "")
    for key in [normalize_name(name), person_key(name)]:
        if key in aliases:
            return aliases[key]
    return person_id


def relation_rows() -> list[dict[str, Any]]:
    aliases, person_ids, party_by_person_id = load_people_lookup()
    rows: list[dict[str, Any]] = []
    for political_object in load_political_objects():
        authors = political_object.get("authors") or []
        if not isinstance(authors, list):
            continue
        for index, author in enumerate(authors):
            if not isinstance(author, dict):
                continue
            person_id = resolve_person_id(author, aliases, person_ids)
            if not person_id or person_id not in person_ids:
                continue
            parties = author.get("parties") or []
            roles = author.get("roles") or ["author"]
            if isinstance(roles, str):
                roles = [roles]
            party_at_time = str(parties[0]) if parties else party_by_person_id.get(person_id, "")
            for role in roles:
                role_text = str(role or "author")
                rows.append(
                    {
                        "object_id": str(political_object["object_id"]),
                        "person_id": person_id,
                        "role": role_text,
                        "party_at_time": party_at_time,
                        "order_index": index,
                        "metadata": {
                            "author_name": author.get("name", ""),
                            "author_parties": parties,
                            "author_variants": author.get("variants") or [],
                            "object_type": political_object.get("object_type", ""),
                            "object_title": political_object.get("object_title", ""),
                            "year": political_object.get("year", ""),
                            "canonical_source": "political_objects.authors",
                        },
                    }
                )
    return rows


def upsert_relations(rows: list[dict[str, Any]]) -> None:
    ensure_schema()
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM political_object_people")
            cursor.executemany(
                """
                INSERT INTO political_object_people (
                    object_id, person_id, role, party_at_time, order_index, metadata
                )
                VALUES (
                    %(object_id)s, %(person_id)s, %(role)s, %(party_at_time)s, %(order_index)s, %(metadata)s::jsonb
                )
                ON CONFLICT (object_id, person_id, role) DO UPDATE
                SET
                    party_at_time = EXCLUDED.party_at_time,
                    order_index = EXCLUDED.order_index,
                    metadata = EXCLUDED.metadata,
                    updated_at = NOW()
                """,
                [
                    {
                        **row,
                        "metadata": json.dumps(row["metadata"], ensure_ascii=False),
                    }
                    for row in rows
                ],
            )
        connection.commit()


def rebuild_political_object_people_index(rebuild_objects: bool = False) -> dict[str, Any]:
    ensure_schema()
    if rebuild_objects:
        rebuild_political_objects_index()
    rows = relation_rows()
    upsert_relations(rows)
    return {
        "political_object_people": len(rows),
        "roles": sorted({row["role"] for row in rows}),
        "parties": sorted({row["party_at_time"] for row in rows if row["party_at_time"]}),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build political object/person relations from political_objects.")
    parser.add_argument("--rebuild-objects", action="store_true", help="Rebuild political_objects before relations.")
    args = parser.parse_args()
    stats = rebuild_political_object_people_index(rebuild_objects=args.rebuild_objects)
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
