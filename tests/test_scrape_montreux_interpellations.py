import importlib.util
import json
import unittest
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRAPER_PATH = (
    ROOT
    / "scrape-montreux"
    / "scrape_interpellations_2021_2026.py"
)
SPEC = importlib.util.spec_from_file_location(
    "scrape_montreux_interpellations", SCRAPER_PATH
)
scraper = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(scraper)


class MontreuxInterpellationScraperTests(unittest.TestCase):
    def test_french_first_day_is_parsed(self):
        self.assertEqual(
            scraper.parse_french_date("Séance du 1er février 2023"),
            date(2023, 2, 1),
        )

    def test_response_filename_signals_exclude_simple_question(self):
        self.assertTrue(
            scraper.response_like_filename(
                "REPONSEMUN_Interpellation_exemple.pdf"
            )
        )
        self.assertFalse(
            scraper.response_like_filename(
                "SimpleQuestion_annexe.pdf"
            )
        )

    def test_generated_inventory_is_complete_and_auditable(self):
        path = (
            ROOT
            / "audit-montreux"
            / "interpellations-2021-2026"
            / "inventory.json"
        )
        inventory = json.loads(path.read_text(encoding="utf-8"))
        diagnostics = inventory["diagnostics"]
        self.assertTrue(diagnostics["complete"])
        self.assertEqual(diagnostics["endpoint_results"], 592)
        self.assertEqual(len(inventory["objects"]), 155)
        self.assertEqual(len(inventory["response_documents"]), 66)
        self.assertEqual(diagnostics["responses_needing_ocr"], 0)
        self.assertEqual(
            sum(row["has_response"] for row in inventory["objects"]),
            152,
        )

    def test_generated_audit_has_complete_relationships(self):
        path = (
            ROOT
            / "audit-montreux"
            / "interpellations-2021-2026"
            / "general-audit"
            / "audit.json"
        )
        audit = json.loads(path.read_text(encoding="utf-8"))
        summary = audit["summary"]
        self.assertEqual(summary["political_objects"], 155)
        self.assertEqual(summary["documents"], 307)
        self.assertEqual(summary["objects_with_response"], 152)
        self.assertEqual(summary["objects_without_verified_response"], 3)
        self.assertEqual(summary["ocr_applied"], 0)


if __name__ == "__main__":
    unittest.main()
