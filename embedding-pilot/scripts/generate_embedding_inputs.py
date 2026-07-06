from __future__ import annotations

import hashlib
import html
import json
import re
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PILOT = ROOT / "embedding-pilot"
CONFIG = PILOT / "config"
OUTPUT = PILOT / "output"


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalized_hash(text: str) -> str:
    normalized = re.sub(r"\s+", " ", text).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def clean_embedding_content(text: str) -> tuple[str, dict]:
    marker_pattern = r"\b\d{1,3}\s*\|\s*\d{1,3}\b"
    markers = re.findall(marker_pattern, text)
    cleaned = re.sub(marker_pattern, " ", text)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r" *\n *", "\n", cleaned).strip()
    return cleaned, {"inline_page_markers_removed": len(markers)}


def canonical_component(chunk: dict) -> str | None:
    section = (chunk.get("section_title") or "").casefold()
    if "majorité" in section:
        return "majority_report"
    if "minorité" in section:
        return "minority_report"
    if section == "interpellation annexée":
        return "interpellation_annex"
    return chunk.get("component")


def metadata_index(pattern: str) -> dict[str, dict]:
    result = {}
    for path in ROOT.glob(pattern):
        payload = read_json(path)
        base = payload.get("document_metadata") or payload
        document_id = base.get("document_id")
        if document_id:
            result[document_id] = {"base": base, "path": path}
    return result


def resolve_regulation_base(index: dict[str, dict]) -> dict:
    if len(index) != 1:
        raise ValueError(f"Expected one regulation metadata file, found {len(index)}")
    return next(iter(index.values()))


def political_input(base: dict, chunk: dict) -> tuple[str, dict, dict]:
    content, cleaning = clean_embedding_content(str(chunk.get("content") or ""))
    values = {
        "document_family": base.get("document_family"),
        "category": base.get("category"),
        "document_role": base.get("document_role"),
        "title": base.get("title"),
        "component": canonical_component(chunk),
        "content": content,
    }
    text = (
        f"document_family: {values['document_family']}\n"
        f"category: {values['category']}\n"
        f"document_role: {values['document_role']}\n"
        f"title: {values['title']}\n"
        f"component: {values['component']}\n\n"
        f"{values['content'] or ''}"
    )
    return text, values, cleaning


def regulation_input(base: dict, chunk: dict) -> tuple[str, dict, dict]:
    specific = chunk.get("chunk_metadata") or {}
    content, cleaning = clean_embedding_content(str(chunk.get("content") or ""))
    values = {
        "document_family": base.get("document_family"),
        "article_number": specific.get("article_number"),
        "article_title": specific.get("article_title"),
        "content": content,
    }
    text = (
        f"document_family: {values['document_family']}\n"
        f"article_number: {values['article_number']}\n"
        f"article_title: {values['article_title']}\n\n"
        f"{values['content'] or ''}"
    )
    return text, values, cleaning


def validate(values: dict, chunk: dict, text: str) -> list[str]:
    issues = []
    for field, value in values.items():
        if value in (None, "", [], {}):
            issues.append(f"missing:{field}")
    content = str(values.get("content") or "").strip()
    if re.fullmatch(r"(?:page\s*)?\d{1,3}", content, flags=re.I):
        issues.append("standalone_page_number")
    if re.search(r"\b\d{1,3}\s*\|\s*\d{1,3}\b", content):
        issues.append("inline_page_marker")
    word_count = len(re.findall(r"\S+", content))
    if 0 < word_count < 10 and "article_number" not in values:
        issues.append("content_under_10_words")
    if word_count > 500:
        issues.append("content_over_500_words")
    if len(text) > 24_000:
        issues.append("embedding_input_over_24000_characters")
    for upstream in chunk.get("quality_issues") or []:
        if upstream == "article_very_short" and "article_number" in values:
            continue
        issues.append(f"upstream:{upstream}")
    return sorted(set(issues))


def render_html(records: list[dict], report: dict) -> str:
    css = """body{font:14px/1.45 system-ui;margin:24px;color:#172033;background:#f4f6f8}a{color:#185a9d}.hero,section,article{background:white;border:1px solid #d7dfe8;border-radius:12px;padding:18px;margin:14px 0}.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}.stat{background:#eef4ff;border-radius:9px;padding:12px}.stat strong{display:block;font-size:1.6rem}.ok{border-left:6px solid #2b9b55}.warn{border-left:6px solid #d89a19}.bad{border-left:6px solid #c83737}pre{white-space:pre-wrap;word-break:break-word;background:#101820;color:#eaf2f8;padding:14px;border-radius:8px;max-height:520px;overflow:auto}.tag{display:inline-block;padding:3px 8px;border-radius:999px;background:#e7edf5;margin:2px}summary{cursor:pointer;font-weight:650}table{border-collapse:collapse;width:100%}th,td{border:1px solid #d9dfeb;padding:8px;text-align:left}th{background:#edf1f7;position:sticky;top:0}code{font-family:Consolas,monospace}"""
    category_rows = "".join(
        f"<tr><td>{html.escape(category)}</td><td>{values['documents']}</td><td>{values['chunks']}</td><td>{values['valid']}</td><td>{values['review']}</td></tr>"
        for category, values in report["by_category"].items()
    )
    cards = []
    for record in records:
        issues = record["validation_issues"]
        css_class = "ok" if not issues else "warn"
        issue_html = "".join(f"<span class='tag'>{html.escape(issue)}</span>" for issue in issues) or "aucune"
        heading = record.get("title") or record.get("article_title") or record["chunk_id"]
        cards.append(
            f"<article id='{html.escape(record['chunk_id'])}' class='{css_class}'>"
            f"<h3>{html.escape(str(heading))}</h3>"
            f"<p><code>{html.escape(record['chunk_id'])}</code> · {html.escape(record['category'])} · {record['word_count']} mots</p>"
            f"<p>Contrôles : {issue_html}</p>"
            f"<details><summary>Voir l'embedding_input</summary><pre>{html.escape(record['embedding_input'])}</pre></details>"
            f"<details><summary>Voir les données structurées</summary><pre>{html.escape(json.dumps(record['embedding_fields'], ensure_ascii=False, indent=2))}</pre></details>"
            f"</article>"
        )
    issue_rows = "".join(
        f"<tr><td>{html.escape(issue)}</td><td>{count}</td></tr>" for issue, count in report["issues"].items()
    ) or "<tr><td>Aucune</td><td>0</td></tr>"
    skipped_rows = "".join(
        f"<tr><td>{html.escape(item['category'])}</td><td>{html.escape(item['title'])}</td><td><code>{html.escape(item['source_chunk_file'])}</code></td><td>{html.escape(item['reason'])}</td></tr>"
        for item in report["skipped_sources"]
    ) or "<tr><td colspan='4'>Aucune source ignorée</td></tr>"
    duplicate_rows = "".join(
        f"<tr><td><code>{html.escape(group['content_hash'][:12])}</code></td><td>{group['count']}</td><td>" +
        "<br>".join(f"{html.escape(item['category'])} — {html.escape(item['title'])} — <code>{html.escape(item['chunk_id'])}</code>" for item in group["chunks"]) +
        "</td></tr>"
        for group in report["duplicate_groups"]
    ) or "<tr><td colspan='3'>Aucun contenu dupliqué</td></tr>"
    exclusion_rows = "".join(
        f"<tr><td><code>{html.escape(item['document_id'])}</code></td><td>{html.escape(item['title'])}</td><td>{html.escape(item['reason'])}</td></tr>"
        for item in report["excluded_documents"]
    ) or "<tr><td colspan='3'>Aucun document exclu</td></tr>"
    return f"""<!doctype html><html lang='fr'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Contrôle des embedding_input</title><style>{css}</style></head><body>
<div class='hero'><p><a href='../index.html'>← Pilote</a></p><h1>Contrôle des {report['summary']['chunks']} embedding_input</h1><p>Aucun vecteur n'est présent : cette page montre uniquement les textes qui seraient envoyés à Mistral.</p>
<div class='stats'><div class='stat'><strong>{report['summary']['chunks']}</strong>chunks</div><div class='stat'><strong>{report['summary']['valid']}</strong>sans alerte</div><div class='stat'><strong>{report['summary']['review']}</strong>à contrôler</div><div class='stat'><strong>{report['summary']['duplicate_chunk_ids']}</strong>ID dupliqués</div><div class='stat'><strong>{report['summary']['duplicate_content_hashes']}</strong>contenus dupliqués</div><div class='stat'><strong>{report['cleaning']['inline_page_markers_removed']}</strong>marqueurs retirés</div></div></div>
<section><h2>Couverture</h2><table><thead><tr><th>Catégorie</th><th>Documents</th><th>Chunks</th><th>Valides</th><th>À contrôler</th></tr></thead><tbody>{category_rows}</tbody></table></section>
<section><h2>Alertes</h2><table><thead><tr><th>Alerte</th><th>Nombre</th></tr></thead><tbody>{issue_rows}</tbody></table></section>
<section><h2>Sources sans chunk</h2><table><thead><tr><th>Catégorie</th><th>Document</th><th>Fichier</th><th>Raison</th></tr></thead><tbody>{skipped_rows}</tbody></table></section>
<section><h2>Groupes de contenus identiques</h2><p>Ces groupes doivent être examinés avant l'indexation. Un contenu identique peut signaler un doublon source ou un petit en-tête commun.</p><table><thead><tr><th>Hash</th><th>Occurrences</th><th>Chunks concernés</th></tr></thead><tbody>{duplicate_rows}</tbody></table></section>
<section><h2>Documents exclus du pilote</h2><table><thead><tr><th>Document ID</th><th>Titre</th><th>Raison</th></tr></thead><tbody>{exclusion_rows}</tbody></table></section>
<section><h2>Tous les inputs</h2><p>Utiliser la recherche du navigateur pour retrouver un titre, un article ou une alerte.</p>{''.join(cards)}</section>
</body></html>"""


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    source_config = read_json(CONFIG / "sources.json")
    exclusion_config = read_json(CONFIG / "exclusions.json")
    exclusions = {item["document_id"]: item for item in exclusion_config.get("documents", [])}
    records = []
    skipped_sources = []
    excluded_documents = []
    document_ids_by_category: dict[str, set[str]] = defaultdict(set)

    for source in source_config["sources"]:
        family = source["family"]
        category = source["category"]
        metadata = metadata_index(source["metadata_glob"])
        regulation_meta = resolve_regulation_base(metadata) if family == "regulation" else None
        for chunk_path in sorted(ROOT.glob(source["glob"])):
            chunks = read_json(chunk_path)
            if not chunks:
                document_id = chunk_path.stem
                meta_entry = metadata.get(document_id)
                title = (meta_entry or {}).get("base", {}).get("title", document_id)
                processing = read_json(meta_entry["path"]).get("processing", {}) if meta_entry else {}
                selected = processing.get("selected_text") or {}
                skipped_sources.append({
                    "category": category,
                    "document_id": document_id,
                    "title": title,
                    "source_chunk_file": str(chunk_path.relative_to(ROOT)).replace("\\", "/"),
                    "reason": selected.get("method") or "empty_chunk_file",
                })
                continue
            for chunk in chunks:
                if family == "regulation":
                    meta_entry = regulation_meta
                    base = meta_entry["base"]
                    document_id = base["document_id"]
                    chunk_id = chunk.get("chunk_id") or f"{document_id}#chunk-{chunk['chunk_index']:03d}"
                    embedding_input, fields, cleaning = regulation_input(base, chunk)
                    title = base.get("title")
                    article_title = fields.get("article_title")
                else:
                    document_id = chunk.get("document_id")
                    if document_id in exclusions:
                        if not any(item["document_id"] == document_id for item in excluded_documents):
                            excluded_documents.append(exclusions[document_id])
                        continue
                    meta_entry = metadata.get(document_id)
                    if not meta_entry:
                        raise ValueError(f"Missing metadata for {document_id} from {chunk_path}")
                    base = meta_entry["base"]
                    chunk_id = chunk.get("chunk_id")
                    embedding_input, fields, cleaning = political_input(base, chunk)
                    title = base.get("title")
                    article_title = None
                content = str(fields.get("content") or "")
                issues = validate(fields, chunk, embedding_input)
                record = {
                    "chunk_id": chunk_id,
                    "document_id": document_id,
                    "document_family": base.get("document_family"),
                    "category": category,
                    "document_role": base.get("document_role"),
                    "title": title,
                    "article_title": article_title,
                    "component": fields.get("component"),
                    "response_number": chunk.get("response_number"),
                    "chunk_index": chunk.get("chunk_index"),
                    "content": content,
                    "content_hash": chunk.get("chunk_hash") or normalized_hash(content),
                    "word_count": len(re.findall(r"\S+", content)),
                    "embedding_recipe": "political_object" if family == "political_object" else "regulation",
                    "embedding_fields": fields,
                    "embedding_input": embedding_input,
                    "validation_issues": issues,
                    "cleaning": cleaning,
                    "source_chunk_file": str(chunk_path.relative_to(ROOT)).replace("\\", "/"),
                    "source_metadata_file": str(meta_entry["path"].relative_to(ROOT)).replace("\\", "/"),
                }
                records.append(record)
                document_ids_by_category[category].add(document_id)

    id_counts = Counter(record["chunk_id"] for record in records)
    hash_counts = Counter(record["content_hash"] for record in records)
    for record in records:
        if id_counts[record["chunk_id"]] > 1:
            record["validation_issues"] = sorted(set(record["validation_issues"] + ["duplicate_chunk_id"]))
        if hash_counts[record["content_hash"]] > 1:
            record["validation_issues"] = sorted(set(record["validation_issues"] + ["duplicate_content_hash"]))

    by_category = {}
    for category in [source["category"] for source in source_config["sources"]]:
        selected = [record for record in records if record["category"] == category]
        by_category[category] = {
            "documents": len(document_ids_by_category[category]),
            "chunks": len(selected),
            "valid": sum(not record["validation_issues"] for record in selected),
            "review": sum(bool(record["validation_issues"]) for record in selected),
        }
    issue_counts = Counter(issue for record in records for issue in record["validation_issues"])
    duplicate_groups = []
    for content_hash, count in hash_counts.items():
        if count <= 1:
            continue
        matching = [record for record in records if record["content_hash"] == content_hash]
        duplicate_groups.append({
            "content_hash": content_hash,
            "count": count,
            "chunks": [
                {"chunk_id": item["chunk_id"], "category": item["category"], "title": item["title"]}
                for item in matching
            ],
        })
    report = {
        "status": "inputs_generated_no_embeddings",
        "model_planned": "mistral-embed",
        "dimension_planned": 1024,
        "recipe_version": "embedding-input-v1",
        "summary": {
            "chunks": len(records),
            "valid": sum(not record["validation_issues"] for record in records),
            "review": sum(bool(record["validation_issues"]) for record in records),
            "duplicate_chunk_ids": sum(count - 1 for count in id_counts.values() if count > 1),
            "duplicate_content_hashes": sum(count - 1 for count in hash_counts.values() if count > 1),
        },
        "by_category": by_category,
        "issues": dict(sorted(issue_counts.items())),
        "duplicate_groups": duplicate_groups,
        "skipped_sources": skipped_sources,
        "excluded_documents": excluded_documents,
        "cleaning": {
            "inline_page_markers_removed": sum(record["cleaning"]["inline_page_markers_removed"] for record in records)
        },
        "review_chunks": [
            {"chunk_id": record["chunk_id"], "category": record["category"], "title": record["title"], "issues": record["validation_issues"]}
            for record in records if record["validation_issues"]
        ],
    }

    jsonl_path = OUTPUT / "embedding_inputs.jsonl"
    with jsonl_path.open("w", encoding="utf-8", newline="\n") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    write_json(OUTPUT / "validation_report.json", report)
    (OUTPUT / "embedding_inputs.html").write_text(render_html(records, report), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(json.dumps(report["issues"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
