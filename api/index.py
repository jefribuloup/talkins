"""
Entry point Flask untuk Vercel (@vercel/python).
Semua request masuk lewat sini (lihat vercel.json).
Struktur modular: logic berat ada di /lib, file ini cuma routing+orkestrasi
-> supaya gampang porting ke Next.js API routes nanti (tiap route = 1 handler mandiri).
"""
import os
import re
import sys
import uuid

from flask import Flask, request, jsonify, render_template, session, Response
from markupsafe import escape
from werkzeug.exceptions import HTTPException

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from lib import db
from lib import excel_engine
from lib import chat_engine
from lib import formula_engine
from lib import slide_engine
from lib import auth

app = Flask(
    __name__,
    template_folder="../templates",
    static_folder="../static",
)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")

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
        "<title>Ups — Meja Data</title></head>"
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
    sheet = request.args.get("sheet")
    lang = request.args.get("lang", "id")
    try:
        return jsonify(excel_engine.get_recap(file_id, sheet, lang))
    except ValueError as e:
        return jsonify({"error": str(e)}), 404


@app.route("/api/files/<file_id>/manipulate", methods=["POST"])
def manipulate(file_id):
    op = request.get_json(silent=True) or {}  # contoh: {"action": "drop_na", "sheet": "Sheet1", "columns": [...]}
    try:
        result = excel_engine.apply_operation(file_id, op)
    except Exception as e:
        return _error_response(e, "Aksi pada data belum berhasil dijalankan. Coba lagi atau pilih aksi lain.")
    return jsonify(result)


@app.route("/api/files/<file_id>/reset", methods=["POST"])
def reset_working_copy(file_id):
    excel_engine.reset_to_original(file_id)
    return jsonify({"ok": True})


@app.route("/api/files/<file_id>/download", methods=["GET"])
def download_working_copy(file_id):
    return excel_engine.export_working_copy(file_id)


# ---------- API: Chat dengan data ----------

@app.route("/api/files/<file_id>/chat", methods=["POST"])
def chat(file_id):
    body = request.get_json(silent=True) or {}
    message = (body.get("message") or "").strip()
    lang = body.get("lang", "id")  # bahasa antarmuka aktif (id/en)
    if not message:
        return jsonify({"error": "Tulis pertanyaanmu dulu, lalu kirim lagi."}), 400
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
    try:
        deck = slide_engine.build_deck(file_id, request.get_json(silent=True) or {})
    except Exception as e:
        return _error_response(e, "Slide belum berhasil dibuat. Coba lagi sebentar lagi.")
    return jsonify({"deck": deck})


@app.route("/api/files/<file_id>/export/slides/polish", methods=["POST"])
def polish_slides(file_id):
    """AI menulis ulang judul & poin slide (opsional, panggilan terpisah supaya deck tampil dulu)."""
    try:
        return jsonify(slide_engine.polish_deck((request.get_json(silent=True) or {}).get("deck") or {}))
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


@app.route("/api/_init-db")
def init_db_route():
    """Endpoint sementara untuk bikin schema di Neon. HAPUS route ini setelah dipakai sekali."""
    try:
        db.init_db()
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"ok": True, "message": "Schema berhasil dibuat/diverifikasi"})


if __name__ == "__main__":
    app.run(debug=True, port=5000)
