from __future__ import annotations

import threading
import unittest
from unittest.mock import patch

from app import agent


class AgentRoutingTests(unittest.TestCase):
    def test_obvious_single_object_question_skips_llm_classifier(self):
        with patch("app.agent.classify_question_with_llm") as classify:
            result = agent.classify_question("Quelle motion a été déposée en 2026 ?")
        classify.assert_not_called()
        self.assertEqual(result["mode"], "single")
        self.assertEqual(result["classification_source"], "deterministic")

    def test_single_quoted_title_does_not_trigger_on_conjunction_inside_title(self):
        question = 'Qui a déposé "Aidons nos commerçants et nos sociétés locales" en 2021 ?'
        self.assertFalse(agent.question_needs_llm_classification(question))

    def test_comparison_question_keeps_llm_classifier(self):
        expected = {
            "complexity": "complex",
            "mode": "multi",
            "subqueries": [{"label": "A", "query": "A"}, {"label": "B", "query": "B"}],
        }
        with patch("app.agent.classify_question_with_llm", return_value=expected) as classify:
            result = agent.classify_question("Compare les motions de 2024 et 2025")
        classify.assert_called_once()
        self.assertEqual(result["mode"], "multi")
        self.assertEqual(result["classification_source"], "llm")

    def test_cross_reference_subqueries_run_concurrently_and_keep_order(self):
        barrier = threading.Barrier(2)
        worker_threads = []

        def search(query, **_kwargs):
            worker_threads.append(threading.get_ident())
            barrier.wait(timeout=2)
            return ([{"id": query, "document_id": query, "metadata": {}}], False)

        subqueries = [
            {"label": "Première", "query": "requête A"},
            {"label": "Deuxième", "query": "requête B"},
        ]
        with patch("app.agent.search_with_relance", side_effect=search):
            result = agent.merge_cross_reference(subqueries)

        self.assertEqual(
            [entry["label"] for entry in result["sub_results"]],
            ["Première", "Deuxième"],
        )
        self.assertEqual(len(set(worker_threads)), 2)

    def test_answered_interpellations_use_structured_route(self):
        rows = [
            {
                "document_id": "object-1",
                "source_document_id": "response-1",
                "title": "Interpellation test",
                "category": "interpellation",
                "document_role": "interpellation_text",
                "summary": None,
                "metadata": {},
                "commune": "Vevey",
                "authors": ["Mme Exemple"],
                "response_number": "9/2025",
                "response_date": "2025-10-02",
                "response_url": "https://example.test/response.pdf",
            }
        ]
        with patch(
            "app.agent.answered_interpellations", return_value=rows
        ) as query:
            answer, results, trace = agent.run_agentic_pipeline(
                "Quelles interpellations ont reçu une réponse en 2025 ?"
            )

        query.assert_called_once()
        self.assertEqual(
            query.call_args.args[0]["response_year"], "2025"
        )
        self.assertEqual(
            trace["aggregate_kind"], "answered_interpellations"
        )
        self.assertIn("réponse municipale 9/2025", answer)
        self.assertNotIn("attendue", answer)
        self.assertEqual(
            results[0]["source_url"],
            "https://example.test/response.pdf",
        )


if __name__ == "__main__":
    unittest.main()
