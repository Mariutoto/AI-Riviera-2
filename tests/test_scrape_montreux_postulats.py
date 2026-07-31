import importlib.util
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRAPER = ROOT / "scrape-montreux" / "scrape_postulats_2021_2026.py"
SPEC = importlib.util.spec_from_file_location("montreux_postulates", SCRAPER)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class MontreuxPostulatePipelineTests(unittest.TestCase):
    def test_response_matching_requires_strong_title_evidence(self):
        postulate = {"title": "Harcèlement de rue à Montreux"}
        response = {
            "source_title": "Réponse au postulat Harcèlement de rue à Montreux",
            "extracted_text": "",
        }
        self.assertTrue(module.response_match(postulate, response)["matched"])

    def test_inventory_is_complete_and_deduplicated(self):
        inventory = json.loads(
            (ROOT / "audit-montreux" / "postulats-2021-2026" / "inventory.json").read_text(encoding="utf-8")
        )
        diagnostics = inventory["diagnostics"]
        self.assertTrue(diagnostics["complete"])
        self.assertEqual(diagnostics["endpoint_results"], 173)
        self.assertEqual(len(inventory["objects"]), 49)
        self.assertEqual(len(inventory["documents"]), 95)
        self.assertEqual(diagnostics["duplicate_document_rows_removed"], 2)
        self.assertEqual(diagnostics["objects_with_response"], 7)
        self.assertEqual(diagnostics["documents_needing_ocr"], 32)

    def test_audit_and_embeddings_are_ready(self):
        root = ROOT / "audit-montreux" / "postulats-2021-2026" / "general-audit"
        audit = json.loads((root / "audit.json").read_text(encoding="utf-8"))
        self.assertEqual(audit["summary"]["documents"], 144)
        self.assertEqual(audit["summary"]["chunks"], 512)
        self.assertEqual(audit["summary"]["ocr_applied"], 32)
        report = json.loads((root / "embedding" / "validation_report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["status"], "ready_for_embeddings")
        self.assertEqual(report["summary"]["review"], 0)
        self.assertEqual(report["summary"]["political_objects"], 49)


if __name__ == "__main__":
    unittest.main()
