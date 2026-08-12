from __future__ import annotations

import hashlib
import hmac
import re

from app.answer import get_secret
from app.diagnostics import record_diagnostic
from app.pilot_v2_store import POSTGRES_V2_URL
from municipal_pipeline.municipalities import MUNICIPALITIES


ALLOWED_MUNICIPALITIES = {
    municipality.label
    for municipality in MUNICIPALITIES.values()
    if municipality.key != "association-securite-riviera"
} | {"Autre commune"}


class SupportRecordError(RuntimeError):
    """Raised when a support entry cannot be validated or saved."""


def _connect():
    import psycopg

    return psycopg.connect(POSTGRES_V2_URL)


def normalize_email(email: str) -> str:
    return email.strip().lower()


def valid_email(email: str) -> bool:
    return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", normalize_email(email)))


def _email_fingerprint(email: str) -> str:
    secret = get_secret("SUPPORT_HASH_SECRET")
    if not secret:
        raise SupportRecordError(
            "Le formulaire de soutien n'est pas encore configuré sur ce site."
        )
    return hmac.new(
        secret.encode("utf-8"),
        normalize_email(email).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _ensure_table(cursor) -> None:
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS project_support (
            id BIGSERIAL PRIMARY KEY,
            email_fingerprint TEXT NOT NULL UNIQUE,
            contact_email TEXT,
            municipality TEXT NOT NULL,
            newsletter_opt_in BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )


def record_project_support(
    email: str,
    municipality: str,
    newsletter_opt_in: bool,
) -> bool:
    """Record one support per email and return True for a new supporter.

    The readable email is retained only when the person explicitly asks to
    receive project news. Otherwise, only a keyed fingerprint is stored.
    """
    normalized_email = normalize_email(email)
    municipality = " ".join(municipality.split()).strip()
    if not valid_email(normalized_email):
        raise SupportRecordError("L'adresse e-mail ne semble pas valide.")
    if not municipality:
        raise SupportRecordError("Merci de sélectionner votre commune.")
    if municipality not in ALLOWED_MUNICIPALITIES:
        raise SupportRecordError("La commune sélectionnée n'est pas valide.")

    fingerprint = _email_fingerprint(normalized_email)
    contact_email = normalized_email if newsletter_opt_in else None

    try:
        with _connect() as connection, connection.cursor() as cursor:
            _ensure_table(cursor)
            cursor.execute(
                """
                INSERT INTO project_support (
                    email_fingerprint,
                    contact_email,
                    municipality,
                    newsletter_opt_in
                ) VALUES (%s, %s, %s, %s)
                ON CONFLICT (email_fingerprint) DO NOTHING
                RETURNING id
                """,
                (fingerprint, contact_email, municipality, newsletter_opt_in),
            )
            created = cursor.fetchone() is not None
            if not created and newsletter_opt_in:
                cursor.execute(
                    """
                    UPDATE project_support
                    SET contact_email = %s,
                        municipality = %s,
                        newsletter_opt_in = TRUE,
                        updated_at = now()
                    WHERE email_fingerprint = %s
                    """,
                    (normalized_email, municipality, fingerprint),
                )
            connection.commit()
            return created
    except SupportRecordError:
        raise
    except Exception as exc:
        record_diagnostic(
            "project_support",
            "Failed to record project support",
            exc,
            municipality=municipality,
        )
        raise SupportRecordError(
            "Le soutien n'a pas pu être enregistré. Merci de réessayer plus tard."
        ) from exc
