from __future__ import annotations

import re
import unicodedata

from municipal_pipeline.municipalities import MUNICIPALITIES


DOCUMENT_TYPE_KEYWORDS = {
    "Interpellations": ("interpellation", "interpellations"),
    "Postulats": ("postulat", "postulats"),
    "Motions": ("motion", "motions"),
    "Préavis municipaux": ("preavis", "preavis municipal", "preavis municipaux"),
    "Procès-verbaux": ("proces-verbal", "proces-verbaux", "pv"),
    "Budgets": ("budget", "budgets"),
    "Rapports de gestion": ("rapport de gestion", "rapports de gestion"),
    "Rapports des comptes": ("rapport des comptes", "rapports des comptes"),
    "Règlement du Conseil communal": (
        "reglement du conseil communal",
        "reglement communal",
    ),
}

DOCUMENT_TYPE_FILTER_VALUES = {
    "Interpellations": "interpellations",
    "Postulats": "postulats",
    "Motions": "motions",
    "Préavis municipaux": "preavis-municipaux",
    "Procès-verbaux": "proces-verbaux",
    "Budgets": "budget",
    "Rapports de gestion": "rapports-gestion",
    "Rapports des comptes": "rapports-comptes",
    "Règlement du Conseil communal": "reglement-conseil-communal",
}


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(char for char in decomposed if not unicodedata.combining(char)).lower()


def _mentioned_document_types(question: str) -> set[str]:
    normalized = _normalize(question)
    mentioned = set()
    for label, keywords in DOCUMENT_TYPE_KEYWORDS.items():
        if any(re.search(rf"\b{re.escape(keyword)}\b", normalized) for keyword in keywords):
            mentioned.add(label)
    return mentioned


def filter_guard_message(
    question: str,
    *,
    selected_city: str,
    selected_year: str,
    selected_document_type: str,
) -> str | None:
    """Return a user-facing warning when explicit question terms conflict with filters."""
    normalized = _normalize(question)
    enabled_city_labels = {
        municipality.label
        for municipality in MUNICIPALITIES.values()
        if municipality.search_enabled
    }
    available_cities_text = " ou ".join(sorted(enabled_city_labels))

    unavailable_cities = []
    for municipality in MUNICIPALITIES.values():
        if municipality.search_enabled:
            continue
        names = (municipality.label, *municipality.aliases)
        if any(
            re.search(rf"\b{re.escape(_normalize(name))}\b", normalized)
            for name in names
        ):
            unavailable_cities.append(municipality.label)
    if unavailable_cities:
        city = unavailable_cities[0]
        return (
            f"Attention : votre question mentionne {city}, mais ses documents ne sont pas "
            f"encore disponibles. Pour l’instant, recherchez uniquement {available_cities_text}."
        )

    if selected_city != "all" and selected_city not in enabled_city_labels:
        return (
            f"Attention : {selected_city} n’est pas encore disponible. "
            f"Choisissez « Toutes » ou « {available_cities_text} »."
        )

    mentioned_enabled_municipalities = []
    for municipality in MUNICIPALITIES.values():
        if not municipality.search_enabled:
            continue
        names = (municipality.label, *municipality.aliases)
        if any(
            re.search(rf"\b{re.escape(_normalize(name))}\b", normalized)
            for name in names
        ):
            mentioned_enabled_municipalities.append(municipality)

    selected_municipality = next(
        (
            municipality
            for municipality in MUNICIPALITIES.values()
            if municipality.label == selected_city
            and municipality.search_enabled
        ),
        None,
    )
    scope_targets = (
        [selected_municipality]
        if selected_municipality
        else mentioned_enabled_municipalities
    )
    requested_types = _mentioned_document_types(question)
    if selected_document_type != "Tous":
        requested_types.add(selected_document_type)
    for municipality in scope_targets:
        unsupported = sorted(
            label
            for label in requested_types
            if DOCUMENT_TYPE_FILTER_VALUES.get(label)
            not in municipality.document_types
        )
        if not unsupported:
            continue
        available = [
            label
            for label, value in DOCUMENT_TYPE_FILTER_VALUES.items()
            if value in municipality.document_types
        ]
        return (
            f"Attention : {' et '.join(unsupported)} ne sont pas encore "
            f"disponibles pour {municipality.label}. "
            f"Documents disponibles : {', '.join(available)}."
        )

    years = set(re.findall(r"\b20(?:21|22|23|24|25|26)\b", normalized))
    if selected_year != "Toutes" and years and selected_year not in years:
        years_text = ", ".join(sorted(years))
        return (
            f"Attention : votre question mentionne {years_text}, mais le filtre Année est "
            f"réglé sur {selected_year}. Choisissez {years_text} ou remettez « Toutes »."
        )

    mentioned_types = _mentioned_document_types(question)
    if (
        selected_document_type != "Tous"
        and mentioned_types
        and selected_document_type not in mentioned_types
    ):
        types_text = ", ".join(sorted(mentioned_types))
        return (
            f"Attention : votre question concerne « {types_text} », mais le filtre Type de "
            f"document est réglé sur « {selected_document_type} ». Corrigez-le ou remettez « Tous »."
        )

    return None
