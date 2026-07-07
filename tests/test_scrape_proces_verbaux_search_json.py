from __future__ import annotations

import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "scrape-la-tour-de-peilz" / "scrape_proces_verbaux_search_json_2021_2026.py"
SPEC = importlib.util.spec_from_file_location("pv_search", MODULE_PATH)
pv_search = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(pv_search)


def test_parse_title() -> None:
    assert pv_search.parse_title("Procès-verbal | N° 35 | de la séance du 6 mai 2026") == (35, "2026-05-06")


def test_parse_result_html() -> None:
    source = '''<div class="ik-callout-info ik-callout">
    <a href="/tools/pdf-viewer/web/viewer.php?file=https://www.la-tour-de-peilz.ch/doc_uploads/images/politique/conseil-communal/proces-verbaux/legislature_2021-2026/PV35-06-05-2026.pdf">
    <h4>Procès-verbal | N° 35 | de la séance du 6 mai 2026</h4></a>
    <div class="lssrchres"></div><div>Procès verbaux / Législature Législature 2021-2026</div></div>'''
    records = pv_search.parse_result_html(source)
    assert len(records) == 1
    assert records[0]["pv_number"] == 35
    assert records[0]["session_date"] == "2026-05-06"
    assert records[0]["document_family"] == "council_session"


def test_ignore_other_legislature() -> None:
    source = '''<div class="ik-callout-info"><a href="https://example.test/PV01.pdf">
    <h4>Procès-verbal | N° 1 | de la séance du 30 juin 2011</h4></a>
    <div class="lssrchres"></div><div>Procès verbaux / Législature Législature 2011-2016</div></div>'''
    assert pv_search.parse_result_html(source) == []
