from __future__ import annotations

import json
import os
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
OUTPUT_PATH = (
    PROJECT_ROOT
    / "audit-vevey"
    / "combined-interpellations-audit"
    / "retrieval-validation.json"
)

CASES = [
    {
        "name": "collection_planque_response",
        "question": (
            "Que répond la Municipalité de Vevey sur les conséquences financières "
            "de la collection Planque ?"
        ),
        "expected_any": ["vevey_interpellation_response_6ab9e130a30b822e4ce2"],
    },
    {
        "name": "spark_response",
        "question": (
            "Quelle réponse a été donnée sur l'avenir du Spark et le projet pour "
            "les jeunes de la place Robin ?"
        ),
        "expected_any": ["vevey_interpellation_response_c61d12825ac0f48445a4"],
    },
    {
        "name": "ai_regulation_response",
        "question": (
            "Que dit la réponse sur la réglementation de l'utilisation de "
            "l'intelligence artificielle dans l'administration communale veveysanne ?"
        ),
        "expected_any": ["vevey_interpellation_response_43a6a6cf3a3e844ed133"],
    },
    {
        "name": "stationnement_quais_response",
        "question": (
            "Quelle est la réponse concernant le stationnement sur les quais de la "
            "Veveyse et Maria Belgia ?"
        ),
        "expected_any": ["vevey_interpellation_response_751f1ccdd768d241b9f2"],
    },
    {
        "name": "advertising_screens_response",
        "question": (
            "Que répond Vevey au sujet des écrans publicitaires et des risques "
            "pour la sécurité routière ?"
        ),
        "expected_any": ["vevey_interpellation_response_a7cfc0978f545f7f2649"],
    },
    {
        "name": "ocr_combined_document",
        "question": (
            "Que contient l'interpellation de Patrick Bertschy intitulée "
            "\"Un point de la situation actuelle et future est nécessaire\" ?"
        ),
        "expected_any": ["vevey_interpellation_cf6bd00e646d4f9a5413"],
    },
    {
        "name": "unanswered_30_kmh",
        "question": (
            "Quelle interpellation de Cyril Gros parle de l'engagement du 30 km/h "
            "à Vevey ?"
        ),
        "expected_any": ["vevey_interpellation_2125366919ee97beedb8"],
    },
    {
        "name": "city_isolation",
        "question": (
            "Existe-t-il à Vevey une interpellation intitulée "
            "\"Kiosques sur le quai Roussy\" ?"
        ),
        "expected_any": [],
        "forbidden_prefixes": ["doc_"],
    },
]


def load_environment() -> None:
    env_path = PROJECT_ROOT / "embedding-pilot" / ".env"
    if env_path.exists():
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(
                    key.strip(), value.strip().strip('"').strip("'")
                )

    secrets_path = PROJECT_ROOT / ".streamlit" / "secrets.toml"
    if secrets_path.exists():
        secrets = tomllib.loads(secrets_path.read_text(encoding="utf-8"))
        for key in ("MISTRAL_API_KEY",):
            if secrets.get(key):
                os.environ.setdefault(key, str(secrets[key]))


def main() -> None:
    load_environment()
    if not os.environ.get("POSTGRES_V2_URL"):
        raise SystemExit("POSTGRES_V2_URL is missing")
    if not os.environ.get("MISTRAL_API_KEY"):
        raise SystemExit("MISTRAL_API_KEY is missing")

    from app.retrieval import search

    case_results = []
    for case in CASES:
        results = search(
            case["question"],
            limit=12,
            filters={"city": "Vevey", "doc_type": "interpellations"},
        )
        document_ids = list(dict.fromkeys(row["document_id"] for row in results))
        communes = {
            str((row.get("metadata") or {}).get("commune") or "")
            for row in results
        }
        source_urls = [
            row.get("source_url") or ""
            for row in results
        ]
        expected_pass = (
            any(expected in document_ids for expected in case["expected_any"])
            if case["expected_any"]
            else True
        )
        forbidden_pass = not any(
            document_id.startswith(prefix)
            for prefix in case.get("forbidden_prefixes", [])
            for document_id in document_ids
        )
        city_pass = communes <= {"Vevey"}
        sources_pass = all(
            url.startswith("https://") for url in source_urls if url
        ) and any(source_urls)
        passed = expected_pass and forbidden_pass and city_pass and sources_pass
        case_results.append(
            {
                **case,
                "passed": passed,
                "checks": {
                    "expected_document": expected_pass,
                    "forbidden_documents_absent": forbidden_pass,
                    "vevey_only": city_pass,
                    "https_sources": sources_pass,
                },
                "result_count": len(results),
                "document_ids": document_ids,
                "top_results": [
                    {
                        "document_id": row["document_id"],
                        "title": row["title"],
                        "role": (row.get("metadata") or {}).get("document_role"),
                        "source_url": row.get("source_url"),
                        "score": row.get("score"),
                    }
                    for row in results[:5]
                ],
            }
        )
        print(f"{case['name']}: {'PASS' if passed else 'FAIL'}")

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "filters": {"city": "Vevey", "doc_type": "interpellations"},
        "passed": all(case["passed"] for case in case_results),
        "passed_cases": sum(case["passed"] for case in case_results),
        "total_cases": len(case_results),
        "cases": case_results,
    }
    OUTPUT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "passed": report["passed"],
                "passed_cases": report["passed_cases"],
                "total_cases": report["total_cases"],
                "report": str(OUTPUT_PATH.relative_to(PROJECT_ROOT)),
            },
            ensure_ascii=False,
        )
    )
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
