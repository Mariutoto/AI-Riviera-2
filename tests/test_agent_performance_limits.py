from __future__ import annotations

import unittest
from unittest.mock import patch

from app import agent


def result(index: int) -> dict:
    return {
        "id": f"chunk-{index}",
        "document_id": f"doc-{index}",
        "content": f"Passage {index}",
        "metadata": {"title": f"Document {index}"},
    }


class AgentPerformanceLimitTests(unittest.TestCase):
    def test_generation_results_are_limited_to_fifteen(self):
        results = [result(index) for index in range(25)]
        self.assertEqual(len(agent._generation_results(results)), 15)

    @patch("app.agent.record_diagnostic")
    @patch("app.agent._timed_source_blurbs", return_value=({}, 1))
    @patch("app.agent.answer_from_sources", return_value="Réponse")
    @patch("app.agent.rerank_results_with_llm")
    @patch("app.agent.search_with_relance")
    @patch("app.agent.classify_question", return_value={"complexity": "simple", "mode": "single"})
    @patch("app.agent.retrieval.detect_aggregate_query", return_value=None)
    def test_simple_pipeline_applies_limits_and_records_timings(
        self,
        _detect,
        _classify,
        search,
        rerank,
        answer,
        _blurbs,
        _diagnostic,
    ):
        retrieved = [result(index) for index in range(30)]
        search.return_value = (retrieved, False)
        rerank.return_value = retrieved

        selected_filters = {"year": "2025", "doc_type": "interpellations"}
        final_answer, returned_results, trace = agent.run_agentic_pipeline(
            "Question simple",
            filters=selected_filters,
        )

        self.assertEqual(final_answer, "Réponse")
        self.assertEqual(returned_results, retrieved)
        rerank.assert_called_once_with(
            "Question simple",
            retrieved,
            keep=agent.RERANK_KEEP_LIMIT,
            max_candidates=20,
        )
        self.assertEqual(len(answer.call_args.args[1]), 15)
        self.assertEqual(trace["rerank_candidate_limit"], 20)
        self.assertEqual(trace["generation_passage_limit"], 15)
        self.assertEqual(trace["generation_passages"], 15)
        self.assertEqual(trace["filters"], selected_filters)
        self.assertEqual(search.call_args.kwargs["filters"], selected_filters)
        for stage in (
            "routing",
            "classification",
            "retrieval",
            "reranking",
            "generation",
            "verification",
            "source_blurbs",
            "total",
        ):
            self.assertIn(stage, trace["timings_ms"])


if __name__ == "__main__":
    unittest.main()
