from __future__ import annotations

import html
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests


PILOT_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = PILOT_ROOT.parent
OUTPUT_JSON = PILOT_ROOT / "evaluation" / "comparison_results.json"
OUTPUT_HTML = PILOT_ROOT / "evaluation" / "comparison_report.html"
CONTAINER = "ai-riviera-embedding-pilot-db"
DATABASE = "ai_riviera_embedding_pilot"
USER = "pilot"

QUESTIONS = [
    "Quelle motion de 2026 parle du remboursement des frais de garde ?",
    "Quelles propositions concernent le bruit et la qualité de vie ?",
    "Quels objets politiques parlent de mobilité ou de bus ?",
    "Que propose le texte sur le crowdfunding local ?",
    "Quels rapports existent pour la motion Un engagement pour la Faraz ?",
    "Que dit l'article 96 du règlement du Conseil communal ?",
    "Quels objets concernent le stationnement ou les macarons ?",
    "Quelles mesures sont proposées pour lutter contre les îlots de chaleur ?",
]


def load_pilot_env() -> None:
    path = PILOT_ROOT / ".env"
    if path.exists():
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    os.environ.setdefault("LLM_PROVIDER", "mistral")


load_pilot_env()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.answer import answer_from_sources, rerank_results_with_llm, rewrite_query_with_llm  # noqa: E402
from app.retrieval import search as search_old  # noqa: E402


def embed_query(query: str) -> list[float]:
    response = requests.post(
        "https://api.mistral.ai/v1/embeddings",
        headers={"Authorization": f"Bearer {os.environ['MISTRAL_API_KEY']}"},
        json={"model": "mistral-embed", "input": [query]}, timeout=60,
    )
    response.raise_for_status()
    return response.json()["data"][0]["embedding"]


def search_new(query: str, limit: int = 50) -> list[dict]:
    vector = embed_query(query)
    vector_text = "[" + ",".join(format(value, ".9g") for value in vector) + "]"
    sql = f"""
        SELECT json_build_object(
            'chunk_id',c.chunk_id,'document_id',c.document_id,'chunk_index',c.chunk_index,
            'component',c.component,'content',c.content,'title',d.title,'category',d.category,
            'document_role',d.document_role,
            'score',round((1-(c.embedding <=> '{vector_text}'::vector))::numeric,6),
            'chunk_metadata',c.metadata,'document_metadata',d.metadata)
        FROM chunks c JOIN documents d USING(document_id)
        ORDER BY c.embedding <=> '{vector_text}'::vector LIMIT {limit};
    """
    process = subprocess.run(
        ["docker", "exec", "-i", CONTAINER, "psql", "-X", "-A", "-t", "-U", USER, "-d", DATABASE],
        input=sql, text=True, encoding="utf-8", capture_output=True, check=True, timeout=90,
    )
    results = []
    for line in process.stdout.splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        metadata = {
            **(row.pop("document_metadata") or {}), **(row.pop("chunk_metadata") or {}),
            "title": row["title"], "category": row["category"], "doc_type": row["category"],
            "document_id": row["document_id"], "component": row.get("component"), "canonical_object": True,
        }
        row.update({"id": row["chunk_id"], "text": row["content"], "metadata": metadata,
                    "_score": float(row["score"]), "_search_source": "mistral_pgvector"})
        results.append(row)
    return results


def compact_result(result: dict) -> dict:
    metadata = result.get("metadata") or {}
    return {
        "chunk_id": result.get("chunk_id") or result.get("id"),
        "title": result.get("title") or metadata.get("title") or metadata.get("filename"),
        "category": result.get("category") or metadata.get("category") or metadata.get("doc_type"),
        "component": result.get("component") or metadata.get("component"),
        "score": result.get("score") or result.get("_score"),
        "excerpt": (result.get("content") or result.get("text") or "")[:500],
    }


def run_pipeline(question: str, retrieval_query: str, backend: str) -> dict:
    started = time.perf_counter()
    raw = search_old(retrieval_query, limit=50) if backend == "old" else search_new(retrieval_query, limit=50)
    retrieval_ms = round((time.perf_counter() - started) * 1000)
    started = time.perf_counter()
    ranked = rerank_results_with_llm(question, raw, keep=20, max_candidates=30)
    rerank_ms = round((time.perf_counter() - started) * 1000)
    started = time.perf_counter()
    answer = answer_from_sources(question, ranked)
    answer_ms = round((time.perf_counter() - started) * 1000)
    return {
        "timings_ms": {"retrieval": retrieval_ms, "rerank": rerank_ms, "answer": answer_ms},
        "raw_top": [compact_result(item) for item in raw[:10]],
        "reranked_top": [compact_result(item) for item in ranked[:10]], "answer": answer,
    }


def result_list(items: list[dict]) -> str:
    rows = []
    for index, item in enumerate(items, 1):
        score = item.get("score")
        score_text = f" · score {float(score):.4f}" if score is not None else ""
        rows.append(
            f"<li><strong>{index}. {html.escape(str(item.get('title') or 'Sans titre'))}</strong>"
            f"<br><small>{html.escape(str(item.get('category') or ''))} · "
            f"{html.escape(str(item.get('component') or ''))}{score_text}</small></li>"
        )
    return "<ol>" + "".join(rows) + "</ol>"


def build_html(records: list[dict]) -> str:
    sections = []
    for record in records:
        columns = []
        for key, label in (("old", "Chatbot actuel"), ("new", "Nouveaux embeddings")):
            data = record[key]
            timing = data["timings_ms"]
            columns.append(f"""<article><h3>{label}</h3>
            <p class="timing">Recherche {timing['retrieval']} ms · reranking {timing['rerank']} ms · réponse {timing['answer']} ms</p>
            <h4>Réponse finale</h4><pre>{html.escape(data['answer'])}</pre>
            <details open><summary>Top 10 après le même reranker</summary>{result_list(data['reranked_top'])}</details>
            <details><summary>Top 10 brut avant reranking</summary>{result_list(data['raw_top'])}</details></article>""")
        sections.append(f"""<section><h2>{html.escape(record['question'])}</h2>
        <p><strong>Requête commune :</strong> {html.escape(record['retrieval_query'])}</p>
        <div class="columns">{''.join(columns)}</div></section>""")
    return f"""<!doctype html><html lang="fr"><head><meta charset="utf-8"><title>Comparaison embeddings</title><style>
    body{{font:15px/1.5 system-ui;margin:24px;color:#172033;background:#f5f7fb}}main{{max-width:1500px;margin:auto}}
    section{{background:white;padding:22px;margin:22px 0;border-radius:14px;box-shadow:0 2px 12px #18233a14}}
    .columns{{display:grid;grid-template-columns:1fr 1fr;gap:18px}}article{{border:1px solid #d9dfeb;padding:16px;border-radius:10px;min-width:0}}
    pre{{white-space:pre-wrap;font:14px/1.5 system-ui;background:#f7f9fc;padding:14px;border-radius:8px;max-height:520px;overflow:auto}}
    summary{{cursor:pointer;font-weight:700;margin-top:12px}}li{{margin:8px 0}}small,.timing{{color:#58657a}}
    @media(max-width:900px){{.columns{{grid-template-columns:1fr}}}}</style></head><body><main>
    <h1>Chatbot actuel vs nouveaux embeddings</h1><p>Même requête, même reranker LLM et même LLM final. Seule la récupération change.</p>
    {''.join(sections)}</main></body></html>"""


def main() -> None:
    records = []
    for index, question in enumerate(QUESTIONS, 1):
        print(f"[{index}/{len(QUESTIONS)}] {question}", flush=True)
        retrieval_query = rewrite_query_with_llm(question) or question
        records.append({"question": question, "retrieval_query": retrieval_query,
                        "old": run_pipeline(question, retrieval_query, "old"),
                        "new": run_pipeline(question, retrieval_query, "new")})
    payload = {"created_at": datetime.now(timezone.utc).isoformat(),
               "method": "same_query_same_llm_reranker_same_llm_answer", "questions": records}
    OUTPUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    OUTPUT_HTML.write_text(build_html(records), encoding="utf-8")
    print(OUTPUT_HTML)


if __name__ == "__main__":
    main()
