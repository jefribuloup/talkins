# Meja Data

Web app Flask untuk mengunggah file Excel atau link spreadsheet (single/multi-sheet,
termasuk file kompleks dengan header tidak rapi), mengobrol dengan isinya lewat LLM,
menelusuri rumus/formula bawaannya sel per sel, memanipulasi datanya secara aman (working
copy di cloud — file lokal tidak pernah tersentuh), dan mengekspor hasilnya jadi outline
slide atau file Excel baru.

Ditulis modular (routing tipis di api/index.py, logic di lib/) supaya gampang
dipindah ke Next.js kalau skalanya membesar: tiap route Flask di sini punya padanan
1:1 sebagai API route Next.js nantinya.

## Struktur folder

meja-data/
  requirements.txt
  vercel.json
  env-example.txt       <- contoh isi environment variable (teks biasa, copy-paste ke Vercel)
  api/
    index.py            <- entry point Flask (semua routing)
  lib/
    db.py               <- koneksi Neon Postgres + schema + migrasi otomatis + query
    excel_engine.py     <- parsing/cleaning Excel, deteksi rumus, preview, manipulasi, export
    chat_engine.py      <- chat-with-data (Gemini Flash primary, Groq fallback)
  templates/
    index.html          <- landing + upload
    workspace.html       <- panel data (kiri) + tab Chat | Rumus (kanan)
  static/
    css/
    js/
      workspace.js      <- tabel, pemilihan sel, tab Rumus, chat (+ render Markdown)
      i18n.js           <- teks Indonesia/Inggris

## Alur data (penting)

1. User upload file .xlsx -> dibaca dengan pandas, header terdeteksi otomatis walau
   ada baris judul/logo di atas tabel.
2. Hasil bersih disimpan dua kali ke Neon: original_data (snapshot, read-only) dan
   working_data (yang boleh diubah).
3. Semua fitur manipulasi (drop_na, filter, sort, dll) hanya mengubah working_data.
4. Tombol "Reset ke asli" menyalin ulang original_data -> working_data.
5. Tombol "Unduh salinan" membuat file .xlsx baru dari working_data — bukan menimpa
   apa pun di komputer user.
6. Upload file dan link spreadsheet lewat jalur yang sama (_ingest_bytes di
   excel_engine.py), jadi semua fitur di bawah berlaku untuk keduanya.

## Fitur utama

### Chat yang hemat token
- Kalau file punya lebih dari satu sheet, chat_engine.py memilih dulu sheet yang relevan
  dengan konteks obrolan, lalu hanya sheet itu yang diprofilkan dan dikirim ke LLM.
- Urutan seleksi: (1) nama sheet disebut di pesan/riwayat chat terbaru — gratis, tanpa
  panggil LLM; (2) kalau tidak ketemu, satu panggilan LLM kecil dengan index ringan (nama
  sheet + nama kolom saja); (3) kalau ambigu atau minta gabungan, semua sheet dipakai.
- Sheet yang dipakai ditampilkan di bawah jawaban ("Sheet dianalisis: ...").
- Jawaban AI dirender sebagai Markdown ringan (tebal, miring, kode, heading, list) oleh
  renderer kecil di workspace.js (HTML di-escape dulu, tanpa library luar).

### Rumus/formula bawaan
- Saat ingest, file dibaca ulang dengan openpyxl (data_only=False) untuk mengambil string
  rumus asli, karena pandas hanya membaca hasil hitungnya. Hasilnya disimpan di dua kolom
  tabel sheets:
  - formula_columns : ringkasan per kolom (jumlah sel rumus, rasio, contoh rumus)
  - formula_cells   : peta rumus per sel + "bahan rumus" (kolom dan baris yang dirujuk,
                      dipetakan ke nama kolom tabel bersih)
- Kolom berumus diberi penanda kecil "ƒx" di header tabel, dan profil data untuk chat ikut
  menyebut kolom mana yang berisi rumus.

### Header tabel gaya spreadsheet
- Header tabel selalu dua baris: baris atas berisi huruf kolom A, B, C, ... (dan AA, AB, ...
  setelah Z), baris bawahnya berisi nama kolom hasil deteksi. Keduanya menempel di atas
  saat tabel di-scroll, dan klik salah satunya memilih satu kolom penuh.
- Huruf yang tampil adalah huruf kolom di sheet ASLI (disimpan di kolom column_letters),
  bukan urutan setelah dibersihkan. Jadi kalau data dimulai dari kolom B, header dimulai dari
  B, dan rumus seperti =C4*D4 langsung cocok dengan huruf di header. Menghapus kolom tidak
  menggeser huruf kolom lain.
- File yang diunggah sebelum fitur ini tidak punya huruf asli tersimpan; header memakai
  cadangan A, B, C berdasarkan urutan kolom yang tampil.

### Tab "Rumus" (gaya Excel)
- Klik sel di tabel untuk memilih; Shift+klik untuk rentang; Ctrl/Cmd+klik untuk beberapa
  sel; klik judul kolom atau nomor baris untuk memilih satu kolom/baris penuh; Esc untuk
  membersihkan pilihan.
- Tab Rumus (di sebelah tab Chat) menampilkan tiap sel terpilih: teks rumus, hasilnya, dan
  bahan rumusnya. Klik chip bahan rumus untuk menandai sel sumbernya di tabel. Sel campuran
  berlabel "Rumus", "Nilai statis" (sel manual di kolom berumus), atau "Data biasa".
- Tab Rumus terbuka otomatis hanya kalau sel yang diklik ada di kolom berumus.

## 1. Setup Neon Postgres

1. Di dashboard Vercel project kamu: Storage -> Create Database -> Neon (Postgres).
   Vercel otomatis mengisi env var DATABASE_URL / POSTGRES_URL.
2. Atau bikin manual di neon.tech, lalu ambil connection string (pooled) dan isi ke
   DATABASE_URL.
3. Inisialisasi schema (tabel files, sheets, chat_messages) — jalankan sekali lewat
   Python shell/endpoint sementara:

   from lib import db
   db.init_db()

   Cara paling gampang: tambahkan endpoint sementara @app.route("/api/_init-db") yang
   memanggil db.init_db(), akses sekali dari browser, lalu hapus lagi endpointnya.

   Kolom yang ditambahkan belakangan (formula_columns, formula_cells, row_order_dirty,
   files.source, column_letters, original_meta) dimigrasikan otomatis: db.get_conn() menjalankan ALTER TABLE ... ADD COLUMN
   IF NOT EXISTS sekali per proses. Jadi setelah update kode, tidak perlu membuka
   /api/_init-db lagi. Kalau tabel belum ada sama sekali (database baru), init_db() tetap
   diperlukan sekali untuk membuatnya.

## 2. API key LLM gratis

- Gemini Flash (primary): buat API key di https://aistudio.google.com/apikey (gratis,
  tanpa kartu kredit).
- Groq (fallback, dipakai otomatis kalau Gemini kena rate limit): buat API key di
  https://console.groq.com/keys (gratis).

## 3. Environment variables

Isi ke Vercel -> Project Settings -> Environment Variables. Contoh nilainya ada di
env-example.txt (format KEY=value, tinggal copy-paste satu-satu):

  SECRET_KEY      -> string acak untuk sesi Flask
  DATABASE_URL    -> connection string Neon Postgres
  GEMINI_API_KEY  -> API key Gemini (aistudio.google.com)
  GEMINI_MODEL    -> default gemini-2.0-flash
  GROQ_API_KEY    -> API key Groq (console.groq.com)
  GROQ_MODEL      -> default llama-3.3-70b-versatile

## 4. Deploy ke Vercel

npm i -g vercel     # kalau belum ada CLI-nya
cd meja-data
vercel               # ikuti prompt, link/buat project
vercel --prod        # deploy ke production

Atau lewat dashboard: Add New -> Project -> Import repo ini, isi environment
variables di atas sebelum deploy pertama.

## 5. Menjalankan lokal

pip install -r requirements.txt
export DATABASE_URL=...   # dan env var lain dari env-example.txt
python api/index.py

Buka http://localhost:5000

## Batasan versi ini

- Ukuran upload dibatasi 20MB (MAX_CONTENT_LENGTH di api/index.py).
- Riwayat chat & working copy disimpan per session_id (cookie Flask) — belum ada login
  user. Untuk multi-user sungguhan, tambahkan auth dan kaitkan session_id ke user id.
- Deteksi header otomatis pakai heuristik sederhana (baris dengan sel terisi & teks
  terbanyak); untuk file yang sangat tidak beraturan, mungkin perlu penyesuaian di
  lib/excel_engine.py::_detect_header_row.
- Rumus hanya terbaca dari .xlsx/.xlsm. Format .xls lama (engine xlrd) tidak menyimpan
  rumus dengan cara yang bisa dibaca ulang. Untuk link Google Sheets, rumus ikut terbaca
  selama hasil ekspor link tersebut berupa .xlsx yang memuat rumus.
- Rumus discan dari 500 baris pertama tiap sheet (MAX_FORMULA_SCAN_ROWS di
  lib/excel_engine.py). Baris setelah itu tetap tampil di tabel, tapi tanpa detail rumus.
- Peta rumus per sel disimpan menurut posisi baris saat upload. Setelah Urutkan, Filter,
  Buang duplikat, atau Buang baris kosong, posisi itu bergeser, jadi tab Rumus menampilkan
  info tingkat kolom saja (label "Tidak pasti") sampai user menekan Reset ke asli. Rename
  dan hapus kolom tetap disinkronkan.
- Referensi rumus lintas-sheet (mis. Sheet2!A1) tidak dipetakan ke kolom; rumusnya
  ditandai "juga merujuk sheet lain".
- File yang diunggah sebelum fitur rumus per sel ditambahkan tidak punya datanya; unggah
  ulang file tersebut untuk mengisi tab Rumus.
- "Reset ke asli" mengembalikan data, daftar kolom, info rumus, dan huruf kolom dari snapshot
  original_meta yang disimpan saat upload. File lama tanpa snapshot hanya dikembalikan
  datanya (daftar kolom dibiarkan apa adanya).
