from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from app import retrieval
from app.answer import (
    answer_from_sources,
    broaden_query_with_llm,
    classify_question_with_llm,
    get_secret,
    group_results_by_document,
    rerank_results_with_llm,
    source_blurbs_with_fallback,
    verify_and_revise_answer,
)
from app.diagnostics import record_diagnostic
from app.pilot_v2_store import (
    aggregate_authors,
    answered_interpellations,
    answered_postulates,
    fetch_document_chunks,
)
from app.text_cleaning import fix_mojibake, strip_accents
from municipal_pipeline.municipalities import MUNICIPALITIES

WEAK_SCORE_THRESHOLD = 0.75
WEAK_MIN_DOCUMENTS = 2
RERANK_CANDIDATE_LIMIT = 20
RERANK_KEEP_LIMIT = 30
GENERATION_PASSAGE_LIMIT = 15
SEARCH_ENABLED_CITY_LABELS = tuple(
    municipality.label
    for municipality in MUNICIPALITIES.values()
    if municipality.search_enabled
)

# Political-object documents are small (interpellations/postulats/motions
# average 4-5 chunks, max 14 — confirmed against the real DB) — cheap enough
# to pull in fully once identified, rather than trust that every relevant
# chunk (e.g. the municipal response, phrased very differently from the
# question) survived the embedding top-K. Capped to the best-scoring few
# documents so this can't quietly balloon the candidate pool.
EXPANDABLE_CATEGORIES = {"interpellation", "postulat", "motion"}
MAX_EXPANDABLE_CHUNKS = 15
MAX_EXPANDED_DOCUMENTS = 3

_COMPLEX_QUESTION_MARKERS = (
    "a la fois",
    "ainsi que",
    "compar",
    "crois",
    "difference",
    "en commun",
    "meme annee",
    "respectivement",
    "tous les deux",
    "versus",
)
_QUOTED_TEXT_PATTERN = re.compile(r'"[^"]*"|«[^»]*»|“[^”]*”')


def _notify(on_stage: Callable[[str], None] | None, label: str) -> None:
    if on_stage is not None:
        on_stage(label)


def _elapsed_ms(started_at: float) -> int:
    return round((time.perf_counter() - started_at) * 1000)


def _generation_results(results: list[dict]) -> list[dict]:
    """Bound the evidence sent to answer generation without hiding sources."""
    return results[:GENERATION_PASSAGE_LIMIT]


def _result_commune(result: dict) -> str:
    metadata = result.get("metadata") or {}
    return str(metadata.get("commune") or metadata.get("city") or "").strip()


def _interleave_result_groups(groups: list[list[dict]]) -> list[dict]:
    """Round-robin result groups while preserving each group's ranking."""
    combined: list[dict] = []
    seen_ids: set[str] = set()
    max_length = max((len(group) for group in groups), default=0)
    for index in range(max_length):
        for group in groups:
            if index >= len(group):
                continue
            result = group[index]
            result_id = result.get("id")
            if result_id and result_id in seen_ids:
                continue
            combined.append(result)
            if result_id:
                seen_ids.add(result_id)
    return combined


def _generation_results_for_filters(results: list[dict], filters: dict) -> list[dict]:
    """Keep evidence from every returned commune when "Toutes" is selected."""
    if str(filters.get("city") or "").lower() != "all":
        return _generation_results(results)

    by_commune: dict[str, list[dict]] = {}
    without_commune: list[dict] = []
    for result in results:
        commune = _result_commune(result)
        if commune:
            by_commune.setdefault(commune, []).append(result)
        else:
            without_commune.append(result)

    groups = [by_commune[commune] for commune in sorted(by_commune)]
    if without_commune:
        groups.append(without_commune)
    if len(groups) <= 1:
        return _generation_results(results)
    return _interleave_result_groups(groups)[:GENERATION_PASSAGE_LIMIT]


def _timed_source_blurbs(results: list[dict]) -> tuple[dict[str, str], int]:
    started_at = time.perf_counter()
    blurbs = source_blurbs_with_fallback(group_results_by_document(results))
    return blurbs, _elapsed_ms(started_at)


def time_budget_seconds() -> float:
    try:
        return float(get_secret("AGENT_TIME_BUDGET_SECONDS", "45"))
    except (TypeError, ValueError):
        return 45.0


def question_needs_llm_classification(question: str) -> bool:
    """Keep the LLM classifier for questions that may require decomposition.

    Most civic-document questions name one object, year, author, or topic and
    are unambiguously single-search. Comparison markers, conjunctions outside
    quoted titles, multiple quoted objects, or multiple years remain routed
    through the LLM so the speed-up does not silently flatten complex queries.
    """
    normalized = strip_accents(question).lower()
    if any(marker in normalized for marker in _COMPLEX_QUESTION_MARKERS):
        return True

    quoted_objects = _QUOTED_TEXT_PATTERN.findall(question)
    if len(quoted_objects) >= 2:
        return True

    without_quoted_titles = _QUOTED_TEXT_PATTERN.sub(" ", normalized)
    if re.search(r"\b(?:et|ou|entre|contre)\b", without_quoted_titles):
        return True

    if len(set(re.findall(r"\b20\d{2}\b", normalized))) >= 2:
        return True
    return False


def classify_question(question: str) -> dict:
    if not question_needs_llm_classification(question):
        return {
            "complexity": "simple",
            "mode": "single",
            "subqueries": [],
            "classification_source": "deterministic",
        }
    classification = classify_question_with_llm(question)
    return {**classification, "classification_source": "llm"}


def _aggregate_result_row_to_result(row: dict) -> dict:
    metadata = dict(row.get("metadata") or {})
    metadata.update({
        "title": row["title"],
        "category": row["category"],
        "doc_type": row["category"],
        "document_id": row["document_id"],
        "canonical_object": True,
    })
    if row.get("summary"):
        metadata["summary"] = row["summary"]
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
        metadata = row.get("metadata") or {}
        raw_communes = metadata.get("commune") or metadata.get("city")
        if isinstance(raw_communes, (list, tuple, set)):
            communes = tuple(
                sorted(str(commune).strip() for commune in raw_communes if str(commune).strip())
            )
        elif raw_communes:
            communes = (str(raw_communes).strip(),)
        else:
            communes = ()

        if len(communes) > 1:
            commune_group = "Plusieurs communes concernées"
        elif communes:
            commune_group = communes[0]
        else:
            commune_group = "Commune non précisée"

        entry = documents.setdefault(
            row["document_id"],
            {
                "title": row["title"],
                "authors": set(),
                "commune_group": commune_group,
                "category": row["category"],
                "metadata": metadata,
                "source_url": (
                    metadata.get("file_url")
                    or metadata.get("source_url")
                    or metadata.get("source_page_url")
                    or ""
                ),
            },
        )
        author = _aggregate_author(row)
        if author:
            entry["authors"].add(author)

    if not documents:
        answer = "Aucun document politique correspondant n’a été trouvé."
        return answer, []

    commune_groups: dict[str, list[dict]] = {}
    for info in documents.values():
        commune_groups.setdefault(info["commune_group"], []).append(info)

    lines: list[str] = []
    show_communes = len(commune_groups) > 1
    for commune, group_documents in sorted(commune_groups.items()):
        if show_communes:
            if lines:
                lines.append("")
            lines.append(f"**{commune}**")
        for info in sorted(group_documents, key=lambda item: item["title"]):
            lines.append(
                _political_document_line(
                    category=info["category"],
                    title=info["title"],
                    authors=sorted(info["authors"]),
                    political_date=_political_date_from_metadata(
                        info["metadata"],
                        info["title"],
                        info["category"],
                    ),
                    pdf_url=info["source_url"],
                )
            )

    answer = "\n".join(lines)
    results = []
    seen_document_ids = set()
    for row in rows:
        document_id = row["document_id"]
        if document_id in seen_document_ids:
            continue
        seen_document_ids.add(document_id)
        results.append(_aggregate_result_row_to_result(row))
    return answer, results


_FRENCH_MONTHS = (
    "",
    "janvier",
    "février",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "août",
    "septembre",
    "octobre",
    "novembre",
    "décembre",
)


def _format_french_date(value: str | None) -> str:
    match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", str(value or ""))
    if not match:
        return str(value or "")
    year, month, day = (int(part) for part in match.groups())
    return f"{day} {_FRENCH_MONTHS[month]} {year}"


_POLITICAL_TYPE_LABELS = {
    "interpellation": "Interpellation",
    "motion": "Motion",
    "postulat": "Postulat",
}


def _aggregate_author(row: dict) -> str:
    name = fix_mojibake(str(row.get("author_name") or "")).strip()
    if not name:
        return ""
    civility = fix_mojibake(str(row.get("civility") or "")).strip()
    party = fix_mojibake(str(row.get("party") or "")).strip()
    if civility and not name.lower().startswith(civility.lower()):
        name = f"{civility} {name}"
    if party and f"({party})".lower() not in name.lower():
        name = f"{name} ({party})"
    return name


def _join_authors(authors: list[str]) -> str:
    authors = [
        fix_mojibake(str(author)).strip()
        for author in authors
        if str(author).strip()
    ]
    if len(authors) < 2:
        return authors[0] if authors else ""
    return ", ".join(authors[:-1]) + f" et {authors[-1]}"


def _political_title_parts(
    category: str,
    title: str,
    authors: list[str],
) -> tuple[str, str, str]:
    label = _POLITICAL_TYPE_LABELS.get(category, "Document")
    clean_title = fix_mojibake(str(title or "")).strip()
    full_title = re.match(
        r"^(?:(Interpellation|Motion|Postulat)|"
        r"((?:Complément de )?réponse à l['’]interpellation))\s+"
        r"(?:de|du|des|d['’])\s+(.+?)"
        r"(?:,\s*)?\s+(?:intitul[ée]e?|ayant pour titre)\s*"
        r"[«\"]\s*(.+?)\s*[»\"]\s*(.*?)\s*$",
        clean_title,
        flags=re.IGNORECASE,
    )
    if full_title:
        subject = " ".join(
            part.strip()
            for part in (full_title.group(4), full_title.group(5))
            if part and part.strip()
        )
        return (
            (
                "Interpellation"
                if full_title.group(2)
                else full_title.group(1).capitalize()
            ),
            full_title.group(3).strip(" ,"),
            subject,
        )

    subject = clean_title
    if clean_title.lower().endswith(".pdf"):
        stem = clean_title[:-4]
        subject = stem.split("_")[-1].replace("-", " ").strip()
    subject = re.sub(
        rf"^{re.escape(label)}\s*[:\-–—]?\s*",
        "",
        subject,
        flags=re.IGNORECASE,
    ).strip().strip(" «»\"")
    return label, _join_authors(authors), subject or clean_title


def _political_date_from_metadata(
    metadata: dict,
    title: str,
    category: str,
) -> str:
    filename_date = re.search(
        r"(?<!\d)20\d{2}-\d{2}-\d{2}(?!\d)",
        str(title or ""),
    )
    additional = metadata.get("additional_metadata") or {}
    extra = additional.get(f"{category}_metadata") or {}
    return (
        filename_date.group(0)
        if filename_date
        else str(
            extra.get("deposit_date")
            or extra.get("interpellation_date")
            or metadata.get("document_date")
            or metadata.get("listing_date")
            or ""
        )
    )


def _normalized_response_reference(reference: str, commune: str) -> str:
    clean = fix_mojibake(str(reference or "")).strip()
    if not clean:
        return ""
    year_first = re.fullmatch(
        r"(20\d{2})\s*/\s*ri\s*0*(\d+)([a-z]*)",
        clean,
        flags=re.IGNORECASE,
    )
    if year_first:
        return (
            f"RI {int(year_first.group(2)):02d}"
            f"{year_first.group(3).lower()}/{year_first.group(1)}"
        )
    ri_first = re.fullmatch(
        r"ri\s*0*(\d+)([a-z]*)\s*/\s*(20\d{2})",
        clean,
        flags=re.IGNORECASE,
    )
    if ri_first:
        return (
            f"RI {int(ri_first.group(1)):02d}"
            f"{ri_first.group(2).lower()}/{ri_first.group(3)}"
        )
    number = re.fullmatch(r"0*(\d+)\s*/\s*(20\d{2})", clean)
    if number and commune == "Vevey":
        return f"RI {int(number.group(1)):02d}/{number.group(2)}"
    if number:
        return (
            f"Réponse municipale n° {int(number.group(1))}/"
            f"{number.group(2)}"
        )
    return clean


def _political_document_line(
    *,
    category: str,
    title: str,
    authors: list[str],
    political_date: str,
    pdf_url: str,
    response_reference: str = "",
    response_date: str = "",
    commune: str = "",
) -> str:
    label, title_author, subject = _political_title_parts(
        category,
        title,
        authors,
    )
    author_part = f" de {title_author}" if title_author else ""
    normalized_reference = _normalized_response_reference(
        response_reference,
        commune,
    )
    has_response_details = bool(normalized_reference or response_date)
    date_part = (
        f" ({_format_french_date(political_date)})"
        if political_date and not has_response_details
        else ""
    )
    source_label = (
        "PDF"
        if (
            ".pdf" in pdf_url.lower()
            or "download.asp" in pdf_url.lower()
        )
        else "Lien officiel"
    )
    pdf_part = (
        f" — [*{source_label}*]({pdf_url})"
        if pdf_url
        else ""
    )
    response_parts = []
    if normalized_reference:
        response_parts.append(normalized_reference)
    if response_date:
        response_parts.append(_format_french_date(response_date))
    response_part = (
        f" *({', '.join(response_parts)})*"
        if response_parts
        else ""
    )
    return (
        f"- {label}{author_part} : *« {subject} »*"
        f"{date_part}{pdf_part}{response_part}."
    )


def _answered_result(row: dict, rank: int) -> dict:
    metadata = dict(row.get("metadata") or {})
    metadata.update(
        {
            "title": row["title"],
            "category": "interpellation",
            "doc_type": "interpellation",
            "document_id": row["source_document_id"],
            "commune": row["commune"],
            "file_url": row["response_url"],
            "source_url": row["response_url"],
            "response_number": row.get("response_number"),
            "response_reference": row.get("response_reference"),
            "political_date": row.get("political_date"),
            "response_date": row.get("response_date"),
            "canonical_object": True,
        }
    )
    score = 1.0 - rank * 0.000001
    content = " | ".join(
        part
        for part in (
            f"Commune: {row['commune']}",
            (
                f"Réponse municipale: {row.get('response_number')}"
                if row.get("response_number")
                else ""
            ),
            (
                f"Date de réponse: {row.get('response_date')}"
                if row.get("response_date")
                else ""
            ),
        )
        if part
    )
    return {
        "id": f"{row['source_document_id']}#answered",
        "chunk_id": f"{row['source_document_id']}#answered",
        "document_id": row["source_document_id"],
        "chunk_index": 0,
        "component": "municipal_response",
        "content": content,
        "text": content,
        "title": row["title"],
        "category": "interpellation",
        "doc_type": "interpellation",
        "source_url": row["response_url"],
        "metadata": metadata,
        "score": score,
        "_score": score,
        "_search_source": "answered_interpellations_v2",
    }


def run_answered_interpellations_query(
    filters: dict,
) -> tuple[str, list[dict]]:
    """Build an exact, citation-safe answer from response relationships."""
    rows = answered_interpellations(filters)
    results = [
        _answered_result(row, rank)
        for rank, row in enumerate(rows)
    ]
    year = str(filters.get("response_year") or "")
    if not rows:
        suffix = f" en {year}" if year else ""
        return (
            "Aucune interpellation avec une réponse municipale effectivement "
            f"fournie{suffix} n’a été trouvée dans les métadonnées indexées.",
            [],
        )

    commune_count = len(
        {
            str(row.get("commune") or "").strip()
            for row in rows
            if str(row.get("commune") or "").strip()
        }
    )
    lines: list[str] = []
    current_commune = None
    for row in rows:
        commune = fix_mojibake(
            str(row.get("commune") or "Commune inconnue")
        )
        if commune_count > 1 and commune != current_commune:
            if lines:
                lines.append("")
            lines.append(f"**{commune}**")
            current_commune = commune
        lines.append(
            _political_document_line(
                category="interpellation",
                title=str(row.get("title") or "Interpellation"),
                authors=list(row.get("authors") or []),
                political_date=str(row.get("political_date") or ""),
                pdf_url=str(row.get("response_url") or ""),
                response_reference=str(
                    row.get("response_reference")
                    or row.get("response_number")
                    or ""
                ),
                response_date=str(row.get("response_date") or ""),
                commune=commune,
            )
        )
    return "\n".join(lines).lstrip(), results


def _answered_postulate_result(row: dict, rank: int) -> dict:
    result = _answered_result(row, rank)
    result["category"] = "postulat"
    result["doc_type"] = "postulat"
    result["_search_source"] = "answered_postulates_v2"
    result["metadata"]["category"] = "postulat"
    result["metadata"]["doc_type"] = "postulat"
    return result


def run_answered_postulates_query(
    filters: dict,
) -> tuple[str, list[dict]]:
    """Build an exact answer from postulate-to-response relationships."""
    rows = answered_postulates(filters)
    results = [
        _answered_postulate_result(row, rank)
        for rank, row in enumerate(rows)
    ]
    year = str(filters.get("response_year") or "")
    if not rows:
        suffix = f" en {year}" if year else ""
        return (
            "Aucun postulat avec une réponse municipale effectivement "
            f"fournie{suffix} n’a été trouvé dans les métadonnées indexées.",
            [],
        )

    commune_count = len(
        {
            str(row.get("commune") or "").strip()
            for row in rows
            if str(row.get("commune") or "").strip()
        }
    )
    lines: list[str] = []
    current_commune = None
    for row in rows:
        commune = fix_mojibake(
            str(row.get("commune") or "Commune inconnue")
        )
        if commune_count > 1 and commune != current_commune:
            if lines:
                lines.append("")
            lines.append(f"**{commune}**")
            current_commune = commune
        lines.append(
            _political_document_line(
                category="postulat",
                title=str(row.get("title") or "Postulat"),
                authors=list(row.get("authors") or []),
                political_date=str(row.get("political_date") or ""),
                pdf_url=str(row.get("response_url") or ""),
                response_reference=str(
                    row.get("response_reference") or ""
                ),
                response_date=str(row.get("response_date") or ""),
                commune=commune,
            )
        )
    return "\n".join(lines).lstrip(), results


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


def search_with_relance(
    query: str,
    limit: int = 50,
    filters: dict | None = None,
    deadline: float | None = None,
    on_stage: Callable[[str], None] | None = None,
) -> tuple[list[dict], bool]:
    """Run retrieval.search, retrying once with a broadened query if the first pass looks weak.

    Skips the retry (rather than failing) once `deadline` (a time.perf_counter()
    value) has passed — the first-pass results are still returned. Small
    political-object documents are expanded to their full chunk set before
    returning either way (see expand_small_documents).
    """
    results = retrieval.search(query, limit=limit, filters=filters)
    is_weak = _unique_document_count(results) < WEAK_MIN_DOCUMENTS or _top_score(results) < WEAK_SCORE_THRESHOLD
    if is_weak and not (deadline is not None and time.perf_counter() > deadline):
        _notify(on_stage, "Résultats faibles, recherche élargie en cours...")
        broadened = broaden_query_with_llm(query)
        if broadened and broadened.strip().lower() != query.strip().lower():
            retried = retrieval.search(broadened, limit=limit, filters=filters)
            if _top_score(retried) > _top_score(results):
                record_diagnostic("agent", "Relance search improved results", query=query, broadened=broadened)
                return expand_small_documents(retried), True

    return expand_small_documents(results), False


def search_with_city_balance(
    query: str,
    limit: int = 50,
    filters: dict | None = None,
    deadline: float | None = None,
    on_stage: Callable[[str], None] | None = None,
) -> tuple[list[dict], bool]:
    """Search each enabled commune independently when the UI scope is "Toutes".

    A single global top-K can otherwise be monopolized by the commune whose
    documents happen to score slightly higher, leaving the answer generator
    with no evidence at all from the other communes.
    """
    filters = dict(filters or {})
    if str(filters.get("city") or "").lower() != "all":
        return search_with_relance(
            query,
            limit=limit,
            filters=filters,
            deadline=deadline,
            on_stage=on_stage,
        )

    city_labels = SEARCH_ENABLED_CITY_LABELS
    if len(city_labels) <= 1:
        return search_with_relance(
            query,
            limit=limit,
            filters=filters,
            deadline=deadline,
            on_stage=on_stage,
        )

    _notify(on_stage, "Recherche équilibrée dans chaque commune...")
    per_city_limit = max(1, limit // len(city_labels))

    def search_city(city: str) -> tuple[list[dict], bool]:
        return search_with_relance(
            query,
            limit=per_city_limit,
            filters={**filters, "city": city},
            deadline=deadline,
            on_stage=None,
        )

    with ThreadPoolExecutor(max_workers=min(len(city_labels), 4)) as pool:
        city_searches = list(pool.map(search_city, city_labels))

    combined = _interleave_result_groups([results for results, _relanced in city_searches])
    return combined, any(relanced for _results, relanced in city_searches)


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


def merge_cross_reference(
    subqueries: list[dict],
    limit: int = 50,
    filters: dict | None = None,
    deadline: float | None = None,
    on_stage: Callable[[str], None] | None = None,
) -> dict:
    """Run independent subqueries concurrently, then compute their real metadata overlap."""
    for index, sub in enumerate(subqueries, start=1):
        _notify(on_stage, f"Recherche {index}/{len(subqueries)}: {sub.get('label') or sub['query']}")

    def run_subquery(sub: dict) -> dict:
        # Streamlit callbacks stay on the main thread; the worker only performs
        # retrieval and returns data. executor.map preserves subquery order.
        results, relanced = search_with_city_balance(
            sub["query"],
            limit=limit,
            filters=filters,
            deadline=deadline,
            on_stage=None,
        )
        return {
            "label": sub.get("label") or sub["query"],
            "results": results,
            "relanced": relanced,
        }

    with ThreadPoolExecutor(max_workers=min(len(subqueries), 4)) as pool:
        sub_results = list(pool.map(run_subquery, subqueries))

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


def run_agentic_pipeline(
    question: str,
    filters: dict | None = None,
    on_stage: Callable[[str], None] | None = None,
) -> tuple[str, list[dict], dict]:
    started_at = time.perf_counter()
    budget = time_budget_seconds()
    deadline = started_at + budget
    filters = dict(filters or {})

    trace: dict = {
        "complexity": "simple",
        "mode": "single",
        "relance": False,
        "verification_claims": [],
        "timings_ms": {},
        "budget_seconds": budget,
        "budget_exceeded": False,
        "rerank_candidate_limit": RERANK_CANDIDATE_LIMIT,
        "generation_passage_limit": GENERATION_PASSAGE_LIMIT,
        "filters": filters,
    }

    _notify(on_stage, "Analyse de la question...")
    stage_started_at = time.perf_counter()
    answered_filters = retrieval.detect_answered_interpellations_query(
        question
    )
    if answered_filters is not None:
        ui_filters = dict(filters)
        if (
            ui_filters.get("year")
            and not answered_filters.get("response_year")
        ):
            answered_filters["response_year"] = ui_filters.pop("year")
        else:
            ui_filters.pop("year", None)
        answered_filters = {**answered_filters, **ui_filters}
        trace["timings_ms"]["routing"] = _elapsed_ms(stage_started_at)
        trace["mode"] = "aggregate"
        trace["aggregate_kind"] = "answered_interpellations"
        trace["aggregate_filters"] = answered_filters
        _notify(on_stage, "Vérification des réponses liées dans la base...")
        stage_started_at = time.perf_counter()
        answer, results = run_answered_interpellations_query(
            answered_filters
        )
        trace["timings_ms"]["aggregate_query"] = _elapsed_ms(
            stage_started_at
        )
        trace["duration_seconds"] = round(
            time.perf_counter() - started_at, 1
        )
        trace["timings_ms"]["total"] = _elapsed_ms(started_at)
        record_diagnostic(
            "agent",
            "Agentic pipeline trace",
            trace=trace,
            question=question[:200],
        )
        return answer, results, trace

    answered_postulate_filters = (
        retrieval.detect_answered_postulates_query(question)
    )
    if answered_postulate_filters is not None:
        ui_filters = dict(filters)
        if (
            ui_filters.get("year")
            and not answered_postulate_filters.get("response_year")
        ):
            answered_postulate_filters["response_year"] = ui_filters.pop(
                "year"
            )
        else:
            ui_filters.pop("year", None)
        answered_postulate_filters = {
            **answered_postulate_filters,
            **ui_filters,
        }
        trace["timings_ms"]["routing"] = _elapsed_ms(stage_started_at)
        trace["mode"] = "aggregate"
        trace["aggregate_kind"] = "answered_postulates"
        trace["aggregate_filters"] = answered_postulate_filters
        _notify(on_stage, "Vérification des réponses liées dans la base...")
        stage_started_at = time.perf_counter()
        answer, results = run_answered_postulates_query(
            answered_postulate_filters
        )
        trace["timings_ms"]["aggregate_query"] = _elapsed_ms(
            stage_started_at
        )
        trace["duration_seconds"] = round(
            time.perf_counter() - started_at,
            1,
        )
        trace["timings_ms"]["total"] = _elapsed_ms(started_at)
        record_diagnostic(
            "agent",
            "Agentic pipeline trace",
            trace=trace,
            question=question[:200],
        )
        return answer, results, trace

    aggregate_filters = retrieval.detect_aggregate_query(question)
    trace["timings_ms"]["routing"] = _elapsed_ms(stage_started_at)
    if aggregate_filters is not None:
        aggregate_filters = {**aggregate_filters, **filters}
        # Deterministic count/enumeration over metadata — no LLM in the loop
        # for the count itself, so no verification pass is needed either.
        trace["mode"] = "aggregate"
        trace["aggregate_filters"] = aggregate_filters
        _notify(on_stage, "Comptage exact dans la base...")
        stage_started_at = time.perf_counter()
        answer, results = run_aggregate_query(aggregate_filters)
        trace["timings_ms"]["aggregate_query"] = _elapsed_ms(stage_started_at)
        trace["duration_seconds"] = round(time.perf_counter() - started_at, 1)
        trace["timings_ms"]["total"] = _elapsed_ms(started_at)
        record_diagnostic("agent", "Agentic pipeline trace", trace=trace, question=question[:200])
        return answer, results, trace

    stage_started_at = time.perf_counter()
    classification = classify_question(question)
    trace["timings_ms"]["classification"] = _elapsed_ms(stage_started_at)
    trace["complexity"] = classification.get("complexity", "simple")
    trace["mode"] = classification.get("mode", "single")
    trace["classification_source"] = classification.get("classification_source", "llm")

    if classification.get("mode") == "multi" and classification.get("subqueries"):
        _notify(on_stage, "Question complexe détectée: recherche en plusieurs étapes...")
        stage_started_at = time.perf_counter()
        cross = merge_cross_reference(
            classification["subqueries"],
            limit=50,
            filters=filters,
            deadline=deadline,
            on_stage=on_stage,
        )
        trace["timings_ms"]["retrieval"] = _elapsed_ms(stage_started_at)
        trace["relance"] = any(entry["relanced"] for entry in cross["sub_results"])
        trace["cross_reference_authors"] = sorted(f"{author} ({year})" for author, year in cross["overlap"])
        _notify(on_stage, "Sélection des passages les plus pertinents...")
        stage_started_at = time.perf_counter()
        reranked = rerank_results_with_llm(
            question,
            cross["combined_results"],
            keep=RERANK_KEEP_LIMIT,
            max_candidates=RERANK_CANDIDATE_LIMIT,
        )
        trace["timings_ms"]["reranking"] = _elapsed_ms(stage_started_at)
        summary_block = _cross_reference_summary(cross["overlap"])
        _notify(on_stage, "Rédaction de la réponse...")
        generation_results = _generation_results_for_filters(reranked, filters)
        stage_started_at = time.perf_counter()
        draft_answer = answer_from_sources(question, generation_results, extra_context=summary_block)
        trace["timings_ms"]["generation"] = _elapsed_ms(stage_started_at)
    else:
        _notify(on_stage, "Recherche dans les documents...")
        stage_started_at = time.perf_counter()
        results, relanced = search_with_city_balance(
            question,
            limit=50,
            filters=filters,
            deadline=deadline,
            on_stage=on_stage,
        )
        trace["timings_ms"]["retrieval"] = _elapsed_ms(stage_started_at)
        trace["relance"] = relanced
        _notify(on_stage, "Sélection des passages les plus pertinents...")
        stage_started_at = time.perf_counter()
        reranked = rerank_results_with_llm(
            question,
            results,
            keep=RERANK_KEEP_LIMIT,
            max_candidates=RERANK_CANDIDATE_LIMIT,
        )
        trace["timings_ms"]["reranking"] = _elapsed_ms(stage_started_at)
        _notify(on_stage, "Rédaction de la réponse...")
        generation_results = _generation_results_for_filters(reranked, filters)
        stage_started_at = time.perf_counter()
        draft_answer = answer_from_sources(question, generation_results)
        trace["timings_ms"]["generation"] = _elapsed_ms(stage_started_at)

    trace["generation_passages"] = len(generation_results)
    trace["reranked_passages"] = len(reranked)

    # Verification is the single slowest stage (runs on the stronger model,
    # 4-6s) and its real value is catching cross-document/decomposed answers
    # inventing an overlap that isn't really there — exactly what "complex"/
    # "multi" mode produces. A "simple"/"single" question is answered from
    # one document's own reranked passages with nothing to synthesize across,
    # UNLESS the first-pass search was weak enough to need relance — a weak
    # match is exactly when a model is more likely to compensate by inventing
    # plausible-sounding detail, so that case still gets verified.
    skip_verification = (
        trace["complexity"] == "simple" and trace["mode"] == "single" and not trace.get("relance", False)
    )

    # Source blurbs don't depend on the verified/revised answer, only on the
    # already-reranked source list — so run that LLM call on a background
    # thread while verification (the slower call, on the stronger model)
    # runs on the main thread, instead of paying for both in sequence.
    with ThreadPoolExecutor(max_workers=1) as pool:
        blurbs_future = pool.submit(_timed_source_blurbs, reranked)

        if skip_verification:
            trace["verification_skipped"] = True
            final_answer, claims = draft_answer, []
            trace["timings_ms"]["verification"] = 0
        elif time.perf_counter() > deadline:
            # Time budget already spent on search/decomposition/answer — skip the
            # verification pass rather than risk running well past the budget.
            trace["budget_exceeded"] = True
            final_answer, claims = draft_answer, []
            trace["timings_ms"]["verification"] = 0
        else:
            _notify(on_stage, "Vérification de la réponse...")
            stage_started_at = time.perf_counter()
            final_answer, claims = verify_and_revise_answer(question, draft_answer, generation_results)
            trace["timings_ms"]["verification"] = _elapsed_ms(stage_started_at)

        trace["source_blurbs"], trace["timings_ms"]["source_blurbs"] = blurbs_future.result()

    trace["verification_claims"] = claims
    trace["duration_seconds"] = round(time.perf_counter() - started_at, 1)
    trace["timings_ms"]["total"] = _elapsed_ms(started_at)

    record_diagnostic("agent", "Agentic pipeline trace", trace=trace, question=question[:200])

    return final_answer, reranked, trace
