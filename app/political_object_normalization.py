from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import date
from typing import Any

from app.postgres_store import _connect, ensure_schema
from app.text_cleaning import strip_accents


@dataclass
class NormalizedPoliticalObject:
    object_id: str
    status_stage: str
    status_is_final: bool
    status_decision: str
    deposit_date: str | None
    referral_date: str | None
    commission_date: str | None
    report_date: str | None
    decision_date: str | None
    response_date: str | None
    last_event_date: str | None
    date_confidence: str
    date_sources: dict[str, Any]


def normalize_text(value: str) -> str:
    return strip_accents(value or "").lower().strip()


def date_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if len(text) >= 10 and text[:4].isdigit() and text[4] == "-" and text[7] == "-":
        return text[:10]
    return None


def min_date(values: list[str | None]) -> str | None:
    dates = [value for value in values if value]
    return min(dates) if dates else None


def max_date(values: list[str | None]) -> str | None:
    dates = [value for value in values if value]
    return max(dates) if dates else None


def normalize_status(row: dict[str, Any]) -> tuple[str, bool, str]:
    object_type = normalize_text(str(row.get("object_type") or ""))
    status_normalized = normalize_text(str(row.get("status_normalized") or ""))
    status_raw = normalize_text(str(row.get("status_raw") or ""))
    metadata = row.get("metadata") or {}
    documents = row.get("documents") or []
    haystack = " ".join([status_normalized, status_raw])

    contains_decision = bool(metadata.get("contains_decision")) or any(doc.get("contains_decision") for doc in documents)
    contains_response = bool(metadata.get("contains_response")) or any(doc.get("contains_response") for doc in documents)
    contains_report = bool(metadata.get("contains_report")) or any(doc.get("contains_report") for doc in documents)

    if any(term in haystack for term in ["retire", "withdrawn"]):
        return "closed", True, "withdrawn"
    if any(term in haystack for term in ["not_supported", "non soutenu", "non soutenue"]):
        return "closed", True, "not_supported"
    if "refused" in haystack or "refuse" in haystack:
        return "closed", True, "refused"
    if "accepted" in haystack or "accepte" in haystack or "adopte" in haystack:
        return "closed", True, "accepted"
    if contains_decision or "decision" in haystack:
        return "decided", True, "decision_available"
    if contains_response or "response" in haystack or "reponse" in haystack:
        return "answered", True, "response_available"
    if "referred" in haystack or "renvoye" in haystack or "municipalite" in haystack:
        return "referred", False, "pending_municipality"
    if contains_report or "report" in haystack or "rapport" in haystack:
        return "reported", False, "report_available"
    if object_type == "interpellation":
        return "submitted", False, "awaiting_response"
    return "submitted", False, "pending"


def scheduled_deposit_date(row: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    sessions = row.get("scheduled_sessions") or []
    candidates = []
    for session in sessions:
        number = str(session.get("agenda_item_number") or "")
        session_date = date_text(session.get("session_date"))
        if session_date and number.startswith("7."):
            candidates.append((session_date, session))
    if not candidates:
        return None, None
    candidates.sort(key=lambda item: item[0])
    return candidates[0]


def first_session_date(row: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    sessions = row.get("scheduled_sessions") or []
    candidates = []
    for session in sessions:
        session_date = date_text(session.get("session_date"))
        if session_date:
            candidates.append((session_date, session))
    if not candidates:
        return None, None
    candidates.sort(key=lambda item: item[0])
    return candidates[0]


def latest_decision_session(row: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    sessions = row.get("scheduled_sessions") or []
    candidates = []
    for session in sessions:
        number = str(session.get("agenda_item_number") or "")
        title = normalize_text(str(session.get("agenda_item_title") or ""))
        session_date = date_text(session.get("session_date"))
        if not session_date:
            continue
        if not number.startswith("7.") or "rapport" in title or "decision" in title or "prise en consideration" in title:
            candidates.append((session_date, session))
    if not candidates:
        return None, None
    candidates.sort(key=lambda item: item[0])
    return candidates[-1]


def document_dates(row: dict[str, Any]) -> list[str]:
    return [date for date in (date_text(doc.get("document_date")) for doc in row.get("documents") or []) if date]


def canonical_document_date(row: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    for document in row.get("documents") or []:
        if document.get("canonical_object"):
            value = date_text(document.get("document_date"))
            if value:
                return value, document
    return None, None


def relation_date(row: dict[str, Any], *needles: str) -> tuple[str | None, dict[str, Any] | None]:
    matches = []
    for document in row.get("documents") or []:
        text = normalize_text(
            " ".join(
                [
                    str(document.get("document_role") or ""),
                    str(document.get("filename") or ""),
                    str(document.get("title") or ""),
                ]
            )
        )
        if any(needle in text for needle in needles):
            value = date_text(document.get("document_date"))
            if value:
                matches.append((value, document))
    if not matches:
        return None, None
    matches.sort(key=lambda item: item[0])
    return matches[-1]


def commission_meeting_date(row: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    matches = []
    for document in row.get("documents") or []:
        commission = document.get("commission") or {}
        meeting = commission.get("meeting") or {}
        value = date_text(meeting.get("date"))
        if value:
            matches.append((value, document))
    if not matches:
        return None, None
    matches.sort(key=lambda item: item[0])
    return matches[-1]


def response_document_date(row: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    matches = []
    for document in row.get("documents") or []:
        if not document.get("contains_response"):
            continue
        value = date_text(document.get("document_date"))
        if value:
            matches.append((value, document))
    if not matches:
        return None, None
    matches.sort(key=lambda item: item[0])
    return matches[-1]


def normalize_dates(row: dict[str, Any], status_stage: str) -> tuple[dict[str, str | None], str, dict[str, Any]]:
    sources: dict[str, Any] = {}

    deposit, source = scheduled_deposit_date(row)
    if deposit:
        sources["deposit_date"] = {"source": "scheduled_in_sessions", "value": source}
        confidence = "exact"
    else:
        deposit = date_text(row.get("deposit_date"))
        if deposit:
            sources["deposit_date"] = {"source": "political_objects.deposit_date"}
            confidence = "document_or_existing"
        else:
            deposit, source = first_session_date(row)
            if deposit:
                sources["deposit_date"] = {"source": "first_scheduled_session", "value": source}
                confidence = "inferred"
            else:
                deposit, source = canonical_document_date(row)
                if deposit:
                    sources["deposit_date"] = {"source": "canonical_document_date", "value": source}
                    confidence = "document_only"
                else:
                    deposit = min_date(document_dates(row))
                    if deposit:
                        sources["deposit_date"] = {"source": "earliest_document_date"}
                        confidence = "document_only"
                    else:
                        confidence = "unknown"

    referral = deposit if status_stage == "referred" else None
    if referral:
        sources["referral_date"] = {"source": "status_stage_referred_from_deposit_date"}

    commission, source = commission_meeting_date(row)
    if commission:
        sources["commission_date"] = {"source": "commission.meeting.date", "value": source}
    if not commission:
        commission, source = relation_date(row, "commission")
        if commission:
            sources["commission_date"] = {"source": "document_relation", "value": source}

    report, source = relation_date(row, "rapport", "report")
    if report:
        sources["report_date"] = {"source": "document_relation", "value": source}

    decision = date_text(row.get("decision_date"))
    if decision:
        sources["decision_date"] = {"source": "political_objects.decision_date"}
    if not decision:
        decision, source = latest_decision_session(row)
        if decision:
            sources["decision_date"] = {"source": "scheduled_decision_session", "value": source}
    if not decision:
        decision, source = relation_date(row, "decision", "decision", "dec")
        if decision:
            sources["decision_date"] = {"source": "document_relation", "value": source}

    response, source = response_document_date(row)
    if response:
        sources["response_date"] = {"source": "contains_response_document", "value": source}

    last_event = max_date([deposit, referral, commission, report, decision, response, *document_dates(row)])
    if last_event:
        sources["last_event_date"] = {"source": "max_known_date"}

    return (
        {
            "deposit_date": deposit,
            "referral_date": referral,
            "commission_date": commission,
            "report_date": report,
            "decision_date": decision,
            "response_date": response,
            "last_event_date": last_event,
        },
        confidence,
        sources,
    )


def load_political_objects() -> list[dict[str, Any]]:
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    object_id, object_type, status_raw, status_normalized,
                    deposit_date, decision_date, documents, scheduled_sessions, metadata
                FROM political_objects
                ORDER BY year, object_type, object_title, object_id
                """
            )
            return list(cursor.fetchall())


def normalize_row(row: dict[str, Any]) -> NormalizedPoliticalObject:
    status_stage, status_is_final, status_decision = normalize_status(row)
    dates, confidence, sources = normalize_dates(row, status_stage)
    return NormalizedPoliticalObject(
        object_id=str(row["object_id"]),
        status_stage=status_stage,
        status_is_final=status_is_final,
        status_decision=status_decision,
        deposit_date=dates["deposit_date"],
        referral_date=dates["referral_date"],
        commission_date=dates["commission_date"],
        report_date=dates["report_date"],
        decision_date=dates["decision_date"],
        response_date=dates["response_date"],
        last_event_date=dates["last_event_date"],
        date_confidence=confidence,
        date_sources=sources,
    )


def upsert_normalization(rows: list[NormalizedPoliticalObject]) -> None:
    ensure_schema()
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.executemany(
                """
                UPDATE political_objects
                SET
                    status_stage = %(status_stage)s,
                    status_is_final = %(status_is_final)s,
                    status_decision = %(status_decision)s,
                    deposit_date = %(deposit_date)s,
                    referral_date = %(referral_date)s,
                    commission_date = %(commission_date)s,
                    report_date = %(report_date)s,
                    decision_date = %(decision_date)s,
                    response_date = %(response_date)s,
                    last_event_date = %(last_event_date)s,
                    date_confidence = %(date_confidence)s,
                    date_sources = %(date_sources)s::jsonb,
                    updated_at = NOW()
                WHERE object_id = %(object_id)s
                """,
                [
                    {
                        **row.__dict__,
                        "date_sources": json.dumps(row.date_sources, ensure_ascii=False),
                    }
                    for row in rows
                ],
            )
        connection.commit()


def rebuild_political_object_normalization() -> dict[str, Any]:
    ensure_schema()
    rows = [normalize_row(row) for row in load_political_objects()]
    upsert_normalization(rows)
    return {
        "political_objects_normalized": len(rows),
        "status_stages": {
            stage: sum(1 for row in rows if row.status_stage == stage)
            for stage in sorted({row.status_stage for row in rows})
        },
        "status_decisions": {
            decision: sum(1 for row in rows if row.status_decision == decision)
            for decision in sorted({row.status_decision for row in rows})
        },
        "date_confidence": {
            confidence: sum(1 for row in rows if row.date_confidence == confidence)
            for confidence in sorted({row.date_confidence for row in rows})
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Normalize political object status and dates.")
    parser.parse_args()
    stats = rebuild_political_object_normalization()
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
