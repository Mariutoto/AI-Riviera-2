from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from app.config import DOCUMENTS_ROOT
from app.postgres_store import _connect, canonical_source_url, ensure_schema
from app.text_cleaning import strip_accents


MONEY_PATTERN = re.compile(r"^-?\d[\d']*(?:\.\d+)?$")
ACCOUNT_PATTERN = re.compile(r"^\d{3,4}\.\d{4}\.\d{2}$")
GROUP_PATTERN = re.compile(r"^\d{2,4}$")
SUMMARY_METRICS = {
    "CHARGES": "charges",
    "REVENUS": "revenues",
    "CHARGES NETTES": "net_charges",
    "REVENUS NETS": "net_revenues",
}
SERVICE_NAMES = {
    "1": "Administration generale",
    "2": "Finances",
    "3": "Domaines et batiments",
    "4": "Urbanisme et travaux publics",
    "5": "Instruction publique et cultes",
    "6": "Securite - population - feu",
    "7": "Famille, jeunesse et sport",
}


def normalize_text(value: str) -> str:
    replacements = {
        "\u00a0": " ",
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
        "é": "e",
        "è": "e",
        "ê": "e",
        "ë": "e",
        "à": "a",
        "â": "a",
        "ç": "c",
        "ô": "o",
        "û": "u",
        "ü": "u",
        "ï": "i",
        "‐": "-",
    }
    for old, new in replacements.items():
        value = value.replace(old, new)
    return re.sub(r"\s+", " ", value).strip()


def comparable(value: str) -> str:
    return strip_accents(normalize_text(value)).lower()


def normalize_lines(text: str) -> list[str]:
    return [line for line in (normalize_text(line) for line in text.splitlines()) if line]


def parse_number(value: str) -> float | int | None:
    value = normalize_text(value).replace(" ", "")
    if value in {"", "-", "--"}:
        return None
    if value.endswith("%"):
        value = value[:-1]
    if not MONEY_PATTERN.match(value):
        return None
    parsed = float(value.replace("'", ""))
    return int(parsed) if parsed.is_integer() else parsed


def looks_like_number(value: str) -> bool:
    return parse_number(value) is not None


def load_budget_metadata(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def find_budget_files(root: Path = DOCUMENTS_ROOT) -> list[Path]:
    return sorted(root.rglob("budget/*.json"))


def get_document(connection, metadata: dict[str, Any], metadata_path: Path) -> dict[str, Any] | None:
    source_url = canonical_source_url(metadata, metadata_path.with_suffix(".txt").as_posix())
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT d.id, d.city_id, d.source_url, d.source_path, c.name AS city
            FROM documents d
            JOIN cities c ON c.id = d.city_id
            WHERE d.source_url = %s
            """,
            (source_url,),
        )
        return cursor.fetchone()


def extract_summary_tables(lines: list[str], fiscal_year: int, source_path: str) -> list[dict[str, Any]]:
    tables: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    previous_year = fiscal_year - 1

    for index, line in enumerate(lines):
        metric = SUMMARY_METRICS.get(line.upper())
        if not metric:
            continue
        window = lines[index : index + 90]
        if not any(re.search(rf"B(?:udget )?{str(fiscal_year)[-2:]}", item, re.IGNORECASE) for item in window[:12]):
            continue

        rows = []
        position = index + 1
        while position < min(len(lines), index + 90):
            current = lines[position]
            if current in SUMMARY_METRICS and position != index:
                break
            if current in SERVICE_NAMES:
                service_code = current
                service_name = lines[position + 1] if position + 1 < len(lines) else SERVICE_NAMES[service_code]
                values = []
                cursor = position + 2
                while cursor < min(len(lines), index + 90) and len(values) < 6:
                    if lines[cursor] in SERVICE_NAMES or lines[cursor].upper() == "TOTAL":
                        break
                    number = parse_number(lines[cursor])
                    if number is not None:
                        values.append(number)
                    cursor += 1
                if len(values) >= 2:
                    row_values = {
                        "budget_current": values[0],
                        "budget_previous": values[1],
                    }
                    if len(values) > 2:
                        row_values["delta_amount"] = values[2]
                    if len(values) > 3:
                        row_values["delta_percent"] = values[3]
                    if len(values) > 4:
                        row_values["current_total_percent"] = values[4]
                    if len(values) > 5:
                        row_values["previous_total_percent"] = values[5]
                    rows.append(
                        {
                            "row_order": len(rows) + 1,
                            "service_code": service_code,
                            "service_name": normalize_text(service_name),
                            "values": row_values,
                        }
                    )
                position = max(cursor, position + 1)
                continue
            if current.upper() == "TOTAL":
                values = []
                cursor = position + 1
                while cursor < min(len(lines), index + 90) and len(values) < 4:
                    number = parse_number(lines[cursor])
                    if number is not None:
                        values.append(number)
                    cursor += 1
                if len(values) >= 2:
                    rows.append(
                        {
                            "row_order": 99,
                            "service_code": "total",
                            "service_name": "Total",
                            "values": {
                                "budget_current": values[0],
                                "budget_previous": values[1],
                                **({"delta_amount": values[2]} if len(values) > 2 else {}),
                                **({"delta_percent": values[3]} if len(values) > 3 else {}),
                            },
                        }
                    )
                position = cursor
                continue
            position += 1

        if len(rows) >= 3:
            key = (metric, index // 100)
            if key in seen:
                continue
            seen.add(key)
            tables.append(
                {
                    "table_key": f"{Path(source_path).stem}-{fiscal_year}-{metric}-{len(tables) + 1}",
                    "fiscal_year": fiscal_year,
                    "title": f"{line.title()} par services",
                    "metric": metric,
                    "source_path": source_path,
                    "metadata": {
                        "line_number": index + 1,
                        "current_budget_year": fiscal_year,
                        "previous_budget_year": previous_year,
                        "extraction_method": "text_heuristic_v1",
                    },
                    "rows": rows,
                }
            )
    return tables


def collect_amounts(lines: list[str], start: int, limit: int = 6) -> tuple[dict[str, Any], int]:
    values: list[float | int] = []
    cursor = start
    while cursor < len(lines) and len(values) < limit:
        line = lines[cursor]
        if ACCOUNT_PATTERN.match(line) or line in SERVICE_NAMES or line.upper().startswith("COMPTE DE FONCTIONNEMENT"):
            break
        number = parse_number(line)
        if number is not None:
            values.append(number)
        cursor += 1

    return {
        "amounts_sequence": values,
        "column_context": [
            "budget_current_charges",
            "budget_current_revenues",
            "budget_previous_charges",
            "budget_previous_revenues",
            "accounts_reference_charges",
            "accounts_reference_revenues",
        ],
        "needs_column_alignment": True,
    }, cursor


def extract_account_lines(lines: list[str], fiscal_year: int, source_path: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    active = False
    current_service_code = ""
    current_service_name = ""
    current_group_code = ""
    current_group_name = ""
    current_department = ""

    for index, line in enumerate(lines):
        if comparable(line).startswith("compte de fonctionnement par services"):
            active = True
            continue
        if not active:
            continue

        if line in SERVICE_NAMES and index + 1 < len(lines) and not looks_like_number(lines[index + 1]):
            expected = comparable(SERVICE_NAMES[line])
            candidate = comparable(lines[index + 1])
            if expected not in candidate and candidate not in expected:
                continue
            current_service_code = line
            current_service_name = normalize_text(lines[index + 1])
            continue

        if line.isupper() and len(line) <= 12 and "/" in line:
            current_department = line
            continue
        if line in {"ECO", "SDOM", "FIN", "UPI", "SPF", "FJS", "CULT", "ADM"}:
            current_department = line
            continue

        if GROUP_PATTERN.match(line) and not ACCOUNT_PATTERN.match(line):
            if index + 1 < len(lines) and not looks_like_number(lines[index + 1]):
                current_group_code = line
                current_group_name = normalize_text(lines[index + 1])
            continue

        if not ACCOUNT_PATTERN.match(line):
            continue
        if not current_service_code:
            continue
        if index + 1 >= len(lines):
            continue

        account_number = line
        account_label = normalize_text(lines[index + 1])
        values, _ = collect_amounts(lines, index + 2)
        if not values["amounts_sequence"]:
            continue

        records.append(
            {
                "line_key": f"{Path(source_path).stem}-{fiscal_year}-{account_number}-{index + 1}",
                "fiscal_year": fiscal_year,
                "service_code": current_service_code,
                "service_name": current_service_name or SERVICE_NAMES.get(current_service_code, ""),
                "group_code": current_group_code,
                "group_name": current_group_name,
                "department": current_department,
                "account_number": account_number,
                "account_label": account_label,
                "values": values,
                "source_path": source_path,
                "line_number": index + 1,
                "metadata": {
                    "current_budget_year": fiscal_year,
                    "previous_budget_year": fiscal_year - 1,
                    "reference_accounts_year": fiscal_year - 2,
                    "extraction_method": "text_heuristic_v1",
                },
            }
        )
    return records


def upsert_financial_data(connection, document: dict[str, Any], tables: list[dict[str, Any]], lines: list[dict[str, Any]]) -> None:
    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM financial_summary_tables WHERE document_id = %s", (document["id"],))
        cursor.execute("DELETE FROM financial_account_lines WHERE document_id = %s", (document["id"],))

        for table in tables:
            cursor.execute(
                """
                INSERT INTO financial_summary_tables (
                    document_id, city_id, table_key, fiscal_year, title, metric,
                    currency, source_path, metadata
                ) VALUES (%s, %s, %s, %s, %s, %s, 'CHF', %s, %s::jsonb)
                RETURNING id
                """,
                (
                    document["id"],
                    document["city_id"],
                    table["table_key"],
                    table["fiscal_year"],
                    table["title"],
                    table["metric"],
                    table["source_path"],
                    json.dumps(table["metadata"], ensure_ascii=False),
                ),
            )
            table_id = cursor.fetchone()["id"]
            for row in table["rows"]:
                cursor.execute(
                    """
                    INSERT INTO financial_summary_rows (
                        table_id, row_order, service_code, service_name, values, metadata
                    ) VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb)
                    """,
                    (
                        table_id,
                        row["row_order"],
                        row["service_code"],
                        row["service_name"],
                        json.dumps(row["values"], ensure_ascii=False),
                        json.dumps(row.get("metadata", {}), ensure_ascii=False),
                    ),
                )

        for line in lines:
            cursor.execute(
                """
                INSERT INTO financial_account_lines (
                    document_id, city_id, line_key, fiscal_year, service_code, service_name,
                    group_code, group_name, department, account_number, account_label,
                    currency, values, source_path, line_number, metadata
                ) VALUES (
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    'CHF', %s::jsonb, %s, %s, %s::jsonb
                )
                """,
                (
                    document["id"],
                    document["city_id"],
                    line["line_key"],
                    line["fiscal_year"],
                    line["service_code"],
                    line["service_name"],
                    line["group_code"],
                    line["group_name"],
                    line["department"],
                    line["account_number"],
                    line["account_label"],
                    json.dumps(line["values"], ensure_ascii=False),
                    line["source_path"],
                    line["line_number"],
                    json.dumps(line["metadata"], ensure_ascii=False),
                ),
            )


def ingest_financial_budget_data(documents_root: Path = DOCUMENTS_ROOT) -> dict[str, Any]:
    ensure_schema()
    stats = {
        "budgets_seen": 0,
        "budgets_loaded": 0,
        "summary_tables": 0,
        "summary_rows": 0,
        "account_lines": 0,
        "missing_documents": [],
    }

    with _connect() as connection:
        for metadata_path in find_budget_files(documents_root):
            stats["budgets_seen"] += 1
            metadata = load_budget_metadata(metadata_path)
            fiscal_year = int(str(metadata.get("year")))
            text_path = metadata_path.with_suffix(".txt")
            if not text_path.exists():
                continue
            document = get_document(connection, metadata, metadata_path)
            if not document:
                stats["missing_documents"].append(str(metadata_path))
                continue

            lines = normalize_lines(text_path.read_text(encoding="utf-8", errors="ignore"))
            source_path = text_path.as_posix()
            summary_tables = extract_summary_tables(lines, fiscal_year, source_path)
            account_lines = extract_account_lines(lines, fiscal_year, source_path)
            upsert_financial_data(connection, document, summary_tables, account_lines)

            stats["budgets_loaded"] += 1
            stats["summary_tables"] += len(summary_tables)
            stats["summary_rows"] += sum(len(table["rows"]) for table in summary_tables)
            stats["account_lines"] += len(account_lines)

        connection.commit()

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract structured financial data from budget text files.")
    parser.add_argument("--documents-root", type=Path, default=DOCUMENTS_ROOT)
    args = parser.parse_args()
    stats = ingest_financial_budget_data(args.documents_root)
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
