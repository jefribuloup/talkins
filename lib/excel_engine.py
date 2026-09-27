"""
Semua logic pandas/openpyxl ada di sini.
Alur: upload -> baca semua sheet -> auto-clean dasar -> simpan snapshot 'original'
      dan 'working' (identik) ke Neon. Manipulasi selanjutnya HANYA mengubah 'working'.
"""
import io
import re
import uuid
import math
import datetime

import pandas as pd
import numpy as np
import requests
import openpyxl
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


def _clean_dataframe(raw: pd.DataFrame):
    """Bersihkan dataframe mentah jadi tabel siap pakai.
    Selain dataframe bersih, kembalikan juga peta posisi ASLI (di sheet Excel
    sebelum dibersihkan) untuk tiap kolom & baris yang selamat -> dipakai nanti
    untuk mencocokkan balik ke openpyxl saat deteksi kolom rumus/formula."""
    raw = raw.dropna(how="all").dropna(axis=1, how="all")
    if raw.empty:
        return raw, {}, []
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

    # raw.columns masih berupa posisi kolom ASLI (0-based) krn raw_df dibaca
    # dengan header=None, jadi belum pernah di-reindex sebelum titik ini.
    col_origin = dict(zip(cols, raw.columns.tolist()))

    df.columns = cols
    df = df.dropna(how="all")
    # df.index di titik ini masih posisi baris ASLI (0-based) di raw_df -> sama
    # dengan posisi baris di sheet Excel aslinya (baris pertama sheet = index 0).
    row_origin = df.index.tolist()
    df = df.reset_index(drop=True)
    return df, col_origin, row_origin


# ---------- Deteksi kolom rumus/formula bawaan ----------

MAX_FORMULA_SAMPLE_ROWS = 300  # cukup buat nentuin pola kolom, ga perlu scan semua baris


def _detect_formula_columns(raw_bytes, filename, sheet_name, columns, col_origin, row_origin):
    """Kembalikan {nama_kolom: {"count", "checked", "ratio", "sample"}} untuk
    kolom yang selnya berisi rumus Excel asli (string diawali '=') di data
    aslinya, bukan nilai statis biasa.

    Kenapa perlu baca ulang file: pandas/openpyxl versi 'data_only' membaca
    HASIL hitungan rumus (angkanya), bukan rumusnya sendiri -> itu yang dipakai
    utk isi tabel/chat. Di sini kita baca file yang sama sekali lagi pakai
    openpyxl mode data_only=False, khusus buat intip string rumus aslinya,
    lalu dipetakan balik ke kolom yang sudah dibersihkan lewat col_origin/row_origin.

    Hanya berlaku utk .xlsx/.xlsm — format .xls lama (engine xlrd) tidak
    menyimpan rumus dengan cara yang bisa dibaca ulang seperti ini.
    """
    if not filename.lower().endswith((".xlsx", ".xlsm")):
        return {}
    try:
        wb = openpyxl.load_workbook(io.BytesIO(raw_bytes), data_only=False)
    except Exception:
        return {}
    if sheet_name not in wb.sheetnames:
        wb.close()
        return {}
    ws = wb[sheet_name]

    sample_rows = row_origin[:MAX_FORMULA_SAMPLE_ROWS]
    result = {}
    for col_name in columns:
        orig_col = col_origin.get(col_name)
        if orig_col is None:
            continue
        checked = formula_hits = 0
        sample_formula = None
        for orig_row in sample_rows:
            # +1 krn openpyxl 1-based, raw_df/col_origin/row_origin 0-based
            val = ws.cell(row=orig_row + 1, column=orig_col + 1).value
            if val is None:
                continue
            checked += 1
            if isinstance(val, str) and val.startswith("="):
                formula_hits += 1
                if sample_formula is None:
                    sample_formula = val
        if formula_hits:
            result[col_name] = {
                "count": formula_hits,
                "checked": checked,
                "ratio": round(formula_hits / checked, 2) if checked else 0,
                "sample": sample_formula,
            }
    wb.close()
    return result


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
    return _ingest_bytes(raw_bytes, filename, session_id, source="upload")


# ---------- Ingest dari link spreadsheet (mis. Google Sheets publik) ----------

_GOOGLE_SHEET_ID_RE = re.compile(r"/spreadsheets/d/([a-zA-Z0-9-_]+)")


def _resolve_spreadsheet_url(url: str):
    """Kalau link Google Sheets biasa -> ubah jadi link export .xlsx langsung.
    Link lain (mis. URL download .xlsx dari cloud storage) dipakai apa adanya."""
    m = _GOOGLE_SHEET_ID_RE.search(url)
    if m:
        sheet_id = m.group(1)
        return (
            f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=xlsx",
            f"google-sheet-{sheet_id}.xlsx",
        )
    name = url.split("?")[0].rstrip("/").split("/")[-1] or "spreadsheet.xlsx"
    if not name.lower().endswith((".xlsx", ".xls", ".xlsm")):
        name += ".xlsx"
    return url, name


def ingest_from_url(url, session_id):
    if not url or not url.strip().lower().startswith(("http://", "https://")):
        raise ValueError("Link tidak valid")

    fetch_url, filename = _resolve_spreadsheet_url(url.strip())
    try:
        resp = requests.get(fetch_url, timeout=20, allow_redirects=True)
        resp.raise_for_status()
    except requests.RequestException:
        raise ValueError(
            "Gagal mengambil data dari link. Pastikan link bisa diakses publik "
            "(Google Sheets: Share -> Anyone with the link -> Viewer)."
        )

    return _ingest_bytes(resp.content, filename, session_id, source="url")


# ---------- Logic inti ingest, dipakai baik oleh upload file maupun link ----------

def _ingest_bytes(raw_bytes, filename, session_id, source="upload"):
    engine = _pick_engine(filename)
    try:
        xls = pd.ExcelFile(io.BytesIO(raw_bytes), engine=engine)
    except Exception:
        raise ValueError(
            "File/spreadsheet tidak bisa dibaca. Pastikan formatnya didukung "
            "(.xlsx/.xls/.xlsm) dan, untuk link, aksesnya publik."
        )
    file_id = str(uuid.uuid4())
    db.create_file(file_id, session_id, filename, [], source=source)

    sheet_names = []
    for sheet_name in xls.sheet_names:
        raw_df = pd.read_excel(xls, sheet_name=sheet_name, header=None)
        clean_df, col_origin, row_origin = _clean_dataframe(raw_df)
        if clean_df.empty:
            continue
        columns = list(clean_df.columns.astype(str))
        records = df_to_records(clean_df)
        formula_columns = _detect_formula_columns(
            raw_bytes, filename, sheet_name, columns, col_origin, row_origin
        )
        db.save_sheet(file_id, sheet_name, records, columns, formula_columns)
        sheet_names.append(sheet_name)

    if not sheet_names:
        db.delete_file(session_id, file_id)
        raise ValueError("Tidak ada data yang bisa dibaca dari file/link ini")

    db.update_file_sheets(file_id, sheet_names)
    return {
        "file_id": file_id,
        "filename": filename,
        "sheets": sheet_names,
        "source": source,
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
        "formula_columns": db.get_formula_columns(file_id, sheet_name),
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

    # Sinkronkan info kolom-rumus kalau nama kolom berubah/kolom dihapus,
    # supaya badge "ƒx" di UI & profil data buat chat tetap akurat.
    if action in ("rename_column", "drop_column"):
        formula_columns = db.get_formula_columns(file_id, sheet_name)
        if formula_columns:
            if action == "rename_column" and op["from"] in formula_columns:
                formula_columns[op["to"]] = formula_columns.pop(op["from"])
            elif action == "drop_column":
                for c in op.get("columns", []):
                    formula_columns.pop(c, None)
            db.update_formula_columns(file_id, sheet_name, formula_columns)

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
