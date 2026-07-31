from __future__ import annotations

import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest


class UiDocumentTabsTests(unittest.TestCase):
    def test_tabs_and_city_specific_document_filters(self):
        app = AppTest.from_file(
            "app/ui.py", default_timeout=20
        ).run()

        self.assertEqual(len(app.exception), 0)
        self.assertEqual(
            [tab.label for tab in app.tabs],
            ["Assistant", "Documents", "À propos", "Contact"],
        )
        self.assertEqual(
            app.chat_input[0].placeholder,
            "Posez une question sur les documents publics de la Riviera vaudoise.",
        )
        self.assertEqual(
            app.expander[0].label,
            "Affiner la recherche (facultatif)",
        )

        app.selectbox[0].set_value("Vevey").run()
        self.assertEqual(
            app.selectbox[2].options,
            ["Tous", "Interpellations", "Postulats", "Motions"],
        )

        app.selectbox[0].set_value("Montreux").run()
        self.assertEqual(
            app.selectbox[2].options,
            ["Tous", "Interpellations", "Postulats"],
        )
        self.assertEqual(len(app.exception), 0)

        self.assertIn(
            "Blonay–Saint-Légier — prochainement",
            app.selectbox[0].options,
        )
        self.assertIn("Villeneuve — prochainement", app.selectbox[0].options)

        content = "\n".join(
            str(element.value) for element in app.markdown
        )
        self.assertIn("plusieurs années après le dépôt", content)
        self.assertIn("doublons possibles", content)
        self.assertIn("Accès régional centralisé", content)
        self.assertIn("Économies d’échelle", content)

    def test_document_browser_uses_multi_select_filters(self):
        with patch("app.pilot_v2_store.ready", return_value=False):
            app = AppTest.from_file(
                "app/ui.py", default_timeout=20
            ).run()
            app.session_state["main-navigation"] = "Documents"
            app.run()

        self.assertEqual(len(app.exception), 0)
        self.assertEqual(
            [widget.label for widget in app.multiselect],
            ["Communes", "Types de document"],
        )
        self.assertIn(
            "Corsier-sur-Vevey — prochainement",
            app.multiselect[0].options,
        )
        self.assertIn("Veytaux — prochainement", app.multiselect[0].options)
        text_input_labels = [widget.label for widget in app.text_input]
        self.assertIn("Recherche par mots-clés", text_input_labels)
        self.assertIn("De l’année", text_input_labels)
        self.assertIn("À l’année", text_input_labels)


if __name__ == "__main__":
    unittest.main()
