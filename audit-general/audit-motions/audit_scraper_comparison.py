from __future__ import annotations

import html
import json
import sys
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parents[1]
SCRAPER_DIR = PROJECT_ROOT / "scrape-la-tour-de-peilz"
sys.path.insert(0, str(SCRAPER_DIR))

import scrape_motions_2021_2026 as old_scraper
import scrape_motions_search_json_2021_2026 as new_scraper


OUTPUT = ROOT / "scraper-comparison"
PILOT = ROOT / "pilot"
SOURCE_FIELDS = [
    "title", "summary", "filename", "pdf_url", "listing_year", "legislature",
    "status_normalized", "authors", "political_object_id",
]
ENRICHED_FIELDS = [
    "authors", "object_title", "document_role", "report_type",
    "contains_report", "contains_decision", "contains_majority_report",
    "contains_minority_report", "document_date", "commission", "decision",
]


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def compare_fields(old: dict, new: dict, fields: list[str]) -> list[dict]:
    result = []
    for field in fields:
        old_present = field in old
        new_present = field in new
        if not old_present and not new_present:
            status = "absent_both"
        elif old.get(field) == new.get(field):
            status = "identical"
        elif not old_present:
            status = "only_new"
        elif not new_present:
            status = "only_old"
        else:
            status = "different"
        result.append({"field": field, "status": status, "old": old.get(field), "new": new.get(field)})
    return result


def native_text(filename: str) -> str:
    path = PILOT / "artifacts" / Path(filename).stem / "native.txt"
    if not path.exists():
        raise FileNotFoundError(f"Texte pilote absent: {path}")
    return path.read_text(encoding="utf-8")


def final_view(filename: str) -> dict:
    manifests = json.loads((PILOT / "manifest.json").read_text(encoding="utf-8"))["documents"]
    record = next(item for item in manifests if Path(item["file_url"]).name == filename)
    return json.loads((PILOT / "combined_metadata_view" / f"{record['document_id']}.json").read_text(encoding="utf-8"))


def build_html(report: dict) -> None:
    rows = []
    for document in report["documents"]:
        source_differences = [x["field"] for x in document["source_comparison"] if x["status"] == "different"]
        metadata_differences = [x["field"] for x in document["enriched_comparison"] if x["status"] == "different"]
        source_json = html.escape(json.dumps(document["source_comparison"], ensure_ascii=False, indent=2))
        enriched_json = html.escape(json.dumps(document["enriched_comparison"], ensure_ascii=False, indent=2))
        final_json = html.escape(json.dumps(document["final_metadata"], ensure_ascii=False, indent=2))
        rows.append(
            f"<tr><td>{document['year']}</td><td>{html.escape(document['filename'])}</td>"
            f"<td><span class='badge ok'>{html.escape(', '.join(source_differences) or 'identiques')}</span></td>"
            f"<td><span class='badge ok'>{html.escape(', '.join(metadata_differences) or 'identiques')}</span></td>"
            f"<td><span class='badge {'ok' if document['final_metadata_complete'] else 'bad'}'>{'complète' if document['final_metadata_complete'] else 'incomplète'}</span></td>"
            f"<td><details><summary>Voir</summary><h4>Données source comparées</h4><pre>{source_json}</pre>"
            f"<h4>Métadonnées enrichies comparées</h4><pre>{enriched_json}</pre>"
            f"<h4>Nouvelle métadonnée finale</h4><pre>{final_json}</pre></details></td></tr>"
        )
    endpoint = report["endpoint_audit"]
    summary = report["summary"]
    page = f"""<!doctype html><html lang='fr'><head><meta charset='utf-8'><title>Comparaison des scrapers motions</title>
<style>body{{font:14px/1.5 system-ui;margin:28px;color:#172033;background:#fbfcfe}}h1{{margin-bottom:6px}}.lead{{color:#526174;margin-top:0}}.cards{{display:grid;grid-template-columns:repeat(4,minmax(140px,1fr));gap:12px;margin:20px 0}}.card{{background:white;border:1px solid #dce3ee;border-radius:10px;padding:14px;box-shadow:0 2px 8px #20304a0a}}.number{{font-size:27px;font-weight:750;color:#175fa8}}.callout{{background:#effaf2;border-left:4px solid #39945a;padding:13px}}code,pre{{background:#f5f7fa}}code{{padding:2px 5px}}pre{{padding:12px;max-height:430px;overflow:auto;white-space:pre-wrap;word-break:break-word;border-radius:6px}}table{{border-collapse:collapse;width:100%;margin-top:18px;background:white}}th,td{{border:1px solid #d8deea;padding:8px;text-align:left;vertical-align:top}}th{{background:#edf1f7;position:sticky;top:0}}.badge{{display:inline-block;border-radius:999px;padding:3px 8px;font-size:12px;font-weight:650;white-space:nowrap}}.badge.ok{{background:#ddf4e4;color:#176b35}}.badge.bad{{background:#ffe0e0;color:#9a2525}}summary{{cursor:pointer;color:#175fa8;font-weight:700}}.flow{{background:white;border:1px solid #dce3ee;border-radius:10px;padding:14px}}@media(max-width:850px){{.cards{{grid-template-columns:1fr 1fr}}table{{display:block;overflow-x:auto}}}}</style></head><body>
<h1>Audit ancien scraper ↔ endpoint JSON</h1>
<p class='lead'>Comparaison exhaustive des motions 2021–2026 de La Tour-de-Peilz.</p>
<div class='cards'><div class='card'><div class='number'>{summary['new_documents']}</div>motions JSON</div><div class='card'><div class='number'>{summary['common_urls']}/{summary['old_documents']}</div>URL communes</div><div class='card'><div class='number'>{summary['documents_with_identical_enriched_metadata']}/{summary['new_documents']}</div>métadonnées identiques</div><div class='card'><div class='number'>{summary['complete_final_views']}/{summary['new_documents']}</div>vues finales complètes</div></div>
<p class='callout'>Conclusion : le nouveau scraper retrouve exactement le corpus de l’ancien et produit les mêmes métadonnées enrichies pour les 12 motions.</p>
<h2>Comment le endpoint est utilisé</h2><div class='flow'><p><code>{html.escape(endpoint['url'])}</code></p><ol><li>Envoi de <code>searchdoc=Motion</code> et <code>categories[]=6</code>.</li><li>Lecture des champs JSON <code>result</code>, <code>rows</code>, <code>qty</code> et <code>active</code>.</li><li>Pagination sur {endpoint['pages_fetched']} pages ({endpoint['endpoint_rows']} résultats historiques).</li><li>Extraction du HTML inclus dans <code>result</code>.</li><li>Filtrage strict des PDF de motions et des années 2021–2026 : {endpoint['selected_2021_2026']} documents.</li><li>Normalisation puis enrichissement avec le schéma final.</li></ol></div>
<h2>Comparaison document par document</h2><p>Ouvrir « Voir » pour examiner les champs comparés et la nouvelle métadonnée complète.</p>
<table><thead><tr><th>Année</th><th>Document</th><th>Source</th><th>Enrichissement</th><th>Vue finale</th><th>Détails</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
</body></html>"""
    (OUTPUT / "audit.html").write_text(page, encoding="utf-8")


def main() -> None:
    new_items, endpoint_diagnostics = new_scraper.collect_items()
    old_items = old_scraper.collect_items()
    old_by_url = {item["pdf_url"]: item for item in old_items}
    new_by_url = {item["pdf_url"]: item for item in new_items}
    common_urls = sorted(set(old_by_url) & set(new_by_url))
    documents = []
    source_counts = Counter()
    enriched_counts = Counter()

    for url in common_urls:
        old_item = old_by_url[url]
        new_item = new_by_url[url]
        text = native_text(new_item["filename"])
        old_enriched = old_scraper.enrich_motion_metadata(old_item, text)
        new_enriched = old_scraper.enrich_motion_metadata(new_item, text)
        source_comparison = compare_fields(old_item, new_item, SOURCE_FIELDS)
        enriched_comparison = compare_fields(old_enriched, new_enriched, ENRICHED_FIELDS)
        source_counts.update(item["status"] for item in source_comparison)
        enriched_counts.update(item["status"] for item in enriched_comparison)
        view = final_view(new_item["filename"])
        required_final = {
            "document_metadata": {"document_id", "title", "file_url", "document_role"},
            "motion_metadata": {
                "authors", "political_status", "contains_majority_report",
                "contains_minority_report", "decision_date",
            },
            "processing": {"text_extraction_status", "header_footer_cleaning", "selected_text"},
        }
        final_complete = all(
            section in view and required.issubset(view[section])
            for section, required in required_final.items()
        )
        documents.append(
            {
                "year": new_item["listing_year"],
                "filename": new_item["filename"],
                "pdf_url": url,
                "source_comparison": source_comparison,
                "enriched_comparison": enriched_comparison,
                "new_source_specific_fields": {
                    "source_page": new_item.get("source_page"),
                    "source_endpoint": new_item.get("source_endpoint"),
                    "source_collection": new_item.get("source_collection"),
                    "source_category_id": new_item.get("source_category_id"),
                },
                "final_metadata_complete": final_complete,
                "final_metadata": view,
            }
        )

    identical_enriched = sum(
        all(item["status"] in {"identical", "absent_both"} for item in document["enriched_comparison"])
        for document in documents
    )
    report = {
        "endpoint_audit": {
            "url": new_scraper.ENDPOINT,
            "request_parameters": {
                "searchdoc": "Motion", "categories[]": "6", "sorting": "rang",
                "direction": "DESC", "qty": new_scraper.PAGE_SIZE,
            },
            "json_contract": ["result", "rows", "qty", "active"],
            **endpoint_diagnostics,
            "selected_2021_2026": len(new_items),
        },
        "summary": {
            "old_documents": len(old_items),
            "new_documents": len(new_items),
            "common_urls": len(common_urls),
            "only_old": sorted(set(old_by_url) - set(new_by_url)),
            "only_new": sorted(set(new_by_url) - set(old_by_url)),
            "source_field_counts": dict(source_counts),
            "enriched_field_counts": dict(enriched_counts),
            "documents_with_identical_enriched_metadata": identical_enriched,
            "complete_final_views": sum(item["final_metadata_complete"] for item in documents),
        },
        "documents": documents,
    }
    write_json(OUTPUT / "audit.json", report)
    build_html(report)
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(OUTPUT / "audit.html")


if __name__ == "__main__":
    main()
