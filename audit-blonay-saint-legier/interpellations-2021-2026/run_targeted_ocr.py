from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import requests


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parents[1]
INVENTORY_PATH = ROOT / "inventory.json"
OCR_DIR = ROOT / "general-audit" / "ocr_overrides"


def main() -> None:
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    from app.config import config_value

    api_key = config_value("MISTRAL_API_KEY").strip()
    if not api_key:
        raise SystemExit("MISTRAL_API_KEY missing")
    inventory = json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))
    targets = [d for d in inventory["documents"] if d["text_audit"]["needs_ocr"]]
    OCR_DIR.mkdir(parents=True, exist_ok=True)
    report = []
    for index, document in enumerate(targets, 1):
        document_id = document["document_id"]
        raw_path = OCR_DIR / f"{document_id}.json"
        markdown_path = OCR_DIR / f"{document_id}.md"
        if raw_path.exists():
            payload = json.loads(raw_path.read_text(encoding="utf-8"))
        else:
            encoded = base64.b64encode(
                (PROJECT_ROOT / document["local_pdf"]).read_bytes()
            ).decode("ascii")
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
                        "document_url": f"data:application/pdf;base64,{encoded}",
                    },
                    "table_format": "markdown",
                    "extract_header": True,
                    "extract_footer": True,
                    "confidence_scores_granularity": "page",
                },
                timeout=600,
            )
            response.raise_for_status()
            payload = response.json()
            raw_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        text = "\n\n".join(
            str(page.get("markdown") or "") for page in payload.get("pages", [])
        ).strip()
        markdown_path.write_text(text + "\n", encoding="utf-8")
        scores = [
            (page.get("confidence_scores") or {}).get("average_page_confidence_score")
            for page in payload.get("pages", [])
        ]
        numeric = [float(score) for score in scores if score is not None]
        report.append(
            {
                "document_id": document_id,
                "pages": len(payload.get("pages", [])),
                "ocr_words": len(text.split()),
                "textual_content": len(text) >= 120,
                "ocr_average_confidence": (
                    round(sum(numeric) / len(numeric), 4) if numeric else None
                ),
            }
        )
        print(f"OCR {index}/{len(targets)}: {document_id}")
    (OCR_DIR / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
