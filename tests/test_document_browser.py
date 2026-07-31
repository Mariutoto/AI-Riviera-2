from __future__ import annotations

import unittest
from unittest.mock import patch

from app import pilot_v2_store


class _FakeCursor:
    def __init__(self, rows):
        self.rows = rows
        self.sql = ""
        self.params = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params):
        self.sql = sql
        self.params = list(params)

    def fetchall(self):
        return self.rows


class _FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self._cursor


class DocumentBrowserTests(unittest.TestCase):
    def test_browse_documents_combines_filters_and_normalizes_rows(self):
        cursor = _FakeCursor(
            [
                {
                    "document_id": "vevey-interpellation-1",
                    "title": "Mobilité scolaire",
                    "category": "interpellation",
                    "document_role": "interpellation_text",
                    "summary": "",
                    "metadata": {
                        "commune": "Vevey",
                        "document_date": "2025-09-04",
                        "pdf_url": "https://example.test/document.pdf",
                        "additional_metadata": {
                            "interpellation_metadata": {
                                "authors": [
                                    {
                                        "name": "Anne Exemple",
                                        "civility": "Mme",
                                        "party": "PS",
                                    }
                                ]
                            }
                        },
                    },
                }
            ]
        )
        connection = _FakeConnection(cursor)

        with patch.object(
            pilot_v2_store,
            "_connect",
            return_value=connection,
        ):
            rows = pilot_v2_store.browse_documents(
                query="mobilité école",
                cities=("Vevey", "Montreux"),
                categories=("interpellation", "motion"),
                year_from="2021",
                year_to="2025",
            )

        self.assertIn("metadata->>'commune' = ANY", cursor.sql)
        self.assertIn("category = ANY", cursor.sql)
        self.assertIn("title ILIKE", cursor.sql)
        self.assertIn(["Vevey", "Montreux"], cursor.params)
        self.assertIn(["interpellation", "motion"], cursor.params)
        self.assertIn("%mobilité%", cursor.params)
        self.assertIn("%école%", cursor.params)
        self.assertEqual(rows[0]["commune"], "Vevey")
        self.assertEqual(rows[0]["year"], "2025")
        self.assertEqual(rows[0]["authors"], ["Mme Anne Exemple (PS)"])
        self.assertEqual(
            rows[0]["source_url"],
            "https://example.test/document.pdf",
        )


if __name__ == "__main__":
    unittest.main()
