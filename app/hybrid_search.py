from __future__ import annotations

from collections import defaultdict
from typing import Any

from app.embeddings import embed_text
from app.opensearch_store import keyword_search, knn_search, ready as opensearch_ready
from app.reranker import rerank_chunks


def _merge_candidates(keyword_hits: list[dict[str, Any]], vector_hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    combined: dict[str, dict[str, Any]] = {}

    for hit in keyword_hits + vector_hits:
        chunk_id = str(hit.get("chunk_id", ""))
        if not chunk_id:
            continue
        existing = combined.get(chunk_id)
        if existing is None:
            combined[chunk_id] = dict(hit)
            continue

        existing["_score"] = max(float(existing.get("_score", 0.0)), float(hit.get("_score", 0.0)))
        if not existing.get("embedding") and hit.get("embedding"):
            existing["embedding"] = hit["embedding"]
        if not existing.get("source_url") and hit.get("source_url"):
            existing["source_url"] = hit["source_url"]

    return list(combined.values())


def search_hybrid(
    query: str,
    limit: int = 6,
    filters: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if not opensearch_ready():
        return []

    query_embedding = embed_text(query)
    keyword_hits = keyword_search(query, filters=filters, size=max(limit * 4, 20))
    vector_hits = knn_search(query_embedding, filters=filters, size=max(limit * 4, 20))
    merged = _merge_candidates(keyword_hits, vector_hits)
    reranked = rerank_chunks(query, merged, limit=max(limit * 4, 20))
    return reranked[:limit]


def grouped_sources(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = defaultdict(lambda: {"passages": [], "score": 0.0})
    for result in results:
        key = result.get("source_url") or result.get("document_id") or result.get("chunk_id")
        groups[key]["metadata"] = {
            "city": result.get("city", ""),
            "doc_type": result.get("doc_type", ""),
            "title": result.get("title", ""),
            "date": result.get("date", ""),
            "source_url": result.get("source_url", ""),
            "document_hash": result.get("document_hash", ""),
        }
        groups[key]["score"] = max(groups[key]["score"], float(result.get("score", 0.0)))
        groups[key]["passages"].append(result)
    return sorted(groups.values(), key=lambda item: item["score"], reverse=True)