import re
from pathlib import Path

import sqlparse

from app.services.ingestion.detector import DocumentFormat
from app.services.ingestion.types import DocumentMetadata, NormalizedDocument, NormalizedTable

_CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`\"\[]?(\w+)[`\"\]]?\s*\((.*)\)\s*(?:ENGINE.*)?$",
    re.IGNORECASE | re.DOTALL,
)
_INSERT_RE = re.compile(
    r"INSERT\s+INTO\s+[`\"\[]?(\w+)[`\"\]]?\s*(?:\(([^)]*)\))?\s*VALUES\s*(.*)$",
    re.IGNORECASE | re.DOTALL,
)
_CONSTRAINT_KEYWORDS = ("PRIMARY", "FOREIGN", "UNIQUE", "KEY", "CONSTRAINT", "CHECK", "INDEX")


def _split_top_level(s: str, sep: str = ",") -> list[str]:
    parts: list[str] = []
    depth = 0
    quote: str | None = None
    current: list[str] = []
    for ch in s:
        if quote:
            current.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
            current.append(ch)
        elif ch == "(":
            depth += 1
            current.append(ch)
        elif ch == ")":
            depth -= 1
            current.append(ch)
        elif ch == sep and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    if current:
        parts.append("".join(current))
    return [p.strip() for p in parts if p.strip()]


def _extract_columns(column_defs: str) -> list[str]:
    columns = []
    for part in _split_top_level(column_defs):
        first_token = part.strip().split(None, 1)[0].strip("`\"[]")
        if first_token.upper() in _CONSTRAINT_KEYWORDS:
            continue
        columns.append(first_token)
    return columns


def _clean_value(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def _extract_value_tuples(values_str: str) -> list[list[str]]:
    tuples = []
    depth = 0
    quote: str | None = None
    current: list[str] = []
    for ch in values_str:
        if quote:
            current.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
            current.append(ch)
        elif ch == "(":
            if depth == 0:
                current = []
            else:
                current.append(ch)
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                tuples.append([_clean_value(v) for v in _split_top_level("".join(current))])
            else:
                current.append(ch)
        else:
            if depth > 0:
                current.append(ch)
    return tuples


def parse(file_path: Path) -> NormalizedDocument:
    raw_sql = file_path.read_text(encoding="utf-8", errors="replace")
    formatted = sqlparse.format(raw_sql, reindent=True, keyword_case="upper")

    tables: dict[str, NormalizedTable] = {}
    table_order: list[str] = []

    for statement in sqlparse.split(raw_sql):
        statement = statement.strip().rstrip(";").strip()
        if not statement:
            continue

        create_match = _CREATE_TABLE_RE.match(statement)
        if create_match:
            table_name, column_defs = create_match.groups()
            headers = _extract_columns(column_defs)
            if table_name not in tables:
                tables[table_name] = NormalizedTable(index=len(table_order), headers=headers, rows=[])
                table_order.append(table_name)
            continue

        insert_match = _INSERT_RE.match(statement)
        if insert_match:
            table_name, _columns, values_str = insert_match.groups()
            if table_name not in tables:
                tables[table_name] = NormalizedTable(index=len(table_order), headers=[], rows=[])
                table_order.append(table_name)
            tables[table_name].rows.extend(_extract_value_tuples(values_str))

    return NormalizedDocument(
        text=formatted,
        tables=[tables[name] for name in table_order],
        images=[],
        metadata=DocumentMetadata(
            document_type=DocumentFormat.SQL.value,
            title=file_path.stem,
            headings=table_order,
        ),
    )
