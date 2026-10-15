"""Turn an Excel/CSV file into rows keyed by flattened, snake_case header names.

Files with several header rows (e.g. "Value Slab" merged over "GSR(Q) Value" and
"Clear Cut") become single names like "value_slab_gsr_q_value", which a database can store.

Workbooks are streamed (openpyxl read-only mode), so a large file is read row by row
instead of being loaded into memory.
"""
import csv
import io
import re
from collections.abc import Iterator
from typing import Any

import openpyxl


def read_table(content: bytes, filename: str, header_rows: int) -> tuple[list[str], Iterator[list[Any]]]:
    """Return the flattened column names and an iterator over the non-blank data rows."""
    grid = _iter_csv(content) if filename.lower().endswith(".csv") else _iter_xlsx(content)
    header = [row for _, row in zip(range(header_rows), grid)]
    if len(header) < header_rows:
        return [], iter(())
    columns = flatten_header(_fill_merged_groups(header))
    width = len(columns)
    rows = (_fit(row, width) for row in grid if not all(_is_blank(v) for v in row))
    return columns, rows


def read_rows(content: bytes, filename: str, header_rows: int) -> list[dict[str, Any]]:
    columns, rows = read_table(content, filename, header_rows)
    return [dict(zip(columns, row)) for row in rows]


def flatten_header(header_rows: list[list[Any]]) -> list[str]:
    width = max(len(row) for row in header_rows)
    names = []
    for col in range(width):
        parts: list[str] = []
        for row in header_rows:
            value = row[col] if col < len(row) else None
            if _is_blank(value):
                continue
            text = str(value).strip()
            if not parts or parts[-1] != text:  # a vertically merged cell repeats its text
                parts.append(text)
        names.append(to_snake(" ".join(parts)) or f"column_{col + 1}")
    return _dedupe(names)


def to_snake(text: str) -> str:
    text = text.replace("%", " pct ")
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _fill_merged_groups(header_rows: list[list[Any]]) -> list[list[Any]]:
    """Copy each group name to the right over the blank cells after it, in every header row but the last.

    Excel stores a merged cell's value only in its first cell, and streaming mode cannot see the
    merges, so "Value Slab" merged over 22 columns arrives as "Value Slab" followed by 21 blanks.
    Only cells that have a column name below them are filled: a blank group cell is taken to
    belong to the group on its left.
    """
    if len(header_rows) < 2:
        return header_rows
    filled = [list(row) for row in header_rows]
    width = max(len(row) for row in filled)
    for row in filled:
        row.extend([None] * (width - len(row)))
    last = filled[-1]
    for row in filled[:-1]:
        group = None
        for col in range(width):
            if not _is_blank(row[col]):
                group = row[col]
            elif group is not None and not _is_blank(last[col]):
                row[col] = group
    return filled


def _iter_xlsx(content: bytes) -> Iterator[list[Any]]:
    workbook = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    try:
        sheet = workbook.worksheets[0]  # data is on the first sheet
        for row in sheet.iter_rows(values_only=True):
            yield list(row)
    finally:
        workbook.close()


def _iter_csv(content: bytes) -> Iterator[list[Any]]:
    yield from csv.reader(io.StringIO(content.decode("utf-8-sig")))


def _fit(row: list[Any], width: int) -> list[Any]:
    return row[:width] if len(row) >= width else row + [None] * (width - len(row))


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _dedupe(names: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    result = []
    for name in names:
        seen[name] = seen.get(name, 0) + 1
        result.append(name if seen[name] == 1 else f"{name}_{seen[name]}")
    return result
