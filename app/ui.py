import sys
from pathlib import Path
import re

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.answer import answer_from_sources
from app.config import CHUNKS_PATH, DOCUMENTS_ROOT, SQLITE_PATH
from app.ingest import build_index
from app.retrieval import load_chunks, search
from app.structured import answer_structured_question


st.set_page_config(page_title="AI Riviera", page_icon="🏛️", layout="wide")

st.title("AI Riviera")
st.caption("Assistant de recherche sur les documents publics de La Tour-de-Peilz - projet à but non lucratif")
st.caption(
    "Rechercheassistent für öffentliche Dokumente der Gemeinde La Tour-de-Peilz - "
    "nicht gewinnorientiertes Projekt"
)


def documents_changed_after_index() -> bool:
    if not CHUNKS_PATH.exists() or not SQLITE_PATH.exists():
        return True

    index_mtime = min(CHUNKS_PATH.stat().st_mtime, SQLITE_PATH.stat().st_mtime)
    for path in DOCUMENTS_ROOT.rglob("*.txt"):
        if path.stat().st_mtime > index_mtime:
            return True
    for path in DOCUMENTS_ROOT.rglob("*.json"):
        if path.stat().st_mtime > index_mtime:
            return True
    return False


def ensure_index_ready() -> None:
    if documents_changed_after_index():
        with st.spinner("Mise à jour de l'index des documents..."):
            build_index()
            load_chunks.cache_clear()


def group_results_by_document(results: list[dict]) -> list[dict]:
    grouped = {}
    for result in results:
        metadata = result["metadata"]
        document_key = (
            metadata.get("pdf_url")
            or metadata.get("text_path")
            or result.get("relative_text_path")
            or metadata.get("filename")
            or result["id"].split("#", 1)[0]
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
    url = metadata.get("pdf_url") or metadata.get("url") or ""
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
        filename = metadata.get("filename", source.get("relative_text_path", "document"))
        year = metadata.get("year", "")
        category = metadata.get("category", "")
        source_lines.append(
            f'<span id="source-{message_index}-{index}"></span>'
            f"{index}. {source_link(metadata, filename)} - {year} / {category}"
        )
    st.markdown("\n".join(source_lines), unsafe_allow_html=True)

    with st.expander("Voir les passages retrouvés"):
        for index, source in enumerate(grouped_sources, start=1):
            metadata = source["metadata"]
            filename = metadata.get("filename", source.get("relative_text_path", "document"))
            passages = source["passages"]
            passage_label = "passage" if len(passages) == 1 else "passages"
            st.markdown(f"**Source {index}. {filename}** ({len(passages)} {passage_label})")
            for passage_index, passage in enumerate(passages[:3], start=1):
                if len(passages) > 1:
                    st.caption(f"Passage {passage_index}")
                st.code(passage["text"][:1800], language="text")


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

        ensure_index_ready()
        structured_answer = answer_structured_question(question)
        if structured_answer:
            results = []
            answer = structured_answer
        else:
            results = search(question, limit=10)
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
        "Les documents sont transformés en textes propres, découpés en passages et indexés. Quand "
        "une question est posée, l'application cherche les passages les plus pertinents, puis le "
        "modèle de langage rédige une réponse en s'appuyant sur ces extraits. Les sources restent "
        "affichées pour pouvoir vérifier."
    )
    st.write(
        "Pour éviter d'envoyer trop de texte au modèle, l'application ne transmet qu'une petite "
        "sélection de passages utiles. Cela réduit le nombre de tokens, donc les coûts et le temps "
        "de réponse. Le prototype utilise actuellement Mistral, mais d'autres modèles pourraient "
        "être testés, y compris des solutions open source selon les besoins."
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
- Passer d'un index JSON de prototype à une base plus robuste, par exemple PostgreSQL, avec une recherche sémantique plus rapide.
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
        "Die Dokumente werden in bereinigten Text umgewandelt, in kleinere Abschnitte geteilt und "
        "indexiert. Bei einer Frage sucht die Anwendung zuerst die relevantesten Textstellen. Danach "
        "formuliert das Sprachmodell eine Antwort auf Basis dieser Auszüge. Die Quellen bleiben "
        "sichtbar, damit die Antwort überprüft werden kann."
    )
    st.write(
        "Um nicht zu viel Text an das Sprachmodell zu senden, übermittelt die Anwendung nur eine "
        "kleine Auswahl relevanter Passagen. Das reduziert die Anzahl Tokens, die Kosten und die "
        "Antwortzeit. Der Prototyp nutzt derzeit Mistral, andere Modelle oder Open-Source-Lösungen "
        "könnten je nach Bedarf ebenfalls geprüft werden."
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
- Vom einfachen JSON-Prototyp zu einer robusteren Datenbank wechseln, zum Beispiel PostgreSQL, mit schnellerer semantischer Suche.
- Ein echtes RAG-System mit Embeddings hinzufügen, damit Fragen auch dann besser verstanden werden, wenn andere Wörter als in den Dokumenten verwendet werden.
- Einen privaten Bereich mit Login für gewählte Behördenmitglieder oder die Verwaltung schaffen, falls interne Dokumente ergänzt werden sollen.
- Antworten zu Zahlen mit strukturierten Tabellen für Budgets, Rechnungen, Vorlagen und finanzielle Entscheide verbessern.
- Einfache Filter nach Gemeinde, Jahr, Sitzung, Dokumenttyp oder Thema hinzufügen.
"""
    )
