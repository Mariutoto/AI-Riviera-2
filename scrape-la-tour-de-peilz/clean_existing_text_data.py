import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.text_cleaning import clean_french_text


TARGET_ROOTS = [
    PROJECT_ROOT / "documents" / "la-tour-de-peilz",
    PROJECT_ROOT / "data" / "sessions" / "la-tour-de-peilz",
    PROJECT_ROOT / "data" / "proces-verbaux" / "la-tour-de-peilz",
    PROJECT_ROOT / "data" / "institutionnel" / "la-tour-de-peilz",
]


def clean_value(value):
    if isinstance(value, str):
        return clean_french_text(value)
    if isinstance(value, list):
        return [clean_value(item) for item in value]
    if isinstance(value, dict):
        return {key: clean_value(item) for key, item in value.items()}
    return value


def clean_text_file(path: Path) -> bool:
    original = path.read_text(encoding="utf-8", errors="replace")
    cleaned = clean_french_text(original) + "\n"
    if cleaned == original:
        return False
    path.write_text(cleaned, encoding="utf-8")
    return True


def clean_json_file(path: Path) -> bool:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return False
    cleaned = clean_value(data)
    if cleaned == data:
        return False
    path.write_text(json.dumps(cleaned, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True


def main() -> None:
    changed = 0
    checked = 0
    for root in TARGET_ROOTS:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if path.suffix.lower() not in {".txt", ".json"}:
                continue
            checked += 1
            if path.suffix.lower() == ".json":
                did_change = clean_json_file(path)
            else:
                did_change = clean_text_file(path)
            if did_change:
                changed += 1
                print(f"cleaned {path.relative_to(PROJECT_ROOT)}")
    print(json.dumps({"files_checked": checked, "files_changed": changed}, ensure_ascii=False))


if __name__ == "__main__":
    main()
