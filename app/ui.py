import sys
import os
from pathlib import Path
import re
import time

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

ASSETS_DIR = PROJECT_ROOT / "assets"
LANDSCAPE_IMAGE_PATH = ASSETS_DIR / "riviera-vaudoise-landscape.jpg"

from app.agent import run_agentic_pipeline
from app.answer import answer_from_sources, get_secret, rerank_results_with_llm, rewrite_query_with_llm, summarize_sources_with_llm
from app.diagnostics import record_diagnostic, record_interaction, recent_diagnostics, recent_interactions
from app.eval_set import load_eval_questions, retrieval_hit
from app.feedback import record_feedback, recent_feedback
from app.pilot_v2_store import ready as pilot_v2_ready
from app.retrieval import search
from app.text_cleaning import fix_mojibake, format_date

SUGGESTED_QUESTIONS = [
    "Quelles interpellations ont reçu une réponse en 2025 ?",
    "Quelle motion de 2026 a été renvoyée à la Municipalité ?",
    "Quels objets politiques parlent de mobilité ou de bus dans cette législature ?",
    "Quels postulats ont été déposés en 2024 ?",
    "Qui a déposé l'interpellation sur l'affichage politique en 2026 ?",
    "Que dit l'article 96 du règlement du Conseil communal ?",
]

USER_ERROR_MESSAGE = (
    "Désolé, la recherche a rencontré un problème technique. "
    "La question a été journalisée pour diagnostic; tu peux réessayer dans un instant."
)

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

st.title("AI Riviera")
st.caption("Assistant de recherche sur les documents publics de La Tour-de-Peilz (législature 2021-2026) - projet à but non lucratif")

st.markdown(
    """
    <style>
    [data-testid="stSidebar"], [data-testid="collapsedControl"] {
        display: none;
    }

    div[data-testid="stButton"] > button {
        background: #f3f7fd;
        border: 1px solid #c9d8ef;
        color: #253247;
        min-height: 3rem;
        text-align: left;
    }

    div[data-testid="stButton"] > button:hover {
        background: #e8f1ff;
        border-color: #8eb0df;
        color: #1f2d42;
    }

    .air-loading {
        align-items: center;
        background: #f3f6fa;
        border: 1px solid #e2e7ef;
        border-radius: 0.45rem;
        color: #3f4652;
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
        border: 2px solid #3a8f6b;
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

    .air-guide {
        background: #f7f8fa;
        border: 1px solid #e2e6ec;
        border-radius: 0.45rem;
        color: #3e4652;
        font-size: 0.92rem;
        line-height: 1.55;
        margin: 0.5rem 0 1rem;
        padding: 0.75rem 0.95rem;
    }

    .air-guide strong {
        color: #253247;
    }

    .air-about-diagram {
        align-items: stretch;
        display: grid;
        gap: 0.75rem;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        margin: 1rem 0 1.25rem;
    }

    .air-about-step {
        background: #f7f8fa;
        border: 1px solid #dfe5ec;
        border-radius: 0.45rem;
        color: #303846;
        min-height: 7.2rem;
        padding: 0.85rem;
    }

    .air-about-step strong {
        color: #1f2d42;
        display: block;
        font-size: 0.98rem;
        margin-bottom: 0.35rem;
    }

    .air-about-step span {
        color: #566171;
        display: block;
        font-size: 0.9rem;
        line-height: 1.45;
    }

    .air-about-note {
        background: #fff8ea;
        border: 1px solid #ead7a9;
        border-radius: 0.45rem;
        color: #4a3b1d;
        margin-top: 1rem;
        padding: 0.8rem 0.95rem;
    }

    @media (max-width: 900px) {
        .air-about-diagram {
            grid-template-columns: repeat(2, minmax(0, 1fr));
        }
    }

    @media (max-width: 560px) {
        .air-about-diagram {
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

def current_filters() -> dict | None:
    return {"city": "La Tour-de-Peilz"}


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
    """
    if not grouped_sources:
        return text

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


@st.dialog("Votre avis sur cette réponse", dismissible=False)
def _feedback_dialog(message_index: int, question: str, answer: str, source_count: int) -> None:
    st.write("Cette réponse vous a-t-elle été utile ?")

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


def _latest_unrated_assistant_index() -> int | None:
    messages = st.session_state.messages
    for index in range(len(messages) - 1, 0, -1):
        message = messages[index]
        if message.get("role") != "assistant" or not message.get("content"):
            continue
        if messages[index - 1].get("role") != "user":
            continue
        if st.session_state.get(f"feedback-{index}-recorded"):
            continue
        return index
    return None


FEEDBACK_DIALOG_DELAY_SECONDS = 4


def render_pending_feedback_dialog() -> None:
    """Shows at most one non-dismissible feedback dialog per rerun, for the
    most recent unrated answer — never inside the message loop, since
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
    message_index = _latest_unrated_assistant_index()
    if message_index is None:
        return

    delay_done_key = f"feedback-{message_index}-delay-done"
    if not st.session_state.get(delay_done_key):
        st.session_state[delay_done_key] = True
        time.sleep(FEEDBACK_DIALOG_DELAY_SECONDS)

    messages = st.session_state.messages
    message = messages[message_index]
    question = messages[message_index - 1].get("content", "")
    answer = message.get("content", "")
    source_count = len(group_results_by_document(message.get("results", [])))
    _feedback_dialog(message_index, question, answer, source_count)


def render_trace(trace: dict) -> None:
    if not trace:
        return

    if trace.get("mode") == "aggregate":
        st.caption("🔢 Comptage exact sur les métadonnées de la base, pas une estimation sur des passages retrouvés.")
    if trace.get("verification_claims"):
        st.caption("✓ vérifié — une ou plusieurs affirmations non sourcées ont été corrigées avant affichage.")
    if trace.get("budget_exceeded"):
        st.caption("⏱️ Vérification sautée: le budget de temps de la recherche était déjà épuisé.")

    if not any([
        trace.get("mode") in {"multi", "aggregate"},
        trace.get("relance"),
        trace.get("verification_claims"),
        trace.get("budget_exceeded"),
    ]):
        return

    with st.expander("🔎 Comment cette réponse a été construite"):
        st.write(f"Complexité détectée: {trace.get('complexity', 'n/a')}")
        st.write(f"Mode de recherche: {trace.get('mode', 'n/a')}")
        if trace.get("duration_seconds") is not None:
            budget = trace.get("budget_seconds", "n/a")
            st.write(f"Temps total: {trace['duration_seconds']}s (budget: {budget}s)")
        if trace.get("mode") == "aggregate":
            st.write(
                "Détecté comme une question de comptage/énumération: réponse calculée directement "
                "à partir des métadonnées (auteurs, année, type de document), sans passer par une "
                "recherche sémantique ni un modèle de langage — donc pas de risque de sous-comptage."
            )
        if trace.get("relance"):
            st.write("Une recherche complémentaire a été relancée car les premiers résultats étaient faibles.")
        if trace.get("cross_reference_authors"):
            st.write("Auteurs communs trouvés entre les sous-recherches: " + ", ".join(trace["cross_reference_authors"]))
        elif trace.get("mode") == "multi":
            st.write("Aucun auteur commun trouvé entre les sous-recherches.")
        if trace.get("budget_exceeded"):
            st.write("Le budget de temps était dépassé avant la vérification: elle a été sautée pour ne pas rallonger encore la réponse.")
        if trace.get("verification_claims"):
            st.write("Affirmations signalées puis corrigées avant affichage:")
            for claim in trace["verification_claims"]:
                st.write(f"- {claim}")


SHOW_ADMIN_TABS = admin_tabs_enabled()
if SHOW_ADMIN_TABS:
    chat_tab, eval_tab, about_tab = st.tabs(["Assistant", "Eval", "À propos"])
else:
    chat_tab, about_tab = st.tabs(["Assistant", "À propos"])
    eval_tab = None


@st.cache_data(ttl=900, max_entries=128, show_spinner=False)
def cached_answer_question(
    question: str,
    filters_key: tuple[tuple[str, str], ...],
    _on_stage=None,
) -> tuple[str, list[dict], dict]:
    # _on_stage is prefixed with an underscore so st.cache_data excludes it
    # from the cache key (a callback isn't hashable/meaningful for caching
    # identity) — on a cache hit it's simply never called, which is fine
    # since a hit returns near-instantly and needs no progress indicator.
    if not pilot_v2_ready():
        return "La base AI Riviera n'est pas encore indexée. Relance l'indexation depuis l'environnement d'administration.", [], {}

    if agentic_pipeline_enabled():
        answer, results, trace = run_agentic_pipeline(question, on_stage=_on_stage)
    else:
        if _on_stage:
            _on_stage("Reformulation de la question...")
        retrieval_question = rewrite_query_with_llm(question) or question
        if _on_stage:
            _on_stage("Recherche dans les documents...")
        candidates = search(retrieval_question, limit=50, filters=dict(filters_key))
        if _on_stage:
            _on_stage("Sélection des passages les plus pertinents...")
        results = rerank_results_with_llm(question, candidates, keep=30, max_candidates=30)
        if _on_stage:
            _on_stage("Rédaction de la réponse...")
        answer, trace = answer_from_sources(question, results), {}

    if trace.get("mode") != "aggregate":
        # Aggregate answers are synthetic rows with no real passage text —
        # nothing meaningful to summarize, and they're already complete
        # (authors shown inline) without a blurb.
        if _on_stage:
            _on_stage("Résumé des sources...")
        trace["source_blurbs"] = summarize_sources_with_llm(group_results_by_document(results))
    return answer, results, trace


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
        answer, results, trace = cached_answer_question(effective_question, cacheable_filters(current_filters()), on_stage)
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
    st.session_state.messages.append({"role": "user", "content": question})
    st.session_state.pending_question = question

with chat_tab:
    st.markdown(
        "Pose une question et l'assistant cherche dans les documents publics "
        "indexés, puis répond avec les sources utilisées."
    )

    st.markdown(
        """
        <div class="air-guide">
            <strong>Conseils rapides</strong>:
            mettez les titres exacts entre guillemets,
            ajoutez l'ann&eacute;e si vous la connaissez,
            et pr&eacute;cisez le type d'objet quand c'est utile
            (motion, postulat, interpellation, pr&eacute;avis, article).
            Exemple: Qui a d&eacute;pos&eacute; l'interpellation
            "Que pr&eacute;voit la Poste pour Notre Poste" en 2024 ?
        </div>
        """,
        unsafe_allow_html=True,
    )

    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "pending_question" not in st.session_state:
        st.session_state.pending_question = None

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
                render_trace(trace)

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

    question = st.chat_input("Pose une question sur les documents...", disabled=st.session_state.pending_question is not None)
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
    
with about_tab:
    st.subheader("À quoi sert AI Riviera ?")
    intro_col, image_col = st.columns([1.8, 1])
    with intro_col:
        st.write(
            "AI Riviera aide à retrouver plus vite des informations dans les documents publics "
            "de La Tour-de-Peilz. On peut poser une question en langage normal, par exemple sur "
            "une interpellation, un article du règlement, un préavis ou un thème discuté au Conseil communal."
        )
        st.write(
            "Le projet est à but non lucratif. Son rôle n'est pas de remplacer les documents officiels, "
            "mais de rendre leur consultation plus simple, plus rapide et plus vérifiable."
        )
        st.markdown(
            "Le code est open source et consultable sur "
            "[GitHub](https://github.com/Mariutoto/AI-Riviera-2)."
        )
    with image_col:
        if LANDSCAPE_IMAGE_PATH.exists():
            st.image(str(LANDSCAPE_IMAGE_PATH), caption="La Riviera vaudoise", width=320)

    st.subheader("Comment ça marche ?")
    st.markdown(
        """
        <div class="air-about-diagram">
            <div class="air-about-step">
                <strong>1. Question</strong>
                <span>Vous écrivez une question simple, avec un titre ou une date si vous les connaissez.</span>
            </div>
            <div class="air-about-step">
                <strong>2. Vérification</strong>
                <span>L'application regarde d'abord les données fiables: articles, auteurs, dates, objets politiques.</span>
            </div>
            <div class="air-about-step">
                <strong>3. Recherche</strong>
                <span>Si besoin, elle cherche ensuite les passages utiles dans les PDF et textes indexés.</span>
            </div>
            <div class="air-about-step">
                <strong>4. Réponse</strong>
                <span>Elle rédige une réponse courte et affiche les sources pour pouvoir contrôler.</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.subheader("Ce que contient la base")
    st.write(
        "La base couvre la législature 2021-2026 avec des documents publics comme les ordres du jour, "
        "procès-verbaux, motions, postulats, interpellations, réponses, préavis municipaux, budgets, "
        "comptes et rapports de gestion."
    )

    st.subheader("Ce que l'outil sait bien faire")
    st.markdown(
        """
- retrouver ce que dit un article du règlement;
- identifier qui a déposé une motion, un postulat ou une interpellation;
- retrouver l'année, le titre et les sources d'un objet politique;
- chercher des passages sur un thème comme la mobilité, les finances ou les travaux.
"""
    )

    st.markdown(
        """
        <div class="air-about-note">
            <strong>À garder en tête:</strong> AI Riviera est une aide à la recherche.
            Pour une décision, une citation officielle ou une interprétation juridique,
            il faut toujours vérifier le PDF source affiché dans la réponse.
        </div>
        """,
        unsafe_allow_html=True,
    )
