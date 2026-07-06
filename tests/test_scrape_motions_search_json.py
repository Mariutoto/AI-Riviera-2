import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scrape-la-tour-de-peilz" / "scrape_motions_search_json_2021_2026.py"
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("motions_search_json", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(module)


SAMPLE = """
<div class="ik-callout-info ik-callout">
  <a class="c-tmplt" href="/tools/pdf-viewer/web/viewer.php?file=https://www.la-tour-de-peilz.ch/doc_uploads/images/politique/conseil-communal/motions-postulats/2022/Motion-Test.pdf">
    <h4>Motion de Mme Jeanne Exemple (LV) + rapport + décision</h4>
  </a>
  <div class="lssrchres">Un sujet &amp; son résumé</div>
  <div> Motions, postulats et interpellations / 2022</div>
</div>
"""


class MotionSearchJsonParserTests(unittest.TestCase):
    def test_parses_motion_pdf_and_metadata(self):
        items = module.parse_result_html(SAMPLE)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["filename"], "Motion-Test.pdf")
        self.assertEqual(items[0]["summary"], "Un sujet & son résumé")
        self.assertEqual(items[0]["status_normalized"], "report_and_decision_available")
        self.assertEqual(items[0]["authors"][0]["name"], "Jeanne Exemple")

    def test_rejects_year_outside_legislature(self):
        self.assertEqual(module.parse_result_html(SAMPLE.replace("2022", "2020")), [])

    def test_rejects_non_motion_title(self):
        self.assertEqual(module.parse_result_html(SAMPLE.replace("Motion de", "Interpellation de")), [])


if __name__ == "__main__":
    unittest.main()
