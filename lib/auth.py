"""
Login & daftar lewat Google OAuth 2.0 (Authorization Code flow, server-side).

"Daftar" dan "Masuk" memakai alur yang sama: kalau akun Google belum pernah dipakai,
akun Meja Data otomatis dibuat; kalau sudah ada, langsung masuk.

Tanpa dependensi baru: cukup `requests` yang sudah ada.

Cara kerja identitas:
- Pengunjung anonim punya session["session_id"] = uuid acak (perilaku lama).
- Setelah login, session["session_id"] diganti menjadi "user:<id>" yang STABIL di semua
  perangkat. Karena seluruh kode lain hanya memakai session["session_id"], file & riwayat chat
  otomatis terikat ke akun tanpa mengubah modul lain.
- File yang diunggah saat masih anonim dipindahkan (diadopsi) ke akun saat pertama login.

Environment variable:
  GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET   (wajib, dari Google Cloud Console)
  APP_BASE_URL       (opsional, mis. https://talkins.vercel.app; dasar redirect URI)
  GOOGLE_REDIRECT_URI(opsional, menimpa APP_BASE_URL + /auth/google/callback)
  REQUIRE_LOGIN=0    (opsional, mematikan wajib login; default AKTIF: semua halaman kecuali beranda & /login butuh login)
"""
import os
import re
from datetime import datetime, timezone
import secrets
import uuid
from urllib.parse import urlencode, urlparse

import requests
from flask import redirect, request, session, jsonify, render_template
from werkzeug.security import generate_password_hash, check_password_hash

from lib import db

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
GOOGLE_TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"
DEFAULT_SECRET = "dev-secret-change-me"

# Login manual (username + kata sandi)
USERNAME_RE = re.compile(r"^[a-z0-9_.]{3,30}$")
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")
PASSWORD_MIN, PASSWORD_MAX = 8, 128
MAX_FAILED_ATTEMPTS, LOCK_MINUTES = 5, 15
# Hash palsu: dipakai agar waktu respons sama saat username tidak ada (cegah user enumeration)
_DUMMY_HASH = generate_password_hash("dummy-password-for-timing")

# Halaman/endpoint yang boleh diakses tanpa login saat REQUIRE_LOGIN aktif
_PUBLIC_PREFIXES = ("/static/", "/auth/", "/login", "/api/me", "/favicon.ico")


def _client_id():
    return os.environ.get("GOOGLE_CLIENT_ID", "").strip()


def _client_secret():
    return os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()


def _require_login():
    """Wajib login secara bawaan. Matikan dengan REQUIRE_LOGIN=0 (tamu boleh memakai aplikasi)."""
    return os.environ.get("REQUIRE_LOGIN", "1").strip().lower() not in ("0", "false", "no", "off")


def _redirect_uri():
    explicit = os.environ.get("GOOGLE_REDIRECT_URI", "").strip()
    if explicit:
        return explicit
    base = os.environ.get("APP_BASE_URL", "").strip().rstrip("/") or request.host_url.rstrip("/")
    host = urlparse(base).hostname or ""
    if base.startswith("http://") and host not in ("localhost", "127.0.0.1"):
        base = "https://" + base[len("http://"):]  # di balik proxy Vercel request tampak http
    return base + "/auth/google/callback"


def _safe_next(value):
    """Cegah open-redirect: hanya path internal yang diterima."""
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return "/dashboard"
    return value


def _user_session_id(user_id):
    return f"user:{user_id}"


def current_user():
    """User yang sedang login (dict) atau None."""
    uid = session.get("user_id")
    if not uid:
        return None
    user = getattr(request, "_meja_user", None)
    if user is None:
        try:
            user = db.get_user(uid) or False
        except Exception:
            user = False
        request._meja_user = user
    return user or None


def _login(user, adopt_from, notice=None):
    """Tandai sesi sebagai login. Dipanggil hanya setelah identitas terverifikasi.
    `notice` ("registered" | "login") ditampilkan sebagai notifikasi sekali pakai di halaman tujuan."""
    sid = _user_session_id(user["id"])
    if adopt_from and not str(adopt_from).startswith("user:"):
        db.adopt_session(adopt_from, sid)  # file & chat tamu -> akun
    session.clear()  # buang state lama (cegah session fixation)
    session.permanent = True
    session["user_id"] = user["id"]
    session["session_id"] = sid
    if notice:
        session["notice"] = notice


def _csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def _csrf_ok():
    expected = session.get("csrf", "")
    given = request.headers.get("X-CSRF-Token", "")
    return bool(expected) and secrets.compare_digest(expected, given)


def _json_error(code, status, **extra):
    return jsonify(dict(error=code, **extra)), status


def _google_finish(profile):
    """Selesaikan masuk/daftar dari profil Google yang sudah terverifikasi (userinfo).
    Akun dibuat bila belum ada. Mengembalikan (kode_error | None, is_new)."""
    sub, email = profile.get("sub"), (profile.get("email") or "").lower()
    if not sub or not email:
        return "failed", False
    if not profile.get("email_verified"):
        return "email_unverified", False
    try:
        user = db.upsert_user(
            google_sub=sub,
            email=email,
            name=profile.get("name") or email.split("@")[0],
            picture=profile.get("picture"),
        )
        # upsert_user sudah membuat akun bila belum ada; is_new=True berarti baru saja didaftarkan
        is_new = bool(user.get("is_new"))
        _login(user, adopt_from=session.get("session_id"), notice="registered" if is_new else "login")
    except Exception:
        return "failed", False
    return None, is_new


def _fail(code):
    return redirect("/login?" + urlencode({"error": code}))


def register_routes(app):
    secure_cookie = bool(os.environ.get("VERCEL")) or bool(os.environ.get("COOKIE_SECURE"))
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",  # juga melindungi POST /auth/logout dari CSRF lintas situs
        SESSION_COOKIE_SECURE=secure_cookie,
        PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 30,
    )

    @app.context_processor
    def inject_user():
        return {
            "current_user": current_user(),
            "require_login": _require_login(),
            "current_year": datetime.now(timezone.utc).year,
            # Notifikasi sekali pakai (registered/login): diambil lalu dihapus dari sesi
            "flash_notice": session.pop("notice", None),
        }

    @app.before_request
    def gate():
        # Wajib login (opsional). Dipasang sebagai before_request tambahan.
        if not _require_login() or current_user():
            return None
        path = request.path
        if path == "/" or path.startswith(_PUBLIC_PREFIXES):
            return None
        if path.startswith("/api/"):
            return jsonify({"error": "Silakan masuk dulu.", "login_required": True}), 401
        return redirect("/login?" + urlencode({"next": request.full_path.rstrip("?")}))

    @app.route("/login")
    def login_page():
        if current_user():
            return redirect(_safe_next(request.args.get("next")))
        return render_template(
            "login.html",
            next_url=_safe_next(request.args.get("next")),
            csrf_token=_csrf_token(),
            google_client_id=_client_id(),
        )

    # ---------- Pengaturan akun: ganti username & kata sandi ----------

    @app.route("/settings")
    def settings_page():
        if not current_user():
            return redirect("/login?" + urlencode({"next": "/settings"}))
        return render_template("settings.html", csrf_token=_csrf_token())

    def _settings_guard():
        """Wajib login + token CSRF. Mengembalikan (user, error_response)."""
        user = current_user()
        if not user:
            return None, _json_error("login_required", 401, login_required=True)
        if not _csrf_ok():
            return None, _json_error("csrf", 403)
        return user, None

    @app.route("/api/settings/username", methods=["POST"])
    def api_settings_username():
        user, err = _settings_guard()
        if err:
            return err
        data = request.get_json(silent=True) or {}
        username = str(data.get("username", "")).strip().lower()
        if not USERNAME_RE.match(username):
            return _json_error("username_invalid", 400)
        if username == (user.get("username") or ""):
            return jsonify({"ok": True, "username": username, "unchanged": True})
        try:
            saved = db.update_username(user["id"], username)
        except Exception:
            return _json_error("failed", 500)
        if not saved:
            return _json_error("username_taken", 409)
        return jsonify({"ok": True, "username": username})

    @app.route("/api/settings/password", methods=["POST"])
    def api_settings_password():
        user, err = _settings_guard()
        if err:
            return err
        data = request.get_json(silent=True) or {}
        current = str(data.get("current_password", ""))
        new = str(data.get("new_password", ""))

        try:
            info = db.get_user_auth(user["id"]) or {}
        except Exception:
            return _json_error("failed", 500)
        stored = info.get("password_hash") or ""
        has_password = bool(stored) and not stored.startswith("!")  # "!..." = akun Google tanpa kata sandi

        # Validasi kata sandi baru dulu (murah), supaya salah ketik tidak dihitung sebagai percobaan gagal
        if len(new) < PASSWORD_MIN:
            return _json_error("password_short", 400)
        if len(new) > PASSWORD_MAX:
            return _json_error("password_long", 400)
        if new.lower() in ((info.get("username") or "").lower(), (info.get("email") or "").lower()):
            return _json_error("password_weak", 400)

        if has_password:
            # Ganti kata sandi wajib menyertakan kata sandi lama; tebakan salah ikut dihitung (kunci akun)
            if info.get("is_locked"):
                return _json_error("locked", 429, minutes=max(int(info.get("lock_minutes") or 1), 1))
            if not current or not check_password_hash(stored, current[:PASSWORD_MAX]):
                try:
                    db.record_login_failure(user["id"], MAX_FAILED_ATTEMPTS, LOCK_MINUTES)
                except Exception:
                    pass
                return _json_error("current_wrong", 401)
            if new == current:
                return _json_error("password_same", 400)

        try:
            db.set_password(user["id"], generate_password_hash(new))
        except Exception:
            return _json_error("failed", 500)
        return jsonify({"ok": True, "created": not has_password})

    @app.route("/auth/register", methods=["POST"])
    def auth_register():
        if not _csrf_ok():
            return _json_error("csrf", 403)
        data = request.get_json(silent=True) or {}
        name = " ".join(str(data.get("name", "")).split())[:80]
        email = str(data.get("email", "")).strip().lower()
        username = str(data.get("username", "")).strip().lower()
        password = str(data.get("password", ""))

        if not name:
            return _json_error("name_required", 400)
        if len(email) > 254 or not EMAIL_RE.match(email):
            return _json_error("email_invalid", 400)
        if not USERNAME_RE.match(username):
            return _json_error("username_invalid", 400)
        if len(password) < PASSWORD_MIN:
            return _json_error("password_short", 400)
        if len(password) > PASSWORD_MAX:
            return _json_error("password_long", 400)
        if password.lower() in (username, email):
            return _json_error("password_weak", 400)

        try:
            conflict = db.find_register_conflict(username, email)
            if conflict:
                return _json_error(conflict, 409)
            user = db.create_manual_user(username, name, email, generate_password_hash(password))
            if not user:  # kalah balapan dgn pendaftar lain yang barusan
                return _json_error(db.find_register_conflict(username, email) or "username_taken", 409)
        except Exception:
            return _json_error("failed", 500)
        _login(user, adopt_from=session.get("session_id"), notice="registered")
        return jsonify({"ok": True, "redirect": "/dashboard"})  # pendaftar baru langsung ke dashboard

    @app.route("/auth/login", methods=["POST"])
    def auth_login():
        if not _csrf_ok():
            return _json_error("csrf", 403)
        data = request.get_json(silent=True) or {}
        # Boleh diisi username ATAU email ("identifier"; "username" tetap diterima agar kompatibel)
        identifier = str(data.get("identifier") or data.get("username") or "").strip().lower()
        password = str(data.get("password", ""))
        valid_id = bool(EMAIL_RE.match(identifier)) if "@" in identifier else bool(USERNAME_RE.match(identifier))

        try:
            user = db.get_user_for_login(identifier) if valid_id else None
        except Exception:
            return _json_error("failed", 500)

        # Akun terkunci: tolak sebelum memeriksa kata sandi, supaya tebakan tidak bisa dilanjutkan
        if user and user.get("is_locked"):
            return _json_error("locked", 429, minutes=max(int(user.get("lock_minutes") or 1), 1))

        stored = user.get("password_hash") if user else None
        if stored and stored.startswith("!"):
            stored = None  # penanda "tanpa kata sandi" (akun Google), bukan hash asli
        ok = check_password_hash(stored or _DUMMY_HASH, password[:PASSWORD_MAX]) and bool(stored)
        if not ok:
            if user and stored:
                try:
                    db.record_login_failure(user["id"], MAX_FAILED_ATTEMPTS, LOCK_MINUTES)
                except Exception:
                    pass
            return _json_error("invalid_credentials", 401)  # pesan sama untuk semua kasus gagal

        try:
            db.reset_login_failures(user["id"])
        except Exception:
            pass
        _login(user, adopt_from=session.get("session_id"), notice="login")
        return jsonify({"ok": True, "redirect": _safe_next(data.get("next"))})

    @app.route("/auth/google")
    def auth_google():
        if not _client_id() or not _client_secret():
            return _fail("not_configured")
        if app.secret_key == DEFAULT_SECRET and os.environ.get("VERCEL"):
            return _fail("not_configured")  # SECRET_KEY default = sesi bisa dipalsukan

        state = secrets.token_urlsafe(32)
        session["oauth_state"] = state
        session["oauth_next"] = _safe_next(request.args.get("next"))
        params = {
            "client_id": _client_id(),
            "redirect_uri": _redirect_uri(),
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "prompt": "select_account",
            "access_type": "online",
        }
        return redirect(GOOGLE_AUTH_URL + "?" + urlencode(params))

    @app.route("/auth/google/token", methods=["POST"])
    def auth_google_token():
        """Masuk/daftar lewat popup Google (Google Identity Services) TANPA pindah halaman.
        Browser mengirim access token; server memastikan token itu diterbitkan UNTUK aplikasi ini
        (cek `aud` ke tokeninfo; tanpa ini token dari aplikasi lain bisa dipakai menyamar), lalu
        mengambil profil dari userinfo dan memakai alur yang sama dengan callback."""
        if not _csrf_ok():
            return _json_error("csrf", 403)
        if not _client_id():
            return _json_error("not_configured", 503)
        if app.secret_key == DEFAULT_SECRET and os.environ.get("VERCEL"):
            return _json_error("not_configured", 503)

        data = request.get_json(silent=True) or {}
        access_token = str(data.get("access_token", "")).strip()
        if not access_token or len(access_token) > 4096:
            return _json_error("failed", 400)

        try:
            ti = requests.get(GOOGLE_TOKENINFO_URL, params={"access_token": access_token}, timeout=10)
            if ti.status_code != 200:
                return _json_error("failed", 401)
            tinfo = ti.json()
            aud = str(tinfo.get("aud") or tinfo.get("azp") or "")
            if not aud or not secrets.compare_digest(aud, _client_id()):
                return _json_error("failed", 401)  # token milik aplikasi lain
            info = requests.get(
                GOOGLE_USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"}, timeout=10
            )
            info.raise_for_status()
            profile = info.json()
        except Exception:
            return _json_error("failed", 502)

        code_err, is_new = _google_finish(profile)
        if code_err:
            return _json_error(code_err, 403 if code_err == "email_unverified" else 500)
        return jsonify({
            "ok": True,
            "is_new": is_new,
            "redirect": "/dashboard" if is_new else _safe_next(data.get("next")),
        })

    @app.route("/auth/google/callback")
    def auth_google_callback():
        if request.args.get("error"):
            return _fail("access_denied" if request.args["error"] == "access_denied" else "failed")

        expected = session.pop("oauth_state", None)
        given = request.args.get("state", "")
        if not expected or not secrets.compare_digest(expected, given):
            return _fail("state")
        code = request.args.get("code")
        if not code:
            return _fail("failed")
        next_url = _safe_next(session.pop("oauth_next", None))

        try:
            tok = requests.post(
                GOOGLE_TOKEN_URL,
                data={
                    "code": code,
                    "client_id": _client_id(),
                    "client_secret": _client_secret(),
                    "redirect_uri": _redirect_uri(),
                    "grant_type": "authorization_code",
                },
                timeout=10,
            )
            tok.raise_for_status()
            access_token = tok.json().get("access_token")
            if not access_token:
                return _fail("failed")
            info = requests.get(
                GOOGLE_USERINFO_URL,
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=10,
            )
            info.raise_for_status()
            profile = info.json()
        except Exception:
            return _fail("failed")

        code_err, is_new = _google_finish(profile)
        if code_err:
            return _fail(code_err)
        return redirect("/dashboard" if is_new else next_url)

    @app.route("/auth/logout", methods=["POST"])
    def auth_logout():
        session.clear()
        session["session_id"] = str(uuid.uuid4())  # kembali jadi tamu baru
        return redirect("/")

    @app.route("/api/me")
    def api_me():
        user = current_user()
        if not user:
            return jsonify({"authenticated": False})
        return jsonify({
            "authenticated": True,
            "user": {k: user.get(k) for k in ("id", "username", "email", "name", "picture")},
        })
