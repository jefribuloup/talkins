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


# Isi password_hash untuk akun Google. Bukan hash yang valid -> tidak pernah cocok dengan kata sandi
# apa pun; awalan "!" = akun terkunci untuk login kata sandi (konvensi Unix). Akun Google masuk
# hanya lewat tombol Google, kata sandinya dikelola Google. Kalau kelak user membuat kata sandi
# sendiri, nilai ini diganti hash asli.
GOOGLE_NO_PASSWORD = "!google-oauth"


# Migrasi kolom ringan yang dijalankan otomatis sekali per proses (idempoten).
# Tujuannya: setiap kali kolom baru ditambah di kode, database Neon menyusul sendiri
# tanpa harus membuka /api/_init-db manual. Kalau tabel belum ada (DB baru), dilewati
# diam-diam dan init_db() yang membuatnya.
_SCHEMA_MIGRATIONS = (
    "ALTER TABLE sheets ADD COLUMN IF NOT EXISTS formula_columns JSONB NOT NULL DEFAULT '{}'",
    "ALTER TABLE sheets ADD COLUMN IF NOT EXISTS formula_cells JSONB NOT NULL DEFAULT '{}'",
    "ALTER TABLE sheets ADD COLUMN IF NOT EXISTS row_order_dirty BOOLEAN NOT NULL DEFAULT false",
    "ALTER TABLE sheets ADD COLUMN IF NOT EXISTS column_letters JSONB NOT NULL DEFAULT '{}'",
    "ALTER TABLE sheets ADD COLUMN IF NOT EXISTS original_meta JSONB NOT NULL DEFAULT '{}'",
    "ALTER TABLE files ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'upload'",
    "ALTER TABLE files ADD COLUMN IF NOT EXISTS content_hash TEXT",
    "ALTER TABLE files ADD COLUMN IF NOT EXISTS source_key TEXT",
    "CREATE INDEX IF NOT EXISTS idx_files_session_hash ON files(session_id, content_hash)",
    "CREATE INDEX IF NOT EXISTS idx_files_session_srckey ON files(session_id, source_key)",
    """CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            google_sub TEXT NOT NULL UNIQUE,
            email TEXT NOT NULL,
            name TEXT,
            picture TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_login_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )""",
    "CREATE INDEX IF NOT EXISTS idx_users_email ON users(lower(email))",
    "ALTER TABLE users ALTER COLUMN google_sub DROP NOT NULL",
    "ALTER TABLE users ALTER COLUMN email DROP NOT NULL",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS username TEXT",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash TEXT",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS failed_attempts INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS locked_until TIMESTAMPTZ",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username ON users(username) WHERE username IS NOT NULL",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS provider TEXT",
    "UPDATE users SET provider = CASE WHEN password_hash IS NOT NULL THEN 'manual' ELSE 'google' END WHERE provider IS NULL",
    # Pembeda akun manual vs Google kini kolom `provider` (bukan isi password_hash)
    "DROP INDEX IF EXISTS idx_users_email_manual",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_manual_v2 ON users(lower(email)) WHERE provider = 'manual'",
    # Akun Google lama: isi password_hash dengan penanda "terkunci" (bukan hash asli, tidak bisa dipakai login)
    "UPDATE users SET password_hash = '" + GOOGLE_NO_PASSWORD + "' WHERE provider = 'google' AND password_hash IS NULL",
)
_schema_ready = False


def _ensure_schema(conn):
    global _schema_ready
    try:
        cur = conn.cursor()
        cur.execute("SET LOCAL lock_timeout = '3s'")
        for stmt in _SCHEMA_MIGRATIONS:
            cur.execute(stmt)
        conn.commit()
        cur.close()
        _schema_ready = True
    except Exception:
        # tabel belum ada / lock timeout -> coba lagi di koneksi berikutnya
        conn.rollback()


def get_conn():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL / POSTGRES_URL belum di-set di environment variable")
    conn = psycopg2.connect(DATABASE_URL, sslmode="require")
    if not _schema_ready:
        _ensure_schema(conn)
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
            column_letters JSONB NOT NULL DEFAULT '{}',
            original_meta JSONB NOT NULL DEFAULT '{}',
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (file_id, sheet_name)
        );

        ALTER TABLE sheets ADD COLUMN IF NOT EXISTS formula_columns JSONB NOT NULL DEFAULT '{}';
        ALTER TABLE sheets ADD COLUMN IF NOT EXISTS formula_cells JSONB NOT NULL DEFAULT '{}';
        ALTER TABLE sheets ADD COLUMN IF NOT EXISTS row_order_dirty BOOLEAN NOT NULL DEFAULT false;
        ALTER TABLE sheets ADD COLUMN IF NOT EXISTS column_letters JSONB NOT NULL DEFAULT '{}';
        ALTER TABLE sheets ADD COLUMN IF NOT EXISTS original_meta JSONB NOT NULL DEFAULT '{}';
        ALTER TABLE files ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'upload';
        ALTER TABLE files ADD COLUMN IF NOT EXISTS content_hash TEXT;
        ALTER TABLE files ADD COLUMN IF NOT EXISTS source_key TEXT;

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
        CREATE INDEX IF NOT EXISTS idx_files_session_hash ON files(session_id, content_hash);
        CREATE INDEX IF NOT EXISTS idx_files_session_srckey ON files(session_id, source_key);

        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            google_sub TEXT NOT NULL UNIQUE,
            email TEXT NOT NULL,
            name TEXT,
            picture TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_login_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS idx_users_email ON users(lower(email));
        ALTER TABLE users ALTER COLUMN google_sub DROP NOT NULL;
        ALTER TABLE users ALTER COLUMN email DROP NOT NULL;
        ALTER TABLE users ADD COLUMN IF NOT EXISTS username TEXT;
        ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash TEXT;
        ALTER TABLE users ADD COLUMN IF NOT EXISTS failed_attempts INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE users ADD COLUMN IF NOT EXISTS locked_until TIMESTAMPTZ;
        CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username ON users(username) WHERE username IS NOT NULL;
        ALTER TABLE users ADD COLUMN IF NOT EXISTS provider TEXT;
        DROP INDEX IF EXISTS idx_users_email_manual;
        CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_manual_v2 ON users(lower(email)) WHERE provider = 'manual';
        """
    )
    conn.commit()
    cur.close()
    conn.close()


# ---------- Users (login Google) ----------

def username_from_email(email):
    """Username dari bagian sebelum '@' di email (mis. budi.santoso+kerja@gmail.com -> budi.santoso).
    Disesuaikan dgn aturan username aplikasi: huruf kecil/angka/titik/garis bawah, 3-30 karakter."""
    import re as _re
    local = (email or "").split("@")[0].split("+")[0].lower()
    base = _re.sub(r"[^a-z0-9_.]", "", local).strip("._")
    if len(base) < 3:
        base = "user" + base
    return base[:24]  # sisakan ruang untuk angka pembeda bila bentrok


def assign_username(user_id, email):
    """Isi username akun (yang masih kosong) dari email. Bila sudah dipakai orang lain,
    ditambah angka: budi, budi2, budi3, ... Aman dari balapan karena dijaga indeks unik di database."""
    import secrets as _secrets
    base = username_from_email(email)
    candidates = [base] + [f"{base}{n}" for n in range(2, 60)] + [f"{base}{_secrets.token_hex(3)}"]
    conn = get_conn()
    cur = conn.cursor()
    try:
        for cand in candidates:
            try:
                cur.execute(
                    "UPDATE users SET username=%s WHERE id=%s AND username IS NULL RETURNING username",
                    (cand, user_id),
                )
                row = cur.fetchone()
                conn.commit()
                if row:
                    return row[0]
                # tidak ada baris yang berubah: username sudah terisi (mis. oleh login lain bersamaan)
                cur.execute("SELECT username FROM users WHERE id=%s", (user_id,))
                got = cur.fetchone()
                return got[0] if got else None
            except Exception as e:
                conn.rollback()
                if getattr(e, "pgcode", None) != "23505":  # 23505 = unique_violation -> coba kandidat berikutnya
                    raise
        return None
    finally:
        cur.close()
        conn.close()


def upsert_user(google_sub, email, name, picture):
    """Daftar lewat Google bila akun belum ada, atau perbarui profil bila sudah ada (masuk).
    Identitas dikunci ke `sub` Google, bukan email, karena email bisa berubah.

    Akun Google: provider='google', username otomatis dari email, dan password_hash berisi penanda
    terkunci (GOOGLE_NO_PASSWORD), bukan hash asli: kata sandinya dikelola Google, user selalu
    masuk lewat tombol Google."""
    import uuid as _uuid
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        """
        INSERT INTO users (id, google_sub, email, name, picture, provider, password_hash)
        VALUES (%s,%s,%s,%s,%s,'google',%s)
        ON CONFLICT (google_sub) DO UPDATE
           SET email=EXCLUDED.email, name=EXCLUDED.name, picture=EXCLUDED.picture,
               provider=COALESCE(users.provider, 'google'),
               password_hash=COALESCE(users.password_hash, EXCLUDED.password_hash),
               last_login_at=now()
        RETURNING id, username, email, name, picture, (xmax = 0) AS is_new
        """,
        (str(_uuid.uuid4()), google_sub, email, name, picture, GOOGLE_NO_PASSWORD),
    )
    row = dict(cur.fetchone())
    conn.commit()
    cur.close()
    conn.close()
    if not row.get("username"):  # akun baru, atau akun Google lama yang belum punya username
        row["username"] = assign_username(row["id"], email)
    return row


def get_user(user_id):
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT id, username, email, name, picture FROM users WHERE id=%s", (user_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return dict(row) if row else None


def find_register_conflict(username, email):
    """Cek bentrok sebelum daftar manual. Mengembalikan kode error atau None:
    username_taken  -> username sudah dipakai
    email_taken     -> email sudah dipakai akun manual lain
    email_google    -> email itu sudah terdaftar lewat Google (arahkan user ke tombol Google)"""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM users WHERE username=%s", (username,))
    if cur.fetchone():
        code = "username_taken"
    else:
        cur.execute(
            "SELECT provider = 'manual' FROM users WHERE lower(email)=%s "
            "ORDER BY (provider = 'manual') DESC NULLS LAST LIMIT 1",
            (email,),
        )
        row = cur.fetchone()
        code = None if not row else ("email_taken" if row[0] else "email_google")
    cur.close()
    conn.close()
    return code


def create_manual_user(username, name, email, password_hash):
    """Daftar manual (username + email + kata sandi). Mengembalikan user, atau None bila bentrok
    (mis. dua orang mendaftar bersamaan dengan username/email yang sama)."""
    import uuid as _uuid
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        """
        INSERT INTO users (id, username, email, name, password_hash, provider)
        VALUES (%s,%s,%s,%s,%s,'manual')
        ON CONFLICT DO NOTHING
        RETURNING id, username, email, name, picture
        """,
        (str(_uuid.uuid4()), username, email, name, password_hash),
    )
    row = cur.fetchone()
    conn.commit()
    cur.close()
    conn.close()
    return dict(row) if row else None


def get_user_for_login(identifier):
    """Cari akun manual untuk login berdasarkan username ATAU email (identifier berisi '@' = email).
    Hanya akun manual yang punya kata sandi asli; akun Google tidak pernah cocok di sini.
    Menyertakan status kunci akun."""
    by_email = "@" in identifier
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        """
        SELECT id, username, email, name, picture, password_hash,
               (locked_until IS NOT NULL AND locked_until > now()) AS is_locked,
               CEIL(GREATEST(EXTRACT(EPOCH FROM (locked_until - now())), 0) / 60.0)::int AS lock_minutes
        FROM users
        WHERE provider = 'manual' AND password_hash IS NOT NULL AND password_hash NOT LIKE '!%%'
          AND """ + ("lower(email)=%s" if by_email else "username=%s"),
        (identifier,),
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    return dict(row) if row else None


def record_login_failure(user_id, max_attempts, lock_minutes):
    """Catat gagal login; setelah max_attempts gagal berturut-turut, akun dikunci sementara."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE users SET
          locked_until = CASE WHEN failed_attempts + 1 >= %s
                              THEN now() + make_interval(mins => %s) ELSE locked_until END,
          failed_attempts = CASE WHEN failed_attempts + 1 >= %s THEN 0 ELSE failed_attempts + 1 END
        WHERE id=%s
        """,
        (max_attempts, lock_minutes, max_attempts, user_id),
    )
    conn.commit()
    cur.close()
    conn.close()


def reset_login_failures(user_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "UPDATE users SET failed_attempts=0, locked_until=NULL, last_login_at=now() WHERE id=%s",
        (user_id,),
    )
    conn.commit()
    cur.close()
    conn.close()


def adopt_session(old_session_id, new_session_id):
    """Pindahkan file & riwayat chat milik sesi tamu ke akun yang baru login."""
    if not old_session_id or old_session_id == new_session_id:
        return
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE files SET session_id=%s WHERE session_id=%s", (new_session_id, old_session_id))
    cur.execute("UPDATE chat_messages SET session_id=%s WHERE session_id=%s", (new_session_id, old_session_id))
    conn.commit()
    cur.close()
    conn.close()


# ---------- Files ----------

def create_file(file_id, session_id, filename, sheet_names, source="upload",
                content_hash=None, source_key=None):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO files (id, session_id, filename, sheet_names, source, content_hash, source_key) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s)",
        (file_id, session_id, filename, json.dumps(sheet_names), source, content_hash, source_key),
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


def find_duplicate(session_id, content_hash=None, source_key=None):
    """Cari file di sesi ini yang sama dgn yang mau diunggah.
    Cocok kalau isi file identik (content_hash) atau link sumbernya sama (source_key).
    Mengembalikan baris file terbaru yang cocok, atau None."""
    if not content_hash and not source_key:
        return None
    conditions, params = [], [session_id]
    if content_hash:
        conditions.append("content_hash=%s")
        params.append(content_hash)
    if source_key:
        conditions.append("source_key=%s")
        params.append(source_key)
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        "SELECT id, filename, sheet_names, source, uploaded_at FROM files "
        "WHERE session_id=%s AND status<>'deleted' AND (" + " OR ".join(conditions) + ") "
        "ORDER BY uploaded_at DESC LIMIT 1",
        params,
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row:
        return None
    return dict(row, uploaded_at=row["uploaded_at"].isoformat())


def list_files(session_id):
    """Daftar file sesi ini, SATU baris per file unik (yang terbaru).
    Kunci unik: link sumber > hash isi file > nama file (untuk file lama yang belum punya hash)."""
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        """
        SELECT id, filename, sheet_names, status, source, uploaded_at FROM (
            SELECT DISTINCT ON (COALESCE(source_key, content_hash, 'name:' || lower(filename)))
                   id, filename, sheet_names, status, source, uploaded_at
            FROM files
            WHERE session_id=%s
            ORDER BY COALESCE(source_key, content_hash, 'name:' || lower(filename)), uploaded_at DESC
        ) uniq
        ORDER BY uploaded_at DESC
        """,
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
    """Hapus file beserta salinan duplikatnya (kunci sama: link sumber / hash isi / nama file
    lama) di sesi ini. Tanpa ini, duplikat lama yang tersembunyi akan muncul lagi di daftar
    setelah file yang tampil dihapus, seolah-olah tombol Hapus tidak bekerja."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        DELETE FROM files
        WHERE session_id=%s AND COALESCE(source_key, content_hash, 'name:' || lower(filename)) = (
            SELECT COALESCE(source_key, content_hash, 'name:' || lower(filename))
            FROM files WHERE id=%s AND session_id=%s
        )
        """,
        (session_id, file_id, session_id),
    )
    cur.execute("DELETE FROM files WHERE id=%s AND session_id=%s", (file_id, session_id))
    conn.commit()
    cur.close()
    conn.close()


# ---------- Sheets (original vs working copy) ----------

def save_sheet(file_id, sheet_name, records, columns, formula_columns=None, formula_cells=None,
               column_letters=None):
    """Dipanggil sekali saat upload: original == working di awal.

    Data disimpan sebagai grid mentah tanpa header: kolom = huruf Excel (A, B, ...), baris 1
    = baris 1 sheet asli. original_meta menyimpan snapshot metadata kolom saat upload supaya
    'Reset ke asli' ikut mengembalikan daftar kolom & info rumus (bukan hanya datanya)."""
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
    cur.execute(
        """
        INSERT INTO sheets (file_id, sheet_name, original_data, working_data, columns,
                            formula_columns, formula_cells, row_order_dirty,
                            column_letters, original_meta)
        VALUES (%s,%s,%s,%s,%s,%s,%s,false,%s,%s)
        ON CONFLICT (file_id, sheet_name) DO UPDATE
        SET original_data=EXCLUDED.original_data,
            working_data=EXCLUDED.working_data,
            columns=EXCLUDED.columns,
            formula_columns=EXCLUDED.formula_columns,
            formula_cells=EXCLUDED.formula_cells,
            row_order_dirty=false,
            column_letters=EXCLUDED.column_letters,
            original_meta=EXCLUDED.original_meta
        """,
        (
            file_id, sheet_name, json.dumps(records), json.dumps(records),
            json.dumps(columns), json.dumps(formula_columns), json.dumps(formula_cells),
            json.dumps(column_letters), json.dumps(original_meta),
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


def get_sheet_head(file_id, sheet_name, n=3):
    """n baris pertama working_data saja (murah, tanpa memuat seluruh sheet)."""
    conn = get_conn()
    cur = conn.cursor()
    exprs = ", ".join(f"working_data->{i}" for i in range(int(n)))
    cur.execute(
        f"SELECT {exprs} FROM sheets WHERE file_id=%s AND sheet_name=%s",
        (file_id, sheet_name),
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    return [r for r in (row or []) if r is not None]


def get_formula_info(file_id, sheet_name):
    """Satu query buat preview: (formula_columns, formula_cells, row_order_dirty, column_letters)."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT formula_columns, formula_cells, row_order_dirty, column_letters FROM sheets "
        "WHERE file_id=%s AND sheet_name=%s",
        (file_id, sheet_name),
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row:
        return {}, {}, False, {}
    return row[0] or {}, row[1] or {}, bool(row[2]), row[3] or {}


def get_column_letters(file_id, sheet_name):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT column_letters FROM sheets WHERE file_id=%s AND sheet_name=%s",
        (file_id, sheet_name),
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    return (row[0] if row else None) or {}


def update_column_letters(file_id, sheet_name, column_letters):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "UPDATE sheets SET column_letters=%s WHERE file_id=%s AND sheet_name=%s",
        (json.dumps(column_letters), file_id, sheet_name),
    )
    conn.commit()
    cur.close()
    conn.close()


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
        """
        UPDATE sheets SET
            working_data    = original_data,
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
