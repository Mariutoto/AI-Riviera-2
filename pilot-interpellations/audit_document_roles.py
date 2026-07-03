from __future__ import annotations

import html
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent

EXPECTED_METADATA_FIELDS = (
    "document_id", "commune", "document_family", "category", "document_role", "title", "source_title",
    "source_page_url", "file_url", "listing_year", "legislature", "document_date", "content_hash",
    "extraction_method", "processing_status",
)

INTERPELLATION_PATTERNS = [
    r"\binterpellation\b",
    r"\bquestions?\s*(?:à|a)\s+la\s+municipalit[ée]\b",
    r"\bj['’]?interpelle\s+la\s+municipalit[ée]\b",
    r"\bje\s+demande\s+(?:à|a)\s+la\s+municipalit[ée]\s+de\s+donner\s+des\s+r[ée]ponses\b",
]
RESPONSE_PATTERNS = [
    r"\br[ée]ponse\s+municipale\b",
    r"\br[ée]ponse\s+de\s+la\s+municipalit[ée]\b",
    r"\bla\s+municipalit[ée]\s+r[ée]pond\b",
    r"\br[ée]ponses?\s+de\s+la\s+municipalit[ée]\s+aux\s+questions?\b",
    r"\badopt[ée]\s+par\s+la\s+municipalit[ée]\b",
]


def load_records() -> list[dict]:
    return [json.loads(path.read_text(encoding="utf-8")) for path in sorted((ROOT / "document_metadata").glob("*.json"))]


def artifact_text(record: dict) -> str:
    filename = Path(record["file_url"]).stem
    return (ROOT / "artifacts" / filename / "native.txt").read_text(encoding="utf-8-sig", errors="replace")


def normalized_interpellation_metadata(scraper_metadata: dict) -> dict:
    return {
        "authors": scraper_metadata.get("authors") or [],
        "political_status": scraper_metadata.get("status_normalized") or "unknown",
    }


def evidence(text: str, patterns: list[str]) -> list[str]:
    found = []
    normalized = re.sub(r"\s+", " ", text)
    for pattern in patterns:
        for match in re.finditer(pattern, normalized, flags=re.I):
            start = max(0, match.start() - 100)
            end = min(len(normalized), match.end() + 180)
            excerpt = normalized[start:end].strip()
            if excerpt not in found:
                found.append(excerpt)
            if len(found) >= 3:
                return found
    return found


def detected_role(has_interpellation: bool, has_response: bool) -> str:
    if has_interpellation and has_response:
        return "combined_interpellation_response"
    if has_response:
        return "municipal_response"
    if has_interpellation:
        return "interpellation_text"
    return "needs_manual_review"


def main() -> None:
    combined_view_dir = ROOT / "combined_metadata_view"
    combined_view_dir.mkdir(parents=True, exist_ok=True)
    cleaning_report_path = ROOT / "cleaning_test" / "report.json"
    cleaning_by_id = {
        item["document_id"]: item
        for item in json.loads(cleaning_report_path.read_text(encoding="utf-8"))
    } if cleaning_report_path.exists() else {}
    rows = []
    for record in load_records():
        text = artifact_text(record)
        additional_metadata = json.loads((ROOT / "scraper_metadata" / f"{record['document_id']}.json").read_text(encoding="utf-8"))
        clean_interpellation_metadata = normalized_interpellation_metadata(additional_metadata)
        combined_metadata = {
            "document_metadata": record,
            "interpellation_metadata": clean_interpellation_metadata,
            "processing": {
                "text_extraction_status": additional_metadata.get("text_extraction_status") or {
                    "characters_extracted": len(text),
                    "text_available": bool(text.strip()),
                    "needs_ocr": None,
                }
            },
        }
        (combined_view_dir / f"{record['document_id']}.json").write_text(
            json.dumps(combined_metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        cleaning = cleaning_by_id.get(record["document_id"], {})
        missing_fields = [field for field in EXPECTED_METADATA_FIELDS if record.get(field) in (None, "", [], {})]
        completeness = (len(EXPECTED_METADATA_FIELDS) - len(missing_fields)) / len(EXPECTED_METADATA_FIELDS)
        interpellation_evidence = evidence(text, INTERPELLATION_PATTERNS)
        response_evidence = evidence(text, RESPONSE_PATTERNS)
        detected = detected_role(bool(interpellation_evidence), bool(response_evidence))
        rows.append({
            "document_id": record["document_id"],
            "title": record["title"],
            "file_url": record["file_url"],
            "declared_role": record["document_role"],
            "detected_role": detected,
            "matches_declared_role": detected == record["document_role"],
            "interpellation_evidence": interpellation_evidence,
            "response_evidence": response_evidence,
            "document_metadata": record,
            "missing_fields": missing_fields,
            "metadata_completeness": round(completeness, 4),
            "additional_metadata": clean_interpellation_metadata,
            "combined_metadata": combined_metadata,
            "cleaning": cleaning,
        })

    (ROOT / "role_audit.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    table_rows = []
    for row in rows:
        css = "match" if row["matches_declared_role"] else "review"
        question = "<br><br>".join(html.escape(value) for value in row["interpellation_evidence"]) or "No strong marker"
        response = "<br><br>".join(html.escape(value) for value in row["response_evidence"]) or "No strong marker"
        combined_json = html.escape(json.dumps(row["combined_metadata"], ensure_ascii=False, indent=2))
        missing = ", ".join(html.escape(field) for field in row["missing_fields"]) or "Aucun"
        completeness_class = "complete" if not row["missing_fields"] else "incomplete"
        cleaning = row["cleaning"]
        removed_json = html.escape(json.dumps(cleaning.get("removed_blocks", []), ensure_ascii=False, indent=2))
        cleaning_summary = (
            f"{cleaning.get('raw_words', '—')} → {cleaning.get('clean_words', '—')} mots<br>"
            f"{cleaning.get('removed_blocks_count', 0)} blocs retirés"
        )
        table_rows.append(
            f"<tr class='{css}'><td><a href='{html.escape(row['file_url'])}' target='_blank' rel='noopener'>{html.escape(row['title'])}</a></td>"
            f"<td><a href='{html.escape(row['file_url'])}' target='_blank' rel='noopener'>Ouvrir le PDF ↗</a></td>"
            f"<td>{html.escape(row['declared_role'])}</td>"
            f"<td><strong>{html.escape(row['detected_role'])}</strong></td><td>{question}</td><td>{response}</td>"
            f"<td class='{completeness_class}'><strong>{row['metadata_completeness']:.0%}</strong><br>Champs manquants : {missing}</td>"
            f"<td><details open><summary>Metadata complète</summary><pre>{combined_json}</pre></details>"
            f"<p><a href='combined_metadata_view/{row['document_id']}.json' target='_blank'>Ouvrir le JSON combiné</a></p></td>"
            f"<td>{cleaning_summary}<details><summary>Blocs retirés</summary><pre>{removed_json}</pre></details>"
            f"<p><a href='cleaning_test/{row['document_id']}/clean.txt' target='_blank'>Voir le texte nettoyé</a></p></td></tr>"
        )
    page = f"""<!doctype html><html lang='fr'><head><meta charset='utf-8'><title>Role audit</title>
<style>body{{font:14px/1.45 system-ui;margin:24px;color:#172033}}.note{{background:#eef5ff;border-left:4px solid #2477d4;padding:13px}}.table-wrap{{overflow-x:auto}}table{{border-collapse:collapse;min-width:2400px;width:100%}}th,td{{border:1px solid #d9dfeb;padding:8px;vertical-align:top}}th{{background:#edf1f7;position:sticky;top:0}}tr.match{{background:#f2faf4}}tr.review{{background:#fff0e8}}td.complete{{background:#dcf5e4}}td.incomplete{{background:#ffe3cf}}td:first-child{{min-width:260px}}td{{min-width:150px}}pre{{white-space:pre-wrap;word-break:break-word;max-height:420px;overflow:auto;background:#fff;padding:8px;font:12px/1.4 ui-monospace,monospace}}summary{{cursor:pointer;font-weight:650}}</style></head><body>
<h1>Audit des rôles et du nettoyage</h1><p class='note'><strong>Une ligne = un PDF physique.</strong> La colonne Metadata complète contient trois sections : <code>document_metadata</code>, <code>interpellation_metadata</code> et <code>processing</code>. Le statut d’extraction du texte appartient à <code>processing</code>. Les JSON bruts du scraper restent archivés séparément.</p>
<div class='table-wrap'><table><thead><tr><th>Document</th><th>PDF</th><th>Rôle actuel</th><th>Rôle détecté</th><th>Preuves d’interpellation</th><th>Preuves de réponse municipale</th><th>Complétude metadata</th><th>Metadata complète dans un JSON</th><th>Test headers/footers</th></tr></thead><tbody>{''.join(table_rows)}</tbody></table></div></body></html>"""
    (ROOT / "role_audit.html").write_text(page, encoding="utf-8")
    print(json.dumps({
        "documents": len(rows),
        "confirmed": sum(row["matches_declared_role"] for row in rows),
        "needs_review": sum(not row["matches_declared_role"] for row in rows),
        "incomplete_metadata": sum(bool(row["missing_fields"]) for row in rows),
    }, ensure_ascii=False))
    print(ROOT / "role_audit.html")


if __name__ == "__main__":
    main()
