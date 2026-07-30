from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

import fitz
import lxml.html
import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SEARCH_ENDPOINT = (
    "https://www.conseilmontreux.ch/coreapi/elasticsearch/projects/"
    "conseilmontreuxpublic/full/search"
)
DETAIL_PAGE = (
    "https://www.conseilmontreux.ch/conseilcommunal/public/"
    "documents/recherche/"
)
LEGISLATURE_START = date(2021, 7, 1)
LEGISLATURE_END = date(2026, 6, 30)
PAGE_SIZE = 20
OBJECT_FILTER = "objectType:3|Interpellation"
HEADERS = {
    "User-Agent": (
        "AI-Riviera/1.0 Montreux council-document research "
        "(public-interest indexing)"
    )
}
MONTHS = {
    "janvier": 1,
    "fevrier": 2,
    "mars": 3,
    "avril": 4,
    "mai": 5,
    "juin": 6,
    "juillet": 7,
    "aout": 8,
    "septembre": 9,
    "octobre": 10,
    "novembre": 11,
    "decembre": 12,
}

# The current site contains two response records whose source text is not
# reproduced, and one empty response event. Keeping these explicit makes the
# pilot auditable. A future generic detector can replace the overrides after
# it has learned how to distinguish speaker-only acknowledgements.
CONFIRMED_RESPONSE_WITHOUT_SOURCE = {2725, 3208}
EMPTY_RESPONSE_EVENT = {3115}


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def compact(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def ascii_text(value: str) -> str:
    return "".join(
        character
        for character in unicodedata.normalize("NFKD", value.casefold())
        if not unicodedata.combining(character)
    )


def normalized_words(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", ascii_text(value)).strip()


def normalize_title(value: str) -> str:
    normalized = normalized_words(value)
    normalized = re.sub(
        r"^interpellation(?:\s+de\s+m(?:me)?\s+.+?\s+intitulee)?\s+",
        "",
        normalized,
    )
    return normalized


def parse_french_date(value: str) -> date | None:
    match = re.search(
        r"\b(\d{1,2})(?:er)?\s+([a-z]+)\s+(20\d{2})\b",
        ascii_text(value),
    )
    if not match or match.group(2) not in MONTHS:
        return None
    return date(
        int(match.group(3)),
        MONTHS[match.group(2)],
        int(match.group(1)),
    )


def iso_date(value: str) -> str:
    parsed = parse_french_date(value)
    return parsed.isoformat() if parsed else ""


def result_count(fragment: str) -> int:
    match = re.search(
        r"sur\s+(\d+)\s+[ée]l[ée]ments?\s+trouv[ée]s",
        fragment,
        re.I,
    )
    if not match:
        raise ValueError("Nombre de résultats Montreux introuvable")
    return int(match.group(1))


def _event_from_node(node) -> dict:
    kind = compact(node.xpath("string(.//strong[1])"))
    date_text = compact(
        node.xpath("string(.//small[contains(@class,'float-right')][1])")
        or node.xpath("string(.//small[1])")
    )
    full_text = compact(node.text_content())
    body = full_text
    if kind and body.startswith(kind):
        body = body[len(kind) :].strip()
    if date_text and body.startswith(date_text):
        body = body[len(date_text) :].strip()
    return {
        "kind": kind,
        "date_text": date_text,
        "date": iso_date(date_text),
        "text": body,
        "text_characters": len(body),
    }


def parse_search_fragment(fragment: str) -> list[dict]:
    document = lxml.html.fromstring(fragment)
    rows = []
    for anchor in document.xpath(
        "//div[@id='search-standard-result']"
        "//a[starts-with(translate(@href,'id','ID'),'?ID=')]"
    ):
        match = re.search(r"[?&]id=(\d+)", anchor.get("href") or "", re.I)
        if not match:
            continue
        card = anchor
        while card is not None and "list-group-item-action" not in (
            card.get("class") or ""
        ):
            card = card.getparent()
        if card is None:
            continue
        events = [
            _event_from_node(node)
            for node in card.xpath(
                ".//li[contains(@class,'list-group-item')]"
            )
        ]
        author_text = compact(
            card.xpath(
                "string(.//small[starts-with(normalize-space(.),'Auteur')][1])"
            )
        )
        rows.append(
            {
                "source_object_id": int(match.group(1)),
                "title": compact(anchor.text_content()),
                "author_text": re.sub(
                    r"^Auteur\s*:\s*", "", author_text, flags=re.I
                ),
                "events": events,
            }
        )
    return rows


def _document_row(node) -> dict | None:
    anchors = node.xpath(".//a[contains(@class,'docname')][1]")
    if not anchors:
        return None
    anchor = anchors[0]
    url = urljoin(DETAIL_PAGE, anchor.get("href") or "")
    download_values = parse_qs(urlparse(url).query).get("d", [])
    cells = node.xpath("./div")
    return {
        "download_id": download_values[0] if download_values else "",
        "filename": anchor.get("title")
        or compact(anchor.text_content()).removesuffix(" Kb"),
        "pdf_url": url,
        "displayed_type": (
            compact(cells[-2].text_content()) if len(cells) >= 2 else ""
        ),
        "attachment_date_text": (
            compact(cells[-1].text_content()) if cells else ""
        ),
        "attachment_date": (
            datetime.strptime(
                compact(cells[-1].text_content()), "%d.%m.%Y"
            )
            .date()
            .isoformat()
            if cells
            and re.fullmatch(
                r"\d{2}\.\d{2}\.\d{4}",
                compact(cells[-1].text_content()),
            )
            else ""
        ),
    }


def parse_detail_page(page_html: str, source_object_id: int) -> dict:
    document = lxml.html.fromstring(page_html)
    title = compact(document.xpath("string(//main//h5[1])"))
    author_text = compact(
        document.xpath(
            "string(//main//small[contains(normalize-space(.),'Auteur')][1])"
        )
    )
    author_text = re.sub(r"^Auteur\s*:\s*", "", author_text, flags=re.I)
    events = [
        _event_from_node(node)
        for node in document.xpath(
            "//section[contains(@class,'tplArticleDetail')]"
            "//ul[contains(@class,'list-group')]/li"
        )
    ]
    attachments = []
    seen_download_ids = set()
    for node in document.xpath("//div[contains(@class,'table-row-body')]"):
        row = _document_row(node)
        if not row:
            continue
        identity = row["download_id"] or row["pdf_url"]
        if identity in seen_download_ids:
            continue
        seen_download_ids.add(identity)
        attachments.append(row)
    session_links = document.xpath(
        "//a[contains(normalize-space(.),'Séance du Conseil concernée')]/@href"
    )
    return {
        "source_object_id": source_object_id,
        "source_page": f"{DETAIL_PAGE}?id={source_object_id}",
        "title": title,
        "author_text": author_text,
        "events": events,
        "attachments": attachments,
        "session_url": (
            urljoin(DETAIL_PAGE, session_links[0])
            if session_links
            else ""
        ),
    }


def parse_author(value: str) -> dict:
    value = compact(value)
    match = re.match(r"(?P<name>.+?)\s*\((?P<party>[^()]*)\)\s*$", value)
    name = compact(match.group("name")) if match else value
    party = compact(match.group("party")) if match else ""
    civility = ""
    if re.match(r"^Mme\b", name, re.I):
        civility = "Mme"
        name = re.sub(r"^Mme\s+", "", name, flags=re.I)
    elif re.match(r"^M\.\s*", name, re.I):
        civility = "M."
        name = re.sub(r"^M\.\s*", "", name, flags=re.I)
    return {
        "name": name,
        "party": party,
        **({"civility": civility} if civility else {}),
        "role": "interpellateur",
    }


def response_like_filename(filename: str) -> bool:
    normalized = normalized_words(filename)
    if "simplequestion" in normalized.replace(" ", ""):
        return False
    return (
        "reponse" in normalized
        or bool(re.search(r"\b(?:rep|mun)\b", normalized))
    ) and "interpellation" in normalized


def pdf_text(content: bytes) -> tuple[str, dict]:
    with fitz.open(stream=content, filetype="pdf") as pdf:
        page_texts = [page.get_text("text") for page in pdf]
        image_counts = [len(page.get_images(full=True)) for page in pdf]
    text = "\n\n".join(page_texts).strip()
    page_characters = [len(compact(value)) for value in page_texts]
    needs_ocr = len(compact(text)) < max(300, len(page_texts) * 100)
    return text, {
        "page_count": len(page_texts),
        "page_text_characters": page_characters,
        "image_counts": image_counts,
        "empty_pages": sum(not count for count in page_characters),
        "text_characters": len(text),
        "text_words": len(re.findall(r"\S+", text)),
        "needs_ocr": needs_ocr,
        "text_preview": compact(text)[:1200],
    }


def title_similarity(title: str, pdf_text_value: str) -> float:
    title_tokens = set(normalize_title(title).split())
    header_tokens = set(normalized_words(pdf_text_value[:3000]).split())
    if not title_tokens:
        return 0.0
    return round(len(title_tokens & header_tokens) / len(title_tokens), 4)


def fetch_search_page(
    page: int, session: requests.Session | None = None
) -> str:
    client = session or requests.Session()
    response = client.get(
        SEARCH_ENDPOINT,
        params={
            "q": "",
            "f": OBJECT_FILTER,
            "s": "date",
            "page": page,
        },
        headers=HEADERS,
        timeout=45,
    )
    response.raise_for_status()
    return response.content.decode("utf-8")


def collect_index(
    session: requests.Session | None = None,
) -> tuple[list[dict], dict]:
    client = session or requests.Session()
    first = fetch_search_page(1, client)
    expected = result_count(first)
    pages = max(1, (expected + PAGE_SIZE - 1) // PAGE_SIZE)
    rows = parse_search_fragment(first)
    for page in range(2, pages + 1):
        rows.extend(parse_search_fragment(fetch_search_page(page, client)))
    unique = {row["source_object_id"]: row for row in rows}
    if len(unique) != expected:
        raise ValueError(
            f"Collecte Montreux incomplète: {len(unique)} sur {expected}"
        )
    return list(unique.values()), {
        "endpoint_results": expected,
        "pages_fetched": pages,
        "parsed_results": len(rows),
        "unique_source_ids": len(unique),
        "complete": len(unique) == expected,
    }


def _deposit_date(row: dict) -> date | None:
    for event in row["events"]:
        if normalized_words(event["kind"]) == "depot":
            return parse_french_date(event["date_text"])
    return None


def fetch_detail(source_object_id: int) -> dict:
    response = requests.get(
        DETAIL_PAGE,
        params={"id": source_object_id},
        headers=HEADERS,
        timeout=45,
    )
    response.raise_for_status()
    return parse_detail_page(
        response.content.decode("utf-8"), source_object_id
    )


def collect_objects(index_rows: list[dict]) -> tuple[list[dict], dict]:
    current_rows = [
        row
        for row in index_rows
        if (
            (deposit := _deposit_date(row))
            and LEGISLATURE_START <= deposit <= LEGISLATURE_END
        )
    ]
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        details = list(
            pool.map(
                fetch_detail,
                sorted(row["source_object_id"] for row in current_rows),
            )
        )
    by_title: dict[str, list[int]] = defaultdict(list)
    for row in index_rows:
        by_title[normalize_title(row["title"])].append(
            row["source_object_id"]
        )
    for detail in details:
        deposit_events = [
            event
            for event in detail["events"]
            if normalized_words(event["kind"]) == "depot"
        ]
        if len(deposit_events) != 1 or not deposit_events[0]["date"]:
            raise ValueError(
                f"Dépôt officiel ambigu pour l'objet {detail['source_object_id']}"
            )
        detail["deposit_date"] = deposit_events[0]["date"]
        detail["deposit_text"] = deposit_events[0]["text"]
        detail["response_events"] = [
            event
            for event in detail["events"]
            if normalized_words(event["kind"]).startswith(
                "reponse a l interpellation"
            )
        ]
        detail["authors"] = [parse_author(detail["author_text"])]
        detail["source_alias_ids"] = sorted(
            set(by_title.get(normalize_title(detail["title"]), []))
            - {detail["source_object_id"]}
        )
    return sorted(details, key=lambda item: item["source_object_id"]), {
        "legislature_source_ids": len(current_rows),
        "canonical_objects": len(details),
        "empty_duplicate_aliases": sum(
            bool(item["source_alias_ids"]) for item in details
        ),
    }


def download_candidate(
    object_record: dict,
    attachment: dict,
    output_dir: Path,
) -> dict:
    response = requests.get(
        attachment["pdf_url"],
        headers=HEADERS,
        timeout=90,
    )
    response.raise_for_status()
    content = response.content
    if not content.startswith(b"%PDF"):
        raise ValueError(
            f"{attachment['download_id']} n'est pas un PDF Montreux"
        )
    extracted, audit = pdf_text(content)
    document_id = (
        f"montreux_interpellation_{object_record['source_object_id']}_"
        f"response_{attachment['download_id']}"
    )
    target = output_dir / f"{document_id}.pdf"
    target.write_bytes(content)
    return {
        **attachment,
        "document_id": document_id,
        "political_object_source_id": object_record["source_object_id"],
        "object_title": object_record["title"],
        "document_role": "municipal_response",
        "local_pdf": str(target.relative_to(PROJECT_ROOT)).replace("\\", "/"),
        "sha256": hashlib.sha256(content).hexdigest(),
        "text_audit": audit,
        "title_match_score": title_similarity(
            object_record["title"], extracted
        ),
        "response_header_detected": (
            "reponse de la municipalite"
            in normalized_words(extracted[:2500])
        ),
    }


def collect_response_pdfs(
    objects: list[dict],
    output_dir: Path,
) -> tuple[list[dict], dict]:
    output_dir.mkdir(parents=True, exist_ok=True)
    candidates = [
        (object_record, attachment)
        for object_record in objects
        for attachment in object_record["attachments"]
        if response_like_filename(attachment["filename"])
    ]
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        downloaded = list(
            pool.map(
                lambda pair: download_candidate(
                    pair[0], pair[1], output_dir
                ),
                candidates,
            )
        )
    grouped: dict[tuple[int, str], list[dict]] = defaultdict(list)
    for document in downloaded:
        grouped[
            (
                document["political_object_source_id"],
                document["sha256"],
            )
        ].append(document)
    type_rank = {"Réponse": 3, "Annexe": 2, "Interpellation": 1}
    canonical = []
    for group in grouped.values():
        selected = max(
            group,
            key=lambda item: (
                type_rank.get(item["displayed_type"], 0),
                int(item["download_id"] or 0),
            ),
        )
        selected["source_download_aliases"] = sorted(
            {
                item["download_id"]
                for item in group
                if item["download_id"] != selected["download_id"]
            }
        )
        canonical.append(selected)
        for duplicate in group:
            if duplicate["local_pdf"] != selected["local_pdf"]:
                duplicate_path = PROJECT_ROOT / duplicate["local_pdf"]
                if duplicate_path.exists():
                    duplicate_path.unlink()
    canonical.sort(
        key=lambda item: (
            item["political_object_source_id"],
            item["download_id"],
        )
    )
    return canonical, {
        "candidate_attachment_rows": len(candidates),
        "downloaded_attachment_rows": len(downloaded),
        "canonical_response_pdfs": len(canonical),
        "duplicate_attachment_rows_removed": len(downloaded)
        - len(canonical),
        "responses_needing_ocr": sum(
            document["text_audit"]["needs_ocr"]
            for document in canonical
        ),
        "response_headers_detected": sum(
            document["response_header_detected"]
            for document in canonical
        ),
    }


def classify_objects(
    objects: list[dict], response_pdfs: list[dict]
) -> dict:
    responses_by_object: dict[int, list[dict]] = defaultdict(list)
    for document in response_pdfs:
        responses_by_object[
            document["political_object_source_id"]
        ].append(document)
    counts = defaultdict(int)
    for item in objects:
        source_id = item["source_object_id"]
        linked = responses_by_object.get(source_id, [])
        response_events = item["response_events"]
        nonempty_events = [
            event for event in response_events if len(event["text"]) >= 40
        ]
        if linked:
            status = "written_response"
            has_response = True
        elif source_id in CONFIRMED_RESPONSE_WITHOUT_SOURCE:
            status = "response_confirmed_missing_source"
            has_response = True
        elif nonempty_events:
            status = "oral_or_transcribed_response"
            has_response = True
        elif response_events or source_id in EMPTY_RESPONSE_EVENT:
            status = "empty_response_event_unverified"
            has_response = False
        else:
            status = "unanswered"
            has_response = False
        response_dates = [
            event["date"]
            for event in response_events
            if event.get("date")
        ]
        item["object_id"] = f"montreux-interpellation-{source_id}"
        item["response_status"] = status
        item["has_response"] = has_response
        item["is_closed"] = has_response
        item["response_date"] = (
            response_dates[-1] if response_dates else ""
        )
        item["response_document_ids"] = [
            document["document_id"] for document in linked
        ]
        counts[status] += 1
    return dict(sorted(counts.items()))


def main() -> None:
    root = (
        PROJECT_ROOT
        / "audit-montreux"
        / "interpellations-2021-2026"
    )
    parser = argparse.ArgumentParser(
        description=(
            "Scrape les interpellations du Conseil communal de Montreux "
            "pour la législature 2021-2026"
        )
    )
    parser.add_argument("--output", type=Path, default=root / "inventory.json")
    parser.add_argument("--download-dir", type=Path, default=root / "pdfs")
    args = parser.parse_args()

    index_rows, index_diagnostics = collect_index()
    objects, object_diagnostics = collect_objects(index_rows)
    responses, response_diagnostics = collect_response_pdfs(
        objects, args.download_dir
    )
    status_counts = classify_objects(objects, responses)
    payload = {
        "schema_version": "montreux-interpellations-inventory-v1",
        "generated_at": datetime.now().astimezone().isoformat(
            timespec="seconds"
        ),
        "source": DETAIL_PAGE,
        "search_endpoint": SEARCH_ENDPOINT,
        "scope": {
            "city": "Montreux",
            "category": "interpellation",
            "legislature": "2021-2026",
            "from": LEGISLATURE_START.isoformat(),
            "to": LEGISLATURE_END.isoformat(),
        },
        "automatic_detection_notes": {
            "object_filter": OBJECT_FILTER,
            "page_size": PAGE_SIZE,
            "object_identity": (
                "source id, then normalized title/author for empty aliases"
            ),
            "response_filename_signals": [
                "reponse",
                "reponsemun",
                "mun_reponse",
                "interpellation",
            ],
            "response_content_signal": "REPONSE DE LA MUNICIPALITE",
            "attachment_type_is_not_authoritative": True,
            "future_work": (
                "Replace the three reviewed response-event overrides with "
                "speaker-aware automatic classification."
            ),
        },
        "diagnostics": {
            **index_diagnostics,
            **object_diagnostics,
            **response_diagnostics,
            "response_status_counts": status_counts,
        },
        "objects": objects,
        "response_documents": responses,
    }
    write_json(args.output, payload)
    print(json.dumps(payload["diagnostics"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
