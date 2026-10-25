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

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from lib import db
from lib import excel_engine
from lib import chat_engine
from lib import formula_engine
from lib import slide_engine

app = Flask(
    __name__,
    template_folder="../templates",
    static_folder="../static",
)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")

MAX_UPLOAD_MB = 20
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024

# Saran rumus untuk sel yang dipilih di tab Rumus: POST /api/formula-suggest
formula_engine.register_routes(app)


@app.before_request
def ensure_session():
    if "session_id" not in session:
        session["session_id"] = str(uuid.uuid4())


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
        return jsonify({"error": str(e)}), 400
    # 200 + duplicate=true -> file sudah pernah diunggah, frontend minta konfirmasi user
    return jsonify(meta), (200 if meta.get("duplicate") else 201)


@app.route("/api/upload-url", methods=["POST"])
def upload_url():
    url = (request.json or {}).get("url", "")
    session_id = session["session_id"]
    try:
        meta = excel_engine.ingest_from_url(url, session_id)
    except Exception as e:
        return jsonify({"error": str(e)}), 400
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
    op = request.json  # contoh: {"action": "drop_na", "sheet": "Sheet1", "columns": [...]}
    try:
        result = excel_engine.apply_operation(file_id, op)
    except Exception as e:
        return jsonify({"error": str(e)}), 400
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
    message = request.json.get("message", "")
    lang = request.json.get("lang", "id")  # bahasa antarmuka aktif (id/en)
    session_id = session["session_id"]
    try:
        reply = chat_engine.ask(session_id, file_id, message, lang)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
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
        deck = slide_engine.build_deck(file_id, request.json or {})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"deck": deck})


@app.route("/api/files/<file_id>/export/slides/polish", methods=["POST"])
def polish_slides(file_id):
    """AI menulis ulang judul & poin slide (opsional, panggilan terpisah supaya deck tampil dulu)."""
    try:
        return jsonify(slide_engine.polish_deck((request.json or {}).get("deck") or {}))
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@app.route("/api/files/<file_id>/export/slides.pptx", methods=["POST"])
def download_pptx(file_id):
    """Ekspor deck (termasuk hasil edit user) ke file .pptx asli dengan grafik native."""
    body = request.json or {}
    deck = body.get("deck") or {}
    if not deck.get("slides"):
        return jsonify({"error": "Deck kosong"}), 400
    try:
        data = slide_engine.render_pptx(deck, body.get("theme"))
    except Exception as e:
        return jsonify({"error": str(e)}), 500
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
