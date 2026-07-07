import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scrape-la-tour-de-peilz" / "scrape_preavis_search_json_2021_2026.py"
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("preavis_search_json", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(module)

SAMPLE = """<div class="ik-callout-info ik-callout"><a href="/tools/pdf-viewer/web/viewer.php?file=https://www.la-tour-de-peilz.ch/doc_uploads/images/politique/municipalite/preavis-municipaux/2025/Preavis-20-Test-Rapp-Dec.pdf"><h4>Préavis municipal + rapport + décision | Nr. 20 | 2025</h4></a><div class="lssrchres">Demande d'un crédit pour un projet test</div><div> Préavis municipaux / 2025</div></div>"""


class PreavisSearchJsonTests(unittest.TestCase):
    def test_parses_combined_preavis(self):
        item = module.parse_result_html(SAMPLE)[0]
        self.assertEqual(item["preavis_number"], "20/2025")
        self.assertEqual(item["title"], "Demande d'un crédit pour un projet test")
        self.assertEqual(item["document_role"], "combined_preavis_report_decision")
        self.assertTrue(item["listing_contains_report"])
        self.assertFalse(item["source_number_conflict"])
        self.assertNotIn("source_category_id", item)

    def test_rejects_wrong_category(self):
        self.assertEqual(module.parse_result_html(SAMPLE.replace("Préavis municipaux /", "Objets divers /")), [])

    def test_rejects_old_year(self):
        self.assertEqual(module.parse_result_html(SAMPLE.replace("2025", "2020")), [])

    def test_accepts_complement_to_preavis(self):
        item = module.parse_result_html(
            SAMPLE.replace("Préavis municipal +", "Complément au préavis municipal +")
        )[0]
        self.assertTrue(item["is_complement"])

    def test_flags_listing_filename_number_conflict(self):
        item = module.parse_result_html(
            SAMPLE.replace("Nr. 20", "Nr. 1")
        )[0]
        self.assertEqual(item["listing_preavis_number"], "1/2025")
        self.assertEqual(item["filename_preavis_number"], "20/2025")
        self.assertTrue(item["source_number_conflict"])


if __name__ == "__main__":
    unittest.main()
