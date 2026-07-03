from __future__ import annotations

import json
import math
import re
import unicodedata
from collections import Counter
from pathlib import Path

import fitz


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "cleaning_test"


def normalize(value: str) -> str:
    value = "".join(c for c in unicodedata.normalize("NFD", value.lower()) if unicodedata.category(c) != "Mn")
    value = re.sub(r"\d+", "#", value)
    return re.sub(r"\s+", " ", value).strip(" -|_")


def word_count(value: str) -> int:
    return len(re.findall(r"\b\w+[\w'-]*\b", value, flags=re.UNICODE))


def artifact_pdf(record: dict) -> Path:
    return ROOT / "artifacts" / Path(record["file_url"]).stem / "document.pdf"


def page_blocks(pdf_path: Path) -> list[list[dict]]:
    pages = []
    with fitz.open(pdf_path) as pdf:
        for page_index, page in enumerate(pdf):
            height = page.rect.height
            blocks = []
            for block in page.get_text("blocks"):
                x0, y0, x1, y1, text, *_ = block
                text = re.sub(r"[ \t]+", " ", text).strip()
                if not text:
                    continue
                blocks.append({
                    "page": page_index + 1, "x0": round(x0, 1), "y0": round(y0, 1),
                    "x1": round(x1, 1), "y1": round(y1, 1), "page_height": round(height, 1),
                    "text": text, "normalized": normalize(text),
                })
            pages.append(blocks)
    return pages


def extract_header_metadata(text: str) -> dict:
    metadata = {}
    number = re.search(r"R[ÉE]PONSE\s+MUNICIPALE\s+N[°ºO]\s*(\d+/\d{4})", text, flags=re.I)
    if number:
        metadata["response_number"] = number.group(1)
    date = re.search(r"\ble\s+(\d{1,2})\s+(janvier|février|mars|avril|mai|juin|juillet|août|septembre|octobre|novembre|décembre)\s+(20\d{2})", text, flags=re.I)
    if date:
        metadata["response_date_label"] = date.group(0)
    response_to = re.search(r"R[ée]ponse\s+[àa]\s+l['’]interpellation\s+(.+)", text, flags=re.I)
    if response_to:
        metadata["response_to_label"] = re.sub(r"\s+", " ", response_to.group(1)).strip()
    return metadata


def clean_document(record: dict) -> dict:
    pages = page_blocks(artifact_pdf(record))
    position_candidates = []
    for blocks in pages:
        for block in blocks:
            height = block["page_height"]
            if block["y1"] <= height * 0.14 or block["y0"] >= height * 0.86:
                position_candidates.append(block["normalized"])
    counts = Counter(value for value in position_candidates if value)
    repetition_threshold = max(2, math.ceil(len(pages) * 0.5))
    repeated = {value for value, count in counts.items() if count >= repetition_threshold}

    removed = []
    kept_pages = []
    extracted_metadata = {}
    title_normalized = normalize(record["title"])

    for page_number, blocks in enumerate(pages, 1):
        kept = []
        for block in blocks:
            text = block["text"]
            normalized = block["normalized"]
            height = block["page_height"]
            reason = None
            if normalized in repeated:
                reason = "repeated_header_or_footer"
            elif re.search(r"\b\d{2,3}\s+\d{3}\s+\d{2}\s+\d{2}\b|@|www\.", text, flags=re.I) and (block["y1"] <= height * 0.2 or block["y0"] >= height * 0.8):
                reason = "contact_boilerplate"
            elif re.search(r"\.docx\b", text, flags=re.I):
                reason = "internal_filename"
            elif re.fullmatch(r"\s*(?:page\s*)?\d+\s*(?:/|sur|\|)\s*\d+\s*", text, flags=re.I):
                reason = "page_number"
            elif page_number == 1 and block["y1"] <= height * 0.38 and re.search(r"R[ÉE]PONSE\s+MUNICIPALE\s+N[°ºO]", text, flags=re.I):
                reason = "semantic_response_header"
            elif page_number == 1 and block["y1"] <= height * 0.38 and title_normalized and (title_normalized in normalized or normalized in title_normalized) and len(normalized) > 25:
                reason = "document_title_already_in_metadata"

            if reason:
                removed.append({key: value for key, value in block.items() if key != "normalized"} | {"reason": reason})
                extracted_metadata.update(extract_header_metadata(text))
            else:
                kept.append(text)
        kept_pages.append("\n".join(kept).strip())

    raw_text = "\n\n".join("\n".join(block["text"] for block in blocks) for blocks in pages).strip()
    clean_text = "\n\n".join(text for text in kept_pages if text).strip()
    return {
        "document_id": record["document_id"],
        "raw_words": word_count(raw_text), "clean_words": word_count(clean_text),
        "removed_word_count": word_count(raw_text) - word_count(clean_text),
        "removed_blocks_count": len(removed), "removed_blocks": removed,
        "extracted_header_metadata": extracted_metadata,
        "clean_text": clean_text,
    }


def main() -> None:
    records = [json.loads(path.read_text(encoding="utf-8")) for path in sorted((ROOT / "document_metadata").glob("*.json"))]
    report = []
    for record in records:
        result = clean_document(record)
        directory = OUTPUT / record["document_id"]
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "clean.txt").write_text(result.pop("clean_text") + "\n", encoding="utf-8")
        (directory / "removed_blocks.json").write_text(json.dumps(result["removed_blocks"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        report.append(result)
    (OUTPUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"documents": len(report), "removed_blocks": sum(item["removed_blocks_count"] for item in report), "removed_words": sum(item["removed_word_count"] for item in report)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
