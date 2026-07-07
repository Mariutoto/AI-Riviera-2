import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scrape-la-tour-de-peilz" / "scrape_rapport_comptes_search_json_2021_2026.py"
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("rapport_comptes_search_json", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(module)

SAMPLE = """<div class="ik-callout-info ik-callout"><a href="https://www.la-tour-de-peilz.ch/doc_uploads/images/politique/municipalite/Rapport-des-comptes/2024-Rapport-des-comptes-LTDP.pdf"><h4>Rapport des comptes</h4></a><div class="lssrchres">2024</div><div> Rapport de gestion – Comptes – Budget  /  2024</div></div>"""


class RapportComptesSearchJsonTests(unittest.TestCase):
    def test_parses_rapport_des_comptes(self):
        item = module.parse_result_html(SAMPLE)[0]
        self.assertEqual(item["title"], "Rapport des comptes 2024")
        self.assertEqual(item["category"], "rapport_comptes")
        self.assertEqual(item["document_role"], "annual_accounts_report")
        self.assertEqual(item["fiscal_year"], 2024)
        self.assertEqual(item["period_start"], "2024-01-01")
        self.assertEqual(item["period_end"], "2024-12-31")

    def test_rejects_sibling_document_types_in_same_category(self):
        for title in ("Rapport de gestion", "Budget"):
            with self.subTest(title=title):
                self.assertEqual(
                    module.parse_result_html(SAMPLE.replace("Rapport des comptes", title)),
                    [],
                )

    def test_rejects_wrong_category(self):
        self.assertEqual(
            module.parse_result_html(SAMPLE.replace("Rapport de gestion – Comptes – Budget", "Objets divers")),
            [],
        )

    def test_rejects_old_year(self):
        self.assertEqual(module.parse_result_html(SAMPLE.replace("2024", "2020")), [])

    def test_deduplicates_by_file_url(self):
        items = module.parse_result_html(SAMPLE + SAMPLE)
        self.assertEqual(len(items), 1)


if __name__ == "__main__":
    unittest.main()
