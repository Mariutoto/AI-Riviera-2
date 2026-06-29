import unittest

from app.structured import (
    requested_object_types,
    status_filter_from_question,
    wants_objects_by_status,
)


class StatusQuestionRoutingTests(unittest.TestCase):
    def test_postulations_en_suspens_is_recognized(self):
        question = "Quels sont les postulations en suspens ?"

        self.assertEqual(requested_object_types(question), {"postulat"})
        self.assertEqual(status_filter_from_question(question)[0], "po.status_is_final = FALSE")
        self.assertTrue(wants_objects_by_status(question))

    def test_common_postulat_typo_is_recognized(self):
        self.assertEqual(requested_object_types("Liste les posutals pending"), {"postulat"})

    def test_common_interpellation_typo_is_recognized(self):
        self.assertEqual(requested_object_types("Interppelations sans réponse"), {"interpellation"})

    def test_interpellations_without_response_use_awaiting_response(self):
        sql, _ = status_filter_from_question("Quelles interpellations sont sans réponse ?")

        self.assertIn("awaiting_response", sql)
        self.assertIn("interpellation", sql)

    def test_motions_waiting_for_municipality_use_pending_stage(self):
        sql, _ = status_filter_from_question("Quelles motions attendent la Municipalité ?")

        self.assertIn("pending_municipality_response", sql)
        self.assertIn("pending_municipality", sql)

    def test_answered_interpellations_are_recognized(self):
        sql, _ = status_filter_from_question("Quelles interpellations ont reçu une réponse ?")

        self.assertIn("response_available", sql)
        self.assertTrue(wants_objects_by_status("Quelles interpellations ont reçu une réponse ?"))

    def test_closed_motions_use_final_flag(self):
        sql, _ = status_filter_from_question("Liste les motions terminées")

        self.assertEqual(sql, "po.status_is_final = TRUE")


if __name__ == "__main__":
    unittest.main()
