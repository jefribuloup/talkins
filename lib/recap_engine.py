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
        "dup_keys.t": "ID/nomor kembar di kolom {col}", "dup_keys.d": "{n} sel di kolom {col} (kolom ID/nomor induk, seharusnya unik) berisi nilai yang muncul lebih dari sekali. Contoh: {ex}.",
        "id_len.t": "Panjang ID tidak seragam di kolom {col}", "id_len.d": "{n} nilai panjangnya berbeda dari mayoritas ({len} digit), mis. {ex}. Periksa salah ketik atau angka nol di depan yang hilang.",
        "type.finance": "Data keuangan", "type.academic": "Data akademik / kemahasiswaan", "type.hr": "Data kepegawaian",
        "type.sales": "Data penjualan / persediaan", "type.roster": "Daftar data (nama / ID)", "type.generic": "Data umum",
        "role.id": "ID / nomor induk", "role.index": "Nomor urut", "role.name": "Nama", "role.contact": "Kontak",
        "role.money": "Nilai uang", "role.quantity": "Jumlah", "role.score": "Nilai / skor", "role.percent": "Persentase",
        "role.measure": "Angka", "role.category": "Kategori", "role.date": "Tanggal", "role.text": "Teks bebas",
        "why.labels": "Label kolom/judul yang dikenali: {ex}.",
        "why.nolabels": "Label kolom tidak jelas, jadi jenis data ditebak dari pola isinya.",
        "why.ids": "Kolom {cols} adalah ID/nomor induk (bukan angka untuk dijumlahkan), jadi tidak dihitung statistik/grafik angkanya.",
        "why.money": "Kolom {cols} berisi nilai uang.",
        "kpi.records": "Jumlah data", "kpi.unique": "ID unik ({col})", "kpi.total": "Total {col}", "kpi.avg": "Rata-rata {col}",
        "kpi.top": "Terbanyak di {col}",
        "trend.count": "Jumlah data",
        "c.type": "Data terdeteksi sebagai: {type}.",
        "c.fin": "Total {col} = {sum} (rata-rata {avg} per baris, {n} baris).",
        "c.roster": "Terdapat {n} data{ids}.",
        "c.ids": " dengan {u} ID unik di kolom {col}",
        "c.topcat": "Sebaran terbesar di kolom {col}: {v} ({n} data, {pct}%).",
        "c.score": "Rata-rata {col}: {avg} (rentang {min}–{max}).",
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
        "dup_keys.t": "Repeated IDs in column {col}", "dup_keys.d": "{n} cells in column {col} (an ID column that should be unique) hold values that appear more than once. Examples: {ex}.",
        "id_len.t": "Inconsistent ID length in column {col}", "id_len.d": "{n} values differ in length from the majority ({len} digits), e.g. {ex}. Check for typos or lost leading zeros.",
        "type.finance": "Financial data", "type.academic": "Academic / student data", "type.hr": "Staff / HR data",
        "type.sales": "Sales / inventory data", "type.roster": "Record list (names / IDs)", "type.generic": "General data",
        "role.id": "ID / registration no.", "role.index": "Row number", "role.name": "Name", "role.contact": "Contact",
        "role.money": "Money amount", "role.quantity": "Quantity", "role.score": "Score / grade", "role.percent": "Percentage",
        "role.measure": "Number", "role.category": "Category", "role.date": "Date", "role.text": "Free text",
        "why.labels": "Recognised column labels/titles: {ex}.",
        "why.nolabels": "Column labels are unclear, so the type was inferred from the data patterns.",
        "why.ids": "Column(s) {cols} are IDs/registration numbers (not numbers to add up), so they get no numeric statistics/charts.",
        "why.money": "Column(s) {cols} hold money amounts.",
        "kpi.records": "Records", "kpi.unique": "Unique IDs ({col})", "kpi.total": "Total {col}", "kpi.avg": "Average {col}",
        "kpi.top": "Most common in {col}",
        "trend.count": "Records",
        "c.type": "Data detected as: {type}.",
        "c.fin": "Total {col} = {sum} (average {avg} per row, {n} rows).",
        "c.roster": "There are {n} records{ids}.",
        "c.ids": " with {u} unique IDs in column {col}",
        "c.topcat": "Largest group in column {col}: {v} ({n} records, {pct}%).",
        "c.score": "Average {col}: {avg} (range {min}–{max}).",
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

NUMERIC_ROLES = {"money", "quantity", "score", "percent", "measure"}

# Kata kunci label kolom -> peran kolom. Yang dipakai = kata kunci TERPANJANG yang cocok.
ROLE_KW = {
    "id": ["nim", "npm", "stambuk", "stb", "nisn", "nis", "nip", "nik", "nidn", "no ktp", "ktp", "id", "kode",
           "no induk", "nomor induk", "no peserta", "nomor peserta", "no pendaftaran", "no reg", "npwp", "sku", "kd"],
    "contact": ["telp", "telepon", "tlp", "hp", "no hp", "nomor hp", "no telp", "wa", "whatsapp", "phone", "email", "e mail"],
    "name": ["nama", "name", "nama lengkap"],
    "date": ["tanggal", "tgl", "date", "waktu", "periode", "tanggal lahir", "tgl lahir", "bulan", "tahun"],
    "percent": ["persen", "persentase", "percent", "rasio"],
    "score": ["ipk", "ips", "skor", "score", "nilai akhir", "nilai angka", "grade"],
    "quantity": ["qty", "kuantitas", "banyak", "stok", "stock", "unit", "pcs", "sks"],
    "money": ["harga", "biaya", "nominal", "saldo", "debit", "kredit", "pendapatan", "pemasukan", "pengeluaran",
              "anggaran", "gaji", "upah", "tagihan", "pembayaran", "bayar", "omzet", "laba", "rugi", "untung", "tarif",
              "spp", "ukt", "denda", "sisa", "piutang", "hutang", "utang", "rp", "idr", "amount", "price", "cost",
              "revenue", "salary", "balance", "payment", "income", "expense", "budget", "dana", "honor", "tunjangan",
              "potongan", "pajak", "bonus"],
    "amount": ["jumlah", "total", "subtotal", "sub total", "grand total"],
    "score_or_value": ["nilai"],
    "category": ["prodi", "program studi", "jurusan", "fakultas", "angkatan", "kelas", "semester", "status",
                 "jenis kelamin", "gender", "jk", "kota", "kabupaten", "provinsi", "wilayah", "divisi", "bagian",
                 "jabatan", "kategori", "tipe", "jenis", "golongan", "agama", "unit kerja", "departemen", "cabang",
                 "sumber", "metode", "kelompok", "tingkat", "tahun masuk", "kecamatan", "kelurahan", "pekerjaan"],
}
_INDEX_LABELS = {"no", "nomor", "no urut", "nomor urut", "urut", "num"}

# Kata kunci jenis data (dicocokkan ke label kolom + judul di atas tabel)
DOMAIN_KW = {
    "finance": ["keuangan", "saldo", "debit", "kredit", "pendapatan", "pengeluaran", "pemasukan", "anggaran",
                "biaya", "laba", "rugi", "kas", "bank", "transaksi", "invoice", "tagihan", "pembayaran", "omzet",
                "rab", "neraca", "jurnal", "akun", "nominal", "revenue", "expense", "budget", "balance", "payment",
                "amount", "price", "harga", "total", "gaji", "pajak", "spp", "ukt"],
    "academic": ["nim", "npm", "stambuk", "stb", "nisn", "mahasiswa", "siswa", "prodi", "program studi", "jurusan",
                 "fakultas", "angkatan", "ipk", "ips", "semester", "nilai", "kelas", "dosen", "mata kuliah",
                 "matakuliah", "sks", "krs", "khs", "kampus", "akademik", "wisuda", "lulus", "sertifikasi", "uji kompetensi"],
    "hr": ["nip", "pegawai", "karyawan", "jabatan", "golongan", "gaji", "divisi", "absensi", "cuti", "departemen",
           "employee", "salary", "payroll", "tunjangan", "unit kerja"],
    "sales": ["produk", "barang", "stok", "stock", "qty", "penjualan", "pelanggan", "customer", "supplier",
              "pembelian", "order", "item", "sku", "gudang", "inventory", "pesanan"],
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


def _norm_label(v):
    t = re.sub(r"[^a-z0-9 ]", " ", str(v).lower().replace(".", " ").replace("_", " ").replace("/", " "))
    return re.sub(r"\s+", " ", t).strip()


def _kw_in(text, kw):
    return re.search(r"(?<![a-z0-9])" + re.escape(kw) + r"(?![a-z0-9])", text) is not None


def _hint_role(label):
    """Tebak peran kolom dari teks labelnya (kata kunci terpanjang menang). None kalau tak dikenali."""
    s = _norm_label(label)
    if not s:
        return None
    if s in _INDEX_LABELS:
        return "index"
    best_len, best = 0, None
    for role, kws in ROLE_KW.items():
        for kw in kws:
            if len(kw) > best_len and _kw_in(s, kw):
                best_len, best = len(kw), role
    return best


def _digits_of(v):
    if isinstance(v, float) and v == v and v == int(v):
        v = int(v)
    t = str(v).strip()
    return t if t.isdigit() else None


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

    col_display = {}  # "B" -> "B (NIM)" : huruf kolom + label-nya (diisi setelah label dikenali)

    def tx(key, **kw):
        if isinstance(kw.get("col"), str) and kw["col"] in col_display:
            kw["col"] = col_display[kw["col"]]
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

    # ---- Profil: label kolom, peran kolom, jenis data ----
    labels, label_rows = {}, {}
    for c in range(ncols):
        nb = [i for i, k in enumerate(kinds[c]) if k[0] != "blank"]
        cand = max(skip[c]) if skip[c] else None
        if cand is None:
            for i in nb[:3]:
                if i <= 8 and kinds[c][i][0] == "text" and len(str(val(i, c)).strip()) <= 40 and _hint_role(val(i, c)):
                    cand = i
                    break
        if cand is None and nb and nb[0] <= 8 and kinds[c][nb[0]][0] == "text" and len(str(val(nb[0], c)).strip()) <= 30:
            rest = [kinds[c][i][0] for i in nb[1:]]
            if len(rest) >= 3 and sum(1 for k in rest if k in ("num", "numtext", "date")) / len(rest) >= 0.8:
                cand = nb[0]
        if cand is not None:
            labels[c] = str(val(cand, c)).strip()
            label_rows[c] = cand
            skip[c].add(cand)

    def num_value(i, c):
        k = kinds[c][i][0]
        if k == "num":
            return float(val(i, c))
        if k == "numtext":
            return _parse_numtext(str(val(i, c)).strip())
        return None

    for c, lb in labels.items():
        if lb:
            col_display[_letters(c)] = f"{_letters(c)} ({_short(lb, 20)})"
    hints = {c: _hint_role(labels[c]) for c in labels}
    roles, id_meta = {}, {}
    for c in range(ncols):
        idx = [i for i, k in enumerate(kinds[c]) if k[0] != "blank" and i not in skip[c]]
        if not idx:
            roles[c] = "empty"
            continue
        n = len(idx)
        hint = hints.get(c)
        kc = Counter(kinds[c][i][0] for i in idx)
        raw = [val(i, c) for i in idx]
        uniq = len({str(v).strip().lower() for v in raw}) / n
        if kc["date"] / n >= 0.6 or (hint == "date" and kc["date"] / n >= 0.3):
            roles[c] = "date"
        elif (kc["num"] + kc["numtext"]) / n >= 0.8:
            digs = [_digits_of(v) for v in raw]
            all_int = all(d is not None for d in digs)
            lens = Counter(len(d) for d in digs if d)
            mode_len, mode_n = lens.most_common(1)[0] if lens else (0, 0)
            nums_ = [num_value(i, c) for i in idx]
            seq = (all_int and n >= 5 and None not in nums_
                   and sum(1 for a, b in zip(nums_, nums_[1:]) if b - a == 1) / (n - 1) >= 0.9)
            leading_zero_phone = all_int and mode_n / n >= 0.8 and 9 <= mode_len <= 15 and sum(
                1 for v in raw if str(v).strip().startswith(("0", "62", "+62"))) / n >= 0.8
            round_amounts = sum(1 for x in nums_ if x is not None and x % 1000 == 0) / n >= 0.8
            if hint == "index":
                roles[c] = "index"
            elif hint == "id" and all_int and uniq >= 0.5:
                roles[c] = "id"
            elif hint in ("money", "quantity", "score", "percent", "amount", "score_or_value"):
                roles[c] = hint  # label yang jelas mengalahkan tebakan dari pola angka
            elif hint == "contact" or leading_zero_phone:
                roles[c] = "contact"
            elif hint == "category" and len({str(v).strip() for v in raw}) <= 20:
                roles[c] = "category"
            elif seq and nums_[0] is not None and nums_[0] <= 1 and mode_len < 6:
                roles[c] = "index"  # 1, 2, 3, ... = nomor urut
            elif all_int and uniq >= 0.95 and n >= 5 and mode_len >= 6 and mode_n / n >= 0.8 and not round_amounts:
                roles[c] = "id"
            else:
                roles[c] = "measure"
            if roles[c] == "id":
                id_meta[c] = (mode_len, mode_n / n)
        else:
            texts_ = [str(v).strip() for v in raw]
            if sum(1 for x in texts_ if "@" in x and "." in x) / n >= 0.6 or hint == "contact":
                roles[c] = "contact"
            elif hint == "id":
                roles[c] = "id"
            elif hint == "name":
                roles[c] = "name"
            elif hint == "category":
                roles[c] = "category"
            elif (n >= 5 and uniq >= 0.9
                  and sum(1 for x in texts_ if re.fullmatch(r"[A-Za-z0-9\-_/.]{3,24}", x) and re.search(r"\d", x)) / n >= 0.8):
                roles[c] = "id"
            elif (n >= 5 and uniq >= 0.7
                  and sum(1 for x in texts_ if re.fullmatch(r"[^\W\d_]+([ .'\-][^\W\d_]+)+", x)) / n >= 0.8):
                roles[c] = "name"
            elif n >= 5 and len(set(texts_)) <= 20 and len(set(texts_)) / n <= 0.5:
                roles[c] = "category"
            else:
                roles[c] = "text"

    # Jenis data: kata kunci label kolom + judul di atas tabel, lalu pola isi data
    dom = Counter()
    seen_kw = set()
    title_texts = [str(val(r, c)).strip() for r in range(min(3, nrows)) for c in range(ncols)
                   if kinds[c][r][0] == "text" and label_rows.get(c) != r and len(str(val(r, c)).strip()) <= 80]
    for text, w in [(_norm_label(x), 2) for x in labels.values()] + [(_norm_label(x), 3) for x in title_texts]:
        for typ, kws in DOMAIN_KW.items():
            for kw in kws:
                if _kw_in(text, kw):
                    dom[typ] += w
                    seen_kw.add(kw)
    cur_cells = sum(1 for c in range(ncols) for i, k in enumerate(kinds[c])
                    if k[0] == "numtext" and _CURRENCY_RE.match(str(val(i, c)).strip()))
    if cur_cells >= 3:
        dom["finance"] += 3
    dom["finance"] += min(6, 2 * sum(1 for c in range(ncols) if roles[c] == "money"))
    top_type, top_score = (dom.most_common(1)[0] if dom else (None, 0))
    id_cols = [c for c in range(ncols) if roles[c] == "id"]
    has_name = any(roles[c] == "name" for c in range(ncols))
    if top_type and top_score >= 2:
        dtype = top_type
    elif id_cols and has_name:
        dtype = "roster"
    else:
        dtype = "generic"
    confidence = "high" if top_score >= 5 else "medium" if top_score >= 2 else "low"

    # Peran ambigu ("jumlah/total", "nilai") ditentukan oleh jenis data; angka besar di data keuangan = uang
    for c in range(ncols):
        if roles[c] == "amount":
            roles[c] = "money" if dtype == "finance" else "quantity"
        elif roles[c] == "score_or_value":
            roles[c] = "money" if dtype == "finance" else "score"
        elif roles[c] == "measure" and dtype == "finance":
            xs_ = [abs(x) for x in (num_value(i, c) for i, k in enumerate(kinds[c]) if k[0] in ("num", "numtext")
                                    and i not in skip[c]) if x is not None]
            if xs_ and statistics.median(xs_) >= 1000:
                roles[c] = "money"

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

    for c in id_cols[:3]:
        idxs = [i for i, k in enumerate(kinds[c]) if k[0] != "blank" and i not in skip[c]]
        norm = {i: (_digits_of(val(i, c)) or str(val(i, c)).strip().lower()) for i in idxs}
        cnt = Counter(norm.values())
        dups = {v for v, n_ in cnt.items() if n_ > 1}
        if dups:
            cells = [(i, c) for i in idxs if norm[i] in dups]
            ex = ", ".join(_short(v, 16) for v in list(dups)[:3])
            add(f"dup_key_{c}", "duplicate", "high", tx("dup_keys.t", col=_letters(c)),
                tx("dup_keys.d", col=_letters(c), n=len(cells), ex=ex), len(cells), _cells_rects(cells))
        mode_len, share = id_meta.get(c, (0, 0))
        if mode_len and share >= 0.8:
            odd = [i for i in idxs if _digits_of(val(i, c)) and len(_digits_of(val(i, c))) != mode_len]
            if odd:
                ex = ", ".join(_short(val(i, c), 16) for i in odd[:3])
                add(f"id_len_{c}", "format", "medium", tx("id_len.t", col=_letters(c)),
                    tx("id_len.d", n=len(odd), len=mode_len, ex=ex), len(odd), _cells_rects([(i, c) for i in odd]))

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
        if roles[c] not in NUMERIC_ROLES and roles[c] != "date":
            continue  # ID/nama/kontak/kategori/teks tidak dinilai "angka nyasar"
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

    # ---- 5. Analisa & bahan grafik/pivot (hanya kolom yang bermakna menurut perannya) ----
    PREF = {"money": 0, "quantity": 1, "measure": 2, "score": 3, "percent": 4}
    numeric, categories, dates, histograms = [], [], [], []
    num_cols, cat_rows, date_cols = [], {}, []
    for c in range(ncols):
        L, role = _letters(c), roles[c]
        if role in NUMERIC_ROLES:
            nums = [(i, num_value(i, c)) for i in range(nrows)
                    if kinds[c][i][0] in ("num", "numtext") and i not in skip[c]]
            nums = [(i, v) for i, v in nums if v is not None]
            n_nb = sum(1 for i, k in enumerate(kinds[c]) if k[0] != "blank" and i not in skip[c])
            if len(nums) >= 3 and len(nums) / max(n_nb, 1) >= 0.6 and len(numeric) < 8:
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
                numeric.append({"col": L, "role": role, "n": len(xs), "min": min(xs), "max": max(xs),
                                "mean": sum(xs) / len(xs), "median": statistics.median(xs),
                                "outliers": len(outl)})
                if outl and len(outl) <= max(3, 0.1 * len(xs)):
                    add(f"outliers_{c}", "insight", "low", tx("outliers.t", col=L),
                        tx("outliers.d", n=len(outl), col=L, lo=_fmt_num(lo), hi=_fmt_num(hi)),
                        len(outl), _cells_rects(outl))
                if len(histograms) < 4:  # histogram (sebaran nilai)
                    k = min(10, max(5, int(math.log2(len(xs))) + 1))
                    mn, mx = min(xs), max(xs)
                    if mx > mn:
                        w = (mx - mn) / k
                        bins = [{"lo": mn + j * w, "hi": mn + (j + 1) * w, "count": 0, "cells": []} for j in range(k)]
                        for i, v in nums:
                            j = min(int((float(v) - mn) / w), k - 1)
                            bins[j]["count"] += 1
                            bins[j]["cells"].append((i, c))
                        histograms.append({"col": L, "role": role, "bins": [
                            {"lo": b_["lo"], "hi": b_["hi"], "count": b_["count"],
                             "targets": _cells_rects(b_["cells"])} for b_ in bins]})
        elif role == "category" and len(categories) < 3:
            vals = [(i, str(val(i, c)).strip()) for i in range(nrows)
                    if kinds[c][i][0] in ("text", "num", "numtext") and i not in skip[c]]
            if len(vals) >= 5:
                cnt = Counter(v for _, v in vals)
                if 2 <= len(cnt) <= 20 and len(cnt) / len(vals) <= 0.5:
                    top = cnt.most_common(8)
                    top_set = {k for k, _ in top}
                    categories.append({
                        "col": L, "ci": c, "total": len(vals), "unique": len(cnt),
                        "top": [{"value": _short(k, 22), "count": n_,
                                 "targets": _cells_rects([(i, c) for i, v in vals if v == k])}
                                for k, n_ in top],
                        "others": sum(1 for _, v in vals if v not in top_set)})
                    cat_rows[c] = (vals, [k for k, _ in top])
        elif role == "date":
            isod = sorted(str(val(i, c)).strip()[:10] for i in range(nrows)
                          if kinds[c][i][1] == "iso" and i not in skip[c])
            if len(isod) >= 3 and len(dates) < 3:
                dates.append({"col": L, "n": len(isod), "min": isod[0], "max": isod[-1]})
                date_cols.append(c)

    meas = sorted(num_cols, key=lambda c: PREF.get(roles[c], 9))

    # ---- Tabel pivot: kategori x (hitung + jumlah/rata-rata kolom angka bermakna) ----
    pivots = []
    for ci, (pvals, top_keys) in list(cat_rows.items())[:2]:
        measures = [nc for nc in meas if nc != ci][:2]
        top_set = set(top_keys)
        groups = [(k, [i for i, v in pvals if v == k]) for k in top_keys]
        rest = [i for i, v in pvals if v not in top_set]
        if rest:
            groups.append((None, rest))

        def agg(rows_):
            out = {"count": len(rows_), "m": []}
            for nc in measures:
                xs = [x for x in (num_value(i, nc) for i in rows_) if x is not None and i not in skip[nc]]
                out["m"].append({"sum": sum(xs), "avg": (sum(xs) / len(xs)) if xs else None})
            return out

        cat_index = next(j for j, cat in enumerate(categories) if cat["ci"] == ci)
        prow = []
        for gi, (k, rows_) in enumerate(groups):
            r_ = agg(rows_)
            r_["label"] = None if k is None else _short(k, 22)
            r_["src"] = [cat_index, gi] if k is not None else None
            prow.append(r_)
        pivots.append({"col": _letters(ci),
                       "measures": [{"col": _letters(nc), "role": roles[nc]} for nc in measures],
                       "rows": prow, "total": agg([i for i, _ in pvals])})

    # ---- Tren: kolom tanggal ISO x (jumlah kolom uang/kuantitas, atau jumlah data) per bulan/hari ----
    trend = None
    if date_cols:
        import datetime as _dt
        dc = date_cols[0]
        tm = [nc for nc in meas if roles[nc] in ("money", "quantity", "measure") and nc != dc][:2]
        ds = [(i, str(val(i, dc)).strip()[:10]) for i in range(nrows)
              if kinds[dc][i][1] == "iso" and i not in skip[dc]]
        try:
            span = (_dt.date.fromisoformat(max(d for _, d in ds)) - _dt.date.fromisoformat(min(d for _, d in ds))).days
        except ValueError:
            span = 0
        gran = "month" if span >= 60 else "day"
        width_ = max(len(tm), 1)
        agg_t = defaultdict(lambda: [0.0] * width_)
        for i, d in ds:
            key = d[:7] if gran == "month" else d
            if tm:
                for j, nc in enumerate(tm):
                    x = num_value(i, nc)
                    if x is not None and i not in skip[nc]:
                        agg_t[key][j] += x
            else:
                agg_t[key][0] += 1
        keys = sorted(agg_t)[-36:]
        if len(keys) >= 2:
            trend = {"date_col": _letters(dc), "cols": [_letters(nc) for nc in tm] or [tx("trend.count")],
                     "granularity": gran, "points": [{"label": k, "values": agg_t[k]} for k in keys]}

    # ---- Skor, urutan, kesimpulan ----
    findings.sort(key=lambda f: (SEV_ORDER[f["severity"]], -f["count"]))
    penalty = sum(SEV_WEIGHT[f["severity"]] for f in findings if f["category"] != "insight")
    score = max(0, min(100, 100 - penalty))
    grade = tx("grade.good") if score >= 85 else tx("grade.fair") if score >= 65 else tx("grade.poor")
    issues = [f for f in findings if f["category"] != "insight"]
    if not issues:
        findings.insert(0, {"id": "ok", "category": "ok", "severity": "ok", "title": tx("ok.t"),
                            "detail": tx("ok.d"), "count": 0, "targets": []})

    # ---- Profil jenis data + kartu ringkas (KPI) ----
    def fmt_big(x):
        return f"{x:,.0f}".replace(",", ".") if abs(x) >= 1000 else _fmt_num(x)

    n_records = sum(1 for r in range(nrows)
                    if any(kinds[c][r][0] != "blank" and r not in skip[c] for c in range(ncols)))
    def sn(c):  # nama pendek kolom untuk kartu: label kalau ada, kalau tidak huruf kolom
        return _short(labels[c], 22) if labels.get(c) else _letters(c)

    kpis = [{"label": tx("kpi.records"), "value": n_records}]
    concl_extra = []
    if dtype == "finance":
        for c in [c for c in num_cols if roles[c] == "money"][:3]:
            xs = [x for x in (num_value(i, c) for i in range(nrows) if i not in skip[c]) if x is not None]
            if xs:
                kpis.append({"label": tx("kpi.total", col=sn(c)), "value": sum(xs),
                             "sub": f"{tx('kpi.avg', col=sn(c))}: {fmt_big(sum(xs) / len(xs))}"})
                concl_extra.append(tx("c.fin", col=_letters(c), sum=fmt_big(sum(xs)),
                                      avg=fmt_big(sum(xs) / len(xs)), n=len(xs)))
    else:
        for c in id_cols[:1]:
            ids = [str(val(i, c)).strip() for i in range(nrows) if kinds[c][i][0] != "blank" and i not in skip[c]]
            kpis.append({"label": tx("kpi.unique", col=sn(c)), "value": len(set(ids))})
            concl_extra.append(tx("c.roster", n=n_records, ids=tx("c.ids", u=len(set(ids)), col=_letters(c))))
        if not id_cols:
            concl_extra.append(tx("c.roster", n=n_records, ids=""))
        for c in [c for c in num_cols if roles[c] == "score"][:2]:
            xs = [x for x in (num_value(i, c) for i in range(nrows) if i not in skip[c]) if x is not None]
            if xs:
                kpis.append({"label": tx("kpi.avg", col=sn(c)), "value": sum(xs) / len(xs)})
                concl_extra.append(tx("c.score", col=_letters(c), avg=_fmt_num(round(sum(xs) / len(xs), 2)),
                                      min=_fmt_num(min(xs)), max=_fmt_num(max(xs))))
    for cat in categories[: (1 if dtype == "finance" else 2)]:
        tp = cat["top"][0]
        kpis.append({"label": tx("kpi.top", col=sn(cat["ci"])), "value": tp["value"], "sub": str(tp["count"])})
        concl_extra.append(tx("c.topcat", col=cat["col"], v=tp["value"], n=tp["count"],
                              pct=round(100 * tp["count"] / max(cat["total"], 1))))

    why = []
    if labels:
        why.append(tx("why.labels", ex=", ".join(f"{_letters(c)}={_short(labels[c], 18)}" for c in list(labels)[:6])))
    else:
        why.append(tx("why.nolabels"))
    if id_cols:
        why.append(tx("why.ids", cols=", ".join(_letters(c) for c in id_cols)))
    money_cols = [c for c in range(ncols) if roles[c] == "money"]
    if money_cols:
        why.append(tx("why.money", cols=", ".join(_letters(c) for c in money_cols)))
    dataset = {
        "type": dtype, "label": tx("type." + dtype), "confidence": confidence, "why": why, "kpis": kpis[:7],
        "roles": [{"col": _letters(c), "label": labels.get(c, ""), "role": roles[c],
                   "role_label": tx("role." + roles[c])} for c in range(ncols)
                  if roles[c] != "empty"][:40],
    }

    concl = [tx("c.open", rows=nrows, cols=ncols, fill=fill_pct, score=score, grade=grade),
             tx("c.type", type=dataset["label"])] + concl_extra
    if issues:
        top = [f"{f['title']} ({f['count']})" for f in issues[:3]]
        concl.append(tx("c.prio", items="; ".join(top)))
    else:
        concl.append(tx("c.clean"))
    if dtype in ("finance", "sales", "generic") and (numeric or categories):
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
        "dataset": dataset,
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
