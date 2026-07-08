from __future__ import annotations

import base64
import json
import os
from pathlib import Path

import requests
from dotenv import load_dotenv

import build_full_audit


ROOT = Path(__file__).resolve().parent
OCR_DIR = ROOT / "ocr_overrides"
TARGETS = [
    ("2021", "Interpellation-PLR-Costa-Cybersecurite-Rep-Rep-Bis.pdf"),
    ("2021", "Interpellation-Schmidhauser-Chateau-Rep.pdf"),
    ("2021", "Interpellation-Schmidhauser-Faraz-Parcelle-928-Rep.pdf"),
    ("2021", "Interpellation-Schmidhauser-Wuthrich-Mobbing-Rep.pdf"),
    ("2023", "Interpellation-Grutta-Stationnement-Rep.pdf"),
    ("2023", "Interpellation-Holzeisen-VMCV-pilote-Rep.pdf"),
    ("2023", "Interpellation-JYSchmidhauser-Refectoires-Rep.pdf"),
    ("2023", "Interpellation-Luceron-Installations-plage-Maladaire-Rep.pdf"),
    ("2023", "Interpellation-Menetrey-Avenue-gare-Demarche-participative-Rep.pdf"),
    ("2024", "Interpellation-Holzeisen-Site_internet-Rep.pdf"),
    ("2026", "Interpellation-Ansermet-Communaute-electrique-locale-Rep.pdf"),
]


def main() -> None:
    load_dotenv(ROOT.parents[1] / "embedding-pilot" / ".env")
    api_key = os.getenv("MISTRAL_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("MISTRAL_API_KEY missing")
    OCR_DIR.mkdir(parents=True, exist_ok=True)
    report = []
    for year, filename in TARGETS:
        print(f"OCR: {filename}")
        pdf_path = ROOT / "pdfs" / year / filename
        raw_path = OCR_DIR / f"{Path(filename).stem}.json"
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
                    "table_format": "markdown",
                    "extract_header": True,
                    "extract_footer": True,
                    "confidence_scores_granularity": "page",
                },
                timeout=600,
            )
            response.raise_for_status()
            data = response.json()
            raw_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        text = "\n\n".join(page.get("markdown", "") for page in data.get("pages", [])).strip()
        text_path = OCR_DIR / f"{Path(filename).stem}.md"
        text_path.write_text(text + "\n", encoding="utf-8")
        native = build_full_audit.clean_pdf(pdf_path, "")["clean_text"]
        confidence = [
            (page.get("confidence_scores") or {}).get("average_page_confidence_score")
            for page in data.get("pages", [])
        ]
        confidence = [float(value) for value in confidence if value is not None]
        report.append({
            "filename": filename,
            "native_words": build_full_audit.words(native),
            "ocr_words": build_full_audit.words(text),
            "ocr_average_confidence": round(sum(confidence) / len(confidence), 4) if confidence else None,
            "native_dates": build_full_audit.extract_document_dates(native),
            "ocr_dates": build_full_audit.extract_document_dates(text),
            "ocr_text_path": str(text_path.relative_to(ROOT)),
        })
    (OCR_DIR / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
