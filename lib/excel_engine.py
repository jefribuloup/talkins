"""
Semua logic pandas/openpyxl ada di sini.
Alur: upload -> baca semua sheet -> auto-clean dasar -> simpan snapshot 'original'
      dan 'working' (identik) ke Neon. Manipulasi selanjutnya HANYA mengubah 'working'.
"""
import io
import re
import bisect
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
        return raw, {}, [], 1
    header_row = _detect_header_row(raw)
    # raw.index masih posisi baris ASLI (0-based) di sheet -> +1 = nomor baris seperti di Excel
    header_sheet_row = int(raw.index[header_row]) + 1
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
    return df, col_origin, row_origin, header_sheet_row


# ---------- Deteksi kolom rumus/formula bawaan ----------

MAX_FORMULA_SCAN_ROWS = 500  # batas baris yg discan rumusnya (cukup mewakili, ga perlu scan semua)


# Referensi sel A1-style: B4, $B$4, B4:B10. Lookbehind/lookahead menghindari salah tangkap
# nama fungsi (LOG10(, ATAN2() dan referensi lintas-sheet (Sheet2!A1).
_REF_RE = re.compile(
    r"(?<![A-Za-z0-9_.!'\"])\$?([A-Z]{1,3})\$?(\d+)(?::\$?([A-Z]{1,3})\$?(\d+))?(?![A-Za-z0-9_(])"
)
MAX_DEPS_PER_FORMULA = 12


def _idx_to_letters(idx):
    """0 -> A, 25 -> Z, 26 -> AA (kebalikan _letters_to_idx)."""
    n, out = idx + 1, ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        out = chr(65 + rem) + out
    return out


def _letters_to_idx(letters):
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _parse_formula_deps(formula, inv_col_origin, row_origin):
    """Ekstrak 'bahan' rumus: list {"ref", "cols", "rows"} dgn kolom dipetakan ke NAMA
    kolom hasil pembersihan dan baris dipetakan ke nomor baris (1-based) di tabel
    bersih. rows=None kalau referensinya di luar area data (mis. baris judul)."""
    deps, seen = [], set()
    for m in _REF_RE.finditer(formula):
        ref = m.group(0).replace("$", "")
        if ref in seen:
            continue
        seen.add(ref)
        c1 = _letters_to_idx(m.group(1))
        r1 = int(m.group(2)) - 1
        c2 = _letters_to_idx(m.group(3)) if m.group(3) else c1
        r2 = int(m.group(4)) - 1 if m.group(4) else r1
        c_lo, c_hi = min(c1, c2), max(c1, c2)
        r_lo, r_hi = min(r1, r2), max(r1, r2)
        cols = [inv_col_origin[c] for c in range(c_lo, min(c_hi, c_lo + 60) + 1) if c in inv_col_origin]
        i_lo = bisect.bisect_left(row_origin, r_lo)
        i_hi = bisect.bisect_right(row_origin, r_hi) - 1
        rows = [i_lo + 1, i_hi + 1] if i_lo <= i_hi else None
        deps.append({"ref": ref, "cols": cols, "rows": rows})
        if len(deps) >= MAX_DEPS_PER_FORMULA:
            break
    return deps


def _detect_formulas(raw_bytes, filename, sheet_name, columns, col_origin, row_origin):
    """Baca ulang file pakai openpyxl (data_only=False) untuk mendapat 2 hal sekaligus:

    1) formula_columns: {nama_kolom: {"count","checked","ratio","sample"}} -> ringkasan
       per kolom (dipakai penanda ƒx di header, profil chat, dan fallback tab Rumus).
    2) formula_cells: {"<posisi_baris>": {nama_kolom: {"f","deps","x"}}} -> peta PER SEL, hanya
       sel yang benar-benar rumus (sparse). posisi_baris = index 0-based baris di
       working_data SAAT INGEST. Kalau nanti baris di-sort/filter/dedupe, posisi itu
       bergeser -> ditandai lewat row_order_dirty di db, bukan dihapus.

    Kenapa baca ulang: pandas hanya membaca HASIL hitung rumus (angka), bukan
    string rumusnya. Hanya .xlsx/.xlsm; format .xls lama (xlrd) tidak bisa.
    Mengembalikan (formula_columns, formula_cells).
    """
    if not filename.lower().endswith((".xlsx", ".xlsm")):
        return {}, {}
    try:
        wb = openpyxl.load_workbook(io.BytesIO(raw_bytes), data_only=False)
    except Exception:
        return {}, {}
    if sheet_name not in wb.sheetnames:
        wb.close()
        return {}, {}
    ws = wb[sheet_name]

    scan_rows = list(enumerate(row_origin[:MAX_FORMULA_SCAN_ROWS]))  # (posisi, baris_asli)
    inv_col_origin = {idx: name for name, idx in col_origin.items()}
    formula_columns = {}
    formula_cells = {}

    for col_name in columns:
        orig_col = col_origin.get(col_name)
        if orig_col is None:
            continue
        checked = formula_hits = 0
        sample_formula = None
        for row_pos, orig_row in scan_rows:
            # +1 krn openpyxl 1-based, sedangkan col_origin/row_origin 0-based
            val = ws.cell(row=orig_row + 1, column=orig_col + 1).value
            if val is None:
                continue
            checked += 1
            if isinstance(val, str) and val.startswith("="):
                formula_hits += 1
                if sample_formula is None:
                    sample_formula = val
                formula_cells.setdefault(str(row_pos), {})[col_name] = {
                    "f": val,
                    "deps": _parse_formula_deps(val, inv_col_origin, row_origin),
                    "x": "!" in val,  # merujuk sheet lain
                }
        if formula_hits:
            formula_columns[col_name] = {
                "count": formula_hits,
                "checked": checked,
                "ratio": round(formula_hits / checked, 2) if checked else 0,
                "sample": sample_formula,
            }

    wb.close()
    return formula_columns, formula_cells


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
        clean_df, col_origin, row_origin, header_sheet_row = _clean_dataframe(raw_df)
        if clean_df.empty:
            continue
        columns = list(clean_df.columns.astype(str))
        records = df_to_records(clean_df)
        formula_columns, formula_cells = _detect_formulas(
            raw_bytes, filename, sheet_name, columns, col_origin, row_origin
        )
        # Huruf kolom di sheet ASLI (bukan urutan setelah dibersihkan) supaya header tabel
        # (A, B, C...) cocok dengan referensi di rumus seperti =B4*C4.
        column_letters = {c: _idx_to_letters(col_origin[c]) for c in columns if c in col_origin}
        db.save_sheet(file_id, sheet_name, records, columns, formula_columns, formula_cells,
                      column_letters, header_row=header_sheet_row)
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

    formula_columns, formula_cells_all, row_order_dirty, column_letters = db.get_formula_info(
        file_id, sheet_name
    )
    header_row = db.get_header_row(file_id, sheet_name)

    # Peta per-sel hanya valid kalau urutan baris belum berubah sejak ingest.
    # Kirim hanya potongan untuk halaman ini, dgn key relatif halaman (0..len(chunk)-1).
    formula_cells_page = {}
    if not row_order_dirty:
        for i in range(len(chunk)):
            row_map = formula_cells_all.get(str(start + i))
            if row_map:
                formula_cells_page[str(i)] = row_map

    return {
        "columns": conn_cols,
        "rows": chunk,
        "page": page,
        "page_size": PAGE_SIZE,
        "total_pages": total_pages,
        "total_rows": total_rows,
        "formula_columns": formula_columns,
        "formula_cells": formula_cells_page,
        "row_order_dirty": row_order_dirty,
        "column_letters": column_letters,
        # nomor baris header di sheet asli (1-based); baris data pertama = header_row + 1
        "header_row": header_row,
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

    # Aksi yang mengubah urutan/jumlah baris -> peta rumus per-sel (berbasis posisi
    # baris saat ingest) tidak akurat lagi sampai user reset ke data asli.
    if action in ("drop_na", "drop_duplicates", "filter_rows", "sort"):
        db.set_row_order_dirty(file_id, sheet_name, True)

    # Sinkronkan nama kolom di info rumus kalau kolom di-rename/dihapus.
    if action in ("rename_column", "drop_column"):
        formula_columns = db.get_formula_columns(file_id, sheet_name)
        formula_cells = db.get_formula_cells(file_id, sheet_name)
        column_letters = db.get_column_letters(file_id, sheet_name)
        if action == "rename_column":
            old, new = op["from"], op["to"]
            if old in formula_columns:
                formula_columns[new] = formula_columns.pop(old)
            if old in column_letters:
                column_letters[new] = column_letters.pop(old)
            for row_map in formula_cells.values():
                if old in row_map:
                    row_map[new] = row_map.pop(old)
        else:
            for c in op.get("columns", []):
                formula_columns.pop(c, None)
                column_letters.pop(c, None)
                for row_map in formula_cells.values():
                    row_map.pop(c, None)
        db.update_formula_columns(file_id, sheet_name, formula_columns)
        db.update_formula_cells(file_id, sheet_name, formula_cells)
        db.update_column_letters(file_id, sheet_name, column_letters)

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
