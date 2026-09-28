"""
Koneksi ke Neon Postgres (lewat integrasi Vercel Postgres/Neon).
Semua data sheet disimpan sebagai JSONB: 'original' (snapshot upload, read-only)
dan 'working' (versi yang boleh dimanipulasi user). File lokal user TIDAK PERNAH disentuh.
"""
import os
import json
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras

DATABASE_URL = (
    os.environ.get("DATABASE_URL")
    or os.environ.get("POSTGRES_URL")
    or os.environ.get("POSTGRES_PRISMA_URL")
)


def get_conn():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL / POSTGRES_URL belum di-set di environment variable")
    conn = psycopg2.connect(DATABASE_URL, sslmode="require")
    return conn


def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS files (
            id UUID PRIMARY KEY,
            session_id TEXT NOT NULL,
            filename TEXT NOT NULL,
            sheet_names JSONB NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'ready',
            uploaded_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        CREATE TABLE IF NOT EXISTS sheets (
            file_id UUID NOT NULL REFERENCES files(id) ON DELETE CASCADE,
            sheet_name TEXT NOT NULL,
            original_data JSONB NOT NULL,
            working_data JSONB NOT NULL,
            columns JSONB NOT NULL DEFAULT '[]',
            formula_columns JSONB NOT NULL DEFAULT '{}',
            formula_cells JSONB NOT NULL DEFAULT '{}',
            row_order_dirty BOOLEAN NOT NULL DEFAULT false,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (file_id, sheet_name)
        );

        ALTER TABLE sheets ADD COLUMN IF NOT EXISTS formula_columns JSONB NOT NULL DEFAULT '{}';
        ALTER TABLE sheets ADD COLUMN IF NOT EXISTS formula_cells JSONB NOT NULL DEFAULT '{}';
        ALTER TABLE sheets ADD COLUMN IF NOT EXISTS row_order_dirty BOOLEAN NOT NULL DEFAULT false;
        ALTER TABLE files ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'upload';

        CREATE TABLE IF NOT EXISTS chat_messages (
            id SERIAL PRIMARY KEY,
            session_id TEXT NOT NULL,
            file_id UUID NOT NULL REFERENCES files(id) ON DELETE CASCADE,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        CREATE INDEX IF NOT EXISTS idx_files_session ON files(session_id);
        CREATE INDEX IF NOT EXISTS idx_chat_file ON chat_messages(file_id, created_at);
        """
    )
    conn.commit()
    cur.close()
    conn.close()


# ---------- Files ----------

def create_file(file_id, session_id, filename, sheet_names, source="upload"):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO files (id, session_id, filename, sheet_names, source) VALUES (%s,%s,%s,%s,%s)",
        (file_id, session_id, filename, json.dumps(sheet_names), source),
    )
    conn.commit()
    cur.close()
    conn.close()


def update_file_sheets(file_id, sheet_names):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "UPDATE files SET sheet_names=%s WHERE id=%s",
        (json.dumps(sheet_names), file_id),
    )
    conn.commit()
    cur.close()
    conn.close()


def list_files(session_id):
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        "SELECT id, filename, sheet_names, status, source, uploaded_at FROM files "
        "WHERE session_id=%s ORDER BY uploaded_at DESC",
        (session_id,),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r, uploaded_at=r["uploaded_at"].isoformat()) for r in rows]


def get_file(file_id):
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM files WHERE id=%s", (file_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return dict(row) if row else None


def delete_file(session_id, file_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM files WHERE id=%s AND session_id=%s", (file_id, session_id))
    conn.commit()
    cur.close()
    conn.close()


# ---------- Sheets (original vs working copy) ----------

def save_sheet(file_id, sheet_name, records, columns, formula_columns=None, formula_cells=None):
    """Dipanggil sekali saat upload: original == working di awal."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO sheets (file_id, sheet_name, original_data, working_data, columns,
                            formula_columns, formula_cells, row_order_dirty)
        VALUES (%s,%s,%s,%s,%s,%s,%s,false)
        ON CONFLICT (file_id, sheet_name) DO UPDATE
        SET original_data=EXCLUDED.original_data,
            working_data=EXCLUDED.working_data,
            columns=EXCLUDED.columns,
            formula_columns=EXCLUDED.formula_columns,
            formula_cells=EXCLUDED.formula_cells,
            row_order_dirty=false
        """,
        (
            file_id, sheet_name, json.dumps(records), json.dumps(records),
            json.dumps(columns), json.dumps(formula_columns or {}),
            json.dumps(formula_cells or {}),
        ),
    )
    conn.commit()
    cur.close()
    conn.close()


def get_formula_columns(file_id, sheet_name):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT formula_columns FROM sheets WHERE file_id=%s AND sheet_name=%s",
        (file_id, sheet_name),
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row[0] if row else {}


def update_formula_columns(file_id, sheet_name, formula_columns):
    """Dipakai saat kolom di-rename/dihapus lewat manipulate, supaya info
    'kolom mana yang rumus' tetap sinkron dgn nama/kolom working_data terkini."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "UPDATE sheets SET formula_columns=%s WHERE file_id=%s AND sheet_name=%s",
        (json.dumps(formula_columns), file_id, sheet_name),
    )
    conn.commit()
    cur.close()
    conn.close()


def get_formula_cells(file_id, sheet_name):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT formula_cells FROM sheets WHERE file_id=%s AND sheet_name=%s",
        (file_id, sheet_name),
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    return (row[0] if row else None) or {}


def update_formula_cells(file_id, sheet_name, formula_cells):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "UPDATE sheets SET formula_cells=%s WHERE file_id=%s AND sheet_name=%s",
        (json.dumps(formula_cells), file_id, sheet_name),
    )
    conn.commit()
    cur.close()
    conn.close()


def set_row_order_dirty(file_id, sheet_name, dirty):
    """Tandai urutan/jumlah baris working_data sudah beda dari saat ingest
    (habis sort/filter/dedupe/drop_na) -> peta formula_cells per-sel tidak
    akurat lagi. Di-reset ke false lewat reset_working_data."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "UPDATE sheets SET row_order_dirty=%s WHERE file_id=%s AND sheet_name=%s",
        (bool(dirty), file_id, sheet_name),
    )
    conn.commit()
    cur.close()
    conn.close()


def get_formula_info(file_id, sheet_name):
    """Satu query buat preview: (formula_columns, formula_cells, row_order_dirty)."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT formula_columns, formula_cells, row_order_dirty FROM sheets "
        "WHERE file_id=%s AND sheet_name=%s",
        (file_id, sheet_name),
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row:
        return {}, {}, False
    return row[0] or {}, row[1] or {}, bool(row[2])


def get_sheet(file_id, sheet_name, version="working"):
    col = "working_data" if version == "working" else "original_data"
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(f"SELECT {col} FROM sheets WHERE file_id=%s AND sheet_name=%s", (file_id, sheet_name))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row[0] if row else None


def update_working_data(file_id, sheet_name, records, columns=None):
    conn = get_conn()
    cur = conn.cursor()
    if columns is not None:
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
    cur.close()
    conn.close()


def reset_working_data(file_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "UPDATE sheets SET working_data=original_data, row_order_dirty=false, updated_at=now() WHERE file_id=%s",
        (file_id,),
    )
    conn.commit()
    cur.close()
    conn.close()


def list_sheet_names(file_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT sheet_name FROM sheets WHERE file_id=%s", (file_id,))
    rows = [r[0] for r in cur.fetchall()]
    cur.close()
    conn.close()
    return rows


# ---------- Chat history ----------

def save_chat_message(session_id, file_id, role, content):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO chat_messages (session_id, file_id, role, content) VALUES (%s,%s,%s,%s)",
        (session_id, file_id, role, content),
    )
    conn.commit()
    cur.close()
    conn.close()


def get_chat_history(session_id, file_id, limit=50):
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        "SELECT role, content, created_at FROM chat_messages "
        "WHERE session_id=%s AND file_id=%s ORDER BY created_at ASC LIMIT %s",
        (session_id, file_id, limit),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r, created_at=r["created_at"].isoformat()) for r in rows]
