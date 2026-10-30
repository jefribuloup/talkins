import os, sys, io, json, time
os.environ.update(DATABASE_URL="postgresql://postgres@/talkins_test?host=/tmp&port=5433", REQUIRE_LOGIN="0",
                  GEMINI_API_KEY="x", GROQ_API_KEY="y", SECRET_KEY="s", ADMIN_TOKEN="adm-secret")
sys.path.insert(0, ".")
import psycopg2
_orig = psycopg2.connect
psycopg2.connect = lambda dsn=None, **kw: _orig(dsn, **{k: v for k, v in kw.items() if k != "sslmode"})
import importlib.util
spec = importlib.util.spec_from_file_location("api_index", "api/index.py"); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
from lib import db, guard, chat_engine, excel_engine
app = m.app; app.config["TESTING"] = False; app.jinja_loader.searchpath = [os.path.abspath("templates")]
db.init_db()
ok = lambda c, msg: print(("PASS " if c else "FAIL ") + msg) or (c or sys.exit(1))

# --- skema ---
conn = db.get_conn(); cur = conn.cursor()
cur.execute("select to_regclass('rate_limits'), to_regclass('usage_events')"); ok(all(cur.fetchone()), "tabel rate_limits & usage_events dibuat"); conn.close()

# --- LLM palsu: catat request, bisa disetel gagal ---
calls = []; mode = {"gemini": "ok"}
class R:
    def __init__(s, code, js): s.status_code, s._js = code, js
    def json(s): return s._js
    def raise_for_status(s):
        if s.status_code >= 400: raise RuntimeError(f"HTTP {s.status_code}")
def fake_post(url, **kw):
    body = kw.get("json", {}); calls.append((url, body))
    if "generativelanguage" in url:
        if mode["gemini"] == "503": return R(503, {})
        return R(200, {"candidates": [{"content": {"parts": [{"text": "Jawaban uji"}]}}],
                       "usageMetadata": {"promptTokenCount": 120, "candidatesTokenCount": 30, "thoughtsTokenCount": 10}})
    return R(200, {"choices": [{"message": {"content": "Jawaban groq"}}], "usage": {"prompt_tokens": 100, "completion_tokens": 20}})
chat_engine.requests.post = fake_post
chat_engine.time.sleep = lambda *_: None

c = app.test_client()
c.environ_base["REMOTE_ADDR"] = "10.0.0.5"
# --- upload xls asli (validasi isi + xlrd) ---
raw = open("/mnt/user-data/uploads/FORMAT_REGISTRASI_WISUDAWAN-4.xls", "rb").read()
r = c.post("/api/upload", data={"file": (io.BytesIO(raw), "wisuda.xls")}, content_type="multipart/form-data")
ok(r.status_code == 201, f"upload .xls valid -> 201 ({r.get_json().get('filename')}, {len(r.get_json().get('sheets', []))} sheet)")
fid = r.get_json()["file_id"]
r = c.post("/api/upload", data={"file": (io.BytesIO(b"<html>login google</html>"), "x.xlsx")}, content_type="multipart/form-data")
ok(r.status_code == 400 and "bukan spreadsheet" in r.get_json()["error"], "HTML berkedok .xlsx ditolak")
r = c.post("/api/upload", data={"file": (io.BytesIO(raw), "salah_ekstensi.xlsx")}, content_type="multipart/form-data")
ok(r.status_code in (200, 201), "isi .xls bernama .xlsx tetap terbaca (duplikat/terdeteksi): %s" % r.status_code)

# --- chat: token cap + usage log ---
calls.clear()
r = c.post(f"/api/files/{fid}/chat", json={"message": "berapa total baris pada Data Master?", "lang": "id"})
ok(r.status_code == 200, "chat 200: " + str(r.get_json())[:80])
gem = [b for u, b in calls if "generativelanguage" in u]
ok(gem and all(b["generationConfig"]["maxOutputTokens"] > 0 for b in gem), f"maxOutputTokens terkirim ke Gemini ({[b['generationConfig'] for b in gem]})")
conn = db.get_conn(); cur = conn.cursor()
cur.execute("select kind, credits, tokens_in, tokens_out, provider, model, ok from usage_events order by id"); rows = cur.fetchall(); conn.close()
print("   usage_events:", rows)
ok(any(k == "chat" and cr == 1 and ti == 120 and to == 40 and p == "gemini" for k, cr, ti, to, p, mo, o in rows), "pemakaian chat tercatat (120 in / 30+10 out, 1 kredit)")

# --- retry: maksimal 1 ulang di Gemini, lalu Groq (total <=3 request) ---
calls.clear(); mode["gemini"] = "503"
r = c.post(f"/api/files/{fid}/chat", json={"message": "berapa total baris pada Data Master?", "lang": "id"})
n_gem = sum("generativelanguage" in u for u, _ in calls); n_groq = sum("groq" in u for u, _ in calls)
ok(r.status_code == 200 and n_gem == 2 and n_groq == 1, f"Gemini 503 -> {n_gem}x Gemini (1 ulang) + {n_groq}x Groq (sebelumnya 3+1)")
ok(all("max_completion_tokens" in b for u, b in calls if "groq" in u), "max_completion_tokens terkirim ke Groq")
mode["gemini"] = "ok"

# --- rate limit chat: 8/menit per user/sesi ---
os.environ["RL_CHAT_USER_MIN"] = "4"
c2 = app.test_client(); c2.environ_base["REMOTE_ADDR"] = "10.0.0.9"
r = c2.post("/api/upload", data={"file": (io.BytesIO(raw), "w2.xls")}, content_type="multipart/form-data"); fid2 = r.get_json()["file_id"]
codes = [c2.post(f"/api/files/{fid2}/chat", json={"message": "berapa total baris pada Data Master?", "lang": "id"}).status_code for _ in range(6)]
print("   status 6 chat berturut:", codes)
ok(codes[:4] == [200] * 4 and codes[4:] == [429, 429], "chat ke-5 dst -> 429")
r = c2.post(f"/api/files/{fid2}/chat", json={"message": "halo", "lang": "id"}); j = r.get_json()
ok(r.status_code == 429 and r.headers.get("Retry-After") and "Coba lagi" in j["error"], "429 punya Retry-After & pesan ramah: " + j["error"])
os.environ.pop("RL_CHAT_USER_MIN")
# sesi lain, IP lain tidak ikut terblokir
c3 = app.test_client(); c3.environ_base["REMOTE_ADDR"] = "10.0.0.77"
r = c3.post("/api/upload", data={"file": (io.BytesIO(raw), "w3.xls")}, content_type="multipart/form-data"); f3 = r.get_json()["file_id"]
ok(c3.post(f"/api/files/{f3}/chat", json={"message": "berapa total baris pada Data Master?", "lang": "id"}).status_code == 200, "user lain tidak terdampak")

# --- saklar AI ---
os.environ["AI_ENABLED"] = "0"; calls.clear()
r = c3.post(f"/api/files/{f3}/chat", json={"message": "halo", "lang": "id"})
ok(r.status_code == 503 and r.get_json().get("ai_disabled") and not calls, "AI_ENABLED=0 -> 503 tanpa memanggil LLM")
r = c3.get(f"/api/files/{f3}/recap?sheet=Psikologi ( S1 )"); ok(r.status_code == 200, "rekap (non-AI) tetap jalan saat AI mati")
os.environ.pop("AI_ENABLED")

# --- polish: validasi ukuran deck + rate limit ---
big = {"title": "t", "slides": [{"title": "s%d" % i} for i in range(31)]}
r = c3.post(f"/api/files/{f3}/export/slides/polish", json={"deck": big}); ok(r.status_code == 400 and "terlalu besar" in r.get_json()["error"], "deck >30 slide ditolak")
r = c3.post(f"/api/files/{f3}/export/slides/polish", json={"deck": {}}); ok(r.status_code == 400, "deck kosong ditolak sebelum menghitung kuota")

# --- batas data ---
os.environ["MAX_FILES_PER_OWNER"] = "1"; excel_engine.MAX_FILES_PER_OWNER = 1
c4 = app.test_client(); c4.environ_base["REMOTE_ADDR"] = "10.0.0.88"
r1 = c4.post("/api/upload", data={"file": (io.BytesIO(raw), "a.xls")}, content_type="multipart/form-data")
import openpyxl
wb = openpyxl.Workbook(); ws = wb.active; ws.append(["a", "b"]); ws.append([1, 2]); b = io.BytesIO(); wb.save(b)
r2 = c4.post("/api/upload", data={"file": (io.BytesIO(b.getvalue()), "b.xlsx")}, content_type="multipart/form-data")
ok(r1.status_code == 201 and r2.status_code == 400 and "Batas 1 file" in r2.get_json()["error"], "batas file aktif per pemilik: " + r2.get_json()["error"])
excel_engine.MAX_FILES_PER_OWNER = 30
excel_engine.MAX_ROWS_PER_SHEET = 100
c5 = app.test_client(); c5.environ_base["REMOTE_ADDR"] = "10.0.0.99"
r = c5.post("/api/upload", data={"file": (io.BytesIO(raw), "c.xls")}, content_type="multipart/form-data")
ok(r.status_code == 400 and "baris" in r.get_json()["error"], "sheet >100 baris ditolak: " + r.get_json()["error"])
cc = db.count_files(str(list(c5.session_transaction().__enter__().values())[0])) if False else None
conn = db.get_conn(); cur = conn.cursor(); cur.execute("select count(*) from files where filename='c.xls'"); ok(cur.fetchone()[0] == 0, "file yang ditolak tidak tertinggal di database"); conn.close()
excel_engine.MAX_ROWS_PER_SHEET = 50000

# --- allowlist link ---
good = ["https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOp/edit#gid=0", "https://drive.google.com/file/d/1AbCdEfGhIjKlMnOp/view?usp=sharing", "https://drive.google.com/open?id=1AbCdEfGhIjKlMnOp"]
bad = ["http://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOp/edit", "https://evil.com/a.xlsx", "https://docs.google.com.evil.com/spreadsheets/d/1AbCdEfGhIjKlMnOp",
       "https://169.254.169.254/latest/meta-data", "https://user@docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOp", "https://docs.google.com:8443/spreadsheets/d/1AbCdEfGhIjKlMnOp",
       "https://docs.google.com/document/d/1AbCdEfGhIjKlMnOp/edit", "https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOp", "https://localhost/x.xlsx"]
for u in good:
    f, n = excel_engine._resolve_spreadsheet_url(u); ok(f.startswith(("https://docs.google.com/spreadsheets/d/", "https://drive.google.com/uc?export=download&id=")), "link diterima -> " + f[:70])
for u in bad:
    try: excel_engine._resolve_spreadsheet_url(u); ok(False, "harus ditolak: " + u)
    except ValueError: ok(True, "link ditolak: " + u[:60])
ok(not excel_engine._redirect_ok("https://evil.com/x") and not excel_engine._redirect_ok("http://docs.google.com/x") and excel_engine._redirect_ok("https://doc-0c-8s-sheets.googleusercontent.com/export/x"), "validasi hop redirect")
# batas ukuran unduhan (server HTTP lokal tidak lolos allowlist, jadi uji fungsi dengan transport palsu)
class Resp:
    def __init__(s, code, headers, chunks): s.status_code, s.headers, s._c = code, headers, chunks
    def raise_for_status(s): pass
    def iter_content(s, n): yield from s._c
    def close(s): pass
seq = [Resp(302, {"Location": "https://doc-0c-8s-sheets.googleusercontent.com/export/x"}, []), Resp(200, {}, [b"a" * 700_000] * 3)]
excel_engine.requests.get = lambda url, **kw: seq.pop(0)
try: excel_engine._download_limited("https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOp/export?format=xlsx", 1024 * 1024); ok(False, "harus kena batas ukuran")
except ValueError as e: ok("terlalu besar" in str(e), "unduhan >1MB dihentikan di tengah jalan (redirect Google diikuti)")
seq = [Resp(302, {"Location": "https://evil.com/x"}, [])]
try: excel_engine._download_limited("https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOp/export?format=xlsx", 1024); ok(False, "harus menolak redirect")
except ValueError as e: ok("tidak diizinkan" in str(e), "redirect ke host asing ditolak")
r = c.post("/api/upload-url", json={"url": "https://evil.com/a.xlsx"}); ok(r.status_code == 400 and "Google" in r.get_json()["error"], "/api/upload-url menolak host non-Google: " + r.get_json()["error"])

# --- auth rate limit ---
os.environ["RL_AUTH_REGISTER_IP_MIN"] = "2"
ca = app.test_client(); ca.environ_base["REMOTE_ADDR"] = "10.9.9.9"
html = ca.get("/daftar").get_data(as_text=True); import re; csrf = re.search(r'data-csrf="([^"]+)"', html).group(1)
codes = [ca.post("/auth/register", json={"name": ""}, headers={"X-CSRF-Token": csrf}).status_code for _ in range(4)]
print("   status register berturut:", codes); ok(codes == [400, 400, 429, 429], "pendaftaran massal per IP -> 429")
j = ca.post("/auth/register", json={"name": ""}, headers={"X-CSRF-Token": csrf}).get_json(); ok(j["error"] == "rate_limited" and j["minutes"] >= 1, "kode rate_limited + menit: %s" % j)
os.environ.pop("RL_AUTH_REGISTER_IP_MIN")

# --- laporan pemakaian ---
r = c.get("/api/usage"); j = r.get_json(); print("   /api/usage:", json.dumps(j)[:200]); ok(r.status_code == 200 and j["credits"] >= 1, "/api/usage ringkas pemakaian bulan ini")
ok(app.test_client().get("/api/admin/usage").status_code == 404, "/api/admin/usage tanpa token -> 404")
ok(app.test_client().get("/api/admin/usage", headers={"X-Admin-Token": "salah"}).status_code == 404, "token salah -> 404")
r = app.test_client().get("/api/admin/usage?days=7", headers={"X-Admin-Token": "adm-secret"}); j = r.get_json()
print("   admin:", json.dumps(j)[:300]); ok(r.status_code == 200 and j["users"] and j["totals"], "laporan admin per user & per model")
print("\nSEMUA TES LULUS")
