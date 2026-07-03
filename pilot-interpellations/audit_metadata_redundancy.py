from __future__ import annotations

import html
import json
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parent

FIELD_MAPPINGS = {
    "commune": "commune",
    "category": "category",
    "document_role": "document_role",
    "title": "title",
    "source_title": "site_listing_title",
    "source_page_url": "source_page",
    "file_url": "pdf_url",
    "listing_year": "listing_year",
    "document_date": "document_date",
}

KEEP_AS_INTERPELLATION_METADATA = {
    "authors",
    "status_normalized",
}

MOVE_TO_RELATIONSHIPS = {
    "political_object_id",
    "related_political_object_id",
    "related_canonical_interpellation",
    "linked_to_session",
    "scheduled_in_sessions",
    "agenda_linked_document",
}

MOVE_TO_PROCESSING = {
    "metadata_version",
    "text_extraction_status",
    "pdf_path",
    "text_path",
    "pdf_storage_year",
    "document_year",
    "object_year",
    "year_mismatch_reason",
}

SOURCE_ONLY_FIELDS = {
    "site_status_raw",
    "site_subject",
    "source_collection",
    "canonical_object",
}


def comparable(value):
    if isinstance(value, str):
        return value.strip().lower().rstrip("s")
    return value


def main() -> None:
    base_by_id = {
        data["document_id"]: data
        for path in (ROOT / "document_metadata").glob("*.json")
        for data in [json.loads(path.read_text(encoding="utf-8"))]
    }
    additional_by_id = {
        path.stem: json.loads(path.read_text(encoding="utf-8"))
        for path in (ROOT / "scraper_metadata").glob("*.json")
    }
    results = []
    aggregate = Counter()
    field_presence = Counter()

    for document_id, base in sorted(base_by_id.items()):
        additional = additional_by_id[document_id]
        comparisons = []
        mapped_additional_fields = set(FIELD_MAPPINGS.values())
        for base_field, additional_field in FIELD_MAPPINGS.items():
            base_value = base.get(base_field)
            additional_value = additional.get(additional_field)
            if additional_field not in additional:
                status = "missing_in_additional"
            elif comparable(base_value) == comparable(additional_value):
                status = "duplicate"
            else:
                status = "conflict_or_different_semantics"
            aggregate[status] += 1
            comparisons.append({
                "base_field": base_field,
                "additional_field": additional_field,
                "base_value": base_value,
                "additional_value": additional_value,
                "status": status,
            })

        for field in additional:
            field_presence[field] += 1
        remaining = sorted(set(additional) - mapped_additional_fields)
        classification = {
            "keep_interpellation_metadata": sorted(set(remaining) & KEEP_AS_INTERPELLATION_METADATA),
            "move_relationships": sorted(set(remaining) & MOVE_TO_RELATIONSHIPS),
            "move_processing": sorted(set(remaining) & MOVE_TO_PROCESSING),
            "keep_source_snapshot": sorted(set(remaining) & SOURCE_ONLY_FIELDS),
            "review_or_remove": sorted(
                set(remaining)
                - KEEP_AS_INTERPELLATION_METADATA
                - MOVE_TO_RELATIONSHIPS
                - MOVE_TO_PROCESSING
                - SOURCE_ONLY_FIELDS
            ),
        }
        results.append({
            "document_id": document_id,
            "title": base.get("title"),
            "comparisons": comparisons,
            "classification": classification,
        })

    proposed_schema = {
        "authors": [{"name": "...", "party": "..."}],
        "political_status": "response_available",
    }
    report = {
        "documents": len(results),
        "comparison_counts": dict(aggregate),
        "additional_field_presence": dict(sorted(field_presence.items())),
        "proposed_interpellation_metadata": proposed_schema,
        "documents_audit": results,
    }
    (ROOT / "metadata_redundancy_audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    rows = []
    for item in results:
        duplicated = [x for x in item["comparisons"] if x["status"] == "duplicate"]
        conflicts = [x for x in item["comparisons"] if x["status"] == "conflict_or_different_semantics"]
        duplicate_names = ", ".join(f"{x['additional_field']}" for x in duplicated) or "—"
        conflict_json = html.escape(json.dumps(conflicts, ensure_ascii=False, indent=2))
        classification_json = html.escape(json.dumps(item["classification"], ensure_ascii=False, indent=2))
        rows.append(
            f"<tr><td>{html.escape(item['title'] or item['document_id'])}</td><td>{len(duplicated)}<br>{html.escape(duplicate_names)}</td>"
            f"<td>{len(conflicts)}<details><summary>Voir</summary><pre>{conflict_json}</pre></details></td>"
            f"<td><pre>{classification_json}</pre></td></tr>"
        )
    proposed = html.escape(json.dumps(proposed_schema, ensure_ascii=False, indent=2))
    page = f"""<!doctype html><html lang='fr'><head><meta charset='utf-8'><title>Audit redondance metadata</title>
<style>body{{font:14px/1.45 system-ui;margin:24px;color:#172033}}.note{{background:#eef5ff;border-left:4px solid #2477d4;padding:13px}}.proposal{{display:grid;grid-template-columns:1fr 1fr;gap:18px}}table{{border-collapse:collapse;width:100%;margin-top:18px}}th,td{{border:1px solid #d9dfeb;padding:8px;vertical-align:top}}th{{background:#edf1f7}}pre{{white-space:pre-wrap;word-break:break-word;background:#f8fafc;padding:9px;font:12px/1.4 ui-monospace,monospace}}summary{{cursor:pointer;font-weight:650}}</style></head><body>
<h1>Audit de redondance des métadonnées</h1><p class='note'>Aucune donnée n'a été supprimée. Cet audit compare la base document_metadata aux métadonnées additionnelles actuelles des 12 interpellations.</p>
<div class='proposal'><section><h2>Résultat global</h2><p>Comparaisons identiques : <strong>{aggregate['duplicate']}</strong><br>Conflits ou sens différents : <strong>{aggregate['conflict_or_different_semantics']}</strong><br>Champs absents : <strong>{aggregate['missing_in_additional']}</strong></p></section><section><h2>Proposition minimale</h2><pre>{proposed}</pre></section></div>
<table><thead><tr><th>Document</th><th>Champs dupliqués</th><th>Conflits</th><th>Classement des autres champs</th></tr></thead><tbody>{''.join(rows)}</tbody></table></body></html>"""
    (ROOT / "metadata_redundancy_audit.html").write_text(page, encoding="utf-8")
    print(json.dumps({"documents": len(results), **dict(aggregate)}, ensure_ascii=False))
    print(ROOT / "metadata_redundancy_audit.html")


if __name__ == "__main__":
    main()
