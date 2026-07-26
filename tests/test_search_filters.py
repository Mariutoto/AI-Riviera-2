from __future__ import annotations

import unittest

from app.pilot_v2_store import CATEGORY_MAP, _filter_clauses


class SearchFilterTests(unittest.TestCase):
    def test_every_ui_document_type_maps_to_a_database_category(self):
        expected = {
            "interpellations": "interpellation",
            "postulats": "postulat",
            "motions": "motion",
            "preavis-municipaux": "preavis_municipal",
            "proces-verbaux": "proces_verbal",
            "budget": "budget",
            "rapports-gestion": "rapport_gestion",
            "rapports-comptes": "rapport_comptes",
            "reglement-conseil-communal": "reglement_conseil_communal",
        }
        for label, category in expected.items():
            self.assertEqual(CATEGORY_MAP[label], category)

    def test_year_and_document_type_become_sql_filters(self):
        clauses, params = _filter_clauses(
            {"year": "2025", "doc_type": "proces-verbaux"}
        )
        self.assertEqual(clauses[0], "d.category = %s")
        self.assertIn("listing_year", clauses[1])
        self.assertEqual(params, ["proces_verbal", "2025"])


if __name__ == "__main__":
    unittest.main()
