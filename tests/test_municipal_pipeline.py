import unittest

from municipal_pipeline.documents import municipal_document, validate_document
from municipal_pipeline.municipalities import (
    ASSOCIATION_SECURITE_RIVIERA,
    BLONAY_SAINT_LEGIER,
    LA_TOUR_DE_PEILZ,
    MONTREUX,
    VEVEY,
    VEYTAUX,
    get_municipality,
)
from municipal_pipeline.preindex_audit import audit_preindex


class MunicipalityRegistryTests(unittest.TestCase):
    def test_tracks_search_availability_per_city(self):
        self.assertTrue(LA_TOUR_DE_PEILZ.search_enabled)
        self.assertIn(
            "preavis-municipaux", LA_TOUR_DE_PEILZ.document_types
        )
        self.assertTrue(VEVEY.search_enabled)
        self.assertEqual(
            VEVEY.search_scope,
            "interpellations, motions et postulats",
        )
        self.assertEqual(
            set(VEVEY.document_types),
            {"interpellations", "motions", "postulats"},
        )
        self.assertTrue(MONTREUX.search_enabled)
        self.assertEqual(
            MONTREUX.search_scope, "interpellations, motions et postulats"
        )
        self.assertEqual(
            set(MONTREUX.document_types),
            {"interpellations", "motions", "postulats"},
        )
        self.assertEqual(
            MONTREUX.source_domain, "www.conseilmontreux.ch"
        )
        self.assertEqual(
            MONTREUX.document_types,
            ("interpellations", "motions", "postulats"),
        )
        self.assertFalse(ASSOCIATION_SECURITE_RIVIERA.search_enabled)
        self.assertTrue(BLONAY_SAINT_LEGIER.search_enabled)
        self.assertEqual(
            BLONAY_SAINT_LEGIER.search_scope,
            "interpellations, motions et postulats",
        )
        self.assertEqual(
            BLONAY_SAINT_LEGIER.document_types,
            ("interpellations", "motions", "postulats"),
        )
        self.assertEqual(
            BLONAY_SAINT_LEGIER.source_domain, "www.blonay-saint-legier.ch"
        )
        self.assertTrue(VEYTAUX.search_enabled)
        self.assertEqual(VEYTAUX.search_scope, "interpellations et motions")
        self.assertEqual(
            VEYTAUX.document_types, ("interpellations", "motions")
        )
        self.assertEqual(VEYTAUX.source_domain, "veytaux.ch")
        self.assertEqual(get_municipality("vevey"), VEVEY)

    def test_unknown_city_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "inconnue"):
            get_municipality("unknown")


class MunicipalDocumentTests(unittest.TestCase):
    def test_builds_shared_metadata_contract(self):
        document = municipal_document(
            municipality=VEVEY,
            category="interpellation",
            title="Une interpellation pilote",
            listing_year="2026",
            pdf_url="https://example.test/document.pdf",
            source_page="https://example.test/documents",
        )

        self.assertEqual(document["city"], "Vevey")
        self.assertEqual(document["city_key"], "vevey")
        self.assertEqual(document["document_type"], "interpellation")

    def test_rejects_missing_required_metadata(self):
        with self.assertRaisesRegex(ValueError, "pdf_url"):
            validate_document(
                {
                    "city": "Vevey",
                    "city_key": "vevey",
                    "category": "interpellation",
                    "document_type": "interpellation",
                    "title": "Test",
                    "listing_year": "2026",
                    "source_page": "https://example.test",
                }
            )


class PreindexAuditTests(unittest.TestCase):
    def document(self, **overrides):
        document = {
            "city": "Vevey",
            "city_key": "vevey",
            "category": "interpellation",
            "document_type": "interpellation",
            "title": "Une interpellation",
            "listing_year": "2026",
            "listing_date": "2026-05-07",
            "author": "Jeanne Exemple",
            "pdf_url": "https://example.test/document.pdf",
            "source_page": "https://example.test/documents",
            "document_id": "vevey_interpellation_test",
            "document_role": "political_object",
            "listing_occurrences": [{}],
            "text_audit": {
                "page_count": 2,
                "text_chars": 2000,
                "text_words": 300,
                "empty_pages": 0,
                "text_preview": "Texte extrait",
                "needs_ocr": False,
            },
        }
        document.update(overrides)
        return document

    def test_ready_document_has_no_findings(self):
        report = audit_preindex([self.document()], {"failed_downloads": []})
        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["summary"]["blockers"], 0)

    def test_ocr_is_blocking(self):
        document = self.document(
            text_audit={
                "page_count": 2,
                "text_chars": 0,
                "text_words": 0,
                "empty_pages": 2,
                "text_preview": "",
                "needs_ocr": True,
            }
        )
        report = audit_preindex([document], {"failed_downloads": []})
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["summary"]["finding_codes"]["ocr_required"], 1)

    def test_recovered_broken_link_is_only_a_warning(self):
        diagnostics = {
            "failed_downloads": [
                {
                    "pdf_url": "https://example.test/broken",
                    "equivalent_document_id": "vevey_interpellation_test",
                }
            ],
            "recoverable_failed_downloads": 1,
        }
        report = audit_preindex([self.document()], diagnostics)
        self.assertEqual(report["status"], "ready_with_warnings")
        self.assertEqual(report["summary"]["blockers"], 0)


if __name__ == "__main__":
    unittest.main()
