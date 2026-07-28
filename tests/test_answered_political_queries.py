from __future__ import annotations

import unittest
from unittest.mock import patch

from app import agent, retrieval


def answered_row(
    document_id: str,
    title: str,
    commune: str,
    author: str,
    party: str,
    response_number: str,
    response_date: str,
    deposit_date: str,
) -> dict:
    political_metadata = {
        "additional_metadata": {
            "interpellation_metadata": {
                "authors": [{"name": author, "party": party}],
                "interpellation_date": deposit_date,
            }
        }
    }
    metadata = {
        "title": title,
        "commune": commune,
        "category": "interpellation",
        "document_date": response_date,
        "file_url": f"https://example.test/{document_id}.pdf",
        "additional_metadata": {
            "interpellation_metadata": {
                "authors": [{"name": author, "party": party}],
                "responses": [
                    {
                        "response_number": response_number,
                        "response_date": response_date,
                        "response_type": "municipal_response",
                    }
                ],
            }
        },
    }
    return {
        "document_id": document_id,
        "title": title,
        "category": "interpellation",
        "document_role": "combined_interpellation_response",
        "summary": "",
        "metadata": metadata,
        "political_object_metadata": political_metadata,
        "responses": metadata["additional_metadata"]["interpellation_metadata"]["responses"],
    }


class AnsweredPoliticalQueryTests(unittest.TestCase):
    def test_detects_plural_answered_interpellation_question(self):
        filters = retrieval.detect_answered_political_query(
            "Quelles interpellations ont reçu une réponse en 2025 ?"
        )

        self.assertEqual(
            filters,
            {
                "doc_type": "interpellations",
                "answered_only": True,
                "response_year": "2025",
            },
        )

    def test_does_not_capture_question_about_one_named_response(self):
        self.assertIsNone(
            retrieval.detect_answered_political_query(
                "Quelle réponse a reçu l'interpellation sur les cantines ?"
            )
        )

    @patch("app.agent.answered_political_objects")
    def test_exact_answer_uses_consistent_format_and_all_rows(self, fetch):
        fetch.return_value = [
            answered_row(
                "doc-1",
                "Zone 50 ?",
                "La Tour-de-Peilz",
                "Roger Urech",
                "PLR",
                "1/2025",
                "2025-03-19",
                "2025-02-08",
            ),
            answered_row(
                "doc-2",
                "Que contient l'assiette ?",
                "La Tour-de-Peilz",
                "Gabrielle Heller",
                "LV",
                "2/2025",
                "2025-06-25",
                "2025-05-14",
            ),
        ]

        answer, results = agent.run_answered_political_query(
            {
                "doc_type": "interpellations",
                "response_year": "2025",
                "city": "La Tour-de-Peilz",
            }
        )

        self.assertEqual(len(results), 2)
        self.assertIn("2 interpellations avec une réponse confirmée", answer)
        self.assertIn("**« Zone 50 ? »**", answer)
        self.assertIn("Auteur : Roger Urech (PLR)", answer)
        self.assertIn("Dépôt : 8 février 2025", answer)
        self.assertIn("Réponse municipale n° 1/2025 du 19 mars 2025", answer)
        self.assertIn("**« Que contient l'assiette ? »**", answer)

    @patch("app.agent.record_diagnostic")
    @patch("app.agent.run_answered_political_query", return_value=("Exact", []))
    @patch(
        "app.agent.retrieval.detect_answered_political_query",
        return_value={
            "doc_type": "interpellations",
            "answered_only": True,
            "response_year": "2025",
        },
    )
    def test_pipeline_routes_answered_question_before_semantic_search(
        self,
        _detect,
        exact_query,
        _diagnostic,
    ):
        answer, _results, trace = agent.run_agentic_pipeline(
            "Quelles interpellations ont reçu une réponse en 2025 ?",
            filters={"city": "all"},
        )

        self.assertEqual(answer, "Exact")
        self.assertEqual(trace["mode"], "answered_political")
        exact_query.assert_called_once_with(
            {
                "doc_type": "interpellations",
                "answered_only": True,
                "response_year": "2025",
                "city": "all",
            }
        )


if __name__ == "__main__":
    unittest.main()
