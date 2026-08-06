from __future__ import annotations

import logging
import os
from collections import deque
from datetime import datetime, timezone
from typing import Any


_MAX_EVENTS = 80
_EVENTS: deque[dict[str, Any]] = deque(maxlen=_MAX_EVENTS)
_INTERACTIONS: deque[dict[str, Any]] = deque(maxlen=120)

# Same default/lookup pattern as app.pilot_v2_store.POSTGRES_V2_URL — read
# directly from the env instead of importing pilot_v2_store, since that
# module already imports record_diagnostic from here (would be circular).
_POSTGRES_V2_URL = os.getenv(
    "POSTGRES_V2_URL",
    "postgresql://pilot:pilot_local_only@127.0.0.1:55432/ai_riviera_embedding_pilot",
)


def _connect():
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(_POSTGRES_V2_URL, row_factory=dict_row)


def _ensure_table(cursor) -> None:
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS app_diagnostics (
            id BIGSERIAL PRIMARY KEY,
            area TEXT NOT NULL,
            message TEXT NOT NULL,
            error TEXT,
            context JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )


def _persist_diagnostic(event: dict[str, Any]) -> None:
    """Best-effort write to Postgres so an error survives process restarts
    and is visible across every Render instance, not just the one that hit
    it — the in-memory deque below resets on every redeploy/instance swap
    and never crosses instances. Never raises."""
    try:
        import json

        with _connect() as connection, connection.cursor() as cursor:
            _ensure_table(cursor)
            cursor.execute(
                "INSERT INTO app_diagnostics (area, message, error, context) VALUES (%s, %s, %s, %s)",
                (
                    event["area"],
                    event["message"],
                    event["error"] or None,
                    json.dumps(event["context"]),
                ),
            )
            connection.commit()
    except Exception:
        # A broken diagnostics write must never break the request it's
        # trying to explain.
        pass


def record_diagnostic(area: str, message: str, exc: Exception | None = None, **context: Any) -> None:
    logger = logging.getLogger(f"ai_riviera.{area}")
    details = {key: value for key, value in context.items() if value is not None}
    event = {
        "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "area": area,
        "message": message,
        "error": repr(exc) if exc else "",
        "context": details,
    }
    _EVENTS.append(event)
    if exc:
        logger.warning("%s | context=%s", message, details, exc_info=True)
    else:
        logger.warning("%s | context=%s", message, details)
    _persist_diagnostic(event)


def recent_diagnostics(limit: int = 20) -> list[dict[str, Any]]:
    return list(_EVENTS)[-limit:]


def fetch_persisted_diagnostics(limit: int = 20) -> list[dict[str, Any]]:
    """Read the durable, cross-instance error log from Postgres (used for
    troubleshooting from outside the running app — e.g. a one-off query —
    rather than the in-process, single-instance `recent_diagnostics`)."""
    try:
        with _connect() as connection, connection.cursor() as cursor:
            _ensure_table(cursor)
            connection.commit()
            cursor.execute(
                "SELECT area, message, error, context, created_at "
                "FROM app_diagnostics ORDER BY created_at DESC LIMIT %s",
                (limit,),
            )
            return cursor.fetchall()
    except Exception:
        return []


def record_interaction(
    question: str,
    *,
    status: str,
    duration_ms: int,
    structured: bool = False,
    source_count: int = 0,
    answer_chars: int = 0,
    error: str = "",
) -> None:
    _INTERACTIONS.append(
        {
            "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "status": status,
            "duration_ms": duration_ms,
            "structured": structured,
            "source_count": source_count,
            "answer_chars": answer_chars,
            "question": question[:300],
            "error": error[:300],
        }
    )


def recent_interactions(limit: int = 30) -> list[dict[str, Any]]:
    return list(_INTERACTIONS)[-limit:]
