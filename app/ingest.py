import argparse
import json
from pathlib import Path

from app.config import CHUNKS_PATH, DOCUMENTS_ROOT, INDEX_DIR
from app.ingestion_pipeline import ingest_documents
from app.sqlite_index import build_sqlite_index
from app.text_cleaning import clean_french_text


CHUNK_SIZE = 1200
CHUNK_OVERLAP = 180


def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    text = clean_french_text(text)
    if not text:
        return []

    chunks = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            boundary = max(text.rfind("\n", start, end), text.rfind(". ", start, end))
            if boundary > start + size // 2:
                end = boundary + 1

        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)

        if end >= len(text):
            break
        start = max(0, end - overlap)

    return chunks


def metadata_prefix(metadata: dict) -> str:
    parts = [
        f"Commune: {metadata.get('commune', '')}",
        f"Année: {metadata.get('year', '')}",
        f"Catégorie: {metadata.get('category', '')}",
        f"Rubrique: {metadata.get('institutional_category', '')}",
        f"Titre: {metadata.get('title', '')}",
        f"Fichier: {metadata.get('filename', '')}",
    ]
    if metadata.get("session_date"):
        parts.append(f"Date de séance: {metadata['session_date']}")
    if metadata.get("time"):
        parts.append(f"Heure: {metadata['time']}")
    if metadata.get("place"):
        parts.append(f"Lieu: {metadata['place']}")
    return "\n".join(part for part in parts if part.split(": ", 1)[-1])


def load_metadata(text_path: Path) -> dict:
    metadata_path = text_path.with_suffix(".json")
    if metadata_path.exists():
        try:
            return json.loads(metadata_path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError:
            pass

    return {
        "commune": "La Tour-de-Peilz",
        "year": text_path.parts[-3] if len(text_path.parts) >= 3 else "",
        "category": text_path.parts[-2] if len(text_path.parts) >= 2 else "",
        "filename": text_path.with_suffix(".pdf").name,
        "pdf_url": "",
        "source_page": "",
        "text_path": str(text_path),
    }


def iter_text_files(root: Path):
    for path in sorted(root.rglob("*.txt")):
        if path.stat().st_size > 0:
            yield path


def build_legacy_json_index(documents_root: Path = DOCUMENTS_ROOT, chunks_path: Path = CHUNKS_PATH) -> dict:
    INDEX_DIR.mkdir(parents=True, exist_ok=True)

    document_count = 0
    chunk_count = 0

    with chunks_path.open("w", encoding="utf-8") as output:
        for text_path in iter_text_files(documents_root):
            metadata = load_metadata(text_path)
            text = text_path.read_text(encoding="utf-8", errors="ignore")
            chunks = chunk_text(text)
            if not chunks:
                continue

            document_count += 1
            for index, chunk in enumerate(chunks):
                searchable_text = f"{metadata_prefix(metadata)}\n\n{chunk}".strip()
                record = {
                    "id": f"{text_path.relative_to(documents_root).as_posix()}#{index}",
                    "text": searchable_text,
                    "chunk_index": index,
                    "relative_text_path": text_path.relative_to(documents_root).as_posix(),
                    "metadata": metadata,
                }
                output.write(json.dumps(record, ensure_ascii=False) + "\n")
                chunk_count += 1

    stats = {
        "documents_root": str(documents_root),
        "chunks_path": str(chunks_path),
        "documents_indexed": document_count,
        "chunks_indexed": chunk_count,
    }
    try:
        sqlite_stats = build_sqlite_index(chunks_path=chunks_path)
        stats["sqlite_path"] = sqlite_stats["sqlite_path"]
    except Exception as exc:
        stats["sqlite_error"] = str(exc)

    try:
        postgres_stats = ingest_documents(documents_root=documents_root, trigger_name="build_index")
        stats["postgres"] = postgres_stats
    except Exception as exc:
        stats["postgres_error"] = str(exc)

    (INDEX_DIR / "stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    return stats


def build_index(
    documents_root: Path = DOCUMENTS_ROOT,
    chunks_path: Path = CHUNKS_PATH,
    include_legacy_json: bool = False,
) -> dict:
    if include_legacy_json:
        return build_legacy_json_index(documents_root=documents_root, chunks_path=chunks_path)

    stats = ingest_documents(documents_root=documents_root, trigger_name="build_index")
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    (INDEX_DIR / "stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Index AI Riviera text documents.")
    parser.add_argument("--documents-root", type=Path, default=DOCUMENTS_ROOT)
    parser.add_argument(
        "--legacy-json",
        action="store_true",
        help="Also rebuild the old chunks.jsonl and SQLite indexes before ingesting SQL.",
    )
    args = parser.parse_args()

    stats = build_index(args.documents_root, include_legacy_json=args.legacy_json)
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
