from __future__ import annotations

import base64
import json
import re
import sys
from pathlib import Path

import requests


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import config_value


DOCUMENT_ID = "vevey_interpellation_cf6bd00e646d4f9a5413"
PDF_PATH = ROOT / "pdfs" / f"{DOCUMENT_ID}.pdf"
OCR_DIR = ROOT / "ocr_overrides"
RAW_PATH = OCR_DIR / f"{DOCUMENT_ID}.json"
TEXT_PATH = OCR_DIR / f"{DOCUMENT_ID}.md"
REPORT_PATH = OCR_DIR / "report.json"
SCANNED_PAGE_INDICES = {5, 6, 7}


def words(value: str) -> int:
    return len(re.findall(r"\S+", value))


def main() -> None:
    api_key = config_value("MISTRAL_API_KEY").strip()
    if not api_key:
        raise SystemExit("MISTRAL_API_KEY missing")
    if not PDF_PATH.exists():
        raise SystemExit(f"PDF missing: {PDF_PATH}")
    OCR_DIR.mkdir(parents=True, exist_ok=True)

    if RAW_PATH.exists():
        data = json.loads(RAW_PATH.read_text(encoding="utf-8"))
    else:
        encoded = base64.b64encode(PDF_PATH.read_bytes()).decode("ascii")
        response = requests.post(
            "https://api.mistral.ai/v1/ocr",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "mistral-ocr-latest",
                "document": {
                    "type": "document_url",
                    "document_url": (
                        "data:application/pdf;base64," + encoded
                    ),
                },
                "table_format": "markdown",
                "extract_header": True,
                "extract_footer": True,
                "confidence_scores_granularity": "page",
            },
            timeout=600,
        )
        response.raise_for_status()
        data = response.json()
        RAW_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    selected_pages = [
        page
        for index, page in enumerate(data.get("pages", []))
        if index in SCANNED_PAGE_INDICES
    ]
    text = "\n\n".join(
        str(page.get("markdown") or "").strip()
        for page in selected_pages
        if str(page.get("markdown") or "").strip()
    ).strip()
    if words(text) < 100:
        raise SystemExit("Targeted OCR returned too little text")
    TEXT_PATH.write_text(text + "\n", encoding="utf-8")
    confidences = [
        (page.get("confidence_scores") or {}).get(
            "average_page_confidence_score"
        )
        for page in selected_pages
    ]
    confidence_values = [
        float(value) for value in confidences if value is not None
    ]
    report = {
        "document_id": DOCUMENT_ID,
        "source_pages": [index + 1 for index in sorted(SCANNED_PAGE_INDICES)],
        "ocr_pages": len(selected_pages),
        "ocr_words": words(text),
        "ocr_characters": len(text),
        "ocr_average_confidence": (
            round(
                sum(confidence_values) / len(confidence_values),
                4,
            )
            if confidence_values
            else None
        ),
        "selected_text_path": str(TEXT_PATH.relative_to(ROOT)),
        "contains_expected_title": bool(
            re.search(
                r"un point de la situation actuelle et future",
                text,
                re.I,
            )
        ),
        "contains_expected_author": bool(
            re.search(r"Patrick\s+Bertschy", text, re.I)
        ),
    }
    REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
