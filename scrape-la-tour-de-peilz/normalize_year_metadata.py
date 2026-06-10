from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.config import DOCUMENTS_ROOT, PROJECT_ROOT as APP_PROJECT_ROOT
from app.year_metadata import category, is_political_document, normalize_year_metadata, object_id_year


DEFAULT_REPORT_PATH = APP_PROJECT_ROOT / "metadata-audit" / "year-normalization-audit.json"


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return None


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def year(value: Any) -> str:
    text = str(value or "")
    return text[:4] if len(text) >= 4 and text[:4].isdigit() else ""


def relative(path: Path) -> str:
    try:
        return path.relative_to(APP_PROJECT_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def audit_record(path: Path, before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any] | None:
    issues = []
    if str(before.get("year") or "") and str(before.get("listing_year") or "") and str(before.get("year")) != str(before.get("listing_year")):
        issues.append("year_differs_from_listing_year")
    if before.get("document_date") and str(before.get("year") or "") and str(before.get("year")) != year(before.get("document_date")):
        issues.append("year_differs_from_document_date")
    oid_year = object_id_year(before)
    if oid_year and str(before.get("year") or "") and oid_year != str(before.get("year")):
        issues.append("object_id_year_differs_from_year")
    if after.get("object_year") and after.get("pdf_storage_year") and after["object_year"] != after["pdf_storage_year"]:
        issues.append("object_year_differs_from_pdf_storage_year")
    if after.get("response_date") and after.get("object_year") and year(after.get("response_date")) and year(after["response_date"]) != after["object_year"]:
        issues.append("response_year_differs_from_object_year")

    changed_keys = sorted(
        key
        for key in set(before) | set(after)
        if before.get(key) != after.get(key)
        and key
        in {
            "object_year",
            "pdf_storage_year",
            "document_year",
            "publication_date",
            "publication_year",
            "fiscal_year",
            "reporting_year",
            "session_year",
            "linked_political_object_ids",
            "linked_object_years",
            "year_mismatch_reason",
            "response_date",
            "decision_date",
        }
    )
    if not issues and not changed_keys:
        return None
    return {
        "path": relative(path),
        "category": category(after),
        "political": is_political_document(after),
        "issues": issues,
        "changed_keys": changed_keys,
        "before": {
            "year": before.get("year"),
            "listing_year": before.get("listing_year"),
            "document_date": before.get("document_date"),
        },
        "after": {
            "year": after.get("year"),
            "listing_year": after.get("listing_year"),
            "object_year": after.get("object_year"),
            "pdf_storage_year": after.get("pdf_storage_year"),
            "document_year": after.get("document_year"),
            "session_year": after.get("session_year"),
            "fiscal_year": after.get("fiscal_year"),
            "reporting_year": after.get("reporting_year"),
            "publication_year": after.get("publication_year"),
            "year_mismatch_reason": after.get("year_mismatch_reason"),
        },
    }


def normalize_documents(documents_root: Path, report_path: Path, dry_run: bool = False) -> dict[str, Any]:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    records = []
    stats = Counter()
    by_category = defaultdict(int)

    for path in sorted(documents_root.rglob("*.json")):
        if path.name.startswith("manifest"):
            continue
        before = read_json(path)
        if not before:
            stats["unreadable"] += 1
            continue
        after = normalize_year_metadata(before, path)
        record = audit_record(path, before, after)
        if record:
            records.append(record)
        if before != after:
            stats["changed"] += 1
            by_category[category(after) or "unknown"] += 1
            if not dry_run:
                write_json(path, after)
        stats["scanned"] += 1

    summary = {
        "dry_run": dry_run,
        "documents_root": str(documents_root),
        "stats": dict(stats),
        "changed_by_category": dict(sorted(by_category.items())),
        "issue_counts": dict(Counter(issue for record in records for issue in record["issues"])),
        "records": records,
    }
    report_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Normalize year/date metadata without moving local document files.")
    parser.add_argument("--documents-root", type=Path, default=DOCUMENTS_ROOT)
    parser.add_argument("--report-path", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    summary = normalize_documents(args.documents_root, args.report_path, dry_run=args.dry_run)
    print(json.dumps({key: summary[key] for key in ["dry_run", "stats", "changed_by_category", "issue_counts"]}, ensure_ascii=False, indent=2))
    print(f"Report: {args.report_path}")


if __name__ == "__main__":
    main()
