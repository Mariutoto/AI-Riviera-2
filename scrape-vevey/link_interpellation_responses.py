from __future__ import annotations

import argparse
import difflib
import hashlib
import html
import json
import re
import unicodedata
from pathlib import Path


def normalize(value: str) -> str:
    value = "".join(
        character
        for character in unicodedata.normalize("NFKD", value.casefold())
        if not unicodedata.combining(character)
    )
    quoted = re.findall(r"[«\"]([^»\"]{8,})[»\"]", value)
    if quoted:
        value = quoted[-1]
    value = re.sub(r"\.pdf$", "", value)
    value = re.sub(
        r"\b(?:reponse|municipale|interpellation|intitulee?|monsieur|madame|mme|m)\b",
        " ",
        value,
    )
    value = re.sub(r"\b(?:20\d{2}|ri\s*\d+)\b", " ", value)
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def title_similarity(left: str, right: str) -> float:
    left_normalized = normalize(left)
    right_normalized = normalize(right)
    if not left_normalized or not right_normalized:
        return 0.0
    if (
        left_normalized in right_normalized
        or right_normalized in left_normalized
    ):
        return 1.0
    sequence = difflib.SequenceMatcher(
        None, left_normalized, right_normalized
    ).ratio()
    left_tokens = set(left_normalized.split())
    right_tokens = set(right_normalized.split())
    jaccard = len(left_tokens & right_tokens) / max(
        1, len(left_tokens | right_tokens)
    )
    return max(sequence, jaccard)


def document_title(document: dict) -> str:
    preview = str((document.get("text_audit") or {}).get("text_preview") or "")
    title_match = re.search(
        r"(?:intitul[ée]e?\s*)?[«\"]([^»\"]{8,})[»\"]",
        preview,
        re.I,
    )
    return title_match.group(1) if title_match else str(document.get("title") or "")


def title_candidates(document: dict) -> list[str]:
    values = [
        str(document.get("title") or ""),
        document_title(document),
        str((document.get("text_audit") or {}).get("text_preview") or "")[:700],
    ]
    return list(dict.fromkeys(value for value in values if value))


def response_number(reference: str) -> str | None:
    match = re.search(
        r"(20\d{2})\s*/+\s*RI\s*0*(\d+)\s*(bis)?",
        reference,
        re.I,
    )
    if not match:
        return None
    suffix = "bis" if match.group(3) else ""
    return f"{int(match.group(2))}{suffix}/{match.group(1)}"


def best_match(response: dict, objects: list[dict]) -> dict:
    ranked = []
    response_title = document_title(response)
    response_titles = title_candidates(response)
    response_author = str(response.get("author") or "").casefold()
    response_year = str(response.get("listing_year") or "")
    for document in objects:
        score = max(
            title_similarity(response_value, object_value)
            for response_value in response_titles
            for object_value in title_candidates(document)
        )
        author = str(document.get("author") or "").casefold()
        if author and response_author and author == response_author:
            score = min(1.0, score + 0.08)
        if response_year and response_year == str(document.get("listing_year") or ""):
            score = min(1.0, score + 0.02)
        ranked.append((score, document))
    ranked.sort(key=lambda item: item[0], reverse=True)
    score, document = ranked[0] if ranked else (0.0, None)
    second_score = ranked[1][0] if len(ranked) > 1 else 0.0
    if score >= 0.98 and score - second_score >= 0.05:
        confidence = "exact"
    elif score >= 0.76 and score - second_score >= 0.08:
        confidence = "probable"
    else:
        confidence = "manual_review"
    return {
        "political_object_id": (
            document.get("document_id")
            if document and confidence in {"exact", "probable"}
            else None
        ),
        "best_candidate_id": document.get("document_id") if document else None,
        "score": round(score, 4),
        "second_score": round(second_score, 4),
        "matching_method": "normalized_title",
        "matching_confidence": confidence,
    }


def homogeneous_response_record(response: dict, match: dict) -> dict:
    text_audit = response.get("text_audit") or {}
    reference = str(response.get("reference") or "")
    text_hash = str(text_audit.get("text_hash") or "")
    base = {
        "document_id": response["document_id"],
        "commune": "Vevey",
        "document_family": "political_object",
        "category": "interpellation",
        "document_role": "municipal_response",
        "title": document_title(response),
        "source_title": response.get("title"),
        "source_page_url": response.get("source_page"),
        "file_url": response.get("pdf_url"),
        "listing_year": int(response.get("listing_year") or 0),
        "legislature": response.get("legislature") or "2021-2026",
        "document_date": response.get("listing_date"),
        "content_hash": text_hash or response.get("content_hash"),
        "extraction_method": (
            "ocr_required"
            if text_audit.get("needs_ocr")
            else "native_pdf"
        ),
        "processing_status": (
            "validated"
            if not text_audit.get("needs_ocr")
            and match["matching_confidence"] in {"exact", "probable"}
            else "needs_review"
        ),
    }
    return {
        "document_metadata": base,
        "interpellation_metadata": {
            "authors": [],
            "political_status": "response_available",
            "interpellation_date": None,
            "responses": [{
                "response_number": response_number(reference),
                "response_date": response.get("listing_date"),
                "response_type": "municipal_response",
                "municipal_adoption_date": None,
            }],
        },
        "processing": {
            "text_extraction_status": {
                "characters_extracted": int(text_audit.get("text_chars") or 0),
                "text_available": bool(text_audit.get("text_chars")),
                "needs_ocr": bool(text_audit.get("needs_ocr")),
            },
            "header_footer_cleaning": {
                "raw_words": int(text_audit.get("text_words") or 0),
                "clean_words": int(text_audit.get("text_words") or 0),
                "removed_blocks": 0,
            },
            "selected_text": {
                "method": base["extraction_method"],
                "words": int(text_audit.get("text_words") or 0),
            },
        },
        "relationships": {
            **match,
            "response_document_ids": [response["document_id"]],
            "official_reference": reference or None,
        },
        "source_tracking": {
            "source_collection": response.get("source_collection"),
            "source_download_id": response.get("source_download_id"),
            "listing_occurrences": response.get("listing_occurrences", []),
        },
    }


def build_links(
    interpellation_inventory: dict,
    annex_inventory: dict,
) -> tuple[list[dict], list[dict]]:
    objects = [
        document
        for document in interpellation_inventory.get(
            "canonical_documents", []
        )
        if document.get("document_role") == "political_object"
    ]
    responses = annex_inventory.get("canonical_response_documents", [])
    links = []
    records = []
    for response in responses:
        match = best_match(response, objects)
        link = {
            "response_document_id": response["document_id"],
            "response_reference": response.get("reference"),
            "response_title": document_title(response),
            **match,
        }
        links.append(link)
        records.append(homogeneous_response_record(response, match))
    return links, records


def merge_object_records(
    metadata_dir: Path,
    response_records: list[dict],
) -> list[dict]:
    responses_by_object: dict[str, list[dict]] = {}
    for record in response_records:
        object_id = record["relationships"].get("political_object_id")
        if object_id:
            responses_by_object.setdefault(object_id, []).append(record)

    merged = []
    for path in sorted(metadata_dir.glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        document_id = record["document_metadata"]["document_id"]
        linked_responses = responses_by_object.get(document_id, [])
        if linked_responses:
            specific = record["interpellation_metadata"]
            specific["political_status"] = "response_available"
            specific["responses"] = [
                response["interpellation_metadata"]["responses"][0]
                for response in linked_responses
            ]
            relation = record.setdefault("relationships", {})
            relation["response_status"] = "response_available"
            relation["response_document_ids"] = [
                response["document_metadata"]["document_id"]
                for response in linked_responses
            ]
        merged.append(record)
    return merged


def write_html(links: list[dict], path: Path) -> None:
    rows = "".join(
        f"<tr class='{item['matching_confidence']}'><td>{html.escape(str(item['response_reference'] or ''))}</td>"
        f"<td>{html.escape(item['response_title'])}</td>"
        f"<td><code>{html.escape(str(item['political_object_id'] or ''))}</code></td>"
        f"<td>{item['score']:.2f}</td><td>{html.escape(item['matching_confidence'])}</td></tr>"
        for item in links
    )
    page = f"""<!doctype html><html lang="fr"><head><meta charset="utf-8">
<title>Rapprochement interpellations et réponses - Vevey</title><style>
body{{font:15px/1.5 system-ui;margin:24px;color:#172033}}table{{border-collapse:collapse;width:100%}}
th,td{{border:1px solid #d7deea;padding:8px;text-align:left}}th{{background:#eaf1fa}}
.exact{{background:#e5f6e9}}.probable{{background:#fff4c7}}.manual_review{{background:#ffe4e4}}
code{{word-break:break-all}}</style></head><body><h1>Rapprochement interpellations et réponses</h1>
<p>Vert : correspondance exacte. Jaune : probable. Rouge : vérification manuelle requise.</p>
<table><thead><tr><th>Référence</th><th>Réponse</th><th>Interpellation liée</th><th>Score</th><th>Confiance</th></tr></thead>
<tbody>{rows}</tbody></table></body></html>"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(page, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rapproche les interpellations et réponses RI de Vevey"
    )
    parser.add_argument("--interpellations", type=Path, required=True)
    parser.add_argument("--annexes", type=Path, required=True)
    parser.add_argument("--interpellation-metadata-dir", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    interpellations = json.loads(
        args.interpellations.read_text(encoding="utf-8")
    )
    annexes = json.loads(args.annexes.read_text(encoding="utf-8"))
    links, records = build_links(interpellations, annexes)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    metadata_dir = args.output_dir / "metadata"
    metadata_dir.mkdir(parents=True, exist_ok=True)
    for stale_path in metadata_dir.glob("*.json"):
        stale_path.unlink()
    for record in records:
        document_id = record["document_metadata"]["document_id"]
        (metadata_dir / f"{document_id}.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    if args.interpellation_metadata_dir:
        object_dir = args.output_dir / "political_objects"
        object_dir.mkdir(parents=True, exist_ok=True)
        for stale_path in object_dir.glob("*.json"):
            stale_path.unlink()
        for record in merge_object_records(
            args.interpellation_metadata_dir, records
        ):
            document_id = record["document_metadata"]["document_id"]
            (object_dir / f"{document_id}.json").write_text(
                json.dumps(record, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
    (args.output_dir / "links.json").write_text(
        json.dumps(links, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_html(links, args.output_dir / "index.html")
    summary = {
        "responses": len(links),
        "exact": sum(
            item["matching_confidence"] == "exact" for item in links
        ),
        "probable": sum(
            item["matching_confidence"] == "probable" for item in links
        ),
        "manual_review": sum(
            item["matching_confidence"] == "manual_review" for item in links
        ),
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
