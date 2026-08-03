from __future__ import annotations

import unittest

from app.ui import (
    available_document_type_labels,
    link_source_mentions,
    source_link_label,
)


def grouped_sources(count: int) -> list[dict]:
    return [
        {"metadata": {"source_url": f"https://example.test/document-{index}.pdf"}}
        for index in range(1, count + 1)
    ]


class LinkSourceMentionsTests(unittest.TestCase):
    def test_document_filters_follow_each_available_corpus(self):
        self.assertEqual(
            available_document_type_labels("Montreux"),
            ["Tous", "Interpellations", "Postulats", "Motions"],
        )
        self.assertEqual(
            available_document_type_labels("Vevey"),
            ["Tous", "Interpellations", "Postulats", "Motions"],
        )
        self.assertIn(
            "Préavis municipaux",
            available_document_type_labels("La Tour-de-Peilz"),
        )

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

    def test_html_source_is_labeled_as_an_official_link(self):
        metadata = {
            "commune": "Montreux",
            "source_url": "https://www.conseilmontreux.ch/documents/recherche/?id=42",
        }

        self.assertEqual(source_link_label(metadata), "Lien officiel")
        linked = link_source_mentions(
            "Reponse disponible (Source 1).",
            [{"metadata": metadata}],
        )
        self.assertIn(
            "[Lien officiel](https://www.conseilmontreux.ch/documents/recherche/?id=42)",
            linked,
        )

    def test_download_endpoint_is_still_labeled_as_pdf(self):
        self.assertEqual(
            source_link_label(
                {
                    "source_url": (
                        "https://conseil.vevey.ch/ConseilCommunal/"
                        "download.asp?d=5981"
                    )
                }
            ),
            "PDF",
        )


if __name__ == "__main__":
    unittest.main()
