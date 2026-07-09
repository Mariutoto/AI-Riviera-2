from __future__ import annotations

from app.diagnostics import record_diagnostic
from app.pilot_v2_store import POSTGRES_V2_URL


def _connect():
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(POSTGRES_V2_URL, row_factory=dict_row)


def _ensure_table(cursor) -> None:
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS answer_feedback (
            id BIGSERIAL PRIMARY KEY,
            question TEXT NOT NULL,
            answer TEXT NOT NULL,
            rating TEXT NOT NULL,
            source_count INTEGER,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )


def record_feedback(question: str, answer: str, rating: str, source_count: int = 0) -> None:
    """Log a thumbs up/down for a given question/answer pair.

    Never raises — a broken feedback write shouldn't crash the chat. Failures
    go through the existing diagnostics log instead.
    """
    try:
        with _connect() as connection, connection.cursor() as cursor:
            _ensure_table(cursor)
            cursor.execute(
                "INSERT INTO answer_feedback (question, answer, rating, source_count) VALUES (%s, %s, %s, %s)",
                (question, answer, rating, source_count),
            )
            connection.commit()
    except Exception as exc:
        record_diagnostic("feedback", "Failed to record answer feedback", exc, rating=rating)


def recent_feedback(limit: int = 100) -> list[dict]:
    try:
        with _connect() as connection, connection.cursor() as cursor:
            _ensure_table(cursor)
            connection.commit()
            cursor.execute(
                "SELECT question, answer, rating, source_count, created_at "
                "FROM answer_feedback ORDER BY created_at DESC LIMIT %s",
                (limit,),
            )
            return cursor.fetchall()
    except Exception as exc:
        record_diagnostic("feedback", "Failed to read answer feedback", exc)
        return []
