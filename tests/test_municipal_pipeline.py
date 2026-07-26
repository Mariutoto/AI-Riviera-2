import unittest

from municipal_pipeline.documents import municipal_document, validate_document
from municipal_pipeline.municipalities import LA_TOUR_DE_PEILZ, VEVEY, get_municipality


class MunicipalityRegistryTests(unittest.TestCase):
    def test_keeps_city_specific_configuration_out_of_the_pipeline(self):
        self.assertTrue(LA_TOUR_DE_PEILZ.search_enabled)
        self.assertFalse(VEVEY.search_enabled)
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


if __name__ == "__main__":
    unittest.main()
