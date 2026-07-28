from __future__ import annotations

import threading
import unittest
from unittest.mock import patch

from app import agent


class AgentRoutingTests(unittest.TestCase):
    def test_named_city_overrides_all_city_scope(self):
        filters = agent.apply_question_city_scope(
            "Quels sont les sujets des postulats de La Tour-de-Peilz en 2024 ?",
            {"city": "all"},
        )

        self.assertEqual(filters["city"], "La Tour-de-Peilz")

    def test_two_named_cities_keep_all_scope(self):
        filters = agent.apply_question_city_scope(
            "Compare Vevey et La Tour-de-Peilz",
            {"city": "all"},
        )

        self.assertEqual(filters["city"], "all")

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


if __name__ == "__main__":
    unittest.main()
