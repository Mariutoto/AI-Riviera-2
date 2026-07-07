import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scrape-la-tour-de-peilz" / "scrape_budgets_search_json_2021_2026.py"
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("budgets_search_json", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(module)

SAMPLE = """<div class="ik-callout-info ik-callout"><a href="https://www.la-tour-de-peilz.ch/doc_uploads/images/politique/municipalite/budget/2024-Budget-LTDP.pdf"><h4>Budget</h4></a><div class="lssrchres">2024</div><div> Rapport de gestion – Comptes – Budget  /  2024</div></div>"""


class BudgetsSearchJsonTests(unittest.TestCase):
    def test_parses_budget(self):
        item = module.parse_result_html(SAMPLE)[0]
        self.assertEqual(item["category"], "budget")
        self.assertEqual(item["fiscal_year"], 2024)
        self.assertEqual(item["document_family"], "financial_plan")

    def test_rejects_sibling_types(self):
        for title in ("Rapport des comptes", "Rapport de gestion"):
            with self.subTest(title=title):
                self.assertEqual(module.parse_result_html(SAMPLE.replace("Budget", title)), [])

    def test_rejects_wrong_category_and_old_year(self):
        self.assertEqual(module.parse_result_html(SAMPLE.replace("Rapport de gestion – Comptes – Budget", "Objets divers")), [])
        self.assertEqual(module.parse_result_html(SAMPLE.replace("2024", "2020")), [])

    def test_deduplicates(self):
        self.assertEqual(len(module.parse_result_html(SAMPLE + SAMPLE)), 1)


if __name__ == "__main__":
    unittest.main()
