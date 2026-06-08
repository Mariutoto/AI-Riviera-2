from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timezone
from typing import Any


_MAX_EVENTS = 80
_EVENTS: deque[dict[str, Any]] = deque(maxlen=_MAX_EVENTS)


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
