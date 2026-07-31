from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT.parent / "interpellations-pilot" / "build_general_audit.py"
SPEC = importlib.util.spec_from_file_location("vevey_interpellations_general_shared", SOURCE)
assert SPEC and SPEC.loader
shared = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(shared)

shared.ROOT = ROOT
shared.INVENTORY_PATH = ROOT / "inventory.json"
shared.PDF_DIR = ROOT / "pdfs"
shared.OUTPUT_DIR = ROOT / "general-audit"
shared.METADATA_DIR = shared.OUTPUT_DIR / "metadata"
shared.TEXT_DIR = shared.OUTPUT_DIR / "clean_text"
shared.CHUNKS_DIR = shared.OUTPUT_DIR / "chunks"
shared.DETAIL_DIR = shared.OUTPUT_DIR / "documents"
shared.OCR_DIR = ROOT / "ocr_overrides"


if __name__ == "__main__":
    shared.main()
