from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load(name: str, relative_path: str):
    path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_curated_minutes_sources_are_unique_and_in_scope():
    module = load(
        "vevey_full_scraper",
        "scrape-vevey/scrape_interpellations_2021_2026.py",
    )
    keys = [
        (row["listing_date"], row["author"], row["object_title"])
        for row in module.EARLY_SOURCES
    ]
    assert len(keys) == 40
    assert len(set(keys)) == len(keys)
    assert all("2021-07-01" <= row["listing_date"] <= "2026-06-30" for row in module.EARLY_SOURCES)


def test_plural_response_is_not_classified_as_an_original():
    module = load(
        "vevey_pilot_classifier",
        "scrape-vevey/scrape_interpellations_pilot.py",
    )
    documents = [{
        "title": "Interpellations de deux conseillères",
        "text_audit": {
            "text_preview": "Réponse aux interpellations de Mme A et Mme B"
        },
    }]
    module.classify_document_roles(documents)
    assert documents[0]["document_role"] == "response"


def test_response_header_is_not_mistaken_for_an_appended_original():
    module = load(
        "vevey_general_audit",
        "audit-vevey/interpellations-pilot/build_general_audit.py",
    )
    assert not module.appended_interpellation_page(
        "RI 09/2023 Réponse à l’interpellation de Mme Exemple. "
        "L’interpellation déposée demandait plusieurs précisions."
    )
    assert module.appended_interpellation_page(
        "Vevey, le 20 septembre 2023 Interpellation : un titre officiel"
    )


def test_embedding_records_are_deduplicated_by_content_hash():
    module = load(
        "vevey_embedding_inputs",
        "audit-vevey/generate_embedding_inputs.py",
    )
    base = {
        "content_hash": "same",
        "document_role": "combined_interpellation_response",
        "document_id": "combined",
        "chunk_id": "combined#chunk-000",
    }
    original = {
        **base,
        "document_role": "interpellation_text",
        "document_id": "original",
        "chunk_id": "original#chunk-000",
    }
    kept, dropped = module.deduplicate_content([base, original])
    assert [row["chunk_id"] for row in kept] == ["original#chunk-000"]
    assert dropped[0]["duplicate_of_chunk_id"] == "original#chunk-000"
