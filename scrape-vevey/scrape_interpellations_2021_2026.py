from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import fitz
import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

PILOT_PATH = Path(__file__).with_name("scrape_interpellations_pilot.py")
SPEC = importlib.util.spec_from_file_location("vevey_interpellations_pilot", PILOT_PATH)
assert SPEC and SPEC.loader
pilot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pilot)

from municipal_pipeline.pdf_audit import inspect_pdf_text
from municipal_pipeline.preindex_audit import audit_preindex, write_preindex_html


SOURCE_PAGE = pilot.SOURCE_PAGE
LEGISLATURE_START = "2021-07-01"
LEGISLATURE_END = "2026-06-30"
HEADERS = {"User-Agent": "AI-Riviera Vevey interpellations 2021-2026"}
SEARCH_PAGE_SIZE = 20


def early(
    source: str,
    pages: tuple[int, int],
    date: str,
    author: str,
    title: str,
    party: str = "",
) -> dict:
    return {
        "source": source,
        "pages": pages,
        "listing_date": date,
        "author": author,
        "party": party,
        "object_title": title,
    }


# Avant février 2023, Vevey annexait généralement les interventions aux
# procès-verbaux signés au lieu de publier chaque texte dans le filtre
# « Interpellation ». Les pages ci-dessous ont été contrôlées séance par séance.
EARLY_SOURCES = [
    early("4709", (6, 6), "2021-09-09", "Serge Ansermet", "Quelques questions sur la sécurité des réseaux informatiques de l’administration veveysanne", "PS"),
    early("4709", (7, 7), "2021-09-09", "Sandra Marques", "Nos données personnelles en liberté… jamais !", "PLR"),
    early("4709", (8, 9), "2021-09-09", "Fabienne Despot", "Cyber-emmentaler", "UDC"),
    early("4709", (10, 10), "2021-09-09", "Vincent Matthys", "Et si cela nous arrive ? Une cyberattaque vue sous l’angle de la communication", "PS"),
    early("4709", (11, 11), "2021-09-09", "Loïc Brawand", "Pourquoi une roulotte à la place du Marché", "PLR"),
    early("4736", (13, 14), "2021-10-07", "Jérôme Christen et Marion Houriet", "La foire de la Saint-Martin ne mérite-t-elle de l’intérêt et de l’attention même sans strass ni paillettes ?", "Interpartis"),
    early("4736", (16, 16), "2021-10-07", "Mickael Bertschy", "Quid de la résolution de janvier à propos du Cabinet cantonal des estampes ?", "VL"),
    early("4736", (17, 18), "2021-10-07", "Jean-Marc Roduit", "Collège du cycle secondaire sur le terrain Copet 3", "LCVL"),
    early("4766", (18, 18), "2021-11-11", "Jérôme Christen", "Des chalands sans places et des places sans marchands", "VL"),
    early("4766", (19, 20), "2021-11-11", "Elise Carruzzo Evéquoz", "Repenser les cours d’école : vers plus de mixité et de végétalisation"),
    early("4766", (21, 21), "2021-11-11", "Elise Carruzzo Evéquoz", "Fourchouette : une maison sans enfants"),
    early("4766", (23, 23), "2021-11-11", "Céline Amiguet", "Une aide bienwenue pour les commerçants veveysans", "PS"),
    early("4766", (24, 24), "2021-11-11", "Caroline Gigon", "Une déchèterie à revaloriser !", "PS"),
    early("4766", (27, 27), "2021-11-11", "Colin Wahli", "Gold Label – tout ce qui brille n’est pas d’or"),
    early("4791", (28, 28), "2021-11-11", "Colin Wahli", "Bloquer des espaces piétons toute la semaine pour faire du tourisme motorisé ?"),
    early("4791", (29, 29), "2021-11-11", "Interpartis Les Vert·e·s, PS et da.", "Zones 30 km/h : éloge de la lenteur", "Interpartis"),
    early("4791", (30, 30), "2021-12-09", "Bastien Schobinger", "Retour de la mendicité dans nos rues", "UDC"),
    early("4791", (31, 32), "2021-12-09", "Stéphane Molliat", "Excès de zèle à l’urbanisme ?", "VL"),
    early("4811", (11, 11), "2022-02-03", "Philippe Herminjard", "Encourageons les marchands du marché de Vevey pour soutenir le développement de notre marché", "PLR"),
    early("4834", (19, 20), "2022-03-17", "Marianne Ghorayeb", "Une vision d’avenir pour le stade de Copet 3", "EAV"),
    early("4834", (23, 23), "2022-03-17", "Guillaume Pilloud", "Gratuité des places de parc sur la place du Marché", "UDC"),
    early("4834", (24, 24), "2022-03-17", "Loïc Brawand", "Passage du Tour de France", "PLR"),
    early("4867", (25, 25), "2022-05-19", "Patrick Bertschy", "Entre l’arrêt, l’abri de bus et les pavés…", "PLR"),
    early("4894", (32, 32), "2022-05-19", "Pierre-Alexandre Fürst", "Démarche participative : réelle prise de température auprès de la population ou blanc-seing pour la Municipalité…", "PLR"),
    early("4894", (33, 34), "2022-05-19", "Sarah Dohr", "Les bancs publics", "VL"),
    early("4894", (35, 35), "2022-06-23", "Sarah Dohr", "Festivalocal", "VL"),
    early("pv-septembre-2022", (20, 20), "2022-05-19", "Fabien Truffer", "Débarrassons les vélos abandonnés", "LCVL"),
    early("pv-septembre-2022", (21, 21), "2022-09-08", "Stéphane Molliat", "1er août, fête qui rassemble ou fête qui divise ?", "VL"),
    early("pv-septembre-2022", (22, 22), "2022-09-08", "Diane von Gunten et Valérie Zonca", "Pour éviter le délestage, il faut se délester de notre consommation", "Vert·e·s"),
    early("pv-septembre-2022", (23, 23), "2022-09-08", "Pierre Butty", "Quelles mesures pour le pouvoir d’achat des Veveysan·ne·s modestes ?", "PS"),
    early("pv-septembre-2022", (24, 24), "2022-09-08", "Anna Iamartino", "Mais où est donc passée la Fête des écoles ?", "PLR"),
    early("pv-septembre-2022", (25, 25), "2022-09-08", "Joëlle Minacci", "Urgence climatique et énergie : garder le cap et ne pas céder à la panique", "da."),
    early("pv-septembre-2022", (26, 26), "2022-09-08", "Patrick Bertschy", "Mise à l’enquête et délais… raisonnables !!!", "PLR"),
    early("4970", (19, 19), "2022-10-06", "Serge Ansermet", "Gros consommateurs communaux d’électricité et marché libre", "PS"),
    early("4970", (20, 20), "2022-10-06", "Pierre Butty", "Vevey a mal à sa permanence médicale", "PS"),
    early("4972", (14, 14), "2022-11-17", "Patrick Bertschy", "Un peu de lumière sur les 2 roues", "PLR"),
    early("pv-decembre-2022", (43, 44), "2022-12-15", "Fabien Truffer", "Mesures rapides et effets significatifs en faveur du climat", "LCVL"),
    early("pv-decembre-2022", (45, 46), "2022-12-15", "Fabien Truffer", "Pour un altruisme efficace", "LCVL"),
    early("pv-decembre-2022", (47, 49), "2022-12-15", "Jérôme Christen", "Des terrains de football naturels, pour rester (ou revenir) aux valeurs sûres !", "VL"),
    # Le texte original est l'annexe scannée du procès-verbal signé. Le
    # catalogue ne publie séparément que la réponse RI 04/2023.
    early("5417", (19, 19), "2023-03-16", "Adrien Colin", "Le monde associatif face à la hausse du coût de la vie", "da./Interpartis"),
]


SOURCE_URLS = {
    **{
        value: f"https://conseil.vevey.ch/ConseilCommunal/download.asp?d={value}"
        for value in {row["source"] for row in EARLY_SOURCES if row["source"].isdigit()}
    },
    "pv-septembre-2022": "https://www.vevey.ch/sites/default/files/council-meeting/minutes-session/2023-01/PV06_08.09.2022.pdf",
    "pv-decembre-2022": "https://www.vevey.ch/sites/default/files/council-meeting/minutes-session/2023-02/PV09-10_08-15%20d%C3%A9cembre%202022.pdf",
}


def slug(value: str) -> str:
    normalized = pilot._ascii(value)
    return re.sub(r"[^a-z0-9]+", "_", normalized).strip("_")[:64]


AUTHOR_HINTS = {
    "dohr-sarah": "Sarah Dohr",
    "dohr-s-": "Sarah Dohr",
    "bertschy-p": "Patrick Bertschy",
    "bertschyp": "Patrick Bertschy",
    "zonca-v": "Valérie Zonca",
    "mollet-c": "Claire Mollet",
    "iamartino-a": "Anna Iamartino",
    "gonthier-a": "Alain Gonthier",
    "girardoz-f": "Florian Girardoz",
    "christen-j": "Jérôme Christen",
    "houriet-m": "Marion Houriet",
    "de-regibus-g": "Giuliana de Regibus",
    "herminjard-p": "Philippe Herminjard",
    "herminjard-ph": "Philippe Herminjard",
    "vongunten-d": "Diane von Gunten",
}

TITLE_HINTS = {
    "6188": "Sauvons le Lido – Enseigne centenaire en péril",
    "6175": "Fonctionnement et sécurité de l’hébergement d’urgence « Le Lien – Vevey »",
    "5995": "Nul n’est censé ignorer la loi. Encore faut-il pouvoir y accéder.",
    "5998": "Les obscurs coûts d’une transparence limitée",
    "5891": "Un centre sportif régional loin de tout, en zone agricole, vraiment ?",
    "5890": "Accès proactif aux documents publics de la Commune",
    "5888": "#VEVEY sculpte son nom",
    "5893": "La technologie embarquée comme aide à la conduite automobile",
    "5894": "L’apprentissage en question",
    "5892": "Quelles mesures rapides pour éviter aux VMCV des sorties de route et des dérapages ?",
    "5889": "Il est temps de remettre à jour notre pyramide fiscale !",
    "5885": "Quelle réglementation de l’utilisation des IA dans l’administration communale veveysanne ?",
    "5531": "Violences domestiques : l’urgence d’agir",
    "5238": "Publicité commerciale et autres…",
    "5204": "La SPA du Haut-Léman, une association d’utilité publique appréciée, mais aussi sous-estimée",
    "5205": "Fontaine, je ne boirai pas de ton eau",
    "5195": "Pour qui ? Pourquoi ? Par qui ?",
    "5189": "Soutenir le solaire Plug & Play",
    "5188": "Horaires d’ouverture des magasins : Vevey n’en fait qu’à sa tête !",
    "5186": "Situation des places de parc en surface !",
    "5138": "La Municipalité actuelle appelée à respecter l’ancienne",
    "5150": "Stop aux violences domestiques et violences faites aux femmes : un dossier toujours d’actualité",
}


def infer_author(item: dict) -> str:
    current = str(item.get("author") or "").strip()
    if current:
        return current
    title = str(item.get("title") or "")
    match = re.search(
        r"^Interpellation de\s+(?:M(?:me|mes|M\.)?\.?\s+)?(.+?)(?:\s*\([^)]*\)|,\s*intitul|\s*:\s*[«\"])",
        title,
        re.I,
    )
    if match:
        return match.group(1).strip()
    folded = pilot._ascii(title).replace("_", "-")
    for marker, author in AUTHOR_HINTS.items():
        if marker in folded:
            return author
    return ""


def collect_search_occurrences(session: requests.Session) -> tuple[list[dict], dict]:
    """Collecte aussi les objets que le filtre de type Drupal omet.

    Le catalogue de Vevey n'est pas homogène : certains documents dont le type
    affiché est bien « interpellation » ne ressortent pas avec
    ``type-desktop=Interpellation``. La recherche textuelle officielle les
    retrouve. On fusionne donc les deux vues avant la déduplication PDF.
    """
    rows: list[dict] = []
    page = 0
    reported = 0
    while True:
        response = session.get(
            SOURCE_PAGE,
            params={
                "search": "interpellation",
                "since-desktop": LEGISLATURE_START,
                "until-desktop": LEGISLATURE_END,
                "submit-desktop": "Appliquer",
                "page": page,
            },
            headers=HEADERS,
            timeout=60,
        )
        response.raise_for_status()
        if page == 0:
            reported = pilot.result_count(response.text)
        blocks = pilot.extract_teaser_blocks(response.text)
        if not blocks:
            break
        for block in blocks:
            item = pilot.parse_teaser(block)
            if (
                item["document_type"].casefold() == "interpellation"
                and LEGISLATURE_START <= item["listing_date"] <= LEGISLATURE_END
            ):
                rows.append(item)
        page += 1
        # Garde-fou contre une pagination défectueuse.
        if page > max(50, (reported + SEARCH_PAGE_SIZE - 1) // SEARCH_PAGE_SIZE + 2):
            raise ValueError("Pagination textuelle Vevey sans fin détectée")
    return rows, {
        "reported_results_before_date_filter": reported,
        "pages_fetched": page,
        "interpellation_occurrences": len(rows),
        "complete": page > 0,
    }


def collect_standalone(session: requests.Session) -> tuple[list[dict], dict]:
    first_html = pilot.fetch_page(0, session)
    expected = pilot.result_count(first_html)
    pages = max(1, (expected + pilot.PAGE_SIZE - 1) // pilot.PAGE_SIZE)
    occurrences = pilot.parse_page(first_html, years=None)
    for page in range(1, pages):
        occurrences.extend(pilot.parse_page(pilot.fetch_page(page, session), years=None))
    search_occurrences, search_diagnostics = collect_search_occurrences(session)
    unique = {}
    for item in [*occurrences, *search_occurrences]:
        key = (item["source_download_id"] or item["pdf_url"], item["listing_date"], item["title"])
        unique[key] = item
    scoped = [
        item for item in unique.values()
        if "2023-01-01" <= item["listing_date"] <= LEGISLATURE_END
    ]
    for item in scoped:
        item["author"] = infer_author(item)
        object_title = TITLE_HINTS.get(str(item.get("source_download_id") or ""))
        if object_title:
            item["title"] = f"Interpellation de {item['author']} : « {object_title} »"
    scoped.sort(key=lambda row: (row["listing_date"], row["source_download_id"]), reverse=True)
    type_filter_unique = {
        (item["source_download_id"] or item["pdf_url"], item["listing_date"], item["title"])
        for item in occurrences
    }
    if len(type_filter_unique) != expected:
        raise ValueError(f"Collecte Interpellation incomplète: {len(unique)} sur {expected}")
    return scoped, {
        "endpoint_results": expected,
        "pages_fetched": pages,
        "unique_type_filter_occurrences": len(type_filter_unique),
        "text_search": search_diagnostics,
        "unique_merged_occurrences": len(unique),
        "standalone_occurrences_in_scope": len(scoped),
        "complete": True,
    }


def early_title(row: dict) -> str:
    party = f" ({row['party']})" if row["party"] else ""
    return f"Interpellation de {row['author']}{party} : « {row['object_title']} »"


def crop_pdf(content: bytes, start: int, end: int) -> bytes:
    with fitz.open(stream=content, filetype="pdf") as source, fitz.open() as target:
        if start < 1 or end > source.page_count or start > end:
            raise ValueError(f"Sélection de pages invalide: {start}-{end}/{source.page_count}")
        target.insert_pdf(source, from_page=start - 1, to_page=end - 1)
        return target.tobytes(garbage=4, deflate=True)


def download_early(output_dir: Path, session: requests.Session) -> tuple[list[dict], dict]:
    output_dir.mkdir(parents=True, exist_ok=True)
    source_bytes = {}
    for key, url in SOURCE_URLS.items():
        response = session.get(url, headers=HEADERS, timeout=120)
        response.raise_for_status()
        if not response.content.startswith(b"%PDF"):
            raise ValueError(f"Source {key} non PDF")
        source_bytes[key] = response.content
    documents = []
    for row in EARLY_SOURCES:
        start, end = row["pages"]
        content = crop_pdf(source_bytes[row["source"]], start, end)
        document_id = f"vevey_interpellation_{row['listing_date'][:4]}_{slug(row['object_title'])}"
        target = output_dir / f"{document_id}.pdf"
        target.write_bytes(content)
        url = SOURCE_URLS[row["source"]]
        documents.append({
            "municipality": "Vevey",
            "municipality_key": "vevey",
            "category": "interpellation",
            "document_type": "interpellation",
            "title": early_title(row),
            "object_title": row["object_title"],
            "listing_year": row["listing_date"][:4],
            "listing_date": row["listing_date"],
            "author": row["author"],
            "party": row["party"],
            "reference": "",
            "pdf_url": url,
            "source_page": SOURCE_PAGE,
            "source_collection": "vevey-signed-minutes",
            "source_download_id": row["source"],
            "source_pages": [start, end],
            "legislature": "2021-2026",
            "document_id": document_id,
            "content_hash": hashlib.sha256(content).hexdigest(),
            "content_bytes": len(content),
            "text_audit": inspect_pdf_text(content),
            "document_role": "political_object",
            "listing_occurrences": [{
                "listing_date": row["listing_date"],
                "author": row["author"],
                "reference": "",
                "title": early_title(row),
                "pdf_url": url,
                "source_download_id": row["source"],
                "source_pages": [start, end],
            }],
        })
    return documents, {
        "curated_objects": len(documents),
        "source_minutes": len(source_bytes),
        "documents_needing_ocr": sum(row["text_audit"]["needs_ocr"] for row in documents),
        "complete": len(documents) == len(EARLY_SOURCES),
    }


def main() -> None:
    root = PROJECT_ROOT / "audit-vevey" / "interpellations-2021-2026"
    parser = argparse.ArgumentParser(description="Interpellations de Vevey, législature 2021-2026")
    parser.add_argument("--output", type=Path, default=root / "inventory.json")
    parser.add_argument("--download-dir", type=Path, default=root / "pdfs")
    parser.add_argument("--html-output", type=Path, default=root / "audit.html")
    args = parser.parse_args()

    session = requests.Session()
    standalone_items, listing = collect_standalone(session)
    standalone, standalone_downloads = pilot.download_audit(
        standalone_items, session=session, download_dir=args.download_dir
    )
    early_documents, early_downloads = download_early(args.download_dir, session)
    documents = sorted(
        [*standalone, *early_documents],
        key=lambda row: (row["listing_date"], row["document_id"]),
        reverse=True,
    )
    preindex = audit_preindex(documents, {
        "complete": standalone_downloads["complete"] and early_downloads["complete"],
        "usable_complete": standalone_downloads.get("usable_complete", False) and early_downloads["complete"],
        "failed_downloads": standalone_downloads.get("failed_downloads", []),
        "documents_needing_ocr": sum(row["text_audit"]["needs_ocr"] for row in documents),
    })
    report = {
        "schema_version": "vevey-interpellations-2021-2026-v1",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": SOURCE_PAGE,
        "scope": {
            "city": "Vevey",
            "document_type": "interpellation",
            "legislature": "2021-2026",
            "from": LEGISLATURE_START,
            "to": LEGISLATURE_END,
        },
        "listing_diagnostics": listing,
        "download_diagnostics": {
            "standalone": standalone_downloads,
            "curated_minutes": early_downloads,
            "canonical_pdf_documents": len(documents),
            "documents_needing_ocr": sum(row["text_audit"]["needs_ocr"] for row in documents),
        },
        "listing_occurrences": standalone_items,
        "canonical_documents": documents,
        "preindex_audit": preindex,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_preindex_html(preindex, args.html_output, title="Audit avant indexation — interpellations de Vevey 2021–2026")
    print(json.dumps({
        "documents": len(documents),
        "standalone": len(standalone),
        "curated_2021_2022": len(early_documents),
        "needs_ocr": report["download_diagnostics"]["documents_needing_ocr"],
        "failed_downloads": len(standalone_downloads.get("failed_downloads", [])),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
