from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from app.config import EMBEDDING_DIMENSIONS, OPENSEARCH_INDEX, OPENSEARCH_MAPPING_PATH, OPENSEARCH_TIMEOUT, OPENSEARCH_URL
from app.diagnostics import record_diagnostic


_OPENSEARCH_UNAVAILABLE = False
_OPENSEARCH_MAPPING_UPDATED = False


@lru_cache(maxsize=1)
def get_client():
    try:
        from opensearchpy import OpenSearch

        return OpenSearch(
            OPENSEARCH_URL,
            timeout=OPENSEARCH_TIMEOUT,
            max_retries=0,
            retry_on_timeout=False,
        )
    except Exception as exc:
        record_diagnostic("opensearch", "OpenSearch client creation failed", exc, url=OPENSEARCH_URL)
        return None


def load_index_body() -> dict[str, Any]:
    body = json.loads(OPENSEARCH_MAPPING_PATH.read_text(encoding="utf-8"))
    body["mappings"]["properties"]["embedding"]["dimension"] = EMBEDDING_DIMENSIONS
    return body


def ensure_index() -> bool:
    global _OPENSEARCH_UNAVAILABLE
    if _OPENSEARCH_UNAVAILABLE:
        return False

    client = get_client()
    if client is None:
        _OPENSEARCH_UNAVAILABLE = True
        return False

    try:
        if client.indices.exists(index=OPENSEARCH_INDEX):
            ensure_runtime_mapping(client)
            return True

        client.indices.create(index=OPENSEARCH_INDEX, body=load_index_body())
        ensure_runtime_mapping(client)
        return True
    except Exception as exc:
        record_diagnostic("opensearch", "OpenSearch index ensure failed", exc, index=OPENSEARCH_INDEX)
        _OPENSEARCH_UNAVAILABLE = True
        return False


def ensure_runtime_mapping(client) -> None:
    global _OPENSEARCH_MAPPING_UPDATED
    if _OPENSEARCH_MAPPING_UPDATED:
        return
    try:
        client.indices.put_mapping(
            index=OPENSEARCH_INDEX,
            body={
                "properties": {
                    "political_object_id": {"type": "keyword"},
                    "political_object_type": {"type": "keyword"},
                    "object_year": {"type": "keyword"},
                    "legislature": {"type": "keyword"},
                }
            },
        )
        _OPENSEARCH_MAPPING_UPDATED = True
    except Exception as exc:
        record_diagnostic("opensearch", "OpenSearch mapping update failed", exc, index=OPENSEARCH_INDEX)


def ready() -> bool:
    global _OPENSEARCH_UNAVAILABLE
    if _OPENSEARCH_UNAVAILABLE:
        return False
    client = get_client()
    try:
        return bool(client and client.ping() and ensure_index())
    except Exception as exc:
        record_diagnostic("opensearch", "OpenSearch readiness check failed", exc, index=OPENSEARCH_INDEX)
        _OPENSEARCH_UNAVAILABLE = True
        return False


def delete_document(document_id: str) -> None:
    client = get_client()
    if client is None:
        return

    if not ensure_index():
        return
    try:
        client.delete_by_query(
            index=OPENSEARCH_INDEX,
            body={"query": {"term": {"document_id": document_id}}},
            conflicts="proceed",
            request_timeout=OPENSEARCH_TIMEOUT,
        )
    except Exception as exc:
        record_diagnostic("opensearch", "OpenSearch delete document failed", exc, document_id=document_id)
        return


def index_chunks(chunks: list[dict[str, Any]]) -> None:
    client = get_client()
    if client is None or not chunks:
        return

    if not ensure_index():
        return
    operations = []
    for chunk in chunks:
        operations.append({"index": {"_index": OPENSEARCH_INDEX, "_id": chunk["chunk_id"]}})
        operations.append(chunk)
    try:
        client.bulk(body=operations, refresh=False, request_timeout=OPENSEARCH_TIMEOUT)
    except Exception as exc:
        record_diagnostic("opensearch", "OpenSearch bulk index failed", exc, chunks=len(chunks), index=OPENSEARCH_INDEX)
        return


def _filters_to_query(filters: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not filters:
        return []

    query_filters: list[dict[str, Any]] = []
    if filters.get("city"):
        query_filters.append({"term": {"city": filters["city"]}})
    if filters.get("doc_type"):
        doc_type = filters["doc_type"]
        if isinstance(doc_type, (list, tuple, set)):
            query_filters.append({"terms": {"doc_type": list(doc_type)}})
        else:
            query_filters.append({"term": {"doc_type": doc_type}})
    if filters.get("content_kind"):
        query_filters.append({"term": {"metadata.content_kind": filters["content_kind"]}})
    if filters.get("year"):
        year = str(filters["year"])
        query_filters.append(
            {
                "bool": {
                    "should": [
                        {"term": {"metadata.object_year": year}},
                        {"term": {"metadata.object_year.keyword": year}},
                        {"term": {"metadata.year": year}},
                        {"term": {"metadata.year.keyword": year}},
                        {"term": {"metadata.listing_year": year}},
                        {"term": {"metadata.listing_year.keyword": year}},
                        {"term": {"object_year": year}},
                    ],
                    "minimum_should_match": 1,
                }
            }
        )
    if filters.get("date_from") or filters.get("date_to"):
        date_filter: dict[str, Any] = {}
        if filters.get("date_from"):
            date_filter["gte"] = filters["date_from"]
        if filters.get("date_to"):
            date_filter["lte"] = filters["date_to"]
        query_filters.append({"range": {"date": date_filter}})
    return query_filters


def keyword_search(query: str, filters: dict[str, Any] | None = None, size: int = 10) -> list[dict[str, Any]]:
    client = get_client()
    if client is None:
        return []

    if not ensure_index():
        return []
    try:
        response = client.search(
            index=OPENSEARCH_INDEX,
            body={
                "size": size,
                "query": {
                    "bool": {
                        "should": [
                            {
                                "multi_match": {
                                    "query": query,
                                    "fields": ["content^3", "title^2", "doc_type^1.5", "city^1.5", "source_url"],
                                    "type": "best_fields",
                                }
                            }
                        ],
                        "minimum_should_match": 1,
                        "filter": _filters_to_query(filters),
                    }
                },
            },
            request_timeout=OPENSEARCH_TIMEOUT,
        )
    except Exception as exc:
        record_diagnostic("opensearch", "OpenSearch keyword search failed", exc, query=query[:300], filters=filters)
        return []
    return [_normalize_hit(hit, "keyword") for hit in response.get("hits", {}).get("hits", [])]


def knn_search(vector: list[float], filters: dict[str, Any] | None = None, size: int = 10) -> list[dict[str, Any]]:
    client = get_client()
    if client is None or not vector:
        return []
    if filters:
        return []

    if not ensure_index():
        return []
    try:
        response = client.search(
            index=OPENSEARCH_INDEX,
            body={
                "size": size,
                "query": {
                    "knn": {
                        "embedding": {
                            "vector": vector,
                            "k": size,
                        }
                    }
                },
            },
            request_timeout=OPENSEARCH_TIMEOUT,
        )
    except Exception as exc:
        record_diagnostic("opensearch", "OpenSearch vector search failed", exc, filters=filters)
        return []
    return [_normalize_hit(hit, "vector") for hit in response.get("hits", {}).get("hits", [])]


def _normalize_hit(hit: dict[str, Any], source: str) -> dict[str, Any]:
    payload = hit.get("_source", {})
    payload["_score"] = hit.get("_score", 0.0)
    payload["_search_source"] = source
    return payload
