from __future__ import annotations

import time

from app import retrieval
from app.answer import (
    answer_from_sources,
    broaden_query_with_llm,
    classify_question_with_llm,
    get_secret,
    rerank_results_with_llm,
    verify_and_revise_answer,
)
from app.diagnostics import record_diagnostic
from app.pilot_v2_store import aggregate_authors, fetch_document_chunks
from app.text_cleaning import strip_accents

WEAK_SCORE_THRESHOLD = 0.75
WEAK_MIN_DOCUMENTS = 2

# Political-object documents are small (interpellations/postulats/motions
# average 4-5 chunks, max 14 — confirmed against the real DB) — cheap enough
# to pull in fully once identified, rather than trust that every relevant
# chunk (e.g. the municipal response, phrased very differently from the
# question) survived the embedding top-K. Capped to the best-scoring few
# documents so this can't quietly balloon the candidate pool.
EXPANDABLE_CATEGORIES = {"interpellation", "postulat", "motion"}
MAX_EXPANDABLE_CHUNKS = 15
MAX_EXPANDED_DOCUMENTS = 3

_CIVILITY_NOUN = {"Mme": "femmes", "M.": "hommes"}
_DOC_TYPE_NOUN = {
    "interpellations": "interpellations",
    "postulats": "postulats",
    "motions": "motions",
    "reglement-conseil-communal": "documents du règlement",
}


def time_budget_seconds() -> float:
    try:
        return float(get_secret("AGENT_TIME_BUDGET_SECONDS", "45"))
    except (TypeError, ValueError):
        return 45.0


def classify_question(question: str) -> dict:
    return classify_question_with_llm(question)


def _aggregate_result_row_to_result(row: dict) -> dict:
    metadata = dict(row.get("metadata") or {})
    metadata.update({
        "title": row["title"],
        "category": row["category"],
        "doc_type": row["category"],
        "document_id": row["document_id"],
        "canonical_object": True,
    })
    source_url = metadata.get("file_url") or metadata.get("source_url") or metadata.get("source_page_url") or ""
    return {
        "id": f"{row['document_id']}#aggregate",
        "chunk_id": f"{row['document_id']}#aggregate",
        "document_id": row["document_id"],
        "chunk_index": 0,
        "component": "aggregate",
        "content": "",
        "text": "",
        "title": row["title"],
        "category": row["category"],
        "doc_type": row["category"],
        "source_url": source_url,
        "metadata": metadata,
        "score": 1.0,
        "_score": 1.0,
        "_search_source": "aggregate_v2",
    }


def run_aggregate_query(filters: dict) -> tuple[str, list[dict]]:
    """A real count/enumeration, computed in code from structured metadata —
    no LLM involved, so there's no risk of it undercounting from a limited
    passage sample the way semantic search + generation would.
    """
    rows = aggregate_authors(filters)

    documents: dict[str, dict] = {}
    for row in rows:
        entry = documents.setdefault(row["document_id"], {"title": row["title"], "authors": set()})
        entry["authors"].add(row["author_name"])

    subject = _DOC_TYPE_NOUN.get(filters.get("doc_type"), "documents")
    who = _CIVILITY_NOUN.get(filters.get("civility"))
    qualifier = f" déposé(e)s par des {who}" if who else ""
    year_note = f" en {filters['year']}" if filters.get("year") else ""

    lines = [
        f"Décompte exact sur les métadonnées de la base ({len(documents)} {subject}{qualifier}{year_note}) "
        "— pas une estimation sur un échantillon de passages retrouvés."
    ]
    if documents:
        lines.append("")
        for info in sorted(documents.values(), key=lambda item: item["title"]):
            lines.append(f"- {info['title']} — {', '.join(sorted(info['authors']))}")

    answer = "\n".join(lines)
    results = [_aggregate_result_row_to_result(row) for row in rows]
    return answer, results


def _unique_document_count(results: list[dict]) -> int:
    return len({result.get("document_id") for result in results if result.get("document_id")})


def _top_score(results: list[dict]) -> float:
    return max((result.get("_score", result.get("score", 0)) for result in results), default=0.0)


def expand_small_documents(results: list[dict]) -> list[dict]:
    """Pull in the rest of a small political-object document's chunks once any
    one of its chunks is already in the candidate pool.

    Deterministic (no confidence threshold to tune): eligibility is just
    "small category, present in the pool", capped to the best-scoring
    MAX_EXPANDED_DOCUMENTS so a handful of loosely-related documents can't
    each contribute noise. New chunks inherit their triggering document's
    best score (rather than floating unscored) and the whole list is
    re-sorted, so they land at a sensible rank instead of risking silent
    truncation by rerank_results_with_llm's max_candidates cutoff.
    """
    if not results:
        return results

    best_score_by_document: dict[str, float] = {}
    category_by_document: dict[str, str] = {}
    seen_chunk_ids: set[str] = set()
    for result in results:
        seen_chunk_ids.add(result["id"])
        document_id = result.get("document_id")
        if not document_id:
            continue
        category_by_document[document_id] = result.get("category", "")
        score = result.get("_score", result.get("score", 0))
        if score > best_score_by_document.get(document_id, -1):
            best_score_by_document[document_id] = score

    expandable_documents = [
        document_id
        for document_id, category in category_by_document.items()
        if category in EXPANDABLE_CATEGORIES
    ]
    expandable_documents.sort(key=lambda document_id: best_score_by_document[document_id], reverse=True)

    expanded = list(results)
    for document_id in expandable_documents[:MAX_EXPANDED_DOCUMENTS]:
        chunks = fetch_document_chunks(document_id, best_score_by_document[document_id])
        if len(chunks) > MAX_EXPANDABLE_CHUNKS:
            continue
        for chunk in chunks:
            if chunk["id"] not in seen_chunk_ids:
                expanded.append(chunk)
                seen_chunk_ids.add(chunk["id"])

    expanded.sort(key=lambda result: result.get("_score", result.get("score", 0)), reverse=True)
    return expanded


def search_with_relance(query: str, limit: int = 50, deadline: float | None = None) -> tuple[list[dict], bool]:
    """Run retrieval.search, retrying once with a broadened query if the first pass looks weak.

    Skips the retry (rather than failing) once `deadline` (a time.perf_counter()
    value) has passed — the first-pass results are still returned. Small
    political-object documents are expanded to their full chunk set before
    returning either way (see expand_small_documents).
    """
    results = retrieval.search(query, limit=limit)
    is_weak = _unique_document_count(results) < WEAK_MIN_DOCUMENTS or _top_score(results) < WEAK_SCORE_THRESHOLD
    if is_weak and not (deadline is not None and time.perf_counter() > deadline):
        broadened = broaden_query_with_llm(query)
        if broadened and broadened.strip().lower() != query.strip().lower():
            retried = retrieval.search(broadened, limit=limit)
            if _top_score(retried) > _top_score(results):
                record_diagnostic("agent", "Relance search improved results", query=query, broadened=broadened)
                return expand_small_documents(retried), True

    return expand_small_documents(results), False


def _result_year(result: dict) -> str:
    metadata = result.get("metadata") or {}
    return str(metadata.get("listing_year") or metadata.get("year") or "")


def _extract_author_years(result: dict) -> set[tuple[str, str]]:
    """(author, year) pairs, not just author names — "the same year" is part of the question."""
    metadata = result.get("metadata") or {}
    additional = metadata.get("additional_metadata") or {}
    year = _result_year(result)
    pairs = set()
    for value in additional.values():
        if not isinstance(value, dict):
            continue
        for author in value.get("authors") or []:
            if isinstance(author, dict) and author.get("name"):
                pairs.add((strip_accents(str(author["name"])).lower().strip(), year))
    return pairs


def merge_cross_reference(subqueries: list[dict], limit: int = 50, deadline: float | None = None) -> dict:
    """Run one search per subquery and compute the real (author, year) overlap across them in code."""
    sub_results = []
    for sub in subqueries:
        results, relanced = search_with_relance(sub["query"], limit=limit, deadline=deadline)
        sub_results.append({"label": sub.get("label") or sub["query"], "results": results, "relanced": relanced})

    matches_by_pair: dict[tuple[str, str], dict[str, list[dict]]] = {}
    for entry in sub_results:
        seen_documents = set()
        for result in entry["results"]:
            document_id = result.get("document_id")
            if document_id in seen_documents:
                continue
            for author, year in _extract_author_years(result):
                if not year:
                    continue
                bucket = matches_by_pair.setdefault((author, year), {})
                bucket.setdefault(entry["label"], []).append(result)
                seen_documents.add(document_id)

    overlap = {
        pair: matches
        for pair, matches in matches_by_pair.items()
        if len(sub_results) >= 2 and len(matches) >= len(sub_results)
    }

    combined_results: list[dict] = []
    seen_ids = set()
    for entry in sub_results:
        for result in entry["results"]:
            if result["id"] not in seen_ids:
                combined_results.append(result)
                seen_ids.add(result["id"])

    return {"sub_results": sub_results, "overlap": overlap, "combined_results": combined_results}


def _cross_reference_summary(overlap: dict) -> str:
    if not overlap:
        return (
            "Croisement des sous-recherches (vérifié sur les métadonnées des documents, "
            "pas une supposition du modèle): aucun auteur n'a d'objet correspondant dans "
            "chaque sous-recherche pour la même année."
        )
    lines = [
        "Croisement des sous-recherches (vérifié sur les métadonnées des documents, "
        "pas une supposition du modèle) — auteur et année présents dans chaque sous-recherche:"
    ]
    for (author, year), matches_by_label in overlap.items():
        lines.append(f"- {author} ({year}):")
        for label, results in matches_by_label.items():
            titles = ", ".join(sorted({result["title"] for result in results}))
            lines.append(f"  - {label}: {titles}")
    return "\n".join(lines)


def run_agentic_pipeline(question: str) -> tuple[str, list[dict], dict]:
    started_at = time.perf_counter()
    budget = time_budget_seconds()
    deadline = started_at + budget

    trace: dict = {
        "complexity": "simple",
        "mode": "single",
        "relance": False,
        "verification_claims": [],
        "budget_seconds": budget,
        "budget_exceeded": False,
    }

    aggregate_filters = retrieval.detect_aggregate_query(question)
    if aggregate_filters is not None:
        # Deterministic count/enumeration over metadata — no LLM in the loop
        # for the count itself, so no verification pass is needed either.
        trace["mode"] = "aggregate"
        trace["aggregate_filters"] = aggregate_filters
        answer, results = run_aggregate_query(aggregate_filters)
        trace["duration_seconds"] = round(time.perf_counter() - started_at, 1)
        record_diagnostic("agent", "Agentic pipeline trace", trace=trace, question=question[:200])
        return answer, results, trace

    classification = classify_question(question)
    trace["complexity"] = classification.get("complexity", "simple")
    trace["mode"] = classification.get("mode", "single")

    if classification.get("mode") == "multi" and classification.get("subqueries"):
        cross = merge_cross_reference(classification["subqueries"], limit=50, deadline=deadline)
        trace["relance"] = any(entry["relanced"] for entry in cross["sub_results"])
        trace["cross_reference_authors"] = sorted(f"{author} ({year})" for author, year in cross["overlap"])
        reranked = rerank_results_with_llm(question, cross["combined_results"], keep=30, max_candidates=30)
        summary_block = _cross_reference_summary(cross["overlap"])
        draft_answer = answer_from_sources(question, reranked, extra_context=summary_block)
    else:
        results, relanced = search_with_relance(question, limit=50, deadline=deadline)
        trace["relance"] = relanced
        reranked = rerank_results_with_llm(question, results, keep=30, max_candidates=30)
        draft_answer = answer_from_sources(question, reranked)

    if time.perf_counter() > deadline:
        # Time budget already spent on search/decomposition/answer — skip the
        # verification pass rather than risk running well past the budget.
        trace["budget_exceeded"] = True
        final_answer, claims = draft_answer, []
    else:
        final_answer, claims = verify_and_revise_answer(question, draft_answer, reranked)
    trace["verification_claims"] = claims
    trace["duration_seconds"] = round(time.perf_counter() - started_at, 1)

    record_diagnostic("agent", "Agentic pipeline trace", trace=trace, question=question[:200])

    return final_answer, reranked, trace
