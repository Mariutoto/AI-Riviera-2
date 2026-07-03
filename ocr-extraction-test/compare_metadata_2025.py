from __future__ import annotations

import html
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.metadata_enrichment import enrich_metadata


CORPUS_RESULTS = ROOT / "corpus_2024_2026" / "results" / "2025"
SOURCE_ROOT = PROJECT_ROOT / "documents" / "la-tour-de-peilz" / "2025"
OUTPUT = ROOT / "metadata_test_2025"
CATEGORIES = ("motions", "postulats", "interpellations")
FIELDS = (
    "category",
    "content_kind",
    "title",
    "summary",
    "authors",
    "status",
    "status_normalized",
    "document_role",
    "document_components",
    "contains_response",
    "document_date",
    "object_title",
    "political_object",
    "search_facets",
)


def normalized(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    value = str(value).lower()
    value = "".join(c for c in unicodedata.normalize("NFD", value) if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", value).strip()


def equivalent(left: Any, right: Any) -> bool:
    return normalized(left) == normalized(right)


def corpus_result_dir(category: str, stem: str) -> Path:
    expected = CORPUS_RESULTS / category / f"2025-{category}-{stem}"
    if not expected.exists():
        raise FileNotFoundError(expected)
    return expected


def base_metadata(reference: dict) -> dict:
    return {
        "commune": reference.get("commune", "La Tour-de-Peilz"),
        "year": "2025",
        "listing_year": "2025",
        "filename": reference.get("filename", ""),
        "pdf_url": reference.get("pdf_url", ""),
        "source_page": reference.get("source_page", ""),
    }


def accuracy(reference: dict, candidate: dict) -> tuple[int, int, float]:
    fields = [field for field in FIELDS if reference.get(field) not in (None, "", [], {})]
    matches = sum(equivalent(reference.get(field), candidate.get(field)) for field in fields)
    return matches, len(fields), matches / len(fields) if fields else 1.0


def display(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, indent=2)
    if isinstance(value, bool):
        return "oui" if value else "non"
    return str(value)


def detail_page(row: dict, output: Path) -> None:
    lines = []
    for field in FIELDS:
        ref = row["reference"].get(field)
        native = row["native"].get(field)
        ocr = row["ocr"].get(field)
        native_class = "match" if equivalent(ref, native) else "diff"
        ocr_class = "match" if equivalent(ref, ocr) else "diff"
        lines.append(
            f"<tr><th>{html.escape(field)}</th>"
            f"<td><pre>{html.escape(display(ref))}</pre></td>"
            f"<td class='{native_class}'><pre>{html.escape(display(native))}</pre></td>"
            f"<td class='{ocr_class}'><pre>{html.escape(display(ocr))}</pre></td></tr>"
        )
    page = f"""<!doctype html><html lang='fr'><head><meta charset='utf-8'><title>{html.escape(row['filename'])}</title>
<style>body{{font:14px/1.4 system-ui;margin:24px;color:#172033}}table{{border-collapse:collapse;width:100%;table-layout:fixed}}th,td{{border:1px solid #d9dfeb;padding:8px;vertical-align:top}}th{{background:#edf1f7;width:13%}}pre{{white-space:pre-wrap;word-break:break-word;margin:0;font:12px/1.4 ui-monospace,monospace}}.match{{background:#eef9f1}}.diff{{background:#fff0e8}}.scores{{background:#eef5ff;padding:12px;border-left:4px solid #2477d4}}</style></head><body>
<p><a href='dashboard.html'>← Retour au tableau 2025</a></p><h1>{html.escape(row['filename'])}</h1>
<p class='scores'>Correspondance avec les métadonnées existantes : natif <strong>{row['native_accuracy']:.1%}</strong> · OCR <strong>{row['ocr_accuracy']:.1%}</strong>.</p>
<p>Vert = valeur identique à la référence. Orange = valeur différente. Une différence n'est pas automatiquement une erreur : les métadonnées existantes ne sont pas une vérité humaine parfaite.</p>
<table><thead><tr><th>Champ</th><th>Référence scraper</th><th>Depuis texte natif</th><th>Depuis OCR</th></tr></thead><tbody>{''.join(lines)}</tbody></table></body></html>"""
    output.write_text(page, encoding="utf-8")


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for category in CATEGORIES:
        for metadata_path in sorted((SOURCE_ROOT / category).glob("*.json")):
            reference = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
            result_dir = corpus_result_dir(category, metadata_path.stem)
            native_text = (result_dir / "native.txt").read_text(encoding="utf-8")
            ocr_text = (result_dir / "ocr.md").read_text(encoding="utf-8")
            base = base_metadata(reference)
            native = enrich_metadata(base, text_path=metadata_path.with_suffix(".txt"), content=native_text)
            ocr = enrich_metadata(base, text_path=metadata_path.with_suffix(".txt"), content=ocr_text)
            native_matches, field_count, native_score = accuracy(reference, native)
            ocr_matches, _, ocr_score = accuracy(reference, ocr)
            row = {
                "filename": reference.get("filename", metadata_path.name),
                "category": category,
                "kind": "réponse" if reference.get("contains_response") else reference.get("document_role", "objet"),
                "field_count": field_count,
                "native_matches": native_matches,
                "ocr_matches": ocr_matches,
                "native_accuracy": native_score,
                "ocr_accuracy": ocr_score,
                "reference": reference,
                "native": native,
                "ocr": ocr,
                "detail": f"{metadata_path.stem}.html",
            }
            detail_page(row, OUTPUT / row["detail"])
            rows.append(row)

    table_rows = []
    for row in rows:
        better = "OCR" if row["ocr_accuracy"] > row["native_accuracy"] else "Natif" if row["native_accuracy"] > row["ocr_accuracy"] else "Égalité"
        table_rows.append(
            f"<tr><td>{html.escape(row['category'])}</td><td><a href='{html.escape(row['detail'])}'>{html.escape(row['filename'])}</a></td>"
            f"<td>{row['field_count']}</td><td>{row['native_matches']}/{row['field_count']} ({row['native_accuracy']:.1%})</td>"
            f"<td>{row['ocr_matches']}/{row['field_count']} ({row['ocr_accuracy']:.1%})</td><td>{better}</td></tr>"
        )
    native_total = sum(row["native_matches"] for row in rows)
    ocr_total = sum(row["ocr_matches"] for row in rows)
    field_total = sum(row["field_count"] for row in rows)
    page = f"""<!doctype html><html lang='fr'><head><meta charset='utf-8'><title>Métadonnées 2025</title>
<style>body{{font:14px/1.45 system-ui;margin:24px;color:#172033}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #d9dfeb;padding:8px;text-align:left}}th{{background:#edf1f7}}.summary{{background:#eef5ff;border-left:4px solid #2477d4;padding:14px}}</style></head><body>
<h1>Test des métadonnées — documents politiques 2025</h1><div class='summary'><strong>{len(rows)} documents</strong><br>Correspondance globale avec les JSON existants : natif <strong>{native_total/field_total:.1%}</strong> · OCR <strong>{ocr_total/field_total:.1%}</strong>.</div>
<p>Ce test utilise le même enrichisseur sur les deux textes. Clique sur un document pour comparer chaque champ. Les JSON existants servent de référence technique, pas de vérité humaine certifiée.</p>
<table><thead><tr><th>Catégorie</th><th>Document</th><th>Champs évalués</th><th>Texte natif</th><th>Texte OCR</th><th>Meilleur score</th></tr></thead><tbody>{''.join(table_rows)}</tbody></table></body></html>"""
    (OUTPUT / "dashboard.html").write_text(page, encoding="utf-8")
    (OUTPUT / "comparison.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"documents": len(rows), "fields": field_total, "native_accuracy": native_total / field_total, "ocr_accuracy": ocr_total / field_total}, indent=2))
    print(OUTPUT / "dashboard.html")


if __name__ == "__main__":
    main()
