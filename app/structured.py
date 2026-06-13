import json
import re
from collections import Counter
from functools import lru_cache
from typing import Any

from app.config import STRUCTURED_DATA_DIR
from app.diagnostics import record_diagnostic
from app.text_cleaning import fix_mojibake, strip_accents


DEPOSIT_TYPES = {"motion", "postulat", "interpellation"}
DEPOSIT_CATEGORY_BY_TYPE = {
    "motion": "motions",
    "postulat": "postulats",
    "interpellation": "interpellations",
}
DEPOSIT_PREFIX_BY_TYPE = {
    "motion": "Motion",
    "postulat": "Postulat",
    "interpellation": "Interpellation",
}
YEAR_PATTERN = re.compile(r"\b(20\d{2})\b")
MOST_PATTERN = re.compile(r"\b(qui|quel|quelle|quels|quelles)\b.*\b(plus|maximum|le plus)\b")


def normalize(text: str) -> str:
    return strip_accents(text).lower()


def compact_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def postgres_rows(sql: str, params: tuple = (), operation: str = "structured_query") -> list[dict[str, Any]] | None:
    try:
        from app.postgres_store import _connect

        with _connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                return list(cursor.fetchall())
    except Exception as exc:
        record_diagnostic(
            "structured",
            "Structured SQL query failed",
            exc,
            operation=operation,
            params=repr(params)[:500],
        )
        return None


def structured_tables_ready() -> bool:
    rows = postgres_rows(
        """
        SELECT to_regclass('public.people') AS people,
               to_regclass('public.political_objects') AS political_objects,
               to_regclass('public.political_object_people') AS political_object_people,
               to_regclass('public.political_object_documents') AS political_object_documents
        """
        ,
        operation="structured_tables_ready",
    )
    return bool(
        rows
        and rows[0].get("people")
        and rows[0].get("political_objects")
        and rows[0].get("political_object_people")
        and rows[0].get("political_object_documents")
    )


@lru_cache(maxsize=1)
def load_structured_data() -> dict:
    def read(name: str) -> list[dict]:
        path = STRUCTURED_DATA_DIR / name
        if not path.exists():
            return []
        return json.loads(path.read_text(encoding="utf-8-sig"))

    return {
        "sessions": read("sessions.json"),
        "political_objects": read("political_objects.json"),
        "documents": read("documents.json"),
    }


def latest_session(sessions: list[dict]) -> dict | None:
    dated_sessions = [session for session in sessions if session.get("session_date")]
    if not dated_sessions:
        return None
    return sorted(dated_sessions, key=lambda item: item["session_date"])[-1]


def wants_latest_session(question: str) -> bool:
    normalized = normalize(question)
    return any(term in normalized for term in ["derniere seance", "derniere séance", "plus recente", "plus recente"])


def wants_deposit_count(question: str) -> bool:
    normalized = normalize(question)
    has_count = any(term in normalized for term in ["combien", "nombre", "nb", "liste", "quels", "quelles"])
    has_deposit = any(term in normalized for term in ["depot", "depose", "deposes", "depots", "motion", "postulat", "interpellation"])
    return has_count and has_deposit


def extract_year(question: str) -> str | None:
    match = YEAR_PATTERN.search(normalize(question))
    return match.group(1) if match else None


def requested_object_types(question: str) -> set[str]:
    normalized = normalize(question)
    requested = set()
    if any(term in normalized for term in ["interpellation", "interpellations"]):
        requested.add("interpellation")
    if any(term in normalized for term in ["postulat", "postulats"]):
        requested.add("postulat")
    if any(term in normalized for term in ["motion", "motions"]):
        requested.add("motion")
    return requested or set(DEPOSIT_TYPES)


def object_type_label(object_type: str) -> str:
    return {
        "motion": "Motion",
        "postulat": "Postulat",
        "interpellation": "Interpellation",
    }.get(object_type, object_type)


def object_type_count_label(object_type: str, count: int) -> str:
    label = object_type_label(object_type).lower()
    if count > 1:
        label = f"{label}s"
    return f"{count} {label}"


def source_markdown(source_url: str, label: str = "source") -> str:
    if not source_url:
        return ""
    return f" [{label}]({source_url})"


def format_date(value: Any) -> str:
    raw = str(value)[:10] if value else ""
    match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", raw)
    if not match:
        return raw
    year, month, day = match.groups()
    months = {
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
    return f"{int(day)} {months.get(month, month)} {year}"


def status_stage_label(status_stage: Any) -> str:
    return {
        "referred": "Renvoyée à la Municipalité",
        "pending": "En attente",
        "submitted": "Déposé",
        "decided": "Traité",
        "answered": "Réponse publiée",
        "closed": "Clos",
    }.get(str(status_stage or ""), str(status_stage or "").replace("_", " "))


def status_decision_label(status_decision: Any) -> str:
    return {
        "pending_municipality": "En attente de traitement par la Municipalité",
        "referred_directly_to_municipality": "Renvoyée directement à la Municipalité",
        "pending": "En attente",
        "awaiting_response": "En attente de réponse",
        "response_available": "Réponse disponible",
        "decision_available": "Décision disponible",
        "accepted": "Accepté",
        "refused": "Refusé",
        "withdrawn": "Retiré",
        "not_supported": "Non soutenu",
    }.get(str(status_decision or ""), str(status_decision or "").replace("_", " "))


def author_names(row: dict[str, Any]) -> list[str]:
    authors = row.get("authors") or []
    if not isinstance(authors, list):
        return []
    names = []
    for author in authors:
        if isinstance(author, dict):
            name = fix_mojibake(str(author.get("name") or "")).strip()
            party = fix_mojibake(str(author.get("party") or "")).strip()
            if name and party:
                names.append(f"{name} ({party})")
            elif name:
                names.append(name)
        elif str(author).strip():
            names.append(fix_mojibake(str(author)).strip())
    return list(dict.fromkeys(names))


def compact_author_text(row: dict[str, Any]) -> str:
    names = author_names(row)
    if not names:
        return ""
    if len(names) <= 2:
        return ", ".join(names)
    return f"{', '.join(names[:2])} et {len(names) - 2} autres"


def find_person_in_question(question: str) -> dict[str, Any] | None:
    normalized_question = normalize(question)
    rows = postgres_rows(
        """
        SELECT person_id, canonical_name, normalized_name, party_current, variants
        FROM people
        ORDER BY length(normalized_name) DESC
        """,
        operation="find_person_in_question",
    )
    if rows is None:
        return None
    for row in rows:
        names = [str(row.get("normalized_name") or "")]
        names.extend(normalize(str(variant)) for variant in row.get("variants") or [])
        for name in names:
            name = compact_spaces(name)
            if name and name in normalized_question:
                return row
    return None


def sources_for_object_ids(object_ids: list[str], limit_per_object: int = 2) -> dict[str, list[dict[str, Any]]]:
    if not object_ids:
        return {}
    rows = postgres_rows(
        """
        SELECT
            pod.object_id,
            pod.relation_type,
            pod.source_url,
            pod.filename,
            pod.title,
            pod.order_index,
            d.source_url AS document_source_url,
            d.title AS document_title
        FROM political_object_documents pod
        LEFT JOIN documents d ON d.id = pod.document_id
        WHERE pod.object_id = ANY(%s)
        ORDER BY
            pod.object_id,
            CASE WHEN pod.relation_type LIKE 'canonical%%' THEN 0 ELSE 1 END,
            pod.order_index,
            pod.relation_type
        """,
        (object_ids,),
        operation="sources_for_object_ids",
    )
    if rows is None:
        return {}
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row["object_id"], [])
        if len(grouped[row["object_id"]]) < limit_per_object:
            grouped[row["object_id"]].append(row)
    return grouped


def object_source_suffix(object_id: str, sources_by_object: dict[str, list[dict[str, Any]]]) -> str:
    sources = sources_by_object.get(object_id) or []
    if not sources:
        return ""
    links = []
    for index, source in enumerate(sources, start=1):
        url = source.get("document_source_url") or source.get("source_url") or ""
        label = "PDF" if str(source.get("relation_type", "")).startswith("canonical") else "Document lié"
        suffix = f" {index}" if len(sources) > 1 else ""
        if url:
            links.append(f"[{label}{suffix}]({url})")
    return f" · {', '.join(links)}" if links else ""


def object_summary(row: dict[str, Any]) -> str:
    parts = [object_type_label(str(row.get("object_type") or ""))]
    author_text = compact_author_text(row)
    if author_text:
        parts.append(author_text)
    if row.get("deposit_date"):
        parts.append(f"déposée le {format_date(row['deposit_date'])}")
    if row.get("status_stage"):
        parts.append(status_stage_label(row["status_stage"]))
    elif row.get("status_decision"):
        parts.append(status_decision_label(row["status_decision"]))
    return " · ".join(part for part in parts if part)


def wants_person_deposits(question: str) -> bool:
    normalized = normalize(question)
    return any(
        term in normalized
        for term in [
            "qu a depose",
            "qu a-t-il depose",
            "qu a-t-elle depose",
            "a depose",
            "depose par",
            "deposes par",
            "objets deposes",
        ]
    )


def answer_person_deposits_db(question: str) -> str | None:
    if not wants_person_deposits(question):
        return None
    person = find_person_in_question(question)
    if not person:
        return None
    object_types = requested_object_types(question)
    year = extract_year(question)
    params: list[Any] = [person["person_id"], sorted(object_types)]
    year_filter = ""
    if year:
        year_filter = "AND (po.year = %s OR EXTRACT(YEAR FROM po.deposit_date)::text = %s OR po.object_id LIKE %s)"
        params.extend([year, year, f"%-{year}-%"])
    rows = postgres_rows(
        f"""
        SELECT DISTINCT
            po.object_id, po.object_type, po.year, po.object_title,
            po.status_stage, po.status_decision, po.deposit_date, po.decision_date, po.response_date,
            pop.role, pop.party_at_time
        FROM political_object_people pop
        JOIN political_objects po ON po.object_id = pop.object_id
        WHERE pop.person_id = %s
          AND po.object_type = ANY(%s)
          {year_filter}
        ORDER BY po.year DESC, po.deposit_date DESC NULLS LAST, po.object_title
        """,
        tuple(params),
        operation="answer_person_deposits",
    )
    if rows is None:
        return None
    if not rows:
        return f"Je ne trouve aucun objet politique déposé par **{person['canonical_name']}** dans les tables structurées."

    sources = sources_for_object_ids([row["object_id"] for row in rows])
    lines = [
        f"Dans la base structurée, **{person['canonical_name']}** est lié à **{len(rows)} objet(s)** comme auteur ou représentant.",
        "",
    ]
    for index, row in enumerate(rows, start=1):
        source_suffix = object_source_suffix(row["object_id"], sources)
        lines.append(
            f"{index}. **{row['object_title']}** - {object_summary(row)}"
            f"; rôle `{row['role']}`; parti `{row['party_at_time']}`{source_suffix}"
        )
    return "\n".join(lines)


def wants_coauthors(question: str) -> bool:
    normalized = normalize(question)
    return any(term in normalized for term in ["avec qui", "co auteur", "coauteur", "co-auteur", "depose avec", "souvent avec"])


def answer_coauthors_db(question: str) -> str | None:
    if not wants_coauthors(question):
        return None
    person = find_person_in_question(question)
    if not person:
        return None
    object_types = requested_object_types(question)
    rows = postgres_rows(
        """
        SELECT
            p2.person_id,
            p2.canonical_name,
            p2.party_current,
            COUNT(DISTINCT p2rel.object_id) AS count,
            jsonb_agg(DISTINCT jsonb_build_object(
                'object_id', po.object_id,
                'object_type', po.object_type,
                'year', po.year,
                'title', po.object_title
            )) AS objects
        FROM political_object_people p1
        JOIN political_object_people p2rel ON p2rel.object_id = p1.object_id
        JOIN people p2 ON p2.person_id = p2rel.person_id
        JOIN political_objects po ON po.object_id = p1.object_id
        WHERE p1.person_id = %s
          AND p2.person_id <> p1.person_id
          AND po.object_type = ANY(%s)
        GROUP BY p2.person_id, p2.canonical_name, p2.party_current
        ORDER BY count DESC, p2.canonical_name
        LIMIT 12
        """,
        (person["person_id"], sorted(object_types)),
        operation="answer_coauthors",
    )
    if rows is None:
        return None
    if not rows:
        return f"Je ne trouve pas de co-auteur récurrent avec **{person['canonical_name']}** dans les objets structurés."
    lines = [f"Co-auteurs ou co-signataires trouvés avec **{person['canonical_name']}** :", ""]
    for row in rows:
        objects = row.get("objects") or []
        examples = ", ".join(str(item.get("title", "")) for item in objects[:2])
        detail = f" Exemple: {examples}." if examples else ""
        lines.append(f"- **{row['canonical_name']}** ({row['party_current']}): **{row['count']}** objet(s).{detail}")
    return "\n".join(lines)


def wants_count_by_party(question: str) -> bool:
    normalized = normalize(question)
    return "par parti" in normalized and any(term in normalized for term in ["combien", "nombre", "compte", "repartition"])


def answer_count_by_party_db(question: str) -> str | None:
    if not wants_count_by_party(question):
        return None
    object_types = requested_object_types(question)
    year = extract_year(question)
    params: list[Any] = [sorted(object_types)]
    year_filter = ""
    if year:
        year_filter = "AND (po.year = %s OR EXTRACT(YEAR FROM po.deposit_date)::text = %s OR po.object_id LIKE %s)"
        params.extend([year, year, f"%-{year}-%"])
    rows = postgres_rows(
        f"""
        SELECT pop.party_at_time, COUNT(DISTINCT pop.object_id) AS count
        FROM political_object_people pop
        JOIN political_objects po ON po.object_id = pop.object_id
        WHERE po.object_type = ANY(%s)
          {year_filter}
        GROUP BY pop.party_at_time
        ORDER BY count DESC, pop.party_at_time
        """,
        tuple(params),
        operation="answer_count_by_party",
    )
    if rows is None:
        return None
    if not rows:
        return None
    type_text = ", ".join(sorted(object_types))
    year_text = f" en **{year}**" if year else ""
    total = sum(int(row["count"]) for row in rows)
    lines = [f"Répartition par parti pour **{type_text}**{year_text}: **{total} lien(s) parti-objet**.", ""]
    for row in rows:
        lines.append(f"- **{row['party_at_time']}**: {row['count']}")
    return "\n".join(lines)


def money_label(value: Any, currency: str = "CHF") -> str:
    if value in (None, ""):
        return ""
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return str(value)
    return f"{amount:,.0f} {currency}".replace(",", "'")


FINANCIAL_STOPWORDS = {
    "budget",
    "budgets",
    "compte",
    "comptes",
    "cout",
    "couts",
    "coût",
    "coûts",
    "depense",
    "depenses",
    "dépense",
    "dépenses",
    "charge",
    "charges",
    "revenu",
    "revenus",
    "montant",
    "montants",
    "quel",
    "quelle",
    "quels",
    "quelles",
    "combien",
    "dans",
    "pour",
    "les",
    "des",
    "aux",
    "avec",
    "etaient",
    "étaient",
}


def wants_financial_question(question: str) -> bool:
    normalized = normalize(question)
    return any(
        term in normalized
        for term in [
            "budget",
            "budgets",
            "compte",
            "comptes",
            "charges",
            "revenus",
            "depense",
            "depenses",
            "cout",
            "couts",
            "montant",
            "financier",
            "financiere",
            "personnel",
        ]
    )


def financial_terms(question: str) -> list[str]:
    normalized = normalize(question)
    tokens = re.findall(r"[a-z0-9]{3,}", normalized)
    terms = [token for token in tokens if token not in FINANCIAL_STOPWORDS and not re.fullmatch(r"20\d{2}", token)]
    if "personnel" in tokens:
        terms.extend(["personnel", "traitement", "traitements", "salaire", "salaires", "sociales"])
    if any(token in tokens for token in ["investissement", "investissements"]):
        terms.extend(["investissement", "investissements", "credit", "credits"])
    return list(dict.fromkeys(terms))[:12]


def answer_financial_db(question: str) -> str | None:
    if not wants_financial_question(question):
        return None
    year = extract_year(question)
    if not year:
        return None
    terms = financial_terms(question)
    patterns = [f"%{term}%" for term in terms]

    rows: list[dict[str, Any]] | None
    if patterns:
        rows = postgres_rows(
            """
            SELECT fal.fiscal_year, fal.service_name, fal.group_name, fal.account_number,
                   fal.account_label, fal.currency, fal.values, d.source_url, d.title
            FROM financial_account_lines fal
            JOIN documents d ON d.id = fal.document_id
            WHERE fal.fiscal_year = %s
              AND (
                fal.service_name ILIKE ANY(%s)
                OR fal.group_name ILIKE ANY(%s)
                OR fal.account_label ILIKE ANY(%s)
                OR fal.account_number ILIKE ANY(%s)
              )
            ORDER BY fal.service_name, fal.account_number
            LIMIT 10
            """,
            (int(year), patterns, patterns, patterns, patterns),
            operation="answer_financial_lines",
        )
    else:
        rows = []

    if rows is None:
        return None
    if rows:
        lines = [f"Dans les lignes financières du budget **{year}**, j'ai trouvé:", ""]
        for index, row in enumerate(rows, start=1):
            values = row.get("values") or {}
            amount = values.get("budget_current")
            if amount is None:
                amounts = values.get("amounts_sequence") or []
                amount = amounts[0] if amounts else None
            amount_text = money_label(amount, row.get("currency") or "CHF")
            amount_suffix = f" - **{amount_text}**" if amount_text else ""
            source = source_markdown(row.get("source_url") or "", "PDF")
            lines.append(
                f"{index}. **{row['account_label']}** ({row['account_number']}) - "
                f"{row['service_name']} / {row['group_name']}{amount_suffix}{source}"
            )
        return "\n".join(lines)

    summary_rows = postgres_rows(
        """
        SELECT fst.fiscal_year, fst.title, fst.metric, fsr.service_code, fsr.service_name,
               fsr.values, fst.currency, d.source_url
        FROM financial_summary_rows fsr
        JOIN financial_summary_tables fst ON fst.id = fsr.table_id
        JOIN documents d ON d.id = fst.document_id
        WHERE fst.fiscal_year = %s
        ORDER BY fst.metric, fsr.row_order
        LIMIT 12
        """,
        (int(year),),
        operation="answer_financial_summary",
    )
    if summary_rows is None:
        return None
    if not summary_rows:
        return None
    lines = [f"Pour le budget **{year}**, voici les lignes de synthèse disponibles:", ""]
    for index, row in enumerate(summary_rows, start=1):
        values = row.get("values") or {}
        amount = values.get("budget_current")
        amount_text = money_label(amount, row.get("currency") or "CHF")
        amount_suffix = f" - **{amount_text}**" if amount_text else ""
        source = source_markdown(row.get("source_url") or "", "PDF")
        lines.append(f"{index}. **{row['service_name']}** ({row['metric']}){amount_suffix}{source}")
    return "\n".join(lines)


REGULATION_STOPWORDS = {
    "article",
    "articles",
    "reglement",
    "règlement",
    "conseil",
    "communal",
    "commune",
    "dit",
    "les",
    "selon",
    "quoi",
    "que",
    "quel",
    "quelle",
    "quels",
    "quelles",
    "comment",
    "peut",
    "peuvent",
    "doit",
    "doivent",
    "sur",
}


def wants_regulation_question(question: str) -> bool:
    normalized = normalize(question)
    return any(term in normalized for term in ["reglement", "rcc", "article", "loi", "legal", "legale"]) and any(
        term in normalized for term in ["conseil", "communal", "article", "reglement", "rcc"]
    )


def regulation_terms(question: str) -> list[str]:
    normalized = normalize(question)
    tokens = re.findall(r"[a-z0-9]{3,}", normalized)
    terms = []
    for token in tokens:
        if token in REGULATION_STOPWORDS or re.fullmatch(r"\d{1,3}", token):
            continue
        terms.append(token)
        if len(token) > 4 and token.endswith("s"):
            terms.append(token[:-1])
    return list(dict.fromkeys(terms))[:10]


def answer_regulation_db(question: str) -> str | None:
    if not wants_regulation_question(question):
        return None
    normalized = normalize(question)
    article_match = re.search(r"\b(?:article|art\.?|art)\s*(\d{1,3}(?:bis|ter)?)\b", normalized)
    if article_match:
        article_number = article_match.group(1)
        rows = postgres_rows(
            """
            SELECT title, source_url, metadata
            FROM documents
            WHERE doc_type = 'reglement-conseil-communal'
              AND metadata->>'content_kind' = 'regulation_article'
              AND metadata->>'article_number' = %s
            LIMIT 1
            """,
            (article_number,),
            operation="answer_regulation_article",
        )
    else:
        terms = regulation_terms(question)
        if not terms:
            return None
        patterns = [f"%{term}%" for term in terms]
        rows = postgres_rows(
            """
            SELECT title, source_url, metadata
            FROM documents
            WHERE doc_type = 'reglement-conseil-communal'
              AND metadata->>'content_kind' = 'regulation_article'
              AND (
                metadata->>'article_title' ILIKE ANY(%s)
                OR metadata->>'article_text' ILIKE ANY(%s)
                OR title ILIKE ANY(%s)
              )
            ORDER BY metadata->>'article_number'
            LIMIT 6
            """,
            (patterns, patterns, patterns),
            operation="answer_regulation_topic",
        )
    if rows is None:
        return None
    if not rows:
        return "Je ne trouve pas d'article correspondant dans le règlement indexé."

    lines = ["Dans le **Règlement du Conseil communal**, j'ai trouvé:", ""]
    for index, row in enumerate(rows, start=1):
        metadata = row.get("metadata") or {}
        number = metadata.get("article_number") or "?"
        title = fix_mojibake(str(metadata.get("article_title") or row.get("title") or ""))
        text = compact_spaces(fix_mojibake(str(metadata.get("article_text") or "")))
        if len(text) > 650:
            text = text[:650].rsplit(" ", 1)[0] + "..."
        source = source_markdown(row.get("source_url") or metadata.get("pdf_url") or "", "PDF")
        lines.append(f"{index}. **Article {number} - {title}**{source}  \n   {text}")
    return "\n".join(lines)


def status_filter_from_question(question: str) -> tuple[str, Any] | None:
    normalized = normalize(question)
    if any(term in normalized for term in ["ouvert", "ouvertes", "ouverts", "encore en cours", "pas final", "non final"]):
        return "po.status_is_final = FALSE", None
    if any(term in normalized for term in ["attente de reponse", "sans reponse", "awaiting response"]):
        return "po.status_decision = 'awaiting_response'", None
    if any(term in normalized for term in ["attente municipalite", "municipalite", "renvoye", "renvoyee"]):
        return "po.status_stage = 'referred'", None
    if any(term in normalized for term in ["decide", "decidee", "decides", "decision"]):
        return "po.status_stage = 'decided'", None
    if any(term in normalized for term in ["retire", "retiree"]):
        return "po.status_decision = 'withdrawn'", None
    if any(term in normalized for term in ["non soutenu", "pas soutenu"]):
        return "po.status_decision = 'not_supported'", None
    if any(term in normalized for term in ["reponse disponible", "avec reponse", "repondu", "repondue"]):
        return "po.status_stage = 'answered'", None
    return None


def wants_objects_by_status(question: str) -> bool:
    return status_filter_from_question(question) is not None and any(
        term in normalize(question)
        for term in ["quel", "quelle", "quels", "quelles", "liste", "combien", "motion", "postulat", "interpellation", "objet"]
    )


def answer_objects_by_status_db(question: str) -> str | None:
    if not wants_objects_by_status(question):
        return None
    status_filter = status_filter_from_question(question)
    if not status_filter:
        return None
    object_types = requested_object_types(question)
    year = extract_year(question)
    params: list[Any] = [sorted(object_types)]
    year_filter = ""
    if year:
        year_filter = "AND (po.year = %s OR EXTRACT(YEAR FROM po.deposit_date)::text = %s OR po.object_id LIKE %s)"
        params.extend([year, year, f"%-{year}-%"])
    rows = postgres_rows(
        f"""
        SELECT po.object_id, po.object_type, po.year, po.object_title,
               po.status_stage, po.status_decision, po.deposit_date, po.decision_date, po.response_date,
               po.authors
        FROM political_objects po
        WHERE po.object_type = ANY(%s)
          AND {status_filter[0]}
          {year_filter}
        ORDER BY po.last_event_date DESC NULLS LAST, po.year DESC, po.object_title
        LIMIT 30
        """,
        tuple(params),
        operation="answer_objects_by_status",
    )
    if rows is None:
        return None
    if not rows:
        return "Je ne trouve aucun objet correspondant dans les tables structurées."
    sources = sources_for_object_ids([row["object_id"] for row in rows], limit_per_object=1)
    object_label = "objet" if len(rows) == 1 else "objets"
    lines = [f"J'ai trouvé **{len(rows)} {object_label}** correspondant:", ""]
    for index, row in enumerate(rows, start=1):
        lines.append(
            f"{index}. **{row['object_title']}**  \n"
            f"   {object_summary(row)}{object_source_suffix(row['object_id'], sources)}"
        )
    return "\n".join(lines)


def wants_objects_by_year_db(question: str) -> bool:
    normalized = normalize(question)
    if wants_themed_political_objects(question):
        return False
    return bool(extract_year(question)) and any(term in normalized for term in ["liste", "quels", "quelles", "combien", "objets", "deposes", "depose"])


THEME_QUERY_TERMS = [
    "parle",
    "parlent",
    "mentionne",
    "mentionnent",
    "concerne",
    "concernent",
    "sujet",
    "theme",
    "themes",
    "lie a",
    "lies a",
    "relative",
    "relatifs",
    "sur ",
]

THEME_STOPWORDS = {
    "annee",
    "actuelle",
    "avec",
    "bus",
    "cette",
    "dans",
    "legislature",
    "communal",
    "communale",
    "commune",
    "conseil",
    "document",
    "documents",
    "interpellation",
    "interpellations",
    "mentionne",
    "mentionnent",
    "mobilite",
    "objet",
    "objets",
    "parle",
    "parlent",
    "politique",
    "politiques",
    "postulat",
    "postulats",
    "motion",
    "motions",
    "quelle",
    "quelles",
    "quels",
    "sujet",
    "theme",
    "themes",
}


def wants_themed_political_objects(question: str) -> bool:
    normalized = normalize(question)
    has_object_scope = any(term in normalized for term in ["objet", "objets", "motion", "postulat", "interpellation"])
    has_theme_marker = any(term in normalized for term in THEME_QUERY_TERMS)
    return has_object_scope and has_theme_marker


def theme_tokens(question: str) -> list[str]:
    normalized = normalize(question)
    tokens = re.findall(r"[a-z0-9]{3,}", normalized)
    cleaned = []
    for token in tokens:
        if token in THEME_STOPWORDS or re.fullmatch(r"20\d{2}", token):
            continue
        cleaned.append(token)
        if len(token) > 5 and token.endswith("e"):
            cleaned.append(token[:-1])
    if "mobilite" in tokens:
        cleaned.extend(["mobilite", "mobilit", "transport", "transports", "vmcv"])
    if "bus" in tokens:
        cleaned.extend(["bus", "arret", "arrets", "vmcv"])
    return list(dict.fromkeys(cleaned))[:10]


def object_id_from_search_hit(hit: dict[str, Any]) -> str | None:
    metadata = hit.get("metadata") or {}
    political_object = metadata.get("political_object") or {}
    related_canonical = metadata.get("related_canonical_interpellation") or {}
    candidates = [
        hit.get("political_object_id"),
        metadata.get("political_object_id"),
        metadata.get("related_political_object_id"),
        political_object.get("object_id") if isinstance(political_object, dict) else None,
        related_canonical.get("political_object_id") if isinstance(related_canonical, dict) else None,
    ]
    for candidate in candidates:
        if candidate:
            return str(candidate)
    return None


def rows_for_object_ids(object_ids: list[str], object_types: set[str], year: str | None) -> list[dict[str, Any]] | None:
    if not object_ids:
        return []
    params: list[Any] = [object_ids, sorted(object_types)]
    year_filter = ""
    if year:
        year_filter = "AND (po.year = %s OR EXTRACT(YEAR FROM po.deposit_date)::text = %s OR po.object_id LIKE %s)"
        params.extend([year, year, f"%-{year}-%"])
    params.append(object_ids)
    return postgres_rows(
        f"""
        SELECT po.object_id, po.object_type, po.year, po.object_title,
               po.status_stage, po.status_decision, po.deposit_date, po.decision_date, po.response_date,
               po.authors
        FROM political_objects po
        WHERE po.object_id = ANY(%s)
          AND po.object_type = ANY(%s)
          {year_filter}
        ORDER BY array_position(%s::text[], po.object_id), po.deposit_date ASC NULLS LAST
        LIMIT 20
        """,
        tuple(params),
        operation="rows_for_object_ids",
    )


def opensearch_themed_object_rows(
    question: str,
    tokens: list[str],
    object_types: set[str],
    year: str | None,
) -> list[dict[str, Any]]:
    try:
        from app.hybrid_search import search_hybrid
    except Exception:
        return []

    doc_types = [DEPOSIT_CATEGORY_BY_TYPE[object_type] for object_type in sorted(object_types)]
    filters: dict[str, Any] = {"doc_type": doc_types}
    if year:
        filters["year"] = year
    enhanced_query = f"{question} {' '.join(tokens)}"
    hits = search_hybrid(enhanced_query, limit=30, filters=filters)
    object_ids = []
    for hit in hits:
        object_id = object_id_from_search_hit(hit)
        if object_id:
            object_ids.append(object_id)
    ordered_ids = list(dict.fromkeys(object_ids))
    rows = rows_for_object_ids(ordered_ids, object_types, year)
    return rows or []


def postgres_title_themed_object_rows(tokens: list[str], object_types: set[str], year: str | None) -> list[dict[str, Any]] | None:
    patterns = [f"%{token}%" for token in tokens]
    params: list[Any] = [sorted(object_types)]
    year_filter = ""
    if year:
        year_filter = "AND (po.year = %s OR EXTRACT(YEAR FROM po.deposit_date)::text = %s OR po.object_id LIKE %s)"
        params.extend([year, year, f"%-{year}-%"])
    params.extend([patterns, patterns, patterns])
    rows = postgres_rows(
        f"""
        SELECT DISTINCT po.object_id, po.object_type, po.year, po.object_title,
               po.status_stage, po.status_decision, po.deposit_date, po.decision_date, po.response_date,
               po.authors
        FROM political_objects po
        LEFT JOIN political_object_documents pod ON pod.object_id = po.object_id
        LEFT JOIN documents d ON d.id = pod.document_id
        WHERE po.object_type = ANY(%s)
          {year_filter}
          AND (
              po.object_title ILIKE ANY(%s)
              OR d.title ILIKE ANY(%s)
              OR d.source_path ILIKE ANY(%s)
          )
        ORDER BY po.deposit_date ASC NULLS LAST, po.object_type, po.object_title
        LIMIT 20
        """,
        tuple(params),
        operation="answer_themed_objects",
    )
    return rows


def merge_object_rows(*row_groups: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for rows in row_groups:
        for row in rows or []:
            object_id = str(row.get("object_id") or "")
            if object_id and object_id not in merged:
                merged[object_id] = row
    return list(merged.values())


def answer_themed_objects_db(question: str) -> str | None:
    if not wants_themed_political_objects(question):
        return None
    tokens = theme_tokens(question)
    if not tokens:
        return None
    year = extract_year(question)
    object_types = requested_object_types(question)
    opensearch_rows = opensearch_themed_object_rows(question, tokens, object_types, year)
    title_rows = postgres_title_themed_object_rows(tokens, object_types, year)
    rows = merge_object_rows(opensearch_rows, title_rows)
    if rows is None:
        return None
    if not rows:
        year_text = f" en **{year}**" if year else ""
        return f"Je ne trouve aucun objet politique correspondant à ce thème{year_text} dans les tables structurées."

    counts = Counter(str(row["object_type"]) for row in rows)
    sources = sources_for_object_ids([row["object_id"] for row in rows], limit_per_object=1)
    count_text = ", ".join(object_type_count_label(object_type, count) for object_type, count in sorted(counts.items()))
    year_text = f" en **{year}**" if year else ""
    lines = [f"Pour ce thème{year_text}, j'ai trouvé **{count_text}**.", ""]
    for index, row in enumerate(rows, start=1):
        lines.append(
            f"{index}. **{row['object_title']}**  \n"
            f"   {object_summary(row)}{object_source_suffix(row['object_id'], sources)}"
        )
    return "\n".join(lines)


def answer_objects_by_year_db(question: str) -> str | None:
    if not wants_objects_by_year_db(question):
        return None
    year = extract_year(question)
    if not year:
        return None
    object_types = requested_object_types(question)
    rows = postgres_rows(
        """
        SELECT po.object_id, po.object_type, po.year, po.object_title,
               po.status_stage, po.status_decision, po.deposit_date, po.decision_date, po.response_date,
               po.authors
        FROM political_objects po
        WHERE po.object_type = ANY(%s)
          AND (po.year = %s OR EXTRACT(YEAR FROM po.deposit_date)::text = %s OR po.object_id LIKE %s)
        ORDER BY po.deposit_date ASC NULLS LAST, po.object_type, po.object_title
        LIMIT 60
        """,
        (sorted(object_types), year, year, f"%-{year}-%"),
        operation="answer_objects_by_year",
    )
    if rows is None:
        return None
    if not rows:
        return None
    counts = Counter(str(row["object_type"]) for row in rows)
    sources = sources_for_object_ids([row["object_id"] for row in rows], limit_per_object=1)
    count_text = ", ".join(object_type_count_label(object_type, count) for object_type, count in sorted(counts.items()))
    lines = [
        f"En **{year}**, j'ai trouvé **{len(rows)} objet(s)**: "
        + ", ".join(object_type_count_label(object_type, count) for object_type, count in sorted(counts.items())),
        "",
    ]
    lines = [f"En **{year}**, j'ai trouvé **{count_text}**.", ""]
    for index, row in enumerate(rows, start=1):
        lines.append(
            f"{index}. **{row['object_title']}**  \n"
            f"   {object_summary(row)}{object_source_suffix(row['object_id'], sources)}"
        )
    return "\n".join(lines)


def answer_database_first(question: str) -> str | None:
    if not structured_tables_ready():
        return None
    return (
        answer_person_deposits_db(question)
        or answer_coauthors_db(question)
        or answer_count_by_party_db(question)
        or answer_financial_db(question)
        or answer_regulation_db(question)
        or answer_themed_objects_db(question)
        or answer_objects_by_status_db(question)
        or answer_objects_by_year_db(question)
    )


def wants_most_deposits(question: str) -> bool:
    normalized = normalize(question)
    has_deposit_verb = any(term in normalized for term in ["depose", "deposes", "depos", "depot", "dépose", "déposés"])
    has_year = bool(extract_year(question))
    has_more = bool(MOST_PATTERN.search(normalized)) or "le plus" in normalized or "plus de" in normalized
    return has_deposit_verb and has_year and has_more


def object_label(object_type: str) -> str:
    labels = {
        "motion": "motion",
        "postulat": "postulat",
        "interpellation": "interpellation",
    }
    return labels.get(object_type, object_type)


def infer_status_from_document(title: str, filename: str) -> str:
    haystack = normalize(f"{title} {filename}")
    if "renvoye directement" in haystack or "renvoye a la municipalite" in haystack:
        return "renvoye_municipalite"
    if "retire par le postulant" in haystack or "retiree par le postulant" in haystack:
        return "retire"
    if "+ reponse" in haystack or (
        filename.lower().startswith(("interpellation-", "motion-", "postulat-")) and "-rep" in filename.lower()
    ):
        return "depot_avec_reponse"
    return "depot"


def extract_author_party_pairs(title: str) -> list[dict[str, str]]:
    title = str(title or "")
    if not title:
        return []

    intro = re.split(r"\s+[–—-]\s+|\s+«", title, maxsplit=1)[0]
    intro = re.sub(r"^(Motion|Postulat|Interpellation)\s+(de|du|des)\s+", "", intro, flags=re.IGNORECASE).strip()
    intro = re.sub(r"\s+\+\s+R[ée]ponse.*$", "", intro, flags=re.IGNORECASE).strip()
    matches = re.findall(
        r"\b(?:Mme|M\.|MM\.|Mmes)\s+([^()]+?)\s*\(([A-ZÀ-Ÿ0-9/-]{2,})\)",
        intro,
        flags=re.IGNORECASE,
    )
    pairs = []
    for raw_name, party in matches:
        name = display_name(raw_name)
        if name:
            pairs.append({"name": name, "party": party})
    if pairs:
        return pairs

    group_match = re.search(r"\bgroupe\s+([A-ZÀ-Ÿ0-9/-]{2,})\b", intro, flags=re.IGNORECASE)
    if group_match:
        party = group_match.group(1)
        return [{"name": f"groupe {party}", "party": party}]
    return []


def authors_from_title(title: str) -> list[str]:
    return [pair["name"] for pair in extract_author_party_pairs(title)]


def party_from_title(title: str) -> str | None:
    pairs = extract_author_party_pairs(title)
    parties = [pair["party"] for pair in pairs if pair.get("party")]
    return parties[0] if parties else None


def search_deposit_documents_from_postgres(year: str, object_types: set[str]) -> list[dict[str, Any]]:
    try:
        from app.postgres_store import _connect
    except Exception as exc:
        record_diagnostic(
            "structured",
            "Structured legacy Postgres import failed",
            exc,
            operation="search_deposit_documents_import",
        )
        return []

    rows = []
    try:
        with _connect() as connection:
            with connection.cursor() as cursor:
                for object_type in sorted(object_types):
                    category = DEPOSIT_CATEGORY_BY_TYPE.get(object_type)
                    prefix = DEPOSIT_PREFIX_BY_TYPE.get(object_type)
                    if not category or not prefix:
                        continue
                    cursor.execute(
                        """
                        SELECT title, source_url, source_path, doc_type, metadata
                        FROM documents
                        WHERE (metadata->>'object_year' = %s OR metadata->>'year' = %s OR metadata->>'listing_year' = %s OR source_path LIKE %s)
                          AND doc_type = %s
                          AND (title ILIKE %s OR metadata->>'filename' ILIKE %s)
                          AND COALESCE(metadata->>'canonical_object', 'true') <> 'false'
                          AND COALESCE(metadata->>'filename', '') NOT ILIKE 'Reponse-%%'
                          AND COALESCE(metadata->>'filename', '') NOT ILIKE '%%-Rapp%%'
                        ORDER BY title, source_url
                        """,
                        (year, year, year, f"%/{year}/%", category, f"{prefix}%", f"{prefix}%"),
                    )
                    rows.extend(cursor.fetchall())
    except Exception as exc:
        record_diagnostic(
            "structured",
            "Structured legacy deposit document search failed",
            exc,
            operation="search_deposit_documents",
            year=year,
            object_types=sorted(object_types),
        )
        return []

    deposits = []
    seen = set()
    for row in rows:
        metadata = row.get("metadata") or {}
        if not isinstance(metadata, dict):
            metadata = {}
        filename = str(metadata.get("filename") or row.get("source_path") or "")
        title = str(row.get("title") or metadata.get("title") or filename)
        source_url = str(row.get("source_url") or metadata.get("pdf_url") or "")
        key = source_url or filename or title
        if not key or key in seen:
            continue
        seen.add(key)

        object_type = next(
            (
                candidate
                for candidate, prefix in DEPOSIT_PREFIX_BY_TYPE.items()
                if title.lower().startswith(prefix.lower()) or filename.lower().startswith(prefix.lower())
            ),
            "",
        )
        if object_type not in object_types:
            continue

        pairs = extract_author_party_pairs(title)
        deposits.append(
            {
                "commune": metadata.get("commune", "La Tour-de-Peilz"),
                "session_date": metadata.get("session_date", ""),
                "agenda_item_number": "",
                "object_type": object_type,
                "status": infer_status_from_document(title, filename),
                "title": title,
                "document_title": title,
                "filename": filename,
                "pdf_url": source_url,
                "category": row.get("doc_type") or metadata.get("category", ""),
                "year": year,
                "authors": [pair["name"] for pair in pairs],
                "party": pairs[0]["party"] if pairs else None,
            }
        )
    return sorted(deposits, key=lambda item: (item.get("object_type", ""), item.get("title", ""), item.get("filename", "")))


def count_by_type(objects: list[dict]) -> dict[str, int]:
    counts = {"motion": 0, "postulat": 0, "interpellation": 0}
    for item in objects:
        object_type = item.get("object_type", "")
        if object_type in counts:
            counts[object_type] += 1
    return counts


def display_name(raw_name: str) -> str:
    name = raw_name.strip()
    name = re.sub(r"\s*\([^)]*\)\s*$", "", name)
    name = re.sub(r"^(mme|m|mmes|mrs|mr)\.\s*", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\s+", " ", name)
    return name.strip(" ,;:")


def authors_for_item(item: dict) -> list[str]:
    title_authors = authors_from_title(str(item.get("title") or item.get("document_title") or ""))
    if title_authors:
        return title_authors

    title = str(item.get("title") or item.get("document_title") or "")
    raw_authors = title
    if raw_authors:
        raw_authors = re.split(r"\s*[«»–\-—]\s*", raw_authors, maxsplit=1)[0]
    title_match = re.search(r"^(?:Postulat|Motion|Interpellation)\s+de\s+(.+)$", raw_authors, flags=re.IGNORECASE)
    if title_match:
        raw_authors = title_match.group(1)
        raw_authors = re.sub(r"\s+et\s+consorts?\b", "", raw_authors, flags=re.IGNORECASE)
        pieces = re.split(r"\s+et\s+|\s*,\s*", raw_authors)
        cleaned = [display_name(piece) for piece in pieces if piece.strip()]
        if cleaned:
            return cleaned

    authors = item.get("authors") or []
    if isinstance(authors, str):
        authors = [authors]
    cleaned = [display_name(str(author)) for author in authors if str(author).strip()]
    return cleaned


def answer_most_deposits(question: str) -> str | None:
    if not wants_most_deposits(question):
        return None

    data = load_structured_data()
    year = extract_year(question)
    if not year:
        return None

    object_types = requested_object_types(question)
    deposits = [
        item
        for item in data["political_objects"]
        if str(item.get("year", "")) == year
        and item.get("object_type") in object_types
        and item.get("status") == "depot"
    ]
    if not deposits:
        return f"Je ne trouve aucun dépôt correspondant en {year}."

    counts: Counter[str] = Counter()
    examples: dict[str, list[str]] = {}
    for item in deposits:
        item_authors = authors_for_item(item)
        if not item_authors:
            continue
        unique_authors = list(dict.fromkeys(item_authors))
        for author in unique_authors:
            counts[author] += 1
            examples.setdefault(author, []).append(str(item.get("title", "")))

    if not counts:
        return (
            f"Je trouve bien des dépôts en {year}, mais les sources structurées ne permettent pas d'identifier clairement les auteurs."
        )

    top_count = max(counts.values())
    leaders = sorted(author for author, count in counts.items() if count == top_count)
    object_label_text = ", ".join(sorted(object_types))
    leader_text = ", ".join(leaders)

    lines = [
        f"En **{year}**, pour les **{object_label_text}** déposés, la personne ou le groupe le plus actif est **{leader_text}** avec **{top_count} dépôt(s)**.",
        "",
        "Détail des principaux dépôts:",
    ]

    for author in leaders[:3]:
        titles = examples.get(author, [])[:3]
        if titles:
            lines.append(f"- {author}: {', '.join(titles)}")

    return "\n".join(lines)


def answer_deposits_by_year(question: str) -> str | None:
    if not wants_deposit_count(question):
        return None

    year = extract_year(question)
    if not year:
        return None

    object_types = requested_object_types(question)
    deposits = search_deposit_documents_from_postgres(year, object_types)
    deposits = sorted(
        deposits,
        key=lambda item: (item.get("session_date", ""), item.get("agenda_item_number", ""), item.get("title", "")),
    )
    counts = count_by_type(deposits)

    if not deposits:
        return (
            f"Pour {year}, je ne trouve aucun objet politique déposé correspondant dans les données structurées."
        )

    total = len(deposits)
    lines = [
        f"En **{year}**, je trouve **{total} dépôt(s)** dans les données structurées:",
        f"- {counts['interpellation']} interpellation(s)",
        f"- {counts['motion']} motion(s)",
        f"- {counts['postulat']} postulat(s)",
        "",
    ]

    for index, item in enumerate(deposits, start=1):
        title = item.get("title", "")
        number = item.get("agenda_item_number", "")
        session_date = item.get("session_date", "")
        pdf_url = item.get("pdf_url", "")
        authors = item.get("authors") or authors_from_title(title)
        party = item.get("party") or party_from_title(title)
        status = item.get("status", "")
        source = f" [PDF]({pdf_url})" if pdf_url else ""
        if session_date and number:
            prefix = f"{index}. {session_date} - {number} - "
        elif session_date:
            prefix = f"{index}. {session_date} - "
        else:
            prefix = f"{index}. "
        details = []
        if authors:
            details.append(f"depose par {', '.join(authors)}")
        if party:
            details.append(party)
        if status and status != "depot":
            details.append(status.replace("_", " "))
        detail_text = f" ({'; '.join(details)})" if details else ""
        lines.append(f"{prefix}{title}{detail_text}{source}")

    source_urls = sorted({item.get("session_source_url", "") for item in deposits if item.get("session_source_url")})
    if source_urls:
        lines.append("")
        lines.append("Sources séances:")
        for source_url in source_urls[:8]:
            lines.append(f"- {source_url}")
    return "\n".join(lines)


def answer_latest_deposits(question: str) -> str | None:
    if not (wants_latest_session(question) and wants_deposit_count(question)):
        return None

    data = load_structured_data()
    session = latest_session(data["sessions"])
    if not session:
        return None

    session_date = session["session_date"]
    deposits = [
        item
        for item in data["political_objects"]
        if item.get("session_date") == session_date
        and item.get("object_type") in DEPOSIT_TYPES
        and item.get("status") == "depot"
    ]
    deposits = sorted(deposits, key=lambda item: item.get("agenda_item_number", ""))
    counts = count_by_type(deposits)

    if not deposits:
        return (
            f"Pour la dernière séance indexée ({session_date}), je ne trouve pas de motion, "
            "postulat ou interpellation déposés dans les données structurées."
        )

    total = len(deposits)
    lines = [
        f"Pour la dernière séance indexée, le **{session_date}**, il y a **{total} dépôt(s)** dans les données structurées:",
        f"- {counts['interpellation']} interpellation(s)",
        f"- {counts['motion']} motion(s)",
        f"- {counts['postulat']} postulat(s)",
        "",
    ]

    for index, item in enumerate(deposits, start=1):
        title = item.get("title", "")
        object_type = object_label(item.get("object_type", ""))
        number = item.get("agenda_item_number", "")
        pdf_url = item.get("pdf_url", "")
        source = f" [PDF]({pdf_url})" if pdf_url else ""
        prefix = f"{index}. {number} - {object_type}: " if number else f"{index}. {object_type}: "
        lines.append(f"{prefix}{title}{source}")

    if session.get("source_url"):
        lines.extend(["", f"Source séance: [ordre du jour du {session_date}]({session['source_url']})"])
    return "\n".join(lines)


def answer_structured_question(question: str) -> str | None:
    return (
        answer_database_first(question)
        or answer_most_deposits(question)
        or answer_deposits_by_year(question)
        or answer_latest_deposits(question)
    )
