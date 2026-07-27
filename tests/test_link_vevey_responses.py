from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scrape-vevey" / "link_interpellation_responses.py"
SPEC = importlib.util.spec_from_file_location("link_vevey_responses", SCRIPT)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class LinkVeveyResponsesTests(unittest.TestCase):
    def test_normalizes_response_reference_like_la_tour(self):
        self.assertEqual(module.response_number("2025//RI07"), "7/2025")
        self.assertEqual(module.response_number("2025/RI08bis"), "8bis/2025")

    def test_normalized_title_matches_response_to_object(self):
        response = {
            "title": (
                "Réponse à l’interpellation de Mme Exemple, intitulée "
                "« Mobilité et sécurité à Vevey »"
            ),
            "listing_year": "2026",
        }
        objects = [{
            "document_id": "object-1",
            "title": (
                "Interpellation de Mme Exemple, intitulée "
                "« Mobilité et sécurité à Vevey »"
            ),
            "listing_year": "2026",
        }]

        match = module.best_match(response, objects)

        self.assertEqual(match["political_object_id"], "object-1")
        self.assertEqual(match["matching_confidence"], "exact")

    def test_response_record_uses_la_tour_general_metadata(self):
        response = {
            "document_id": "response-1",
            "title": "Réponse mobilité",
            "source_page": "https://example.test/list",
            "pdf_url": "https://example.test/response.pdf",
            "listing_year": "2026",
            "legislature": "2021-2026",
            "listing_date": "2026-05-07",
            "reference": "2026/RI09",
            "content_hash": "pdf-hash",
            "text_audit": {
                "text_hash": "text-hash",
                "text_chars": 1200,
                "text_words": 200,
                "needs_ocr": False,
            },
        }
        match = {
            "political_object_id": "object-1",
            "score": 1.0,
            "second_score": 0.2,
            "matching_method": "normalized_title",
            "matching_confidence": "exact",
        }

        record = module.homogeneous_response_record(response, match)

        self.assertEqual(
            set(record["document_metadata"]),
            {
                "document_id", "commune", "document_family", "category",
                "document_role", "title", "source_title",
                "source_page_url", "file_url", "listing_year",
                "legislature", "document_date", "content_hash",
                "extraction_method", "processing_status",
            },
        )
        self.assertEqual(
            record["interpellation_metadata"]["responses"][0][
                "response_number"
            ],
            "9/2026",
        )
        self.assertEqual(
            record["relationships"]["political_object_id"], "object-1"
        )

    def test_merges_response_into_object_metadata_like_la_tour(self):
        object_record = {
            "document_metadata": {"document_id": "object-1"},
            "interpellation_metadata": {
                "political_status": "filed",
                "responses": [],
            },
            "processing": {},
            "relationships": {
                "response_status": "not_collected_yet",
                "response_document_ids": [],
            },
        }
        response_record = {
            "document_metadata": {"document_id": "response-1"},
            "interpellation_metadata": {
                "responses": [{
                    "response_number": "9/2026",
                    "response_date": "2026-05-07",
                    "response_type": "municipal_response",
                    "municipal_adoption_date": None,
                }]
            },
            "relationships": {"political_object_id": "object-1"},
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "object-1.json"
            path.write_text(json.dumps(object_record), encoding="utf-8")

            merged = module.merge_object_records(
                Path(directory), [response_record]
            )

        self.assertEqual(
            merged[0]["interpellation_metadata"]["political_status"],
            "response_available",
        )
        self.assertEqual(
            merged[0]["relationships"]["response_document_ids"],
            ["response-1"],
        )


if __name__ == "__main__":
    unittest.main()
