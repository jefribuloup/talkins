"""
PATCH untuk lib/db.py  --  "MODE BARIS" untuk sheet besar
==========================================================

Ide: sheet kecil tetap disimpan seperti sekarang (satu blob JSONB di tabel `sheets`).
Hanya sheet BESAR (ambang ada di excel_engine, lihat patch_excel_engine_large_mode.py)
yang disimpan per baris di tabel baru `sheet_rows`, sehingga:
  - preview cukup mengambil ~200 baris (LIMIT), bukan memuat + mem-parse 50 ribu baris
    tiap kali user scroll,
  - insert data besar lewat COPY (satu kali jalan), bukan satu blob JSON raksasa.

Sheet lama TIDAK berubah (kolom `storage` default 'json'). Tidak perlu migrasi manual:
migrasi di bawah dijalankan otomatis oleh _ensure_schema().

CARA PASANG (urut):
  [1] import  -> tempel di bagian atas db.py (bersama import yang sudah ada)
  [2] migrasi -> tempel TEPAT SETELAH tuple _SCHEMA_MIGRATIONS (sebelum `_schema_ready = False`)
  [3] fungsi baru -> tempel di bagian "Sheets" (mis. setelah save_sheet)
  [4] fungsi pengganti -> HAPUS versi lama get_sheet, get_sheet_head, update_working_data,
      reset_working_data lalu tempel versi di bawah.
"""

# ---------------------------------------------------------------------------
# [1] IMPORT (cukup yang belum ada di db.py)
# ---------------------------------------------------------------------------
import csv
import io
import json

# ---------------------------------------------------------------------------
# [2] MIGRASI SKEMA
# ---------------------------------------------------------------------------
_LARGE_MODE_MIGRATIONS = (
    # 'json' = cara lama (blob di tabel sheets), 'rows' = per baris di tabel sheet_rows
    "ALTER TABLE sheets ADD COLUMN IF NOT EXISTS storage TEXT NOT NULL DEFAULT 'json'",
    "ALTER TABLE sheets ADD COLUMN IF NOT EXISTS original_rows INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE sheets ADD COLUMN IF NOT EXISTS working_rows INTEGER NOT NULL DEFAULT 0",
    """CREATE TABLE IF NOT EXISTS sheet_rows (
            file_id UUID NOT NULL,
            sheet_name TEXT NOT NULL,
            version SMALLINT NOT NULL,   -- 0 = original (read-only), 1 = working
            row_idx INTEGER NOT NULL,    -- 0-based, berurutan tanpa lubang
            data JSONB NOT NULL,         -- satu baris grid: [nilai A, nilai B, ...]
            PRIMARY KEY (file_id, sheet_name, version, row_idx),
            FOREIGN KEY (file_id, sheet_name) REFERENCES sheets(file_id, sheet_name) ON DELETE CASCADE
        )""",
)
_SCHEMA_MIGRATIONS = _SCHEMA_MIGRATIONS + _LARGE_MODE_MIGRATIONS


# ---------------------------------------------------------------------------
# [3] FUNGSI BARU
# ---------------------------------------------------------------------------
ROW_COPY_BATCH = 20000  # baris per satu COPY (menjaga memori tetap kecil)


def _copy_rows(cur, file_id, sheet_name, version, records):
    """Masukkan `records` (list baris) ke sheet_rows lewat COPY. Jauh lebih cepat daripada INSERT
    per baris. Dijalankan di transaksi milik pemanggil (commit/rollback di pemanggil)."""
    fid = str(file_id)
    for start in range(0, len(records), ROW_COPY_BATCH):
        buf = io.StringIO()
        w = csv.writer(buf, quoting=csv.QUOTE_ALL, lineterminator="\n")
        for i, rec in enumerate(records[start:start + ROW_COPY_BATCH], start=start):
            w.writerow((fid, sheet_name, version, i, json.dumps(rec)))
        buf.seek(0)
        cur.copy_expert(
            "COPY sheet_rows (file_id, sheet_name, version, row_idx, data) "
            "FROM STDIN WITH (FORMAT csv)",
            buf,
        )


def save_sheet_rows(file_id, sheet_name, records, columns, formula_columns=None,
                    formula_cells=None, column_letters=None):
    """Padanan save_sheet() untuk sheet besar: baris disimpan di sheet_rows (original + working),
    metadata (kolom, rumus, dll) tetap di tabel sheets. Semua dalam satu transaksi."""
    formula_columns = formula_columns or {}
    formula_cells = formula_cells or {}
    column_letters = column_letters or {}
    original_meta = {
        "columns": columns,
        "formula_columns": formula_columns,
        "formula_cells": formula_cells,
        "column_letters": column_letters,
    }
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO sheets (file_id, sheet_name, original_data, working_data, columns,
                                formula_columns, formula_cells, row_order_dirty,
                                column_letters, original_meta, storage,
                                original_rows, working_rows)
            VALUES (%s,%s,'[]','[]',%s,%s,%s,false,%s,%s,'rows',%s,%s)
            """,
            (
                file_id, sheet_name, json.dumps(columns), json.dumps(formula_columns),
                json.dumps(formula_cells), json.dumps(column_letters),
                json.dumps(original_meta), len(records), len(records),
            ),
        )
        _copy_rows(cur, file_id, sheet_name, 0, records)
        # working = salinan original, dibuat DI DALAM database (tanpa mengirim data lagi)
        cur.execute(
            "INSERT INTO sheet_rows (file_id, sheet_name, version, row_idx, data) "
            "SELECT file_id, sheet_name, 1, row_idx, data FROM sheet_rows "
            "WHERE file_id=%s AND sheet_name=%s AND version=0",
            (file_id, sheet_name),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def sheet_storage(file_id, sheet_name):
    """-> ('json' | 'rows', jumlah_baris_working_untuk_mode_rows). (None, 0) bila sheet tidak ada."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT storage, working_rows FROM sheets WHERE file_id=%s AND sheet_name=%s",
        (file_id, sheet_name),
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    return (row[0], row[1]) if row else (None, 0)


def has_rows_mode(file_id):
    """True bila ada minimal satu sheet di file ini yang disimpan dalam mode baris."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT EXISTS (SELECT 1 FROM sheets WHERE file_id=%s AND storage='rows')", (file_id,))
    found = cur.fetchone()[0]
    cur.close()
    conn.close()
    return bool(found)


def get_preview_data(file_id, sheet_name, start, count):
    """Satu koneksi untuk preview. Mengembalikan None bila sheet tidak ada, selain itu:
       {"storage", "total_rows", "row_start", "rows", "records"}
    `records` = seluruh grid (hanya mode 'json', dipakai utk hitung lebar kolom); None utk 'rows'.
    Mode 'rows' hanya mengambil `count` baris mulai `start` -> cepat berapa pun ukuran sheet."""
    count = int(count)
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT storage, working_rows, working_data FROM sheets WHERE file_id=%s AND sheet_name=%s",
            (file_id, sheet_name),
        )
        row = cur.fetchone()
        if not row:
            return None
        storage, nrows, data = row
        if storage == "rows":
            start = max(0, min(int(start), nrows))
            cur.execute(
                "SELECT data FROM sheet_rows WHERE file_id=%s AND sheet_name=%s AND version=1 "
                "AND row_idx >= %s AND row_idx < %s ORDER BY row_idx",
                (file_id, sheet_name, start, start + count),
            )
            return {"storage": "rows", "total_rows": nrows, "row_start": start,
                    "rows": [r[0] for r in cur.fetchall()], "records": None}
        nrows = len(data)
        start = max(0, min(int(start), nrows))
        return {"storage": "json", "total_rows": nrows, "row_start": start,
                "rows": data[start:start + count], "records": data}
    finally:
        cur.close()
        conn.close()


def get_rows_slice(file_id, sheet_name, start, count, version="working"):
    """Potongan baris [start, start+count) dari sheet mode 'rows'."""
    ver = 1 if version == "working" else 0
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT data FROM sheet_rows WHERE file_id=%s AND sheet_name=%s AND version=%s "
        "AND row_idx >= %s AND row_idx < %s ORDER BY row_idx",
        (file_id, sheet_name, ver, int(start), int(start) + int(count)),
    )
    rows = [r[0] for r in cur.fetchall()]
    cur.close()
    conn.close()
    return rows


def iter_rows(file_id, sheet_name, version="working", batch=5000):
    """Generator: baca baris sheet mode 'rows' bertahap lewat SATU koneksi (untuk ekspor),
    tanpa memuat seluruh sheet ke memori."""
    ver = 1 if version == "working" else 0
    conn = get_conn()
    cur = conn.cursor()
    try:
        start = 0
        while True:
            cur.execute(
                "SELECT data FROM sheet_rows WHERE file_id=%s AND sheet_name=%s AND version=%s "
                "AND row_idx >= %s AND row_idx < %s ORDER BY row_idx",
                (file_id, sheet_name, ver, start, start + batch),
            )
            chunk = cur.fetchall()
            if not chunk:
                break
            for (d,) in chunk:
                yield d
            start += batch
    finally:
        cur.close()
        conn.close()


def set_cell_value(file_id, sheet_name, row, col, value):
    """Ubah SATU sel di working copy mode 'rows' tanpa memuat/menulis ulang seluruh sheet."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "UPDATE sheet_rows SET data = jsonb_set(data, ARRAY[%s::text], %s::jsonb) "
        "WHERE file_id=%s AND sheet_name=%s AND version=1 AND row_idx=%s",
        (int(col), json.dumps(value), file_id, sheet_name, int(row)),
    )
    cur.execute(
        "UPDATE sheets SET updated_at=now() WHERE file_id=%s AND sheet_name=%s",
        (file_id, sheet_name),
    )
    conn.commit()
    cur.close()
    conn.close()


# ---------------------------------------------------------------------------
# [4] FUNGSI PENGGANTI (hapus versi lama di db.py, pakai yang ini)
#     Perilaku untuk sheet mode 'json' TIDAK berubah.
#     Untuk sheet mode 'rows', fungsi-fungsi ini membaca/menulis tabel sheet_rows, sehingga
#     kode lain (apply_operation, set_cell, get_recap, chat, dll) yang memanggil
#     db.get_sheet / db.update_working_data tetap jalan tanpa diubah.
# ---------------------------------------------------------------------------

def get_sheet(file_id, sheet_name, version="working"):
    col = "working_data" if version == "working" else "original_data"
    ver = 1 if version == "working" else 0
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            f"SELECT storage, {col} FROM sheets WHERE file_id=%s AND sheet_name=%s",
            (file_id, sheet_name),
        )
        row = cur.fetchone()
        if not row:
            return None
        if row[0] == "rows":
            cur.execute(
                "SELECT data FROM sheet_rows WHERE file_id=%s AND sheet_name=%s AND version=%s "
                "ORDER BY row_idx",
                (file_id, sheet_name, ver),
            )
            return [r[0] for r in cur.fetchall()]
        return row[1]
    finally:
        cur.close()
        conn.close()


def get_sheet_head(file_id, sheet_name, n=3):
    """n baris pertama working_data saja (murah, tanpa memuat seluruh sheet)."""
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT storage FROM sheets WHERE file_id=%s AND sheet_name=%s", (file_id, sheet_name))
        st = cur.fetchone()
        if st and st[0] == "rows":
            cur.execute(
                "SELECT data FROM sheet_rows WHERE file_id=%s AND sheet_name=%s AND version=1 "
                "AND row_idx < %s ORDER BY row_idx",
                (file_id, sheet_name, int(n)),
            )
            return [r[0] for r in cur.fetchall()]
        exprs = ", ".join(f"working_data->{i}" for i in range(int(n)))
        cur.execute(
            f"SELECT {exprs} FROM sheets WHERE file_id=%s AND sheet_name=%s",
            (file_id, sheet_name),
        )
        row = cur.fetchone()
        return [r for r in (row or []) if r is not None]
    finally:
        cur.close()
        conn.close()


def update_working_data(file_id, sheet_name, records, columns=None):
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT storage FROM sheets WHERE file_id=%s AND sheet_name=%s", (file_id, sheet_name))
        st = cur.fetchone()
        if st and st[0] == "rows":
            # tulis ulang baris working (atomik: DELETE + COPY + update metadata satu transaksi)
            cur.execute(
                "DELETE FROM sheet_rows WHERE file_id=%s AND sheet_name=%s AND version=1",
                (file_id, sheet_name),
            )
            _copy_rows(cur, file_id, sheet_name, 1, records)
            sets, params = "working_rows=%s, updated_at=now()", [len(records)]
            if columns is not None:
                sets += ", columns=%s"
                params.append(json.dumps(columns))
            cur.execute(
                f"UPDATE sheets SET {sets} WHERE file_id=%s AND sheet_name=%s",
                params + [file_id, sheet_name],
            )
        elif columns is not None:
            cur.execute(
                "UPDATE sheets SET working_data=%s, columns=%s, updated_at=now() "
                "WHERE file_id=%s AND sheet_name=%s",
                (json.dumps(records), json.dumps(columns), file_id, sheet_name),
            )
        else:
            cur.execute(
                "UPDATE sheets SET working_data=%s, updated_at=now() WHERE file_id=%s AND sheet_name=%s",
                (json.dumps(records), file_id, sheet_name),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def reset_working_data(file_id):
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            UPDATE sheets SET
                working_data    = original_data,
                working_rows    = original_rows,
                columns         = COALESCE(original_meta->'columns', columns),
                formula_columns = COALESCE(original_meta->'formula_columns', formula_columns),
                formula_cells   = COALESCE(original_meta->'formula_cells', formula_cells),
                column_letters  = COALESCE(original_meta->'column_letters', column_letters),
                row_order_dirty = false,
                updated_at      = now()
            WHERE file_id=%s
            """,
            (file_id,),
        )
        # sheet mode 'rows': salin ulang original -> working langsung di dalam database
        cur.execute(
            "DELETE FROM sheet_rows r USING sheets s "
            "WHERE s.file_id=%s AND s.storage='rows' AND r.file_id=s.file_id "
            "AND r.sheet_name=s.sheet_name AND r.version=1",
            (file_id,),
        )
        cur.execute(
            "INSERT INTO sheet_rows (file_id, sheet_name, version, row_idx, data) "
            "SELECT r.file_id, r.sheet_name, 1, r.row_idx, r.data FROM sheet_rows r "
            "JOIN sheets s ON s.file_id=r.file_id AND s.sheet_name=r.sheet_name "
            "WHERE s.file_id=%s AND s.storage='rows' AND r.version=0",
            (file_id,),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()
