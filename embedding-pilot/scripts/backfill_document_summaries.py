"""Generate and store one reusable summary per document.

This is an enrichment pass only: it never changes chunks or embeddings. It is
safe to stop and resume because documents with an existing summary are skipped
unless --force is supplied.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
import time
from pathlib import Path

import requests


ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = ROOT.parent
DEFAULT_MODEL = "mistral-small-latest"
DEFAULT_MAX_CHARS = 12_000

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SUMMARY_PROMPT = """Tu résumes un document communal pour la liste des sources d'un assistant documentaire.
Écris une seule phrase concise en français qui indique le sujet et l'objectif principal du document.
Base-toi uniquement sur le titre, les métadonnées et les extraits fournis.
N'invente aucun nom, chiffre, date, statut ou résultat.
Retourne seulement la phrase, sans préfixe ni guillemets."""


def load_local_config() -> None:
    env_path = ROOT / ".env"
    if env_path.exists():
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

    secrets_path = PROJECT_ROOT / ".streamlit" / "secrets.toml"
    if secrets_path.exists():
        import tomllib

        secrets = tomllib.loads(secrets_path.read_text(encoding="utf-8"))
        for key, value in secrets.items():
            if isinstance(value, (str, int, float, bool)):
                os.environ.setdefault(str(key), str(value))


def ensure_columns(cursor) -> None:
    cursor.execute(
        """
        ALTER TABLE documents
            ADD COLUMN IF NOT EXISTS summary text,
            ADD COLUMN IF NOT EXISTS summary_generated_at timestamptz,
            ADD COLUMN IF NOT EXISTS summary_model text,
            ADD COLUMN IF NOT EXISTS summary_content_hash text
        """
    )


def select_documents(cursor, *, limit: int | None, force: bool, document_id: str | None) -> list[dict]:
    clauses = []
    params: list[object] = []
    if not force:
        clauses.append("nullif(btrim(d.summary), '') IS NULL")
    if document_id:
        clauses.append("d.document_id = %s")
        params.append(document_id)
    where_sql = "WHERE " + " AND ".join(clauses) if clauses else ""
    limit_sql = "LIMIT %s" if limit is not None else ""
    if limit is not None:
        params.append(limit)

    cursor.execute(
        f"""
        SELECT d.document_id, d.title, d.category, d.document_role, d.metadata
        FROM documents d
        {where_sql}
        ORDER BY d.document_id
        {limit_sql}
        """,
        params,
    )
    return cursor.fetchall()


def representative_chunks(cursor, document_id: str, max_chars: int) -> tuple[str, str]:
    cursor.execute(
        """
        SELECT chunk_index, component, content
        FROM chunks
        WHERE document_id = %s
        ORDER BY chunk_index
        """,
        (document_id,),
    )
    rows = cursor.fetchall()
    if not rows:
        return "", hashlib.sha256(b"").hexdigest()

    priority_terms = ("response", "decision", "conclusion", "answer", "reponse")
    prioritized = [
        row for row in rows
        if any(term in str(row.get("component") or "").lower() for term in priority_terms)
    ]
    ordered = []
    seen = set()
    for row in [*rows[:2], *prioritized, *rows[-2:]]:
        key = row["chunk_index"]
        if key not in seen:
            ordered.append(row)
            seen.add(key)

    parts = []
    remaining = max_chars
    for row in ordered:
        text = " ".join(str(row["content"] or "").split())
        if not text or remaining <= 0:
            continue
        excerpt = text[:remaining]
        parts.append(f"[{row.get('component') or 'passage'}]\n{excerpt}")
        remaining -= len(excerpt)

    context = "\n\n".join(parts)
    content_hash = hashlib.sha256(context.encode("utf-8")).hexdigest()
    return context, content_hash


def generate_summary(
    session: requests.Session,
    *,
    api_key: str,
    model: str,
    document: dict,
    context: str,
    retries: int,
) -> str:
    metadata = document.get("metadata") or {}
    year = metadata.get("listing_year") or metadata.get("year") or ""
    user_content = (
        f"Titre: {document['title']}\n"
        f"Catégorie: {document.get('category') or ''}\n"
        f"Rôle: {document.get('document_role') or ''}\n"
        f"Année: {year}\n\n"
        f"Extraits:\n{context}"
    )
    response = None
    for attempt in range(retries + 1):
        response = session.post(
            "https://api.mistral.ai/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": SUMMARY_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                "max_tokens": 140,
                "temperature": 0,
            },
            timeout=45,
        )
        if response.status_code not in {429, 500, 502, 503, 504} or attempt >= retries:
            response.raise_for_status()
            break
        retry_after = response.headers.get("Retry-After")
        try:
            delay = float(retry_after) if retry_after else min(2 ** attempt, 30)
        except ValueError:
            delay = min(2 ** attempt, 30)
        print(f"  Mistral returned {response.status_code}; retrying in {delay:g}s", flush=True)
        time.sleep(delay)

    assert response is not None
    summary = " ".join(response.json()["choices"][0]["message"]["content"].strip().split())
    if not summary:
        raise ValueError("Mistral returned an empty summary")
    return summary[:1_000]


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill reusable document summaries without re-embedding")
    parser.add_argument("--limit", type=int, help="Maximum number of documents to process")
    parser.add_argument("--document-id", help="Process one document only")
    parser.add_argument("--model", default=None)
    parser.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS)
    parser.add_argument("--sleep", type=float, default=0.2, help="Delay between API calls")
    parser.add_argument("--retries", type=int, default=5, help="Retries for rate limits and temporary API errors")
    parser.add_argument("--dry-run", action="store_true", help="Generate and print without updating PostgreSQL")
    parser.add_argument("--force", action="store_true", help="Regenerate summaries that already exist")
    args = parser.parse_args()

    load_local_config()
    database_url = os.environ.get("POSTGRES_V2_URL", "")
    api_key = os.environ.get("MISTRAL_API_KEY", "")
    model = args.model or os.environ.get("MISTRAL_SUMMARY_MODEL") or os.environ.get("MISTRAL_MODEL") or DEFAULT_MODEL
    if not database_url:
        raise SystemExit("POSTGRES_V2_URL is missing")
    if not api_key:
        raise SystemExit("MISTRAL_API_KEY is missing")
    if args.limit is not None and args.limit < 1:
        raise SystemExit("--limit must be positive")
    if args.max_chars < 1_000:
        raise SystemExit("--max-chars must be at least 1000")
    if args.retries < 0:
        raise SystemExit("--retries cannot be negative")

    import psycopg
    from psycopg.rows import dict_row

    completed = 0
    failed = 0
    with (
        psycopg.connect(database_url, row_factory=dict_row) as connection,
        connection.cursor() as cursor,
        requests.Session() as session,
    ):
        ensure_columns(cursor)
        connection.commit()
        documents = select_documents(
            cursor,
            limit=args.limit,
            force=args.force,
            document_id=args.document_id,
        )
        print(f"{len(documents)} document(s) selected")

        for index, document in enumerate(documents, start=1):
            try:
                context, content_hash = representative_chunks(cursor, document["document_id"], args.max_chars)
                if not context:
                    print(f"[{index}/{len(documents)}] skipped (no chunks): {document['title']}")
                    continue
                summary = generate_summary(
                    session,
                    api_key=api_key,
                    model=model,
                    document=document,
                    context=context,
                    retries=args.retries,
                )
                print(f"[{index}/{len(documents)}] {document['title']}\n  {summary}")
                if not args.dry_run:
                    cursor.execute(
                        """
                        UPDATE documents
                        SET summary = %s,
                            summary_generated_at = now(),
                            summary_model = %s,
                            summary_content_hash = %s
                        WHERE document_id = %s
                        """,
                        (summary, model, content_hash, document["document_id"]),
                    )
                    connection.commit()
                completed += 1
            except Exception as exc:
                connection.rollback()
                failed += 1
                print(f"[{index}/{len(documents)}] FAILED {document['document_id']}: {exc}")
                if isinstance(exc, requests.HTTPError) and exc.response is not None and exc.response.status_code in {401, 403}:
                    print("Stopping: the Mistral API key is not authorized.")
                    break
            if args.sleep:
                time.sleep(args.sleep)

    print(f"Completed: {completed}; failed: {failed}; dry_run: {args.dry_run}")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
