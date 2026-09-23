"""
Semua logic pandas/openpyxl ada di sini.
Alur: upload -> baca semua sheet -> auto-clean dasar -> simpan snapshot 'original'
      dan 'working' (identik) ke Neon. Manipulasi selanjutnya HANYA mengubah 'working'.
"""
import io
import uuid
import math
import datetime

import pandas as pd
import numpy as np
from flask import send_file

from lib import db

PAGE_SIZE = 50


# ---------- Konversi aman ke JSON ----------

def _to_jsonable(val):
    if val is None:
        return None
    if isinstance(val, float) and math.isnan(val):
        return None
    try:
        if pd.isna(val):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(val, (pd.Timestamp, datetime.datetime, datetime.date, datetime.time)):
        return val.isoformat()
    if isinstance(val, (np.datetime64,)):
        return pd.Timestamp(val).isoformat()
    if isinstance(val, (np.integer,)):
        return int(val)
    if isinstance(val, (np.floating,)):
        return float(val)
    if isinstance(val, (np.bool_,)):
        return bool(val)
    return val


def df_to_records(df: pd.DataFrame):
    records = []
    for row in df.itertuples(index=False, name=None):
        records.append([_to_jsonable(v) for v in row])
    return records


def records_to_df(records, columns):
    return pd.DataFrame(records, columns=columns)


# ---------- Auto-clean file kompleks ----------

def _detect_header_row(raw: pd.DataFrame, max_scan=10):
    """Cari baris yang paling mungkin jadi header (paling sedikit sel kosong,
    banyak sel berupa teks unik) — dipakai untuk file dgn baris judul/logo di atas tabel."""
    best_row, best_score = 0, -1
    for i in range(min(max_scan, len(raw))):
        row = raw.iloc[i]
        non_null = row.notna().sum()
        text_like = sum(isinstance(v, str) for v in row if pd.notna(v))
        score = non_null + text_like
        if score > best_score:
            best_score, best_row = score, i
    return best_row


def _clean_dataframe(raw: pd.DataFrame) -> pd.DataFrame:
    raw = raw.dropna(how="all").dropna(axis=1, how="all")
    if raw.empty:
        return raw
    header_row = _detect_header_row(raw)
    header = raw.iloc[header_row]
    df = raw.iloc[header_row + 1:].copy()
    cols = []
    seen = {}
    for i, c in enumerate(header):
        name = str(c).strip() if pd.notna(c) else f"kolom_{i+1}"
        if name in seen:
            seen[name] += 1
            name = f"{name}_{seen[name]}"
        else:
            seen[name] = 0
        cols.append(name)
    df.columns = cols
    df = df.dropna(how="all").reset_index(drop=True)
    return df


# ---------- Ingest ----------

def _pick_engine(filename: str) -> str:
    lower = filename.lower()
    if lower.endswith(".xls"):
        return "xlrd"
    return "openpyxl"


def ingest_file(file_storage, session_id):
    filename = file_storage.filename
    if not filename.lower().endswith((".xlsx", ".xls", ".xlsm")):
        raise ValueError("Format harus .xlsx / .xls / .xlsm")

    raw_bytes = file_storage.read()
    engine = _pick_engine(filename)
    try:
        xls = pd.ExcelFile(io.BytesIO(raw_bytes), engine=engine)
    except Exception:
        raise ValueError(
            "File tidak bisa dibaca. Pastikan file .xls/.xlsx tidak rusak atau "
            "terkunci password."
        )
    file_id = str(uuid.uuid4())
    db.create_file(file_id, session_id, filename, [])

    sheet_names = []
    for sheet_name in xls.sheet_names:
        raw_df = pd.read_excel(xls, sheet_name=sheet_name, header=None)
        clean_df = _clean_dataframe(raw_df)
        if clean_df.empty:
            continue
        columns = list(clean_df.columns.astype(str))
        records = df_to_records(clean_df)
        db.save_sheet(file_id, sheet_name, records, columns)
        sheet_names.append(sheet_name)

    if not sheet_names:
        db.delete_file(session_id, file_id)
        raise ValueError("Tidak ada data yang bisa dibaca dari file ini")

    db.update_file_sheets(file_id, sheet_names)
    return {
        "file_id": file_id,
        "filename": filename,
        "sheets": sheet_names,
    }


# ---------- Preview & metadata ----------

def get_sheet_list(file_id):
    return {"sheets": db.list_sheet_names(file_id)}


def preview_sheet(file_id, sheet_name, page=1):
    records = db.get_sheet(file_id, sheet_name, version="working")
    if records is None:
        raise ValueError("Sheet tidak ditemukan")
    conn_cols = _get_columns(file_id, sheet_name)
    total_rows = len(records)
    total_pages = max(1, math.ceil(total_rows / PAGE_SIZE))
    page = max(1, min(page, total_pages))
    start = (page - 1) * PAGE_SIZE
    chunk = records[start:start + PAGE_SIZE]
    return {
        "columns": conn_cols,
        "rows": chunk,
        "page": page,
        "total_pages": total_pages,
        "total_rows": total_rows,
    }


def _get_columns(file_id, sheet_name):
    conn = db.get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT columns FROM sheets WHERE file_id=%s AND sheet_name=%s",
        (file_id, sheet_name),
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row[0] if row else []


# ---------- Manipulasi working copy ----------

def apply_operation(file_id, op):
    sheet_name = op.get("sheet")
    action = op.get("action")
    columns = _get_columns(file_id, sheet_name)
    records = db.get_sheet(file_id, sheet_name, version="working")
    df = records_to_df(records, columns)

    if action == "drop_na":
        cols = op.get("columns") or None
        df = df.dropna(subset=cols) if cols else df.dropna(how="all")

    elif action == "fill_na":
        value = op.get("value", "")
        cols = op.get("columns") or df.columns.tolist()
        df[cols] = df[cols].fillna(value)

    elif action == "drop_duplicates":
        cols = op.get("columns") or None
        df = df.drop_duplicates(subset=cols)

    elif action == "rename_column":
        df = df.rename(columns={op["from"]: op["to"]})

    elif action == "drop_column":
        df = df.drop(columns=[c for c in op.get("columns", []) if c in df.columns])

    elif action == "filter_rows":
        col, cond, val = op["column"], op["condition"], op["value"]
        series = df[col]
        if cond == "eq":
            df = df[series == val]
        elif cond == "neq":
            df = df[series != val]
        elif cond == "gt":
            df = df[pd.to_numeric(series, errors="coerce") > float(val)]
        elif cond == "lt":
            df = df[pd.to_numeric(series, errors="coerce") < float(val)]
        elif cond == "contains":
            df = df[series.astype(str).str.contains(str(val), case=False, na=False)]

    elif action == "sort":
        col = op["column"]
        ascending = op.get("ascending", True)
        df = df.sort_values(by=col, ascending=ascending)

    elif action == "cast_numeric":
        cols = op.get("columns", [])
        for c in cols:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    else:
        raise ValueError(f"Aksi tidak dikenal: {action}")

    df = df.reset_index(drop=True)
    new_columns = list(df.columns.astype(str))
    new_records = df_to_records(df)
    db.update_working_data(file_id, sheet_name, new_records, new_columns)
    return {
        "columns": new_columns,
        "row_count": len(new_records),
        "preview": new_records[:PAGE_SIZE],
    }


def reset_to_original(file_id):
    db.reset_working_data(file_id)


# ---------- Export working copy jadi file baru (bukan menimpa file asli) ----------

def export_working_copy(file_id):
    file_meta = db.get_file(file_id)
    sheet_names = db.list_sheet_names(file_id)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        for sheet_name in sheet_names:
            columns = _get_columns(file_id, sheet_name)
            records = db.get_sheet(file_id, sheet_name, version="working")
            df = records_to_df(records, columns)
            df.to_excel(writer, sheet_name=sheet_name[:31], index=False)
    output.seek(0)
    download_name = f"edited_{file_meta['filename']}"
    return send_file(
        output,
        as_attachment=True,
        download_name=download_name,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
