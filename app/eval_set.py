import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EVAL_QUESTIONS_PATH = PROJECT_ROOT / "eval" / "eval_questions.json"


def load_eval_questions() -> list[dict]:
    if not EVAL_QUESTIONS_PATH.exists():
        return []
    return json.loads(EVAL_QUESTIONS_PATH.read_text(encoding="utf-8"))


def source_matches_expected(result: dict, expected_sources: list[str]) -> bool:
    if not expected_sources:
        return False

    metadata = result.get("metadata") or {}
    candidates = [
        result.get("relative_text_path", ""),
        metadata.get("text_path", ""),
        metadata.get("pdf_path", ""),
        metadata.get("filename", ""),
    ]
    normalized_candidates = [candidate.replace("\\", "/") for candidate in candidates if candidate]

    for expected in expected_sources:
        expected_normalized = expected.replace("\\", "/").removesuffix(".json")
        for candidate in normalized_candidates:
            candidate_no_suffix = candidate.removesuffix(".txt").removesuffix(".pdf").removesuffix(".json")
            if expected_normalized in candidate_no_suffix or candidate_no_suffix in expected_normalized:
                return True
    return False


def retrieval_hit(results: list[dict], expected_sources: list[str]) -> bool:
    return any(source_matches_expected(result, expected_sources) for result in results)
