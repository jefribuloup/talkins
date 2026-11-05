"""
PATCH untuk lib/excel_engine.py  --  jalur "FILE BESAR"
=======================================================
Prasyarat: patch_db_large_mode.py sudah terpasang di lib/db.py.

Semua jalur baru hanya aktif kalau file/sheet melewati ambang. File kecil memakai kode lama
persis seperti sekarang.

  Ambang (bisa diubah lewat Environment Variable di Vercel, tanpa edit kode):
    LARGE_FILE_MB  (default 3)       ukuran file >= ini  -> baca pakai engine cepat (calamine)
                                      + scan rumus versi hemat memori
    LARGE_ROWS     (default 10000)   sheet >= ini baris   -> simpan per baris (mode 'rows')
    LARGE_CELLS    (default 150000)  sheet >= ini sel     -> simpan per baris (mode 'rows')

CARA PASANG (urut):
  [1] konfigurasi + helper  -> tempel setelah baris `PAGE_SIZE = 50`
  [2] _detect_formulas      -> ganti fungsi lama
  [3] _ingest_bytes         -> ganti fungsi lama
  [4] preview_sheet         -> ganti fungsi lama
  [5] _set_cell_fast        -> tempel fungsi baru + 3 baris "hook" di awal set_cell (lihat catatan)
  [6] export_working_copy   -> ganti fungsi lama (+ tempel _export_streaming)
  [7] requirements.txt      -> tambah:  python-calamine  dan  xlsxwriter   (pandas >= 2.2)
"""
import io
import os
import hashlib
import uuid

import pandas as pd
import openpyxl
from flask import send_file

# ---------------------------------------------------------------------------
# [1] KONFIGURASI + HELPER
# ---------------------------------------------------------------------------

def _env_num(name, default):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return float(default)


LARGE_FILE_BYTES = int(_env_num("LARGE_FILE_MB", 3) * 1024 * 1024)
LARGE_ROW_LIMIT = int(_env_num("LARGE_ROWS", 10000))
LARGE_CELL_LIMIT = int(_env_num("LARGE_CELLS", 150000))
WIDTH_SAMPLE_ROWS = 5000  # lebar kolom (mode 'rows') dihitung dari sekian baris pertama saja

_PLAIN_TYPES = (str, int, float, bool, type(None))


def df_to_records_fast(df):
    """Versi cepat df_to_records() untuk sheet besar.
    Tidak memanggil fungsi konversi untuk tiap sel: NaN/NaT -> None dilakukan sekali secara
    vektor; _to_jsonable() hanya dipanggil untuk sel bertipe khusus (tanggal/jam/numpy)."""
    obj = df.astype(object)
    rows = obj.where(df.notna(), None).to_numpy().tolist()
    for row in rows:
        for j, v in enumerate(row):
            if not isinstance(v, _PLAIN_TYPES):
                row[j] = _to_jsonable(v)
    return rows


_CALAMINE_OK = None


def _calamine_available():
    global _CALAMINE_OK
    if _CALAMINE_OK is None:
        try:
            import python_calamine  # noqa: F401
            _CALAMINE_OK = True
        except ImportError:
            _CALAMINE_OK = False
    return _CALAMINE_OK


# ---------------------------------------------------------------------------
# [2] _detect_formulas  (parameter baru: fast)
#     fast=True: workbook dibuka read_only dan hanya baris yang discan yang dibaca.
#     Versi lama membuka SELURUH workbook ke memori (50 ribu baris = ratusan MB + puluhan detik)
#     padahal yang dipakai hanya 500 baris pertama.
# ---------------------------------------------------------------------------

def _detect_formulas(raw_bytes, filename, sheet_name, columns, col_origin, row_origin, fast=False):
    """Lihat docstring versi lama. Hasil identik; beda hanya cara membaca file."""
    if not filename.lower().endswith((".xlsx", ".xlsm")):
        return {}, {}
    try:
        wb = openpyxl.load_workbook(io.BytesIO(raw_bytes), data_only=False, read_only=fast)
    except Exception:
        return {}, {}
    if sheet_name not in wb.sheetnames:
        wb.close()
        return {}, {}
    ws = wb[sheet_name]

    scan_rows = list(enumerate(row_origin[:MAX_FORMULA_SCAN_ROWS]))  # (posisi, baris_asli)

    if fast:
        # Di mode read_only, ws.cell() sangat lambat -> baca baris yang discan SEKALI saja
        last = scan_rows[-1][1] + 1 if scan_rows else 0
        cache = list(ws.iter_rows(min_row=1, max_row=last, values_only=True)) if last else []

        def cell_value(orig_row, orig_col):
            if orig_row < len(cache):
                r = cache[orig_row]
                if orig_col < len(r):
                    return r[orig_col]
            return None
    else:
        def cell_value(orig_row, orig_col):
            # +1 krn openpyxl 1-based, sedangkan col_origin/row_origin 0-based
            return ws.cell(row=orig_row + 1, column=orig_col + 1).value

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
            val = cell_value(orig_row, orig_col)
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


# ---------------------------------------------------------------------------
# [3] _ingest_bytes
#     Perubahan dibanding versi lama:
#       - file >= LARGE_FILE_MB : coba engine calamine (jauh lebih cepat); gagal -> engine lama
#       - sheet besar           : konversi cepat + simpan per baris (save_sheet_rows)
# ---------------------------------------------------------------------------

def _ingest_bytes(raw_bytes, filename, session_id, source="upload", source_key=None):
    # Cegah dobel: isi file identik (SHA-256) dgn yang sudah ada di sesi ini
    content_hash = hashlib.sha256(raw_bytes).hexdigest()
    dup = db.find_duplicate(session_id, content_hash=content_hash, source_key=source_key)
    if dup:
        return _duplicate_response(dup)

    big_file = len(raw_bytes) >= LARGE_FILE_BYTES

    xls = None
    if big_file and _calamine_available():
        try:
            xls = pd.ExcelFile(io.BytesIO(raw_bytes), engine="calamine")
        except Exception:
            xls = None  # pandas lama / file aneh -> jatuh ke engine standar di bawah

    if xls is None:
        engine = _pick_engine(filename)
        try:
            xls = pd.ExcelFile(io.BytesIO(raw_bytes), engine=engine)
        except ImportError:
            # Pustaka pembaca belum terpasang di server (mis. xlrd untuk .xls): bukan salah file user
            raise ValueError(
                "Server belum memiliki pustaka pembaca untuk format ini (" + engine + "). "
                "Simpan file sebagai .xlsx lalu unggah ulang, atau hubungi admin."
            )
        except Exception:
            raise ValueError(
                "File/spreadsheet tidak bisa dibaca. Pastikan formatnya didukung "
                "(.xlsx/.xls/.xlsm) dan, untuk link, aksesnya publik."
            )

    file_id = str(uuid.uuid4())
    db.create_file(file_id, session_id, filename, [], source=source,
                   content_hash=content_hash, source_key=source_key)

    sheet_names = []
    for sheet_name in xls.sheet_names:
        # dtype=object: nilai dibaca apa adanya (angka tetap angka, teks tetap teks)
        raw_df = pd.read_excel(xls, sheet_name=sheet_name, header=None, dtype=object)
        grid = _build_grid(raw_df)
        del raw_df
        if grid is None:
            continue
        columns = list(grid.columns)  # ["A", "B", ...] = huruf kolom Excel asli
        use_rows = len(grid) >= LARGE_ROW_LIMIT or grid.size >= LARGE_CELL_LIMIT
        records = df_to_records_fast(grid) if use_rows else df_to_records(grid)
        del grid
        # Grid = sheet asli (tanpa pemangkasan di depan) -> posisi kolom/baris identik dgn Excel
        col_origin = {name: i for i, name in enumerate(columns)}
        row_origin = list(range(len(records)))
        formula_columns, formula_cells = _detect_formulas(
            raw_bytes, filename, sheet_name, columns, col_origin, row_origin,
            fast=(big_file or use_rows),
        )
        if use_rows:
            db.save_sheet_rows(file_id, sheet_name, records, columns, formula_columns, formula_cells)
        else:
            db.save_sheet(file_id, sheet_name, records, columns, formula_columns, formula_cells)
        del records
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


# ---------------------------------------------------------------------------
# [4] preview_sheet
#     Bentuk respons SAMA seperti sebelumnya (+ kunci baru "large"), jadi JS tidak perlu diubah.
#     Sheet mode 'rows': hanya `row_count` baris yang diambil dari database.
# ---------------------------------------------------------------------------

def preview_sheet(file_id, sheet_name, row_start=0, row_count=200, with_meta=True):
    """Kirim potongan baris [row_start, row_start+row_count) dari grid (tanpa header).

    Frontend memuat grid per blok saat user scroll. with_meta=True menambahkan info
    ringkas sheet (jumlah baris/kolom, info rumus) -> cukup diminta sekali per sheet."""
    if with_meta:
        _migrate_legacy_sheet(file_id, sheet_name)
    row_count = max(1, min(int(row_count), MAX_ROWS_PER_REQUEST))
    got = db.get_preview_data(file_id, sheet_name, row_start, row_count)
    if got is None:
        raise ValueError("Sheet tidak ditemukan")
    large = got["storage"] == "rows"
    out = {
        "row_start": got["row_start"],
        "rows": got["rows"],
    }
    if with_meta:
        columns = _get_columns(file_id, sheet_name)
        formula_columns, formula_cells, row_order_dirty, _ = db.get_formula_info(file_id, sheet_name)
        # Lebar kolom: sheet kecil dihitung dari semua baris (seperti dulu); sheet besar dari
        # WIDTH_SAMPLE_ROWS baris pertama saja supaya tidak perlu memuat seluruh sheet.
        width_rows = (db.get_rows_slice(file_id, sheet_name, 0, WIDTH_SAMPLE_ROWS)
                      if large else got["records"])
        out.update({
            "columns": columns,
            "total_rows": got["total_rows"],
            "total_cols": len(columns),
            "col_chars": _col_chars(width_rows, len(columns)),
            "formula_columns": formula_columns,
            # peta rumus per sel (sparse, hanya baris yg discan saat ingest) -> key = indeks baris
            "formula_cells": _formula_cells_view(formula_cells, row_order_dirty),
            "row_order_dirty": row_order_dirty,
            "large": large,
        })
    return out


# ---------------------------------------------------------------------------
# [5] set_cell: jalur cepat untuk sheet besar
#     Tanpa ini, mengisi SATU sel di sheet 50 ribu baris menulis ulang 50 ribu baris.
#     Jalur cepat hanya dipakai bila: sheet mode 'rows', isian bukan rumus, sel masih di
#     dalam data, dan sheet belum punya rumus isian user. Selain itu -> jalur lama (benar,
#     tapi lebih lambat).
#
#     HOOK: di fungsi set_cell yang sudah ada, tempel 3 baris ini TEPAT SEBELUM baris
#           `columns = _get_columns(file_id, sheet_name)`:
#
#               fast = _set_cell_fast(file_id, sheet_name, row, col, text)
#               if fast is not None:
#                   return fast
# ---------------------------------------------------------------------------

def _set_cell_fast(file_id, sheet_name, row, col, text):
    if text.strip().startswith("="):
        return None
    storage, nrows = db.sheet_storage(file_id, sheet_name)
    if storage != "rows":
        return None
    columns = _get_columns(file_id, sheet_name)
    if not (0 <= row < nrows and 0 <= col < len(columns)):
        return None
    _, formula_cells, dirty, _ = db.get_formula_info(file_id, sheet_name)
    if _user_formulas(formula_cells):
        return None

    value = _parse_literal(text)
    db.set_cell_value(file_id, sheet_name, row, col, value)

    # Menimpa sel yang tadinya rumus bawaan file -> catatan rumusnya dilepas
    letter = _idx_to_letters(col)
    cells = formula_cells.get(str(row))
    if cells and letter in cells:
        del cells[letter]
        if not cells:
            formula_cells.pop(str(row), None)
        db.update_formula_cells(file_id, sheet_name, formula_cells)

    return {
        "total_rows": nrows,
        "total_cols": len(columns),
        "columns": columns,
        "changes": [{"r": row, "c": col, "v": value}],
        "formula_cells": _formula_cells_view(formula_cells, dirty),
    }


# ---------------------------------------------------------------------------
# [6] Ekspor
#     Bila ada sheet mode 'rows': tulis xlsx baris-per-baris (xlsxwriter constant_memory),
#     baris dibaca bertahap dari database. pandas.to_excel + openpyxl untuk 50 ribu baris
#     lambat dan memakan memori besar. File tanpa sheet besar tetap lewat jalur lama.
# ---------------------------------------------------------------------------

def _export_streaming(file_id, sheet_names):
    import xlsxwriter

    output = io.BytesIO()
    wb = xlsxwriter.Workbook(output, {
        "constant_memory": True,       # baris yang sudah ditulis langsung dibuang dari memori
        "nan_inf_to_errors": True,
        "strings_to_urls": False,      # teks "http://..." tetap teks (batas 65 rb hyperlink/sheet)
    })
    try:
        for name in sheet_names:
            ws = wb.add_worksheet(name[:31])
            storage, _n = db.sheet_storage(file_id, name)
            if storage == "rows":
                rows = db.iter_rows(file_id, name)
            else:
                rows = db.get_sheet(file_id, name, version="working") or []
            for r, row in enumerate(rows):
                ws.write_row(r, 0, row)
    finally:
        wb.close()
    output.seek(0)
    return output


def export_working_copy(file_id):
    file_meta = db.get_file(file_id)
    sheet_names = db.list_sheet_names(file_id)
    output = None
    if db.has_rows_mode(file_id):
        try:
            output = _export_streaming(file_id, sheet_names)
        except ImportError:
            output = None  # xlsxwriter belum terpasang -> jalur lama di bawah (lebih lambat)
    if output is None:
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            for sheet_name in sheet_names:
                columns = _get_columns(file_id, sheet_name)
                records = db.get_sheet(file_id, sheet_name, version="working")
                df = records_to_df(records, columns)
                # header=False: grid ditulis apa adanya (baris 1 = baris 1), tanpa baris huruf A/B/C
                df.to_excel(writer, sheet_name=sheet_name[:31], index=False, header=False)
        output.seek(0)
    download_name = f"edited_{file_meta['filename']}"
    return send_file(
        output,
        as_attachment=True,
        download_name=download_name,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
