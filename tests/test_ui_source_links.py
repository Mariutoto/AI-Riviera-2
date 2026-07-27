from __future__ import annotations

import unittest

from app.ui import link_source_mentions


def grouped_sources(count: int) -> list[dict]:
    return [
        {"metadata": {"source_url": f"https://example.test/document-{index}.pdf"}}
        for index in range(1, count + 1)
    ]


class LinkSourceMentionsTests(unittest.TestCase):
    def test_removes_parenthesized_citation_only_line(self):
        answer = (
            "Les interpellations sont :\n\n"
            "- Premier objet (Source 1)\n"
            "- Deuxieme objet (Source 2)\n\n"
            "(Source 1, Source 2)"
        )

        linked = link_source_mentions(answer, grouped_sources(2))

        self.assertIn("- Premier objet ([PDF](https://example.test/document-1.pdf))", linked)
        self.assertIn("- Deuxieme objet ([PDF](https://example.test/document-2.pdf))", linked)
        self.assertNotIn("\n(PDF", linked)
        self.assertNotIn("Source ", linked)

    def test_removes_separately_parenthesized_citation_only_line(self):
        answer = "Reponse utile (Source 1).\n\n(Source 1), (Source 2), (Source 3)"

        linked = link_source_mentions(answer, grouped_sources(3))

        self.assertEqual(
            linked,
            "Reponse utile ([PDF](https://example.test/document-1.pdf)).",
        )

    def test_keeps_citations_attached_to_answer_text(self):
        answer = "Un fait verifiable (Source 1), puis un autre (Source 2)."

        linked = link_source_mentions(answer, grouped_sources(2))

        self.assertIn("[PDF](https://example.test/document-1.pdf)", linked)
        self.assertIn("[PDF](https://example.test/document-2.pdf)", linked)
        self.assertIn("Un fait verifiable", linked)


if __name__ == "__main__":
    unittest.main()
