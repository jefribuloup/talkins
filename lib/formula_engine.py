"""Saran rumus untuk tab 'Rumus' di Meja Data.

Menerima blok sel yang dipilih user (matriks 2D), lalu mengembalikan daftar rumus
Excel yang masuk akal beserta pratinjau hasilnya. Semua dihitung di server dari
salinan data yang dikirim; tidak ada file atau working_data yang diubah.

Pemakaian di api/index.py:

    from lib import formula_engine
    formula_engine.register_routes(app)
"""
import math
import re
import statistics
from collections import Counter
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP

MAX_CELLS = 20000
MAX_PREVIEW = 5

CAT_SUMMARY = "Ringkasan angka"
CAT_COUNT = "Hitung isi"
CAT_TEXT = "Teks"
CAT_DATE = "Tanggal"
CAT_LOOKUP = "Pencarian"
CAT_DERIVED = "Kolom turunan"
CATEGORY_ORDER = [CAT_SUMMARY, CAT_COUNT, CAT_DERIVED, CAT_TEXT, CAT_DATE, CAT_LOOKUP]

_DATE_FORMATS = ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d")


# ---------- alamat sel ----------

def col_letter(n):
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def a1(row, col, absolute=False):
    if absolute:
        return f"${col_letter(col)}${row}"
    return f"{col_letter(col)}{row}"


def ref(r1, c1, r2, c2, absolute=False):
    first, last = a1(r1, c1, absolute), a1(r2, c2, absolute)
    return first if first == last else f"{first}:{last}"


# ---------- pengenalan tipe nilai ----------

def _is_blank(v):
    if v is None:
        return True
    if isinstance(v, float) and math.isnan(v):
        return True
    return isinstance(v, str) and not v.strip()


def _to_num(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v) if math.isfinite(v) else None
    if isinstance(v, str):
        try:
            x = float(v.strip())
        except ValueError:
            return None
        return x if math.isfinite(x) else None
    return None


def _to_date(v):
    if isinstance(v, datetime):
        return v
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day)
    if isinstance(v, str) and _to_num(v) is None:
        s = v.strip()
        for fmt in _DATE_FORMATS:
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                pass
        if "-" in s or "T" in s:
            try:
                return datetime.fromisoformat(s)
            except ValueError:
                pass
    return None


def _kind(v):
    if _is_blank(v):
        return "blank"
    if _to_num(v) is not None:
        return "num"
    if _to_date(v) is not None:
        return "date"
    return "text"


# ---------- format keluaran ----------

def _fmt(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, (int, float)):
        s = f"{round(float(v), 4):.4f}".rstrip("0").rstrip(".")
        return "0" if s in ("-0", "") else s
    return str(v)


def _q(s):
    """Kutip string untuk dipakai di dalam rumus."""
    return '"' + str(s).replace('"', '""') + '"'


def _criteria(s):
    """Escape karakter wildcard agar COUNTIF/SUMIF mencocokkan teks apa adanya."""
    return _q(re.sub(r"([*?~])", r"~\1", str(s)))


def _round_half_up(x, nd=0):
    return float(Decimal(repr(x)).quantize(Decimal(1).scaleb(-nd), rounding=ROUND_HALF_UP))


def _same(a, b):
    na, nb = _to_num(a), _to_num(b)
    if na is not None and nb is not None:
        return na == nb
    if na is not None or nb is not None:
        return False
    return str(a).strip().casefold() == str(b).strip().casefold()


def _proper(s):
    return re.sub(r"[^\W\d_]+", lambda m: m.group(0).capitalize(), s)


# ---------- pembentuk saran ----------

def _single(sid, name, cat, formula, desc, result):
    return {"id": sid, "name": name, "category": cat, "formula": formula,
            "description": desc, "kind": "single", "result": _fmt(result)}


def _per_row(sid, name, cat, formula, desc, pairs):
    return {"id": sid, "name": name, "category": cat, "formula": formula,
            "description": desc, "kind": "per_row",
            "preview": [{"input": _fmt(i), "output": _fmt(o)} for i, o in pairs]}


def _normalize(rows):
    grid = [list(r) if isinstance(r, (list, tuple)) else [r] for r in rows]
    width = max((len(r) for r in grid), default=0)
    return [r + [None] * (width - len(r)) for r in grid]


def _pick_col(grid, kind):
    """Kolom pertama yang mayoritas isinya bertipe `kind`."""
    for j in range(len(grid[0])):
        vals = [row[j] for row in grid if not _is_blank(row[j])]
        if vals and sum(1 for v in vals if _kind(v) == kind) / len(vals) >= 0.5:
            return j
    return None


def _col_values(grid, j, kind, limit):
    return [row[j] for row in grid if _kind(row[j]) == kind][:limit]


def analyze_selection(rows, start_row=2, start_col=1, sep=",", max_preview=MAX_PREVIEW):
    """Kembalikan {'selection': {...}, 'suggestions': [...]} untuk blok sel `rows`.

    start_row/start_col: posisi sel kiri-atas pilihan di lembar Excel hasil ekspor
    (1-based), dipakai untuk menulis alamat seperti B2:B10.
    sep: pemisah argumen rumus; pakai ";" untuk Excel berlokal Indonesia.
    """
    sep = sep if sep in (",", ";") else ","
    grid = _normalize(rows)
    nr = len(grid)
    nc = len(grid[0]) if nr else 0
    if nr == 0 or nc == 0:
        return {"selection": {"ref": "", "rows": 0, "cols": 0}, "suggestions": []}

    def call(name, *args):
        return f"={name}({sep.join(args)})"

    r1, c1 = start_row, start_col
    r2, c2 = r1 + nr - 1, c1 + nc - 1
    full = ref(r1, c1, r2, c2)

    kinds = [[_kind(v) for v in row] for row in grid]
    flat = [v for row in grid for v in row]
    flat_kinds = [k for row in kinds for k in row]
    nonblank = [v for v, k in zip(flat, flat_kinds) if k != "blank"]
    nums = [_to_num(v) for v, k in zip(flat, flat_kinds) if k == "num"]
    n_blank = flat_kinds.count("blank")
    out = []

    # --- ringkasan angka (seluruh pilihan) ---
    if nums and len(nums) / len(nonblank) >= 0.5:
        out.append(_single("sum", "SUM", CAT_SUMMARY, call("SUM", full),
                           "Menjumlahkan semua angka di pilihan.", sum(nums)))
        out.append(_single("average", "AVERAGE", CAT_SUMMARY, call("AVERAGE", full),
                           "Rata-rata semua angka di pilihan.", sum(nums) / len(nums)))
        out.append(_single("min", "MIN", CAT_SUMMARY, call("MIN", full),
                           "Angka terkecil.", min(nums)))
        out.append(_single("max", "MAX", CAT_SUMMARY, call("MAX", full),
                           "Angka terbesar.", max(nums)))
        out.append(_single("median", "MEDIAN", CAT_SUMMARY, call("MEDIAN", full),
                           "Nilai tengah setelah diurutkan.", statistics.median(nums)))
        if len(nums) >= 2:
            out.append(_single("stdev", "STDEV.S", CAT_SUMMARY, call("STDEV.S", full),
                               "Simpangan baku sampel: seberapa menyebar angkanya.",
                               statistics.stdev(nums)))

    # --- hitung isi ---
    out.append(_single("counta", "COUNTA", CAT_COUNT, call("COUNTA", full),
                       "Jumlah sel yang tidak kosong.", len(nonblank)))
    if nums:
        out.append(_single("count", "COUNT", CAT_COUNT, call("COUNT", full),
                           "Jumlah sel yang berisi angka.", len(nums)))
    if n_blank:
        out.append(_single("countblank", "COUNTBLANK", CAT_COUNT, call("COUNTBLANK", full),
                           "Jumlah sel kosong; berguna untuk mencari data yang belum terisi.", n_blank))

    non_num = [v for v, k in zip(flat, flat_kinds) if k in ("text", "date")]
    if non_num:
        counts = Counter(str(v).strip().casefold() for v in non_num)
        top_key, top_n = counts.most_common(1)[0]
        top_label = next(str(v).strip() for v in non_num if str(v).strip().casefold() == top_key)
        if top_n >= 2:
            out.append(_single("countif", "COUNTIF", CAT_COUNT,
                               call("COUNTIF", full, _criteria(top_label)),
                               f"Berapa kali {_q(top_label)} muncul (nilai paling sering di pilihan).",
                               top_n))
    if len(nonblank) >= 2:
        distinct = {(_to_num(v) if _kind(v) == "num" else str(v).strip().casefold())
                    for v in nonblank}
        out.append(_single("unique", "COUNTA + UNIQUE", CAT_COUNT,
                           "=COUNTA(UNIQUE(" + full + "))",
                           "Jumlah nilai yang berbeda. Perlu Excel 365 atau Google Sheets.",
                           len(distinct)))

    # --- kolom turunan dari kolom angka ---
    jn = _pick_col(grid, "num")
    if jn is not None:
        nvals = _col_values(grid, jn, "num", 10 ** 9)
        cc = c1 + jn
        first = a1(r1, cc)
        col_abs = ref(r1, cc, r2, cc, True)
        pv = [_to_num(v) for v in nvals][:max_preview]
        if any(not float(x).is_integer() for x in (_to_num(v) for v in nvals)):
            out.append(_per_row("round", "ROUND", CAT_DERIVED, call("ROUND", first, "0"),
                                "Membulatkan ke bilangan bulat. Isi ke bawah untuk seluruh kolom.",
                                [(x, _round_half_up(x, 0)) for x in pv]))
        if len(nvals) >= 2:
            avg = sum(_to_num(v) for v in nvals) / len(nvals)
            out.append(_per_row(
                "if", "IF + AVERAGE", CAT_DERIVED,
                call("IF", f"{first}>AVERAGE({col_abs})", _q("Di atas rata-rata"), _q("Sama/di bawah")),
                "Memberi label tiap baris terhadap rata-rata kolom.",
                [(x, "Di atas rata-rata" if x > avg else "Sama/di bawah") for x in pv]))
            allnums = [_to_num(v) for v in nvals]
            out.append(_per_row(
                "rank", "RANK", CAT_DERIVED, call("RANK", first, col_abs, "0"),
                "Peringkat tiap nilai, 1 untuk yang terbesar.",
                [(x, 1 + sum(1 for y in allnums if y > x)) for x in pv]))
            total = sum(allnums)
            if total > 0 and all(x >= 0 for x in allnums):
                out.append(_per_row(
                    "share", "Persentase dari total", CAT_DERIVED,
                    call("TEXT", f"{first}/SUM({col_abs})", _q("0.0%")),
                    "Porsi tiap baris terhadap jumlah kolom.",
                    [(x, f"{x / total * 100:.1f}%") for x in pv]))

    # --- teks ---
    jt = _pick_col(grid, "text")
    if jt is not None:
        tvals = [str(v) for v in _col_values(grid, jt, "text", 10 ** 9)]
        first = a1(r1, c1 + jt)
        pv = tvals[:max_preview]
        out.append(_per_row("upper", "UPPER", CAT_TEXT, call("UPPER", first),
                            "Mengubah semua huruf jadi kapital.", [(s, s.upper()) for s in pv]))
        out.append(_per_row("lower", "LOWER", CAT_TEXT, call("LOWER", first),
                            "Mengubah semua huruf jadi kecil.", [(s, s.lower()) for s in pv]))
        out.append(_per_row("proper", "PROPER", CAT_TEXT, call("PROPER", first),
                            "Huruf pertama tiap kata jadi kapital.", [(s, _proper(s)) for s in pv]))
        if any(re.sub(" +", " ", s).strip(" ") != s for s in tvals):
            out.append(_per_row("trim", "TRIM", CAT_TEXT, call("TRIM", first),
                                "Menghapus spasi berlebih di awal, akhir, dan tengah teks.",
                                [(s, re.sub(" +", " ", s).strip(" ")) for s in pv]))
        out.append(_per_row("len", "LEN", CAT_TEXT, call("LEN", first),
                            "Jumlah karakter tiap sel.", [(s, len(s)) for s in pv]))

    if nc >= 2 and any(k in ("text", "date") for k in flat_kinds):
        rows_with_data = [row for row in grid if sum(1 for v in row if not _is_blank(v)) >= 2]
        if rows_with_data:
            fr = ref(r1, c1, r1, c2)
            pairs = [(" | ".join(_fmt(v) for v in row),
                      " ".join(_fmt(v) for v in row if not _is_blank(v)))
                     for row in rows_with_data[:max_preview]]
            out.append(_per_row("textjoin", "TEXTJOIN", CAT_TEXT,
                                call("TEXTJOIN", _q(" "), "TRUE", fr),
                                "Menggabungkan isi sel satu baris, melewati sel kosong. Excel 2019 ke atas.",
                                pairs))

    # --- tanggal ---
    jd = _pick_col(grid, "date")
    if jd is not None:
        dvals = [_to_date(v) for v in _col_values(grid, jd, "date", 10 ** 9)][:max_preview]
        first = a1(r1, c1 + jd)
        out.append(_per_row("year", "YEAR", CAT_DATE, call("YEAR", first),
                            "Mengambil tahun dari tanggal.", [(d, d.year) for d in dvals]))
        out.append(_per_row("month", "MONTH", CAT_DATE, call("MONTH", first),
                            "Mengambil bulan (1-12).", [(d, d.month) for d in dvals]))
        out.append(_per_row("day", "DAY", CAT_DATE, call("DAY", first),
                            "Mengambil tanggal (1-31).", [(d, d.day) for d in dvals]))

    # --- SUMIF: kolom teks sebagai kunci, kolom angka sebagai nilai ---
    if nc >= 2 and jt is not None and jn is not None and jt != jn:
        keys = [str(row[jt]).strip() for row in grid if _kind(row[jt]) == "text"]
        if keys:
            top_key, top_n = Counter(k.casefold() for k in keys).most_common(1)[0]
            label = next(k for k in keys if k.casefold() == top_key)
            total = sum(_to_num(row[jn]) for row in grid
                        if _kind(row[jt]) == "text" and str(row[jt]).strip().casefold() == top_key
                        and _kind(row[jn]) == "num")
            kr = ref(r1, c1 + jt, r2, c1 + jt)
            vr = ref(r1, c1 + jn, r2, c1 + jn)
            out.append(_single("sumif", "SUMIF", CAT_SUMMARY, call("SUMIF", kr, _criteria(label), vr),
                               f"Jumlah kolom {col_letter(c1 + jn)} untuk baris yang kolom "
                               f"{col_letter(c1 + jt)}-nya {_q(label)}.", total))

    # --- VLOOKUP / HLOOKUP ---
    if nr >= 2 and nc >= 2 and nr >= nc:
        idx = next((i for i in range(nr - 1, -1, -1) if not _is_blank(grid[i][0])), None)
        if idx is not None:
            key = grid[idx][0]
            hit = next((row for row in grid if not _is_blank(row[0]) and _same(row[0], key)), None)
            res = hit[1] if hit is not None else None
            result = "#N/A" if hit is None else ("0" if _is_blank(res) else res)
            out.append(_single("vlookup", "VLOOKUP", CAT_LOOKUP,
                               call("VLOOKUP", a1(r1 + idx, c1), full, "2", "FALSE"),
                               f"Mencari isi {a1(r1 + idx, c1)} di kolom pertama pilihan, "
                               "lalu mengambil nilai kolom ke-2 di baris yang cocok. "
                               "Jika ada duplikat, yang dipakai kecocokan pertama.",
                               result))
    if nr >= 2 and nc >= 2 and nc >= nr:
        jdx = next((j for j in range(nc - 1, -1, -1) if not _is_blank(grid[0][j])), None)
        if jdx is not None:
            key = grid[0][jdx]
            hitj = next((j for j in range(nc) if not _is_blank(grid[0][j]) and _same(grid[0][j], key)), None)
            res = grid[1][hitj] if hitj is not None else None
            result = "#N/A" if hitj is None else ("0" if _is_blank(res) else res)
            out.append(_single("hlookup", "HLOOKUP", CAT_LOOKUP,
                               call("HLOOKUP", a1(r1, c1 + jdx), full, "2", "FALSE"),
                               f"Mencari isi {a1(r1, c1 + jdx)} di baris pertama pilihan, "
                               "lalu mengambil nilai baris ke-2 di kolom yang cocok.",
                               result))

    order = {c: i for i, c in enumerate(CATEGORY_ORDER)}
    out.sort(key=lambda s: order.get(s["category"], 99))  # sort stabil: urutan dalam kategori terjaga

    return {
        "selection": {
            "ref": full, "rows": nr, "cols": nc,
            "numeric": len(nums), "blank": n_blank,
            "text": sum(1 for k in flat_kinds if k == "text"),
            "date": sum(1 for k in flat_kinds if k == "date"),
        },
        "suggestions": out,
    }


def register_routes(app):
    """Daftarkan POST /api/formula-suggest ke aplikasi Flask."""
    from flask import jsonify, request

    @app.route("/api/formula-suggest", methods=["POST"])
    def formula_suggest():
        data = request.get_json(silent=True) or {}
        rows = data.get("rows")
        if not isinstance(rows, list) or not rows or not all(isinstance(r, list) for r in rows):
            return jsonify({"error": "Pilihan kosong atau formatnya salah."}), 400
        if len(rows) * max(len(r) for r in rows) > MAX_CELLS:
            return jsonify({"error": f"Pilihan terlalu besar (maksimal {MAX_CELLS} sel)."}), 413
        try:
            start_row = max(1, int(data.get("start_row", 2)))
            start_col = max(1, int(data.get("start_col", 1)))
        except (TypeError, ValueError):
            return jsonify({"error": "start_row/start_col harus angka."}), 400
        return jsonify(analyze_selection(rows, start_row, start_col, data.get("sep", ",")))
