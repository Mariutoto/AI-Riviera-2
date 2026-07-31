import unittest

from app.search_guard import filter_guard_message


class SearchGuardTests(unittest.TestCase):
    def guard(self, question, city="all", year="Toutes", document_type="Tous"):
        return filter_guard_message(
            question,
            selected_city=city,
            selected_year=year,
            selected_document_type=document_type,
        )

    def test_blocks_conflicting_year(self):
        warning = self.guard(
            "Quelles interpellations ont reçu une réponse en 2025 ?",
            year="2024",
        )
        self.assertIn("2025", warning)
        self.assertIn("2024", warning)

    def test_all_years_accepts_explicit_year(self):
        self.assertIsNone(
            self.guard("Quelles interpellations ont reçu une réponse en 2025 ?")
        )

    def test_blocks_conflicting_document_type(self):
        warning = self.guard(
            "Quelles interpellations ont reçu une réponse ?",
            document_type="Postulats",
        )
        self.assertIn("Interpellations", warning)
        self.assertIn("Postulats", warning)

    def test_montreux_interpellations_are_available(self):
        self.assertIsNone(
            self.guard(
                "Quelles interpellations ont reçu une réponse à Montreux ?",
                city="Montreux",
                document_type="Interpellations",
            )
        )

    def test_montreux_postulates_are_available(self):
        self.assertIsNone(
            self.guard(
                "Quels postulats ont été déposés à Montreux ?",
                city="Montreux",
                document_type="Postulats",
            )
        )

    def test_vevey_motions_and_postulates_are_available(self):
        self.assertIsNone(
            self.guard(
                "Quels postulats ont été déposés à Vevey ?",
                city="Vevey",
                document_type="Postulats",
            )
        )
        self.assertIsNone(
            self.guard(
                "Quelles motions ont été déposées à Vevey ?",
                city="Vevey",
                document_type="Motions",
            )
        )

    def test_vevey_is_available(self):
        self.assertIsNone(
            self.guard(
                "Quelles interpellations ont reçu une réponse en 2025 à Vevey ?",
                city="Vevey",
                year="2025",
                document_type="Interpellations",
            )
        )

    def test_blocks_asr_alias_while_it_is_upcoming(self):
        warning = self.guard("Quels documents sont disponibles pour l’ASR ?")

        self.assertIn("Association Sécurité Riviera", warning)
        self.assertIn("pas encore disponibles", warning)

    def test_matching_filters_are_accepted(self):
        self.assertIsNone(
            self.guard(
                "Quelles interpellations ont reçu une réponse en 2025 à La Tour-de-Peilz ?",
                city="La Tour-de-Peilz",
                year="2025",
                document_type="Interpellations",
            )
        )


if __name__ == "__main__":
    unittest.main()
