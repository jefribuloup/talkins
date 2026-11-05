"""
Entry point Flask untuk Vercel (@vercel/python).
Semua request masuk lewat sini (lihat vercel.json).
Struktur modular: logic berat ada di /lib, file ini cuma routing+orkestrasi
-> supaya gampang porting ke Next.js API routes nanti (tiap route = 1 handler mandiri).
"""
import os
import re
import secrets
import sys
import uuid

import time

from flask import Flask, request, jsonify, render_template, session, Response, abort
from markupsafe import escape
from werkzeug.exceptions import HTTPException

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from lib import db
from lib import excel_engine
from lib import chat_engine
from lib import formula_engine
from lib import slide_engine
from lib import auth
from lib import guard

app = Flask(
    __name__,
    template_folder="../templates",
    static_folder="../static",
)


def _load_secret_key():
    """SECRET_KEY menandatangani cookie sesi. Di produksi (Vercel) wajib diisi nilai acak yang kuat:
    kalau kosong / masih bawaan / terlalu pendek, aplikasi SENGAJA gagal start (fail-fast) daripada
    jalan dengan kunci yang bisa ditebak. Di komputer lokal kunci bawaan dipakai untuk development."""
    key = os.environ.get("SECRET_KEY", "")
    prod = bool(os.environ.get("VERCEL") or os.environ.get("VERCEL_ENV")
                or os.environ.get("FLASK_ENV", "").lower() == "production")
    if not key or key == auth.DEFAULT_SECRET:
        if prod:
            raise RuntimeError(
                "SECRET_KEY belum diisi (atau masih nilai bawaan). Isi env SECRET_KEY di Vercel dengan "
                "string acak minimal 32 karakter, mis. hasil: python -c \"import secrets; print(secrets.token_hex(32))\"")
        return auth.DEFAULT_SECRET
    if prod and len(key) < 16:
        raise RuntimeError("SECRET_KEY terlalu pendek (minimal 16 karakter, disarankan 32+).")
    return key


app.secret_key = _load_secret_key()

MAX_UPLOAD_MB = 20
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024
# Di Vercel, body request serverless dibatasi ±4,5 MB (platform menolak sebelum sampai ke Flask),
# jadi batas yang dikomunikasikan ke user di sana 4 MB.
UPLOAD_LIMIT_MB = 4 if os.environ.get("VERCEL") else MAX_UPLOAD_MB

# Saran rumus untuk sel yang dipilih di tab Rumus: POST /api/formula-suggest
formula_engine.register_routes(app)

# Login & daftar via Google OAuth: /login, /auth/google, /auth/google/callback, /auth/logout, /api/me
auth.register_routes(app)


@app.before_request
def ensure_session():
    if "session_id" not in session:
        session["session_id"] = str(uuid.uuid4())


# ---------- Header keamanan ----------
# CSP_MODE: "report-only" (default; hanya melaporkan pelanggaran di Console browser, tidak memblokir),
# "enforce" (memblokir), atau "off". Disarankan: biarkan report-only dulu, buka semua halaman
# (login, dashboard, workspace) dengan DevTools Console terbuka, lalu ubah ke "enforce" bila bersih.
# 'unsafe-inline' dibutuhkan karena ada skrip inline (login/beranda) yang dijalankan router SPA
# dan atribut style di HTML hasil render JS.
CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self' 'unsafe-inline' https://accounts.google.com/gsi/client",
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://accounts.google.com/gsi/style",
    "font-src 'self' https://fonts.gstatic.com",
    "img-src 'self' data: blob: https://*.googleusercontent.com",
    "connect-src 'self' https://www.googleapis.com https://accounts.google.com/gsi/",
    "frame-src https://accounts.google.com/gsi/",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self' https://accounts.google.com",
    "frame-ancestors 'none'",
])


@app.after_request
def add_security_headers(resp):
    h = resp.headers
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("X-Frame-Options", "DENY")
    h.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=(), usb=()")
    h.setdefault("Cross-Origin-Opener-Policy", "same-origin-allow-popups")  # tetap kompatibel dengan popup Google
    if request.is_secure or request.headers.get("X-Forwarded-Proto", "").split(",")[0].strip() == "https":
        h.setdefault("Strict-Transport-Security", "max-age=31536000")
    mode = os.environ.get("CSP_MODE", "report-only").strip().lower()
    if mode == "enforce":
        h.setdefault("Content-Security-Policy", CSP)
    elif mode != "off":
        h.setdefault("Content-Security-Policy-Report-Only", CSP)
    if request.path.startswith("/api/"):
        h.setdefault("Cache-Control", "no-store")  # data spreadsheet tidak disimpan di cache browser/proxy
    return resp


# ---------- Kepemilikan file ----------
# Semua route berparameter <file_id> (halaman workspace & seluruh /api/files/<id>/...) hanya
# boleh diakses pemilik file: session["session_id"] harus sama dengan files.session_id
# (uuid tamu, atau "user:<id>" setelah login). Tidak ada vs milik orang lain dijawab sama (404)
# supaya keberadaan file tidak bisa ditebak. DELETE sudah dibatasi pemilik di SQL-nya sendiri.
_OWNER_OK = {}            # (file_id, session_id) -> waktu kedaluwarsa; hanya hasil "boleh" yang di-cache
_OWNER_OK_TTL = 30        # detik; menghemat 1 query DB di setiap scroll/preview
_OWNER_OK_MAX = 2000


def _forget_owner(file_id):
    for k in [k for k in _OWNER_OK if k[0] == str(file_id)]:
        _OWNER_OK.pop(k, None)


@app.before_request
def enforce_file_owner():
    file_id = (request.view_args or {}).get("file_id")
    if not file_id or request.method == "DELETE":
        return None
    sid = session.get("session_id")
    key = (str(file_id), sid)
    now = time.time()
    if _OWNER_OK.get(key, 0) > now:
        return None
    owner = db.get_file_owner(file_id)
    if owner is None or owner != sid:
        abort(404)
    if len(_OWNER_OK) >= _OWNER_OK_MAX:
        _OWNER_OK.clear()
    _OWNER_OK[key] = now + _OWNER_OK_TTL
    return None


# ---------- Pesan error ramah (bukan teks teknis) ----------

GENERIC_ERROR = "Maaf, terjadi kendala di server. Coba lagi sebentar lagi. Kalau masih berulang, hubungi pengembang."

FRIENDLY_HTTP = {
    400: "Permintaan belum bisa diproses. Periksa isiannya lalu coba lagi.",
    401: "Sesi kamu sudah habis. Silakan masuk lagi.",
    403: "Kamu tidak punya akses ke halaman atau data ini.",
    404: "Halaman atau data yang kamu cari tidak ditemukan. Mungkin sudah dihapus atau alamatnya salah.",
    405: "Aksi ini tidak tersedia di alamat tersebut.",
    413: f"File terlalu besar. Kecilkan dulu ukurannya (maksimal sekitar {UPLOAD_LIMIT_MB} MB) atau unggah lewat link.",
    415: "Format data yang dikirim tidak dikenali. Muat ulang halaman lalu coba lagi.",
    429: "Terlalu banyak permintaan dalam waktu singkat. Tunggu sebentar lalu coba lagi.",
}


@app.context_processor
def inject_limits():
    return {"max_upload_mb": UPLOAD_LIMIT_MB}


def _wants_json():
    return request.path.startswith(("/api/", "/auth/"))


def _error_page(msg):
    return (
        "<!doctype html><html lang='id'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<link rel='icon' href='/static/favicon.svg' type='image/svg+xml'>"
        "<title>Ups — Talkins</title></head>"
        "<body style=\"font-family:system-ui,sans-serif;max-width:32rem;margin:15vh auto;"
        "padding:0 1.25rem;text-align:center;color:#222\">"
        "<h1 style='font-size:1.4rem'>Ups, ada kendala</h1>"
        f"<p>{escape(msg)}</p><p><a href='/'>Kembali ke beranda</a></p></body></html>"
    )


def _respond_error(code, msg):
    if _wants_json():
        return jsonify({"error": msg}), code
    return _error_page(msg), code


@app.errorhandler(HTTPException)
def handle_http_error(e):
    code = e.code or 500
    default = GENERIC_ERROR if code >= 500 else FRIENDLY_HTTP[400]
    return _respond_error(code, FRIENDLY_HTTP.get(code, default))


@app.errorhandler(Exception)
def handle_unexpected_error(e):
    app.logger.exception("Error tak terduga: %s", e)
    return _respond_error(500, GENERIC_ERROR)


def _error_response(e, default, status=500):
    """ValueError = pesan yang memang ditulis untuk user di lib/ -> tampilkan apa adanya (400).
    Error lain (teknis) -> dicatat di log server, user hanya melihat pesan `default` yang ramah."""
    if isinstance(e, ValueError):
        return jsonify({"error": str(e)}), 400
    app.logger.exception("Error: %s", e)
    return jsonify({"error": default}), status


@app.route("/favicon.ico")
def favicon():
    return Response(status=204)  # ikon sebenarnya dipasang lewat <link rel="icon"> di template


# ---------- Halaman ----------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/cara-kerja")
def how_it_works():
    return render_template("how.html")


def guest_retention_days():
    """Lama file tamu disimpan sebelum dihapus otomatis (env GUEST_RETENTION_DAYS, default 30)."""
    try:
        return max(1, int(os.environ.get("GUEST_RETENTION_DAYS", "30")))
    except ValueError:
        return 30


def _legal_context():
    """Data untuk halaman Privasi & Syarat. Isi env COMPANY_NAME (nama badan usaha) dan CONTACT_EMAIL."""
    return {
        "operator": os.environ.get("COMPANY_NAME", "").strip() or "Talkins",
        "contact_email": os.environ.get("CONTACT_EMAIL", "").strip(),
        "guest_days": guest_retention_days(),
        "updated": "5 November 2026",
    }


@app.route("/privasi")
def privacy_page():
    return render_template("privacy.html", **_legal_context())


@app.route("/syarat")
def terms_page():
    return render_template("terms.html", **_legal_context())


@app.route("/api/data/delete-all", methods=["POST"])
def delete_all_data():
    """Hapus semua file & riwayat chat milik sesi/akun ini. Wajib body JSON {"confirm": true}
    (JSON + cookie SameSite=Lax mencegah pemicu lintas situs lewat form)."""
    blocked = guard.guard("edit")
    if blocked:
        return blocked
    if (request.get_json(silent=True) or {}).get("confirm") is not True:
        return jsonify({"error": "confirm_required"}), 400
    deleted = db.delete_all_files_for_owner(session["session_id"])
    _OWNER_OK.clear()
    return jsonify({"ok": True, "deleted_files": deleted})


@app.route("/api/cron/purge", methods=["GET"])
def cron_purge():
    """Retensi otomatis harian (lihat vercel.json crons). Hanya menerima header
    Authorization: Bearer <CRON_SECRET> (Vercel mengirimnya otomatis bila env CRON_SECRET diisi);
    selain itu dijawab 404."""
    secret = os.environ.get("CRON_SECRET", "").strip()
    given = request.headers.get("Authorization", "")
    if not secret or not secrets.compare_digest(given.encode(), f"Bearer {secret}".encode()):
        return Response(status=404)
    return jsonify({"ok": True, **db.purge_expired(guest_retention_days())})


@app.route("/dashboard")
def dashboard():
    return render_template("dashboard.html")


@app.route("/workspace/<file_id>")
def workspace(file_id):
    return render_template("workspace.html", file_id=file_id)


# ---------- API: Upload & file management ----------

@app.route("/api/upload", methods=["POST"])
def upload():
    if "file" not in request.files:
        return jsonify({"error": "Tidak ada file"}), 400
    blocked = guard.guard("upload")
    if blocked:
        return blocked
    f = request.files["file"]
    session_id = session["session_id"]
    try:
        meta = excel_engine.ingest_file(f, session_id)
    except Exception as e:
        return _error_response(
            e, "File belum bisa dibaca. Pastikan formatnya .xlsx/.xls/.xlsm, tidak rusak, dan tidak diberi kata sandi."
        )
    # 200 + duplicate=true -> file sudah pernah diunggah, frontend minta konfirmasi user
    return jsonify(meta), (200 if meta.get("duplicate") else 201)


@app.route("/api/upload-url", methods=["POST"])
def upload_url():
    blocked = guard.guard("upload")
    if blocked:
        return blocked
    url = (request.get_json(silent=True) or {}).get("url", "")
    session_id = session["session_id"]
    try:
        meta = excel_engine.ingest_from_url(url, session_id)
    except Exception as e:
        return _error_response(
            e, "Link belum bisa dibuka. Pastikan link-nya benar dan filenya bisa diakses publik, lalu coba lagi."
        )
    return jsonify(meta), (200 if meta.get("duplicate") else 201)


@app.route("/api/drive/config")
def drive_config():
    """Konfigurasi unggah file besar via Google Drive (dipakai drive_upload.js).
    client_id OAuth memang publik; tanpa GOOGLE_CLIENT_ID fitur ini nonaktif."""
    client_id = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
    return jsonify(
        {"enabled": bool(client_id), "client_id": client_id, "max_mb": excel_engine.drive_max_mb()}
    )


@app.route("/api/upload-drive", methods=["POST"])
def upload_drive():
    """File besar: sudah diunggah browser ke Google Drive user, di sini hanya diproses.
    Body: {"file_id": "...", "access_token": "..."} (token dipakai sekali untuk mengunduh, tidak disimpan)."""
    blocked = guard.guard("upload")
    if blocked:
        return blocked
    data = request.get_json(silent=True) or {}
    session_id = session["session_id"]
    try:
        meta = excel_engine.ingest_from_drive(data.get("file_id"), data.get("access_token"), session_id)
    except Exception as e:
        return _error_response(
            e, "File dari Google Drive belum bisa diproses. Pastikan formatnya .xlsx/.xls/.xlsm lalu coba lagi."
        )
    return jsonify(meta), (200 if meta.get("duplicate") else 201)


@app.route("/api/files", methods=["GET"])
def list_files():
    session_id = session["session_id"]
    return jsonify(db.list_files(session_id))


@app.route("/api/files/<file_id>", methods=["DELETE"])
def delete_file(file_id):
    session_id = session["session_id"]
    db.delete_file(session_id, file_id)
    _forget_owner(file_id)
    return jsonify({"ok": True})


# ---------- API: Data preview & manipulasi (working copy, bukan file asli) ----------

@app.route("/api/files/<file_id>/sheets", methods=["GET"])
def get_sheets(file_id):
    return jsonify(excel_engine.get_sheet_list(file_id))


@app.route("/api/files/<file_id>/preview", methods=["GET"])
def preview(file_id):
    sheet = request.args.get("sheet")
    try:
        row_start = int(request.args.get("row_start", 0))
        row_count = int(request.args.get("row_count", 200))
    except ValueError:
        return jsonify({"error": "row_start/row_count harus angka"}), 400
    with_meta = request.args.get("meta", "1") != "0"
    try:
        return jsonify(excel_engine.preview_sheet(file_id, sheet, row_start, row_count, with_meta))
    except ValueError as e:
        return jsonify({"error": str(e)}), 404


@app.route("/api/files/<file_id>/recap", methods=["GET"])
def recap(file_id):
    blocked = guard.guard("heavy")
    if blocked:
        return blocked
    sheet = request.args.get("sheet")
    lang = request.args.get("lang", "id")
    try:
        return jsonify(excel_engine.get_recap(file_id, sheet, lang))
    except ValueError as e:
        return jsonify({"error": str(e)}), 404


@app.route("/api/files/<file_id>/manipulate", methods=["POST"])
def manipulate(file_id):
    blocked = guard.guard("edit")
    if blocked:
        return blocked
    op = request.get_json(silent=True) or {}  # contoh: {"action": "drop_na", "sheet": "Sheet1", "columns": [...]}
    try:
        result = excel_engine.apply_operation(file_id, op)
    except Exception as e:
        return _error_response(e, "Aksi pada data belum berhasil dijalankan. Coba lagi atau pilih aksi lain.")
    return jsonify(result)


@app.route("/api/files/<file_id>/cell", methods=["POST"])
def set_cell(file_id):
    """Isi satu sel di working copy. Teks berawalan '=' dihitung sebagai rumus Excel."""
    blocked = guard.guard("edit")
    if blocked:
        return blocked
    data = request.get_json(silent=True) or {}
    try:
        result = excel_engine.set_cell(
            file_id, data.get("sheet"), data.get("row"), data.get("col"), data.get("input", ""))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(result)


@app.route("/api/files/<file_id>/reset", methods=["POST"])
def reset_working_copy(file_id):
    blocked = guard.guard("edit")
    if blocked:
        return blocked
    excel_engine.reset_to_original(file_id)
    return jsonify({"ok": True})


@app.route("/api/files/<file_id>/download", methods=["GET"])
def download_working_copy(file_id):
    blocked = guard.guard("heavy")
    if blocked:
        return blocked
    return excel_engine.export_working_copy(file_id)


# ---------- API: Pemakaian AI ----------

@app.route("/api/usage", methods=["GET"])
def api_usage():
    """Ringkasan pemakaian AI bulan berjalan milik pengguna ini."""
    return jsonify(guard.usage_summary())


@app.route("/api/admin/usage", methods=["GET"])
def api_admin_usage():
    """Laporan biaya per pengguna/model untuk admin. Dilindungi header X-Admin-Token (env ADMIN_TOKEN);
    tanpa token yang benar dijawab 404 supaya keberadaannya tidak terlihat."""
    expected = os.environ.get("ADMIN_TOKEN", "").strip()
    given = request.headers.get("X-Admin-Token", "")
    if not expected or not secrets.compare_digest(expected.encode(), given.encode()):
        return Response(status=404)
    try:
        days = int(request.args.get("days", 30))
    except ValueError:
        days = 30
    return jsonify(guard.admin_report(days=days))


# ---------- API: Chat dengan data ----------

@app.route("/api/files/<file_id>/chat", methods=["POST"])
def chat(file_id):
    body = request.get_json(silent=True) or {}
    message = (body.get("message") or "").strip()
    lang = body.get("lang", "id")  # bahasa antarmuka aktif (id/en)
    if not message:
        return jsonify({"error": "Tulis pertanyaanmu dulu, lalu kirim lagi."}), 400
    blocked = guard.guard("chat", ai=True)
    if blocked:
        return blocked
    guard.set_file(file_id)
    session_id = session["session_id"]
    try:
        reply = chat_engine.ask(session_id, file_id, message, lang)
    except Exception as e:
        return _error_response(
            e, "Asisten AI sedang sibuk atau ada gangguan sementara. Tunggu beberapa saat lalu coba lagi.", 503
        )
    return jsonify(reply)


@app.route("/api/files/<file_id>/chat/history", methods=["GET"])
def chat_history(file_id):
    session_id = session["session_id"]
    return jsonify(db.get_chat_history(session_id, file_id))


# ---------- API: Export tampilan lain (slide, ringkasan) ----------

@app.route("/api/files/<file_id>/export/slides", methods=["POST"])
def export_slides(file_id):
    """Susun deck slide dari data (cepat, tanpa AI). Body: {topic, sheet, lang, theme}."""
    blocked = guard.guard("heavy")
    if blocked:
        return blocked
    try:
        deck = slide_engine.build_deck(file_id, request.get_json(silent=True) or {})
    except Exception as e:
        return _error_response(e, "Slide belum berhasil dibuat. Coba lagi sebentar lagi.")
    return jsonify({"deck": deck})


@app.route("/api/files/<file_id>/export/slides/polish", methods=["POST"])
def polish_slides(file_id):
    """AI menulis ulang judul & poin slide (opsional, panggilan terpisah supaya deck tampil dulu)."""
    deck = (request.get_json(silent=True) or {}).get("deck") or {}
    if not deck.get("slides"):  # tolak sebelum menghitung kuota
        return jsonify({"error": "Slide masih kosong, belum ada yang bisa dirapikan."}), 400
    try:
        slide_engine.validate_deck(deck)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    blocked = guard.guard("polish", ai=True)
    if blocked:
        return blocked
    guard.set_file(file_id)
    try:
        return jsonify(slide_engine.polish_deck(deck))
    except Exception as e:
        return _error_response(e, "Asisten AI belum bisa merapikan slide sekarang. Slide aslinya tetap aman, coba lagi nanti.", 502)


@app.route("/api/files/<file_id>/export/slides.pptx", methods=["POST"])
def download_pptx(file_id):
    """Ekspor deck (termasuk hasil edit user) ke file .pptx asli dengan grafik native."""
    body = request.get_json(silent=True) or {}
    deck = body.get("deck") or {}
    if not deck.get("slides"):
        return jsonify({"error": "Slide masih kosong, belum ada yang bisa diunduh."}), 400
    try:
        slide_engine.validate_deck(deck)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    blocked = guard.guard("heavy")
    if blocked:
        return blocked
    try:
        data = slide_engine.render_pptx(deck, body.get("theme"))
    except Exception as e:
        return _error_response(e, "File PowerPoint belum berhasil dibuat. Coba lagi sebentar lagi.")
    fname = re.sub(r"[^A-Za-z0-9_.-]+", "_", (deck.get("title") or "slide"))[:60] or "slide"
    return Response(
        data,
        mimetype="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        headers={"Content-Disposition": f'attachment; filename="{fname}.pptx"'},
    )


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(debug=True, port=5000)
