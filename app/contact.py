from __future__ import annotations

import smtplib
from email.message import EmailMessage

from app.answer import get_secret
from app.diagnostics import record_diagnostic

DEFAULT_RECIPIENT = "yannboulben@gmail.com"


class ContactSendError(RuntimeError):
    """Raised when the contact form message could not be delivered."""


def _single_line(value: str) -> str:
    """Strip embedded CR/LF so a pasted multi-line value can't split or
    inject extra email headers (EmailMessage rejects them outright, but
    that raises outside the try/except below if left unguarded)."""
    return " ".join(value.splitlines()).strip()


def _send_owner_email(subject: str, body: str, reply_to: str | None = None) -> None:
    """Send one email to the private address configured for the site owner."""
    smtp_host = get_secret("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(get_secret("SMTP_PORT", "587") or "587")
    smtp_user = get_secret("SMTP_USER")
    smtp_password = get_secret("SMTP_PASSWORD")
    recipient = get_secret("CONTACT_RECIPIENT", DEFAULT_RECIPIENT)

    if not smtp_user or not smtp_password:
        raise ContactSendError(
            "L'envoi d'e-mail n'est pas encore configuré sur ce site."
        )

    email_msg = EmailMessage()
    email_msg["Subject"] = _single_line(subject)
    email_msg["From"] = smtp_user
    email_msg["To"] = recipient
    if reply_to:
        email_msg["Reply-To"] = _single_line(reply_to)
    email_msg.set_content(body)

    with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as server:
        server.starttls()
        server.login(smtp_user, smtp_password)
        server.send_message(email_msg)


def send_contact_message(name: str, email: str, subject: str, message: str) -> None:
    """Email a contact-form submission to the site owner.

    Requires SMTP_USER / SMTP_PASSWORD secrets (e.g. a Gmail address and an
    app password) to be configured; raises ContactSendError otherwise or on
    delivery failure.
    """
    name = _single_line(name)
    email = _single_line(email)
    subject = _single_line(subject)

    try:
        _send_owner_email(
            f"[AI Riviera] {subject}" if subject else "[AI Riviera] Nouveau message de contact",
            f"Nom : {name}\nEmail : {email}\n\n{message}",
            reply_to=email,
        )
    except Exception as exc:
        record_diagnostic("contact", "Failed to send contact email", exc)
        raise ContactSendError(
            "L'envoi du message a échoué. Réessayez plus tard ou passez par GitHub."
        ) from exc


def send_support_notification(
    municipality: str,
    newsletter_opt_in: bool,
    contact_email: str | None = None,
) -> None:
    """Notify only the site owner that a new supporter was recorded."""
    newsletter = "Oui" if newsletter_opt_in else "Non"
    email_line = contact_email if newsletter_opt_in and contact_email else "Non conservée"
    body = (
        "Un nouveau soutien à AI Riviera a été enregistré.\n\n"
        f"Commune : {municipality}\n"
        f"Souhaite recevoir les nouvelles : {newsletter}\n"
        f"Adresse e-mail : {email_line}\n"
    )
    _send_owner_email("[AI Riviera] Nouveau soutien", body)
