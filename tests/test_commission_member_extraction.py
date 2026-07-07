from __future__ import annotations

import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "scrape-la-tour-de-peilz" / "scrape_motions_2021_2026.py"
SPEC = importlib.util.spec_from_file_location("motion_scraper", MODULE_PATH)
motion_scraper = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(motion_scraper)


def test_members_after_avait_composee_de() -> None:
    text = """La commission chargée d'examiner l'objet cité en titre était composée de :
Mmes Sophie Blank Barbezat
Nathalie Demarta
Elise Kaiser
MM.
Alessio Grutta, en remplacement de Kurt Egli
Jacques Vallotton
Gilbert Vernez
Alois Raemy, président-rapporteur
La commission s'est réunie le mercredi 19 mai 2021.
"""
    members = motion_scraper.extract_commission_members(text)
    assert [member["name"] for member in members] == [
        "Sophie Blank Barbezat", "Nathalie Demarta", "Elise Kaiser",
        "Alessio Grutta", "Jacques Vallotton", "Gilbert Vernez", "Alois Raemy",
    ]
    assert members[-1]["role"] == "president_rapporteur"


def test_members_after_composition_mentionnee() -> None:
    text = """La commission s'est réunie le jeudi 20 mai 2021 à 19h30
suivant la composition mentionnée :
Costa François, président (PLR)
Christine Hausherr-de Maddalena (PLR)
Daoud Latif (PS)
Jimmy Suro (PS)
Jean-Pierre Belotti (UDC)
Geneviève Pasche (Les Verts)
Margareta Brüssow (PDC+I)
François Vodoz (hors parti)
La Municipalité est représentée par le syndic.
"""
    members = motion_scraper.extract_commission_members(text)
    assert len(members) == 8
    assert members[0]["name"] == "Costa François"
    assert members[0]["role"] == "president"


def test_finance_commission_with_implicit_meeting_subject() -> None:
    text = """La commission des finances composée de :
Mesdames
Sophie Blank Barbezat (excusée)
Fanny Limat
Messieurs
Paul Castelain
Guy Chervet
Nicolas Fardel, président-rapporteur
Philippe Neyroud
Jean-Yves Schmidhauser
s'est réunie le mardi 21 septembre afin d'étudier le préavis.
"""
    members = motion_scraper.extract_commission_members(text)
    assert len(members) == 7
    assert members[0]["attendance"] == "excused"
    assert members[4]["role"] == "president_rapporteur"
