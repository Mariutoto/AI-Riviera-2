from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "audit-vevey"
    / "interpellations-pilot"
    / "build_general_audit.py"
)
SPEC = importlib.util.spec_from_file_location("vevey_general_audit", SCRIPT)
assert SPEC and SPEC.loader
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


class VeveyGeneralAuditTests(unittest.TestCase):
    def test_regular_interpellation_embeds_whole_document(self):
        document = {"document_role": "political_object"}
        extraction = {"text": "Texte de l'interpellation", "page_texts": []}

        text, source = audit.select_interpellation_text(document, extraction)

        self.assertEqual(text, "Texte de l'interpellation")
        self.assertEqual(source, "whole_document")

    def test_scanned_interpellation_annex_requires_targeted_ocr(self):
        document = {"document_role": "response"}
        extraction = {
            "text": "Réponse municipale native",
            "page_texts": ["Réponse municipale native", "", "", ""],
            "empty_pages": 3,
        }

        text, source = audit.select_interpellation_text(document, extraction)

        self.assertEqual(text, "")
        self.assertEqual(source, "appended_interpellation_scanned")

    def test_embedding_input_matches_la_tour_recipe(self):
        base = {
            "document_family": "political_object",
            "category": "interpellation",
            "document_role": "interpellation_text",
            "title": "Mobilité à Vevey",
            "document_date": "2026-02-05",
            "content_hash": "secret-technical-hash",
            "file_url": "https://example.test/source.pdf",
        }

        value = audit.embedding_input(base, "Question municipale")

        self.assertIn("Famille: political_object", value)
        self.assertIn("Catégorie: interpellation", value)
        self.assertIn("Rôle: interpellation_text", value)
        self.assertIn("Section: Interpellation", value)
        self.assertIn("Question municipale", value)
        self.assertNotIn("secret-technical-hash", value)
        self.assertNotIn("https://", value)


if __name__ == "__main__":
    unittest.main()
