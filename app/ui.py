import sys
from pathlib import Path
import re
import time

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.answer import answer_from_sources
from app.diagnostics import record_diagnostic, record_interaction, recent_diagnostics, recent_interactions
from app.eval_set import load_eval_questions, retrieval_hit
from app.opensearch_store import ready as opensearch_ready
from app.postgres_store import ready as postgres_ready
from app.retrieval import search
from app.structured import answer_structured_question
from app.text_cleaning import fix_mojibake

SUGGESTED_QUESTIONS = [
    "Quelles interpellations ont reçu une réponse en 2025 ?",
    "Quelle motion de 2026 a été renvoyée à la Municipalité ?",
    "Quels objets politiques parlent de mobilité ou de bus dans cette législature ?",
    "Quels postulats ont été déposés en 2024 ?",
]

USER_ERROR_MESSAGE = (
    "Désolé, la recherche a rencontré un problème technique. "
    "La question a été journalisée pour diagnostic; tu peux réessayer dans un instant."
)


st.set_page_config(page_title="AI Riviera", page_icon="🏛️", layout="wide")

st.title("AI Riviera")
st.caption("Assistant de recherche sur les documents publics de La Tour-de-Peilz (législature 2021-2026) - projet à but non lucratif")
st.caption(
    "Rechercheassistent für öffentliche Dokumente der Gemeinde La Tour-de-Peilz "
    "(Legislatur 2021-2026) - "
    "nicht gewinnorientiertes Projekt"
)

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


def ensure_index_ready() -> bool:
    if opensearch_ready() or postgres_ready():
        return True
    st.warning(
        "La base Riviera 2 n'est pas encore prête. Démarre Postgres/OpenSearch ou relance l'indexation "
        "depuis l'environnement d'administration."
    )
    return False


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
    url = metadata.get("source_url") or metadata.get("pdf_url") or metadata.get("url") or ""
    if not url:
        return label
    return f"[{label}]({url})"


def link_source_mentions(text: str, message_index: int, source_count: int) -> str:
    if source_count == 0:
        return text

    def replace(match: re.Match) -> str:
        number = int(match.group(1))
        if number > source_count:
            return match.group(0)
        label = match.group(0)
        return f"[{label}](#source-{message_index}-{number})"

    return re.sub(r"\bSource\s+(\d+)\b", replace, text)


def render_sources(results: list[dict], message_index: int) -> None:
    grouped_sources = group_results_by_document(results)
    if not grouped_sources:
        return

    st.divider()
    st.subheader("Sources")
    st.markdown("Documents utilisés dans la réponse:")
    source_lines = []
    for index, source in enumerate(grouped_sources, start=1):
        metadata = source["metadata"]
        filename = fix_mojibake(metadata.get("filename") or metadata.get("title") or source.get("relative_text_path", "document"))
        year = metadata.get("year") or metadata.get("date", "")
        category = metadata.get("category") or metadata.get("doc_type", "")
        source_lines.append(
            f'<span id="source-{message_index}-{index}"></span>'
            f"{index}. {source_link(metadata, filename)} - {year} / {category}"
        )
    st.markdown("\n".join(source_lines), unsafe_allow_html=True)

    with st.expander("Voir les passages retrouvés"):
        for index, source in enumerate(grouped_sources, start=1):
            metadata = source["metadata"]
            filename = fix_mojibake(metadata.get("filename") or metadata.get("title") or source.get("relative_text_path", "document"))
            passages = source["passages"]
            passage_label = "passage" if len(passages) == 1 else "passages"
            st.markdown(f"**Source {index}. {filename}** ({len(passages)} {passage_label})")
            for passage_index, passage in enumerate(passages[:3], start=1):
                if len(passages) > 1:
                    st.caption(f"Passage {passage_index}")
                st.code(fix_mojibake(passage.get("text") or passage.get("content") or "")[:1800], language="text")


chat_tab, eval_tab, about_tab, next_tab, about_de_tab, next_de_tab = st.tabs(
    ["Assistant", "Eval", "À propos", "Prochaines étapes", "Über das Projekt", "Nächste Schritte"]
)


@st.cache_data(ttl=900, max_entries=128, show_spinner=False)
def cached_answer_question(question: str, filters_key: tuple[tuple[str, str], ...]) -> tuple[str, list[dict], bool]:
    structured_answer = answer_structured_question(question)
    if structured_answer:
        return structured_answer, [], True
    if not (opensearch_ready() or postgres_ready()):
        return "La base Riviera 2 n'est pas encore indexée. Relance l'indexation depuis l'environnement d'administration.", [], False
    results = search(question, limit=20, filters=dict(filters_key))
    return answer_from_sources(question, results), results, False


def answer_question(question: str) -> tuple[str, list[dict], bool]:
    if not ensure_index_ready():
        return "La base Riviera 2 n'est pas encore indexée. Relance l'indexation depuis l'environnement d'administration.", [], False

    started_at = time.perf_counter()
    try:
        answer, results, structured = cached_answer_question(question, cacheable_filters(current_filters()))
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        record_interaction(
            question,
            status="ok",
            duration_ms=duration_ms,
            structured=structured,
            source_count=len(group_results_by_document(results)) if results else 0,
            answer_chars=len(answer),
        )
        return answer, results, structured
    except Exception as exc:
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        record_diagnostic("ui", "Question answering failed", exc, question=question[:300])
        record_interaction(question, status="error", duration_ms=duration_ms, error=repr(exc))
        return USER_ERROR_MESSAGE, [], False


def queue_question(question: str) -> None:
    st.session_state.messages.append({"role": "user", "content": question})
    st.session_state.pending_question = question

with chat_tab:
    st.markdown(
        "Pose une question en langage naturel. L'assistant cherche dans les documents publics "
        "indexés, puis répond avec les sources utilisées."
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
            source_count = len(group_results_by_document(results)) if results else 0
            st.markdown(link_source_mentions(fix_mojibake(message["content"]), message_index, source_count))
            if message["role"] == "assistant":
                render_sources(results, message_index)

    if st.session_state.pending_question:
        suggestions_slot.empty()
        pending_question = st.session_state.pending_question
        st.markdown(
            """
            <div class="air-loading" aria-live="polite" aria-label="Recherche en cours">
                <span class="air-loading-docs" aria-hidden="true">
                    <span class="air-loading-page"></span>
                    <span class="air-loading-page"></span>
                    <span class="air-loading-page"></span>
                </span>
                <span class="air-loading-text">Lecture des sources...</span>
            </div>
            """,
            unsafe_allow_html=True,
        )
        answer, results, _ = answer_question(pending_question)

        st.session_state.messages.append({"role": "assistant", "content": answer, "results": results})
        st.session_state.pending_question = None
        st.rerun()

    question = st.chat_input("Pose une question sur les documents...", disabled=st.session_state.pending_question is not None)
    if question and st.session_state.pending_question is None:
        queue_question(question)
        st.rerun()

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
        answer, results, structured = answer_question(item["question"])
        st.session_state.eval_runs.insert(
            0,
            {
                "id": item["id"],
                "question": item["question"],
                "expected_answer": item.get("expected_answer", ""),
                "expected_sources": item.get("expected_sources", []),
                "answer": answer,
                "results": results,
                "structured": structured,
                "retrieval_ok": structured or retrieval_hit(results, item.get("expected_sources", [])),
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
    st.subheader("Qu'est-ce que c'est ?")
    st.write(
        "AI Riviera est un prototype de chatbot qui aide à retrouver rapidement des informations "
        "dans les documents publics d'une commune. C'est un projet à but non lucratif, pensé comme "
        "un outil d'intérêt public. L'objectif est simple : poser une question comme on la formulerait "
        "à un collègue, puis obtenir une réponse avec les documents sources."
    )

    st.subheader("Ce qui est déjà dans la base")
    st.write(
        "Pour La Tour-de-Peilz, le prototype contient déjà une partie importante de la législature "
        "2021-2026 : ordres du jour, procès-verbaux, motions, postulats, interpellations, réponses, "
        "préavis municipaux, objets divers, communications municipales, infos de la Municipalité, "
        "budgets, rapports des comptes, rapports de gestion et rubriques institutionnelles du "
        "Conseil communal."
    )

    st.subheader("Comment ça répond ?")
    st.write(
        "Les documents sont transformés en textes propres, découpés en passages, stockés dans Postgres "
        "et indexés dans OpenSearch. Quand une question est posée, l'application cherche les passages "
        "les plus pertinents, puis le modèle de langage rédige une réponse en s'appuyant sur ces extraits. "
        "Les sources restent affichées pour pouvoir vérifier."
    )
    st.write(
        "Riviera 2 utilise une recherche hybride SQL/OpenSearch. Le mode JSON de la première version "
        "reste disponible seulement comme export ou fallback explicite. Le prototype utilise actuellement "
        "Mistral, mais d'autres modèles pourraient être testés, y compris des solutions open source selon les besoins."
    )

    st.info(
        "Ce prototype ne remplace pas les documents officiels. Il sert à gagner du temps pour "
        "chercher, comparer et préparer une première lecture."
    )

with next_tab:
    st.subheader("Idées pour une version plus solide")
    st.markdown(
        """
- Mettre en place un web scraping continu pour détecter automatiquement les nouvelles séances, pages et PDF.
- Étendre la collecte aux autres communes de la Riviera, par exemple Vevey, Montreux, Blonay-Saint-Légier, Veytaux et les communes voisines.
- Consolider Postgres/OpenSearch comme base principale, avec une recherche sémantique plus rapide et mieux observable.
- Ajouter un vrai RAG avec embeddings pour mieux comprendre les questions formulées avec des mots différents de ceux des documents.
- Créer un espace privé avec login pour les élus ou l'administration, si des documents internes doivent être ajoutés.
- Améliorer les réponses chiffrées avec des tables structurées pour les budgets, comptes, préavis et décisions financières.
- Ajouter des filtres simples par commune, année, séance, type de document ou thème.
"""
    )

with about_de_tab:
    st.subheader("Was ist das?")
    st.write(
        "AI Riviera ist ein Chatbot-Prototyp, der dabei hilft, Informationen in öffentlichen "
        "Gemeindedokumenten schneller zu finden. Es handelt sich um ein nicht gewinnorientiertes "
        "Projekt im öffentlichen Interesse. Man stellt eine Frage in normaler Sprache und erhält "
        "eine Antwort mit den verwendeten Quellen."
    )

    st.subheader("Was ist bereits in der Datenbasis?")
    st.write(
        "Für La Tour-de-Peilz enthält der Prototyp bereits einen grossen Teil der Legislatur "
        "2021-2026: Traktandenlisten, Protokolle, Motionen, Postulate, Interpellationen, Antworten, "
        "kommunale Vorlagen, verschiedene Objekte, Mitteilungen der Municipalité, Informationen der "
        "Municipalité, Budgets, Rechnungsberichte, Geschäftsberichte und institutionelle Rubriken "
        "des Conseil communal."
    )

    st.subheader("Wie entstehen die Antworten?")
    st.write(
        "Die Dokumente werden in bereinigten Text umgewandelt, in kleinere Abschnitte geteilt, in "
        "Postgres gespeichert und in OpenSearch indexiert. Bei einer Frage sucht die Anwendung zuerst "
        "die relevantesten Textstellen. Danach formuliert das Sprachmodell eine Antwort auf Basis dieser "
        "Auszüge. Die Quellen bleiben sichtbar, damit die Antwort überprüft werden kann."
    )
    st.write(
        "Riviera 2 nutzt eine hybride SQL/OpenSearch-Suche. Der JSON-Modus der ersten Version bleibt "
        "nur als Export oder expliziter Fallback verfügbar. Der Prototyp nutzt derzeit Mistral, andere "
        "Modelle oder Open-Source-Lösungen könnten je nach Bedarf ebenfalls geprüft werden."
    )

    st.info(
        "Dieser Prototyp ersetzt keine offiziellen Dokumente. Er soll helfen, schneller zu suchen, "
        "zu vergleichen und eine erste Einschätzung vorzubereiten."
    )

with next_de_tab:
    st.subheader("Ideen für eine robustere Version")
    st.markdown(
        """
- Kontinuierliches Web Scraping einrichten, um neue Sitzungen, Seiten und PDF-Dokumente automatisch zu erkennen.
- Die Sammlung auf weitere Gemeinden der Riviera ausweiten, zum Beispiel Vevey, Montreux, Blonay-Saint-Légier, Veytaux und Nachbargemeinden.
- Postgres/OpenSearch als Hauptbasis konsolidieren, mit schnellerer und besser beobachtbarer semantischer Suche.
- Ein echtes RAG-System mit Embeddings hinzufügen, damit Fragen auch dann besser verstanden werden, wenn andere Wörter als in den Dokumenten verwendet werden.
- Einen privaten Bereich mit Login für gewählte Behördenmitglieder oder die Verwaltung schaffen, falls interne Dokumente ergänzt werden sollen.
- Antworten zu Zahlen mit strukturierten Tabellen für Budgets, Rechnungen, Vorlagen und finanzielle Entscheide verbessern.
- Einfache Filter nach Gemeinde, Jahr, Sitzung, Dokumenttyp oder Thema hinzufügen.
"""
    )
