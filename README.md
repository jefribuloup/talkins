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

Sesuai isi repo `jefribuloup/talkins` saat ini:

```
talkins/
├── README.md
├── LICENSE
├── .gitignore
├── requirements.txt        <- Flask, pandas, openpyxl, psycopg2, requests, python-dotenv, python-pptx
├── vercel.json             <- build @vercel/python (api/index.py) + @vercel/static (static/**)
├── api/
│   └── index.py            <- entry point Flask: semua routing tipis, logic ada di lib/
├── lib/
│   ├── __init__.py
│   ├── db.py               <- koneksi Neon Postgres + schema + migrasi otomatis + query
│   ├── excel_engine.py     <- parsing/cleaning Excel, deteksi rumus, preview, manipulasi, export
│   ├── chat_engine.py      <- chat-with-data (Gemini Flash primary, Groq fallback)
│   ├── formula_engine.py   <- saran rumus untuk tab "Rumus" dari blok sel yang dipilih
│   ├── recap_engine.py     <- rekap otomatis sheet: duplikat, kosong, format, error, statistik, kesimpulan
│   └── slide_engine.py     <- slide dari data: build_deck, polish_deck (AI), render_pptx (.pptx native)
├── templates/
│   ├── index.html          <- landing page
│   ├── dashboard.html      <- daftar file + unggah file/link
│   ├── workspace.html      <- panel data (kiri) + tab Chat | Rumus | Rekap (kanan)
│   └── partials/
│       ├── navbar.html
│       └── footer.html
└── static/
    ├── css/
    │   ├── main.css        <- tema, layout umum, navbar/footer, dashboard
    │   └── workspace.css   <- tampilan workspace (tabel, tab, chat, popup)
    └── js/
        ├── router.js       <- router tanpa reload (ganti konten antara navbar dan footer)
        ├── preloader.js    <- preloader global di setiap fetch + progress bar
        ├── theme.js        <- toggle tema gelap/terang
        ├── i18n.js         <- teks Indonesia/Inggris
        ├── confirm.js      <- dialog konfirmasi global (MejaConfirm)
        ├── upload.js       <- unggah file/link dan daftar file di dashboard
        ├── workspace.js    <- tabel, pemilihan sel, chat (+ render Markdown), rekap, slide, manipulasi
        └── formula_tab.js  <- tab "Rumus" (blok sel -> saran rumus + pratinjau)
```

Catatan: `env-example.txt` yang disebut di bagian "3. Environment variables" belum ada
di repo (tidak ikut ter-commit). Daftar variabelnya tetap ada di bagian itu, atau buat
file tersebut dan commit (jangan isi dengan nilai rahasia sungguhan).

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
  GOOGLE_CLIENT_ID      -> OAuth Client ID (Google Cloud Console)
  GOOGLE_CLIENT_SECRET  -> OAuth Client Secret
  APP_BASE_URL    -> opsional, mis. https://talkins.vercel.app (dasar redirect URI)
  REQUIRE_LOGIN   -> opsional, isi 1 supaya aplikasi wajib login (default: tamu boleh)

Setup login Google: Google Cloud Console -> APIs & Services -> Credentials ->
Create Credentials -> OAuth client ID -> Web application. Isi "Authorized redirect URIs":
  https://DOMAIN-KAMU/auth/google/callback
  http://localhost:5000/auth/google/callback   (untuk uji lokal)
Layar persetujuan (OAuth consent screen) cukup scope: openid, email, profile.

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

## Kontrol biaya, penyimpanan, dan roadmap

> Status: bagian ini adalah SPESIFIKASI untuk pengerjaan berikutnya. Belum ada yang
> diimplementasikan. Angka harga/kuota di bawah adalah hipotesis awal; ukur dulu pemakaian
> nyata (Fase 2) sebelum dijadikan harga final. Batas gratis layanan pihak ketiga bisa
> berubah, cek halaman resmi mereka saat implementasi.

### A. Titik biaya yang bisa membengkak

| # | Titik biaya | Kondisi kode sekarang | Mitigasi (fase) |
|---|-------------|-----------------------|-----------------|
| 1 | Panggilan LLM (chat, slide) | `chat_engine.ask` bisa memanggil LLM 2x per pesan (pilih sheet + jawab); `_call_llm` jatuh ke Groq kalau Gemini gagal (2x per request); panggilan Groq belum punya batas token keluaran; tombol "polish" slide bisa ditekan berulang | Fase 0, 2, 3, 4 |
| 2 | Penyalahgunaan tanpa identitas | Tidak ada login; user = cookie `session_id`, sesi baru bisa dibuat tanpa batas | Fase 0 (rate limit), 1 (login) |
| 3 | Penyimpanan database | Tiap sheet disimpan 2x (`original_data` + `working_data`) sebagai JSONB + peta rumus, jauh lebih besar dari .xlsx aslinya; file dan chat tidak pernah kedaluwarsa | Fase 5 |
| 4 | Komputasi server | Parsing 20MB (pandas/openpyxl), rekap, dan deteksi rumus berat di fungsi serverless | Fase 0 (batas), 4 (cache) |
| 5 | Unduhan dari link | `ingest_from_url` mengunduh URL apa saja tanpa batas ukuran (risiko bandwidth/memori dan SSRF) | Fase 0 |
| 6 | Biaya tetap (Vercel, Neon, domain) | Naik pelan seiring jumlah user, mudah diprediksi | Pantau bulanan |

### B. Keputusan desain

**1. Link hanya dari Google Sheets dan Google Drive (allowlist ketat).**

Aturan untuk `excel_engine.ingest_from_url` / `_resolve_spreadsheet_url`:

- Hanya `https`. Host harus persis `docs.google.com` atau `drive.google.com`. Domain lain
  ditolak dengan pesan jelas ("Hanya link Google Sheets atau Google Drive yang didukung").
- Ambil ID file dengan regex, lalu bangun URL unduhan SENDIRI dari ID itu (jangan memakai
  URL dari user apa adanya, ini menutup celah SSRF):
  - Google Sheets: `https://docs.google.com/spreadsheets/d/{ID}/export?format=xlsx`
  - File Drive (.xlsx yang diunggah ke Drive): `https://drive.google.com/uc?export=download&id={ID}`
    (dari link `drive.google.com/file/d/{ID}/view` atau `...open?id={ID}`)
  - Folder Drive, Google Docs, dan Google Slides ditolak.
- Redirect diikuti manual (`allow_redirects=False`, maksimal 3 lompatan). Setiap tujuan
  harus berhost `docs.google.com`, `drive.google.com`, atau berakhiran
  `.googleusercontent.com` (Google memakai domain ini untuk file unduhan).
- Batas ukuran unduhan sama dengan `MAX_UPLOAD_MB` (20MB): cek `Content-Length` dan hitung
  byte saat streaming, hentikan kalau lewat. Timeout: connect 5 detik, read 20 detik.
- Validasi isi: file .xlsx harus diawali signature zip `PK`. Kalau yang datang HTML (halaman
  login Google atau peringatan Drive), tolak dengan pesan: pastikan akses file
  "Siapa saja yang memiliki link: Viewer".
- File privat belum didukung (butuh OAuth). Opsi lanjutan setelah login ada (Fase 1):
  Google Picker dengan scope `drive.file` agar user memilih file privatnya sendiri.
- Deteksi file dobel tetap memakai URL unduhan hasil normalisasi sebagai `source_url`.
- Update juga: placeholder dan teks error di `static/js/i18n.js` (kunci
  `workspace.upload.linkPlaceholder`, `workspace.upload.linkSubmit`), plus validasi sisi
  frontend di `workspace.js` supaya URL non-Google langsung ditolak sebelum dikirim.

**2. Penyimpanan di luar Vercel (gratis).**

Neon sudah layanan terpisah dari Vercel (integrasi Vercel hanya membuatkan databasenya).
Masalahnya bukan lokasi, tapi ukuran: paket gratis Neon dibatasi sekitar 0,5 GB per project,
dan data sheet (JSONB, disimpan 2x) adalah penyebab utama membengkak.

Rancangan yang disarankan:

| Data | Disimpan di | Alasan |
|------|-------------|--------|
| User, kuota/kredit, usage_events, metadata file, riwayat chat | Neon Postgres (tetap) | Kecil, perlu query dan transaksi |
| Isi sheet (`original`, `working`), peta rumus | Object storage: Cloudflare R2 (utama), cadangan Backblaze B2 | Besar; R2 gratis 10 GB, 1 juta operasi tulis dan 10 juta operasi baca per bulan, tanpa biaya egress (verifikasi di halaman resmi) |
| Riwayat chat lama | Tetap di Neon dengan retensi | Teks kecil; potong otomatis (mis. simpan 50 pesan terakhir per file) |

Catatan pemilihan:

- Supabase gratis (500 MB database, 1 GB file) kecil dan project-nya dijeda setelah sekitar
  seminggu tidak aktif, kurang cocok untuk produksi.
- Google Drive tidak disarankan sebagai penyimpanan backend aplikasi (kuota API, OAuth per
  user, dan latensi tidak stabil).
- Cek apakah pendaftaran R2 mensyaratkan metode pembayaran, dan pasang batas pengeluaran.

Susunan kunci objek: `u/{user_id}/{file_id}/{sheet_slug}.{original|working}.json.gz`.
Tabel `sheets` menyimpan pointer (kunci objek, ukuran, versi/etag) dan metadata ringan
(`columns`, `column_letters`, `row_order_dirty`), bukan isi data. Trade-off: setiap operasi
manipulasi jadi baca dan tulis objek (lebih lambat dari JSONB lokal); redam dengan memuat
sheet sekali per request dan menulis balik sekali.

**3. Kuota dalam satuan kredit (nilai awal, sesuaikan setelah mengukur).**

| Aksi | Kredit |
|------|--------|
| Pesan chat | 1 |
| Buat slide (1 panggilan AI) | 10 |
| Polish slide | 3 |
| Rekap, rumus, manipulasi, unduh salinan | 0 (tanpa AI) |

Cek kuota SEBELUM memanggil AI, bukan sesudahnya. Peringatan di 80%, berhenti di 100%
dengan tawaran upgrade atau beli paket kredit tambahan. Rate limit tambahan per menit.

**4. Paket (hipotesis untuk diuji beta).**

| Paket | Harga/bulan | Isi |
|-------|-------------|-----|
| Gratis | Rp0 | 1-3 file, file kecil, ~20 pesan, Rumus dan Rekap terbuka, slide watermark tanpa .pptx |
| Pribadi | ~Rp49-79 ribu | ~10 file sampai 20MB, ~300 pesan, ~10 slide, .pptx tanpa watermark, link Google |
| Pro | ~Rp129-199 ribu | File jauh lebih banyak, ~1.500 pesan, ~50 slide, semua tema, model AI lebih kuat |
| Tim/Instansi | ~Rp99-149 ribu per kursi (min. 3) atau flat per lembaga | Invoice, berbagi file |

Diskon tahunan 15-20%. Pembayaran lewat payment gateway lokal (mis. Midtrans atau Xendit
dengan QRIS/VA), cek biaya transaksinya.

### C. Urutan pengerjaan (checklist)

Kerjakan berurutan; tiap fase bisa di-deploy sendiri.

**Fase 0: Pengaman cepat (tanpa login)**
- [ ] Allowlist link Google Sheets/Drive + batas unduhan 20MB + validasi isi (bagian B.1)
- [ ] `max_tokens` untuk panggilan Gemini dan Groq di `lib/chat_engine.py` (env `MAX_OUTPUT_TOKENS`)
- [ ] Batasi retry/fallback: maksimal 1 percobaan ulang per request, plus timeout
- [ ] Saklar darurat: env `AI_ENABLED=false` mematikan fitur AI dengan pesan ramah
- [ ] Rate limit per session dan per IP, disimpan di Neon (memori proses tidak andal di serverless)
- [ ] Batas data: jumlah file aktif per sesi, jumlah baris dan sheet yang diproses; tolak sebelum diproses
- [ ] Pasang batas pengeluaran dan alert di dashboard penyedia AI, Vercel, dan Neon

**Fase 1: Login**
- [ ] Tabel `users` (`id UUID PK`, `email UNIQUE`, `plan DEFAULT 'free'`, `created_at`)
- [ ] Login email (magic link) atau Google OAuth; sesi memuat `user_id`
- [ ] Migrasi `files.session_id` menjadi `files.user_id`; file anonim lama diklaim atau dihapus
- [ ] Akun gratis wajib verifikasi email

**Fase 2: Pencatatan pemakaian (dasar semuanya)**
- [ ] Tabel `usage_events` (`id BIGSERIAL`, `user_id`, `kind`, `credits`, `tokens_in`,
  `tokens_out`, `model`, `file_id`, `created_at`) + indeks `(user_id, created_at)`
- [ ] Catat tiap panggilan AI (dan unggahan) dari `chat_engine.py` / route terkait
- [ ] Endpoint `GET /api/usage` (sisa kredit bulan ini) dan laporan biaya per user untuk admin
- [ ] Setelah 2-4 minggu data nyata: hitung biaya AI per user aktif, lalu finalkan harga

**Fase 3: Penegakan kuota**
- [ ] Fungsi `check_and_reserve(user_id, kind)` dipanggil sebelum setiap panggilan AI
- [ ] Meter kredit di UI, peringatan 80%, layar paywall di 100%
- [ ] Rate limit per user per menit; aksi gratis (tanpa AI) tidak dikuota

**Fase 4: Cache dan penghematan**
- [ ] Cache deck slide: kunci `hash(fingerprint + topik + sheet + bahasa)`
- [ ] Cache rekap dan profil data selama file belum diubah
- [ ] Ganti langkah "pilih sheet" dengan aturan sederhana (cocokkan nama sheet) sebelum memanggil AI
- [ ] Model bertingkat: model termurah default, model kuat hanya Pro atau permintaan sulit

**Fase 5: Penyimpanan eksternal dan retensi**
- [ ] Tambah klien R2 (S3-compatible, `boto3`) di modul baru `lib/storage.py`; env `R2_ACCOUNT_ID`,
  `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`
- [ ] Dual-write (JSONB + R2), skrip backfill, pindahkan pembacaan ke R2, lalu hapus JSONB
- [ ] Retensi: Vercel Cron (di `vercel.json`) menghapus file dan chat akun gratis/anonim setelah 7-30 hari
- [ ] Batas penyimpanan per paket

**Fase 6: Pembayaran**
- [ ] Integrasi payment gateway lokal + webhook yang mengubah `users.plan`
- [ ] Paket kredit tambahan untuk user berat; langganan tahunan

**Fase 7: Beta dan uji harga**
- [ ] 10-20 user beta, pantau biaya per user
- [ ] Survei kemauan membayar (empat pertanyaan: terlalu murah, murah, mahal, terlalu mahal)
- [ ] Sesuaikan kuota, harga, dan paket

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
- Link spreadsheet saat ini menerima URL apa saja dan mengunduhnya tanpa batas ukuran. Target:
  hanya Google Sheets/Drive dengan batas 20MB (lihat "Kontrol biaya, penyimpanan, dan roadmap",
  Fase 0).
- Belum ada batas pemakaian AI, kuota, maupun masa simpan data; semuanya ada di roadmap di atas.
