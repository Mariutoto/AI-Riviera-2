from __future__ import annotations

import hashlib
import importlib.util
import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
COMBINED = ROOT / "combined-interpellations-audit"
OUTPUT = COMBINED / "embedding"
INPUT_PATH = OUTPUT / "embedding_inputs.jsonl"
REPORT_PATH = OUTPUT / "validation_report.json"

SHARED_SCRIPT = (
    PROJECT_ROOT
    / "embedding-pilot"
    / "scripts"
    / "generate_embedding_inputs.py"
)
SPEC = importlib.util.spec_from_file_location(
    "shared_embedding_inputs", SHARED_SCRIPT
)
assert SPEC and SPEC.loader
shared = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(shared)


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def build_records(
    chunk_dir: Path,
    metadata_dir: Path,
) -> tuple[list[dict], list[dict]]:
    metadata = {
        path.stem: (read_json(path), path)
        for path in metadata_dir.glob("*.json")
    }
    records = []
    skipped = []
    for chunk_path in sorted(chunk_dir.glob("*.json")):
        chunks = read_json(chunk_path)
        entry = metadata.get(chunk_path.stem)
        if not entry:
            raise ValueError(f"Missing metadata for {chunk_path.stem}")
        source_record, metadata_path = entry
        base = source_record["document_metadata"]
        if not chunks:
            skipped.append({
                "document_id": base["document_id"],
                "title": base["title"],
                "reason": base["processing_status"],
            })
            continue
        if base["processing_status"] != "validated":
            raise ValueError(
                f"Non-validated document has chunks: {base['document_id']}"
            )
        for chunk in chunks:
            text, fields, cleaning = shared.political_input(base, chunk)
            issues = shared.validate(fields, chunk, text)
            content = str(fields.get("content") or "")
            records.append({
                "chunk_id": chunk["chunk_id"],
                "document_id": base["document_id"],
                "document_family": base["document_family"],
                "category": base["category"],
                "document_role": base["document_role"],
                "title": base["title"],
                "article_title": None,
                "component": fields["component"],
                "response_number": chunk.get("response_number"),
                "chunk_index": chunk["chunk_index"],
                "content": content,
                "content_hash": (
                    chunk.get("chunk_hash")
                    or hashlib.sha256(content.encode("utf-8")).hexdigest()
                ),
                "word_count": len(content.split()),
                "embedding_recipe": "political_object",
                "embedding_fields": fields,
                "embedding_input": text,
                "validation_issues": issues,
                "cleaning": cleaning,
                "source_chunk_file": str(
                    chunk_path.relative_to(PROJECT_ROOT)
                ).replace("\\", "/"),
                "source_metadata_file": str(
                    metadata_path.relative_to(PROJECT_ROOT)
                ).replace("\\", "/"),
            })
    return records, skipped


def main() -> None:
    object_records, object_skipped = build_records(
        ROOT / "interpellations-pilot" / "general-audit" / "chunks",
        ROOT / "interpellation-response-links" / "political_objects",
    )
    response_records, response_skipped = build_records(
        COMBINED / "chunks" / "responses",
        COMBINED / "metadata" / "responses",
    )
    records = object_records + response_records
    id_counts = Counter(record["chunk_id"] for record in records)
    hash_counts = Counter(record["content_hash"] for record in records)
    for record in records:
        if id_counts[record["chunk_id"]] > 1:
            record["validation_issues"].append("duplicate_chunk_id")
        if hash_counts[record["content_hash"]] > 1:
            record["validation_issues"].append("duplicate_content_hash")
        record["validation_issues"] = sorted(
            set(record["validation_issues"])
        )
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with INPUT_PATH.open("w", encoding="utf-8", newline="\n") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    report = {
        "status": (
            "ready_for_embeddings"
            if not any(record["validation_issues"] for record in records)
            else "review_required"
        ),
        "recipe": "political_object",
        "model": "mistral-embed",
        "summary": {
            "documents": len({record["document_id"] for record in records}),
            "interpellation_documents": len({
                record["document_id"]
                for record in object_records
            }),
            "response_documents": len({
                record["document_id"]
                for record in response_records
            }),
            "chunks": len(records),
            "valid": sum(
                not record["validation_issues"] for record in records
            ),
            "review": sum(
                bool(record["validation_issues"]) for record in records
            ),
            "duplicate_chunk_ids": sum(
                count - 1 for count in id_counts.values() if count > 1
            ),
            "duplicate_content_hashes": sum(
                count - 1 for count in hash_counts.values() if count > 1
            ),
        },
        "skipped_documents": object_skipped + response_skipped,
        "review_chunks": [
            {
                "chunk_id": record["chunk_id"],
                "issues": record["validation_issues"],
            }
            for record in records
            if record["validation_issues"]
        ],
    }
    REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
