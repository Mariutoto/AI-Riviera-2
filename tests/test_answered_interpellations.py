from __future__ import annotations

import unittest

from app import pilot_v2_store, retrieval


def metadata(
    *,
    commune: str,
    file_url: str,
    responses: list[dict] | None = None,
    response_ids: list[str] | None = None,
) -> dict:
    return {
        "commune": commune,
        "file_url": file_url,
        "additional_metadata": {
            "interpellation_metadata": {
                "authors": [{"name": "Mme Exemple"}],
                "political_status": (
                    "response_available" if responses else "filed"
                ),
                "responses": responses or [],
            },
            "relationships": {
                "response_status": (
                    "response_available"
                    if responses
                    else "not_collected_yet"
                ),
                "response_document_ids": response_ids or [],
            },
        },
    }


class AnsweredInterpellationsTests(unittest.TestCase):
    def test_detects_response_enumeration_and_uses_response_year(self):
        filters = retrieval.detect_answered_interpellations_query(
            "Quelles interpellations ont reçu une réponse en 2025 ?"
        )

        self.assertEqual(
            filters,
            {
                "doc_type": "interpellations",
                "response_available": True,
                "response_year": "2025",
            },
        )

    def test_does_not_capture_single_interpellation_content_question(self):
        self.assertIsNone(
            retrieval.detect_answered_interpellations_query(
                "Quelle réponse a reçu l’interpellation sur la mobilité ?"
            )
        )

    def test_detects_general_political_document_enumeration(self):
        self.assertEqual(
            retrieval.detect_aggregate_query(
                "Quels postulats ont été déposés en 2024 ?"
            ),
            {"doc_type": "postulats", "year": "2024"},
        )

    def test_excludes_unanswered_and_wrong_response_year(self):
        objects = [
            {
                "document_id": "unanswered",
                "title": "Sans réponse",
                "category": "interpellation",
                "document_role": "interpellation_text",
                "summary": None,
                "metadata": metadata(
                    commune="Vevey",
                    file_url="https://example.test/unanswered.pdf",
                ),
            },
            {
                "document_id": "answered-2026",
                "title": "Réponse en 2026",
                "category": "interpellation",
                "document_role": "interpellation_text",
                "summary": None,
                "metadata": metadata(
                    commune="Vevey",
                    file_url="https://example.test/object-2026.pdf",
                    responses=[
                        {
                            "response_number": "1/2026",
                            "response_date": "2026-02-05",
                        }
                    ],
                    response_ids=["response-2026"],
                ),
            },
        ]
        response_documents = {
            "response-2026": {
                "document_id": "response-2026",
                "metadata": metadata(
                    commune="Vevey",
                    file_url="https://example.test/response-2026.pdf",
                    responses=[
                        {
                            "response_number": "1/2026",
                            "response_date": "2026-02-05",
                        }
                    ],
                ),
            }
        }

        selected = pilot_v2_store.select_answered_interpellations(
            objects,
            response_documents,
            {"response_year": "2025"},
        )

        self.assertEqual(selected, [])

    def test_prefers_response_pdf_without_crossing_municipalities(self):
        vevey_response_metadata = metadata(
            commune="Vevey",
            file_url="https://example.test/vevey-response.pdf",
            responses=[
                {
                    "response_number": "9/2025",
                    "response_date": "2025-10-02",
                }
            ],
        )
        vevey_response_metadata["source_title"] = (
            "Interpellation de Mme Giuliana de Regibus (PS), "
            "intitulée « L’apprentissage en question »"
        )
        vevey_response_metadata["additional_metadata"]["relationships"][
            "official_reference"
        ] = "2025/ri09"
        objects = [
            {
                "document_id": "vevey-object",
                "title": (
                    "Int_2025-09-04_De-Regibus-G_"
                    "Apprentissage-en-question.pdf"
                ),
                "category": "interpellation",
                "document_role": "interpellation_text",
                "summary": None,
                "metadata": metadata(
                    commune="Vevey",
                    file_url="https://example.test/vevey-object.pdf",
                    responses=[
                        {
                            "response_number": "9/2025",
                            "response_date": "2025-10-02",
                        }
                    ],
                    response_ids=["vevey-response"],
                ),
            },
            {
                "document_id": "tour-combined",
                "title": "Modifications d’ordonnances fédérales",
                "category": "interpellation",
                "document_role": "combined_interpellation_response",
                "summary": None,
                "metadata": metadata(
                    commune="La Tour-de-Peilz",
                    file_url="https://example.test/tour-combined.pdf",
                    responses=[
                        {
                            "response_number": "3/2025",
                            "response_date": "2025-12-10",
                        }
                    ],
                ),
            },
        ]
        response_documents = {
            "vevey-response": {
                "document_id": "vevey-response",
                "document_role": "municipal_response",
                "content": (
                    "RI 09/2025. Vevey, le 15 septembre 2025. "
                    "Réponse à l’interpellation."
                ),
                "metadata": vevey_response_metadata,
            }
        }

        selected = pilot_v2_store.select_answered_interpellations(
            objects,
            response_documents,
            {"response_year": "2025"},
        )

        self.assertEqual(len(selected), 2)
        by_city = {row["commune"]: row for row in selected}
        self.assertEqual(
            by_city["Vevey"]["response_url"],
            "https://example.test/vevey-response.pdf",
        )
        self.assertEqual(
            by_city["Vevey"]["title"],
            "Interpellation de Mme Giuliana de Regibus (PS), "
            "intitulée « L’apprentissage en question »",
        )
        self.assertEqual(
            by_city["Vevey"]["political_date"],
            "2025-09-04",
        )
        self.assertEqual(
            by_city["Vevey"]["response_reference"],
            "2025/ri09",
        )
        self.assertEqual(
            by_city["Vevey"]["response_date"],
            "2025-09-15",
        )
        self.assertEqual(
            by_city["La Tour-de-Peilz"]["response_url"],
            "https://example.test/tour-combined.pdf",
        )


if __name__ == "__main__":
    unittest.main()
