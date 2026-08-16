from __future__ import annotations

import pytest

from app import support


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (" Personne@Example.CH ", "personne@example.ch"),
        ("simple@example.org", "simple@example.org"),
    ],
)
def test_normalize_email(value: str, expected: str) -> None:
    assert support.normalize_email(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("personne@example.ch", True),
        (" Personne@Example.CH ", True),
        ("pas-un-email", False),
        ("a@b", False),
        ("a b@example.ch", False),
    ],
)
def test_valid_email(value: str, expected: bool) -> None:
    assert support.valid_email(value) is expected


def test_email_fingerprint_is_normalized_and_keyed(monkeypatch) -> None:
    monkeypatch.setattr(support, "get_secret", lambda name: "test-secret")

    first = support._email_fingerprint(" Personne@Example.CH ")
    second = support._email_fingerprint("personne@example.ch")

    assert first == second
    assert "personne@example.ch" not in first


def test_email_fingerprint_requires_a_secret(monkeypatch) -> None:
    monkeypatch.setattr(support, "get_secret", lambda name: "")

    with pytest.raises(support.SupportRecordError):
        support._email_fingerprint("personne@example.ch")


def test_support_municipalities_exclude_non_municipal_association() -> None:
    assert "Vevey" in support.ALLOWED_MUNICIPALITIES
    assert "Autre commune" in support.ALLOWED_MUNICIPALITIES
    assert not any("Association" in value for value in support.ALLOWED_MUNICIPALITIES)


def test_la_tour_de_peilz_scope_lists_indexed_documents_and_period() -> None:
    municipality = next(
        item for item in support.MUNICIPALITIES.values() if item.label == "La Tour-de-Peilz"
    )

    assert "procès-verbaux" in municipality.search_scope
    assert "interpellations" in municipality.search_scope
    assert "préavis municipaux" in municipality.search_scope
    assert "rapports de gestion et des comptes" in municipality.search_scope
    assert municipality.search_period == "Période principale : législature 2021–2026"


class _FakeCursor:
    def __init__(self, inserted: bool) -> None:
        self.inserted = inserted

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        return None

    def execute(self, query: str, params=None) -> None:
        pass

    def fetchone(self):
        return (1,) if self.inserted else None


class _FakeConnection:
    def __init__(self, inserted: bool) -> None:
        self.inserted = inserted

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        return None

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self.inserted)

    def commit(self) -> None:
        pass


def test_new_support_sends_one_owner_notification(monkeypatch) -> None:
    notifications = []
    monkeypatch.setattr(support, "_connect", lambda: _FakeConnection(inserted=True))
    monkeypatch.setattr(support, "_email_fingerprint", lambda email: "fingerprint")
    monkeypatch.setattr(
        "app.contact.send_support_notification",
        lambda **details: notifications.append(details),
    )

    created = support.record_project_support("person@example.ch", "Vevey", False)

    assert created is True
    assert notifications == [
        {
            "municipality": "Vevey",
            "newsletter_opt_in": False,
            "contact_email": None,
        }
    ]


def test_duplicate_support_does_not_send_notification(monkeypatch) -> None:
    notifications = []
    monkeypatch.setattr(support, "_connect", lambda: _FakeConnection(inserted=False))
    monkeypatch.setattr(support, "_email_fingerprint", lambda email: "fingerprint")
    monkeypatch.setattr(
        "app.contact.send_support_notification",
        lambda **details: notifications.append(details),
    )

    created = support.record_project_support("person@example.ch", "Vevey", False)

    assert created is False
    assert notifications == []
