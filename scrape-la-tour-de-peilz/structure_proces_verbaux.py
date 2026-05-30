import json
import re
from datetime import date
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DOCUMENTS_ROOT = PROJECT_ROOT / "documents" / "la-tour-de-peilz"
SESSIONS_ROOT = PROJECT_ROOT / "data" / "sessions" / "la-tour-de-peilz"
PV_ROOT = PROJECT_ROOT / "data" / "proces-verbaux" / "la-tour-de-peilz"

YEARS = {str(year) for year in range(2021, 2027)}


def parse_pv_filename(filename: str) -> dict | None:
    normalized = filename.replace("_", "-").replace(".", "-")
    match = re.search(r"PV\s*0*(\d+).*?(\d{2})-(\d{2})-(\d{2,4})", normalized, flags=re.I)
    if not match:
        return None

    pv_number = int(match.group(1))
    day = int(match.group(2))
    month = int(match.group(3))
    year = int(match.group(4))
    if year < 100:
        year += 2000
    return {
        "pv_number": pv_number,
        "session_date": date(year, month, day).isoformat(),
        "year": str(year),
    }


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def collect_pvs() -> list[dict]:
    pvs = []
    for metadata_path in sorted(DOCUMENTS_ROOT.rglob("proces-verbaux/*.json")):
        metadata = read_json(metadata_path)
        if "legislature-2016-2021" in metadata.get("pdf_url", ""):
            continue
        filename = metadata.get("filename", metadata_path.with_suffix(".pdf").name)
        parsed = parse_pv_filename(filename)
        if not parsed or parsed["year"] not in YEARS:
            continue

        metadata["type"] = "proces_verbal"
        metadata["session_date"] = parsed["session_date"]
        metadata["pv_number"] = parsed["pv_number"]
        write_json(metadata_path, metadata)

        pvs.append(
            {
                "commune": "La Tour-de-Peilz",
                "type": "proces_verbal",
                "pv_number": parsed["pv_number"],
                "session_date": parsed["session_date"],
                "year": parsed["year"],
                "filename": filename,
                "category": "proces-verbaux",
                "pdf_url": metadata.get("pdf_url", ""),
                "source_page": metadata.get("source_page", ""),
                "metadata_path": str(metadata_path),
                "text_path": str(metadata_path.with_suffix(".txt")),
                "pdf_path": str(metadata_path.with_suffix(".pdf")),
            }
        )
    return sorted(pvs, key=lambda item: item["session_date"])


def update_sessions_with_pvs(pvs: list[dict]) -> None:
    pvs_by_date = {pv["session_date"]: pv for pv in pvs}
    for session_path in SESSIONS_ROOT.glob("20*/*.json"):
        session = read_json(session_path)
        pv = pvs_by_date.get(session.get("session_date"))
        if pv:
            session["proces_verbal"] = pv
        write_json(session_path, session)


def main() -> None:
    pvs = collect_pvs()
    update_sessions_with_pvs(pvs)

    for pv in pvs:
        write_json(PV_ROOT / pv["year"] / f"{pv['session_date']}.json", pv)

    manifest = {
        "commune": "La Tour-de-Peilz",
        "years": sorted(YEARS),
        "proces_verbaux_count": len(pvs),
        "proces_verbaux": pvs,
    }
    write_json(PV_ROOT / "manifest_proces_verbaux_2021_2026.json", manifest)

    for pv in pvs:
        print(f"{pv['session_date']} -> PV{pv['pv_number']:02d} {pv['filename']}")


if __name__ == "__main__":
    main()
