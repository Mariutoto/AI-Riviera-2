from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from app.config import DOCUMENTS_ROOT
from app.metadata_enrichment import enrich_metadata


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, metadata: dict[str, Any]) -> None:
    path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def iter_document_metadata(root: Path):
    for path in sorted(root.rglob("*.json")):
        if path.name.startswith("manifest"):
            continue
        text_path = path.with_suffix(".txt")
        if text_path.exists() and text_path.stat().st_size > 0:
            yield path


def enrich_file(path: Path, dry_run: bool = False) -> bool:
    metadata = read_json(path)
    text_path = path.with_suffix(".txt")
    content = text_path.read_text(encoding="utf-8", errors="ignore")
    enriched = enrich_metadata(metadata, text_path=text_path, content=content)

    if enriched == metadata:
        return False

    if not dry_run:
        write_json(path, enriched)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Persist enriched metadata for every document JSON with extracted text.")
    parser.add_argument("--documents-root", type=Path, default=DOCUMENTS_ROOT)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    scanned = 0
    updated = 0
    failures: list[dict[str, str]] = []

    for path in iter_document_metadata(args.documents_root):
        scanned += 1
        try:
            if enrich_file(path, dry_run=args.dry_run):
                updated += 1
        except Exception as exc:
            failures.append({"path": str(path), "error": str(exc)})

    result = {
        "documents_root": str(args.documents_root),
        "scanned": scanned,
        "updated": updated,
        "dry_run": args.dry_run,
        "failures": failures,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
