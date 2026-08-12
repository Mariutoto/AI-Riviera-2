from __future__ import annotations

import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

# Known ordering artifact, not a product bug: if this file runs in the same
# process as tests/test_ui_source_links.py (e.g. via `unittest discover`),
# both tests below fail with "st.chat_input() can't be used in a st.form()".
# Root cause: test_ui_source_links imports pure helpers straight from
# app.ui ("from app.ui import ..."), which executes the whole module body —
# including the Contact tab's st.form — in Streamlit's unsupported "bare
# mode" (no ScriptRunContext). In bare mode st.form's cleanup on `__exit__`
# is skipped, permanently leaking the form onto Streamlit's shared
# context_dg_stack for the rest of the test process, so every later
# AppTest run of app/ui.py sees a phantom open form. Confirmed by
# reproducing it standalone: `import app.ui` followed by any
# `AppTest.from_file("app/ui.py").run()` fails the same way, with no
# involvement of this file at all. Doesn't affect the deployed app (each
# `streamlit run` gets its own real ScriptRunContext) and doesn't affect
# running this file alone or via `python -m unittest tests.test_ui_document_tabs`.
# Real fix would be moving app.ui's pure/testable helpers into a module
# with no page-rendering side effects on import; tracked as a follow-up
# rather than done as part of this pass.


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
            ["Tous", "Interpellations", "Postulats", "Motions"],
        )
        self.assertEqual(len(app.exception), 0)

        self.assertIn(
            "Corsier-sur-Vevey — prochainement",
            app.selectbox[0].options,
        )
        self.assertIn("Villeneuve — prochainement", app.selectbox[0].options)
        self.assertNotIn(
            "Blonay–Saint-Légier — prochainement", app.selectbox[0].options
        )
        self.assertNotIn("Veytaux — prochainement", app.selectbox[0].options)

        content = "\n".join(
            str(element.value) for element in app.markdown
        )
        self.assertIn("ce qui reste en attente", content)
        self.assertIn("doublons possibles", content)
        self.assertIn("Centraliser l’accès régional", content)
        self.assertIn(
            "Communes et documents disponibles",
            [element.value for element in app.subheader],
        )

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
        self.assertIn("Villeneuve — prochainement", app.multiselect[0].options)
        self.assertNotIn("Veytaux — prochainement", app.multiselect[0].options)
        text_input_labels = [widget.label for widget in app.text_input]
        self.assertIn("Recherche par mots-clés", text_input_labels)
        self.assertIn("De l’année", text_input_labels)
        self.assertIn("À l’année", text_input_labels)


if __name__ == "__main__":
    unittest.main()
