import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scrape-la-tour-de-peilz" / "scrape_postulats_search_json_2021_2026.py"
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("postulats_search_json", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(module)

SAMPLE = """<div class="ik-callout-info ik-callout"><a href="/tools/pdf-viewer/web/viewer.php?file=https://www.la-tour-de-peilz.ch/doc_uploads/images/politique/conseil-communal/motions-postulats/2024/Postulat-Test.pdf"><h4>Postulat de Mme Jeanne Exemple (LV) + rapport + décision</h4></a><div class="lssrchres">Un sujet test</div><div> Motions, postulats et interpellations / 2024</div></div>"""


class PostulatSearchJsonTests(unittest.TestCase):
    def test_parses_postulat(self):
        item = module.parse_result_html(SAMPLE)[0]
        self.assertEqual(item["filename"], "Postulat-Test.pdf")
        self.assertEqual(item["status_normalized"], "report_and_decision_available")
        self.assertEqual(item["authors"][0]["name"], "Jeanne Exemple")

    def test_rejects_motion(self):
        self.assertEqual(module.parse_result_html(SAMPLE.replace("Postulat de", "Motion de")), [])

    def test_rejects_old_year(self):
        self.assertEqual(module.parse_result_html(SAMPLE.replace("2024", "2020")), [])


if __name__ == "__main__":
    unittest.main()
