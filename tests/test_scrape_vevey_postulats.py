import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRAPER_PATH = ROOT / "scrape-vevey" / "scrape_postulats_2021_2026.py"
SPEC = importlib.util.spec_from_file_location(
    "scrape_vevey_postulats",
    SCRAPER_PATH,
)
scraper = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(scraper)


class VeveyPostulatePipelineTests(unittest.TestCase):
    def test_inventory_has_thirty_originals_and_five_responses(self):
        self.assertEqual(len(scraper.OBJECTS), 30)
        self.assertEqual(len(scraper.ORIGINAL_SOURCES), 30)
        self.assertEqual(
            sum(profile["has_response"] for profile in scraper.OBJECTS.values()),
            5,
        )
        self.assertEqual(
            sum(row[2] == "municipal_response" for row in scraper.FOLLOW_UPS),
            5,
        )

    def test_early_postulates_use_only_the_relevant_minutes_pages(self):
        early = {
            key: pages
            for key, _download_id, pages in scraper.ORIGINAL_SOURCES
            if pages
        }
        self.assertEqual(len(early), 8)
        self.assertEqual(early["precarite-menstruelle"], [15, 15])
        self.assertEqual(early["carte-citoyenne"], [25, 26])

    def test_known_catalogue_duplicates_are_not_canonical_sources(self):
        source_ids = {download_id for _key, download_id, _pages in scraper.ORIGINAL_SOURCES}
        self.assertIn("5412", source_ids)
        self.assertNotIn("5423", source_ids)
        self.assertIn("5151", source_ids)
        self.assertNotIn("5174", source_ids)
        self.assertIn("6073", source_ids)
        self.assertNotIn("6072", source_ids)

    def test_generated_audit_has_no_unanswered_object_marked_answered(self):
        path = (
            ROOT
            / "audit-vevey"
            / "postulats-2021-2026"
            / "general-audit"
            / "audit.json"
        )
        audit = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(audit["summary"]["political_objects"], 30)
        self.assertEqual(audit["summary"]["objects_with_response"], 5)
        answered = {
            row["object_key"]
            for row in audit["political_objects"]
            if row["has_response"]
        }
        self.assertEqual(
            answered,
            {
                "non-recours",
                "insecurite-gare",
                "ville-images",
                "micro-forets",
                "accueil-prescolaire",
            },
        )


class AnsweredPostulateRoutingTests(unittest.TestCase):
    def test_french_masculine_plural_query_is_detected(self):
        from app.retrieval import detect_answered_postulates_query

        self.assertEqual(
            detect_answered_postulates_query(
                "Quels postulats ont reçu une réponse en 2025 ?"
            ),
            {
                "doc_type": "postulats",
                "response_available": True,
                "response_year": "2025",
            },
        )

    @patch("app.agent.answered_postulates")
    def test_answer_uses_response_pdf_and_one_response_date(self, answered):
        from app.agent import run_answered_postulates_query

        answered.return_value = [
            {
                "document_id": "original",
                "source_document_id": "response",
                "title": "Un objet test",
                "commune": "Vevey",
                "authors": ["Mme Jeanne Exemple (PS)"],
                "political_date": "2024-01-01",
                "response_reference": "2025/RP01",
                "response_date": "2025-02-03",
                "response_url": "https://example.test/response.pdf",
                "metadata": {},
            }
        ]
        answer, results = run_answered_postulates_query(
            {"response_year": "2025"}
        )
        self.assertIn("https://example.test/response.pdf", answer)
        self.assertIn("3 février 2025", answer)
        self.assertNotIn("1 janvier 2024", answer)
        self.assertEqual(results[0]["category"], "postulat")


if __name__ == "__main__":
    unittest.main()
