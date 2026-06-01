import sys
from pathlib import Path
import re

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.answer import answer_from_sources
from app.config import STORAGE_BACKEND
from app.ingest import build_index
from app.opensearch_store import ready as opensearch_ready
from app.postgres_store import ready as postgres_ready
from app.retrieval import search
from app.structured import answer_structured_question


st.set_page_config(page_title="AI Riviera", page_icon="🏛️", layout="wide")

st.title("AI Riviera")
st.caption("Assistant de recherche sur les documents publics de La Tour-de-Peilz - projet à but non lucratif")
st.caption(
    "Rechercheassistent für öffentliche Dokumente der Gemeinde La Tour-de-Peilz - "
    "nicht gewinnorientiertes Projekt"
)

with st.sidebar:
    st.header("Indexation")
    st.caption(f"Mode stockage: {STORAGE_BACKEND}")
    if st.button("Indexer Riviera 2"):
        with st.spinner("Indexation SQL/OpenSearch en cours..."):
            try:
                stats = build_index()
                st.success(
                    f"Indexation terminée: {stats.get('documents_indexed', 0)} documents, "
                    f"{stats.get('chunks_indexed', 0)} passages."
                )
            except Exception as exc:
                st.error(f"Indexation impossible: {exc}")

    st.header("Filtres")
    city_filter = st.text_input("Ville", value="La Tour-de-Peilz")
    doc_type_filter = st.text_input("Type de document", value="")
    date_from_filter = st.text_input("Date de début (YYYY-MM-DD)", value="")
    date_to_filter = st.text_input("Date de fin (YYYY-MM-DD)", value="")


def current_filters() -> dict | None:
    filters = {}
    if city_filter.strip():
        filters["city"] = city_filter.strip()
    if doc_type_filter.strip():
        filters["doc_type"] = doc_type_filter.strip()
    if date_from_filter.strip():
        filters["date_from"] = date_from_filter.strip()
    if date_to_filter.strip():
        filters["date_to"] = date_to_filter.strip()
    return filters or None


def ensure_index_ready() -> bool:
    if opensearch_ready() or postgres_ready():
        return True
    st.warning(
        "La base Riviera 2 n'est pas encore prête. Clique sur 'Indexer Riviera 2' dans la barre latérale, "
        "ou démarre Postgres/OpenSearch puis relance l'indexation."
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
        filename = metadata.get("filename") or metadata.get("title") or source.get("relative_text_path", "document")
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
            filename = metadata.get("filename") or metadata.get("title") or source.get("relative_text_path", "document")
            passages = source["passages"]
            passage_label = "passage" if len(passages) == 1 else "passages"
            st.markdown(f"**Source {index}. {filename}** ({len(passages)} {passage_label})")
            for passage_index, passage in enumerate(passages[:3], start=1):
                if len(passages) > 1:
                    st.caption(f"Passage {passage_index}")
                st.code((passage.get("text") or passage.get("content") or "")[:1800], language="text")


chat_tab, about_tab, next_tab, about_de_tab, next_de_tab = st.tabs(
    ["Assistant", "À propos", "Prochaines étapes", "Über das Projekt", "Nächste Schritte"]
)

with chat_tab:
    st.markdown(
        "Pose une question en langage naturel. L'assistant cherche dans les documents publics "
        "indexés, puis répond avec les sources utilisées."
    )

    if "messages" not in st.session_state:
        st.session_state.messages = []

    for message_index, message in enumerate(st.session_state.messages):
        with st.chat_message(message["role"]):
            results = message.get("results", [])
            source_count = len(group_results_by_document(results)) if results else 0
            st.markdown(link_source_mentions(message["content"], message_index, source_count))
            if message["role"] == "assistant":
                render_sources(results, message_index)

    question = st.chat_input("Pose une question sur les documents...")
    if question:
        st.session_state.messages.append({"role": "user", "content": question})

        structured_answer = answer_structured_question(question)
        if structured_answer:
            results = []
            answer = structured_answer
        elif not ensure_index_ready():
            results = []
            answer = "La base Riviera 2 n'est pas encore indexée. Lance l'indexation SQL depuis la barre latérale."
        else:
            results = search(question, limit=20, filters=current_filters())
            answer = answer_from_sources(question, results)

        st.session_state.messages.append({"role": "assistant", "content": answer, "results": results})
        st.rerun()

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
