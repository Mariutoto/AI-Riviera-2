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

from municipal_pipeline.pdf_audit import audit_pdf_documents
from municipal_pipeline.preindex_audit import audit_preindex, write_preindex_html


INTERPELLATION_SCRIPT = Path(__file__).with_name(
    "scrape_interpellations_pilot.py"
)
SPEC = importlib.util.spec_from_file_location(
    "vevey_interpellation_scraper_shared", INTERPELLATION_SCRIPT
)
assert SPEC and SPEC.loader
shared = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(shared)

SOURCE_PAGE = shared.SOURCE_PAGE
YEARS = {"2025", "2026"}
PAGE_SIZE = 30
HEADERS = {"User-Agent": "AI-Riviera Vevey annexes pilot"}


def parse_page(page_html: str, years: set[str] | None = YEARS) -> list[dict]:
    items = []
    for block in shared.extract_teaser_blocks(page_html):
        teaser_id = re.search(
            r'<p[^>]*\bteaser-id\b[^>]*>(.*?)</p>',
            block,
            flags=re.I | re.S,
        )
        id_parts = [
            shared.clean_html(part)
            for part in re.findall(
                r"<span[^>]*>(.*?)</span>",
                teaser_id.group(1) if teaser_id else "",
                re.I | re.S,
            )
        ]
        if not id_parts or id_parts[0].casefold() != "annexe":
            continue
        item = shared.parse_teaser(block)
        item["category"] = "annexe"
        item["document_type"] = "annexe"
        item["reference"] = id_parts[1] if len(id_parts) > 1 else ""
        item["source_collection"] = "vevey-council-annexes"
        if years is None or item["listing_year"] in years:
            items.append(item)
    return items


def fetch_page(
    page: int,
    session: requests.Session | None = None,
) -> str:
    client = session or requests.Session()
    response = client.get(
        SOURCE_PAGE,
        params={
            "type-desktop": "Annexe",
            "submit-desktop": "Appliquer",
            "page": page,
        },
        headers=HEADERS,
        timeout=30,
    )
    response.raise_for_status()
    return response.text


def collect_items(
    session: requests.Session | None = None,
) -> tuple[list[dict], dict]:
    first_html = fetch_page(0, session)
    expected = shared.result_count(first_html)
    pages = max(1, (expected + PAGE_SIZE - 1) // PAGE_SIZE)
    all_items = parse_page(first_html, years=None)
    for page in range(1, pages):
        all_items.extend(parse_page(fetch_page(page, session), years=None))

    unique = {}
    for item in all_items:
        key = (
            item["source_download_id"] or item["pdf_url"],
            item["listing_date"],
            item["title"],
        )
        unique[key] = item
    scoped = [
        item for item in unique.values()
        if item["listing_year"] in YEARS
    ]
    scoped.sort(
        key=lambda item: (item["listing_date"], item["source_download_id"]),
        reverse=True,
    )
    diagnostics = {
        "endpoint_results": expected,
        "pages_fetched": pages,
        "parsed_endpoint_occurrences": len(all_items),
        "unique_endpoint_occurrences": len(unique),
        "scoped_2025_2026_occurrences": len(scoped),
        "complete": len(unique) == expected,
    }
    if not diagnostics["complete"]:
        raise ValueError(
            f"Collecte Annexes incomplète: {len(unique)} sur {expected}"
        )
    return scoped, diagnostics


def response_candidate_reason(item: dict) -> str | None:
    reference = str(item.get("reference") or "")
    title = shared._ascii(str(item.get("title") or ""))
    if re.search(r"(?:^|/)RI\s*0*\d+\b", reference, re.I):
        return "ri_reference"
    if re.search(r"\breponse\s+(?:municipale\s+)?a\s+l.?interpellation\b", title):
        return "response_title"
    if "interpellation" in title and "reponse" in title:
        return "response_title"
    return None


def select_response_candidates(items: list[dict]) -> list[dict]:
    candidates = []
    for item in items:
        reason = response_candidate_reason(item)
        if reason:
            candidates.append({**item, "candidate_reason": reason})
    return candidates


def download_candidates(
    candidates: list[dict],
    *,
    session: requests.Session | None = None,
    download_dir: Path | None = None,
) -> tuple[list[dict], dict]:
    documents, diagnostics = audit_pdf_documents(
        candidates,
        document_id_prefix="vevey_interpellation_response",
        session=session,
        headers=HEADERS,
        download_dir=download_dir,
        normalize_title=shared._ascii,
    )
    classify_candidate_roles(documents)
    return documents, diagnostics


def classify_candidate_roles(documents: list[dict]) -> None:
    for document in documents:
        preview = shared._ascii(
            str((document.get("text_audit") or {}).get("text_preview") or "")
        )
        if re.search(r"\breponse\s+a\s+l.?interpellation\b", preview):
            document["document_role"] = "municipal_response"
        elif re.search(r"\binterpellation\b", preview):
            document["document_role"] = "interpellation_text_duplicate"
        else:
            document["document_role"] = "needs_review"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inventaire des annexes de Vevey et réponses RI candidates"
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--html-output", type=Path)
    parser.add_argument("--download-dir", type=Path)
    parser.add_argument("--audit-candidates", action="store_true")
    args = parser.parse_args()

    items, listing_diagnostics = collect_items()
    candidates = select_response_candidates(items)
    report = {
        "schema_version": "vevey-annexes-pilot-v1",
        "generated_at": datetime.now().astimezone().isoformat(
            timespec="seconds"
        ),
        "source": SOURCE_PAGE,
        "scope": {
            "city": "Vevey",
            "document_type": "annexe",
            "years": sorted(YEARS),
            "legislature": "2021-2026",
        },
        "listing_diagnostics": listing_diagnostics,
        "listing_occurrences": items,
        "response_candidate_occurrences": candidates,
    }
    if args.audit_candidates:
        documents, download_diagnostics = download_candidates(
            candidates,
            download_dir=args.download_dir,
        )
        responses = [
            document
            for document in documents
            if document["document_role"] == "municipal_response"
        ]
        report["download_diagnostics"] = download_diagnostics
        report["canonical_candidate_documents"] = documents
        report["canonical_response_documents"] = responses
        report["preindex_audit"] = audit_preindex(
            responses, download_diagnostics
        )
        if args.html_output:
            write_preindex_html(
                report["preindex_audit"],
                args.html_output,
                title=(
                    "Audit des réponses d'interpellations candidates - "
                    "Annexes de Vevey 2025-2026"
                ),
            )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    summary = {
        "listing_diagnostics": listing_diagnostics,
        "response_candidates": len(candidates),
        "canonical_response_documents": len(
            report.get("canonical_response_documents", [])
        ),
        "download_diagnostics": report.get("download_diagnostics"),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
