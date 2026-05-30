import json
import sqlite3
from collections import Counter
from pathlib import Path

from app.config import CHUNKS_PATH, SQLITE_PATH


def connect(db_path: Path = SQLITE_PATH) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        DROP TABLE IF EXISTS chunks_fts;
        DROP TABLE IF EXISTS chunks;
        DROP TABLE IF EXISTS documents;

        CREATE TABLE documents (
            id INTEGER PRIMARY KEY,
            relative_text_path TEXT NOT NULL UNIQUE,
            title TEXT,
            filename TEXT,
            year TEXT,
            category TEXT,
            pdf_url TEXT,
            source_url TEXT,
            metadata_json TEXT NOT NULL
        );

        CREATE TABLE chunks (
            id TEXT PRIMARY KEY,
            document_id INTEGER NOT NULL,
            chunk_index INTEGER NOT NULL,
            relative_text_path TEXT NOT NULL,
            text TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            FOREIGN KEY (document_id) REFERENCES documents(id)
        );

        CREATE VIRTUAL TABLE chunks_fts USING fts5(
            chunk_id UNINDEXED,
            text,
            title,
            filename,
            category,
            year,
            tokenize='unicode61 remove_diacritics 2'
        );

        CREATE INDEX idx_chunks_document_id ON chunks(document_id);
        CREATE INDEX idx_documents_year_category ON documents(year, category);
        """
    )


def build_sqlite_index(
    chunks_path: Path = CHUNKS_PATH,
    sqlite_path: Path = SQLITE_PATH,
) -> dict:
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    if not chunks_path.exists():
        raise FileNotFoundError(f"Missing chunks file: {chunks_path}")

    connection = connect(sqlite_path)
    try:
        initialize_schema(connection)
        document_ids: dict[str, int] = {}
        document_count = 0
        chunk_count = 0

        with chunks_path.open("r", encoding="utf-8") as file:
            for line in file:
                if not line.strip():
                    continue

                record = json.loads(line)
                metadata = record.get("metadata", {})
                relative_text_path = record.get("relative_text_path", "")
                if relative_text_path not in document_ids:
                    cursor = connection.execute(
                        """
                        INSERT INTO documents (
                            relative_text_path, title, filename, year, category,
                            pdf_url, source_url, metadata_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            relative_text_path,
                            metadata.get("title", ""),
                            metadata.get("filename", ""),
                            str(metadata.get("year", "")),
                            metadata.get("category", ""),
                            metadata.get("pdf_url", ""),
                            metadata.get("url") or metadata.get("source_page", ""),
                            json.dumps(metadata, ensure_ascii=False),
                        ),
                    )
                    document_ids[relative_text_path] = int(cursor.lastrowid)
                    document_count += 1

                connection.execute(
                    """
                    INSERT INTO chunks (
                        id, document_id, chunk_index, relative_text_path, text, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record["id"],
                        document_ids[relative_text_path],
                        record.get("chunk_index", 0),
                        relative_text_path,
                        record.get("text", ""),
                        json.dumps(metadata, ensure_ascii=False),
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO chunks_fts (
                        chunk_id, text, title, filename, category, year
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record["id"],
                        record.get("text", ""),
                        metadata.get("title", ""),
                        metadata.get("filename", ""),
                        metadata.get("category", ""),
                        str(metadata.get("year", "")),
                    ),
                )
                chunk_count += 1

        connection.commit()
        return {
            "sqlite_path": str(sqlite_path),
            "documents_indexed": document_count,
            "chunks_indexed": chunk_count,
        }
    finally:
        connection.close()


def sqlite_ready(sqlite_path: Path = SQLITE_PATH) -> bool:
    if not sqlite_path.exists():
        return False
    try:
        with connect(sqlite_path) as connection:
            row = connection.execute("SELECT COUNT(*) AS count FROM chunks").fetchone()
        return bool(row and row["count"] > 0)
    except sqlite3.Error:
        return False


def search_sqlite(query: str, tokens: list[str], limit: int = 6) -> list[dict]:
    if not sqlite_ready():
        return []

    fts_tokens = [token.replace('"', "") for token in tokens if len(token) >= 3]
    if not fts_tokens:
        return []
    fts_query = " OR ".join(f'"{token}"' for token in fts_tokens[:24])

    with connect() as connection:
        rows = connection.execute(
            """
            SELECT
                chunks.id,
                chunks.text,
                chunks.chunk_index,
                chunks.relative_text_path,
                chunks.metadata_json,
                bm25(chunks_fts) AS rank
            FROM chunks_fts
            JOIN chunks ON chunks.id = chunks_fts.chunk_id
            WHERE chunks_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (fts_query, max(limit * 4, 20)),
        ).fetchall()

    query_counts = Counter(tokens)
    results = []
    for row in rows:
        metadata = json.loads(row["metadata_json"])
        metadata_text = " ".join(
            str(metadata.get(key, ""))
            for key in ["title", "institutional_category", "category", "filename", "session_date"]
        ).lower()
        text = row["text"]
        text_lower = text.lower()
        score = abs(float(row["rank"]))
        for token, count in query_counts.items():
            if token in metadata_text:
                score += count * 8
            if token in text_lower:
                score += count * 0.8
        results.append(
            {
                "id": row["id"],
                "text": text,
                "chunk_index": row["chunk_index"],
                "relative_text_path": row["relative_text_path"],
                "metadata": metadata,
                "score": round(score, 3),
            }
        )
    return sorted(results, key=lambda item: item["score"], reverse=True)[:limit]
