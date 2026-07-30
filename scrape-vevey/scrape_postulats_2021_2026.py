from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path

import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

SHARED_PATH = Path(__file__).with_name("scrape_motions_2021_2026.py")
SPEC = importlib.util.spec_from_file_location("vevey_motion_scraper", SHARED_PATH)
assert SPEC and SPEC.loader
shared = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(shared)

SOURCE_PAGE = shared.SOURCE_PAGE
DOWNLOAD_URL = shared.DOWNLOAD_URL
HEADERS = {"User-Agent": "AI-Riviera Vevey postulates 2021-2026"}
PAGE_SIZE = 30
LEGISLATURE_START = "2021-07-01"
LEGISLATURE_END = "2026-06-30"


def author(name: str, civility: str, party: str) -> dict:
    return {
        "name": name,
        "civility": civility,
        "party": party,
        "role": "postulant",
    }


OBJECTS = {
    "precarite-menstruelle": {
        "object_id": "vevey-postulate-2021-precarite-menstruelle",
        "object_title": "Lutter contre la précarité menstruelle à Vevey",
        "authors": [author("Joëlle Minacci", "Mme", "da.")],
        "deposit_date": "2021-10-07",
        "status": "En traitement",
    },
    "bienwenue-elargie": {
        "object_id": "vevey-postulate-2021-bienwenue-elargie",
        "object_title": "Vers une « bienwenue » élargie",
        "authors": [author("Fabienne Despot", "Mme", "UDC")],
        "deposit_date": "2021-11-11",
        "status": "En traitement",
    },
    "carte-citoyenne": {
        "object_id": "vevey-postulate-2021-carte-citoyenne",
        "object_title": "Une carte citoyenne pour développer l'intégration et le vivre ensemble",
        "authors": [author("Marion Houriet", "Mme", "PS")],
        "deposit_date": "2021-11-11",
        "status": "En traitement",
    },
    "infrastructures-plan-dessus": {
        "object_id": "vevey-postulate-2022-infrastructures-plan-dessus",
        "object_title": "Vision d’avenir pour les infrastructures sportives de Plan-Dessus",
        "authors": [author("Nicolas Bonjour", "M.", "VL")],
        "deposit_date": "2022-02-03",
        "status": "En traitement",
    },
    "subventions-sportives": {
        "object_id": "vevey-postulate-2022-subventions-sportives",
        "object_title": "Réflexion sur les subventions sportives et soutiens à nos associations sportives",
        "authors": [author("Nicolas Bonjour", "M.", "VL")],
        "deposit_date": "2022-02-03",
        "status": "En traitement",
    },
    "non-recours": {
        "object_id": "vevey-postulate-2022-non-recours",
        "object_title": "Pour que Vevey fasse sa part contre le non-recours aux prestations sociales",
        "authors": [author("Sabrina Berrocal", "Mme", "da./Interpartis")],
        "deposit_date": "2022-03-17",
        "status": "Réponse 2025/RP17 disponible",
        "response_date": "2025-05-15",
        "has_response": True,
    },
    "insecurite-gare": {
        "object_id": "vevey-postulate-2022-insecurite-gare",
        "object_title": "Insécurité à la Gare – impunité ou réactivité ?",
        "authors": [author("Anna Iamartino", "Mme", "PLR")],
        "deposit_date": "2022-05-19",
        "status": "Réponse 2025/P03 disponible",
        "response_date": "2025-02-06",
        "has_response": True,
    },
    "climat-preemption": {
        "object_id": "vevey-postulate-2022-climat-preemption",
        "object_title": "Climat et préemption…",
        "authors": [author("Patrick Bertschy", "M.", "PLR")],
        "deposit_date": "2022-06-23",
        "status": "En traitement",
    },
    "ville-images": {
        "object_id": "vevey-postulate-2022-ville-images",
        "object_title": "Faire de Vevey une ville d’images au quotidien",
        "authors": [author("Mickael Bertschy", "M.", "VL")],
        "deposit_date": "2022-10-06",
        "status": "Réponse 2024/RP34 disponible",
        "response_date": "2024-11-14",
        "has_response": True,
    },
    "alimentation-durable": {
        "object_id": "vevey-postulate-2023-alimentation-durable",
        "object_title": "Pour une ville exemplaire en matière d’alimentation durable",
        "authors": [author("Fabien Truffer", "M.", "LCVL")],
        "deposit_date": "2023-06-15",
        "status": "Rapport de prise en considération 2023/R23 disponible",
    },
    "micro-forets": {
        "object_id": "vevey-postulate-2023-micro-forets",
        "object_title": "Réchauffement climatique : et si on créait des micro-forêts urbaines ?",
        "authors": [author("Serge Ansermet", "M.", "PS")],
        "deposit_date": "2023-12-07",
        "status": "Réponse 2025/RP01 disponible",
        "response_date": "2025-02-06",
        "has_response": True,
    },
    "accueil-prescolaire": {
        "object_id": "vevey-postulate-2024-accueil-prescolaire",
        "object_title": "La complémentarité de l’offre publique-privée au service de l’accueil préscolaire",
        "authors": [author("Mickael Bertschy", "M.", "VL")],
        "deposit_date": "2024-03-14",
        "status": "Réponse 2026/RP19 disponible",
        "response_date": "2026-06-11",
        "has_response": True,
    },
    "agir-sans-attendre": {
        "object_id": "vevey-postulate-2024-agir-sans-attendre",
        "object_title": "Agir sans attendre pour notre bien… et pas seulement…",
        "authors": [author("Patrick Bertschy", "M.", "PLR")],
        "deposit_date": "2024-09-05",
        "status": "En traitement",
    },
    "commission-evenementiel": {
        "object_id": "vevey-postulate-2024-commission-evenementiel",
        "object_title": "Pour une commission de l’événementiel",
        "authors": [author("Hervé Queyranne", "M.", "da.")],
        "deposit_date": "2024-12-12",
        "status": "Rapport de prise en considération 2025/R06 disponible",
    },
    "audit-services": {
        "object_id": "vevey-postulate-2024-audit-services",
        "object_title": "Audit externe pour optimiser la gestion des services communaux et les conditions de travail des chefs et cheffes de service à Vevey",
        "authors": [author("Sandra Marques", "Mme", "PLR")],
        "deposit_date": "2024-12-12",
        "status": "En traitement",
    },
    "vevey-chef-lieu": {
        "object_id": "vevey-postulate-2025-vevey-chef-lieu",
        "object_title": "Vevey, ville indépendante ou chef-lieu du district de la Riviera ?",
        "authors": [author("Laurent Cornu", "M.", "PLR")],
        "deposit_date": "2025-02-06",
        "status": "Rapport de prise en considération 2025/R16 disponible",
    },
    "sans-abrisme": {
        "object_id": "vevey-postulate-2025-sans-abrisme",
        "object_title": "Lutter pour la fin du sans-abrisme, un défi communal, cantonal et national : pour un état des lieux et une politique coordonnée et intercommunale des hébergements d'urgence",
        "authors": [author("Sabrina Berrocal", "Mme", "da./Interpartis")],
        "deposit_date": "2025-03-27",
        "status": "En traitement",
    },
    "hebergement-violences": {
        "object_id": "vevey-postulate-2025-hebergement-violences",
        "object_title": "Un hébergement pour les victimes de violences domestiques",
        "authors": [author("Joëlle Minacci", "Mme", "da.")],
        "deposit_date": "2025-09-04",
        "status": "En traitement",
    },
    "securite-privee": {
        "object_id": "vevey-postulate-2025-securite-privee",
        "object_title": "Pour une étude sur l’opportunité de recourir à des agents de sécurité privés afin de renforcer le sentiment de sécurité sur le domaine public veveysan",
        "authors": [author("Capy Boissard", "Mme", "PLR")],
        "deposit_date": "2025-11-13",
        "status": "Rapport de prise en considération 2025/R36 disponible",
    },
    "trente-nuit": {
        "object_id": "vevey-postulate-2025-trente-nuit",
        "object_title": "30 km/h de nuit : combien de décibels en moins ?",
        "authors": [author("Sandra Marques", "Mme", "PLR")],
        "deposit_date": "2025-11-13",
        "status": "Rapport de prise en considération 2025/R38 disponible",
    },
    "gare-urgence": {
        "object_id": "vevey-postulate-2025-gare-urgence",
        "object_title": "Vevey en gare d’urgence, le deal doit dérailler !",
        "authors": [author("Anna Iamartino", "Mme", "PLR")],
        "deposit_date": "2025-11-13",
        "status": "Rapport de prise en considération 2025/R37 disponible",
    },
    "vmcv-ecoute": {
        "object_id": "vevey-postulate-2025-vmcv-ecoute",
        "object_title": "Les VMCV à l’écoute des citoyens et citoyennes",
        "authors": [author("Florian Girardoz", "M.", "PLR")],
        "deposit_date": "2025-12-04",
        "status": "Rapport de prise en considération 2026/R02 disponible",
    },
    "tourisme-train-velo": {
        "object_id": "vevey-postulate-2026-tourisme-train-velo",
        "object_title": "Visiter la Riviera, c’est bien… en train ou en vélo, c’est mieux",
        "authors": [author("Diane Von Gunten", "Mme", "Vert·e·s")],
        "deposit_date": "2026-02-05",
        "status": "En traitement",
    },
    "vevey-loupe": {
        "object_id": "vevey-postulate-2026-vevey-loupe",
        "object_title": "Vevey sous la loupe : mesurer l’efficacité des mesures prises pour la satisfaction des usagers et des contribuables",
        "authors": [author("Philippe Herminjard", "M.", "PLR")],
        "deposit_date": "2026-02-05",
        "status": "En traitement",
    },
    "potaclos": {
        "object_id": "vevey-postulate-2026-potaclos",
        "object_title": "Potaclos – Pour un pôle urbain mixte et innovant à l'Avenue de Blonay",
        "authors": [author("Martino Rizzello", "M.", "LCVL")],
        "deposit_date": "2026-02-05",
        "status": "Rapport de prise en considération 2026/R03 disponible",
    },
    "logistique-urbaine": {
        "object_id": "vevey-postulate-2026-logistique-urbaine",
        "object_title": "Stratégie de logistique urbaine",
        "authors": [author("Colin Wahli", "M.", "Vert·e·s")],
        "deposit_date": "2026-03-19",
        "status": "Déposé",
    },
    "feuilles-sol": {
        "object_id": "vevey-postulate-2026-feuilles-sol",
        "object_title": "Des feuilles pour nourrir le sol",
        "authors": [author("Colin Wahli", "M.", "Vert·e·s")],
        "deposit_date": "2026-03-19",
        "status": "Déposé",
    },
    "proximite-centre-ville": {
        "object_id": "vevey-postulate-2026-proximite-centre-ville",
        "object_title": "Choisissons la proximité pour garder un centre-ville vivant",
        "authors": [author("Fabienne Despot", "Mme", "UDC")],
        "deposit_date": "2026-03-19",
        "status": "Déposé",
    },
    "mobilite-eleves": {
        "object_id": "vevey-postulate-2026-mobilite-eleves",
        "object_title": "Pour une mobilité durable et équitable : étude de la gratuité des transports publics pour les élèves de la scolarité obligatoire à Vevey",
        "authors": [author("Sarah Dohr", "Mme", "VL")],
        "deposit_date": "2026-03-19",
        "status": "Déposé",
    },
    "rue-lausanne": {
        "object_id": "vevey-postulate-2026-rue-lausanne",
        "object_title": "Stop à l’impasse idéologique sur la rue de Lausanne !",
        "authors": [author("Anna Iamartino", "Mme", "PLR")],
        "deposit_date": "2026-03-19",
        "status": "Déposé",
    },
}

for profile in OBJECTS.values():
    profile.setdefault("has_response", False)
    profile["has_dedicated_response"] = profile["has_response"]
    profile["response_status"] = (
        "dedicated_response_available"
        if profile["has_response"]
        else "no_response_available"
    )
    profile["status_normalized"] = (
        "answered" if profile["has_response"] else "pending_or_unanswered"
    )
    profile["is_closed"] = profile["has_response"]


ORIGINAL_SOURCES = [
    # Early-legislature originals are attached to the signed minutes.
    ("precarite-menstruelle", "4736", [15, 15]),
    ("bienwenue-elargie", "4766", [22, 22]),
    ("carte-citoyenne", "4766", [25, 26]),
    ("infrastructures-plan-dessus", "4811", [8, 9]),
    ("subventions-sportives", "4811", [10, 11]),
    ("non-recours", "4834", [21, 22]),
    ("climat-preemption", "4894", [26, 28]),
    ("ville-images", "4970", [17, 18]),
    # Standalone postulate PDFs.
    ("insecurite-gare", "4876", None),
    ("alimentation-durable", "5151", None),
    ("micro-forets", "5289", None),
    ("accueil-prescolaire", "5412", None),
    ("agir-sans-attendre", "5580", None),
    ("commission-evenementiel", "5656", None),
    ("audit-services", "5657", None),
    ("vevey-chef-lieu", "5693", None),
    ("sans-abrisme", "5741", None),
    ("hebergement-violences", "5887", None),
    ("securite-privee", "5971", None),
    ("trente-nuit", "5976", None),
    ("gare-urgence", "5984", None),
    ("vmcv-ecoute", "5996", None),
    ("tourisme-train-velo", "6042", None),
    ("vevey-loupe", "6045", None),
    ("potaclos", "6048", None),
    ("logistique-urbaine", "6073", None),
    ("feuilles-sol", "6074", None),
    ("proximite-centre-ville", "6076", None),
    ("mobilite-eleves", "6078", None),
    ("rue-lausanne", "6079", None),
]


FOLLOW_UPS = [
    ("non-recours", "5811", "municipal_response", "2025/RP17", "2025-05-15"),
    ("insecurite-gare", "5704", "municipal_response", "2025/P03", "2025-02-06"),
    ("ville-images", "5612", "municipal_response", "2024/RP34", "2024-11-14"),
    ("micro-forets", "5681", "municipal_response", "2025/RP01", "2025-02-06"),
    ("accueil-prescolaire", "6190", "municipal_response", "2026/RP19", "2026-06-11"),
    ("alimentation-durable", "5240", "consideration_report", "2023/R23", "2023-11-16"),
    ("agir-sans-attendre", "5660", "consideration_report", "2024/R36", "2024-12-05"),
    ("commission-evenementiel", "5725", "consideration_report", "2025/R06", "2025-03-27"),
    ("vevey-chef-lieu", "5795", "consideration_report", "2025/R16", "2025-05-15"),
    ("securite-privee", "6065", "consideration_report", "2025/R36", "2026-03-19"),
    ("trente-nuit", "6088", "consideration_report", "2025/R38", "2025-12-04"),
    ("gare-urgence", "6066", "consideration_report", "2025/R37", "2026-03-19"),
    ("vmcv-ecoute", "6068", "consideration_report", "2026/R02", "2026-03-19"),
    ("potaclos", "6116", "consideration_report", "2026/R03", "2026-05-07"),
]


def collect_catalogue(session: requests.Session | None = None) -> tuple[dict[str, dict], dict]:
    client = session or requests.Session()
    first = client.get(SOURCE_PAGE, params={"page": 0}, headers=HEADERS, timeout=30)
    first.raise_for_status()
    expected = shared.result_count(first.text)
    pages = max(1, (expected + PAGE_SIZE - 1) // PAGE_SIZE)
    rows = [shared.parse_teaser(block) for block in shared.extract_teaser_blocks(first.text)]
    for page in range(1, pages):
        response = client.get(
            SOURCE_PAGE,
            params={"page": page},
            headers=HEADERS,
            timeout=30,
        )
        response.raise_for_status()
        rows.extend(
            shared.parse_teaser(block)
            for block in shared.extract_teaser_blocks(response.text)
        )
    by_id = {row["source_download_id"]: row for row in rows if row["source_download_id"]}
    if len(rows) != expected:
        raise ValueError(f"Collecte Vevey incomplète: {len(rows)} sur {expected}")
    needed = {item[1] for item in ORIGINAL_SOURCES} | {item[1] for item in FOLLOW_UPS}
    missing = sorted(needed - set(by_id))
    if missing:
        raise ValueError(f"Documents officiels absents du catalogue: {missing}")
    return by_id, {
        "endpoint_results": expected,
        "pages_fetched": pages,
        "parsed_occurrences": len(rows),
        "required_download_ids": len(needed),
        "missing_required_ids": missing,
        "complete": not missing and len(rows) == expected,
    }


def document_specs(by_id: dict[str, dict]) -> list[dict]:
    specs: list[dict] = []
    for key, download_id, pages in ORIGINAL_SOURCES:
        profile = OBJECTS[key]
        source = by_id[download_id]
        specs.append(
            {
                **source,
                "political_object_key": key,
                "document_role": "postulate_text",
                "document_date": profile["deposit_date"],
                "selection_pages": pages,
                "title": profile["object_title"],
            }
        )
    for key, download_id, role, reference, date in FOLLOW_UPS:
        source = by_id[download_id]
        specs.append(
            {
                **source,
                "political_object_key": key,
                "document_role": role,
                "document_date": date,
                "selection_pages": None,
                "reference": reference,
            }
        )
    return specs


def download_documents(
    specs: list[dict],
    output_dir: Path,
    session: requests.Session | None = None,
) -> tuple[list[dict], dict]:
    client = session or requests.Session()
    output_dir.mkdir(parents=True, exist_ok=True)
    records = []
    for spec in specs:
        response = client.get(spec["pdf_url"], headers=HEADERS, timeout=90)
        response.raise_for_status()
        content = response.content
        if not content.startswith(b"%PDF"):
            raise ValueError(f"{spec['source_download_id']} n'est pas un PDF")
        _, extraction = shared.pdf_text(content)
        document_id = (
            f"vevey_postulate_{spec['source_download_id']}_"
            f"{spec['political_object_key'].replace('-', '_')}"
        )
        target = output_dir / f"{document_id}.pdf"
        target.write_bytes(content)
        records.append(
            {
                **spec,
                "document_id": document_id,
                "sha256": hashlib.sha256(content).hexdigest(),
                "local_pdf": str(target.relative_to(PROJECT_ROOT)).replace("\\", "/"),
                "text_audit": extraction,
            }
        )
    return records, {
        "documents": len(records),
        "political_objects": len(OBJECTS),
        "original_postulates": sum(row["document_role"] == "postulate_text" for row in records),
        "municipal_responses": sum(row["document_role"] == "municipal_response" for row in records),
        "consideration_reports": sum(row["document_role"] == "consideration_report" for row in records),
        "documents_needing_ocr": sum(row["text_audit"]["needs_ocr"] for row in records),
        "unique_source_pdfs": len({row["sha256"] for row in records}),
        "complete": len(records) == len(specs),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scrape les postulats de Vevey de la législature 2021-2026"
    )
    root = PROJECT_ROOT / "audit-vevey" / "postulats-2021-2026"
    parser.add_argument("--output", type=Path, default=root / "inventory.json")
    parser.add_argument("--download-dir", type=Path, default=root / "pdfs")
    args = parser.parse_args()

    by_id, listing = collect_catalogue()
    documents, downloads = download_documents(document_specs(by_id), args.download_dir)
    payload = {
        "schema_version": "vevey-postulates-inventory-v1",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": SOURCE_PAGE,
        "scope": {
            "city": "Vevey",
            "category": "postulat",
            "legislature": "2021-2026",
            "from": LEGISLATURE_START,
            "to": LEGISLATURE_END,
        },
        "listing_diagnostics": listing,
        "download_diagnostics": downloads,
        "objects": OBJECTS,
        "documents": documents,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({**listing, **downloads}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
