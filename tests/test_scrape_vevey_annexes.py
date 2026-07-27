from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scrape-vevey" / "scrape_annexes_pilot.py"
SPEC = importlib.util.spec_from_file_location("scrape_vevey_annexes", SCRIPT)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def teaser(
    *,
    reference: str = "2026/RI09",
    title: str = "Réponse à l'interpellation sur la mobilité",
    download_id: str = "7001",
) -> str:
    return f"""
    <div class="teaser-politique text-sm row border-bottom mb-2 mx-0 py-3">
      <div class="col-3 pl-0"><p>07.05.2026<br></p></div>
      <div class="col-9 col-md-7">
        <p class="teaser-id"><span>Annexe</span><span>{reference}</span></p>
        <h4><a href="https://conseil.vevey.ch/ConseilCommunal/download.asp?d={download_id}">{title}</a></h4>
        <a href="https://conseil.vevey.ch/ConseilCommunal/download.asp?d={download_id}">
          Télécharger PDF
        </a>
      </div>
    </div>
    """


class VeveyAnnexesTests(unittest.TestCase):
    def test_parses_annexe_with_common_document_contract(self):
        item = module.parse_page(teaser())[0]

        self.assertEqual(item["city"], "Vevey")
        self.assertEqual(item["document_type"], "annexe")
        self.assertEqual(item["source_collection"], "vevey-council-annexes")
        self.assertEqual(item["reference"], "2026/RI09")

    def test_selects_ri_reference_as_response_candidate(self):
        candidates = module.select_response_candidates(
            module.parse_page(teaser())
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["candidate_reason"], "ri_reference")

    def test_does_not_select_unrelated_annexe(self):
        items = module.parse_page(
            teaser(reference="2026/RP19", title="Rapport de commission")
        )

        self.assertEqual(module.select_response_candidates(items), [])

    def test_classifies_response_and_duplicate_object_after_pdf_read(self):
        documents = [
            {
                "text_audit": {
                    "text_preview": "RI 09/2026 Réponse à l’interpellation de Mme Exemple"
                }
            },
            {
                "text_audit": {
                    "text_preview": "INTERPELLATION Mobilité et sécurité à Vevey"
                }
            },
        ]

        module.classify_candidate_roles(documents)

        self.assertEqual(documents[0]["document_role"], "municipal_response")
        self.assertEqual(
            documents[1]["document_role"], "interpellation_text_duplicate"
        )


if __name__ == "__main__":
    unittest.main()
