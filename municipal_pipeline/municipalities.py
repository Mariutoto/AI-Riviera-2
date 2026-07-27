from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Municipality:
    key: str
    label: str
    source_domain: str
    documents_directory: str
    search_enabled: bool = False
    search_scope: str = ""
    aliases: tuple[str, ...] = ()


LA_TOUR_DE_PEILZ = Municipality(
    key="la-tour-de-peilz",
    label="La Tour-de-Peilz",
    source_domain="www.la-tour-de-peilz.ch",
    documents_directory="la-tour-de-peilz",
    search_enabled=True,
)

VEVEY = Municipality(
    key="vevey",
    label="Vevey",
    source_domain="www.vevey.ch",
    documents_directory="vevey",
    search_enabled=True,
    search_scope="interpellations uniquement",
)

MONTREUX = Municipality(
    key="montreux",
    label="Montreux",
    source_domain="www.montreux.ch",
    documents_directory="montreux",
)

ASSOCIATION_SECURITE_RIVIERA = Municipality(
    key="association-securite-riviera",
    label="ASR – Association Sécurité Riviera",
    source_domain="www.securite-riviera.ch",
    documents_directory="association-securite-riviera",
    aliases=("ASR", "Association Sécurité Riviera"),
)

MUNICIPALITIES = {
    municipality.key: municipality
    for municipality in (
        LA_TOUR_DE_PEILZ,
        VEVEY,
        MONTREUX,
        ASSOCIATION_SECURITE_RIVIERA,
    )
}


def get_municipality(key: str) -> Municipality:
    try:
        return MUNICIPALITIES[key]
    except KeyError as exc:
        raise ValueError(f"Commune AI Riviera inconnue: {key}") from exc
