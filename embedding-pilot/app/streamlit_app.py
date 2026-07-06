from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import requests
import streamlit as st


PILOT_ROOT = Path(__file__).resolve().parents[1]
CONTAINER = "ai-riviera-embedding-pilot-db"
DATABASE = "ai_riviera_embedding_pilot"
USER = "pilot"
CATEGORIES = {
    "Motions": "motion",
    "Postulats": "postulat",
    "Interpellations": "interpellation",
    "Règlement": "reglement_conseil_communal",
}


def load_api_key() -> str:
    key = os.environ.get("MISTRAL_API_KEY", "")
    env_path = PILOT_ROOT / ".env"
    if not key and env_path.exists():
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            if raw_line.startswith("MISTRAL_API_KEY="):
                key = raw_line.split("=", 1)[1].strip().strip('"').strip("'")
                break
    return key


def sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


@st.cache_data(show_spinner=False, ttl=3600)
def embed_query(query: str) -> list[float]:
    key = load_api_key()
    if not key:
        raise RuntimeError("Clé Mistral absente dans embedding-pilot/.env")
    response = requests.post(
        "https://api.mistral.ai/v1/embeddings",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": "mistral-embed", "input": [query]},
        timeout=60,
    )
    response.raise_for_status()
    return response.json()["data"][0]["embedding"]


def database_status() -> dict:
    sql = "SELECT json_build_object('documents',count(DISTINCT document_id),'chunks',count(*),'vectors',count(embedding)) FROM chunks;"
    result = run_psql(sql)
    return json.loads(result[0]) if result else {}


def run_psql(sql: str) -> list[str]:
    result = subprocess.run(
        ["docker", "exec", "-i", CONTAINER, "psql", "-X", "-A", "-t", "-U", USER, "-d", DATABASE],
        input=sql,
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=60,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "La base Docker ne répond pas.")
    return [line for line in result.stdout.splitlines() if line.strip()]


def semantic_search(query: str, categories: list[str], limit: int) -> list[dict]:
    vector = embed_query(query)
    vector_text = "[" + ",".join(format(value, ".9g") for value in vector) + "]"
    category_filter = ""
    if categories:
        category_filter = "WHERE d.category IN (" + ",".join(sql_literal(value) for value in categories) + ")"
    sql = f"""
        SELECT json_build_object(
            'score', round((1 - (c.embedding <=> '{vector_text}'::vector))::numeric, 4),
            'category', d.category,
            'document_role', d.document_role,
            'title', d.title,
            'component', c.component,
            'chunk_id', c.chunk_id,
            'chunk_index', c.chunk_index,
            'content', c.content,
            'metadata', c.metadata
        )
        FROM chunks c JOIN documents d USING (document_id)
        {category_filter}
        ORDER BY c.embedding <=> '{vector_text}'::vector
        LIMIT {limit};
    """
    return [json.loads(line) for line in run_psql(sql)]


st.set_page_config(page_title="Pilote embeddings · AI Riviera", page_icon="🔎", layout="wide")
st.title("🔎 Pilote de recherche sémantique")
st.caption("Mistral Embed · PostgreSQL/pgvector local · données des audits généraux")

try:
    status = database_status()
    cols = st.columns(3)
    cols[0].metric("Documents", status.get("documents", 0))
    cols[1].metric("Chunks", status.get("chunks", 0))
    cols[2].metric("Vecteurs", status.get("vectors", 0))
except Exception as error:
    st.error(f"Base Docker indisponible : {error}")
    st.code("docker compose -f embedding-pilot/database/compose.yaml up -d --wait", language="powershell")
    st.stop()

with st.sidebar:
    st.header("Filtres")
    selected_labels = st.multiselect("Catégories", list(CATEGORIES), default=list(CATEGORIES))
    result_limit = st.slider("Nombre de résultats", min_value=3, max_value=20, value=8)
    st.divider()
    st.caption("Chaque recherche génère un seul embedding de requête. Les résultats proviennent de la base Docker locale.")

with st.form("semantic-search"):
    query = st.text_input(
        "Question ou thème",
        placeholder="Ex. Quelles propositions concernent le bruit et la qualité de vie ?",
    )
    submitted = st.form_submit_button("Rechercher", type="primary", use_container_width=True)

if submitted:
    if not query.strip():
        st.warning("Écris une question ou un thème.")
    elif not selected_labels:
        st.warning("Sélectionne au moins une catégorie.")
    else:
        selected_categories = [CATEGORIES[label] for label in selected_labels]
        try:
            with st.spinner("Recherche sémantique…"):
                results = semantic_search(query.strip(), selected_categories, result_limit)
        except Exception as error:
            st.error(f"Recherche impossible : {error}")
        else:
            st.subheader(f"{len(results)} résultats")
            for rank, result in enumerate(results, 1):
                score = float(result["score"])
                component = result.get("component") or "article du règlement"
                with st.container(border=True):
                    left, right = st.columns([5, 1])
                    left.markdown(f"### {rank}. {result['title']}")
                    right.metric("Similarité", f"{score:.1%}")
                    st.caption(
                        f"{result['category']} · {component} · chunk {result['chunk_index']} · `{result['chunk_id']}`"
                    )
                    st.write(result["content"])
                    with st.expander("Métadonnées techniques"):
                        st.json({
                            "document_role": result.get("document_role"),
                            "component": result.get("component"),
                            **(result.get("metadata") or {}),
                        })
elif not query:
    st.info("Entre une question pour comparer les documents politiques et les articles du règlement.")
