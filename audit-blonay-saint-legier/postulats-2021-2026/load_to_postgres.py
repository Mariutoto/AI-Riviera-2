from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parents[1]
SOURCE = PROJECT_ROOT / "audit-montreux" / "interpellations-2021-2026" / "load_to_postgres.py"
SPEC = importlib.util.spec_from_file_location("blonay_saint_legier_postulate_loader", SOURCE)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def main() -> None:
    module.ROOT = ROOT
    module.PROJECT_ROOT = PROJECT_ROOT
    module.EMBEDDING_DIR = ROOT / "general-audit" / "embedding"
    module.INPUT_PATH = module.EMBEDDING_DIR / "embedding_inputs.jsonl"
    module.VECTOR_PATH = module.EMBEDDING_DIR / "mistral_embeddings.jsonl"
    module.MANIFEST_PATH = module.EMBEDDING_DIR / "manifest.json"
    module.DOCUMENT_PREFIX = "blonay-saint-legier_postulate_"
    module.CATEGORY = "postulat"
    module.SPECIFIC_METADATA_KEY = "postulate_metadata"
    module.RECIPE_VERSION = "blonay-saint-legier-postulates-v1"
    module.COMMUNE = "Blonay–Saint-Légier"
    module.main()


if __name__ == "__main__":
    main()
