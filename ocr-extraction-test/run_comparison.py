from __future__ import annotations

import base64
import json
import os
import re
from difflib import SequenceMatcher
from pathlib import Path

import fitz
import requests
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parent
DOCUMENTS_DIR = ROOT / "documents"
RESULTS_DIR = ROOT / "results"
MISTRAL_OCR_URL = "https://api.mistral.ai/v1/ocr"
MODEL = "mistral-ocr-latest"


def load_documents() -> list[dict]:
    return json.loads((ROOT / "documents.json").read_text(encoding="utf-8"))


def download_pdf(document: dict) -> Path:
    path = DOCUMENTS_DIR / f"{document['id']}.pdf"
    if path.exists() and path.stat().st_size > 0:
        return path

    response = requests.get(document["url"], timeout=90)
    response.raise_for_status()
    if not response.content.startswith(b"%PDF"):
        raise ValueError(f"La réponse n'est pas un PDF: {document['url']}")
    path.write_bytes(response.content)
    return path


def extract_native(pdf_path: Path) -> tuple[str, int]:
    with fitz.open(pdf_path) as pdf:
        pages = [page.get_text("text") for page in pdf]
        return "\n\n".join(pages).strip(), len(pdf)


def extract_mistral_ocr(pdf_path: Path, api_key: str) -> tuple[str, dict]:
    encoded = base64.b64encode(pdf_path.read_bytes()).decode("ascii")
    payload = {
        "model": MODEL,
        "document": {
            "type": "document_url",
            "document_url": f"data:application/pdf;base64,{encoded}",
        },
        "table_format": "markdown",
        "confidence_scores_granularity": "page",
    }
    response = requests.post(
        MISTRAL_OCR_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=300,
    )
    response.raise_for_status()
    data = response.json()
    text = "\n\n".join(page.get("markdown", "") for page in data.get("pages", [])).strip()
    return text, data


def normalized_text(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^\wàâäçéèêëîïôöùûüÿœæ'-]+", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def metrics(native: str, ocr: str, page_count: int) -> dict:
    native_normalized = normalized_text(native)
    ocr_normalized = normalized_text(ocr)
    native_words = native_normalized.split()
    ocr_words = ocr_normalized.split()
    native_unique = set(native_words)
    ocr_unique = set(ocr_words)
    union = native_unique | ocr_unique
    intersection = native_unique & ocr_unique
    return {
        "pages": page_count,
        "native_characters": len(native),
        "ocr_characters": len(ocr),
        "native_words": len(native_words),
        "ocr_words": len(ocr_words),
        "character_length_difference": len(ocr) - len(native),
        "sequence_similarity": round(
            SequenceMatcher(None, native_normalized, ocr_normalized, autojunk=False).ratio(), 4
        ),
        "unique_word_jaccard": round(len(intersection) / len(union), 4) if union else 1.0,
    }


def write_markdown_report(rows: list[dict]) -> None:
    lines = [
        "# Comparaison extraction native / Mistral OCR",
        "",
        f"Modèle OCR : `{MODEL}`",
        "",
        "Les scores mesurent la similarité entre les sorties, pas leur exactitude. "
        "Une vérification humaine reste nécessaire.",
        "",
        "| Document | Pages | Mots natifs | Mots OCR | Similarité séquentielle | Jaccard mots uniques |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        item = row["metrics"]
        lines.append(
            f"| {row['title']} | {item['pages']} | {item['native_words']} | "
            f"{item['ocr_words']} | {item['sequence_similarity']:.1%} | "
            f"{item['unique_word_jaccard']:.1%} |"
        )
    lines.extend(
        [
            "",
            "## Fichiers à comparer manuellement",
            "",
        ]
    )
    for row in rows:
        lines.extend(
            [
                f"### {row['title']}",
                "",
                f"- Extraction native : `{row['id']}/native.txt`",
                f"- Mistral OCR : `{row['id']}/mistral_ocr.md`",
                f"- Réponse OCR complète : `{row['id']}/mistral_ocr.json`",
                "",
            ]
        )
    (RESULTS_DIR / "comparison_report.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    load_dotenv(ROOT / ".env")
    api_key = os.getenv("MISTRAL_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("Ajoute MISTRAL_API_KEY dans le fichier .env avant de lancer le test.")

    DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    rows = []

    for document in load_documents():
        print(f"Traitement: {document['title']}")
        pdf_path = download_pdf(document)
        native, page_count = extract_native(pdf_path)
        ocr, raw_ocr = extract_mistral_ocr(pdf_path, api_key)

        result_dir = RESULTS_DIR / document["id"]
        result_dir.mkdir(parents=True, exist_ok=True)
        (result_dir / "native.txt").write_text(native + "\n", encoding="utf-8")
        (result_dir / "mistral_ocr.md").write_text(ocr + "\n", encoding="utf-8")
        (result_dir / "mistral_ocr.json").write_text(
            json.dumps(raw_ocr, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

        row = {
            "id": document["id"],
            "title": document["title"],
            "source_url": document["url"],
            "model": MODEL,
            "metrics": metrics(native, ocr, page_count),
        }
        (result_dir / "metrics.json").write_text(
            json.dumps(row, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        rows.append(row)

    (RESULTS_DIR / "comparison_report.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    write_markdown_report(rows)
    print(f"\nRapport créé: {RESULTS_DIR / 'comparison_report.md'}")


if __name__ == "__main__":
    main()

