# Meja Data

Web app Flask untuk mengunggah file Excel (single/multi-sheet, termasuk file kompleks
dengan header tidak rapi), mengobrol dengan isinya lewat LLM, memanipulasi datanya secara
aman (working copy di cloud — file lokal tidak pernah tersentuh), dan mengekspor hasilnya
jadi outline slide atau file Excel baru.

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
    db.py               <- koneksi Neon Postgres + schema + query
    excel_engine.py     <- parsing/cleaning Excel, preview, manipulasi, export
    chat_engine.py      <- chat-with-data (Gemini Flash primary, Groq fallback)
  templates/
    index.html          <- landing + upload
    workspace.html       <- panel data + panel chat
  static/
    css/
    js/

## Alur data (penting)

1. User upload file .xlsx -> dibaca dengan pandas, header terdeteksi otomatis walau
   ada baris judul/logo di atas tabel.
2. Hasil bersih disimpan dua kali ke Neon: original_data (snapshot, read-only) dan
   working_data (yang boleh diubah).
3. Semua fitur manipulasi (drop_na, filter, sort, dll) hanya mengubah working_data.
4. Tombol "Reset ke asli" menyalin ulang original_data -> working_data.
5. Tombol "Unduh salinan" membuat file .xlsx baru dari working_data — bukan menimpa
   apa pun di komputer user.

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
