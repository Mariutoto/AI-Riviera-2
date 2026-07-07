from __future__ import annotations

import base64
import argparse
import json
import os
import time
from pathlib import Path

import fitz
import requests


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parents[1]
OCR_DIR = ROOT / "pilot-10" / "ocr_overrides"
FILENAME = "Preavis_Compl-Preavis_01_2021-approb-plans-reponses-oppositions-Rives-lac-rapp-dec.pdf"
BATCH_PAGES = 5


def load_env() -> None:
    for path in (PROJECT_ROOT / "embedding-pilot" / ".env", PROJECT_ROOT / "ocr-extraction-test" / ".env"):
        if not path.exists():
            continue
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--filename", default=FILENAME)
    args = parser.parse_args()
    filename = args.filename
    load_env()
    api_key = os.getenv("MISTRAL_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("MISTRAL_API_KEY missing")
    matches = list((PROJECT_ROOT / "documents" / "la-tour-de-peilz").rglob(filename))
    matches += list(ROOT.rglob(filename))
    if not matches:
        raise SystemExit(f"PDF missing: {filename}")
    pdf_path = matches[0]
    OCR_DIR.mkdir(parents=True, exist_ok=True)
    raw_path = OCR_DIR / f"{pdf_path.stem}.json"
    batch_dir = OCR_DIR / f"{pdf_path.stem}_batches"
    batch_dir.mkdir(exist_ok=True)
    all_pages = []
    with fitz.open(pdf_path) as source_pdf:
        page_count = len(source_pdf)
        for start in range(0, page_count, BATCH_PAGES):
            end = min(page_count, start + BATCH_PAGES)
            batch_path = batch_dir / f"pages-{start + 1:03d}-{end:03d}.json"
            if batch_path.exists():
                batch_data = json.loads(batch_path.read_text(encoding="utf-8"))
            else:
                batch_pdf = fitz.open()
                batch_pdf.insert_pdf(source_pdf, from_page=start, to_page=end - 1)
                encoded = base64.b64encode(batch_pdf.tobytes(garbage=4, deflate=True)).decode("ascii")
                batch_pdf.close()
                for attempt in range(5):
                    response = requests.post(
                        "https://api.mistral.ai/v1/ocr",
                        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                        json={
                            "model": "mistral-ocr-latest",
                            "document": {"type": "document_url", "document_url": f"data:application/pdf;base64,{encoded}"},
                            "table_format": "markdown",
                            "extract_header": True,
                            "extract_footer": True,
                            "confidence_scores_granularity": "page",
                        },
                        timeout=600,
                    )
                    if response.ok:
                        break
                    if response.status_code not in {429, 500, 502, 503, 504} or attempt == 4:
                        raise RuntimeError(f"Mistral OCR {response.status_code}: {response.text[:500]}")
                    time.sleep(2 ** attempt)
                batch_data = response.json()
                batch_path.write_text(json.dumps(batch_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                print(f"OCR pages {start + 1}-{end}", flush=True)
            for offset, page in enumerate(batch_data.get("pages", [])):
                page["index"] = start + offset
                all_pages.append(page)
    data = {"model": "mistral-ocr-latest", "pages": all_pages}
    raw_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    pages = data.get("pages", [])
    expanded_pages = []
    for page in pages:
        markdown = page.get("markdown", "")
        for table in page.get("tables", []):
            table_id = table.get("id", "")
            if table_id:
                markdown = markdown.replace(f"[{table_id}]({table_id})", table.get("content", ""))
        expanded_pages.append(markdown)
    text = "\n\n\f\n\n".join(expanded_pages).strip()
    (OCR_DIR / f"{pdf_path.stem}.md").write_text(text + "\n", encoding="utf-8")
    scores = [
        (page.get("confidence_scores") or {}).get("average_page_confidence_score")
        for page in pages
    ]
    scores = [float(score) for score in scores if score is not None]
    with fitz.open(pdf_path) as pdf:
        native_text = "\n\n".join(page.get_text("text") for page in pdf)
    report = {
        "filename": filename,
        "model": data.get("model", "mistral-ocr-latest"),
        "pages": len(pages),
        "native_words": len(native_text.split()),
        "ocr_words": len(text.split()),
        "ocr_average_confidence": round(sum(scores) / len(scores), 4) if scores else None,
        "raw_response": str(raw_path),
    }
    (OCR_DIR / f"{pdf_path.stem}-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
