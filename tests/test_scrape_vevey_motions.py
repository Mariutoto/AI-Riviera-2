import importlib.util
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRAPER_PATH = ROOT / "scrape-vevey" / "scrape_motions_2021_2026.py"
SCRAPER_SPEC = importlib.util.spec_from_file_location(
    "scrape_vevey_motions", SCRAPER_PATH
)
scraper = importlib.util.module_from_spec(SCRAPER_SPEC)
assert SCRAPER_SPEC and SCRAPER_SPEC.loader
SCRAPER_SPEC.loader.exec_module(scraper)

AUDIT_PATH = (
    ROOT / "audit-vevey" / "motions-2021-2026" / "build_audit.py"
)
AUDIT_SPEC = importlib.util.spec_from_file_location(
    "audit_vevey_motions", AUDIT_PATH
)
audit = importlib.util.module_from_spec(AUDIT_SPEC)
assert AUDIT_SPEC and AUDIT_SPEC.loader
AUDIT_SPEC.loader.exec_module(audit)


def teaser(document_type: str, title: str, download_id: str = "5740"):
    return f"""
    <div class="teaser-politique text-sm row">
      <div class="col-3"><p>27.03.2025<br>Sandra Marques</p></div>
      <div class="col-9">
        <p class="teaser-id"><span>{document_type}</span><span></span></p>
        <h4><a href="https://conseil.vevey.ch/ConseilCommunal/download.asp?d={download_id}">{title}</a></h4>
        <a href="https://conseil.vevey.ch/ConseilCommunal/download.asp?d={download_id}">Télécharger PDF</a>
      </div>
    </div>
    """


class VeveyMotionScraperTests(unittest.TestCase):
    def test_parser_preserves_catalogue_type(self):
        item = scraper.parse_teaser(
            teaser("Motion", "Motion de Mme Exemple")
        )
        self.assertEqual(item["displayed_type"], "Motion")
        self.assertEqual(item["city"], "Vevey")
        self.assertEqual(item["source_download_id"], "5740")

    def test_motion_filter_false_positive_is_detectable_from_title(self):
        item = scraper.parse_teaser(
            teaser("Motion", "Postulat de Mme Exemple")
        )
        self.assertFalse(
            bool(re.match(r"^\\s*motion\\b", scraper.ascii_fold(item["title"])))
        )

    def test_section_selection_uses_substantive_last_occurrence(self):
        text = (
            "13.2 Motion de Mme Sandra Marques titre 14. Questions "
            "autre contenu 13.2 Motion de Mme Sandra Marques débat décision "
            "14. Questions"
        )
        selected = audit.selected_text(text, "sandra_decision")
        self.assertIn("débat décision", selected)
        self.assertNotIn("autre contenu", selected)

    def test_four_object_profiles_have_explicit_response_state(self):
        self.assertEqual(len(audit.OBJECTS), 4)
        self.assertEqual(
            sum(profile["has_response"] for profile in audit.OBJECTS.values()),
            2,
        )
        self.assertTrue(
            all(
                profile.get("response_status")
                for profile in audit.OBJECTS.values()
            )
        )


if __name__ == "__main__":
    unittest.main()
