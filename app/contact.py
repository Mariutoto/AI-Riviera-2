from __future__ import annotations

import smtplib
from email.message import EmailMessage

from app.answer import get_secret
from app.diagnostics import record_diagnostic

DEFAULT_RECIPIENT = "yannboulben@gmail.com"


class ContactSendError(RuntimeError):
    """Raised when the contact form message could not be delivered."""


def send_contact_message(name: str, email: str, subject: str, message: str) -> None:
    """Email a contact-form submission to the site owner.

    Requires SMTP_USER / SMTP_PASSWORD secrets (e.g. a Gmail address and an
    app password) to be configured; raises ContactSendError otherwise or on
    delivery failure.
    """
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
    email_msg["Subject"] = f"[AI Riviera] {subject.strip()}" if subject.strip() else "[AI Riviera] Nouveau message de contact"
    email_msg["From"] = smtp_user
    email_msg["To"] = recipient
    email_msg["Reply-To"] = email
    email_msg.set_content(f"Nom : {name}\nEmail : {email}\n\n{message}")

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.send_message(email_msg)
    except Exception as exc:
        record_diagnostic("contact", "Failed to send contact email", exc)
        raise ContactSendError(
            "L'envoi du message a échoué. Réessayez plus tard ou passez par GitHub."
        ) from exc
