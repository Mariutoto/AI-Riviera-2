from __future__ import annotations

import html
import json
import sys
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scrape-la-tour-de-peilz"))

import scrape_postulats_2021_2026 as old_scraper
import scrape_postulats_search_json_2021_2026 as new_scraper


OUTPUT = ROOT / "scraper-comparison"
SOURCE_FIELDS = ["title", "summary", "filename", "pdf_url", "listing_year", "legislature", "status_normalized", "authors", "political_object_id"]
ENRICHED_FIELDS = ["authors", "object_title", "document_role", "report_type", "contains_report", "contains_decision", "contains_majority_report", "contains_minority_report", "document_date", "commission", "decision"]


def compare(old: dict, new: dict, fields: list[str]) -> list[dict]:
    result = []
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
        result.append({"field": field, "status": status, "old": old.get(field), "new": new.get(field)})
    return result


def main() -> None:
    new_items, endpoint = new_scraper.collect_items()
    old_items = old_scraper.collect_items_legacy_page()
    new_by_url, old_by_url = {x["pdf_url"]: x for x in new_items}, {x["pdf_url"]: x for x in old_items}
    common = sorted(set(new_by_url) & set(old_by_url))
    documents, source_counts, enriched_counts = [], Counter(), Counter()
    for url in common:
        new, old = new_by_url[url], old_by_url[url]
        text_path = ROOT / "pilot" / "artifacts" / Path(new["filename"]).stem / "native.txt"
        text = text_path.read_text(encoding="utf-8")
        source = compare(old, new, SOURCE_FIELDS)
        enriched = compare(old_scraper.enrich_postulat_metadata(old, text), old_scraper.enrich_postulat_metadata(new, text), ENRICHED_FIELDS)
        source_counts.update(x["status"] for x in source)
        enriched_counts.update(x["status"] for x in enriched)
        documents.append({"year": new["listing_year"], "title": new["summary"], "filename": new["filename"], "source": source, "enriched": enriched})
    clean = {"identical", "absent_both"}
    summary = {
        "old_documents": len(old_items), "new_documents": len(new_items), "common_urls": len(common),
        "only_old": sorted(set(old_by_url)-set(new_by_url)), "only_new": sorted(set(new_by_url)-set(old_by_url)),
        "source_field_counts": dict(source_counts), "enriched_field_counts": dict(enriched_counts),
        "identical_source_documents": sum(all(x["status"] in clean for x in d["source"]) for d in documents),
        "identical_enriched_documents": sum(all(x["status"] in clean for x in d["enriched"]) for d in documents),
    }
    report = {"endpoint": endpoint, "summary": summary, "documents": documents}
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    rows = []
    for item in documents:
        source_diff = [x for x in item["source"] if x["status"] not in clean]
        enriched_diff = [x for x in item["enriched"] if x["status"] not in clean]
        details = html.escape(json.dumps({"source": source_diff, "enriched": enriched_diff}, ensure_ascii=False, indent=2))
        rows.append(f"<tr class='{'warn' if source_diff or enriched_diff else 'ok'}'><td>{item['year']}</td><td>{html.escape(item['title'])}</td><td>{'Identique' if not source_diff else ', '.join(x['field'] for x in source_diff)}</td><td>{'Identique' if not enriched_diff else ', '.join(x['field'] for x in enriched_diff)}</td><td><details><summary>Voir</summary><pre>{details}</pre></details></td></tr>")
    page = f"""<!doctype html><html lang='fr'><head><meta charset='utf-8'><title>Comparaison scrapers postulats</title><style>body{{font:14px/1.45 system-ui;margin:24px;color:#172033}}.cards{{display:flex;gap:12px}}.card{{border:1px solid #d9dfeb;padding:12px 18px;border-radius:8px}}.card b{{display:block;font-size:24px;color:#1769aa}}table{{border-collapse:collapse;width:100%;margin-top:18px}}th,td{{border:1px solid #d9dfeb;padding:8px}}th{{background:#edf1f7}}tr.ok{{background:#effaf2}}tr.warn{{background:#fff3bf}}pre{{white-space:pre-wrap;background:#f5f7fa;padding:10px}}</style></head><body><p><a href='../pilot/review.html'>← Pilote métadonnées</a></p><h1>Audit ancien scraper ↔ endpoint JSON — postulats</h1><div class='cards'><div class='card'><b>{summary['common_urls']}/{summary['old_documents']}</b>URL communes</div><div class='card'><b>{summary['identical_source_documents']}</b>sources identiques</div><div class='card'><b>{summary['identical_enriched_documents']}</b>enrichissements identiques</div></div><p>{endpoint['pages_fetched']} pages JSON, {endpoint['endpoint_rows']} résultats historiques, {endpoint['postulats_2021_2026']} postulats retenus.</p><table><thead><tr><th>Année</th><th>Document</th><th>Source</th><th>Enrichissement</th><th>Écarts</th></tr></thead><tbody>{''.join(rows)}</tbody></table></body></html>"""
    (OUTPUT / "audit.html").write_text(page, encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(OUTPUT / "audit.html")


if __name__ == "__main__":
    main()
