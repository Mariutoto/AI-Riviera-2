from __future__ import annotations

import unittest
from unittest.mock import patch

from app.agent import run_aggregate_query
from app.answer import SYSTEM_PROMPT, build_context


class MultiCityAnswerTests(unittest.TestCase):
    def test_system_prompt_requires_one_section_per_commune(self):
        self.assertIn("une section distincte par commune", SYSTEM_PROMPT)
        self.assertIn("Plusieurs communes concernées", SYSTEM_PROMPT)

    def test_build_context_exposes_each_source_commune(self):
        results = [
            {
                "id": "vevey#1",
                "document_id": "vevey",
                "content": "Contenu Vevey",
                "score": 1,
                "metadata": {"title": "Document Vevey", "commune": "Vevey"},
            },
            {
                "id": "tour#1",
                "document_id": "tour",
                "content": "Contenu La Tour-de-Peilz",
                "score": 0.9,
                "metadata": {
                    "title": "Document La Tour-de-Peilz",
                    "commune": "La Tour-de-Peilz",
                },
            },
        ]

        context = build_context(results)

        self.assertIn("Commune: Vevey", context)
        self.assertIn("Commune: La Tour-de-Peilz", context)

    @patch("app.agent.aggregate_authors")
    def test_aggregate_answer_groups_documents_by_commune(self, aggregate_authors):
        aggregate_authors.return_value = [
            {
                "document_id": "vevey-1",
                "title": "Interpellation Vevey",
                "category": "interpellation",
                "summary": "",
                "author_name": "Alice",
                "metadata": {"commune": "Vevey"},
            },
            {
                "document_id": "tour-1",
                "title": "Interpellation La Tour",
                "category": "interpellation",
                "summary": "",
                "author_name": "Bob",
                "metadata": {"commune": "La Tour-de-Peilz"},
            },
        ]

        answer, _results = run_aggregate_query({"doc_type": "interpellations"})

        self.assertIn("**La Tour-de-Peilz**", answer)
        self.assertIn("**Vevey**", answer)
        self.assertLess(
            answer.index("**La Tour-de-Peilz**"),
            answer.index("- Interpellation de Bob"),
        )
        self.assertLess(
            answer.index("**Vevey**"),
            answer.index("- Interpellation de Alice"),
        )

    @patch("app.agent.aggregate_authors")
    def test_single_commune_aggregate_keeps_compact_format(self, aggregate_authors):
        aggregate_authors.return_value = [
            {
                "document_id": "vevey-1",
                "title": "Interpellation Vevey",
                "category": "interpellation",
                "summary": "",
                "author_name": "Alice",
                "metadata": {"commune": "Vevey"},
            }
        ]

        answer, _results = run_aggregate_query(
            {"doc_type": "interpellations", "city": "Vevey"}
        )

        self.assertNotIn("### Vevey", answer)
        self.assertIn(
            "- Interpellation de Alice : *« Vevey »*.",
            answer,
        )

    @patch("app.agent.aggregate_authors")
    def test_aggregate_sources_are_unique_per_document(self, aggregate_authors):
        base = {
            "document_id": "motion-1",
            "title": "Une motion cosignée",
            "category": "motion",
            "summary": "",
            "metadata": {"commune": "Vevey"},
        }
        aggregate_authors.return_value = [
            {**base, "author_name": "Alice"},
            {**base, "author_name": "Bob"},
        ]

        answer, results = run_aggregate_query(
            {"doc_type": "motions", "city": "Vevey"}
        )

        self.assertIn("Alice et Bob", answer)
        self.assertEqual(len(results), 1)


if __name__ == "__main__":
    unittest.main()
