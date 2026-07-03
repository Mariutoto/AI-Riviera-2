from __future__ import annotations

import base64
import html
import json
import os
import re
from difflib import SequenceMatcher
from pathlib import Path

import fitz
import requests
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
SOURCE_ROOT = PROJECT_ROOT / "documents" / "la-tour-de-peilz"
CORPUS_ROOT = ROOT / "corpus_2024_2026"
PDF_ROOT = CORPUS_ROOT / "pdfs"
RESULTS_ROOT = CORPUS_ROOT / "results"
CATEGORIES = ("motions", "postulats", "interpellations")
YEARS = (2024, 2025, 2026)
MODEL = "mistral-ocr-latest"
OCR_URL = "https://api.mistral.ai/v1/ocr"


def normalize(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^\wàâäçéèêëîïôöùûüÿœæ'-]+", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def stats(text: str) -> dict:
    normalized = normalize(text)
    return {"characters": len(text), "words": len(normalized.split())}


def similarity(left: str, right: str) -> float:
    return round(SequenceMatcher(None, normalize(left), normalize(right), autojunk=False).ratio(), 4)


def response_kind(filename: str) -> str:
    stem = Path(filename).stem.lower()
    if re.search(r"(?:^|[-_])(rep|reponse)(?:[-_]|$)", stem):
        return "réponse"
    if re.search(r"(?:^|[-_])(rapp|rapport)(?:[-_]|$)", stem):
        return "rapport/réponse"
    return "objet initial"


def inventory() -> list[dict]:
    rows = []
    for year in YEARS:
        for category in CATEGORIES:
            for metadata_path in sorted((SOURCE_ROOT / str(year) / category).glob("*.json")):
                try:
                    metadata = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
                except (OSError, json.JSONDecodeError):
                    continue
                url = metadata.get("pdf_url")
                text_path = metadata_path.with_suffix(".txt")
                if not url or not text_path.exists():
                    continue
                filename = metadata.get("filename") or Path(url).name
                rows.append(
                    {
                        "id": f"{year}-{category}-{metadata_path.stem}",
                        "year": year,
                        "category": category,
                        "kind": response_kind(filename),
                        "filename": filename,
                        "title": metadata.get("title") or metadata.get("object_title") or metadata_path.stem,
                        "url": url,
                        "metadata_path": str(metadata_path),
                        "text_path": str(text_path),
                        "metadata": metadata,
                    }
                )
    return rows


def download(row: dict) -> Path:
    path = PDF_ROOT / str(row["year"]) / row["category"] / row["filename"]
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > 0:
        return path
    response = requests.get(row["url"], timeout=120)
    response.raise_for_status()
    if not response.content.startswith(b"%PDF"):
        raise ValueError("La réponse téléchargée n'est pas un PDF")
    path.write_bytes(response.content)
    return path


def native_extract(path: Path) -> tuple[str, int]:
    with fitz.open(path) as pdf:
        return "\n\n".join(page.get_text("text") for page in pdf).strip(), len(pdf)


def ocr_extract(path: Path, api_key: str, cache_path: Path) -> tuple[str, dict]:
    if cache_path.exists():
        data = json.loads(cache_path.read_text(encoding="utf-8"))
    else:
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        response = requests.post(
            OCR_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": MODEL,
                "document": {
                    "type": "document_url",
                    "document_url": f"data:application/pdf;base64,{encoded}",
                },
                "table_format": "markdown",
                "confidence_scores_granularity": "page",
            },
            timeout=600,
        )
        response.raise_for_status()
        data = response.json()
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    text = "\n\n".join(page.get("markdown", "") for page in data.get("pages", [])).strip()
    return text, data


def confidence(data: dict) -> float | None:
    scores = []
    for page in data.get("pages", []):
        value = (page.get("confidence_scores") or {}).get("average_page_confidence_score")
        if value is not None:
            scores.append(float(value))
    return round(sum(scores) / len(scores), 4) if scores else None


def detail_page(row: dict, scraper: str, native: str, ocr: str, result: dict, output: Path) -> None:
    def panel(label: str, text: str, color: str) -> str:
        info = stats(text)
        return f"<section style='border-top:5px solid {color}'><h2>{html.escape(label)}</h2><p>{info['words']} mots · {info['characters']} caractères</p><pre>{html.escape(text)}</pre></section>"
    warning = ""
    if result["scraper_vs_native"] < 0.95:
        warning = "<div class='warning'>Le texte enregistré par le scraper diffère sensiblement de l'extraction native du même PDF. Vérifie le nettoyage, l'ordre de lecture ou une ancienne version locale.</div>"
    page = f"""<!doctype html><html lang='fr'><head><meta charset='utf-8'><title>{html.escape(row['filename'])}</title>
<style>body{{font:15px/1.5 system-ui;margin:24px;background:#f4f6fa;color:#172033}}a{{color:#1769c2}}.grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}}section,details,.summary{{background:white;border:1px solid #d9dfeb;border-radius:10px;padding:14px}}pre{{white-space:pre-wrap;max-height:70vh;overflow:auto;background:#f8fafc;padding:12px;font:13px/1.45 ui-monospace,monospace}}.warning{{background:#fff4dd;border-left:4px solid #e28a18;padding:12px;margin:12px 0}}@media(max-width:1000px){{.grid{{grid-template-columns:1fr}}}}</style></head><body>
<p><a href='../dashboard.html'>← Retour au tableau</a></p><div class='summary'><h1>{html.escape(row['title'])}</h1><p>{row['year']} · {html.escape(row['category'])} · {html.escape(row['kind'])}</p><p>Scraper ↔ native : <strong>{result['scraper_vs_native']:.1%}</strong> · Scraper ↔ OCR : <strong>{result['scraper_vs_ocr']:.1%}</strong> · Confiance OCR : <strong>{result['ocr_confidence'] if result['ocr_confidence'] is not None else 'n/a'}</strong></p></div>{warning}
<div class='grid'>{panel('Scraper existant', scraper, '#6f42c1')}{panel('Extraction native', native, '#2477d4')}{panel('Mistral OCR', ocr, '#e28a18')}</div>
<details><summary>Métadonnées JSON</summary><pre>{html.escape(json.dumps(row['metadata'], ensure_ascii=False, indent=2))}</pre></details></body></html>"""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(page, encoding="utf-8")


def dashboard(results: list[dict], failures: list[dict]) -> None:
    rows = []
    for item in results:
        css = "low" if min(item["scraper_vs_native"], item["scraper_vs_ocr"]) < 0.9 else "ok"
        rows.append(f"""<tr class='{css}' data-year='{item['year']}' data-category='{item['category']}' data-kind='{item['kind']}'>
<td>{item['year']}</td><td>{html.escape(item['category'])}</td><td>{html.escape(item['kind'])}</td><td><a href='{html.escape(item['detail'])}'>{html.escape(item['title'])}</a></td><td>{item['pages']}</td><td>{item['scraper_words']}</td><td>{item['native_words']}</td><td>{item['ocr_words']}</td><td>{item['scraper_vs_native']:.1%}</td><td>{item['scraper_vs_ocr']:.1%}</td><td>{item['native_vs_ocr']:.1%}</td><td>{item['ocr_confidence'] if item['ocr_confidence'] is not None else ''}</td></tr>""")
    failure_html = "".join(f"<li>{html.escape(x['id'])}: {html.escape(x['error'])}</li>" for x in failures)
    page = f"""<!doctype html><html lang='fr'><head><meta charset='utf-8'><title>Corpus OCR 2024–2026</title>
<style>body{{font:14px/1.45 system-ui;margin:24px;color:#172033}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #d9dfeb;padding:7px;text-align:left}}th{{background:#edf1f7;position:sticky;top:0}}tr.low{{background:#fff0e8}}tr.ok{{background:#f3fbf5}}select{{padding:7px;margin:0 8px 14px 0}}.note{{background:#eef5ff;border-left:4px solid #2477d4;padding:12px}}</style></head><body>
<h1>Comparaison complète 2024–2026</h1><p class='note'>Chaque ligne compare exactement le même PDF avec le texte sauvegardé par le scraper, PyMuPDF brut et Mistral OCR. Clique sur le titre pour voir les trois textes côte à côte.</p>
<label>Année <select id='year'><option value=''>Toutes</option><option>2024</option><option>2025</option><option>2026</option></select></label><label>Catégorie <select id='cat'><option value=''>Toutes</option><option>motions</option><option>postulats</option><option>interpellations</option></select></label><label>Type <select id='kind'><option value=''>Tous</option><option>objet initial</option><option>réponse</option><option>rapport/réponse</option></select></label>
<table><thead><tr><th>Année</th><th>Catégorie</th><th>Type</th><th>Document</th><th>Pages</th><th>Mots scraper</th><th>Mots natifs</th><th>Mots OCR</th><th>Scraper↔native</th><th>Scraper↔OCR</th><th>Native↔OCR</th><th>Confiance OCR</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
<h2>Échecs ({len(failures)})</h2><ul>{failure_html or '<li>Aucun</li>'}</ul>
<script>const filters=['year','cat','kind'];function apply(){{document.querySelectorAll('tbody tr').forEach(r=>{{r.hidden=(year.value&&r.dataset.year!==year.value)||(cat.value&&r.dataset.category!==cat.value)||(kind.value&&r.dataset.kind!==kind.value)}})}}filters.forEach(x=>document.getElementById(x).addEventListener('change',apply));</script></body></html>"""
    (RESULTS_ROOT / "dashboard.html").write_text(page, encoding="utf-8")


def main() -> None:
    load_dotenv(ROOT / ".env")
    api_key = os.getenv("MISTRAL_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("MISTRAL_API_KEY manque dans .env")
    rows = inventory()
    results = []
    failures = []
    for index, row in enumerate(rows, 1):
        print(f"[{index}/{len(rows)}] {row['year']} {row['category']} {row['filename']}")
        result_dir = RESULTS_ROOT / str(row["year"]) / row["category"] / row["id"]
        try:
            pdf_path = download(row)
            scraper = Path(row["text_path"]).read_text(encoding="utf-8-sig", errors="replace").strip()
            native, pages = native_extract(pdf_path)
            ocr, raw = ocr_extract(pdf_path, api_key, result_dir / "ocr.json")
            (result_dir / "scraper.txt").write_text(scraper + "\n", encoding="utf-8")
            (result_dir / "native.txt").write_text(native + "\n", encoding="utf-8")
            (result_dir / "ocr.md").write_text(ocr + "\n", encoding="utf-8")
            result = {
                "id": row["id"], "year": row["year"], "category": row["category"], "kind": row["kind"],
                "title": row["title"], "filename": row["filename"], "url": row["url"], "pages": pages,
                "scraper_words": stats(scraper)["words"], "native_words": stats(native)["words"], "ocr_words": stats(ocr)["words"],
                "scraper_vs_native": similarity(scraper, native), "scraper_vs_ocr": similarity(scraper, ocr),
                "native_vs_ocr": similarity(native, ocr), "ocr_confidence": confidence(raw),
                "detail": f"{row['year']}/{row['category']}/{row['id']}/detail.html",
            }
            detail_page(row, scraper, native, ocr, result, result_dir / "detail.html")
            results.append(result)
        except Exception as exc:
            failures.append({"id": row["id"], "error": str(exc)})
            print(f"  ERROR: {exc}")
        (RESULTS_ROOT / "summary.json").parent.mkdir(parents=True, exist_ok=True)
        (RESULTS_ROOT / "summary.json").write_text(json.dumps({"model": MODEL, "results": results, "failures": failures}, ensure_ascii=False, indent=2), encoding="utf-8")
        dashboard(results, failures)
    print(f"Terminé: {len(results)} réussis, {len(failures)} échecs")
    print(RESULTS_ROOT / "dashboard.html")


if __name__ == "__main__":
    main()
