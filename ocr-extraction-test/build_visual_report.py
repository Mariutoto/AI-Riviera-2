from __future__ import annotations

import html
import json
import re
from difflib import SequenceMatcher
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
RESULTS = ROOT / "results"

DOCUMENTS = [
    {
        "id": "postulat-huber-quai-roussy",
        "label": "Postulat Huber — Quai Roussy",
        "scraper": PROJECT_ROOT / "documents/la-tour-de-peilz/2026/postulats/Postulat-Huber-Chervet-Quai-roussy",
    },
    {
        "id": "motion-roethlisberger-frais-garde",
        "label": "Motion Roethlisberger — Frais de garde",
        "scraper": PROJECT_ROOT / "documents/la-tour-de-peilz/2026/motions/Motion-Roethlisberger-Frais-garde",
    },
    {
        "id": "interpellation-urech-travaux-preavis",
        "label": "Interpellation Urech — Travaux préavis 17/2024",
        "scraper": PROJECT_ROOT / "documents/la-tour-de-peilz/2026/interpellations/Interpellation-Urech-Travaux-preavis-17-2024",
    },
]


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig", errors="replace").strip()


def normalize(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^\wàâäçéèêëîïôöùûüÿœæ'-]+", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, normalize(left), normalize(right), autojunk=False).ratio()


def stats(text: str) -> tuple[int, int]:
    return len(text), len(normalize(text).split())


def text_panel(title: str, subtitle: str, text: str, css_class: str) -> str:
    chars, words = stats(text)
    return f"""
    <section class="panel {css_class}">
      <h3>{html.escape(title)}</h3>
      <p class="muted">{html.escape(subtitle)} · {chars:,} caractères · {words:,} mots</p>
      <pre>{html.escape(text)}</pre>
    </section>
    """


def build() -> Path:
    test_sources = {
        item["id"]: item["url"]
        for item in json.loads((ROOT / "documents.json").read_text(encoding="utf-8"))
    }
    cards = []
    summary_rows = []
    for document in DOCUMENTS:
        scraper_text = read(document["scraper"].with_suffix(".txt"))
        metadata = json.loads(read(document["scraper"].with_suffix(".json")))
        scraper_url = metadata.get("pdf_url", "")
        test_url = test_sources[document["id"]]
        same_source = scraper_url == test_url
        native_text = read(RESULTS / document["id"] / "native.txt")
        ocr_text = read(RESULTS / document["id"] / "mistral_ocr.md")
        native_similarity = similarity(scraper_text, native_text)
        ocr_similarity = similarity(scraper_text, ocr_text)
        scraper_chars, scraper_words = stats(scraper_text)
        native_chars, native_words = stats(native_text)
        ocr_chars, ocr_words = stats(ocr_text)

        summary_rows.append(
            f"""
            <tr>
              <td>{html.escape(document['label'])}</td>
              <td>{scraper_words:,}</td><td>{native_words:,}</td><td>{ocr_words:,}</td>
              <td>{native_similarity:.1%}</td><td>{ocr_similarity:.1%}</td>
            </tr>
            """
        )
        cards.append(
            f"""
            <article class="document">
              <h2>{html.escape(document['label'])}</h2>
              {'' if same_source else '<div class="warning"><strong>Attention :</strong> les PDF ne sont pas identiques. Le scraper contient la demande seule, tandis que le test contient la demande et la réponse municipale. Les scores ne mesurent donc pas ici la qualité d’extraction.</div>'}
              <div class="verdict">
                Le scraper et l'extraction native concordent à <strong>{native_similarity:.1%}</strong>.
                Le scraper et Mistral OCR concordent à <strong>{ocr_similarity:.1%}</strong>.
              </div>
              <div class="grid">
                {text_panel('1. Scraper existant', 'Texte actuellement utilisé par AI Riviera', scraper_text, 'scraper')}
                {text_panel('2. Extraction native', 'PyMuPDF brut dans le test', native_text, 'native')}
                {text_panel('3. Mistral OCR', 'Sortie Markdown de mistral-ocr-latest', ocr_text, 'ocr')}
              </div>
              <details>
                <summary>Métadonnées JSON du scraper</summary>
                <pre class="json">{html.escape(json.dumps(metadata, ensure_ascii=False, indent=2))}</pre>
              </details>
              <details>
                <summary>Chiffres détaillés</summary>
                <table>
                  <tr><th>Méthode</th><th>Caractères</th><th>Mots</th></tr>
                  <tr><td>Scraper</td><td>{scraper_chars:,}</td><td>{scraper_words:,}</td></tr>
                  <tr><td>Native</td><td>{native_chars:,}</td><td>{native_words:,}</td></tr>
                  <tr><td>Mistral OCR</td><td>{ocr_chars:,}</td><td>{ocr_words:,}</td></tr>
                </table>
              </details>
            </article>
            """
        )

    page = f"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Comparaison extraction — AI Riviera</title>
<style>
  :root {{ color-scheme: light; --ink:#172033; --muted:#667085; --line:#d9dfeb; --bg:#f4f6fa; }}
  * {{ box-sizing:border-box }} body {{ margin:0; font:15px/1.5 system-ui,sans-serif; color:var(--ink); background:var(--bg) }}
  main {{ max-width:1800px; margin:auto; padding:28px }} h1 {{ margin:0 0 8px }} h2 {{ margin-top:0 }}
  .intro,.document {{ background:white; border:1px solid var(--line); border-radius:14px; padding:22px; margin-bottom:22px; box-shadow:0 3px 14px #1720330b }}
  .muted {{ color:var(--muted) }} .verdict {{ background:#eef8f1; border-left:4px solid #2f9e60; padding:10px 14px; margin:12px 0 18px }}
  .warning {{ background:#fff4dd; border-left:4px solid #e28a18; padding:10px 14px; margin:12px 0 }}
  .grid {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:14px }}
  .panel {{ min-width:0; border:1px solid var(--line); border-top:5px solid; border-radius:10px; padding:14px }}
  .scraper {{ border-top-color:#6f42c1 }} .native {{ border-top-color:#2477d4 }} .ocr {{ border-top-color:#e28a18 }}
  pre {{ white-space:pre-wrap; overflow:auto; max-height:560px; padding:12px; background:#f8fafc; border-radius:8px; font:13px/1.45 ui-monospace,monospace }}
  table {{ width:100%; border-collapse:collapse; margin:12px 0 }} th,td {{ padding:9px 11px; border:1px solid var(--line); text-align:left }} th {{ background:#f1f4f9 }}
  details {{ margin-top:14px }} summary {{ cursor:pointer; font-weight:650 }} .json {{ max-height:420px }}
  @media(max-width:1000px) {{ .grid {{ grid-template-columns:1fr }} }}
</style></head><body><main>
  <section class="intro">
    <h1>Comparaison visuelle des extractions</h1>
    <p class="muted">Scrapers AI Riviera existants contre PyMuPDF brut et Mistral OCR.</p>
    <table><thead><tr><th>Document</th><th>Mots scraper</th><th>Mots natifs</th><th>Mots OCR</th><th>Scraper ↔ native</th><th>Scraper ↔ OCR</th></tr></thead>
    <tbody>{''.join(summary_rows)}</tbody></table>
    <p><strong>Lecture :</strong> un score élevé signifie que les textes se ressemblent. Pour juger la qualité réelle, fais défiler les trois colonnes et vérifie surtout les noms, nombres, accents et l'ordre des paragraphes.</p>
  </section>
  {''.join(cards)}
</main></body></html>"""
    output = RESULTS / "visual_comparison.html"
    output.write_text(page, encoding="utf-8")
    return output


if __name__ == "__main__":
    print(build())
