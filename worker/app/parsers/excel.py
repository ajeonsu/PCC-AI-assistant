"""Excel and CSV parser with smart header detection, type inference, and catalog generation."""

from __future__ import annotations

import datetime as dt
import io
import json
import math
import re
from dataclasses import dataclass
from typing import Any

import pandas as pd
from python_calamine import CalamineWorkbook


@dataclass(slots=True)
class ParsedSheet:
    """Normalized worksheet ready for SQL COPY and spreadsheet_catalog indexing."""

    sheet_name: str
    sql_table_name: str
    row_count: int
    columns_meta: list[dict[str, str]]
    sample_rows: list[dict[str, Any]]
    summary_text: str
    dataframe: pd.DataFrame
    pg_types: list[tuple[str, str]]


def _sanitize_identifier(raw: str, max_len: int = 55, fallback: str = "col") -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", raw.strip().lower()).strip("_")
    if not cleaned:
        cleaned = fallback
    if cleaned[0].isdigit():
        cleaned = f"c_{cleaned}"
    return cleaned[:max_len].strip("_") or fallback


def make_sql_table_name(file_id: str, sheet_name: str, used_names: set[str]) -> str:
    """Create a deterministic, <=63-char PostgreSQL table name in excel_data."""
    id_part = re.sub(r"[^a-zA-Z0-9]", "", file_id).lower()[:10] or "file"
    sheet_part = _sanitize_identifier(sheet_name, max_len=30, fallback="sheet")
    base = f"xl_{id_part}_{sheet_part}"[:58]
    candidate = f"excel_data.{base}"
    suffix = 2
    while candidate in used_names:
        candidate = f"excel_data.{base[:54]}_{suffix}"
        suffix += 1
    used_names.add(candidate)
    return candidate


def _detect_header_row(rows: list[list[Any]], scan_limit: int = 15) -> int:
    """Pick the most likely header row among the top rows."""
    best_idx = 0
    best_score = -1.0

    for idx, row in enumerate(rows[:scan_limit]):
        non_empty = [v for v in row if v is not None and str(v).strip() != ""]
        if not non_empty:
            continue
        str_count = sum(1 for v in non_empty if isinstance(v, str) and not _looks_numeric(v))
        unique_count = len({str(v).strip().lower() for v in non_empty})
        score = len(non_empty) * 2.0 + unique_count * 0.5 + str_count * 1.0 - idx * 0.15
        if score > best_score:
            best_score = score
            best_idx = idx

    return best_idx


def _looks_numeric(val: Any) -> bool:
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        return True
    s = str(val).strip()
    if not s:
        return False
    s_clean = re.sub(r"[₱$,%\s]|PHP|USD", "", s, flags=re.IGNORECASE)
    try:
        float(s_clean)
        return True
    except ValueError:
        return False


def _has_leading_zero_code(val: str) -> bool:
    s = val.strip()
    return bool(re.match(r"^0\d+$", s))


def _clean_numeric_series(series: pd.Series) -> pd.Series:
    def _to_num(v: Any) -> float | None:
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return None
        if isinstance(v, bool):
            return None
        if isinstance(v, (int, float)):
            return float(v)
        s = str(v).strip()
        if not s or s in ("-", "--", "N/A", "n/a", "NA", "null", "None"):
            return None
        if _has_leading_zero_code(s):
            return None
        neg = s.startswith("(") and s.endswith(")")
        s = s.strip("()")
        s = re.sub(r"[₱$,\s]|PHP|USD", "", s, flags=re.IGNORECASE)
        if not s:
            return None
        num = float(s)
        return -num if neg else num

    return series.map(_to_num)


def _infer_and_convert_column(series: pd.Series) -> tuple[pd.Series, str]:
    """Infer safe PostgreSQL type (BIGINT, DOUBLE PRECISION, DATE, TIMESTAMP, TEXT)."""
    non_null = [v for v in series.tolist() if v is not None and not (isinstance(v, float) and math.isnan(v)) and str(v).strip() != ""]
    if not non_null:
        return pd.Series([None] * len(series), dtype=object), "TEXT"

    # Check native dates / datetimes first
    if all(isinstance(v, (dt.date, dt.datetime, pd.Timestamp)) for v in non_null):
        has_time = any(
            isinstance(v, (dt.datetime, pd.Timestamp)) and (v.hour or v.minute or v.second)
            for v in non_null
        )
        if has_time:
            converted = [
                v.to_pydatetime() if isinstance(v, pd.Timestamp) else (
                    v if isinstance(v, dt.datetime) else dt.datetime(v.year, v.month, v.day)
                )
                if v is not None and not (isinstance(v, float) and math.isnan(v))
                else None
                for v in series.tolist()
            ]
            return pd.Series(converted, dtype=object), "TIMESTAMP"
        converted_d = [
            v.date() if isinstance(v, (dt.datetime, pd.Timestamp)) else (v if isinstance(v, dt.date) else None)
            for v in series.tolist()
        ]
        return pd.Series(converted_d, dtype=object), "DATE"

    # Check numeric conversion (require >= 90% parse rate and no leading-zero codes)
    has_leading_zeros = any(isinstance(v, str) and _has_leading_zero_code(v) for v in non_null)
    if not has_leading_zeros:
        parsed_nums: list[float | None] = []
        ok_count = 0
        try:
            cleaned = _clean_numeric_series(series)
            for orig, conv in zip(series.tolist(), cleaned.tolist()):
                is_empty = orig is None or (isinstance(orig, float) and math.isnan(orig)) or str(orig).strip() == ""
                if is_empty:
                    parsed_nums.append(None)
                elif conv is not None and math.isfinite(conv):
                    parsed_nums.append(conv)
                    ok_count += 1
                else:
                    parsed_nums.append(None)
            if len(non_null) > 0 and (ok_count / len(non_null)) >= 0.90:
                valid_vals = [v for v in parsed_nums if v is not None]
                if valid_vals and all(float(v).is_integer() for v in valid_vals):
                    if max(abs(int(v)) for v in valid_vals) <= 9_000_000_000_000_000:
                        int_vals = [int(v) if v is not None else None for v in parsed_nums]
                        return pd.Series(int_vals, dtype=object), "BIGINT"
                return pd.Series(parsed_nums, dtype=object), "DOUBLE PRECISION"
        except (ValueError, TypeError, OverflowError):
            pass

    # Fallback: clean TEXT
    text_vals: list[str | None] = []
    for v in series.tolist():
        if v is None or (isinstance(v, float) and math.isnan(v)):
            text_vals.append(None)
        else:
            s = str(v).strip()
            text_vals.append(s if s != "" else None)
    return pd.Series(text_vals, dtype=object), "TEXT"


def _json_safe(val: Any) -> Any:
    if val is None:
        return None
    if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
        return None
    if isinstance(val, (dt.datetime, dt.date, pd.Timestamp)):
        return val.isoformat()
    return val


def _process_raw_rows(
    raw_rows: list[list[Any]],
    sheet_name: str,
    file_id: str,
    file_name: str,
    folder_path: str,
    modified_time: str | None,
    used_names: set[str],
) -> ParsedSheet | None:
    # Trim trailing completely empty rows
    while raw_rows and all(v is None or str(v).strip() == "" for v in raw_rows[-1]):
        raw_rows.pop()
    if len(raw_rows) < 2:
        return None

    header_idx = _detect_header_row(raw_rows)
    header_raw = raw_rows[header_idx]
    data_rows = raw_rows[header_idx + 1 :]
    if not data_rows:
        return None

    max_cols = max([len(header_raw)] + [len(r) for r in data_rows])
    padded_header = list(header_raw) + [None] * (max_cols - len(header_raw))
    padded_data = [list(r) + [None] * (max_cols - len(r)) for r in data_rows]

    df = pd.DataFrame(padded_data, dtype=object)

    # Drop columns that have neither header nor any data
    keep_cols: list[int] = []
    for col_i in range(max_cols):
        hdr = padded_header[col_i]
        has_hdr = hdr is not None and str(hdr).strip() != ""
        has_data = any(
            v is not None and not (isinstance(v, float) and math.isnan(v)) and str(v).strip() != ""
            for v in df.iloc[:, col_i].tolist()
        )
        if has_hdr or has_data:
            keep_cols.append(col_i)

    if not keep_cols:
        return None

    df = df.iloc[:, keep_cols].copy()
    raw_headers = [
        str(padded_header[c]).strip() if padded_header[c] is not None and str(padded_header[c]).strip() != "" else f"column_{i + 1}"
        for i, c in enumerate(keep_cols)
    ]

    # Drop completely empty rows
    mask_non_empty = df.apply(
        lambda row: any(
            v is not None and not (isinstance(v, float) and math.isnan(v)) and str(v).strip() != ""
            for v in row.tolist()
        ),
        axis=1,
    )
    df = df[mask_non_empty].reset_index(drop=True)
    if df.empty:
        return None

    # Deduplicate SQL column names
    used_cols: set[str] = set()
    sql_cols: list[str] = []
    for i, orig in enumerate(raw_headers):
        base = _sanitize_identifier(orig, max_len=55, fallback=f"col_{i + 1}")
        col = base
        suffix = 2
        while col in used_cols:
            col = f"{base[:50]}_{suffix}"
            suffix += 1
        used_cols.add(col)
        sql_cols.append(col)

    df.columns = sql_cols
    pg_types: list[tuple[str, str]] = []
    columns_meta: list[dict[str, str]] = []

    for sql_col, orig_hdr in zip(sql_cols, raw_headers):
        converted_series, pg_type = _infer_and_convert_column(df[sql_col])
        df[sql_col] = converted_series
        pg_types.append((sql_col, pg_type))
        columns_meta.append(
            {"name": sql_col, "original_header": orig_hdr, "pg_type": pg_type}
        )

    sample_rows: list[dict[str, Any]] = []
    for _, row in df.head(5).iterrows():
        sample_rows.append({k: _json_safe(row[k]) for k in sql_cols})

    table_name = make_sql_table_name(file_id, sheet_name, used_names)
    cols_desc = ", ".join(f"{c['name']} ({c['pg_type']}, header='{c['original_header']}')" for c in columns_meta[:40])
    summary_text = (
        f"Spreadsheet file: {file_name} | Folder: {folder_path} | "
        f"Modified: {modified_time or 'unknown'} | Sheet: {sheet_name} | "
        f"Table: {table_name} | Rows: {len(df)} | Columns: {cols_desc}"
    )

    return ParsedSheet(
        sheet_name=sheet_name,
        sql_table_name=table_name,
        row_count=len(df),
        columns_meta=columns_meta,
        sample_rows=sample_rows,
        summary_text=summary_text,
        dataframe=df,
        pg_types=pg_types,
    )


def parse_spreadsheet_bytes(
    data: bytes,
    file_id: str,
    file_name: str,
    folder_path: str,
    modified_time: str | None,
    is_csv: bool = False,
) -> list[ParsedSheet]:
    """Parse an Excel (.xlsx/.xlsm/.xls) or CSV file into normalized ParsedSheet objects."""
    used_names: set[str] = set()
    parsed_sheets: list[ParsedSheet] = []

    if is_csv:
        try:
            raw_df = pd.read_csv(io.BytesIO(data), header=None, dtype=object, keep_default_na=False)
        except UnicodeDecodeError:
            raw_df = pd.read_csv(
                io.BytesIO(data),
                header=None,
                dtype=object,
                keep_default_na=False,
                encoding="latin-1",
            )
        raw_rows = raw_df.values.tolist()
        sheet = _process_raw_rows(
            raw_rows, "Sheet1", file_id, file_name, folder_path, modified_time, used_names
        )
        if sheet is not None:
            parsed_sheets.append(sheet)
        return parsed_sheets

    workbook = CalamineWorkbook.from_filelike(io.BytesIO(data))
    for sheet_name in workbook.sheet_names:
        raw_rows = workbook.get_sheet_by_name(sheet_name).to_python()
        sheet = _process_raw_rows(
            raw_rows, sheet_name, file_id, file_name, folder_path, modified_time, used_names
        )
        if sheet is not None:
            parsed_sheets.append(sheet)

    return parsed_sheets
