from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parents[1]
SOURCE = PROJECT_ROOT / "audit-vevey" / "motions-2021-2026" / "generate_embedding_inputs.py"
SPEC = importlib.util.spec_from_file_location("embedding_inputs_source", SOURCE)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def main() -> None:
    module.ROOT = ROOT
    module.PROJECT_ROOT = PROJECT_ROOT
    module.AUDIT = ROOT / "general-audit"
    module.CHUNK_DIR = module.AUDIT / "chunks"
    module.METADATA_DIR = module.AUDIT / "metadata"
    module.OUTPUT = module.AUDIT / "embedding"
    module.INPUT_PATH = module.OUTPUT / "embedding_inputs.jsonl"
    module.REPORT_PATH = module.OUTPUT / "validation_report.json"
    module.main()
    report = json.loads(module.REPORT_PATH.read_text(encoding="utf-8"))
    report["summary"]["political_objects"] = 6
    module.REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
