import html
import re
import unicodedata


MOJIBAKE_MARKERS = ("Ã", "â€", "â€™", "â€“", "â€œ", "â€", "ï¿½", "\ufffd")


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
