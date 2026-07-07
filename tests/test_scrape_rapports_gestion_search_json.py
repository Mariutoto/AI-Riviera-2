from __future__ import annotations

import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "scrape-la-tour-de-peilz" / "scrape_rapports_gestion_search_json_2021_2026.py"
SPEC = importlib.util.spec_from_file_location("gestion_search", MODULE_PATH)
gestion_search = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(gestion_search)


def fixture(year: str = "2025") -> str:
    return f'''<div class="ik-callout-info"><a href="/tools/pdf-viewer/web/viewer.php?file=https://www.la-tour-de-peilz.ch/doc_uploads/images/politique/municipalite/rapport-de-gestion/Rapport-de-gestion-{year}-complet.pdf">
    <h4>Rapport de gestion</h4></a><div class="lssrchres">{year} + Rapport de la commission de gestion + Réponse de la Municipalité</div>
    <div>Rapport de gestion – Comptes – Budget / {year}</div></div>'''


def test_parse_management_report() -> None:
    records = gestion_search.parse_result_html(fixture())
    assert len(records) == 1
    assert records[0]["management_year"] == 2025
    assert records[0]["period_start"] == "2025-01-01"
    assert records[0]["category"] == "rapport_gestion"


def test_ignore_year_outside_legislature() -> None:
    assert gestion_search.parse_result_html(fixture("2020")) == []


def test_ignore_budget_document() -> None:
    source = fixture().replace("Rapport de gestion</h4>", "Budget</h4>")
    assert gestion_search.parse_result_html(source) == []
