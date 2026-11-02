"""Evaluator rumus Excel untuk sel yang diisi user langsung di grid.

Alur: user menyalin rumus dari tab 'Rumus' (mis. =SUM(B2:B9) atau =SUM(B2;B9) untuk Excel
berlokal Indonesia), menempelnya ke sel mana pun (termasuk sel kosong), lalu server
menghitung hasilnya dari working_data dan menyimpannya sebagai nilai sel.

Mendukung seluruh fungsi yang dihasilkan formula_engine (SUM, AVERAGE, MIN, MAX, MEDIAN,
STDEV.S, COUNT, COUNTA, COUNTBLANK, COUNTIF, SUMIF, UNIQUE, ROUND, IF, RANK, TEXT, UPPER,
LOWER, PROPER, TRIM, LEN, TEXTJOIN, YEAR, MONTH, DAY, VLOOKUP, HLOOKUP) plus beberapa fungsi
umum (IFERROR, ABS, INT, ROUNDUP, ROUNDDOWN, AND, OR, NOT, CONCAT, CONCATENATE, LEFT, RIGHT,
MID, STDEV). Argumen boleh dipisah "," atau ";".

Alamat sel = posisi di grid (A1 = baris 1 kolom A), sama persis dengan angka di gutter tabel.
Referensi antar-sheet belum didukung.

Modul ini murni Python (tanpa database/Flask) supaya mudah dites.
"""
import math
import re
import statistics
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, ROUND_UP, ROUND_DOWN

from lib.formula_engine import _to_date, _proper

MAX_FORMULA_LEN = 2000
MAX_RANGE_CELLS = 2_000_000
MAX_ROW = 1_048_576
MAX_COL = 16_384
MAX_DEPS = 12


class FormulaSyntaxError(ValueError):
    """Rumus tidak bisa dibaca / memakai fungsi yang belum didukung (rumus ditolak)."""


class XlError(Exception):
    """Galat nilai ala Excel (#DIV/0!, #N/A, ...). Disimpan sebagai teks di sel, seperti Excel."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


class XlErr:
    """Nilai galat yang tersimpan di sel hasil rumus (bukan exception)."""

    def __init__(self, code):
        self.code = code

    def __repr__(self):
        return self.code


# ---------- alamat ----------

def letters_to_idx(letters):
    n = 0
    for ch in letters.upper():
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def idx_to_letters(idx):
    n, out = idx + 1, ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        out = chr(65 + rem) + out
    return out


# ---------- tokenizer ----------

_NUM_RE = re.compile(r"(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")
_STR_RE = re.compile(r'"(?:[^"]|"")*"')
_RANGE_RE = re.compile(r"\$?([A-Za-z]{1,3})\$?(\d+):\$?([A-Za-z]{1,3})\$?(\d+)(?![A-Za-z0-9_(.])")
_COLRANGE_RE = re.compile(r"\$?([A-Za-z]{1,3}):\$?([A-Za-z]{1,3})(?![A-Za-z0-9_(.])")
_REF_RE = re.compile(r"\$?([A-Za-z]{1,3})\$?(\d+)(?![A-Za-z0-9_(.])")
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*")
_OP_RE = re.compile(r"<=|>=|<>|[+\-*/^&=<>%(),;]")


def _tokenize(src):
    tokens, i, n = [], 0, len(src)
    while i < n:
        ch = src[i]
        if ch.isspace():
            i += 1
            continue
        if ch == "!" or ch == "'":
            raise FormulaSyntaxError("Referensi antar-sheet belum didukung.")
        if ch in "{}[]":
            raise FormulaSyntaxError("Array konstan / referensi tabel belum didukung.")
        m = _STR_RE.match(src, i)
        if m:
            tokens.append(("str", m.group(0)[1:-1].replace('""', '"')))
            i = m.end()
            continue
        m = _NUM_RE.match(src, i)
        if m and not (src[i].isalpha()):
            tokens.append(("num", float(m.group(0))))
            i = m.end()
            continue
        if ch.isalpha() or ch in "_$":
            m = _RANGE_RE.match(src, i)
            if m:
                c1, r1, c2, r2 = m.groups()
                tokens.append(("range", (int(r1) - 1, letters_to_idx(c1), int(r2) - 1, letters_to_idx(c2)),
                               m.group(0).replace("$", "").upper()))
                i = m.end()
                continue
            m = _COLRANGE_RE.match(src, i)
            if m:
                c1, c2 = m.groups()
                tokens.append(("colrange", (letters_to_idx(c1), letters_to_idx(c2)),
                               m.group(0).replace("$", "").upper()))
                i = m.end()
                continue
            m = _REF_RE.match(src, i)
            if m:
                c, r = m.groups()
                tokens.append(("ref", (int(r) - 1, letters_to_idx(c)), m.group(0).replace("$", "").upper()))
                i = m.end()
                continue
            m = _IDENT_RE.match(src, i)
            if m:
                name = m.group(0)
                j = m.end()
                while j < n and src[j].isspace():
                    j += 1
                if j < n and src[j] == "(":
                    tokens.append(("func", name.upper()))
                elif name.upper() in ("TRUE", "FALSE"):
                    tokens.append(("bool", name.upper() == "TRUE"))
                elif j < n and src[j] == "!":
                    raise FormulaSyntaxError("Referensi antar-sheet belum didukung.")
                else:
                    raise FormulaSyntaxError(f"Nama tidak dikenal: {name}")
                i = m.end()
                continue
            raise FormulaSyntaxError(f"Karakter tidak valid: {ch}")
        m = _OP_RE.match(src, i)
        if m:
            tokens.append(("op", m.group(0)))
            i = m.end()
            continue
        raise FormulaSyntaxError(f"Karakter tidak valid: {ch}")
    return tokens


# ---------- parser (precedence Excel: perbandingan < & < +- < */ < ^ < unary < %) ----------

_CMP_OPS = ("=", "<>", "<", ">", "<=", ">=")


class _Parser:
    def __init__(self, tokens):
        self.t = tokens
        self.i = 0

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else (None, None)

    def take(self):
        tok = self.peek()
        self.i += 1
        return tok

    def is_op(self, *ops):
        k, v = self.peek()[:2]
        return k == "op" and v in ops

    def parse(self):
        node = self.cmp()
        if self.i < len(self.t):
            raise FormulaSyntaxError("Ada bagian rumus yang tidak bisa dibaca.")
        return node

    def cmp(self):
        left = self.concat()
        while self.is_op(*_CMP_OPS):
            op = self.take()[1]
            left = ("bin", op, left, self.concat())
        return left

    def concat(self):
        left = self.add()
        while self.is_op("&"):
            self.take()
            left = ("bin", "&", left, self.add())
        return left

    def add(self):
        left = self.mul()
        while self.is_op("+", "-"):
            op = self.take()[1]
            left = ("bin", op, left, self.mul())
        return left

    def mul(self):
        left = self.pow()
        while self.is_op("*", "/"):
            op = self.take()[1]
            left = ("bin", op, left, self.pow())
        return left

    def pow(self):
        left = self.unary()
        while self.is_op("^"):
            self.take()
            left = ("bin", "^", left, self.unary())
        return left

    def unary(self):
        if self.is_op("-"):
            self.take()
            return ("neg", self.unary())
        if self.is_op("+"):
            self.take()
            return self.unary()
        return self.postfix()

    def postfix(self):
        node = self.primary()
        while self.is_op("%"):
            self.take()
            node = ("pct", node)
        return node

    def primary(self):
        kind, val, *rest = self.take() if self.i < len(self.t) else (None, None)
        if kind == "num":
            return ("num", val)
        if kind == "str":
            return ("str", val)
        if kind == "bool":
            return ("bool", val)
        if kind == "ref":
            return ("ref", val[0], val[1], rest[0])
        if kind == "range":
            return ("range", val[0], val[1], val[2], val[3], rest[0])
        if kind == "colrange":
            return ("colrange", val[0], val[1], rest[0])
        if kind == "func":
            if not self.is_op("("):
                raise FormulaSyntaxError("Kurung buka hilang setelah nama fungsi.")
            self.take()
            args = []
            if self.is_op(")"):
                self.take()
                return ("call", val, args)
            while True:
                # argumen kosong (mis. IF(A1;;B1)) dianggap nilai kosong
                if self.is_op(",", ";"):
                    args.append(("blank",))
                else:
                    args.append(self.cmp())
                if self.is_op(",", ";"):
                    self.take()
                    if self.is_op(")"):
                        args.append(("blank",))
                        self.take()
                        break
                    continue
                if self.is_op(")"):
                    self.take()
                    break
                raise FormulaSyntaxError("Kurung tutup atau pemisah argumen hilang.")
            return ("call", val, args)
        if kind == "op" and val == "(":
            node = self.cmp()
            if not self.is_op(")"):
                raise FormulaSyntaxError("Kurung tutup hilang.")
            self.take()
            return node
        raise FormulaSyntaxError("Rumus tidak lengkap atau ada operator di tempat yang salah.")


def parse(formula):
    """Baca rumus (diawali '=') jadi pohon. Raise FormulaSyntaxError kalau tidak valid."""
    src = str(formula).strip()
    if not src.startswith("="):
        raise FormulaSyntaxError("Rumus harus diawali tanda =")
    src = src[1:]
    if not src.strip():
        raise FormulaSyntaxError("Rumus kosong.")
    if len(src) > MAX_FORMULA_LEN:
        raise FormulaSyntaxError(f"Rumus terlalu panjang (maksimal {MAX_FORMULA_LEN} karakter).")
    try:
        ast = _Parser(_tokenize(src)).parse()
    except RecursionError:
        raise FormulaSyntaxError("Rumus terlalu bersarang.")
    _check_functions(ast)
    return ast


def _check_functions(node):
    if node[0] == "call":
        if node[1] not in _FUNCS and node[1] not in _LAZY:
            raise FormulaSyntaxError(f"Fungsi {node[1]} belum didukung.")
        for a in node[2]:
            _check_functions(a)
    elif node[0] in ("neg", "pct"):
        _check_functions(node[1])
    elif node[0] == "bin":
        _check_functions(node[2])
        _check_functions(node[3])


def dependencies(ast, nrows=None):
    """Daftar 'bahan' rumus untuk kartu di tab Rumus: [{ref, cols, rows}] (rows = nomor baris 1-based)."""
    deps, seen = [], set()

    def add(ref, c1, c2, r1, r2):
        if ref in seen or len(deps) >= MAX_DEPS:
            return
        seen.add(ref)
        lo, hi = min(c1, c2), max(c1, c2)
        cols = [idx_to_letters(c) for c in range(lo, min(hi, lo + 60) + 1)]
        rows = None if r1 is None else [min(r1, r2) + 1, max(r1, r2) + 1]
        deps.append({"ref": ref, "cols": cols, "rows": rows})

    def walk(n):
        k = n[0]
        if k == "ref":
            add(n[3], n[2], n[2], n[1], n[1])
        elif k == "range":
            add(n[5], n[2], n[4], n[1], n[3])
        elif k == "colrange":
            add(n[3], n[1], n[2], 0 if nrows else None, max(nrows or 1, 1) - 1)
        elif k == "call":
            for a in n[2]:
                walk(a)
        elif k in ("neg", "pct"):
            walk(n[1])
        elif k == "bin":
            walk(n[2])
            walk(n[3])

    walk(ast)
    return deps


# ---------- konteks sheet ----------

class Sheet:
    """Akses sel + evaluasi rumus user secara rekursif (urutan ketergantungan otomatis benar,
    referensi melingkar terdeteksi)."""

    def __init__(self, records, user_formulas):
        self.records = records
        self.formulas = user_formulas      # {(r, c): "=..."}
        self.asts = {}
        self.cache = {}
        self.busy = set()
        self.nrows = len(records)
        self.ncols = max((len(r) for r in records), default=0)

    def cell(self, r, c):
        key = (r, c)
        if key in self.formulas:
            if key in self.cache:
                return self.cache[key]
            if key in self.busy:
                raise XlError("#REF!")  # referensi melingkar
            self.busy.add(key)
            try:
                try:
                    ast = self.asts.get(key) or self.asts.setdefault(key, parse(self.formulas[key]))
                    v = _clean(_scalar(_eval(ast, self)))
                except XlError as e:
                    v = XlErr(e.code)
                except FormulaSyntaxError:
                    v = XlErr("#NAME?")
                except RecursionError:
                    v = XlErr("#REF!")
            finally:
                self.busy.discard(key)
            self.cache[key] = v
            return v
        if 0 <= r < self.nrows:
            row = self.records[r]
            if 0 <= c < len(row):
                return row[c]
        return None

    def rng(self, r1, c1, r2, c2):
        r_lo, r_hi, c_lo, c_hi = min(r1, r2), max(r1, r2), min(c1, c2), max(c1, c2)
        if (r_hi - r_lo + 1) * (c_hi - c_lo + 1) > MAX_RANGE_CELLS:
            raise XlError("#VALUE!")
        return [[self.cell(r, c) for c in range(c_lo, c_hi + 1)] for r in range(r_lo, r_hi + 1)]


def evaluate_all(records, user_formulas):
    """Hitung semua rumus user. Return {(r, c): nilai_siap_simpan} (galat = teks '#DIV/0!' dst)."""
    sheet = Sheet(records, user_formulas)
    out = {}
    for key in user_formulas:
        v = sheet.cell(*key)
        out[key] = v.code if isinstance(v, XlErr) else v
    return out


def validate(formula, nrows=None):
    """Cek sintaks saja. Return (ast, deps); raise FormulaSyntaxError kalau salah."""
    ast = parse(formula)
    return ast, dependencies(ast, nrows)


def check(formula, records, user_formulas, nrows=None):
    """Validasi penuh sebelum rumus disimpan: sintaks + jumlah argumen fungsi (dry-run pada data
    sheet). Galat nilai seperti #DIV/0! BUKAN penolakan (Excel juga menampilkannya).
    Return deps; raise FormulaSyntaxError kalau rumus tidak valid."""
    ast, deps = validate(formula, nrows)
    sheet = Sheet(records, user_formulas)
    try:
        _eval(ast, sheet)
    except (XlError, RecursionError):
        pass
    return deps


# ---------- konversi nilai ----------

def _is_blank(v):
    return v is None or (isinstance(v, str) and v == "") or (isinstance(v, float) and math.isnan(v))


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _scalar(v):
    while isinstance(v, list):
        v = v[0][0] if v and v[0] else None
    if isinstance(v, XlErr):
        raise XlError(v.code)
    return v


def _clean(v):
    """Rapikan hasil akhir: bulatkan noise float (0.1+0.2 -> 0.3), angka bulat jadi int."""
    if isinstance(v, bool) or v is None or isinstance(v, str):
        return v
    if isinstance(v, (int, float)):
        if isinstance(v, float):
            if not math.isfinite(v):
                raise XlError("#NUM!")
            v = float(f"{v:.12g}")
            if v.is_integer() and abs(v) < 1e15:
                return int(v)
        return v
    return v


def _num(v):
    v = _scalar(v)
    if _is_blank(v):
        return 0.0
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if _is_num(v):
        return float(v)
    if isinstance(v, str):
        s = v.strip()
        try:
            return float(s)
        except ValueError:
            pass
        if s.endswith("%"):
            try:
                return float(s[:-1]) / 100
            except ValueError:
                pass
    raise XlError("#VALUE!")


def _text(v):
    v = _scalar(v)
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, float):
        if v.is_integer() and abs(v) < 1e15:
            return str(int(v))
        return f"{v:.15g}"
    return str(v)


def _bool(v):
    v = _scalar(v)
    if isinstance(v, bool):
        return v
    if _is_blank(v):
        return False
    if _is_num(v):
        return v != 0
    if isinstance(v, str):
        up = v.strip().upper()
        if up == "TRUE":
            return True
        if up == "FALSE":
            return False
    raise XlError("#VALUE!")


def _flat(a):
    """Meratakan argumen (skalar atau 2D) jadi daftar nilai mentah."""
    if isinstance(a, list):
        return [v for row in a for v in row]
    return [a]


def _numbers(args):
    """Angka untuk SUM/AVERAGE/... : sel di range hanya angka asli; argumen langsung boleh teks-angka."""
    out = []
    for a in args:
        if isinstance(a, list):
            for v in _flat(a):
                if isinstance(v, XlErr):
                    raise XlError(v.code)
                if _is_num(v):
                    out.append(float(v))
        else:
            if isinstance(a, XlErr):
                raise XlError(a.code)
            if _is_blank(a):
                continue
            out.append(_num(a))
    return out


def _to_dt(v):
    v = _scalar(v)
    if _is_num(v):
        if v < 0:
            raise XlError("#NUM!")
        return datetime(1899, 12, 30) + timedelta(days=int(v))
    d = _to_date(v) if not _is_blank(v) else None
    if d is None:
        raise XlError("#VALUE!")
    return d


# ---------- evaluasi ----------

def _compare(op, a, b):
    a, b = _scalar(a), _scalar(b)
    if _is_blank(a) and _is_blank(b):
        a = b = 0
    elif _is_blank(a):
        a = "" if isinstance(b, str) else (False if isinstance(b, bool) else 0)
    elif _is_blank(b):
        b = "" if isinstance(a, str) else (False if isinstance(a, bool) else 0)

    def rank(x):  # angka < teks < logika (urutan Excel)
        return 2 if isinstance(x, bool) else 1 if isinstance(x, str) else 0

    ka, kb = (rank(a), a.casefold() if isinstance(a, str) else a), (rank(b), b.casefold() if isinstance(b, str) else b)
    if op == "=":
        return ka == kb
    if op == "<>":
        return ka != kb
    if op == "<":
        return ka < kb
    if op == ">":
        return ka > kb
    if op == "<=":
        return ka <= kb
    return ka >= kb


def _eval(n, sh):
    k = n[0]
    if k == "num":
        return n[1]
    if k == "str":
        return n[1]
    if k == "bool":
        return n[1]
    if k == "blank":
        return None
    if k == "ref":
        return [[sh.cell(n[1], n[2])]]
    if k == "range":
        return sh.rng(n[1], n[2], n[3], n[4])
    if k == "colrange":
        last = max(sh.nrows - 1, 0)
        return sh.rng(0, n[1], last, n[2])
    if k == "neg":
        return -_num(_eval(n[1], sh))
    if k == "pct":
        return _num(_eval(n[1], sh)) / 100
    if k == "bin":
        op = n[1]
        a, b = _eval(n[2], sh), _eval(n[3], sh)
        if op in _CMP_OPS:
            return _compare(op, a, b)
        if op == "&":
            return _text(a) + _text(b)
        x, y = _num(a), _num(b)
        if op == "+":
            return x + y
        if op == "-":
            return x - y
        if op == "*":
            return x * y
        if op == "/":
            if y == 0:
                raise XlError("#DIV/0!")
            return x / y
        if op == "^":
            try:
                r = x ** y
            except (ZeroDivisionError, OverflowError):
                raise XlError("#NUM!")
            if isinstance(r, complex):
                raise XlError("#NUM!")
            return r
    if k == "call":
        name, args = n[1], n[2]
        if name in _LAZY:
            return _LAZY[name](args, sh)
        return _FUNCS[name]([_eval(a, sh) for a in args])
    raise FormulaSyntaxError("Rumus tidak bisa dibaca.")


# ---------- fungsi lazy (hanya evaluasi cabang yang dipakai) ----------

def _f_if(args, sh):
    if not 2 <= len(args) <= 3:
        raise FormulaSyntaxError("IF butuh 2 atau 3 argumen.")
    cond = _bool(_eval(args[0], sh))
    if cond:
        return _eval(args[1], sh)
    return _eval(args[2], sh) if len(args) == 3 else False


def _f_iferror(args, sh):
    if len(args) != 2:
        raise FormulaSyntaxError("IFERROR butuh 2 argumen.")
    try:
        v = _eval(args[0], sh)
        _scalar(v)  # raise XlError kalau hasilnya galat
        return v
    except XlError:
        return _eval(args[1], sh)


_LAZY = {"IF": _f_if, "IFERROR": _f_iferror}


# ---------- fungsi biasa ----------

def _need(args, lo, hi=None, name="Fungsi"):
    hi = lo if hi is None else hi
    if not lo <= len(args) <= hi:
        raise FormulaSyntaxError(f"{name} butuh {lo if lo == hi else f'{lo}-{hi}'} argumen.")


def _f_sum(a):
    return math.fsum(_numbers(a))


def _f_average(a):
    nums = _numbers(a)
    if not nums:
        raise XlError("#DIV/0!")
    return math.fsum(nums) / len(nums)


def _f_min(a):
    nums = _numbers(a)
    return min(nums) if nums else 0


def _f_max(a):
    nums = _numbers(a)
    return max(nums) if nums else 0


def _f_median(a):
    nums = _numbers(a)
    if not nums:
        raise XlError("#NUM!")
    return statistics.median(nums)


def _f_stdev(a):
    nums = _numbers(a)
    if len(nums) < 2:
        raise XlError("#DIV/0!")
    return statistics.stdev(nums)


def _f_count(a):
    c = 0
    for x in a:
        if isinstance(x, list):
            c += sum(1 for v in _flat(x) if _is_num(v))
        elif _is_num(x) or isinstance(x, bool):
            c += 1
        elif isinstance(x, str):
            try:
                float(x.strip())
                c += 1
            except ValueError:
                pass
    return c


def _f_counta(a):
    return sum(1 for x in a for v in _flat(x) if not _is_blank(v))


def _f_countblank(a):
    _need(a, 1, 1, "COUNTBLANK")
    return sum(1 for v in _flat(a[0]) if _is_blank(v))


def _try_float(s):
    try:
        return float(str(s).strip())
    except ValueError:
        return None


def _matcher(crit):
    """Kriteria ala COUNTIF/SUMIF: angka, teks dgn wildcard (* ? ~), atau operator (>5, <>x, >=10)."""
    crit = _scalar(crit)
    if isinstance(crit, bool):
        return lambda v: v is crit
    if _is_num(crit):
        t = float(crit)
        return lambda v: (_is_num(v) and float(v) == t) or (isinstance(v, str) and _try_float(v) == t)
    s = "" if crit is None else str(crit)
    m = re.match(r"^(<=|>=|<>|<|>|=)?(.*)$", s, re.S)
    op, rest = m.group(1) or "=", m.group(2)
    if rest == "":
        if op == "=":
            return _is_blank
        if op == "<>":
            return lambda v: not _is_blank(v)
    num = _try_float(rest) if rest.strip() else None
    if num is not None:
        def f(v):
            x = float(v) if _is_num(v) else (_try_float(v) if isinstance(v, str) and op in ("=", "<>") else None)
            if x is None:
                return op == "<>"
            return {"=": x == num, "<>": x != num, "<": x < num, ">": x > num,
                    "<=": x <= num, ">=": x >= num}[op]
        return f
    if op in ("=", "<>"):
        pat, i = [], 0
        while i < len(rest):
            ch = rest[i]
            if ch == "~" and i + 1 < len(rest):
                pat.append(re.escape(rest[i + 1]))
                i += 2
                continue
            pat.append(".*" if ch == "*" else "." if ch == "?" else re.escape(ch))
            i += 1
        rx = re.compile("".join(pat), re.I | re.S)

        def g(v):
            hit = isinstance(v, str) and rx.fullmatch(v) is not None
            return hit if op == "=" else not hit
        return g
    target = rest.casefold()
    return lambda v: isinstance(v, str) and {"<": v.casefold() < target, ">": v.casefold() > target,
                                              "<=": v.casefold() <= target, ">=": v.casefold() >= target}[op]


def _f_countif(a):
    _need(a, 2, 2, "COUNTIF")
    match = _matcher(a[1])
    return sum(1 for v in _flat(a[0]) if match(v))


def _f_sumif(a):
    _need(a, 2, 3, "SUMIF")
    match = _matcher(a[1])
    keys = _flat(a[0])
    vals = _flat(a[2]) if len(a) == 3 else keys
    total = 0.0
    for key, v in zip(keys, vals):
        if match(key) and _is_num(v):
            total += float(v)
    return total


def _f_unique(a):
    _need(a, 1, 1, "UNIQUE")
    grid = a[0] if isinstance(a[0], list) else [[a[0]]]
    seen, out = set(), []
    for row in grid:
        norm = tuple(("n", float(v)) if _is_num(v) else ("t", str(v).casefold()) if isinstance(v, str) and v != ""
                     else ("b", v) if isinstance(v, bool) else ("e",) for v in row)
        if norm in seen:
            continue
        seen.add(norm)
        out.append([0 if _is_blank(v) else v for v in row])
    return out


def _round_to(x, nd, mode):
    try:
        d = Decimal(repr(float(x)))
        return float(d.quantize(Decimal(1).scaleb(-int(nd)), rounding=mode))
    except (InvalidOperation, ValueError, OverflowError):
        raise XlError("#NUM!")


def _f_round(a):
    _need(a, 2, 2, "ROUND")
    return _round_to(_num(a[0]), _num(a[1]), ROUND_HALF_UP)


def _f_roundup(a):
    _need(a, 2, 2, "ROUNDUP")
    return _round_to(_num(a[0]), _num(a[1]), ROUND_UP)


def _f_rounddown(a):
    _need(a, 2, 2, "ROUNDDOWN")
    return _round_to(_num(a[0]), _num(a[1]), ROUND_DOWN)


def _f_int(a):
    _need(a, 1, 1, "INT")
    return math.floor(_num(a[0]))


def _f_abs(a):
    _need(a, 1, 1, "ABS")
    return abs(_num(a[0]))


def _f_rank(a):
    _need(a, 2, 3, "RANK")
    x = _num(a[0])
    nums = [float(v) for v in _flat(a[1]) if _is_num(v)]
    if x not in nums:
        raise XlError("#N/A")
    ascending = len(a) == 3 and _num(a[2]) != 0
    return 1 + sum(1 for y in nums if (y < x if ascending else y > x))


_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
           "September", "October", "November", "December"]


def _f_text(a):
    _need(a, 2, 2, "TEXT")
    v, fmt = _scalar(a[0]), _text(a[1])
    if isinstance(v, str) and not re.search(r"[ymd]", fmt, re.I):
        n = _try_float(v)
        if n is None:
            return v
        v = n
    if re.search(r"[yd]", fmt, re.I) or (re.search(r"m", fmt, re.I) and not re.search(r"[0#]", fmt)):
        dt = _to_dt(v)
        tokens = {"yyyy": f"{dt.year:04d}", "yy": f"{dt.year % 100:02d}", "mmmm": _MONTHS[dt.month - 1],
                  "mmm": _MONTHS[dt.month - 1][:3], "mm": f"{dt.month:02d}", "m": str(dt.month),
                  "dd": f"{dt.day:02d}", "d": str(dt.day)}
        return re.sub(r"yyyy|yy|mmmm|mmm|mm|m|dd|d", lambda m: tokens[m.group(0).lower()], fmt, flags=re.I)
    x = _num(v)
    pct = "%" in fmt
    if pct:
        x *= 100
    core = fmt.replace("%", "")
    decimals = len(re.findall(r"[0#]", core.split(".", 1)[1])) if "." in core else 0
    grouping = "," in core
    try:
        q = Decimal(repr(x)).quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        raise XlError("#NUM!")
    s = f"{q:,.{decimals}f}" if grouping else f"{q:.{decimals}f}"
    return s + ("%" if pct else "")


def _f_upper(a):
    _need(a, 1, 1, "UPPER")
    return _text(a[0]).upper()


def _f_lower(a):
    _need(a, 1, 1, "LOWER")
    return _text(a[0]).lower()


def _f_proper(a):
    _need(a, 1, 1, "PROPER")
    return _proper(_text(a[0]))


def _f_trim(a):
    _need(a, 1, 1, "TRIM")
    return re.sub(" +", " ", _text(a[0])).strip(" ")


def _f_len(a):
    _need(a, 1, 1, "LEN")
    return len(_text(a[0]))


def _f_left(a):
    _need(a, 1, 2, "LEFT")
    n = int(_num(a[1])) if len(a) == 2 else 1
    if n < 0:
        raise XlError("#VALUE!")
    return _text(a[0])[:n]


def _f_right(a):
    _need(a, 1, 2, "RIGHT")
    n = int(_num(a[1])) if len(a) == 2 else 1
    if n < 0:
        raise XlError("#VALUE!")
    return _text(a[0])[-n:] if n else ""


def _f_mid(a):
    _need(a, 3, 3, "MID")
    start, n = int(_num(a[1])), int(_num(a[2]))
    if start < 1 or n < 0:
        raise XlError("#VALUE!")
    return _text(a[0])[start - 1:start - 1 + n]


def _f_concat(a):
    return "".join(_text(v) for x in a for v in _flat(x))


def _f_textjoin(a):
    if len(a) < 3:
        raise FormulaSyntaxError("TEXTJOIN butuh pemisah, abaikan-kosong, dan minimal 1 teks.")
    delim, skip = _text(a[0]), _bool(a[1])
    parts = [v for x in a[2:] for v in _flat(x)]
    for v in parts:
        if isinstance(v, XlErr):
            raise XlError(v.code)
    texts = [_text(v) for v in parts if not (skip and _is_blank(v))]
    return delim.join(texts)


def _f_year(a):
    _need(a, 1, 1, "YEAR")
    return _to_dt(a[0]).year


def _f_month(a):
    _need(a, 1, 1, "MONTH")
    return _to_dt(a[0]).month


def _f_day(a):
    _need(a, 1, 1, "DAY")
    return _to_dt(a[0]).day


def _f_and(a):
    vals = [v for x in a for v in (_flat(x) if isinstance(x, list) else [x]) if not _is_blank(v)]
    if not vals:
        raise XlError("#VALUE!")
    return all(_bool(v) for v in vals)


def _f_or(a):
    vals = [v for x in a for v in (_flat(x) if isinstance(x, list) else [x]) if not _is_blank(v)]
    if not vals:
        raise XlError("#VALUE!")
    return any(_bool(v) for v in vals)


def _f_not(a):
    _need(a, 1, 1, "NOT")
    return not _bool(a[0])


def _same(x, y):
    if _is_num(x) and _is_num(y):
        return float(x) == float(y)
    if _is_num(x) or _is_num(y):
        return False
    if _is_blank(x) or _is_blank(y):
        return False
    return str(x).strip().casefold() == str(y).strip().casefold()


def _lookup(key, line_keys, exact):
    """Indeks kecocokan pertama (exact) atau nilai terbesar <= key pada data terurut (approx)."""
    if exact:
        return next((i for i, k in enumerate(line_keys) if _same(k, key)), None)
    best = None
    for i, k in enumerate(line_keys):
        if _is_blank(k):
            continue
        if _is_num(key) and _is_num(k):
            if float(k) <= float(key):
                best = i
            else:
                break
        elif isinstance(key, str) and isinstance(k, str):
            if k.casefold() <= key.casefold():
                best = i
            else:
                break
    return best


def _f_vlookup(a):
    _need(a, 3, 4, "VLOOKUP")
    key, table = _scalar(a[0]), a[1] if isinstance(a[1], list) else [[a[1]]]
    col = int(_num(a[2]))
    exact = len(a) == 4 and not _bool(a[3])
    if col < 1 or col > len(table[0]):
        raise XlError("#REF!")
    idx = _lookup(key, [row[0] for row in table], exact)
    if idx is None:
        raise XlError("#N/A")
    v = table[idx][col - 1]
    return 0 if _is_blank(v) else v


def _f_hlookup(a):
    _need(a, 3, 4, "HLOOKUP")
    key, table = _scalar(a[0]), a[1] if isinstance(a[1], list) else [[a[1]]]
    row = int(_num(a[2]))
    exact = len(a) == 4 and not _bool(a[3])
    if row < 1 or row > len(table):
        raise XlError("#REF!")
    idx = _lookup(key, table[0], exact)
    if idx is None:
        raise XlError("#N/A")
    v = table[row - 1][idx]
    return 0 if _is_blank(v) else v


_FUNCS = {
    "SUM": _f_sum, "AVERAGE": _f_average, "MIN": _f_min, "MAX": _f_max, "MEDIAN": _f_median,
    "STDEV.S": _f_stdev, "STDEV": _f_stdev,
    "COUNT": _f_count, "COUNTA": _f_counta, "COUNTBLANK": _f_countblank,
    "COUNTIF": _f_countif, "SUMIF": _f_sumif, "UNIQUE": _f_unique,
    "ROUND": _f_round, "ROUNDUP": _f_roundup, "ROUNDDOWN": _f_rounddown, "INT": _f_int, "ABS": _f_abs,
    "RANK": _f_rank, "TEXT": _f_text,
    "UPPER": _f_upper, "LOWER": _f_lower, "PROPER": _f_proper, "TRIM": _f_trim, "LEN": _f_len,
    "LEFT": _f_left, "RIGHT": _f_right, "MID": _f_mid,
    "CONCAT": _f_concat, "CONCATENATE": _f_concat, "TEXTJOIN": _f_textjoin,
    "YEAR": _f_year, "MONTH": _f_month, "DAY": _f_day,
    "AND": _f_and, "OR": _f_or, "NOT": _f_not,
    "VLOOKUP": _f_vlookup, "HLOOKUP": _f_hlookup,
}
