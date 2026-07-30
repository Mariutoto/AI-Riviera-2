from __future__ import annotations

import unittest

from streamlit.testing.v1 import AppTest


class UiDocumentTabsTests(unittest.TestCase):
    def test_tabs_and_city_specific_document_filters(self):
        app = AppTest.from_file(
            "app/ui.py", default_timeout=20
        ).run()

        self.assertEqual(len(app.exception), 0)
        self.assertEqual(
            [tab.label for tab in app.tabs],
            ["Assistant", "Documents disponibles", "À propos"],
        )

        app.selectbox[0].set_value("Vevey").run()
        self.assertEqual(
            app.selectbox[2].options,
            ["Tous", "Interpellations", "Postulats", "Motions"],
        )

        app.selectbox[0].set_value("Montreux").run()
        self.assertEqual(
            app.selectbox[2].options,
            ["Tous", "Interpellations"],
        )
        self.assertEqual(len(app.exception), 0)

        content = "\n".join(
            str(element.value) for element in app.markdown
        )
        self.assertIn("152 disposent", content)
        self.assertIn("plusieurs années après le dépôt", content)
        self.assertIn("doublons possibles", content)


if __name__ == "__main__":
    unittest.main()
