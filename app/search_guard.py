from __future__ import annotations

import re
import unicodedata


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

    unavailable_cities = [
        city for city in ("Vevey", "Montreux") if re.search(rf"\b{city.lower()}\b", normalized)
    ]
    if unavailable_cities:
        city = unavailable_cities[0]
        return (
            f"Attention : votre question mentionne {city}, mais ses documents ne sont pas "
            "encore disponibles. Pour l’instant, recherchez uniquement La Tour-de-Peilz."
        )

    if selected_city not in {"all", "La Tour-de-Peilz"}:
        return (
            f"Attention : {selected_city} n’est pas encore disponible. "
            "Choisissez « Toutes » ou « La Tour-de-Peilz »."
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
