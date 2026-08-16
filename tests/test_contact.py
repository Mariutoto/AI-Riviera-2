from __future__ import annotations

from email.message import EmailMessage

from app import contact


class FakeSMTP:
    sent_messages: list[EmailMessage] = []

    def __init__(self, host: str, port: int, timeout: int) -> None:
        assert host == "smtp.example.test"
        assert port == 587
        assert timeout == 10

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        return None

    def starttls(self) -> None:
        pass

    def login(self, user: str, password: str) -> None:
        assert user == "sender@example.test"
        assert password == "password"

    def send_message(self, message: EmailMessage) -> None:
        self.sent_messages.append(message)


def _configure_email(monkeypatch) -> None:
    secrets = {
        "SMTP_HOST": "smtp.example.test",
        "SMTP_PORT": "587",
        "SMTP_USER": "sender@example.test",
        "SMTP_PASSWORD": "password",
        "CONTACT_RECIPIENT": "owner@example.test",
    }
    monkeypatch.setattr(
        contact,
        "get_secret",
        lambda name, default=None: secrets.get(name, default),
    )
    monkeypatch.setattr(contact.smtplib, "SMTP", FakeSMTP)
    FakeSMTP.sent_messages.clear()


def test_support_notification_goes_only_to_owner_without_private_email(monkeypatch) -> None:
    _configure_email(monkeypatch)

    contact.send_support_notification("Vevey", newsletter_opt_in=False)

    message = FakeSMTP.sent_messages[0]
    assert message["To"] == "owner@example.test"
    assert message["Subject"] == "[AI Riviera] Nouveau soutien"
    assert message["Cc"] is None
    assert message["Bcc"] is None
    assert "Adresse e-mail : Non conservée" in message.get_content()


def test_support_notification_includes_email_only_with_consent(monkeypatch) -> None:
    _configure_email(monkeypatch)

    contact.send_support_notification(
        "Montreux",
        newsletter_opt_in=True,
        contact_email="person@example.test",
    )

    assert "person@example.test" in FakeSMTP.sent_messages[0].get_content()
