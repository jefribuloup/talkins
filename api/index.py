"""
Entry point Flask untuk Vercel (@vercel/python).
Semua request masuk lewat sini (lihat vercel.json).
Struktur modular: logic berat ada di /lib, file ini cuma routing+orkestrasi
-> supaya gampang porting ke Next.js API routes nanti (tiap route = 1 handler mandiri).
"""
import os
import sys
import uuid

from flask import Flask, request, jsonify, render_template, session

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from lib import db
from lib import excel_engine
from lib import chat_engine

app = Flask(
    __name__,
    template_folder="../templates",
    static_folder="../static",
)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")

MAX_UPLOAD_MB = 20
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024


@app.before_request
def ensure_session():
    if "session_id" not in session:
        session["session_id"] = str(uuid.uuid4())


# ---------- Halaman ----------

@app.route("/")
def index():
    return render_template("index.html")


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
    return jsonify(meta), 201


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
    page = int(request.args.get("page", 1))
    return jsonify(excel_engine.preview_sheet(file_id, sheet, page))


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
    session_id = session["session_id"]
    try:
        reply = chat_engine.ask(session_id, file_id, message)
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
    spec = request.json  # {"topic": "...", "chart_cols": [...]}
    try:
        result = chat_engine.generate_slide_outline(file_id, spec)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify(result)


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(debug=True, port=5000)
