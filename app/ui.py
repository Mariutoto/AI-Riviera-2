import sys
import os
import html
import math
from pathlib import Path
import re
import time

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

ASSETS_DIR = PROJECT_ROOT / "assets"
LANDSCAPE_IMAGE_PATH = ASSETS_DIR / "riviera-vaudoise-landscape.jpg"

from app.agent import GENERATION_PASSAGE_LIMIT, RERANK_CANDIDATE_LIMIT, RERANK_KEEP_LIMIT, run_agentic_pipeline
from app.answer import answer_from_sources, get_secret, rerank_results_with_llm, rewrite_query_with_llm, source_blurbs_with_fallback
from app.diagnostics import record_diagnostic, record_interaction, recent_diagnostics, recent_interactions
from app.eval_set import load_eval_questions, retrieval_hit
from app.feedback import record_feedback, recent_feedback
from app.pilot_v2_store import CATEGORY_MAP, browse_documents, ready as pilot_v2_ready
from app.retrieval import search
from app.search_guard import filter_guard_message
from app.text_cleaning import fix_mojibake, format_date
from municipal_pipeline.municipalities import MUNICIPALITIES

SUGGESTED_QUESTIONS = [
    "Quelles interpellations ont reçu une réponse en 2025 ?",
    "Quels postulats ont été déposés en 2024 ?",
]

CITY_OPTIONS = {"all": "Toutes"}
CITY_OPTIONS.update(
    {
        municipality.label: (
            (
                f"{municipality.label} — {municipality.search_scope}"
                if municipality.search_scope
                else municipality.label
            )
            if municipality.search_enabled
            else f"{municipality.label} — à venir"
        )
        for municipality in MUNICIPALITIES.values()
    }
)
SEARCH_ENABLED_CITIES = {
    municipality.label
    for municipality in MUNICIPALITIES.values()
    if municipality.search_enabled
}
ALL_YEARS = "Toutes"
YEAR_OPTIONS = [ALL_YEARS, "2026", "2025", "2024", "2023", "2022", "2021"]
ALL_DOCUMENT_TYPES = "Tous"
DOCUMENT_TYPE_OPTIONS = {
    ALL_DOCUMENT_TYPES: "",
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
DOCUMENT_BROWSER_CATEGORY_LABELS = {
    CATEGORY_MAP[document_type]: label
    for label, document_type in DOCUMENT_TYPE_OPTIONS.items()
    if document_type in CATEGORY_MAP
}


def available_document_type_labels(city: str) -> list[str]:
    """Return only document filters backed by the selected corpus."""
    if city == "all":
        available = {
            document_type
            for municipality in MUNICIPALITIES.values()
            if municipality.search_enabled
            for document_type in municipality.document_types
        }
    else:
        municipality = next(
            (
                item
                for item in MUNICIPALITIES.values()
                if item.label == city and item.search_enabled
            ),
            None,
        )
        available = set(municipality.document_types) if municipality else set()
    return [
        label
        for label, document_type in DOCUMENT_TYPE_OPTIONS.items()
        if label == ALL_DOCUMENT_TYPES or document_type in available
    ]


def available_browser_document_type_labels(cities: list[str]) -> list[str]:
    if not cities:
        available = {
            document_type
            for municipality in MUNICIPALITIES.values()
            if municipality.search_enabled
            for document_type in municipality.document_types
        }
    else:
        available = {
            document_type
            for municipality in MUNICIPALITIES.values()
            if municipality.search_enabled and municipality.label in cities
            for document_type in municipality.document_types
        }
    return [
        label
        for label, document_type in DOCUMENT_TYPE_OPTIONS.items()
        if label != ALL_DOCUMENT_TYPES and document_type in available
    ]


USER_ERROR_MESSAGE = (
    "Désolé, la recherche a rencontré un problème technique. "
    "La question a été journalisée pour diagnostic; tu peux réessayer dans un instant."
)

ANSWER_CACHE_VERSION = "political-document-format-v3"

FOLLOW_UP_HINTS = {
    "alors",
    "aussi",
    "ca",
    "cela",
    "celles",
    "celle",
    "celui",
    "ceux",
    "combien",
    "donc",
    "meme",
    "precedent",
    "precedente",
    "quoi",
    "somme",
    "total",
}


def admin_tabs_enabled() -> bool:
    value = os.getenv("SHOW_ADMIN_TABS", "")
    try:
        value = str(st.secrets.get("SHOW_ADMIN_TABS", value))
    except Exception:
        pass
    return value.lower().strip() in {"1", "true", "yes", "on"}


st.set_page_config(page_title="AI Riviera", page_icon="🏛️", layout="wide")

st.markdown(
    """
    <style>
    :root {
        --air-ink: #233139;
        --air-muted: #6f7d83;
        --air-line: #dbe4e6;
        --air-soft: #f3f7f8;
        --air-accent: #34788a;
        --air-accent-dark: #285f6e;
    }

    html, body {
        font-family: Arial, Helvetica, sans-serif;
    }

    [data-testid="stAppViewContainer"] {
        background: #ffffff;
        color: var(--air-ink);
        font-family: Arial, Helvetica, sans-serif;
    }

    [data-testid="stSidebar"], [data-testid="collapsedControl"] {
        display: none;
    }

    [data-testid="stMainBlockContainer"] {
        max-width: 1040px;
        padding-bottom: 6rem;
        padding-top: 2rem;
    }

    .air-site-brand {
        color: var(--air-ink);
        font-size: 1.18rem;
        font-weight: 600;
        letter-spacing: -0.025em;
        line-height: 2.6rem;
        margin: 0;
    }

    [data-testid="stTabs"] {
        position: relative;
    }

    [data-testid="stTabs"] [data-baseweb="tab-list"] {
        border-bottom: 1px solid var(--air-line);
        gap: 0.25rem;
        justify-content: flex-end;
        margin-top: -3.25rem;
        min-height: 3.25rem;
    }

    [data-testid="stTabs"] button[data-baseweb="tab"] {
        background: transparent;
        color: var(--air-muted);
        font-size: 0.88rem;
        font-weight: 400;
        padding-left: 0.8rem;
        padding-right: 0.8rem;
    }

    [data-testid="stTabs"] button[data-baseweb="tab"][aria-selected="true"] {
        color: var(--air-ink);
    }

    [data-testid="stTabs"] div[data-baseweb="tab-highlight"] {
        background: var(--air-accent);
    }

    .air-home-hero {
        margin: 4.25rem auto 1.55rem;
        max-width: 720px;
        text-align: center;
    }

    .air-home-hero h1 {
        color: var(--air-ink);
        font-size: clamp(2rem, 4vw, 2.85rem);
        font-weight: 500;
        letter-spacing: -0.04em;
        line-height: 1.08;
        margin: 0;
    }

    .air-home-hero p {
        color: var(--air-muted);
        font-size: 1rem;
        line-height: 1.55;
        margin: 0.85rem auto 0;
    }

    .st-key-air-home-search,
    .st-key-air-search-filters {
        margin-left: auto;
        margin-right: auto;
        max-width: 700px;
    }

    .st-key-air-home-search {
        margin-bottom: 0.55rem;
    }

    .st-key-air-home-search [data-testid="stChatInput"] {
        border: 1px solid #bfcfd3;
        border-radius: 0.7rem;
        box-shadow: 0 0.45rem 1.4rem rgba(35, 49, 57, 0.06);
        position: relative;
    }

    [data-testid="stChatInput"] {
        border-color: #bfcfd3;
        border-radius: 0.7rem;
    }

    [data-testid="stChatInput"]:focus-within {
        border-color: var(--air-accent);
        box-shadow: 0 0 0 1px var(--air-accent);
    }

    [data-testid="stChatInput"] button {
        color: var(--air-accent-dark);
    }

    .st-key-air-search-filters [data-testid="stExpander"] {
        background: transparent;
        border: 0;
        border-bottom: 1px solid var(--air-line);
        border-radius: 0;
        box-shadow: none;
    }

    .st-key-air-search-filters [data-testid="stExpander"] summary {
        color: var(--air-muted);
        font-size: 0.82rem;
        min-height: 2.65rem;
    }

    .st-key-air-search-filters [data-testid="stExpander"] summary:focus,
    .st-key-air-search-filters [data-testid="stExpander"] summary:focus-visible {
        box-shadow: none;
        outline: 1px solid var(--air-accent);
        outline-offset: -1px;
    }

    div[data-testid="stButton"] > button {
        background: #ffffff;
        border: 1px solid var(--air-line);
        border-radius: 0.55rem;
        color: var(--air-ink);
        font-weight: 400;
        min-height: 2.75rem;
        text-align: left;
    }

    div[data-testid="stButton"] > button:hover {
        background: var(--air-soft);
        border-color: #9eb8be;
        color: var(--air-accent-dark);
    }

    .air-loading {
        align-items: center;
        background: var(--air-soft);
        border: 1px solid var(--air-line);
        border-radius: 0.45rem;
        color: var(--air-ink);
        display: flex;
        gap: 0.75rem;
        justify-content: flex-start;
        margin: 0.75rem 0 0;
        min-height: 3.6rem;
        padding: 0.72rem 1.45rem;
        width: 100%;
    }

    .air-loading-docs {
        flex: 0 0 auto;
        height: 2rem;
        position: relative;
        width: 2.2rem;
    }

    .air-loading-page {
        background: #ffffff;
        border: 2px solid var(--air-accent);
        border-radius: 0.25rem;
        box-shadow: 0 0.12rem 0.3rem rgba(31, 41, 51, 0.08);
        height: 1.6rem;
        left: 0.18rem;
        position: absolute;
        top: 0.12rem;
        width: 1.35rem;
    }

    .air-loading-page:nth-child(1) {
        animation: airPageFlip 1.45s ease-in-out infinite;
        z-index: 3;
    }

    .air-loading-page:nth-child(2) {
        left: 0.55rem;
        opacity: 0.74;
        top: 0.35rem;
        z-index: 2;
    }

    .air-loading-page:nth-child(3) {
        left: 0.9rem;
        opacity: 0.46;
        top: 0.58rem;
        z-index: 1;
    }

    .air-loading-text {
        font-size: 0.92rem;
        font-weight: 650;
        line-height: 1.2;
    }

    .air-about-diagram {
        align-items: stretch;
        display: grid;
        gap: 0.75rem;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        margin: 1rem 0 1.25rem;
    }

    .air-about-step {
        background: var(--air-soft);
        border: 1px solid var(--air-line);
        border-radius: 0.45rem;
        color: var(--air-ink);
        min-height: 7.2rem;
        padding: 0.85rem;
    }

    .air-about-step strong {
        color: var(--air-ink);
        display: block;
        font-size: 0.98rem;
        margin-bottom: 0.35rem;
    }

    .air-about-step span {
        color: var(--air-muted);
        display: block;
        font-size: 0.9rem;
        line-height: 1.45;
    }

    .air-about-list {
        color: var(--air-muted);
        display: grid;
        gap: 0.75rem 2rem;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        line-height: 1.5;
        margin: 0.8rem 0 1.7rem;
        padding-left: 1.25rem;
    }

    .air-about-list li {
        padding-left: 0.2rem;
    }

    .air-about-list li::marker {
        color: var(--air-accent);
    }

    .air-about-list strong {
        color: var(--air-ink);
        display: block;
        font-weight: 600;
        margin-bottom: 0.15rem;
    }

    .air-about-note {
        background: #fff8ea;
        border: 1px solid #ead7a9;
        border-radius: 0.45rem;
        color: #4a3b1d;
        margin-top: 1rem;
        padding: 0.8rem 0.95rem;
    }

    .air-doc-grid {
        display: grid;
        gap: 0.85rem;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        margin: 1rem 0 1.25rem;
    }

    .air-doc-card {
        background: var(--air-soft);
        border: 1px solid var(--air-line);
        border-radius: 0.55rem;
        padding: 1rem;
    }

    .air-doc-card h3 {
        color: var(--air-ink);
        font-size: 1.05rem;
        margin: 0 0 0.65rem;
    }

    .air-doc-card p {
        color: var(--air-muted);
        font-size: 0.91rem;
        line-height: 1.5;
        margin: 0.35rem 0;
    }

    .air-doc-card strong {
        color: var(--air-ink);
    }

    .st-key-document-browser-search {
        margin: 1rem 0 1.25rem;
    }

    .st-key-document-browser-search [data-testid="stTextInputRootElement"] {
        background: #ffffff;
        border-color: #bfcfd3;
        border-radius: 0.65rem;
        box-shadow: 0 0.35rem 1.2rem rgba(35, 49, 57, 0.05);
        min-height: 3rem;
    }

    .st-key-document-browser-search [data-testid="stTextInputRootElement"]:focus-within {
        border-color: var(--air-accent);
        box-shadow: 0 0 0 1px var(--air-accent);
    }

    .st-key-document-browser-filters {
        background: var(--air-soft);
        border: 1px solid var(--air-line);
        border-radius: 0.65rem;
        padding: 0.85rem 0.9rem 0.45rem;
    }

    .st-key-document-browser-filters h3 {
        color: var(--air-ink);
        font-size: 1rem;
        margin: 0 0 0.75rem;
    }

    .st-key-document-browser-filters [data-testid="stWidgetLabel"] {
        color: var(--air-ink);
        font-size: 0.82rem;
    }

    .st-key-document-browser-results {
        min-width: 0;
    }

    .air-browser-result {
        align-items: center;
        border-bottom: 1px solid var(--air-line);
        display: grid;
        gap: 1rem;
        grid-template-columns: minmax(0, 1fr) auto;
        padding: 0.95rem 0.2rem;
    }

    .air-browser-result:first-child {
        border-top: 1px solid var(--air-line);
    }

    .air-browser-result p {
        color: var(--air-accent-dark);
        font-size: 0.72rem;
        letter-spacing: 0.025em;
        margin: 0 0 0.35rem;
    }

    .air-browser-result h3 {
        color: var(--air-ink);
        font-size: 0.95rem;
        font-weight: 550;
        line-height: 1.4;
        margin: 0;
    }

    .air-browser-result a {
        background: #ffffff;
        border: 1px solid var(--air-line);
        border-radius: 0.45rem;
        color: var(--air-accent-dark);
        font-size: 0.78rem;
        padding: 0.55rem 0.7rem;
        text-decoration: none;
        white-space: nowrap;
    }

    .air-browser-result a:hover {
        border-color: var(--air-accent);
        color: var(--air-accent-dark);
    }

    .air-browser-no-link {
        color: var(--air-muted);
        font-size: 0.75rem;
        white-space: nowrap;
    }

    @media (max-width: 900px) {
        .air-about-diagram, .air-doc-grid {
            grid-template-columns: repeat(2, minmax(0, 1fr));
        }
    }

    @media (max-width: 560px) {
        [data-testid="stMainBlockContainer"] {
            padding-left: 1rem;
            padding-right: 1rem;
            padding-top: 1rem;
        }

        .air-site-brand {
            line-height: 2.25rem;
        }

        [data-testid="stTabs"] [data-baseweb="tab-list"] {
            justify-content: flex-start;
            margin-top: 0;
            overflow-x: auto;
        }

        .air-home-hero {
            margin-top: 2.75rem;
        }

        .air-browser-result {
            align-items: start;
            grid-template-columns: 1fr;
        }

        .air-browser-result a,
        .air-browser-no-link {
            justify-self: start;
        }

        .air-about-diagram, .air-doc-grid, .air-about-list {
            grid-template-columns: 1fr;
        }
    }

    @keyframes airPageFlip {
        0%, 100% {
            transform: translateX(0) rotate(0);
        }
        50% {
            transform: translateX(0.55rem) rotate(6deg);
        }
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown('<div class="air-site-brand">AI Riviera</div>', unsafe_allow_html=True)

def current_filters() -> dict | None:
    filters = {}
    city = st.session_state.get("search_city", "all")
    # Keep the explicit "all" scope in the cache key and pipeline filters so
    # retrieval can balance candidates across every enabled commune.
    filters["city"] = city
    year = st.session_state.get("search_year", ALL_YEARS)
    if year != ALL_YEARS:
        filters["year"] = year
    document_type_label = st.session_state.get("search_document_type", ALL_DOCUMENT_TYPES)
    document_type = DOCUMENT_TYPE_OPTIONS.get(document_type_label, "")
    if document_type:
        filters["doc_type"] = document_type
    return filters


def cacheable_filters(filters: dict | None) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((str(key), str(value)) for key, value in (filters or {}).items()))


def normalize_follow_up_text(text: str) -> str:
    return fix_mojibake(text).lower().replace("ç", "c").replace("ê", "e")


def looks_like_follow_up(question: str) -> bool:
    normalized = normalize_follow_up_text(question)
    words = re.findall(r"[a-z0-9]+", normalized)
    if not words:
        return False
    return len(words) <= 4 or any(word in FOLLOW_UP_HINTS for word in words)


def compact_history_for_question(messages: list[dict], max_messages: int = 4) -> str:
    history_lines = []
    for message in messages[-max_messages:]:
        role = "Utilisateur" if message.get("role") == "user" else "Assistant"
        content = fix_mojibake(str(message.get("content", ""))).strip()
        if not content:
            continue
        content = re.sub(r"\nSources utilisées:.*", "", content, flags=re.DOTALL)
        content = re.sub(r"\s+", " ", content)
        history_lines.append(f"{role}: {content[:1200]}")
    return "\n".join(history_lines)


def contextualize_question(question: str, messages: list[dict]) -> str:
    previous_messages = messages[:-1] if messages and messages[-1].get("content") == question else messages
    if not previous_messages or not looks_like_follow_up(question):
        return question

    history = compact_history_for_question(previous_messages)
    if not history:
        return question
    return (
        "Question de suivi dans une conversation.\n"
        "Contexte récent:\n"
        f"{history}\n\n"
        f"Question actuelle: {question}"
    )


def ensure_index_ready() -> bool:
    if pilot_v2_ready():
        return True
    st.warning(
        "La base AI Riviera n'est pas encore prête. Relance l'indexation "
        "depuis l'environnement d'administration."
    )
    return False


def agentic_pipeline_enabled() -> bool:
    value = get_secret("ENABLE_AGENTIC_PIPELINE", "true")
    return str(value).lower().strip() in {"1", "true", "yes", "on"}


_COMPLEXITY_HINT_MARKERS = (
    "a la fois",
    "et aussi",
    "les deux",
    "compar",
    "difference entre",
    "et une motion",
    "et un postulat",
    "et une interpellation",
)


def guess_loading_complexity(question: str) -> str:
    """Cheap local heuristic used only to pick the loading message before the
    real (LLM-based) classification runs inside the cached pipeline — avoids
    paying for a second classification call just for the progress text."""
    normalized = normalize_follow_up_text(question)
    if any(marker in normalized for marker in _COMPLEXITY_HINT_MARKERS):
        return "complex"
    return "simple"


def group_results_by_document(results: list[dict]) -> list[dict]:
    grouped = {}
    for result in results:
        metadata = result.get("metadata") or {
            "city": result.get("city", ""),
            "doc_type": result.get("doc_type", ""),
            "title": result.get("title", ""),
            "date": result.get("date", ""),
            "source_url": result.get("source_url", ""),
            "document_hash": result.get("document_hash", ""),
        }
        document_key = (
            metadata.get("document_hash")
            or result.get("document_hash")
            or result.get("source_url")
            or metadata.get("source_url")
            or metadata.get("pdf_url")
            or metadata.get("text_path")
            or result.get("relative_text_path")
            or metadata.get("filename")
            or str(result.get("id", "")).split("#", 1)[0]
        )
        if document_key not in grouped:
            grouped[document_key] = {
                "metadata": metadata,
                "relative_text_path": result.get("relative_text_path", ""),
                "score": result.get("score", 0),
                "passages": [],
            }
        grouped[document_key]["score"] = max(grouped[document_key]["score"], result.get("score", 0))
        grouped[document_key]["passages"].append(result)
    return sorted(grouped.values(), key=lambda item: item["score"], reverse=True)


def source_link(metadata: dict, label: str) -> str:
    label = fix_mojibake(label)
    url = metadata.get("source_url") or metadata.get("pdf_url") or metadata.get("url") or metadata.get("file_url") or ""
    if not url:
        return label
    return f"[{label}]({url})"


POLITICAL_OBJECT_TYPE_LABELS = {"motion": "Motion", "postulat": "Postulat", "interpellation": "Interpellation"}

POLITICAL_STATUS_LABELS = {
    "filed": "Déposée",
    "referred_to_municipality": "Renvoyée à la Municipalité",
    "referred_directly_to_municipality": "Renvoyée directement à la Municipalité",
    "not_supported_by_council": "Non soutenue par le Conseil",
    "report_available": "Rapport disponible",
    "decision_available": "Décision disponible",
    "report_and_decision_available": "Rapport et décision disponibles",
    "withdrawn": "Retirée",
    "with_report_and_decision": "Rapport et décision disponibles",
    "with_decision": "Décision disponible",
    "withdrawn_by_municipality": "Retiré par la Municipalité",
}


def compact_names(names: list[str]) -> str:
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    return f"{names[0]} et autres"


def status_label(status: str | None) -> str | None:
    if not status:
        return None
    return POLITICAL_STATUS_LABELS.get(status, str(status).replace("_", " "))


def format_chf(value: float | int | None) -> str | None:
    if value is None:
        return None
    return f"{value:,.2f}".replace(",", "'") + " CHF"


def political_object_citation_line(metadata: dict, extra: dict) -> str | None:
    category = str(metadata.get("category") or "")
    type_label = POLITICAL_OBJECT_TYPE_LABELS.get(category)
    if not type_label:
        return None
    parts = [type_label]

    authors = extra.get("authors") or []
    names = [author.get("name") for author in authors if isinstance(author, dict) and author.get("name")]
    if names:
        parts.append(compact_names(names))

    deposit_date = metadata.get("document_date") or extra.get("deposit_date")
    if deposit_date:
        parts.append(f"déposée le {format_date(deposit_date)}")

    label = status_label(extra.get("political_status") or extra.get("status"))
    if label:
        parts.append(label)

    return " · ".join(parts)


def preavis_citation_line(extra: dict) -> str:
    parts = ["Préavis municipal"]
    number = extra.get("preavis_number")
    if number:
        parts.append(f"N° {number}")
    label = status_label(extra.get("political_status"))
    if label:
        parts.append(label)
    if extra.get("decision_date"):
        parts.append(f"décidé le {format_date(extra['decision_date'])}")
    return " · ".join(parts)


def proces_verbal_citation_line(extra: dict) -> str:
    parts = ["Procès-verbal"]
    if extra.get("pv_number"):
        parts.append(f"N° {extra['pv_number']}")
    if extra.get("session_date"):
        parts.append(f"séance du {format_date(extra['session_date'])}")
    if extra.get("presiding_officer"):
        parts.append(f"présidée par {extra['presiding_officer']}")
    return " · ".join(parts)


def rapport_gestion_citation_line(extra: dict) -> str:
    parts = ["Rapport de gestion"]
    if extra.get("management_year"):
        parts.append(str(extra["management_year"]))
    if extra.get("decision_date"):
        parts.append(f"décidé le {format_date(extra['decision_date'])}")
    return " · ".join(parts)


def rapport_comptes_citation_line(extra: dict) -> str:
    parts = ["Rapport des comptes"]
    if extra.get("fiscal_year"):
        parts.append(str(extra["fiscal_year"]))
    result = format_chf(extra.get("result_surplus_or_deficit"))
    if result is not None:
        kind = "excédent" if extra.get("result_surplus_or_deficit", 0) >= 0 else "déficit"
        parts.append(f"{kind} de {result}")
    return " · ".join(parts)


CATEGORY_METADATA_KEYS = {
    "motion": "motion_metadata",
    "postulat": "postulat_metadata",
    "interpellation": "interpellation_metadata",
    "preavis_municipal": "preavis_metadata",
    "proces_verbal": "minutes_metadata",
    "rapport_gestion": "management_report_metadata",
    "rapport_comptes": "accounts_metadata",
}


def source_citation_line(metadata: dict) -> str | None:
    category = str(metadata.get("category") or "")
    extra_key = CATEGORY_METADATA_KEYS.get(category)
    extra = (metadata.get("additional_metadata") or {}).get(extra_key) or {} if extra_key else {}

    if category in POLITICAL_OBJECT_TYPE_LABELS:
        return political_object_citation_line(metadata, extra)
    if category == "preavis_municipal":
        return preavis_citation_line(extra)
    if category == "proces_verbal":
        return proces_verbal_citation_line(extra)
    if category == "rapport_gestion":
        return rapport_gestion_citation_line(extra)
    if category == "rapport_comptes":
        return rapport_comptes_citation_line(extra)
    return None


def link_source_mentions(text: str, grouped_sources: list[dict]) -> str:
    """Turn a "Source N" mention in the answer body into a link straight to
    that source's actual PDF — not an anchor into the Sources expander,
    since that's collapsed by default and an anchor into hidden content
    wouldn't do anything useful.

    Remove citation-only recap paragraphs before linking them. Otherwise a
    model-generated ``(Source 1, Source 2)`` footer is rendered as the
    unhelpful duplicate line ``(PDF, PDF)`` above the Sources expander.
    """
    if not grouped_sources:
        return text

    citation_only = re.compile(
        r"^\s*(?:[\(\[]\s*)?"
        r"(?:[\(\[]?\s*Source\s+\d+\s*[\)\]]?\s*[,;]?\s*)+"
        r"(?:[\)\]]\s*)?[.!]?\s*$",
        flags=re.IGNORECASE,
    )
    text = "\n".join(
        line for line in text.splitlines()
        if not citation_only.fullmatch(line)
    ).rstrip("\n")

    def replace(match: re.Match) -> str:
        number = int(match.group(1))
        if number < 1 or number > len(grouped_sources):
            return match.group(0)
        metadata = grouped_sources[number - 1]["metadata"]
        url = metadata.get("source_url") or metadata.get("pdf_url") or metadata.get("url") or metadata.get("file_url") or ""
        if not url:
            return match.group(0)
        return f"[PDF]({url})"

    return re.sub(r"\bSource\s+(\d+)\b", replace, text)


def render_sources(results: list[dict], message_index: int, source_blurbs: dict[str, str] | None = None) -> None:
    grouped_sources = group_results_by_document(results)
    if not grouped_sources:
        return
    source_blurbs = source_blurbs or {}

    with st.expander(f"Sources ({len(grouped_sources)})", expanded=False):
        source_lines = []
        for index, source in enumerate(grouped_sources, start=1):
            metadata = source["metadata"]
            title = fix_mojibake(metadata.get("title") or metadata.get("filename") or source.get("relative_text_path", "document"))
            citation_line = source_citation_line(metadata)
            if citation_line is None:
                year = metadata.get("year") or metadata.get("listing_year") or metadata.get("date", "")
                category = metadata.get("category") or metadata.get("doc_type", "")
                citation_line = " / ".join(str(part) for part in (year, category) if part)
            pdf_link = source_link(metadata, "PDF")
            summary_line = f"{citation_line} · {pdf_link}" if citation_line else pdf_link
            blurb = fix_mojibake(source_blurbs.get(str(index), ""))
            blurb_line = f"<br>{blurb}" if blurb else ""
            source_lines.append(
                f'<span id="source-{message_index}-{index}"></span>'
                f"**{index}. {title}**<br>{summary_line}{blurb_line}"
            )
        st.markdown("\n\n".join(source_lines), unsafe_allow_html=True)


@st.dialog("Votre avis sur cette réponse", dismissible=True)
def _feedback_dialog(message_index: int, question: str, answer: str, source_count: int) -> None:
    st.write("Cette réponse vous a-t-elle été utile ?")
    st.caption("Un clic suffit. Votre avis nous aide à améliorer les réponses.")

    def submit(rating: str) -> None:
        record_feedback(question, answer, rating, source_count)
        st.session_state[f"feedback-{message_index}-recorded"] = rating
        st.rerun()

    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("👍👍 Très utile", key=f"feedback-{message_index}-double-up", width="stretch"):
            submit("double_up")
    with col2:
        if st.button("👍 Utile", key=f"feedback-{message_index}-up", width="stretch"):
            submit("up")
    with col3:
        if st.button("👎 Pas utile", key=f"feedback-{message_index}-down", width="stretch"):
            submit("down")

    if st.button(
        "Fermer · Plus tard",
        key=f"feedback-{message_index}-close",
        width="stretch",
        type="tertiary",
    ):
        st.session_state[f"feedback-{message_index}-dismissed"] = True
        st.rerun()


def _pending_feedback_assistant_index() -> int | None:
    """Return the latest answer only when this is a feedback turn.

    Asking after every answer feels like a gate in front of the conversation.
    Instead, ask after every second successful, sourced answer and never show
    the same prompt twice, even if the user closes it with the dialog's X.
    """
    messages = st.session_state.messages
    assistant_count = 0
    candidate_index = None
    for index, message in enumerate(messages):
        if message.get("role") != "assistant" or not message.get("content"):
            continue
        if index == 0 or messages[index - 1].get("role") != "user":
            continue
        if not message.get("results"):
            continue
        assistant_count += 1
        candidate_index = index

    if assistant_count == 0 or assistant_count % 2 != 0 or candidate_index is None:
        return None

    for state_suffix in ("recorded", "dismissed", "prompted"):
        if st.session_state.get(f"feedback-{candidate_index}-{state_suffix}"):
            return None

    return candidate_index


FEEDBACK_DIALOG_DELAY_SECONDS = 8


def render_pending_feedback_dialog() -> None:
    """Shows at most one dismissible feedback dialog per rerun, for every
    second successful answer — never inside the message loop, since
    calling more than one @st.dialog function in the same script run isn't
    supported.

    Waits a few seconds before popping the dialog so there's time to read
    the answer first. Streamlit has no background timer independent of a
    rerun; since elements stream to the browser as the script executes, the
    answer (rendered earlier in this same run, in the message loop above)
    should already be visible while this sleeps. Only sleeps once per
    message — the flag guards against a second full-script rerun landing
    here before the dialog is resolved.
    """
    message_index = _pending_feedback_assistant_index()
    if message_index is None:
        return

    time.sleep(FEEDBACK_DIALOG_DELAY_SECONDS)
    # Mark it before opening: closing via the native X cannot run a callback,
    # so this flag prevents the same prompt from returning on the next rerun.
    st.session_state[f"feedback-{message_index}-prompted"] = True

    messages = st.session_state.messages
    message = messages[message_index]
    question = messages[message_index - 1].get("content", "")
    answer = message.get("content", "")
    source_count = len(group_results_by_document(message.get("results", [])))
    _feedback_dialog(message_index, question, answer, source_count)


SHOW_ADMIN_TABS = admin_tabs_enabled()
if SHOW_ADMIN_TABS:
    chat_tab, eval_tab, documents_tab, about_tab = st.tabs(
        ["Assistant", "Eval", "Documents", "À propos"],
        key="main-navigation",
        on_change="rerun",
    )
else:
    chat_tab, documents_tab, about_tab = st.tabs(
        ["Assistant", "Documents", "À propos"],
        key="main-navigation",
        on_change="rerun",
    )
    eval_tab = None


@st.cache_data(ttl=900, max_entries=128, show_spinner=False)
def cached_answer_question(
    question: str,
    filters_key: tuple[tuple[str, str], ...],
    cache_version: str,
    _on_stage=None,
) -> tuple[str, list[dict], dict]:
    _ = cache_version
    # _on_stage is prefixed with an underscore so st.cache_data excludes it
    # from the cache key (a callback isn't hashable/meaningful for caching
    # identity) — on a cache hit it's simply never called, which is fine
    # since a hit returns near-instantly and needs no progress indicator.
    if not pilot_v2_ready():
        return "La base AI Riviera n'est pas encore indexée. Relance l'indexation depuis l'environnement d'administration.", [], {}

    if agentic_pipeline_enabled():
        answer, results, trace = run_agentic_pipeline(
            question,
            filters=dict(filters_key),
            on_stage=_on_stage,
        )
    else:
        if _on_stage:
            _on_stage("Reformulation de la question...")
        retrieval_question = rewrite_query_with_llm(question) or question
        if _on_stage:
            _on_stage("Recherche dans les documents...")
        candidates = search(retrieval_question, limit=50, filters=dict(filters_key))
        if _on_stage:
            _on_stage("Sélection des passages les plus pertinents...")
        results = rerank_results_with_llm(
            question,
            candidates,
            keep=RERANK_KEEP_LIMIT,
            max_candidates=RERANK_CANDIDATE_LIMIT,
        )
        if _on_stage:
            _on_stage("Rédaction de la réponse...")
        answer, trace = answer_from_sources(question, results[:GENERATION_PASSAGE_LIMIT]), {
            "rerank_candidate_limit": RERANK_CANDIDATE_LIMIT,
            "generation_passage_limit": GENERATION_PASSAGE_LIMIT,
            "generation_passages": min(len(results), GENERATION_PASSAGE_LIMIT),
            "reranked_passages": len(results),
        }

    if trace.get("mode") != "aggregate" and "source_blurbs" not in trace:
        # Aggregate answers are synthetic rows with no real passage text —
        # nothing meaningful to summarize, and they're already complete
        # (authors shown inline) without a blurb. The agentic path already
        # computes source_blurbs itself (in parallel with verification), so
        # this only runs for the non-agentic path.
        if _on_stage:
            _on_stage("Résumé des sources...")
        trace["source_blurbs"] = source_blurbs_with_fallback(group_results_by_document(results))
    return answer, results, trace


@st.cache_data(ttl=120, show_spinner=False)
def cached_index_ready() -> bool:
    return pilot_v2_ready()


@st.cache_data(ttl=300, max_entries=128, show_spinner=False)
def cached_browse_documents(
    query: str,
    cities: tuple[str, ...],
    categories: tuple[str, ...],
    year_from: str,
    year_to: str,
) -> list[dict]:
    return browse_documents(
        query=query,
        cities=cities,
        categories=categories,
        year_from=year_from,
        year_to=year_to,
    )


def answer_question(
    question: str,
    messages: list[dict] | None = None,
    on_stage=None,
) -> tuple[str, list[dict], dict]:
    if not ensure_index_ready():
        return "La base AI Riviera n'est pas encore indexée. Relance l'indexation depuis l'environnement d'administration.", [], {}

    effective_question = contextualize_question(question, messages or [])
    started_at = time.perf_counter()
    try:
        answer, results, trace = cached_answer_question(
            effective_question,
            cacheable_filters(current_filters()),
            ANSWER_CACHE_VERSION,
            _on_stage=on_stage,
        )
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        record_interaction(
            question,
            status="ok",
            duration_ms=duration_ms,
            source_count=len(group_results_by_document(results)) if results else 0,
            answer_chars=len(answer),
        )
        return answer, results, trace
    except Exception as exc:
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        record_diagnostic("ui", "Question answering failed", exc, question=question[:300])
        record_interaction(question, status="error", duration_ms=duration_ms, error=repr(exc))
        return USER_ERROR_MESSAGE, [], {}


def queue_question(question: str) -> None:
    warning = filter_guard_message(
        question,
        selected_city=st.session_state.get("search_city", "all"),
        selected_year=st.session_state.get("search_year", ALL_YEARS),
        selected_document_type=st.session_state.get(
            "search_document_type", ALL_DOCUMENT_TYPES
        ),
    )
    if warning:
        st.session_state.filter_warning = warning
        return
    st.session_state.filter_warning = None
    st.session_state.messages.append({"role": "user", "content": question})
    st.session_state.pending_question = question


def clear_filter_warning() -> None:
    st.session_state.filter_warning = None


def reset_document_browser_page() -> None:
    st.session_state.document_browser_page = 1


def clear_document_browser_filters() -> None:
    st.session_state.document_browser_query = ""
    st.session_state.document_browser_cities = []
    st.session_state.document_browser_types = []
    st.session_state.document_browser_year_from = ""
    st.session_state.document_browser_year_to = ""
    st.session_state.document_browser_page = 1


def document_browser_year(value: str) -> str | None:
    value = str(value or "").strip()
    if not value:
        return ""
    return value if re.fullmatch(r"\d{4}", value) else None


def render_document_browser_result(document: dict) -> None:
    title = html.escape(fix_mojibake(str(document.get("title") or "Document")))
    commune = html.escape(
        fix_mojibake(str(document.get("commune") or "Commune non précisée"))
    )
    category = DOCUMENT_BROWSER_CATEGORY_LABELS.get(
        str(document.get("category") or ""),
        str(document.get("category") or "Document").replace("_", " ").title(),
    )
    category = html.escape(fix_mojibake(category))
    authors = [
        fix_mojibake(str(author))
        for author in (document.get("authors") or [])
        if str(author).strip()
    ]
    date = str(document.get("document_date") or "").strip()
    year = str(document.get("year") or "").strip()
    details = [commune, category]
    if authors:
        details.append(html.escape(", ".join(authors)))
    if date:
        details.append(html.escape(format_date(date)))
    elif year:
        details.append(html.escape(year))

    source_url = str(document.get("source_url") or "").strip()
    if re.match(r"^https?://", source_url, flags=re.IGNORECASE):
        link = (
            f'<a href="{html.escape(source_url, quote=True)}" '
            'target="_blank" rel="noopener noreferrer">PDF ↗</a>'
        )
    else:
        link = '<span class="air-browser-no-link">Lien indisponible</span>'

    st.markdown(
        f"""
        <article class="air-browser-result">
            <div>
                <p>{" · ".join(details)}</p>
                <h3>{title}</h3>
            </div>
            {link}
        </article>
        """,
        unsafe_allow_html=True,
    )


def render_documents_browser() -> None:
    st.subheader("Explorer les documents")
    st.caption(
        "Recherchez un titre, un auteur ou un mot-clé, puis combinez plusieurs "
        "communes, types de documents et années."
    )

    with st.container(key="document-browser-search"):
        browser_query = st.text_input(
            "Recherche par mots-clés",
            placeholder="Titre, auteur ou sujet…",
            key="document_browser_query",
            on_change=reset_document_browser_page,
        )

    filter_column, results_column = st.columns([1, 3], gap="large")
    browser_filters_valid = True

    with filter_column:
        with st.container(key="document-browser-filters"):
            st.markdown("### Filtres")
            browser_city_options = [
                municipality.label
                for municipality in MUNICIPALITIES.values()
                if municipality.search_enabled
            ]
            selected_browser_cities = st.multiselect(
                "Communes",
                options=browser_city_options,
                placeholder="Toutes les communes",
                key="document_browser_cities",
                on_change=reset_document_browser_page,
            )

            browser_type_options = available_browser_document_type_labels(
                selected_browser_cities
            )
            stored_browser_types = [
                label
                for label in st.session_state.get(
                    "document_browser_types", []
                )
                if label in browser_type_options
            ]
            if stored_browser_types != st.session_state.get(
                "document_browser_types", []
            ):
                st.session_state.document_browser_types = stored_browser_types
            selected_browser_types = st.multiselect(
                "Types de document",
                options=browser_type_options,
                placeholder="Tous les types",
                key="document_browser_types",
                on_change=reset_document_browser_page,
            )

            year_columns = st.columns(2)
            with year_columns[0]:
                raw_year_from = st.text_input(
                    "De l’année",
                    placeholder="2021",
                    max_chars=4,
                    key="document_browser_year_from",
                    on_change=reset_document_browser_page,
                )
            with year_columns[1]:
                raw_year_to = st.text_input(
                    "À l’année",
                    placeholder="2026",
                    max_chars=4,
                    key="document_browser_year_to",
                    on_change=reset_document_browser_page,
                )

            year_from = document_browser_year(raw_year_from)
            year_to = document_browser_year(raw_year_to)
            if year_from is None or year_to is None:
                st.error("Utilisez une année à quatre chiffres, par exemple 2025.")
                browser_filters_valid = False
            elif year_from and year_to and int(year_from) > int(year_to):
                st.error("L’année de début doit précéder l’année de fin.")
                browser_filters_valid = False

            st.button(
                "Effacer les filtres",
                key="clear-document-browser-filters",
                on_click=clear_document_browser_filters,
                width="stretch",
            )
            st.caption(
                "Laissez les années vides pour inclure toutes les archives."
            )

    browser_rows: list[dict] = []
    browser_error = ""
    index_is_ready = browser_filters_valid and cached_index_ready()
    if index_is_ready:
        selected_categories = tuple(
            CATEGORY_MAP[DOCUMENT_TYPE_OPTIONS[label]]
            for label in selected_browser_types
        )
        try:
            browser_rows = cached_browse_documents(
                browser_query.strip(),
                tuple(selected_browser_cities),
                selected_categories,
                year_from or "",
                year_to or "",
            )
        except Exception as exc:
            record_diagnostic(
                "document_browser",
                "Manual document browser query failed",
                exc,
            )
            browser_error = (
                "La liste des documents est temporairement indisponible."
            )

    with results_column:
        with st.container(key="document-browser-results"):
            if not browser_filters_valid:
                st.info("Corrigez la période pour afficher les documents.")
            elif browser_error:
                st.error(browser_error)
            elif not index_is_ready:
                st.info(
                    "La base documentaire n’est pas disponible dans cet "
                    "environnement."
                )
            else:
                total_documents = len(browser_rows)
                page_size = 25
                page_count = max(1, math.ceil(total_documents / page_size))
                current_page = min(
                    int(st.session_state.get("document_browser_page", 1)),
                    page_count,
                )
                st.session_state.document_browser_page = current_page

                heading_columns = st.columns([3, 1])
                with heading_columns[0]:
                    st.markdown(
                        f"**{total_documents} document"
                        f"{'' if total_documents == 1 else 's'}**"
                    )
                with heading_columns[1]:
                    if page_count > 1:
                        current_page = int(
                            st.number_input(
                                "Page",
                                min_value=1,
                                max_value=page_count,
                                step=1,
                                key="document_browser_page",
                            )
                        )

                page_start = (current_page - 1) * page_size
                page_rows = browser_rows[
                    page_start : page_start + page_size
                ]
                if not page_rows:
                    st.info("Aucun document ne correspond à ces filtres.")
                for browser_document in page_rows:
                    render_document_browser_result(browser_document)

                if total_documents:
                    st.caption(
                        f"Affichage {page_start + 1}–"
                        f"{min(page_start + page_size, total_documents)} "
                        f"sur {total_documents} documents principaux."
                    )


with chat_tab:
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "pending_question" not in st.session_state:
        st.session_state.pending_question = None
    if "filter_warning" not in st.session_state:
        st.session_state.filter_warning = None

    is_home_view = (
        not st.session_state.messages
        and st.session_state.pending_question is None
    )
    selected_city = st.session_state.get("search_city", "all")
    selected_year = st.session_state.get("search_year", ALL_YEARS)
    selected_document_type = st.session_state.get(
        "search_document_type", ALL_DOCUMENT_TYPES
    )
    active_filter_labels = []
    if selected_city != "all":
        active_filter_labels.append(CITY_OPTIONS[selected_city])
    if selected_year != ALL_YEARS:
        active_filter_labels.append(selected_year)
    if selected_document_type != ALL_DOCUMENT_TYPES:
        active_filter_labels.append(selected_document_type)
    filter_expander_label = "Affiner la recherche (facultatif)"
    if active_filter_labels:
        filter_expander_label += " — " + " · ".join(active_filter_labels)

    city_available = (
        selected_city == "all" or selected_city in SEARCH_ENABLED_CITIES
    )
    question = None
    if is_home_view:
        st.markdown(
            """
            <div class="air-home-hero">
                <h1>Que souhaitez-vous savoir&nbsp;?</h1>
                <p>Posez une question sur les documents publics de la Riviera vaudoise.</p>
            </div>
            """,
            unsafe_allow_html=True,
        )
        with st.container(key="air-home-search"):
            question = st.chat_input(
                "Posez une question sur les documents...",
                disabled=not city_available,
                key="home-question-input",
            )

    filter_container = st.container(key="air-search-filters")
    with filter_container.expander(filter_expander_label, expanded=False):
        st.caption(
            "Plus votre question et vos filtres sont précis, plus la recherche est rapide."
        )
        filter_columns = st.columns(3)
        with filter_columns[0]:
            selected_city = st.selectbox(
                "Institution",
                options=list(CITY_OPTIONS),
                format_func=CITY_OPTIONS.get,
                key="search_city",
                disabled=st.session_state.pending_question is not None,
                on_change=clear_filter_warning,
            )
            selected_institution = next(
                (
                    municipality
                    for municipality in MUNICIPALITIES.values()
                    if municipality.label == selected_city
                ),
                None,
            )
            if selected_institution and selected_institution.search_scope:
                st.caption(
                    f"Périmètre actuel : {selected_institution.search_scope}."
                )
        with filter_columns[1]:
            selected_year = st.selectbox(
                "Année",
                options=YEAR_OPTIONS,
                key="search_year",
                disabled=st.session_state.pending_question is not None,
                on_change=clear_filter_warning,
            )
        with filter_columns[2]:
            document_type_labels = available_document_type_labels(
                selected_city
            )
            if (
                st.session_state.get(
                    "search_document_type", ALL_DOCUMENT_TYPES
                )
                not in document_type_labels
            ):
                st.session_state.search_document_type = (
                    ALL_DOCUMENT_TYPES
                )
            selected_document_type = st.selectbox(
                "Type de document",
                options=document_type_labels,
                key="search_document_type",
                disabled=st.session_state.pending_question is not None,
                on_change=clear_filter_warning,
            )

    city_available = selected_city == "all" or selected_city in SEARCH_ENABLED_CITIES
    if not city_available:
        st.caption(
            f"*{selected_city} sera disponible prochainement. Sélectionnez Toutes "
            "ou La Tour-de-Peilz pour lancer une recherche.*"
        )

    if st.session_state.filter_warning:
        st.warning(st.session_state.filter_warning, icon="⚠️")

    suggestions_slot = st.empty()
    if not st.session_state.messages and st.session_state.pending_question is None:
        with suggestions_slot.container():
            st.markdown("**Questions pour commencer**")
            for row_start in range(0, len(SUGGESTED_QUESTIONS), 2):
                columns = st.columns(2)
                for offset, question_example in enumerate(SUGGESTED_QUESTIONS[row_start : row_start + 2]):
                    with columns[offset]:
                        st.button(
                            question_example,
                            key=f"suggested-question-{row_start + offset}",
                            on_click=queue_question,
                            args=(question_example,),
                            width="stretch",
                            disabled=not city_available,
                        )
    else:
        suggestions_slot.empty()

    for message_index, message in enumerate(st.session_state.messages):
        avatar = ":material/person:" if message["role"] == "user" else ":material/find_in_page:"
        with st.chat_message(message["role"], avatar=avatar):
            results = message.get("results", [])
            grouped_sources = group_results_by_document(results) if results else []
            st.markdown(link_source_mentions(fix_mojibake(message["content"]), grouped_sources))
            if message["role"] == "assistant":
                trace = message.get("trace", {})
                render_sources(results, message_index, trace.get("source_blurbs"))

    render_pending_feedback_dialog()

    if st.session_state.pending_question:
        suggestions_slot.empty()
        pending_question = st.session_state.pending_question
        loading_placeholder = st.empty()

        def render_loading(text: str) -> None:
            loading_placeholder.markdown(
                f"""
                <div class="air-loading" aria-live="polite" aria-label="Recherche en cours">
                    <span class="air-loading-docs" aria-hidden="true">
                        <span class="air-loading-page"></span>
                        <span class="air-loading-page"></span>
                        <span class="air-loading-page"></span>
                    </span>
                    <span class="air-loading-text">{text}</span>
                </div>
                """,
                unsafe_allow_html=True,
            )

        if agentic_pipeline_enabled() and guess_loading_complexity(pending_question) == "complex":
            render_loading("Recherche approfondie, comparaison de plusieurs sources...")
        else:
            render_loading("Lecture des sources...")

        # Updated live as the pipeline actually progresses (see app/agent.py's
        # on_stage callbacks) rather than a single static guess — so a
        # genuinely harder question visibly *looks* like it's doing more,
        # instead of leaving an unexplained long wait on the same message.
        answer, results, trace = answer_question(pending_question, st.session_state.messages, on_stage=render_loading)

        st.session_state.messages.append({"role": "assistant", "content": answer, "results": results, "trace": trace})
        st.session_state.pending_question = None
        st.rerun()

    if not is_home_view:
        question = st.chat_input(
            "Posez une question sur les documents...",
            disabled=(
                st.session_state.pending_question is not None
                or not city_available
            ),
            key="conversation-question-input",
        )
    if question and st.session_state.pending_question is None:
        queue_question(question)
        st.rerun()

if SHOW_ADMIN_TABS and eval_tab is not None:
    with eval_tab:
        st.markdown(
            "Questions fixes pour vérifier si les changements de données, metadata, embeddings ou recherche "
            "améliorent vraiment les réponses."
        )
    
        eval_questions = load_eval_questions()
        if "eval_runs" not in st.session_state:
            st.session_state.eval_runs = []
    
        eval_rows = [
            {
                "id": item["id"],
                "question": item["question"],
                "difficulty": item.get("difficulty", ""),
                "tags": ", ".join(item.get("tags", [])),
            }
            for item in eval_questions
        ]
        st.dataframe(eval_rows, width="stretch", hide_index=True)
    
        if st.session_state.eval_runs:
            recent_runs = st.session_state.eval_runs[: len(eval_questions)]
            ok_count = sum(1 for run in recent_runs if run.get("retrieval_ok"))
            avg_sources = sum(len(group_results_by_document(run.get("results", []))) for run in recent_runs) / max(len(recent_runs), 1)
            col_ok, col_total, col_sources = st.columns(3)
            col_ok.metric("Derniers runs OK", f"{ok_count}/{len(recent_runs)}")
            col_total.metric("Runs en mémoire", str(len(st.session_state.eval_runs)))
            col_sources.metric("Sources moyennes", f"{avg_sources:.1f}")
    
        def run_eval_question(item: dict) -> None:
            answer, results, trace = answer_question(item["question"])
            st.session_state.eval_runs.insert(
                0,
                {
                    "id": item["id"],
                    "question": item["question"],
                    "expected_answer": item.get("expected_answer", ""),
                    "expected_sources": item.get("expected_sources", []),
                    "answer": answer,
                    "results": results,
                    "retrieval_ok": retrieval_hit(results, item.get("expected_sources", [])),
                },
            )
    
        col_run_all, col_clear, col_cache = st.columns([1, 1, 1])
        with col_run_all:
            if st.button(f"Lancer les {len(eval_questions)} questions"):
                for eval_question in eval_questions:
                    run_eval_question(eval_question)
                st.rerun()
        with col_clear:
            if st.button("Vider l'historique eval"):
                st.session_state.eval_runs = []
                st.rerun()
        with col_cache:
            if st.button("Vider le cache"):
                st.cache_data.clear()
                st.rerun()
    
        st.subheader("Lancer une question")
        for item in eval_questions:
            if st.button(f"{item['id']} - {item['question']}", key=f"run-{item['id']}"):
                run_eval_question(item)
                st.rerun()
    
        st.subheader("Historique")
        if not st.session_state.eval_runs:
            st.caption("Aucun run pour l'instant.")
        for run_index, run in enumerate(st.session_state.eval_runs):
            status = "OK" if run["retrieval_ok"] else "A vérifier"
            with st.expander(f"{run['id']} - {status} - {run['question']}"):
                st.markdown("**Réponse attendue**")
                st.write(run["expected_answer"])
                st.markdown("**Réponse obtenue**")
                st.write(fix_mojibake(run["answer"]))
                st.markdown("**Sources attendues**")
                st.code("\n".join(run["expected_sources"]) or "Aucune source attendue définie", language="text")
                render_sources(run.get("results", []), 1000 + run_index)
    
        st.subheader("Feedback des réponses")
        feedback_rows = recent_feedback(100)
        if feedback_rows:
            rating_emoji = {"double_up": "👍👍", "up": "👍", "down": "👎"}
            double_up_count = sum(1 for row in feedback_rows if row["rating"] == "double_up")
            up_count = sum(1 for row in feedback_rows if row["rating"] == "up")
            down_count = sum(1 for row in feedback_rows if row["rating"] == "down")
            col_double_up, col_up, col_down = st.columns(3)
            col_double_up.metric("👍👍", double_up_count)
            col_up.metric("👍", up_count)
            col_down.metric("👎", down_count)
            st.dataframe(
                [
                    {
                        "date": row["created_at"],
                        "note": rating_emoji.get(row["rating"], row["rating"]),
                        "question": fix_mojibake(row["question"]),
                        "réponse": fix_mojibake(row["answer"])[:300],
                        "sources": row["source_count"],
                    }
                    for row in feedback_rows
                ],
                width="stretch",
                hide_index=True,
            )
        else:
            st.caption("Aucun retour utilisateur pour l'instant.")

        st.subheader("Diagnostics")
        interactions = list(reversed(recent_interactions(12)))
        if interactions:
            st.dataframe(interactions, width="stretch", hide_index=True)
        else:
            st.caption("Aucune question journalisée pour l'instant.")
    
        diagnostics = list(reversed(recent_diagnostics(8)))
        if diagnostics:
            with st.expander("Dernières erreurs techniques"):
                st.dataframe(diagnostics, width="stretch", hide_index=True)
    
with documents_tab:
    if documents_tab.open:
        render_documents_browser()

with about_tab:
    st.subheader("À quoi sert AI Riviera ?")
    intro_col, image_col = st.columns([1.8, 1])
    with intro_col:
        st.write(
            "AI Riviera facilite la recherche dans les documents publics de la "
            "Riviera vaudoise, dont ceux de La Tour-de-Peilz, Vevey et Montreux. "
            "Une question en langage courant permet de retrouver un objet politique, "
            "son auteur, sa date, son éventuelle réponse et la source officielle "
            "correspondante."
        )
        st.write(
            "Le projet est à but non lucratif. Il ne remplace ni les sites "
            "officiels ni le travail des administrations : il propose un accès "
            "régional centralisé aux fichiers publics et rend les archives plus "
            "faciles à explorer, à relier et à vérifier. La mutualisation de "
            "l'infrastructure et des méthodes permet également de réaliser des "
            "économies d'échelle entre les communes."
        )
        st.markdown(
            "Le code est open source et consultable sur "
            "[GitHub](https://github.com/Mariutoto/AI-Riviera-2)."
        )
    with image_col:
        if LANDSCAPE_IMAGE_PATH.exists():
            st.image(
                str(LANDSCAPE_IMAGE_PATH),
                caption="La Riviera vaudoise",
                width=320,
            )

    st.subheader("À qui cela peut être utile ?")
    st.markdown(
        """
        <ul class="air-about-list">
            <li>
                <strong>Habitants et médias</strong>
                Poser une question sans connaître le nom exact du document et accéder directement aux sources.
            </li>
            <li>
                <strong>Conseillères et conseillers</strong>
                Retrouver les interventions précédentes, les engagements annoncés et les réponses déjà données.
            </li>
            <li>
                <strong>Administration communale</strong>
                Suivre les objets en attente et retrouver une réponse même lorsqu’elle arrive plusieurs années après le dépôt.
            </li>
            <li>
                <strong>Mémoire institutionnelle</strong>
                Relier une interpellation, un postulat ou une motion à ses réponses, rapports et décisions dans le temps.
            </li>
            <li>
                <strong>Sujets récurrents</strong>
                Repérer des titres proches, des doublons possibles ou des questions qui reviennent sous une autre formulation.
            </li>
            <li>
                <strong>Contrôle et transparence</strong>
                Comparer les dates, vérifier si une réponse existe et ouvrir la publication officielle utilisée.
            </li>
            <li>
                <strong>Accès régional centralisé</strong>
                Consulter depuis un même site les fichiers publics disponibles dans plusieurs communes de la Riviera.
            </li>
            <li>
                <strong>Économies d’échelle</strong>
                Mutualiser l’indexation, la recherche et les outils techniques plutôt que de reproduire le même travail dans chaque commune.
            </li>
        </ul>
        """,
        unsafe_allow_html=True,
    )

    st.subheader("Comment ça marche ?")
    st.markdown(
        """
        <ul class="air-about-list">
            <li>
                <strong>1. Question</strong>
                Vous écrivez une question simple, avec un titre, un auteur ou une date si vous les connaissez.
            </li>
            <li>
                <strong>2. Vérification</strong>
                L’application consulte les métadonnées fiables : commune, auteurs, dates, catégories et relations entre documents.
            </li>
            <li>
                <strong>3. Recherche</strong>
                Elle recherche ensuite les passages utiles dans les PDF et les transcriptions officielles indexées.
            </li>
            <li>
                <strong>4. Réponse sourcée</strong>
                Elle présente une réponse concise et les liens officiels nécessaires pour la contrôler.
            </li>
        </ul>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        """
        <div class="air-about-note">
            <strong>À garder en tête :</strong> AI Riviera est une aide à la recherche.
            Une absence de résultat ne prouve pas qu’un document n’existe pas, et deux
            sujets proches ne sont pas forcément de vrais doublons. Pour une décision,
            une citation officielle ou une interprétation juridique, il faut toujours
            contrôler la source officielle affichée dans la réponse.
        </div>
        """,
        unsafe_allow_html=True,
    )
