from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timezone
from typing import Any


_MAX_EVENTS = 80
_EVENTS: deque[dict[str, Any]] = deque(maxlen=_MAX_EVENTS)
_INTERACTIONS: deque[dict[str, Any]] = deque(maxlen=120)


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


def recent_diagnostics(limit: int = 20) -> list[dict[str, Any]]:
    return list(_EVENTS)[-limit:]


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
