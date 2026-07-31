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
    document_types: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()


LA_TOUR_DE_PEILZ = Municipality(
    key="la-tour-de-peilz",
    label="La Tour-de-Peilz",
    source_domain="www.la-tour-de-peilz.ch",
    documents_directory="la-tour-de-peilz",
    search_enabled=True,
    search_scope="tous les documents actuellement indexés",
    document_types=(
        "interpellations",
        "postulats",
        "motions",
        "preavis-municipaux",
        "proces-verbaux",
        "budget",
        "rapports-gestion",
        "rapports-comptes",
        "reglement-conseil-communal",
    ),
)

VEVEY = Municipality(
    key="vevey",
    label="Vevey",
    source_domain="www.vevey.ch",
    documents_directory="vevey",
    search_enabled=True,
    search_scope="interpellations, motions et postulats",
    document_types=("interpellations", "motions", "postulats"),
)

MONTREUX = Municipality(
    key="montreux",
    label="Montreux",
    source_domain="www.conseilmontreux.ch",
    documents_directory="montreux",
    search_enabled=True,
    search_scope="interpellations uniquement",
    document_types=("interpellations",),
)

BLONAY_SAINT_LEGIER = Municipality(
    key="blonay-saint-legier",
    label="Blonay–Saint-Légier",
    source_domain="",
    documents_directory="blonay-saint-legier",
    aliases=("Blonay-Saint-Légier", "Blonay", "Saint-Légier"),
)

CORSIER_SUR_VEVEY = Municipality(
    key="corsier-sur-vevey",
    label="Corsier-sur-Vevey",
    source_domain="",
    documents_directory="corsier-sur-vevey",
    aliases=("Corsier",),
)

CORSEAUX = Municipality(
    key="corseaux",
    label="Corseaux",
    source_domain="",
    documents_directory="corseaux",
)

CHARDONNE = Municipality(
    key="chardonne",
    label="Chardonne",
    source_domain="",
    documents_directory="chardonne",
)

JONGNY = Municipality(
    key="jongny",
    label="Jongny",
    source_domain="",
    documents_directory="jongny",
)

VEYTAUX = Municipality(
    key="veytaux",
    label="Veytaux",
    source_domain="",
    documents_directory="veytaux",
)

VILLENEUVE = Municipality(
    key="villeneuve",
    label="Villeneuve",
    source_domain="",
    documents_directory="villeneuve",
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
        BLONAY_SAINT_LEGIER,
        CORSIER_SUR_VEVEY,
        CORSEAUX,
        CHARDONNE,
        JONGNY,
        VEYTAUX,
        VILLENEUVE,
        ASSOCIATION_SECURITE_RIVIERA,
    )
}


def get_municipality(key: str) -> Municipality:
    try:
        return MUNICIPALITIES[key]
    except KeyError as exc:
        raise ValueError(f"Commune AI Riviera inconnue: {key}") from exc
