from __future__ import annotations

import unittest
from unittest.mock import patch

from app.answer import source_blurbs_with_fallback


def source(summary: str = "", title: str = "Document") -> dict:
    metadata = {"title": title}
    if summary:
        metadata["summary"] = summary
    return {
        "metadata": metadata,
        "passages": [{"content": f"Contenu de {title}"}],
    }


class SourceBlurbsWithFallbackTests(unittest.TestCase):
    def test_uses_stored_summaries_without_llm_call(self):
        grouped = [source("Résumé A", "A"), source("Résumé B", "B")]
        with patch("app.answer.summarize_sources_with_llm") as summarize:
            result = source_blurbs_with_fallback(grouped)
        self.assertEqual(result, {"1": "Résumé A", "2": "Résumé B"})
        summarize.assert_not_called()

    def test_generates_only_missing_summaries_and_preserves_ids(self):
        grouped = [source("Résumé stocké", "A"), source(title="B"), source(title="C")]
        with patch(
            "app.answer.summarize_sources_with_llm",
            return_value={"1": "Résumé B", "2": "Résumé C"},
        ) as summarize:
            result = source_blurbs_with_fallback(grouped)
        self.assertEqual(result, {"1": "Résumé stocké", "2": "Résumé B", "3": "Résumé C"})
        summarize.assert_called_once_with(grouped[1:])

    def test_missing_fallback_result_is_omitted(self):
        grouped = [source(title="A")]
        with patch("app.answer.summarize_sources_with_llm", return_value={}):
            result = source_blurbs_with_fallback(grouped)
        self.assertEqual(result, {})


if __name__ == "__main__":
    unittest.main()
