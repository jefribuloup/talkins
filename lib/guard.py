"""Pengaman biaya & penyalahgunaan.

1. Saklar darurat AI           : env AI_ENABLED=0 mematikan semua fitur yang memanggil LLM.
2. Rate limit                  : disimpan di Neon (memori proses tidak andal di serverless).
                                 Jendela tetap per menit & per hari, per user/sesi DAN per IP.
3. Pencatatan pemakaian        : tabel usage_events (token, kredit, model) untuk menghitung biaya
                                 per user sebelum harga ditetapkan.

Semua batas bisa diubah lewat env tanpa deploy ulang kode, polanya:
    RL_<JENIS>_<USER|IP>_<MIN|DAY>=<angka>     (0 = batas itu dimatikan)
contoh: RL_CHAT_USER_MIN=5  RL_UPLOAD_IP_DAY=300
Jika database gagal dihubungi, rate limit "fail-open" (request tetap lolos, error dicatat di log)
supaya gangguan database tidak ikut mematikan seluruh aplikasi.
"""
import os
import random
import time

from flask import g, has_request_context, jsonify, request, session

from lib import db

# ---------- 1. Saklar AI ----------

AI_DISABLED_MESSAGE = (
    "Fitur AI sedang dinonaktifkan sementara. Rekap, rumus, manipulasi data, dan slide "
    "tanpa AI tetap bisa dipakai."
)


def ai_enabled():
    return os.environ.get("AI_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off")


# ---------- 2. Rate limit ----------

# jenis -> {"user": (per_menit, per_hari), "ip": (per_menit, per_hari)}
# Nilai awal sengaja longgar untuk IP (satu kampus/kantor bisa berbagi satu IP lewat NAT).
_DEFAULTS = {
    "chat": {"user": (8, 150), "ip": (60, 2000)},
    "polish": {"user": (3, 30), "ip": (20, 300)},
    "upload": {"user": (6, 60), "ip": (30, 600)},
    "heavy": {"user": (30, 0), "ip": (120, 0)},          # rekap, ekspor slide/pptx, unduh salinan
    "auth_register": {"ip": (10, 200)},
    "auth_login": {"ip": (20, 500)},
}


def _env_int(name, default):
    try:
        return max(0, int(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


def limits_for(kind):
    """[(scope, detik_jendela, batas)] untuk jenis aksi ini, sudah memperhitungkan override env."""
    out = []
    for scope, (per_min, per_day) in _DEFAULTS[kind].items():
        pm = _env_int(f"RL_{kind}_{scope}_MIN".upper(), per_min)
        pd_ = _env_int(f"RL_{kind}_{scope}_DAY".upper(), per_day)
        if pm > 0:
            out.append((scope, 60, pm))
        if pd_ > 0:
            out.append((scope, 86400, pd_))
    return out


def client_ip():
    """IP klien. Di Vercel, header proxy ditulis ulang oleh platform (tidak bisa dipalsukan klien);
    di luar Vercel hanya alamat soket yang dipercaya."""
    if os.environ.get("VERCEL"):
        for h in ("x-vercel-forwarded-for", "x-real-ip", "x-forwarded-for"):
            v = request.headers.get(h)
            if v:
                return v.split(",")[0].strip()
    return request.remote_addr or "unknown"


def owner_id():
    """Pemilik kuota: id user kalau sudah login, kalau tidak id sesi (cookie)."""
    uid = session.get("user_id")
    if uid:
        return "u:" + str(uid)
    return "s:" + str(session.get("session_id", "anon"))


def check(kind, owner=None, ip=None):
    """Hitung 1 permintaan ke semua jendela milik `kind`.
    Return None kalau lolos; kalau kena batas: {"retry_after": detik, "scope": "user"|"ip"}."""
    rules = limits_for(kind)
    if not rules:
        return None
    now = int(time.time())
    blocked = None
    try:
        conn = db.get_conn()
        try:
            cur = conn.cursor()
            for scope, window, limit in rules:
                ident = owner if scope == "user" else ip
                if not ident:
                    continue
                wid = now // window
                cur.execute(
                    """
                    INSERT INTO rate_limits (key, bucket, window_id, count, expires_at)
                    VALUES (%s, %s, %s, 1, to_timestamp(%s))
                    ON CONFLICT (key, bucket, window_id)
                    DO UPDATE SET count = rate_limits.count + 1
                    RETURNING count
                    """,
                    (f"{scope}:{ident}", f"{kind}:{window}", wid, (wid + 2) * window),
                )
                if cur.fetchone()[0] > limit:
                    retry = (wid + 1) * window - now
                    if blocked is None or retry > blocked["retry_after"]:
                        blocked = {"retry_after": max(1, retry), "scope": scope}
            if random.random() < 0.02:  # bersih-bersih berkala, murah
                cur.execute("DELETE FROM rate_limits WHERE expires_at < now()")
            conn.commit()
            cur.close()
        finally:
            conn.close()
    except Exception:
        _log_exception("rate limit gagal (fail-open)")
        return None
    return blocked


def limit_response(blocked):
    secs = blocked["retry_after"]
    wait = f"{secs} detik" if secs <= 120 else f"{round(secs / 60)} menit" if secs <= 7200 else f"{round(secs / 3600)} jam"
    resp = jsonify({
        "error": f"Terlalu banyak permintaan. Coba lagi dalam sekitar {wait}.",
        "rate_limited": True,
        "retry_after": secs,
    })
    resp.status_code = 429
    resp.headers["Retry-After"] = str(secs)
    return resp


def guard(kind, ai=False, per_user=True):
    """Satu pintu untuk endpoint: return respons (503/429) bila harus ditolak, None bila boleh lanjut."""
    if ai and not ai_enabled():
        resp = jsonify({"error": AI_DISABLED_MESSAGE, "ai_disabled": True})
        resp.status_code = 503
        return resp
    blocked = check(kind, owner=owner_id() if per_user else None, ip=client_ip())
    return limit_response(blocked) if blocked else None


# ---------- 3. Pencatatan pemakaian ----------

# Kredit per jenis panggilan (nilai awal; sesuaikan setelah mengukur biaya nyata).
CREDITS = {"chat": 1, "chat_select": 0, "polish": 3, "slide_outline": 0}


def set_file(file_id):
    """Tandai file yang sedang diproses request ini (ikut tercatat di usage_events)."""
    if has_request_context():
        g.usage_file_id = str(file_id)


def record_llm(kind, provider, model, tokens_in, tokens_out, ok=True):
    """Catat satu panggilan LLM. Best-effort: kegagalan mencatat tidak boleh menggagalkan jawaban."""
    if not has_request_context():
        return
    try:
        conn = db.get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO usage_events
                    (user_id, session_id, kind, credits, tokens_in, tokens_out, model, provider, file_id, ok)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    session.get("user_id"), session.get("session_id"), kind,
                    CREDITS.get(kind, 0) if ok else 0,
                    tokens_in, tokens_out, model, provider,
                    getattr(g, "usage_file_id", None), bool(ok),
                ),
            )
            conn.commit()
            cur.close()
        finally:
            conn.close()
    except Exception:
        _log_exception("catat pemakaian gagal")


def usage_summary():
    """Pemakaian bulan berjalan milik pemanggil (user bila login, kalau tidak sesi ini)."""
    uid, sid = session.get("user_id"), session.get("session_id")
    where, arg = ("user_id = %s", uid) if uid else ("session_id = %s", sid)
    conn = db.get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT kind, count(*), COALESCE(sum(credits), 0),
                   COALESCE(sum(tokens_in), 0), COALESCE(sum(tokens_out), 0)
            FROM usage_events
            WHERE {where} AND ok AND created_at >= date_trunc('month', now())
            GROUP BY kind ORDER BY kind
            """,
            (arg,),
        )
        rows = cur.fetchall()
        cur.close()
    finally:
        conn.close()
    by_kind = {k: {"calls": int(c), "credits": int(cr), "tokens_in": int(ti), "tokens_out": int(to)}
               for k, c, cr, ti, to in rows}
    return {
        "period": "month",
        "credits": sum(v["credits"] for v in by_kind.values()),
        "tokens_in": sum(v["tokens_in"] for v in by_kind.values()),
        "tokens_out": sum(v["tokens_out"] for v in by_kind.values()),
        "by_kind": by_kind,
    }


def admin_report(days=30, top=100):
    """Laporan biaya per user untuk admin: panggilan, kredit, token, rasio gagal/fallback."""
    days = max(1, min(int(days), 365))
    conn = db.get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT COALESCE(e.user_id, 'sesi:' || e.session_id) AS who, u.email,
                   count(*) AS calls, COALESCE(sum(e.credits), 0) AS credits,
                   COALESCE(sum(e.tokens_in), 0) AS tin, COALESCE(sum(e.tokens_out), 0) AS tout,
                   count(*) FILTER (WHERE NOT e.ok) AS failed
            FROM usage_events e LEFT JOIN users u ON u.id = e.user_id
            WHERE e.created_at >= now() - (%s || ' days')::interval
            GROUP BY who, u.email
            ORDER BY credits DESC,
                     COALESCE(sum(e.tokens_in), 0) + COALESCE(sum(e.tokens_out), 0) DESC
            LIMIT %s
            """,
            (str(days), int(top)),
        )
        users = [
            {"who": w, "email": em, "calls": int(c), "credits": int(cr),
             "tokens_in": int(ti), "tokens_out": int(to), "failed": int(f)}
            for w, em, c, cr, ti, to, f in cur.fetchall()
        ]
        cur.execute(
            """
            SELECT kind, COALESCE(provider, '-'), COALESCE(model, '-'), count(*),
                   COALESCE(sum(tokens_in), 0), COALESCE(sum(tokens_out), 0), count(*) FILTER (WHERE NOT ok)
            FROM usage_events WHERE created_at >= now() - (%s || ' days')::interval
            GROUP BY 1, 2, 3 ORDER BY 4 DESC
            """,
            (str(days),),
        )
        totals = [
            {"kind": k, "provider": p, "model": m, "calls": int(c),
             "tokens_in": int(ti), "tokens_out": int(to), "failed": int(f)}
            for k, p, m, c, ti, to, f in cur.fetchall()
        ]
        cur.close()
    finally:
        conn.close()
    return {"days": days, "users": users, "totals": totals}


def _log_exception(msg):
    try:
        from flask import current_app
        current_app.logger.exception(msg)
    except Exception:
        pass
