from __future__ import annotations

import hashlib
import html
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent
CHUNKS_DIR = ROOT / "chunks"
DETAIL_DIR = ROOT / "chunk_details"
MAX_WORDS = 450
OVERLAP_WORDS = 60
INITIAL_PREAMBLE_MAX_WORDS = 60


def words(text: str) -> list[str]:
    return re.findall(r"\S+", text)


def chunk_hash(text: str) -> str:
    return hashlib.sha256(re.sub(r"\s+", " ", text).strip().encode("utf-8")).hexdigest()


def merge_initial_preamble(sections: list[dict]) -> list[dict]:
    """Keep a short author/date preamble with the first substantive section."""
    if (
        len(sections) > 1
        and len(words(sections[0]["content"])) < INITIAL_PREAMBLE_MAX_WORDS
        and sections[0]["component"] == sections[1]["component"]
    ):
        sections[1]["content"] = (
            f"{sections[0]['content']}\n{sections[1]['content']}"
        ).strip()
        sections = sections[1:]
    return sections


def split_sections(text: str, document_role: str | None = None) -> list[dict]:
    # Some combined PDFs put the municipal response first (including its
    # annexes), then append the original interpellation at the end. Detect the
    # explicit appended-document heading instead of assuming physical order.
    appended_interpellation = re.search(
        r"(?im)^\s*(?:\#\s*)?(?:\*\*)?Interpellation(?:\*\*)?"
        r"(?:\s+de\s+.+?\s+au\s+Conseil\s+communal\s+du\s+"
        r"\d{1,2}(?:er)?\s+\w+\s+20\d{2}|\s*:).*$",
        text,
    )
    starts_with_response = bool(
        re.search(r"(?i)r[ée]ponse\s+[àa]\s+l[’']interpellation", text[:1200])
    )
    if appended_interpellation and starts_with_response:
        response_text = text[:appended_interpellation.start()].strip()
        interpellation_text = text[appended_interpellation.start():].strip()
        number_match = re.search(
            r"(?i)R[ÉE]PONSE\s+MUNICIPALE\s+N[°ºO]\s*(\d+(?:bis)?/\d{4})",
            response_text,
        )
        number = number_match.group(1) if number_match else None
        return merge_initial_preamble([
            {
                "component": "municipal_response",
                "section_title": f"Réponse municipale {number}" if number else "Réponse municipale",
                "response_number": number,
                "content": response_text,
            },
            {
                "component": "interpellation_text",
                "section_title": "Interpellation annexée",
                "response_number": None,
                "content": interpellation_text,
            },
        ])

    # A response can be numbered ("N° 3/2025") or explicitly labelled
    # "ORALE". Both headings are hard semantic boundaries for chunking.
    pattern = re.compile(
        r"(?im)^\s*#?\s*R[ÉE]PONSE\s+MUNICIPALE\s+"
        r"(?:(?:N[°ºO]\s*)?(\d+(?:bis)?/\d{4})|ORALE)\b.*$"
    )
    matches = list(pattern.finditer(text))
    if not matches:
        fallback = re.search(r"(?im)^.*La\s+Municipalit[ée]\s+r[ée]pond.*$", text)
        if fallback:
            matches = [fallback]
    sections = []
    if matches and matches[0].start() > 0:
        sections.append({"component": "interpellation_text", "section_title": "Interpellation", "response_number": None, "content": text[:matches[0].start()].strip()})
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        number = match.group(1) if match.lastindex else None
        is_oral = bool(re.search(r"\bORALE\b", match.group(0), flags=re.I))
        section_title = (
            f"Réponse municipale {number}"
            if number
            else "Réponse municipale orale"
            if is_oral
            else "Réponse municipale"
        )
        sections.append({
            "component": "municipal_response",
            "section_title": section_title,
            "response_number": number,
            "content": text[match.start():end].strip(),
        })
    if not matches:
        if document_role == "interpellation_text":
            component, section_title = "interpellation_text", "Interpellation"
        elif document_role == "municipal_response":
            component, section_title = "municipal_response", "Réponse municipale"
        else:
            component, section_title = "unknown_component", "Document"
        sections.append({"component": component, "section_title": section_title, "response_number": None, "content": text.strip()})
    return merge_initial_preamble(
        [section for section in sections if section["content"]]
    )


def split_words(text: str) -> list[str]:
    tokens = words(text)
    if not tokens:
        return []
    chunks = []
    start = 0
    while start < len(tokens):
        end = min(len(tokens), start + MAX_WORDS)
        chunks.append(" ".join(tokens[start:end]))
        if end == len(tokens):
            break
        start = end - OVERLAP_WORDS
    return chunks


def quality(chunk: dict, is_last: bool) -> tuple[str, list[str]]:
    issues = []
    count = chunk["word_count"]
    if count > MAX_WORDS:
        issues.append("chunk_too_long")
    if count < 60 and not is_last:
        issues.append("chunk_too_short")
    if chunk["component"] == "unknown_component":
        issues.append("component_not_detected")
    if not chunk["section_title"]:
        issues.append("section_title_missing")
    return ("red" if "chunk_too_long" in issues else "yellow" if issues else "green"), issues


def build_document_chunks(record: dict, text: str) -> list[dict]:
    base = record["document_metadata"]
    output = []
    for section_index, section in enumerate(split_sections(text, base.get("document_role"))):
        pieces = split_words(section["content"])
        for piece_index, content in enumerate(pieces):
            index = len(output)
            embedding_input = (
                f"Famille: {base['document_family']}\nCatégorie: {base['category']}\n"
                f"Rôle: {base['document_role']}\nTitre: {base['title']}\n"
                f"Section: {section['section_title']}\n\n{content}"
            )
            chunk = {
                "chunk_id": f"{base['document_id']}#chunk-{index:03d}",
                "document_id": base["document_id"],
                "chunk_index": index,
                "section_index": section_index,
                "component": section["component"],
                "section_title": section["section_title"],
                "response_number": section["response_number"],
                "content": content,
                "word_count": len(words(content)),
                "chunk_hash": chunk_hash(content),
                "embedding_input": embedding_input,
            }
            color, issues = quality(chunk, piece_index == len(pieces) - 1)
            chunk["quality"] = color
            chunk["quality_issues"] = issues
            output.append(chunk)
    return output


def detail_page(record: dict, chunks: list[dict]) -> str:
    base = record["document_metadata"]
    cards = []
    for chunk in chunks:
        cards.append(
            f"<article class='{chunk['quality']}'><h2>{html.escape(chunk['chunk_id'])}</h2>"
            f"<p><strong>{html.escape(chunk['component'])}</strong> · {html.escape(chunk['section_title'])} · {chunk['word_count']} mots</p>"
            f"<p>Alertes : {html.escape(', '.join(chunk['quality_issues']) or 'aucune')}</p>"
            f"<details><summary>Contenu du chunk</summary><pre>{html.escape(chunk['content'])}</pre></details>"
            f"<details><summary>Entrée prévue pour l’embedding</summary><pre>{html.escape(chunk['embedding_input'])}</pre></details></article>"
        )
    return f"""<!doctype html><html lang='fr'><head><meta charset='utf-8'><title>Chunks — {html.escape(base['title'])}</title>
<style>body{{font:14px/1.45 system-ui;margin:24px;color:#172033}}article{{border:2px solid;padding:14px;margin:14px 0;border-radius:10px}}article.green{{border-color:#58a66d;background:#effaf2}}article.yellow{{border-color:#d8a928;background:#fff8d8}}article.red{{border-color:#d44;background:#ffe5e5}}pre{{white-space:pre-wrap;word-break:break-word;background:white;padding:10px;max-height:500px;overflow:auto}}summary{{cursor:pointer;font-weight:650}}</style></head><body><p><a href='../audit.html'>← Audit principal</a> · <a href='../chunks_audit.html'>Audit global des chunks</a></p><h1>{html.escape(base['title'])}</h1><p>{len(chunks)} chunks · limite {MAX_WORDS} mots · chevauchement {OVERLAP_WORDS} mots.</p>{''.join(cards)}</body></html>"""


def main() -> None:
    CHUNKS_DIR.mkdir(parents=True, exist_ok=True)
    DETAIL_DIR.mkdir(parents=True, exist_ok=True)
    summaries = []
    for metadata_path in sorted((ROOT / "metadata").glob("*.json")):
        record = json.loads(metadata_path.read_text(encoding="utf-8"))
        document_id = record["document_metadata"]["document_id"]
        text = (ROOT / "clean_text" / f"{document_id}.txt").read_text(encoding="utf-8")
        chunks = build_document_chunks(record, text)
        (CHUNKS_DIR / f"{document_id}.json").write_text(json.dumps(chunks, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (DETAIL_DIR / f"{document_id}.html").write_text(detail_page(record, chunks), encoding="utf-8")
        counts = {color: sum(chunk["quality"] == color for chunk in chunks) for color in ("green", "yellow", "red")}
        summaries.append({
            "document_id": document_id, "title": record["document_metadata"]["title"],
            "chunks": len(chunks), **counts,
        })
    rows = "".join(
        f"<tr class='{'red' if x['red'] else 'yellow' if x['yellow'] else 'green'}'><td><a href='chunk_details/{x['document_id']}.html'>{html.escape(x['title'])}</a></td><td>{x['chunks']}</td><td>{x['green']}</td><td>{x['yellow']}</td><td>{x['red']}</td></tr>"
        for x in summaries
    )
    page = f"""<!doctype html><html lang='fr'><head><meta charset='utf-8'><title>Audit des chunks</title><style>body{{font:14px/1.45 system-ui;margin:24px;color:#172033}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #d9dfeb;padding:8px}}th{{background:#edf1f7}}tr.green{{background:#e1f5e6}}tr.yellow{{background:#fff3bf}}tr.red{{background:#ffd8d8}}</style></head><body><p><a href='audit.html'>← Audit principal</a></p><h1>Audit structurel des chunks</h1><p>Vert : taille et section correctes. Jaune : chunk court ou composant non détecté. Rouge : chunk trop long. La qualité sémantique réelle devra ensuite être testée avec des questions de recherche.</p><table><thead><tr><th>Document</th><th>Chunks</th><th>Verts</th><th>Jaunes</th><th>Rouges</th></tr></thead><tbody>{rows}</tbody></table></body></html>"""
    (ROOT / "chunks_audit.html").write_text(page, encoding="utf-8")
    (ROOT / "chunks_summary.json").write_text(json.dumps(summaries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    import build_full_audit
    audit_data = json.loads((ROOT / "audit.json").read_text(encoding="utf-8"))
    build_full_audit.build_html(audit_data["documents"], audit_data["failures"])
    print(json.dumps({"documents": len(summaries), "chunks": sum(x["chunks"] for x in summaries), "yellow": sum(x["yellow"] for x in summaries), "red": sum(x["red"] for x in summaries)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
