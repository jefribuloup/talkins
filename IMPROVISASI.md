# Analisis Kelemahan Talkins

Dasar analisis: repo `jefribuloup/talkins`, commit `334f3c7` (3 Nov 2026), dibaca langsung dari kode.
Hanya **analisis statis**: belum ada uji penetrasi dan belum ada beban. Temuan ditandai **[terverifikasi]** bila saya lihat langsung di kode, **[perlu cek]** bila hanya dugaan kuat.

---

## TINGGI

**T1. Mayoritas endpoint tidak memeriksa pemilik file** [terverifikasi]
`/api/files/<id>/` untuk `sheets`, `preview`, `recap`, `manipulate`, `cell`, `reset`, `download`, dan `export/slides` hanya menerima `file_id`. Pemeriksaan `session_id` hanya ada di `chat`, `list`, dan `delete`. Halaman `/workspace/<id>` juga tanpa cek.
Keamanannya kini bergantung pada UUID yang sulit ditebak. Siapa pun yang memegang link (riwayat browser, tangkapan layar, perangkat bersama) bisa membaca, mengubah, dan mengunduh file orang lain. Route `/cell` yang saya tambahkan juga belum memeriksanya.
**Perbaikan:** satu helper `require_file(file_id)` yang mencocokkan `files.session_id` dengan sesi aktif, dipakai di semua route di atas.

**T2. `guard.py` (rate limit, saklar AI, pencatatan biaya) tidak terpasang di route mana pun** [terverifikasi]
Tidak ada pemanggil `guard.guard()` atau `guard.check()` di `api/` maupun `lib/`. Akibatnya `/chat` (biaya LLM), `/upload`, `/upload-url`, slide, dan `/cell` tanpa batas. `AI_ENABLED=0` tidak berlaku, dan `usage_events` tidak terisi.
**Perbaikan:** pasang `guard.guard("chat", ai=True)` dan sejenisnya di awal tiap route sesuai jenisnya.

**T3. Unggah lewat link (`/api/upload-url`) rawan SSRF dan tanpa batas ukuran** [terverifikasi]
`requests.get(url, allow_redirects=True)` menerima URL apa pun (`localhost`, IP privat, alamat metadata cloud). Seluruh isi dimuat ke memori lewat `resp.content`, tanpa `stream` dan tanpa batas byte.
**Perbaikan:** blokir IP privat/loopback/link-local setelah resolve DNS (dan setelah tiap redirect), batasi redirect, pakai `stream=True` dengan batas maksimum byte, atau izinkan hanya domain tertentu.

**T4. Privasi dan aspek hukum belum ada** [terverifikasi]
Tidak ada halaman Kebijakan Privasi atau Syarat Layanan. Isi file pengguna (sampel baris dan statistik) dikirim ke Gemini/Groq tanpa pemberitahuan. Tidak ditemukan kebijakan retensi atau fitur hapus akun/seluruh data. Aplikasi menyimpan data pribadi (email, Google ID, nama), jadi perlu dasar hukum yang jelas (mis. UU No. 27/2022 tentang Pelindungan Data Pribadi). Konsultasikan dengan ahli hukum.
**Perbaikan:** halaman `/privasi` dan `/syarat`, notifikasi yang jelas bahwa data dikirim ke penyedia AI, tombol "hapus semua data saya", dan masa simpan otomatis untuk file tamu.

---

## SEDANG

**S1. `SECRET_KEY` punya nilai bawaan publik** [terverifikasi]
`app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")`. Di Vercel, login memang ditolak jika masih bawaan, tetapi sesi tamu tetap ditandatangani kunci itu. Aplikasi sebaiknya gagal jalan (fail-fast) saat produksi tanpa `SECRET_KEY`.

**S2. `/api/_init-db` masih terbuka** [terverifikasi]
Komentar kodenya sendiri berkata "HAPUS setelah dipakai sekali". Siapa pun yang login bisa memanggilnya, dan responsnya membocorkan `str(e)` mentah dari database. Hapus atau lindungi dengan token admin.

**S3. Tidak ada header keamanan** [terverifikasi]
Tidak ada `Content-Security-Policy`, `X-Frame-Options`/`frame-ancestors`, `X-Content-Type-Options`, `Strict-Transport-Security`, dan `Referrer-Policy`, baik di kode maupun `vercel.json`. Risiko clickjacking dan XSS tidak punya lapisan cadangan.

**S4. Ekspor `.xlsx` rentan formula injection** [terverifikasi]
`export_working_copy` memakai `df.to_excel` (openpyxl). Teks sel yang diawali `=` ditulis sebagai formula hidup. File sumber yang tidak tepercaya bisa membawa formula berbahaya ke file unduhan. Awali nilai teks berawalan `= + - @` dengan `'` atau set tipe sel sebagai string.

**S5. Arsitektur data tidak skala** [terverifikasi]
- `working_data` disimpan sebagai **satu blob JSON per sheet**, ditulis ulang utuh pada setiap perubahan dan dibaca utuh pada setiap `preview` (`get_sheet` lalu iris).
- Pembaruan baca-ubah-tulis tanpa kunci/versi, jadi dua tab bisa saling menimpa (lost update).
- `get_conn()` membuka koneksi baru di tiap fungsi (37 titik). Ini menambah latensi dan bisa menghabiskan batas koneksi Neon.
**Perbaikan:** kolom per sel atau chunk per 200 baris, kolom `version` untuk optimistic locking, dan connection pooling (Neon pooler).

**S6. Unggah tanpa batas baris, kolom, atau ukuran setelah dekompresi** [terverifikasi]
Batas ada pada ukuran berkas (4 MB di Vercel, 30 MB via Drive), tetapi tidak ditemukan batas jumlah baris/kolom saat ingest. File `.xlsx` yang kecil bisa mengembang besar di memori (decompression bomb). `maxDuration` hanya 60 detik.

**S7. CSRF hanya di endpoint autentikasi** [terverifikasi]
Token `X-CSRF-Token` hanya dipakai di `/auth/*`. Endpoint API lain (hapus, ubah, reset, chat, unggah) mengandalkan `SameSite=Lax` saja. Cukup untuk browser modern, tetapi tidak berlapis.

**S8. Endpoint slide menerima `deck` dari klien tanpa batas** [terverifikasi]
`/export/slides.pptx` dan `/export/slides/polish` memproses JSON `deck` apa adanya (tanpa batas ukuran/jumlah slide), sehingga bisa dipakai untuk membebani CPU dan memicu biaya LLM. Tidak ada rate limit karena T2.

---

## RENDAH

- **R1. `.gitignore` berisi `lib/`**: file baru di folder itu diabaikan git secara diam-diam (sudah menjebak `cell_formula.py`). Hapus baris `lib/` dan `lib64/`.
- **R2. Dependensi:** `requests` tertulis dua kali (satu dikunci, satu tidak), dan Flask/Werkzeug 3.0.3 sudah lama. Belum diaudit. Jalankan `pip-audit`.
- **R3. Tes minim:** hanya `lib/test_guard.py` (di dalam `lib/`) dan tidak ada CI. `excel_engine`, `formula_engine`, `auth`, dan `cell_formula` belum punya tes.
- **R4. Frontend monolit:** `workspace.js` ±2.400 baris, tanpa build/minify. Navigasi panah antar-sel belum ada, dan aksesibilitas (ARIA/fokus) minim **[perlu cek]**.
- **R5. Branding tidak konsisten:** "Meja Data" masih muncul di 5 tempat, dan kata "Excel" muncul 38 kali (24 di `i18n.js`, 14 di template), termasuk slogan "Ngobrol dengan Excel-mu". Ini bertentangan dengan `BRAND_GUIDE.md`.
- **R6. Observabilitas:** hanya `app.logger`, tanpa pemantauan/alert/pelacak galat.
- **R7. Sebagian pesan UI masih tertulis langsung dalam bahasa Indonesia** (mis. `alert`), tidak lewat i18n **[perlu cek]**.
- **R8. Data tamu terikat cookie:** hilang bila cookie dihapus, tanpa peringatan di UI.

---

## Yang sudah baik (pertahankan)

- Kata sandi di-hash, ada hash palsu untuk waktu respons seragam, dan akun dikunci setelah percobaan gagal.
- `session.clear()` saat login (anti session fixation), cookie `HttpOnly` + `SameSite` + `Secure` di Vercel.
- `_safe_next` mencegah open-redirect.
- `renderMarkdown` me-*escape* HTML lebih dulu, jadi jawaban AI aman dari XSS.
- Pesan galat ramah tanpa membocorkan stack trace.
- Ada rancangan `guard.py` yang matang (tinggal dipasang).

---

## Urutan perbaikan yang disarankan

1. **T2** pasang `guard` (cepat, langsung menahan biaya)
2. **T1** helper `require_file`
3. **T3** amankan unggah via link
4. **S1, S2, S3** kunci rahasia, hapus `_init-db`, header keamanan
5. **T4** halaman Privasi/Syarat + hapus data
6. **S4, S6, S8** lalu **S5** (perubahan arsitektur terbesar, terakhir)
7. **R1** (satu baris) bisa dikerjakan kapan saja, termasuk **R5** (branding) sesuai `BRAND_GUIDE.md`
