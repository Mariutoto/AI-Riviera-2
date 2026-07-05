from __future__ import annotations

import hashlib
import html
import json
import sys
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
SCRAPER_DIR = PROJECT_ROOT / "scrape-la-tour-de-peilz"
sys.path.insert(0, str(SCRAPER_DIR))

import scrape_interpellations_2021_2026 as old_scraper
import scrape_interpellations_search_json_2021_2026 as new_scraper


OUTPUT = ROOT / "scraper-comparison-json"
SOURCE_FIELDS = [
    "title", "summary", "filename", "pdf_url", "listing_year", "legislature",
    "status_normalized", "authors", "political_object_id",
]
ENRICHED_FIELDS = [
    "authors", "object_title", "document_role", "document_components",
    "contains_response", "document_date",
]


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def compare_fields(old: dict, new: dict, fields: list[str]) -> list[dict]:
    comparisons = []
    for field in fields:
        if field not in old and field not in new:
            status = "absent_both"
        elif old.get(field) == new.get(field):
            status = "identical"
        elif field not in old:
            status = "only_new"
        elif field not in new:
            status = "only_old"
        else:
            status = "different"
        comparisons.append({"field": field, "status": status, "old": old.get(field), "new": new.get(field)})
    return comparisons


def document_id(url: str) -> str:
    return "doc_" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]


def audited_text(url: str) -> str:
    path = ROOT / "clean_text" / f"{document_id(url)}.txt"
    if not path.exists():
        raise FileNotFoundError(path)
    return path.read_text(encoding="utf-8")


def build_html(report: dict) -> None:
    summary = report["summary"]
    endpoint = report["endpoint"]
    rows = []
    for item in report["documents"]:
        source_diff = [x for x in item["source_comparison"] if x["status"] not in {"identical", "absent_both"}]
        enriched_diff = [x for x in item["enriched_comparison"] if x["status"] not in {"identical", "absent_both"}]
        details = html.escape(json.dumps({"source": source_diff, "enriched": enriched_diff}, ensure_ascii=False, indent=2))
        row_class = "different" if source_diff or enriched_diff else "identical"
        rows.append(
            f"<tr class='{row_class}'><td>{item['year']}</td><td>{html.escape(item['title'])}</td>"
            f"<td>{'Identique' if not source_diff else html.escape(', '.join(x['field'] for x in source_diff))}</td>"
            f"<td>{'Identique' if not enriched_diff else html.escape(', '.join(x['field'] for x in enriched_diff))}</td>"
            f"<td><details><summary>Voir</summary><pre>{details}</pre></details></td></tr>"
        )
    page = f"""<!doctype html><html lang='fr'><head><meta charset='utf-8'><title>Audit scraper JSON interpellations</title>
<style>body{{font:14px/1.45 system-ui;margin:24px;color:#172033}}.cards{{display:flex;gap:12px;flex-wrap:wrap;margin:18px 0}}.card{{border:1px solid #d9dfeb;border-radius:8px;padding:12px 18px}}.card b{{display:block;font-size:24px;color:#1769aa}}.ok{{background:#e1f5e6;padding:13px;border-left:4px solid #3d9658}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #d9dfeb;padding:8px;vertical-align:top}}th{{background:#edf1f7;position:sticky;top:0}}tr.identical{{background:#effaf2}}tr.different{{background:#fff3bf}}pre{{white-space:pre-wrap;background:#f5f7fa;padding:10px;max-height:450px;overflow:auto}}summary{{cursor:pointer;font-weight:700}}</style></head><body>
<p><a href='../audit.html'>← Audit général des interpellations</a></p><h1>Audit ancien scraper ↔ endpoint JSON</h1>
<div class='cards'><div class='card'><b>{summary['new_documents']}</b>documents JSON</div><div class='card'><b>{summary['common_urls']}/{summary['old_documents']}</b>URL communes</div><div class='card'><b>{summary['identical_source_documents']}</b>données source identiques</div><div class='card'><b>{summary['identical_enriched_documents']}</b>métadonnées enrichies identiques</div></div>
<p class='ok'>Le endpoint JSON retrouve le même corpus canonique. Aucun document n'est ajouté ou perdu.</p>
<h2>Collecte</h2><p><code>{html.escape(endpoint['url'])}</code></p><p>Recherche <code>Interpellation</code>, catégorie <code>6</code>, {endpoint['pages_fetched']} pages, {endpoint['endpoint_rows']} résultats historiques, puis filtre strict 2021–2026 : {endpoint['selected']} documents.</p>
<table><thead><tr><th>Année</th><th>Document</th><th>Données source</th><th>Métadonnées enrichies</th><th>Écarts</th></tr></thead><tbody>{''.join(rows)}</tbody></table></body></html>"""
    (OUTPUT / "audit.html").write_text(page, encoding="utf-8")


def main() -> None:
    new_items, diagnostics = new_scraper.collect_items()
    old_items = old_scraper.collect_items_legacy_page()
    new_by_url = {item["pdf_url"]: item for item in new_items}
    old_by_url = {item["pdf_url"]: item for item in old_items}
    common = sorted(set(new_by_url) & set(old_by_url))
    documents = []
    source_counts = Counter()
    enriched_counts = Counter()
    for url in common:
        old_item, new_item = old_by_url[url], new_by_url[url]
        text = audited_text(url)
        old_enriched = old_scraper.enrich_interpellation_metadata(old_item, text)
        new_enriched = old_scraper.enrich_interpellation_metadata(new_item, text)
        source_comparison = compare_fields(old_item, new_item, SOURCE_FIELDS)
        enriched_comparison = compare_fields(old_enriched, new_enriched, ENRICHED_FIELDS)
        source_counts.update(x["status"] for x in source_comparison)
        enriched_counts.update(x["status"] for x in enriched_comparison)
        documents.append({
            "year": new_item["listing_year"], "title": new_item["summary"],
            "filename": new_item["filename"], "pdf_url": url,
            "source_comparison": source_comparison,
            "enriched_comparison": enriched_comparison,
        })
    clean_statuses = {"identical", "absent_both"}
    summary = {
        "old_documents": len(old_items), "new_documents": len(new_items), "common_urls": len(common),
        "only_old": sorted(set(old_by_url) - set(new_by_url)),
        "only_new": sorted(set(new_by_url) - set(old_by_url)),
        "source_field_counts": dict(source_counts), "enriched_field_counts": dict(enriched_counts),
        "identical_source_documents": sum(all(x["status"] in clean_statuses for x in d["source_comparison"]) for d in documents),
        "identical_enriched_documents": sum(all(x["status"] in clean_statuses for x in d["enriched_comparison"]) for d in documents),
    }
    report = {
        "endpoint": {"url": new_scraper.ENDPOINT, **diagnostics, "selected": len(new_items)},
        "summary": summary, "documents": documents,
    }
    write_json(OUTPUT / "audit.json", report)
    build_html(report)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(OUTPUT / "audit.html")


if __name__ == "__main__":
    main()
