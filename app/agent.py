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
from app.text_cleaning import strip_accents

WEAK_SCORE_THRESHOLD = 0.75
WEAK_MIN_DOCUMENTS = 2


def time_budget_seconds() -> float:
    try:
        return float(get_secret("AGENT_TIME_BUDGET_SECONDS", "45"))
    except (TypeError, ValueError):
        return 45.0


def classify_question(question: str) -> dict:
    return classify_question_with_llm(question)


def _unique_document_count(results: list[dict]) -> int:
    return len({result.get("document_id") for result in results if result.get("document_id")})


def _top_score(results: list[dict]) -> float:
    return max((result.get("_score", result.get("score", 0)) for result in results), default=0.0)


def search_with_relance(query: str, limit: int = 50, deadline: float | None = None) -> tuple[list[dict], bool]:
    """Run retrieval.search, retrying once with a broadened query if the first pass looks weak.

    Skips the retry (rather than failing) once `deadline` (a time.perf_counter()
    value) has passed — the first-pass results are still returned.
    """
    results = retrieval.search(query, limit=limit)
    is_weak = _unique_document_count(results) < WEAK_MIN_DOCUMENTS or _top_score(results) < WEAK_SCORE_THRESHOLD
    if not is_weak:
        return results, False
    if deadline is not None and time.perf_counter() > deadline:
        return results, False

    broadened = broaden_query_with_llm(query)
    if not broadened or broadened.strip().lower() == query.strip().lower():
        return results, False

    retried = retrieval.search(broadened, limit=limit)
    if _top_score(retried) > _top_score(results):
        record_diagnostic("agent", "Relance search improved results", query=query, broadened=broadened)
        return retried, True
    return results, False


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

    classification = classify_question(question)
    trace["complexity"] = classification.get("complexity", "simple")
    trace["mode"] = classification.get("mode", "single")

    if classification.get("mode") == "multi" and classification.get("subqueries"):
        cross = merge_cross_reference(classification["subqueries"], limit=50, deadline=deadline)
        trace["relance"] = any(entry["relanced"] for entry in cross["sub_results"])
        trace["cross_reference_authors"] = sorted(f"{author} ({year})" for author, year in cross["overlap"])
        reranked = rerank_results_with_llm(question, cross["combined_results"], keep=20, max_candidates=30)
        summary_block = _cross_reference_summary(cross["overlap"])
        draft_answer = answer_from_sources(question, reranked, extra_context=summary_block)
    else:
        results, relanced = search_with_relance(question, limit=50, deadline=deadline)
        trace["relance"] = relanced
        reranked = rerank_results_with_llm(question, results, keep=20, max_candidates=30)
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
