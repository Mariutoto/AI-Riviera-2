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
                "title": (
                    "Interpellation de Mme Giuliana de Regibus (PS), "
                    "intitulée « L’apprentissage en question »"
                ),
                "category": "interpellation",
                "document_role": "interpellation_text",
                "summary": None,
                "metadata": {},
                "commune": "Vevey",
                "authors": ["Mme Giuliana de Regibus (PS)"],
                "response_number": "9/2025",
                "response_reference": "2025/ri09",
                "political_date": "2025-09-04",
                "response_date": "2025-09-15",
                "response_url": "https://example.test/vevey-response.pdf",
            },
            {
                "document_id": "object-2",
                "source_document_id": "response-2",
                "title": (
                    "Interpellation de Mme Gabrielle Heller (LV), "
                    "intitulée « Que contient l’assiette ? »"
                ),
                "category": "interpellation",
                "document_role": "combined_interpellation_response",
                "summary": None,
                "metadata": {},
                "commune": "La Tour-de-Peilz",
                "authors": ["Mme Gabrielle Heller (LV)"],
                "response_number": "2/2025",
                "political_date": "2025-04-13",
                "response_date": "2025-06-25",
                "response_url": "https://example.test/tour-response.pdf",
            },
        ]
        with patch(
            "app.agent.answered_interpellations", return_value=rows
        ) as query, patch("app.agent.answer_from_sources") as llm_answer:
            answer, results, trace = agent.run_agentic_pipeline(
                "Quelles interpellations ont reçu une réponse en 2025 ?",
                filters={"city": "all"},
            )

        query.assert_called_once()
        self.assertEqual(
            query.call_args.args[0]["response_year"], "2025"
        )
        self.assertEqual(query.call_args.args[0]["city"], "all")
        self.assertEqual(
            trace["aggregate_kind"], "answered_interpellations"
        )
        llm_answer.assert_not_called()
        self.assertIn("**Vevey**", answer)
        self.assertIn("**La Tour-de-Peilz**", answer)
        self.assertIn(
            "Interpellation de Mme Giuliana de Regibus (PS) : "
            "*« L'apprentissage en question »* (4 septembre 2025) — "
            "[*PDF*](https://example.test/vevey-response.pdf) "
            "*(RI 09/2025, 15 septembre 2025)*.",
            answer,
        )
        self.assertIn(
            "Interpellation de Mme Gabrielle Heller (LV) : "
            "*« Que contient l'assiette ? »* (13 avril 2025) — "
            "[*PDF*](https://example.test/tour-response.pdf) "
            "*(Réponse municipale n° 2/2025, 25 juin 2025)*.",
            answer,
        )
        self.assertNotIn("réponse non visible", answer.lower())
        self.assertNotIn("Source ", answer)
        self.assertEqual(
            results[0]["source_url"],
            "https://example.test/vevey-response.pdf",
        )


if __name__ == "__main__":
    unittest.main()
