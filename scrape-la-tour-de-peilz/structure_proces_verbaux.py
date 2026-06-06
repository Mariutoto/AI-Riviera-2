import json
import re
import unicodedata
from datetime import date
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DOCUMENTS_ROOT = PROJECT_ROOT / "documents" / "la-tour-de-peilz"
SESSIONS_ROOT = PROJECT_ROOT / "data" / "sessions" / "la-tour-de-peilz"
PV_ROOT = PROJECT_ROOT / "data" / "proces-verbaux" / "la-tour-de-peilz"

YEARS = {str(year) for year in range(2021, 2027)}


SECTION_STOPS = (
    "Appel",
    "Liste de présence",
    "Excusé",
    "ExcusÃ",
    "Absent",
    "M. le Président ouvre",
    "Mme la Présidente ouvre",
    "M. R. Urech",
    "Les conseillères et conseillers suivants",
)


def strip_accents(value: str) -> str:
    return "".join(
        char for char in unicodedata.normalize("NFD", value)
        if unicodedata.category(char) != "Mn"
    )


def normalize_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip()


def join_wrapped_lines(lines: list[str]) -> str:
    output = ""
    for raw_line in lines:
        line = normalize_spaces(raw_line)
        if not line or re.fullmatch(r"-\s*\d+\s*-", line):
            continue
        if output.endswith("-"):
            output = output[:-1] + line
        elif output:
            output += " " + line
        else:
            output = line
    return normalize_spaces(output)


def split_people(value: str) -> list[str]:
    value = re.sub(r"\bAbsent\(e\)s?\s*:.*", "", value, flags=re.I)
    value = re.sub(r"\bEst absent(?:e)?\s*:.*", "", value, flags=re.I)
    value = re.sub(r"\bExcus[ée]\(e\)s?\s*:", "", value, flags=re.I)
    value = re.sub(r"\bLes conseill[èe]res? et conseillers suivants se sont excus[ée]s?\s*:", "", value, flags=re.I)
    value = re.sub(r"\s+(?:M\.|Mme|Le Conseil|Monsieur|Madame|Il rend attentif|Après lecture|Apr[èe]s lecture).*$", "", value)
    names = re.split(r"\s+[–—-]\s+|,|;", value)
    cleaned = []
    for name in names:
        name = normalize_spaces(name)
        name = re.sub(r"\s+M\..*$", "", name)
        name = re.sub(r"\s+Mme\s+.*$", "", name)
        name = re.sub(r"^[•\-\s]+|[.;:\s]+$", "", name)
        if name and len(name.split()) >= 2 and not re.search(r"\d", name):
            cleaned.append(name)
    return cleaned


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


def read_text_for_metadata(metadata_path: Path) -> str:
    text_path = metadata_path.with_suffix(".txt")
    if not text_path.exists():
        return ""
    return text_path.read_text(encoding="utf-8-sig")


def extract_session_details(text: str) -> dict:
    lines = [normalize_spaces(line) for line in text.splitlines()]
    details: dict = {}
    for line in lines[:80]:
        match = re.search(r"\b(?:[Ll]undi|[Mm]ardi|[Mm]ercredi|[Jj]eudi|[Vv]endredi|[Ss]amedi|[Dd]imanche)\b.*?\b[àa]\s*(\d{1,2})h(\d{2})", line)
        if match:
            details["start_time"] = f"{int(match.group(1)):02d}:{match.group(2)}"
        if re.search(r"^Salle\b", line):
            details["location"] = line
        elif not details.get("location") and re.search(r"^(Maison|Coll[èe]ge)\b", line):
            details["location"] = line
        if ("Présidence" in line or "PrÃ©sidence" in line) and ":" in line:
            president = re.sub(r"^.*Présidence\s*:\s*", "", line)
            president = re.sub(r"^.*PrÃ©sidence\s*:\s*", "", president)
            details["president"] = normalize_person_name(president)
    signatures = extract_signatures(text)
    if signatures.get("president"):
        details["signature_president"] = signatures["president"]
    if signatures.get("secretary"):
        details["secretary"] = signatures["secretary"]
    return details


def normalize_person_name(value: str) -> str:
    value = normalize_spaces(value)
    value = re.sub(r"^(M\.|Mme|Mlle|Monsieur|Madame)\s+", "", value, flags=re.I)
    value = re.sub(r"^le Pr[ée]fet\s+", "Préfet ", value, flags=re.I)
    return value.strip(" .")


def is_person_line(value: str) -> bool:
    normalized = strip_accents(value).lower()
    if normalized.startswith(("le president", "la presidente", "le secretaire", "la secretaire", "au nom", "vote appel")):
        return False
    if re.search(r"\d", value):
        return False
    return len(value.split()) >= 2 and len(value) <= 80


def extract_signatures(text: str) -> dict:
    marker = strip_accents(text).lower().rfind("au nom du conseil communal")
    if marker < 0:
        return {}
    block = text[marker:]
    lines = [normalize_spaces(line) for line in block.splitlines() if normalize_spaces(line)]
    people = [normalize_person_name(line) for line in lines if is_person_line(line)]
    if len(people) >= 2:
        return {"president": people[0], "secretary": people[1]}
    return {}


def extract_signature_name(text: str, label_prefix: str) -> str | None:
    lines = [normalize_spaces(line) for line in text.splitlines() if normalize_spaces(line)]
    for index, line in enumerate(lines):
        normalized = strip_accents(line).lower()
        if normalized.startswith(f"la {strip_accents(label_prefix).lower()}") or normalized.startswith(f"le {strip_accents(label_prefix).lower()}"):
            for candidate in lines[index + 1:index + 6]:
                candidate_normalized = strip_accents(candidate).lower()
                if candidate_normalized.startswith(("le president", "la presidente", "le secretaire", "la secretaire")):
                    continue
                if re.search(r"\d", candidate):
                    continue
                if len(candidate.split()) >= 2:
                    return normalize_person_name(candidate)
    return None


def extract_agenda(text: str) -> list[dict]:
    marker = re.search(r"ORDRE DU JOUR", text, flags=re.I)
    if not marker:
        return []
    after = text[marker.end():]
    agenda_lines = []
    for raw_line in after.splitlines():
        line = normalize_spaces(raw_line)
        if not line or re.fullmatch(r"-\s*\d+\s*-", line):
            continue
        if any(line.startswith(stop) for stop in SECTION_STOPS):
            break
        agenda_lines.append(line)

    agenda = []
    current_number: str | None = None
    current_lines: list[str] = []
    inline_pattern = re.compile(r"^(\d+(?:\.\d+)*)\.\s*(.*)$")
    number_only_pattern = re.compile(r"^(\d+(?:\.\d+)*)\.?$")
    for line in agenda_lines:
        inline = inline_pattern.match(line)
        number_only = number_only_pattern.match(line)
        if inline or number_only:
            if current_number:
                agenda.append({"number": current_number, "title": join_wrapped_lines(current_lines)})
            current_number = (inline or number_only).group(1)
            current_lines = []
            if inline and inline.group(2):
                current_lines.append(inline.group(2))
            continue
        if current_number:
            current_lines.append(line)
    if current_number:
        agenda.append({"number": current_number, "title": join_wrapped_lines(current_lines)})
    return [item for item in agenda if item["title"]]


def extract_attendance(text: str) -> dict:
    normalized = strip_accents(text)
    attendance: dict = {}
    count_match = re.search(
        r"(\d+)\s+(?:personnes\s+)?present(?:e)?s?\s+sur\s+(\d+)\s+(?:personnes\s+)?(?:membres\s+)?elus?",
        normalized,
        flags=re.I,
    )
    if count_match:
        attendance["present_count"] = int(count_match.group(1))
        attendance["elected_count"] = int(count_match.group(2))

    excused_match = re.search(
        r"(?:Excus[ée]\(e\)s?\s*:|Les conseill[èe]res? et conseillers suivants se sont excus[ée]s?\s*:)(.*?)(?:\n\s*Absent\(e\)s?\s*:|\n\s*Est absent(?:e)?\s*:|\n\s*Il rend attentif|\n\s*M\. le Pr[ée]sident ouvre|\n\s*Mme la Pr[ée]sidente ouvre|\n\s*Le Conseil communal|\n\s*\d+\.\s*\n)",
        text,
        flags=re.I | re.S,
    )
    if excused_match:
        attendance["excused"] = split_people(excused_match.group(1))

    absent_match = re.search(
        r"(?:Absent\(e\)s?|Est absent(?:e)?)\s*:(.*?)(?:\n\s*M\. le Pr[ée]sident ouvre|\n\s*Mme la Pr[ée]sidente ouvre|\n\s*Il rend attentif|\n\s*\d+\.\s*\n)",
        text,
        flags=re.I | re.S,
    )
    if absent_match:
        attendance["absent"] = split_people(absent_match.group(1))
    return attendance


def vote_result_from_text(value: str) -> str | None:
    normalized = strip_accents(value).lower()
    if "unanimite" in normalized:
        return "unanimous"
    if "large majorite" in normalized:
        return "large_majority"
    if "majorite" in normalized:
        return "majority"
    if "refuse" in normalized or "rejete" in normalized:
        return "rejected"
    return None


def extract_previous_minutes(text: str) -> list[dict]:
    previous = []
    pattern = re.compile(
        r"Adoption du proc[èe]s-verbal\s+N[°o]\s*(\d+).*?s[ée]ance du\s+(\d{1,2}\s+\w+\s+\d{4}).{0,360}?est adopt[ée].{0,160}?(?:\(([^)]*)\))?",
        flags=re.I | re.S,
    )
    for match in pattern.finditer(text):
        item = {
            "pv_number": int(match.group(1)),
            "session_date_label": normalize_spaces(match.group(2)),
            "decision": "adopted",
        }
        vote_text = match.group(0)
        result = vote_result_from_text(vote_text)
        if result:
            item["vote_result"] = result
        abstentions = re.search(r"(\d+|une|deux|trois)\s+abstention", vote_text, flags=re.I)
        if abstentions:
            item["abstentions_label"] = abstentions.group(1)
        previous.append(item)
    return previous


def extract_decisions(text: str, agenda: list[dict]) -> list[dict]:
    decisions = []
    for item in agenda:
        title = item["title"]
        title_pattern = re.escape(title[:80])
        match = re.search(title_pattern + r".{0,450}?(Au vote,.{0,240}?\.|est adopt[ée].{0,160}?\.)", text, flags=re.I | re.S)
        if not match:
            continue
        decision_text = normalize_spaces(match.group(1))
        result = vote_result_from_text(decision_text)
        decision = {
            "agenda_item": item["number"],
            "object": title,
            "decision_text": decision_text,
        }
        if result:
            decision["vote_result"] = result
            decision["decision"] = "adopted" if result != "rejected" else "rejected"
        decisions.append(decision)
    return decisions[:20]


def extract_political_objects(agenda: list[dict]) -> list[dict]:
    objects = []
    object_patterns = [
        ("motion", r"\bMotion\b"),
        ("postulat", r"\bPostulat\b"),
        ("interpellation", r"\bInterpellation\b"),
        ("preavis", r"\bPr[ée]avis municipal\s+N[°o]\s*([0-9]+/[0-9]{4})"),
        ("communication", r"\bCommunication municipale\s+N[°o]\s*([0-9]+/[0-9]{4})"),
        ("report", r"\bRapport\b"),
    ]
    for item in agenda:
        title = item["title"]
        for object_type, pattern in object_patterns:
            match = re.search(pattern, title, flags=re.I)
            if not match:
                continue
            obj = {
                "agenda_item": item["number"],
                "type": object_type,
                "title": title,
            }
            if match.groups():
                obj["number"] = match.group(1)
            author = re.search(r"(?:de|du groupe)\s+(M\.|Mme)\s+([^–-]+?)(?:\s+\(([^)]+)\))?\s+[–-]", title)
            if author:
                obj["author"] = normalize_person_name(f"{author.group(1)} {author.group(2)}")
                if author.group(3):
                    obj["party"] = author.group(3)
            objects.append(obj)
            break
    return objects


def enrich_pv_metadata(metadata: dict, metadata_path: Path) -> dict:
    text = read_text_for_metadata(metadata_path)
    if not text:
        return metadata

    agenda = extract_agenda(text)
    attendance = extract_attendance(text)
    session_details = extract_session_details(text)
    previous_minutes = extract_previous_minutes(text)
    decisions = extract_decisions(text, agenda)
    political_objects = extract_political_objects(agenda)

    if session_details:
        metadata["session_details"] = session_details
        if session_details.get("president"):
            metadata["president"] = session_details["president"]
        if session_details.get("secretary"):
            metadata["secretary"] = session_details["secretary"]
    if attendance:
        metadata["attendance"] = attendance
    if agenda:
        metadata["agenda"] = agenda
    if previous_minutes:
        metadata["previous_minutes"] = previous_minutes
    if decisions:
        metadata["decisions"] = decisions
    if political_objects:
        metadata["political_objects_discussed"] = political_objects
    return metadata


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
        metadata = enrich_pv_metadata(metadata, metadata_path)
        write_json(metadata_path, metadata)

        summary_fields = {
            key: metadata[key]
            for key in [
                "session_details",
                "attendance",
                "agenda",
                "previous_minutes",
                "decisions",
                "political_objects_discussed",
                "president",
                "secretary",
            ]
            if key in metadata
        }
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
                **summary_fields,
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
