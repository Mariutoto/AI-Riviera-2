from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

SHARED_PATH = Path(__file__).with_name("scrape_interpellations_pilot.py")
SPEC = importlib.util.spec_from_file_location("vevey_interpellation_shared", SHARED_PATH)
assert SPEC and SPEC.loader
shared = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(shared)

from municipal_pipeline.pdf_audit import audit_pdf_documents
from municipal_pipeline.preindex_audit import audit_preindex, write_preindex_html


SOURCE_PAGE = shared.SOURCE_PAGE
START = "2021-07-01"
END = "2026-06-30"
PAGE_SIZE = 20
HEADERS = {"User-Agent": "AI-Riviera Vevey interpellation responses 2021-2026"}


def fetch_page(page: int, session: requests.Session) -> str:
    response = session.get(
        SOURCE_PAGE,
        params={
            "search": "interpellation",
            "since-desktop": START,
            "until-desktop": END,
            "submit-desktop": "Appliquer",
            "page": page,
        },
        headers=HEADERS,
        timeout=60,
    )
    response.raise_for_status()
    return response.text


def is_response(item: dict) -> bool:
    title = shared._ascii(str(item.get("title") or ""))
    reference = str(item.get("reference") or "")
    return bool(
        ("reponse" in title and "interpellation" in title)
        or re.search(r"(?:^|/)RI\s*0*\d+", reference, re.I)
    )


def collect(session: requests.Session) -> tuple[list[dict], dict]:
    first = fetch_page(0, session)
    expected = shared.result_count(first)
    pages = max(1, (expected + PAGE_SIZE - 1) // PAGE_SIZE)
    rows = []
    for page in range(pages):
        html = first if page == 0 else fetch_page(page, session)
        rows.extend(shared.parse_teaser(block) for block in shared.extract_teaser_blocks(html))
    # Le compteur Drupal est calculé avant le filtre de dates : il annonce 218
    # résultats alors que les pages filtrées en exposent 158. Une page vide en
    # fin de pagination est donc le signal de complétude pertinent ici.
    unique = {}
    for item in rows:
        if not is_response(item):
            continue
        item["category"] = "annexe"
        item["document_type"] = "response"
        item["source_collection"] = "vevey-council-interpellation-responses"
        key = (item["source_download_id"] or item["pdf_url"], item["listing_date"], item["title"])
        unique[key] = item
    candidates = sorted(unique.values(), key=lambda row: (row["listing_date"], row["source_download_id"]), reverse=True)
    return candidates, {
        "endpoint_results": expected,
        "pages_fetched": pages,
        "parsed_occurrences": len(rows),
        "response_candidate_occurrences": len(candidates),
        "counter_includes_out_of_scope_rows": expected != len(rows),
        "complete": bool(rows),
    }


def classify(documents: list[dict]) -> None:
    for document in documents:
        title = shared._ascii(str(document.get("title") or ""))
        preview = shared._ascii(str((document.get("text_audit") or {}).get("text_preview") or ""))
        reference = str(document.get("reference") or "")
        if (
            ("reponse" in title and "interpellation" in title)
            or re.search(r"reponse\s+(?:municipale\s+)?a\s+l.?interpellation", preview)
            or re.search(r"reponses?\s+aux?\s+interpellations?", preview)
        ):
            document["document_role"] = "municipal_response"
        else:
            document["document_role"] = "needs_review"


def main() -> None:
    root = PROJECT_ROOT / "audit-vevey" / "interpellation-responses-2021-2026"
    parser = argparse.ArgumentParser(description="Réponses aux interpellations de Vevey 2021-2026")
    parser.add_argument("--output", type=Path, default=root / "inventory.json")
    parser.add_argument("--download-dir", type=Path, default=root / "pdfs")
    parser.add_argument("--html-output", type=Path, default=root / "audit.html")
    args = parser.parse_args()

    session = requests.Session()
    candidates, listing = collect(session)
    documents, downloads = audit_pdf_documents(
        candidates,
        document_id_prefix="vevey_interpellation_response",
        session=session,
        headers=HEADERS,
        download_dir=args.download_dir,
        normalize_title=shared._ascii,
    )
    classify(documents)
    responses = [row for row in documents if row["document_role"] == "municipal_response"]
    preindex = audit_preindex(responses, downloads)
    report = {
        "schema_version": "vevey-interpellation-responses-2021-2026-v1",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": SOURCE_PAGE,
        "scope": {"city": "Vevey", "category": "interpellation", "role": "municipal_response", "legislature": "2021-2026", "from": START, "to": END},
        "listing_diagnostics": listing,
        "download_diagnostics": downloads,
        "response_candidate_occurrences": candidates,
        "canonical_candidate_documents": documents,
        "canonical_response_documents": responses,
        "preindex_audit": preindex,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_preindex_html(preindex, args.html_output, title="Audit des réponses aux interpellations de Vevey 2021–2026")
    print(json.dumps({
        "candidate_occurrences": len(candidates),
        "canonical_responses": len(responses),
        "needs_ocr": sum(row["text_audit"]["needs_ocr"] for row in responses),
        "failed_downloads": len(downloads.get("failed_downloads", [])),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
