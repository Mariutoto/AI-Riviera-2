"""Detect new/renamed/changed documents on La Tour-de-Peilz's document search.

Detection only — this script does not trigger re-ingestion. It logs what
changed (site_changes table) and keeps a running inventory (site_inventory)
so the next run can diff against it. Reuses the existing per-category
scrapers in scrape-la-tour-de-peilz/ for the listing (tier 1) and adds a
cheap HEAD-request check (tier 2) to catch a same-URL content edit without
downloading the PDF.
"""
from __future__ import annotations

import importlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

if hasattr(sys.stdout, "reconfigure"):
    # Windows consoles default to cp1252, which chokes on titles containing
    # unicode punctuation (e.g. U+2010). GitHub Actions runners are already
    # UTF-8, but make local runs robust too.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = ROOT.parent
SCRAPER_DIR = PROJECT_ROOT / "scrape-la-tour-de-peilz"
sys.path.insert(0, str(SCRAPER_DIR))

SCRAPER_MODULES = [
    "scrape_interpellations_search_json_2021_2026",
    "scrape_postulats_search_json_2021_2026",
    "scrape_motions_search_json_2021_2026",
    "scrape_preavis_search_json_2021_2026",
    "scrape_proces_verbaux_search_json_2021_2026",
    "scrape_rapports_gestion_search_json_2021_2026",
    "scrape_rapport_comptes_search_json_2021_2026",
    "scrape_budgets_search_json_2021_2026",
]


def load_env() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def ensure_tables(cursor) -> None:
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS site_inventory (
            pdf_url TEXT PRIMARY KEY,
            category TEXT NOT NULL,
            title TEXT,
            listing_year TEXT,
            content_length BIGINT,
            last_modified TEXT,
            etag TEXT,
            first_seen TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_seen TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_checked TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS site_changes (
            id BIGSERIAL PRIMARY KEY,
            pdf_url TEXT NOT NULL,
            category TEXT,
            change_type TEXT NOT NULL,
            detected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            details JSONB
        )
        """
    )


def collect_all_listings() -> list[dict]:
    session = requests.Session()
    all_items: list[dict] = []
    for module_name in SCRAPER_MODULES:
        module = importlib.import_module(module_name)
        items, diagnostics = module.collect_items(session)
        print(f"  {module_name}: {len(items)} documents ({diagnostics})")
        all_items.extend(items)
    return all_items


def check_head(url: str, session: requests.Session) -> dict:
    try:
        response = session.head(url, timeout=15, allow_redirects=True)
        response.raise_for_status()
        content_length = response.headers.get("Content-Length")
        return {
            "content_length": int(content_length) if content_length is not None else None,
            "last_modified": response.headers.get("Last-Modified"),
            "etag": response.headers.get("ETag"),
        }
    except Exception as exc:
        print(f"    HEAD check failed for {url}: {exc}")
        return {}


def main() -> None:
    load_env()
    url = os.environ.get("POSTGRES_V2_URL", "")
    if not url:
        raise SystemExit("POSTGRES_V2_URL is missing")

    import psycopg
    from psycopg.rows import dict_row

    print("Fetching current listings from the town's search endpoint...")
    listing_items = collect_all_listings()
    print(f"Total documents currently listed: {len(listing_items)}")

    changes: list[dict] = []
    session = requests.Session()

    with psycopg.connect(url, row_factory=dict_row) as connection, connection.cursor() as cursor:
        ensure_tables(cursor)
        connection.commit()

        cursor.execute("SELECT pdf_url, title, content_length, last_modified, etag FROM site_inventory")
        known = {row["pdf_url"]: row for row in cursor.fetchall()}

        for item in listing_items:
            # interpellations/postulats/motions use "pdf_url"; preavis/proces_verbaux/
            # rapports/budgets use "file_url" — not a naming typo on our side, just
            # inconsistent field names across the existing per-category scrapers.
            pdf_url = item.get("pdf_url") or item.get("file_url")
            if not pdf_url:
                print(f"    Skipping item with no URL field: {item.get('title')}")
                continue
            category = item.get("category", "")
            title = item.get("title", "")
            existing = known.get(pdf_url)
            head = check_head(pdf_url, session)

            if existing is None:
                changes.append({
                    "pdf_url": pdf_url, "category": category, "change_type": "new",
                    "details": {"title": title},
                })
            else:
                if existing["title"] != title:
                    changes.append({
                        "pdf_url": pdf_url, "category": category, "change_type": "renamed",
                        "details": {"old_title": existing["title"], "new_title": title},
                    })
                if head.get("content_length") is not None and (
                    head["content_length"] != existing["content_length"]
                    or head.get("last_modified") != existing["last_modified"]
                    or head.get("etag") != existing["etag"]
                ):
                    changes.append({
                        "pdf_url": pdf_url, "category": category, "change_type": "content_changed",
                        "details": {
                            "old": {
                                "content_length": existing["content_length"],
                                "last_modified": existing["last_modified"],
                                "etag": existing["etag"],
                            },
                            "new": head,
                        },
                    })

            cursor.execute(
                """
                INSERT INTO site_inventory (pdf_url, category, title, listing_year, content_length, last_modified, etag)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (pdf_url) DO UPDATE SET
                    category = EXCLUDED.category,
                    title = EXCLUDED.title,
                    listing_year = EXCLUDED.listing_year,
                    content_length = COALESCE(EXCLUDED.content_length, site_inventory.content_length),
                    last_modified = COALESCE(EXCLUDED.last_modified, site_inventory.last_modified),
                    etag = COALESCE(EXCLUDED.etag, site_inventory.etag),
                    last_seen = now(),
                    last_checked = now()
                """,
                (
                    pdf_url, category, title, item.get("listing_year"),
                    head.get("content_length"), head.get("last_modified"), head.get("etag"),
                ),
            )

        for change in changes:
            cursor.execute(
                "INSERT INTO site_changes (pdf_url, category, change_type, details) VALUES (%s, %s, %s, %s)",
                (change["pdf_url"], change["category"], change["change_type"], json.dumps(change["details"], ensure_ascii=False)),
            )

        connection.commit()

    print(f"\n{len(changes)} change(s) detected:")
    for change in changes:
        print(f"  [{change['change_type']}] {change['category']}: {change['pdf_url']}")

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write(f"## Verification La Tour-de-Peilz — {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n\n")
            handle.write(f"{len(listing_items)} documents listes, {len(changes)} changement(s) detecte(s).\n\n")
            if changes:
                handle.write("| Type | Categorie | URL |\n|---|---|---|\n")
                for change in changes:
                    handle.write(f"| {change['change_type']} | {change['category']} | {change['pdf_url']} |\n")


if __name__ == "__main__":
    main()
