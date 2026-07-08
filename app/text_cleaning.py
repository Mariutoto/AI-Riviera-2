import html
import re
import unicodedata


MOJIBAKE_MARKERS = (
    "\u00c3",
    "\u00c2",
    "\u00e2\u20ac",
    "\u00c5\u201c",
    "\u00c5\u2019",
    "\u00ef\u00bf\u00bd",
    "\ufffd",
)


def _badness(text: str) -> int:
    marker_score = sum(text.count(marker) * 5 for marker in MOJIBAKE_MARKERS)
    replacement_score = text.count("\ufffd") * 20
    control_score = sum(
        1
        for char in text
        if unicodedata.category(char)[0] == "C" and char not in "\n\t"
    )
    return marker_score + replacement_score + control_score


def fix_mojibake(text: str) -> str:
    """Repair common UTF-8 French text accidentally decoded as latin-1/cp1252."""
    if not text:
        return text

    try:
        from ftfy import fix_text

        return fix_text(text)
    except Exception:
        pass

    candidates = [text]
    for encoding in ("latin1", "cp1252"):
        try:
            candidates.append(text.encode(encoding).decode("utf-8"))
        except UnicodeError:
            pass

    return min(candidates, key=_badness)


def clean_french_text(text: str) -> str:
    text = fix_mojibake(text)
    text = html.unescape(text)
    text = text.replace("\x00", " ").replace("\xa0", " ")
    text = re.sub(r"[\x01-\x08\x0b\x0c\x0e-\x1f]", " ", text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def strip_accents(text: str) -> str:
    text = fix_mojibake(text)
    text = unicodedata.normalize("NFKD", text)
    return "".join(char for char in text if not unicodedata.combining(char))


FRENCH_MONTHS = {
    "01": "janvier",
    "02": "février",
    "03": "mars",
    "04": "avril",
    "05": "mai",
    "06": "juin",
    "07": "juillet",
    "08": "août",
    "09": "septembre",
    "10": "octobre",
    "11": "novembre",
    "12": "décembre",
}


def format_date(value) -> str:
    raw = str(value)[:10] if value else ""
    match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", raw)
    if not match:
        return raw
    year, month, day = match.groups()
    return f"{int(day)} {FRENCH_MONTHS.get(month, month)} {year}"
