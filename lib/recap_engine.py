"""Rekapitulasi otomatis sebuah sheet: duplikat, kekosongan, format tidak sesuai, error/rumus
bermasalah, plus analisa ringkas (statistik, pencilan, kategori) dan kesimpulan.

Semua angka dihitung langsung dari data (bukan tebakan AI). Grid tanpa header: semua baris &
kolom setara. Hanya untuk menilai FORMAT kolom, beberapa sel teks di bagian paling atas kolom
angka/tanggal dianggap label dan dilewati supaya judul kolom tidak dikira "teks nyasar".

Posisi temuan dikembalikan sebagai persegi panjang 0-based [r1, c1, r2, c2] supaya frontend
bisa langsung memilih & menggulir ke selnya.
"""

import math
import re
import statistics
from collections import Counter, defaultdict

MAX_ROWS_ANALYZE = 30000
MAX_TARGETS = 300

_ERROR_RE = re.compile(r"^#(DIV/0!|REF!|N/A|VALUE!|NAME\?|NUM!|NULL!|SPILL!|CALC!)$", re.I)
_CURRENCY_RE = re.compile(r"^(rp\.?|idr|usd|\$|€|£)\s*", re.I)
_NUM_GROUPED = re.compile(r"^[+-]?\d{1,3}([.,]\d{3})+([.,]\d+)?%?$")
_NUM_PLAIN = re.compile(r"^[+-]?\d+([.,]\d+)?%?$")
_DATE_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2})?(\.\d+)?)?$")
_DATE_DMY = re.compile(r"^\d{1,2}([/.-])\d{1,2}\1\d{2,4}$")
_DATE_NAME = re.compile(r"^\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}$")

TEXTS = {
    "id": {
        "grade.good": "baik", "grade.fair": "cukup", "grade.poor": "perlu perbaikan",
        "dup_rows.t": "Baris dobel", "dup_rows.d": "{n} baris merupakan salinan persis dari baris lain (dari {g} kelompok data kembar). Contoh baris: {ex}.",
        "dup_keys.t": "Nilai kembar di kolom {col}", "dup_keys.d": "Kolom {col} hampir unik, tetapi ada {n} nilai yang muncul lebih dari sekali — kemungkinan kolom ID/kode yang seharusnya unik. Contoh: {ex}.",
        "blank_rows.t": "Baris kosong di tengah data", "blank_rows.d": "{n} baris tidak berisi apa pun di antara baris yang terisi.",
        "blank_cols.t": "Kolom kosong", "blank_cols.d": "{n} kolom tidak berisi data sama sekali: {ex}.",
        "sparse.t": "Kolom {col} banyak kosong", "sparse.d": "Hanya {pct}% sel di kolom {col} yang terisi ({n} sel kosong).",
        "numtext_mixed.t": "Angka tersimpan sebagai teks di kolom {col}", "numtext_mixed.d": "{n} sel berisi angka yang tersimpan sebagai teks (mis. {ex}) di kolom yang sebagian besar angka, sehingga tidak ikut terjumlah. Ubah dengan 'Ubah ke angka'.",
        "numtext_all.t": "Kolom {col} berisi angka berbentuk teks", "numtext_all.d": "Seluruh {n} sel terisi di kolom {col} berupa teks yang terlihat seperti angka (mis. {ex}). Abaikan jika ini kode/ID/nomor telepon.",
        "stray_text.t": "Teks nyasar di kolom angka {col}", "stray_text.d": "{n} sel berisi teks (mis. {ex}) di kolom {col} yang sebagian besar berisi angka.",
        "date_mixed.t": "Format tanggal campur di kolom {col}", "date_mixed.d": "Kolom {col} memakai {k} format tanggal berbeda (mis. {ex}). Seragamkan agar bisa diurutkan dan difilter dengan benar.",
        "date_stray.t": "Teks nyasar di kolom tanggal {col}", "date_stray.d": "{n} sel di kolom {col} bukan tanggal (mis. {ex}) padahal sebagian besar isinya tanggal.",
        "spaces.t": "Spasi berlebih", "spaces.d": "{n} sel teks punya spasi di awal/akhir atau spasi ganda (mis. {ex}). Ini membuat nilai yang sama dianggap berbeda.",
        "case.t": "Penulisan tidak seragam di kolom {col}", "case.d": "{n} nilai sebenarnya sama tetapi beda huruf besar/kecil atau spasi (mis. {ex}).",
        "errors.t": "Nilai error di sel", "errors.d": "{n} sel berisi error Excel ({ex}).",
        "f_blank.t": "Rumus menunjuk sel kosong", "f_blank.d": "{n} sel berumus merujuk ke sel yang seluruhnya kosong, hasilnya mungkin 0 atau salah.",
        "f_out.t": "Rumus menunjuk di luar area data", "f_out.d": "{n} sel berumus merujuk ke baris/kolom di luar data yang terbaca.",
        "f_manual.t": "Angka ketikan manual di kolom rumus {col}", "f_manual.d": "{n} sel di kolom {col} berisi nilai biasa, padahal sel di sekitarnya berumus. Rumusnya mungkin tertimpa. Contoh baris: {ex}.",
        "outliers.t": "Kemungkinan pencilan di kolom {col}", "outliers.d": "{n} nilai jauh di luar sebaran umum kolom {col} (rentang wajar {lo} s/d {hi}). Periksa apakah salah ketik.",
        "ok.t": "Tidak ada masalah berarti", "ok.d": "Pemeriksaan duplikat, kekosongan, format, dan error tidak menemukan masalah yang perlu ditindaklanjuti.",
        "c.open": "Sheet berisi {rows} baris × {cols} kolom dengan {fill}% sel terisi. Skor kesehatan data {score}/100 ({grade}).",
        "c.clean": "Tidak ditemukan masalah berarti pada duplikat, kekosongan, format, maupun error.",
        "c.prio": "Prioritas perbaikan: {items}.",
        "c.potential": "Potensi analisa: ada {num} kolom angka dan {cat} kolom kategori, cocok untuk dirangkum per kategori (pivot) atau dibuat grafik.",
        "c.dates": "Kolom tanggal ({cols}) memungkinkan analisa tren per periode.",
        "c.truncated": "Catatan: hanya {n} baris pertama yang dianalisa.",
        "c.formula_scan": "Pemeriksaan rumus mencakup baris yang discan saat unggah (maks. 500 baris pertama).",
    },
    "en": {
        "grade.good": "good", "grade.fair": "fair", "grade.poor": "needs work",
        "dup_rows.t": "Duplicate rows", "dup_rows.d": "{n} rows are exact copies of other rows ({g} groups of identical data). Example rows: {ex}.",
        "dup_keys.t": "Repeated values in column {col}", "dup_keys.d": "Column {col} is almost unique, but {n} values appear more than once — probably an ID/code column that should be unique. Examples: {ex}.",
        "blank_rows.t": "Blank rows inside the data", "blank_rows.d": "{n} rows contain nothing between filled rows.",
        "blank_cols.t": "Empty columns", "blank_cols.d": "{n} columns contain no data at all: {ex}.",
        "sparse.t": "Column {col} is mostly empty", "sparse.d": "Only {pct}% of cells in column {col} are filled ({n} empty cells).",
        "numtext_mixed.t": "Numbers stored as text in column {col}", "numtext_mixed.d": "{n} cells hold numbers stored as text (e.g. {ex}) in a mostly numeric column, so they are not summed. Fix with 'Convert to number'.",
        "numtext_all.t": "Column {col} holds number-like text", "numtext_all.d": "All {n} filled cells in column {col} are text that looks like numbers (e.g. {ex}). Ignore if these are codes/IDs/phone numbers.",
        "stray_text.t": "Stray text in numeric column {col}", "stray_text.d": "{n} cells contain text (e.g. {ex}) in column {col}, which is mostly numbers.",
        "date_mixed.t": "Mixed date formats in column {col}", "date_mixed.d": "Column {col} uses {k} different date formats (e.g. {ex}). Unify them so sorting and filtering work correctly.",
        "date_stray.t": "Stray text in date column {col}", "date_stray.d": "{n} cells in column {col} are not dates (e.g. {ex}) although most of it is dates.",
        "spaces.t": "Extra spaces", "spaces.d": "{n} text cells have leading/trailing or double spaces (e.g. {ex}). Equal values are then treated as different.",
        "case.t": "Inconsistent spelling in column {col}", "case.d": "{n} values are really the same but differ in letter case or spacing (e.g. {ex}).",
        "errors.t": "Error values in cells", "errors.d": "{n} cells contain Excel errors ({ex}).",
        "f_blank.t": "Formulas pointing to empty cells", "f_blank.d": "{n} formula cells reference cells that are entirely empty; the result may be 0 or wrong.",
        "f_out.t": "Formulas pointing outside the data", "f_out.d": "{n} formula cells reference rows/columns outside the data that was read.",
        "f_manual.t": "Manually typed values in formula column {col}", "f_manual.d": "{n} cells in column {col} hold plain values while neighbouring cells are formulas. A formula may have been overwritten. Example rows: {ex}.",
        "outliers.t": "Possible outliers in column {col}", "outliers.d": "{n} values lie far outside the usual spread of column {col} (normal range {lo} to {hi}). Check for typos.",
        "ok.t": "No significant issues", "ok.d": "Checks for duplicates, blanks, formats and errors found nothing that needs action.",
        "c.open": "The sheet has {rows} rows × {cols} columns with {fill}% of cells filled. Data health score {score}/100 ({grade}).",
        "c.clean": "No significant issues found in duplicates, blanks, formats or errors.",
        "c.prio": "Fix first: {items}.",
        "c.potential": "Analysis potential: {num} numeric and {cat} category columns — good for summarising by category (pivot) or charting.",
        "c.dates": "Date columns ({cols}) allow trend analysis over time.",
        "c.truncated": "Note: only the first {n} rows were analysed.",
        "c.formula_scan": "The formula check covers the rows scanned at upload (first 500 rows at most).",
    },
}

SEV_WEIGHT = {"high": 12, "medium": 6, "low": 2, "info": 0}
SEV_ORDER = {"high": 0, "medium": 1, "low": 2, "info": 3}


def _letters(idx):
    n, out = idx + 1, ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        out = chr(65 + rem) + out
    return out


def _letters_to_idx(s):
    n = 0
    for ch in s.upper():
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _parse_numtext(s):
    """'Rp 1.250.000' / '1,234.50' / '12%' -> float, atau None kalau bukan angka berbentuk teks."""
    t = _CURRENCY_RE.sub("", s.strip()).replace(" ", "")
    if not t:
        return None
    pct = t.endswith("%")
    if _NUM_GROUPED.match(t):
        body = t.rstrip("%")
        last_dot, last_com = body.rfind("."), body.rfind(",")
        if last_dot >= 0 and last_com >= 0:
            dec = "." if last_dot > last_com else ","
            body = body.replace("," if dec == "." else ".", "").replace(dec, ".")
        else:
            sep = "." if last_dot >= 0 else ","
            parts = body.split(sep)
            body = "".join(parts) if len(parts[-1]) == 3 else "".join(parts[:-1]) + "." + parts[-1]
    elif _NUM_PLAIN.match(t):
        body = t.rstrip("%").replace(",", ".")
    else:
        return None
    try:
        return float(body) / (100 if pct else 1)
    except ValueError:
        return None


def _classify(v):
    """-> (kind, tag). kind: blank | num | numtext | date | text | error | bool."""
    if v is None:
        return "blank", None
    if isinstance(v, bool):
        return "bool", None
    if isinstance(v, (int, float)):
        if isinstance(v, float) and v != v:
            return "blank", None
        return "num", None
    s = str(v)
    st = s.strip()
    if not st:
        return "blank", None
    if _ERROR_RE.match(st):
        return "error", None
    if _DATE_ISO.match(st):
        return "date", "iso"
    m = _DATE_DMY.match(st)
    if m:
        return "date", "dmy" + m.group(1)
    if _DATE_NAME.match(st):
        return "date", "name"
    if _parse_numtext(st) is not None:
        return "numtext", None
    return "text", None


def _runs(sorted_vals):
    out, start, prev = [], None, None
    for v in sorted_vals:
        if start is None:
            start = prev = v
        elif v == prev + 1:
            prev = v
        else:
            out.append((start, prev))
            start = prev = v
    if start is not None:
        out.append((start, prev))
    return out


def _rows_rects(rows, c1, c2):
    return [[a, c1, b, c2] for a, b in _runs(sorted(set(rows)))][:MAX_TARGETS]


def _cells_rects(cells):
    by_col = defaultdict(list)
    for r, c in cells:
        by_col[c].append(r)
    out = []
    for c in sorted(by_col):
        out += [[a, c, b, c] for a, b in _runs(sorted(set(by_col[c])))]
    return out[:MAX_TARGETS]


def _short(v, n=24):
    s = str(v).strip().replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"


def _fmt_num(x):
    return str(int(x)) if x == int(x) else f"{x:,.2f}"


def build_recap(records, columns, formula_columns=None, formula_cells=None, lang="id"):
    lang = lang if lang in TEXTS else "id"
    X = TEXTS[lang]

    def tx(key, **kw):
        return X[key].format(**kw)

    total_rows = len(records)
    truncated = total_rows > MAX_ROWS_ANALYZE
    grid = records[:MAX_ROWS_ANALYZE]
    nrows, ncols = len(grid), len(columns)
    formula_columns = formula_columns or {}
    formula_cells = formula_cells or {}

    # Klasifikasi tiap sel sekali saja
    kinds = [[] for _ in range(ncols)]  # kinds[c][r] = (kind, tag)
    for r in range(nrows):
        row = grid[r]
        for c in range(ncols):
            kinds[c].append(_classify(row[c] if c < len(row) else None))

    def val(r, c):
        row = grid[r]
        return row[c] if c < len(row) else None

    filled_total = sum(1 for c in range(ncols) for k in kinds[c] if k[0] != "blank")
    cells_total = nrows * ncols
    fill_pct = round(100 * filled_total / cells_total) if cells_total else 0

    # Baris label di atas kolom angka/tanggal (dilewati hanya untuk pemeriksaan format)
    skip = [set() for _ in range(ncols)]
    for c in range(ncols):
        nonblank = [i for i, k in enumerate(kinds[c]) if k[0] != "blank"]
        if not nonblank:
            continue
        for dom in ("num", "date"):
            cnt = sum(1 for i in nonblank if kinds[c][i][0] == dom)
            if cnt >= 3 and cnt / len(nonblank) >= 0.5:
                for i in nonblank:
                    if kinds[c][i][0] == "text" and len(skip[c]) < 4:
                        skip[c].add(i)
                    else:
                        break
                break

    findings = []

    def add(fid, cat, sev, title, detail, count, rects=None, extra=None):
        f = {"id": fid, "category": cat, "severity": sev, "title": title,
             "detail": detail, "count": count, "targets": rects or []}
        if extra:
            f.update(extra)
        findings.append(f)

    # ---- 1. Duplikat ----
    groups = defaultdict(list)
    for r in range(nrows):
        norm = tuple("" if kinds[c][r][0] == "blank" else str(val(r, c)).strip()
                     for c in range(ncols))
        if sum(1 for x in norm if x) >= min(2, ncols):
            groups[norm].append(r)
    dup_groups = [g for g in groups.values() if len(g) > 1]
    if dup_groups:
        extra_rows = sum(len(g) - 1 for g in dup_groups)
        all_rows = [r for g in dup_groups for r in g]
        ex = "; ".join("/".join(str(r + 1) for r in g[:4]) for g in dup_groups[:3])
        sev = "high" if extra_rows / max(nrows, 1) >= 0.05 else "medium"
        add("dup_rows", "duplicate", sev, tx("dup_rows.t"),
            tx("dup_rows.d", n=extra_rows, g=len(dup_groups), ex=ex),
            extra_rows, _rows_rects(all_rows, 0, ncols - 1))

    key_shown = 0
    for c in range(ncols):
        if key_shown >= 3 or nrows < 10:
            break
        idxs = [i for i, k in enumerate(kinds[c]) if k[0] in ("text", "numtext") and i not in skip[c]]
        if len(idxs) < 10 or len(idxs) / nrows < 0.5:
            continue
        cnt = Counter(str(val(i, c)).strip().lower() for i in idxs)
        dups = {v for v, n in cnt.items() if n > 1}
        if dups and len(cnt) / len(idxs) >= 0.9:
            cells = [(i, c) for i in idxs if str(val(i, c)).strip().lower() in dups]
            n_dup = len(cells)
            ex = ", ".join(_short(v, 16) for v in list(dups)[:3])
            add(f"dup_key_{c}", "duplicate", "medium", tx("dup_keys.t", col=_letters(c)),
                tx("dup_keys.d", col=_letters(c), n=n_dup, ex=ex), n_dup, _cells_rects(cells))
            key_shown += 1

    # ---- 2. Kekosongan ----
    blank_rows = [r for r in range(nrows) if all(kinds[c][r][0] == "blank" for c in range(ncols))]
    first_data = next((r for r in range(nrows) if r not in set(blank_rows)), None)
    inner_blank = [r for r in blank_rows if first_data is not None and r > first_data]
    if inner_blank:
        add("blank_rows", "empty", "low", tx("blank_rows.t"), tx("blank_rows.d", n=len(inner_blank)),
            len(inner_blank), _rows_rects(inner_blank, 0, ncols - 1))
    blank_cols = [c for c in range(ncols) if all(k[0] == "blank" for k in kinds[c])]
    if blank_cols:
        add("blank_cols", "empty", "low", tx("blank_cols.t"),
            tx("blank_cols.d", n=len(blank_cols), ex=", ".join(_letters(c) for c in blank_cols[:8])),
            len(blank_cols), [[0, c, max(nrows - 1, 0), c] for c in blank_cols[:MAX_TARGETS]])
    column_fill = []
    sparse_shown = 0
    for c in range(ncols):
        filled = sum(1 for k in kinds[c] if k[0] != "blank")
        pct = round(100 * filled / nrows) if nrows else 0
        column_fill.append({"col": _letters(c), "filled": filled, "total": nrows, "pct": pct})
        if c not in blank_cols and nrows >= 10 and pct < 50 and sparse_shown < 3:
            add(f"sparse_{c}", "empty", "low", tx("sparse.t", col=_letters(c)),
                tx("sparse.d", col=_letters(c), pct=pct, n=nrows - filled), nrows - filled,
                [[0, c, nrows - 1, c]])
            sparse_shown += 1

    # ---- 3. Format ----
    for c in range(ncols):
        L = _letters(c)
        idx = [i for i, k in enumerate(kinds[c]) if k[0] != "blank" and i not in skip[c]]
        if not idx:
            continue
        n = len(idx)
        by = defaultdict(list)
        for i in idx:
            by[kinds[c][i][0]].append(i)
        n_num, n_nt, n_date = len(by["num"]), len(by["numtext"]), len(by["date"])
        n_text, n_err = len(by["text"]), len(by["error"])

        if n_num >= 1 and n_nt >= 1 and (n_num + n_nt) / n >= 0.6:
            ex = ", ".join(_short(val(i, c), 14) for i in by["numtext"][:3])
            add(f"numtext_mixed_{c}", "format", "medium", tx("numtext_mixed.t", col=L),
                tx("numtext_mixed.d", n=n_nt, ex=ex), n_nt, _cells_rects([(i, c) for i in by["numtext"]]))
        elif n_nt >= 5 and n_nt == n:
            plain = [i for i in by["numtext"] if not str(val(i, c)).strip().startswith("0")]
            if len(plain) == n:
                ex = ", ".join(_short(val(i, c), 14) for i in by["numtext"][:3])
                add(f"numtext_all_{c}", "format", "low", tx("numtext_all.t", col=L),
                    tx("numtext_all.d", n=n, ex=ex), n, [[0, c, nrows - 1, c]])

        if n_num >= 3 and (n_num + n_nt) / n >= 0.6 and n_text >= 1:
            ex = ", ".join(_short(val(i, c), 14) for i in by["text"][:3])
            add(f"stray_text_{c}", "format", "medium", tx("stray_text.t", col=L),
                tx("stray_text.d", n=n_text, ex=ex, col=L), n_text, _cells_rects([(i, c) for i in by["text"]]))

        if n_date >= 2 and n_date / n >= 0.6:
            tags = Counter(kinds[c][i][1] for i in by["date"])
            if len(tags) > 1:
                ex = ", ".join(_short(val(by_i, c), 12) for by_i in
                               [next(i for i in by["date"] if kinds[c][i][1] == t) for t in tags])
                add(f"date_mixed_{c}", "format", "medium", tx("date_mixed.t", col=L),
                    tx("date_mixed.d", col=L, k=len(tags), ex=ex), len(by["date"]),
                    _cells_rects([(i, c) for i in by["date"]]))
            if n_text >= 1:
                ex = ", ".join(_short(val(i, c), 14) for i in by["text"][:3])
                add(f"date_stray_{c}", "format", "medium", tx("date_stray.t", col=L),
                    tx("date_stray.d", n=n_text, ex=ex, col=L), n_text,
                    _cells_rects([(i, c) for i in by["text"]]))

    # Spasi berlebih (gabungan semua kolom)
    space_cells = []
    for c in range(ncols):
        for r, k in enumerate(kinds[c]):
            if k[0] in ("text", "numtext"):
                s = str(val(r, c))
                if s != s.strip() or "  " in s:
                    space_cells.append((r, c))
    if space_cells:
        ex = ", ".join(repr(_short(val(r, c), 14)) for r, c in space_cells[:3])
        add("spaces", "format", "low", tx("spaces.t"), tx("spaces.d", n=len(space_cells), ex=ex),
            len(space_cells), _cells_rects(space_cells))

    # Penulisan tidak seragam (kolom kategori)
    case_shown = 0
    for c in range(ncols):
        if case_shown >= 3:
            break
        idx = [i for i, k in enumerate(kinds[c]) if k[0] == "text" and i not in skip[c]]
        if len(idx) < 6:
            continue
        variants = defaultdict(set)
        for i in idx:
            s = str(val(i, c))
            variants[re.sub(r"\s+", " ", s.strip().lower())].add(s)
        if len(variants) > max(50, len(idx) * 0.3):
            continue
        bad = {k for k, v in variants.items() if len(v) > 1}
        if bad:
            cells = [(i, c) for i in idx if re.sub(r"\s+", " ", str(val(i, c)).strip().lower()) in bad]
            ex = " / ".join(sorted(variants[next(iter(bad))])[:3])
            add(f"case_{c}", "format", "low", tx("case.t", col=_letters(c)),
                tx("case.d", n=len(bad), ex=ex), len(cells), _cells_rects(cells))
            case_shown += 1

    # ---- 4. Error & rumus ----
    err_cells = [(r, c) for c in range(ncols) for r, k in enumerate(kinds[c]) if k[0] == "error"]
    if err_cells:
        types = Counter(str(val(r, c)).strip().upper() for r, c in err_cells)
        add("errors", "error", "high", tx("errors.t"),
            tx("errors.d", n=len(err_cells), ex=", ".join(f"{k} ×{v}" for k, v in types.most_common(4))),
            len(err_cells), _cells_rects(err_cells))

    f_blank, f_out = [], []
    for rk, cmap in formula_cells.items():
        try:
            r = int(rk)
        except (TypeError, ValueError):
            continue
        for letter, e in cmap.items():
            if not isinstance(e, dict) or e.get("x"):
                continue
            c = _letters_to_idx(letter)
            for dep in e.get("deps") or []:
                rows = dep.get("rows")
                if rows is None:
                    f_out.append((r, c))
                    break
                cols = [_letters_to_idx(x) for x in dep.get("cols") or []]
                r_lo, r_hi = rows[0] - 1, min(rows[1] - 1, rows[0] + 198)
                if cols and r_hi < nrows and all(
                    kinds[cc][rr][0] == "blank" for cc in cols if cc < ncols for rr in range(r_lo, r_hi + 1)
                ):
                    f_blank.append((r, c))
                    break
    if f_blank:
        add("f_blank", "formula", "medium", tx("f_blank.t"), tx("f_blank.d", n=len(f_blank)),
            len(f_blank), _cells_rects(f_blank))
    if f_out:
        add("f_out", "formula", "medium", tx("f_out.t"), tx("f_out.d", n=len(f_out)),
            len(f_out), _cells_rects(f_out))

    shown = 0
    for letter, info in formula_columns.items():
        if shown >= 3 or (info or {}).get("ratio", 0) < 0.6:
            continue
        c = _letters_to_idx(letter)
        rows_with = [int(rk) for rk, m in formula_cells.items() if letter in m]
        if len(rows_with) < 3 or c >= ncols:
            continue
        lo, hi = min(rows_with), max(rows_with)
        have = set(rows_with)
        manual = [r for r in range(lo, min(hi, nrows - 1) + 1)
                  if r not in have and kinds[c][r][0] in ("num", "numtext")]
        if manual:
            add(f"f_manual_{c}", "formula", "medium", tx("f_manual.t", col=letter),
                tx("f_manual.d", n=len(manual), col=letter, ex=", ".join(str(r + 1) for r in manual[:5])),
                len(manual), _cells_rects([(r, c) for r in manual]))
            shown += 1

    # ---- 5. Analisa & bahan grafik/pivot ----
    numeric, categories, dates, histograms = [], [], [], []
    num_cols = []   # indeks kolom angka (urut kemunculan)
    cat_rows = {}   # indeks kolom -> [(baris, nilai)] untuk tabel pivot
    date_cols = []
    for c in range(ncols):
        L = _letters(c)
        idx = [i for i, k in enumerate(kinds[c]) if k[0] != "blank" and i not in skip[c]]
        if not idx:
            continue
        nums = [(i, val(i, c)) for i in idx if kinds[c][i][0] == "num"]
        if len(nums) >= 3 and len(nums) / len(idx) >= 0.6 and len(numeric) < 8:
            xs = [float(v) for _, v in nums]
            q = statistics.quantiles(xs, n=4) if len(xs) >= 4 else None
            outl = []
            lo = hi = None
            if q:
                iqr = q[2] - q[0]
                lo, hi = q[0] - 1.5 * iqr, q[2] + 1.5 * iqr
                if iqr > 0:
                    outl = [(i, c) for i, v in nums if v < lo or v > hi]
            num_cols.append(c)
            numeric.append({"col": L, "n": len(xs), "min": min(xs), "max": max(xs),
                            "mean": sum(xs) / len(xs), "median": statistics.median(xs),
                            "outliers": len(outl)})
            if outl and len(outl) <= max(3, 0.1 * len(xs)):
                add(f"outliers_{c}", "insight", "low", tx("outliers.t", col=L),
                    tx("outliers.d", n=len(outl), col=L, lo=_fmt_num(lo), hi=_fmt_num(hi)),
                    len(outl), _cells_rects(outl))
            # Histogram (sebaran nilai), maksimal 4 kolom
            if len(histograms) < 4:
                k = min(10, max(5, int(math.log2(len(xs))) + 1))
                mn, mx = min(xs), max(xs)
                if mx > mn:
                    w = (mx - mn) / k
                    bins = [{"lo": mn + j * w, "hi": mn + (j + 1) * w, "count": 0, "cells": []} for j in range(k)]
                    for i, v in nums:
                        j = min(int((float(v) - mn) / w), k - 1)
                        bins[j]["count"] += 1
                        bins[j]["cells"].append((i, c))
                    histograms.append({"col": L, "bins": [
                        {"lo": b_["lo"], "hi": b_["hi"], "count": b_["count"],
                         "targets": _cells_rects(b_["cells"])} for b_ in bins]})

        vals = [(i, str(val(i, c)).strip()) for i in idx if kinds[c][i][0] == "text"]
        if len(categories) < 3 and len(vals) >= 5 and len(vals) / len(idx) >= 0.8:
            cnt = Counter(v for _, v in vals)
            # Sel teks pertama yang unik & beda dari sisanya kemungkinan label kolom -> jangan dihitung
            if cnt[vals[0][1]] == 1 and 2 <= len(cnt) - 1 <= 20 and (len(cnt) - 1) / (len(vals) - 1) <= 0.5:
                vals = vals[1:]
                cnt = Counter(v for _, v in vals)
            if 2 <= len(cnt) <= 20 and len(cnt) / len(vals) <= 0.5:
                top = cnt.most_common(8)
                top_set = {k for k, _ in top}
                categories.append({
                    "col": L, "ci": c, "total": len(vals), "unique": len(cnt),
                    "top": [{"value": _short(k, 22), "count": n,
                             "targets": _cells_rects([(i, c) for i, v in vals if v == k])}
                            for k, n in top],
                    "others": sum(1 for _, v in vals if v not in top_set)})
                cat_rows[c] = (vals, [k for k, _ in top])
        isod = sorted(str(val(i, c)).strip()[:10] for i in idx if kinds[c][i][1] == "iso")
        if len(isod) >= 3 and len(isod) / len(idx) >= 0.6 and len(dates) < 3:
            dates.append({"col": L, "n": len(isod), "min": isod[0], "max": isod[-1]})
            date_cols.append(c)

    # ---- Tabel pivot: kategori x (hitung, jumlah, rata-rata kolom angka) ----
    pivots = []
    for ci, (vals, top_keys) in list(cat_rows.items())[:2]:
        measures = [nc for nc in num_cols if nc != ci][:2]
        groups = [(k, [i for i, v in vals if v == k]) for k in top_keys]
        rest = [i for i, v in vals if v not in set(top_keys)]
        if rest:
            groups.append((None, rest))

        def agg(rows_):
            out = {"count": len(rows_), "m": []}
            for nc in measures:
                xs = [float(val(i, nc)) for i in rows_ if kinds[nc][i][0] == "num" and i not in skip[nc]]
                out["m"].append({"sum": sum(xs), "avg": (sum(xs) / len(xs)) if xs else None})
            return out

        cat_index = next(j for j, cat in enumerate(categories) if cat["ci"] == ci)
        prow = []
        for gi, (k, rows_) in enumerate(groups):
            r_ = agg(rows_)
            r_["label"] = None if k is None else _short(k, 22)
            r_["src"] = [cat_index, gi] if k is not None else None
            prow.append(r_)
        pivots.append({"col": _letters(ci), "measures": [_letters(nc) for nc in measures],
                       "rows": prow, "total": agg([i for i, _ in vals])})

    # ---- Tren: kolom tanggal ISO x kolom angka, dijumlah per bulan (atau per hari) ----
    trend = None
    if date_cols and num_cols:
        dc = date_cols[0]
        ms = [nc for nc in num_cols if nc != dc][:2]
        if ms:
            import datetime as _dt
            ds = [(i, str(val(i, dc)).strip()[:10]) for i in range(nrows)
                  if kinds[dc][i][1] == "iso" and i not in skip[dc]]
            try:
                span = (_dt.date.fromisoformat(max(d for _, d in ds)) - _dt.date.fromisoformat(min(d for _, d in ds))).days
            except ValueError:
                span = 0
            gran = "month" if span >= 60 else "day"
            agg_t = defaultdict(lambda: [0.0] * len(ms))
            for i, d in ds:
                key = d[:7] if gran == "month" else d
                for j, nc in enumerate(ms):
                    if kinds[nc][i][0] == "num" and i not in skip[nc]:
                        agg_t[key][j] += float(val(i, nc))
            keys = sorted(agg_t)[-36:]
            if len(keys) >= 2:
                trend = {"date_col": _letters(dc), "cols": [_letters(nc) for nc in ms],
                         "granularity": gran,
                         "points": [{"label": k, "values": agg_t[k]} for k in keys]}

    # ---- Skor, urutan, kesimpulan ----
    findings.sort(key=lambda f: (SEV_ORDER[f["severity"]], -f["count"]))
    penalty = sum(SEV_WEIGHT[f["severity"]] for f in findings if f["category"] != "insight")
    score = max(0, min(100, 100 - penalty))
    grade = tx("grade.good") if score >= 85 else tx("grade.fair") if score >= 65 else tx("grade.poor")
    issues = [f for f in findings if f["category"] != "insight"]
    if not issues:
        findings.insert(0, {"id": "ok", "category": "ok", "severity": "ok", "title": tx("ok.t"),
                            "detail": tx("ok.d"), "count": 0, "targets": []})

    concl = [tx("c.open", rows=nrows, cols=ncols, fill=fill_pct, score=score, grade=grade)]
    if issues:
        top = [f"{f['title']} ({f['count']})" for f in issues[:3]]
        concl.append(tx("c.prio", items="; ".join(top)))
    else:
        concl.append(tx("c.clean"))
    if numeric or categories:
        concl.append(tx("c.potential", num=len(numeric), cat=len(categories)))
    if dates:
        concl.append(tx("c.dates", cols=", ".join(d["col"] for d in dates)))
    if formula_cells or formula_columns:
        concl.append(tx("c.formula_scan"))
    if truncated:
        concl.append(tx("c.truncated", n=MAX_ROWS_ANALYZE))

    return {
        "summary": {"rows": total_rows, "cols": ncols, "cells": cells_total, "filled": filled_total,
                    "empty": cells_total - filled_total, "fill_pct": fill_pct, "score": score,
                    "grade": grade, "truncated": truncated},
        "conclusion": concl,
        "findings": findings,
        "column_fill": column_fill[:60],
        "numeric": numeric,
        "categories": categories,
        "dates": dates,
        "histograms": histograms,
        "pivots": pivots,
        "trend": trend,
    }
