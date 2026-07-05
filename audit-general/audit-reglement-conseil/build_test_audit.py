import hashlib
import html
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SOURCE_DIR = ROOT / "documents" / "la-tour-de-peilz" / "institutionnel" / "conseil-communal"
ARTICLE_DIR = ROOT / "documents" / "la-tour-de-peilz" / "institutionnel" / "reglements" / "reglement-conseil-communal"
OUTPUT_DIR = Path(__file__).resolve().parent / "test-audit"

BASE_FIELDS = {
    "document_id",
    "commune",
    "document_family",
    "category",
    "document_role",
    "title",
    "source_title",
    "source_page_url",
    "file_url",
    "document_date",
    "content_hash",
    "extraction_method",
    "processing_status",
}

CHUNK_FIELDS = {
    "chunk_type",
    "article_number",
    "article_title",
    "title_heading",
    "chapter",
}

FIELD_AUDIT = [
    ("commune", "base", "Conserver dans document_metadata."),
    ("title", "base", "Conserver dans document_metadata."),
    ("pdf_url", "rename", "Renommer file_url dans document_metadata."),
    ("source_page", "rename", "Renommer source_page_url dans document_metadata."),
    ("document_date", "correct", "Conserver, mais corriger 2013-06-09 en 2017-10-25."),
    ("doc_type", "replace", "Remplacé par category = reglement_conseil_communal."),
    ("content_kind", "replace", "Remplacé par document_family et document_role."),
    ("regulation_name", "duplicate", "Doublon du titre."),
    ("body", "remove", "Peu utile et déductible de la catégorie."),
    ("jurisdiction", "duplicate", "Doublon de commune."),
    ("is_normative", "remove", "Déductible de document_family = regulation."),
    ("contains_articles", "remove", "Déductible du type de document."),
    ("article_count", "remove", "Valeur calculable à partir des chunks."),
    ("regulation", "remove", "Bloc additionnel redondant refusé pour le schéma minimal."),
    ("institutional_document", "remove", "Bloc redondant."),
    ("search_facets", "remove", "À produire dans l'index de recherche, pas dans la source documentaire."),
    ("year", "remove", "La valeur institutionnel n'est pas une année documentaire."),
    ("institutional_category", "duplicate", "Doublon de category."),
    ("filename", "remove", "Déductible de file_url et inutile pour la recherche."),
    ("pdf_path", "remove", "Chemin local non portable."),
    ("text_path", "remove", "Chemin local non portable."),
    ("article_index_path", "remove", "Chemin local non portable."),
    ("metadata_version", "remove", "Information technique hors métadonnées documentaires."),
    ("text_extraction_status", "processing", "À conserver uniquement dans processing."),
    ("characters_extracted", "duplicate", "Déjà présent dans processing.text_extraction_status."),
]


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def document_id(pdf_bytes: bytes) -> str:
    return "doc_" + hashlib.sha256(pdf_bytes).hexdigest()[:20]


def make_base(source: dict, pdf_bytes: bytes) -> dict:
    return {
        "document_id": document_id(pdf_bytes),
        "commune": "La Tour-de-Peilz",
        "document_family": "regulation",
        "category": "reglement_conseil_communal",
        "document_role": "regulation_text",
        "title": "Règlement du Conseil communal",
        "source_title": "Règlement du Conseil communal de La Tour-de-Peilz 2017",
        "source_page_url": source["source_page"],
        "file_url": source["pdf_url"],
        "document_date": "2017-10-25",
        "content_hash": hashlib.sha256(pdf_bytes).hexdigest(),
        "extraction_method": "native_pdf",
        "processing_status": "audit_test_generated",
    }


def make_chunk(article: dict) -> dict:
    return {
        "chunk_type": "regulation_article",
        "article_number": article.get("article_number"),
        "article_title": article.get("article_title"),
        "title_heading": article.get("title_heading"),
        "chapter": article.get("chapter"),
    }


def render(payload: dict) -> str:
    esc = lambda value: html.escape(str(value))
    badges = {
        "base": "good", "rename": "good", "correct": "warn", "replace": "good",
        "duplicate": "bad", "remove": "bad", "processing": "info",
    }
    rows = "".join(
        f"<tr><td><code>{esc(row['field'])}</code></td>"
        f"<td><span class='badge {badges[row['classification']]}'>{esc(row['classification'])}</span></td>"
        f"<td>{esc(row['reason'])}</td></tr>"
        for row in payload["field_audit"]
    )
    samples = "".join(
        f"<article><h3>Article {esc(item['chunk_metadata']['article_number'])} — "
        f"{esc(item['chunk_metadata']['article_title'])}</h3>"
        f"<pre>{esc(json.dumps(item['chunk_metadata'], ensure_ascii=False, indent=2))}</pre>"
        f"<details><summary>Aperçu du texte</summary><p>{esc(item['text_preview'])}</p></details></article>"
        for item in payload["chunk_samples"]
    )
    anomalies = "".join(f"<li>{esc(value)}</li>" for value in payload["observations"])
    return f"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Audit test — Règlement du Conseil communal</title>
<style>
body{{font-family:Inter,Segoe UI,sans-serif;background:#f4f6f8;color:#17202a;margin:0}}main{{max-width:1120px;margin:auto;padding:32px}}
.hero,section,article{{background:white;border:1px solid #dfe5eb;border-radius:14px;padding:22px;margin-bottom:18px}}h1{{margin-top:0}}h2{{margin-top:0;font-size:1.25rem}}
.stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px}}.stat{{background:#eef4ff;border-radius:10px;padding:14px}}.stat strong{{display:block;font-size:1.7rem}}
pre{{background:#101820;color:#e8f0f7;padding:18px;border-radius:10px;overflow:auto}}table{{width:100%;border-collapse:collapse}}th,td{{padding:10px;border-bottom:1px solid #e6ebef;text-align:left;vertical-align:top}}
.badge{{padding:4px 8px;border-radius:999px;font-size:.82rem}}.good{{background:#d9f7e5;color:#126b39}}.warn{{background:#fff1c7;color:#815c00}}.bad{{background:#ffe1e1;color:#8a2020}}.info{{background:#dcecff;color:#18558d}}
code{{font-family:Consolas,monospace}}details p{{white-space:pre-wrap;line-height:1.45}}</style></head>
<body><main><div class="hero"><h1>Audit test — Règlement du Conseil communal</h1>
<p>Test du schéma minimal : métadonnées documentaires de base et cinq champs structurels par article. Aucun bloc <code>regulation_metadata</code> n'est ajouté.</p>
<div class="stats"><div class="stat"><strong>{payload['stats']['article_files']}</strong>chunks actuels</div><div class="stat"><strong>{payload['stats']['unique_article_numbers']}</strong>numéros uniques</div><div class="stat"><strong>{payload['stats']['base_fields']}</strong>champs de base</div><div class="stat"><strong>{payload['stats']['chunk_fields']}</strong>champs par chunk</div></div></div>
<section><h2>Base documentaire proposée</h2><pre>{esc(json.dumps(payload['proposed_document_metadata'], ensure_ascii=False, indent=2))}</pre></section>
<section><h2>Tri des champs actuels</h2><table><thead><tr><th>Champ actuel</th><th>Classement</th><th>Décision</th></tr></thead><tbody>{rows}</tbody></table></section>
<section><h2>Observations à traiter dans l'audit général</h2><ul>{anomalies}</ul></section>
<section><h2>Échantillon de chunks minimaux</h2>{samples}</section>
</main></body></html>"""


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    source = load_json(SOURCE_DIR / "reglement-conseil-communal.json")
    pdf_bytes = (SOURCE_DIR / "reglement-conseil-communal.pdf").read_bytes()
    articles = [load_json(path) for path in sorted(ARTICLE_DIR.glob("art-*.json"))]
    by_number = {item.get("article_number"): item for item in articles}
    counts = Counter(item.get("article_number") for item in articles)
    sample_numbers = ["1", "54b", "86", "140", "164"]
    samples = []
    for number in sample_numbers:
        article = by_number.get(number)
        if not article:
            continue
        samples.append({
            "chunk_metadata": make_chunk(article),
            "text_preview": article.get("article_text", "")[:800],
        })
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "test_audit",
        "decision": "No regulation_metadata block; base document metadata plus five chunk fields.",
        "proposed_document_metadata": make_base(source, pdf_bytes),
        "chunk_schema": sorted(CHUNK_FIELDS),
        "field_audit": [
            {"field": field, "classification": classification, "reason": reason}
            for field, classification, reason in FIELD_AUDIT
        ],
        "stats": {
            "article_files": len(articles),
            "unique_article_numbers": len(counts),
            "base_fields": len(BASE_FIELDS),
            "chunk_fields": len(CHUNK_FIELDS),
        },
        "observations": [
            "La date actuelle 2013-06-09 est erronée : le texte indique une adoption le 25 octobre 2017.",
            "Le parseur produit 165 chunks mais seulement 164 numéros uniques : l'article 140 apparaît deux fois.",
            "Les numéros 49, 114, 141 et 160 sont absents de la série actuelle et doivent être vérifiés dans le PDF avant de parler de données manquantes.",
            "Le titre actuel de l'article 164 est faux : il reprend le titre de l'annexe alors que le texte concerne l'entrée en vigueur.",
            "Le découpage par article est pertinent, mais le contenu et la hiérarchie doivent être audités avant l'indexation finale.",
        ],
        "chunk_samples": samples,
    }
    (OUTPUT_DIR / "audit.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUTPUT_DIR / "audit.html").write_text(render(payload), encoding="utf-8")
    print(f"Wrote {OUTPUT_DIR / 'audit.html'}")
    print(json.dumps(payload["stats"], ensure_ascii=False))


if __name__ == "__main__":
    main()
