from __future__ import annotations

from collections import Counter

from app.embeddings import cosine_similarity, embed_text


def canonical_priority(metadata: dict) -> float:
    if metadata.get("canonical_object") is False:
        return -1.25
    if str(metadata.get("source_collection", "")) == "ordre-du-jour-linked-document":
        return -1.25
    if metadata.get("canonical_object") is True:
        return 1.5
    if str(metadata.get("source_collection", "")) == "motions-postulats":
        return 1.0
    return 0.0


def rerank_chunks(query: str, chunks: list[dict], limit: int = 12) -> list[dict]:
    if not chunks:
        return []

    query_embedding = embed_text(query)
    query_tokens = Counter(token.lower() for token in query.split() if len(token) >= 3)
    reranked: list[tuple[float, dict]] = []

    for position, chunk in enumerate(chunks):
        content = str(chunk.get("content", ""))
        title = str(chunk.get("title", ""))
        metadata = chunk.get("metadata") or {}
        if not isinstance(metadata, dict):
            metadata = {}
        combined_text = f"{title} {content}".lower()
        score = float(chunk.get("_score", 0.0))
        score += cosine_similarity(query_embedding, chunk.get("embedding", [])) * 2.0
        score += canonical_priority(metadata)
        for token, count in query_tokens.items():
            if token in combined_text:
                score += count * 0.25
        if chunk.get("source_url"):
            score += 0.1
        score -= position * 0.01
        reranked.append((score, chunk))

    reranked.sort(key=lambda item: item[0], reverse=True)
    ordered = []
    for score, chunk in reranked[:limit]:
        item = dict(chunk)
        item["score"] = round(score, 4)
        item["text"] = item.get("text") or item.get("content", "")
        item["metadata"] = item.get("metadata") or {
            "city": item.get("city", ""),
            "doc_type": item.get("doc_type", ""),
            "title": item.get("title", ""),
            "date": item.get("date", ""),
            "source_url": item.get("source_url", ""),
            "document_hash": item.get("document_hash", ""),
        }
        item["id"] = item.get("id") or item.get("chunk_id", "")
        ordered.append(item)
    return ordered
