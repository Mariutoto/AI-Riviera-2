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
