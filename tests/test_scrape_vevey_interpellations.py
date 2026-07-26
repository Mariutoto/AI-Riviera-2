import importlib.util
import unittest
from pathlib import Path
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scrape-vevey" / "scrape_interpellations_pilot.py"
SPEC = importlib.util.spec_from_file_location("scrape_vevey_interpellations", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(module)


def teaser(
    *,
    date="07.05.2026",
    author="Jérôme Christen",
    reference="",
    title="Interpellation pour une meilleure politique alimentaire",
    download_id="6123",
):
    return f"""
    <div class="teaser-politique text-sm row border-bottom mb-2 mx-0 py-3">
      <div class="col-3 pl-0"><p>{date}<br>{author}</p></div>
      <div class="col-9 col-md-7">
        <p class="text-black text-uppercase mb-2 text-xs teaser-id">
          <span>Interpellation</span><span>{reference}</span>
        </p>
        <h4><a href="https://conseil.vevey.ch/ConseilCommunal/download.asp?d={download_id}">{title}</a></h4>
        <div class="teaser-links mt-2">
          <a href="https://conseil.vevey.ch/ConseilCommunal/download.asp?d={download_id}">
            <svg><use></use></svg>Télécharger PDF (71 Ko)
          </a>
        </div>
      </div>
    </div>
    """


class VeveyInterpellationPilotTests(unittest.TestCase):
    def test_parses_metadata_and_download_url(self):
        item = module.parse_page(teaser())[0]
        self.assertEqual(item["city"], "Vevey")
        self.assertEqual(item["listing_date"], "2026-05-07")
        self.assertEqual(item["author"], "Jérôme Christen")
        self.assertEqual(item["source_download_id"], "6123")
        self.assertEqual(item["document_type"], "interpellation")

    def test_rejects_out_of_scope_year(self):
        self.assertEqual(module.parse_page(teaser(date="07.05.2024")), [])
        self.assertEqual(len(module.parse_page(teaser(date="07.05.2024"), years=None)), 1)

    def test_extracts_multiple_balanced_teasers(self):
        page = teaser(download_id="1") + teaser(download_id="2")
        self.assertEqual(len(module.extract_teaser_blocks(page)), 2)
        self.assertEqual(len(module.parse_page(page)), 2)

    def test_download_audit_groups_identical_pdf_content(self):
        content = b"%PDF-1.4 identical"
        response = Mock(content=content)
        response.raise_for_status.return_value = None
        session = Mock()
        session.get.return_value = response
        items = module.parse_page(
            teaser(download_id="1") + teaser(download_id="2", date="08.05.2026")
        )

        documents, diagnostics = module.download_audit(items, session=session)

        self.assertEqual(len(documents), 1)
        self.assertEqual(len(documents[0]["listing_occurrences"]), 2)
        self.assertEqual(diagnostics["duplicate_listing_occurrences"], 1)
        self.assertEqual(diagnostics["documents_needing_ocr"], 1)
        self.assertTrue(diagnostics["complete"])
        self.assertTrue(diagnostics["usable_complete"])


if __name__ == "__main__":
    unittest.main()
