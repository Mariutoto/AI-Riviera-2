from __future__ import annotations

import base64
import json
import os
from pathlib import Path

import requests


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parents[1]
OCR_DIR = ROOT / "full-audit" / "ocr_overrides"
TARGETS = ["Postulat-Kaiser-Accueil_de_jour-Rap-Dec.pdf"]


def load_local_env() -> None:
    path = PROJECT_ROOT / "embedding-pilot" / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main() -> None:
    load_local_env()
    api_key = os.getenv("MISTRAL_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("MISTRAL_API_KEY missing")
    OCR_DIR.mkdir(parents=True, exist_ok=True)
    report = []
    for filename in TARGETS:
        stem = Path(filename).stem
        pdf_path = ROOT / "pilot" / "artifacts" / stem / "document.pdf"
        raw_path = OCR_DIR / f"{stem}.json"
        if raw_path.exists():
            data = json.loads(raw_path.read_text(encoding="utf-8"))
        else:
            encoded = base64.b64encode(pdf_path.read_bytes()).decode("ascii")
            response = requests.post(
                "https://api.mistral.ai/v1/ocr",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": "mistral-ocr-latest",
                    "document": {"type": "document_url", "document_url": f"data:application/pdf;base64,{encoded}"},
                    "table_format": "markdown", "extract_header": True, "extract_footer": True,
                    "confidence_scores_granularity": "page",
                },
                timeout=600,
            )
            response.raise_for_status()
            data = response.json()
            raw_path.write_text(json.dumps(data, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
        text = "\n\n".join(page.get("markdown", "") for page in data.get("pages", [])).strip()
        (OCR_DIR / f"{stem}.md").write_text(text+"\n", encoding="utf-8")
        scores = [(p.get("confidence_scores") or {}).get("average_page_confidence_score") for p in data.get("pages", [])]
        scores = [float(x) for x in scores if x is not None]
        report.append({"filename": filename, "pages": len(data.get("pages", [])), "ocr_words": len(text.split()), "ocr_average_confidence": round(sum(scores)/len(scores), 4) if scores else None})
    (OCR_DIR / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
