"""
Chat dengan data: kirim 'profil data' (schema + sample + statistik ringkas, bukan seluruh
isi file supaya hemat token) + histori + pertanyaan user ke LLM.
Primary: Gemini Flash (gratis). Fallback otomatis: Groq (Llama 3.3 70B, gratis) kalau
Gemini kena rate limit / error.
"""
import os
import time
import re
import json
import requests
import pandas as pd

from lib import db
from lib.excel_engine import records_to_df, _get_columns

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")
# Model cadangan kalau model utama sedang overload (503). Bisa diganti lewat env.
GEMINI_FALLBACK_MODEL = os.environ.get("GEMINI_FALLBACK_MODEL", "gemini-flash-lite-latest")
GEMINI_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
)

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

SYSTEM_PROMPT = (
    "Kamu adalah asisten analisis data di dalam sebuah web app 'chat with your data'. "
    "Kamu diberi profil ringkas dari file Excel yang diunggah user (schema, sample baris, "
    "statistik dasar) — BUKAN seluruh isi file. Jawab pertanyaan user tentang data tersebut "
    "dengan jelas, boleh sertakan angka/insight, dan gunakan bahasa yang sama dengan user. "
    "Jika user minta ubah/olah data, jelaskan hasilnya secara naratif (perubahan data yang "
    "sesungguhnya dilakukan lewat fitur manipulasi terpisah di UI, bukan oleh chat ini). "
    "Jika informasi tidak cukup dari profil data, katakan dengan jujur keterbatasannya. "
    "Data disajikan sebagai grid mentah seperti di Excel: kolom dirujuk dengan huruf (A, B, C...) "
    "dan baris dengan nomor baris (1, 2, 3...). TIDAK ada baris header khusus — kalau baris tertentu "
    "terlihat berisi judul/label kolom, simpulkan sendiri dari isinya dan rujuk selnya dengan alamat "
    "seperti B3.\n\n"
    "Format jawaban pakai Markdown ringan supaya enak dibaca di chat (jangan berlebihan):\n"
    "- **Tebalkan** istilah kunci, nama sheet/kolom, dan angka penting — jangan seluruh kalimat.\n"
    "- Pakai bullet list ('- ') untuk poin-poin sejenis, numbered list ('1. ') kalau urutan penting.\n"
    "- Pakai heading pendek ('### Judul') hanya kalau jawabannya punya beberapa bagian berbeda; "
    "untuk jawaban singkat/satu poin, cukup paragraf biasa tanpa heading.\n"
    "- Jangan pakai tabel Markdown (belum didukung tampilannya)."
)


MAX_PROFILE_CHARS = 6000       # batas total profil data yg dikirim ke LLM
MAX_HISTORY_MESSAGES = 8       # batas jumlah pesan histori yg dikirim
MAX_HISTORY_MSG_CHARS = 800    # batas panjang tiap pesan histori
MAX_USER_MESSAGE_CHARS = 4000  # batas panjang pesan user
SHEET_SELECT_HISTORY_TAIL = 4  # jumlah pesan histori terakhir yg dilihat saat memilih sheet


# ---------- Bangun 'profil data' hemat token ----------

def _profile_sheet(file_id, sheet_name, max_sample_rows=5, max_cat_values=5):
    columns = _get_columns(file_id, sheet_name)
    records = db.get_sheet(file_id, sheet_name, version="working")
    df = records_to_df(records, columns)

    lines = [f"### Sheet: {sheet_name} ({len(df)} baris, {len(columns)} kolom)"]
    lines.append(f"Kolom (huruf Excel): {', '.join(columns)}")

    formula_columns = db.get_formula_columns(file_id, sheet_name)
    if formula_columns:
        parts = []
        for col, info in formula_columns.items():
            sample = info.get("sample", "")
            parts.append(f"{col} (mis. {sample})" if sample else col)
        lines.append(
            "Kolom berisi RUMUS/FORMULA Excel bawaan (nilai di data adalah hasil "
            "hitungannya, bukan input manual): " + "; ".join(parts)
        )

    # Baris-baris pertama apa adanya, diberi nomor baris Excel (mulai 1). Tidak ada header.
    sample = df.head(max_sample_rows).copy()
    sample.index = sample.index + 1
    lines.append(f"{len(sample)} baris pertama (kolom 'baris' = nomor baris Excel):")
    lines.append(sample.to_csv(index=True, index_label="baris"))

    for col in df.columns:
        series = pd.to_numeric(df[col], errors="coerce")
        if series.notna().sum() >= max(3, int(len(df) * 0.5)):
            lines.append(
                f"Statistik '{col}': min={series.min():.2f}, max={series.max():.2f}, "
                f"rata-rata={series.mean():.2f}, jumlah_null={series.isna().sum()}"
            )
        else:
            top = df[col].astype(str).value_counts().head(max_cat_values)
            top_str = ", ".join(f"{k}({v})" for k, v in top.items())
            lines.append(f"Nilai tersering '{col}': {top_str}")

    return "\n".join(lines)


def _build_data_profile(file_id, relevant_sheets, all_sheet_names):
    """Bangun profil HANYA untuk sheet yang relevan (relevant_sheets), bukan
    semua sheet di file — inti dari penghematan token. Sheet lain yang tidak
    diprofilkan tetap disebut namanya (super murah) supaya LLM tahu itu ada
    dan bisa minta klarifikasi kalau ternyata dibutuhkan."""
    parts = [_profile_sheet(file_id, s) for s in relevant_sheets]
    profile = "\n\n".join(parts)
    if len(profile) > MAX_PROFILE_CHARS:
        profile = (
            profile[:MAX_PROFILE_CHARS]
            + "\n\n...(profil dipotong karena file/sheet terlalu besar untuk dikirim penuh ke AI)"
        )

    skipped = [s for s in all_sheet_names if s not in relevant_sheets]
    if skipped:
        profile += (
            "\n\n(Sheet lain yang tersedia di file ini tapi TIDAK diprofilkan di atas "
            "karena dianggap tidak relevan dengan pertanyaan saat ini: "
            + ", ".join(skipped)
            + ". Kalau ternyata user butuh salah satu sheet ini, minta dia sebutkan "
            "nama sheetnya secara eksplisit.)"
        )
    return profile


# ---------- Seleksi sheet relevan (hemat token) ----------

def _sheet_index_text(file_id, sheet_names):
    """Index super ringan: cuma nama sheet + nama kolom, TANPA data/statistik.
    Jauh lebih murah daripada profil penuh — dipakai untuk MEMILIH sheet mana
    yang relevan, sebelum profil penuh dibangun."""
    lines = []
    for s in sheet_names:
        cols = _get_columns(file_id, s)
        # Tidak ada header -> beri petunjuk isi sheet dari beberapa sel teks pertama
        hint = []
        for row in db.get_sheet_head(file_id, s, 3):
            for v in row:
                if isinstance(v, str) and v.strip():
                    hint.append(v.strip()[:30])
                if len(hint) >= 8:
                    break
            if len(hint) >= 8:
                break
        lines.append(
            f"- {s}: {len(cols)} kolom"
            + (f"; sel teks awal: {' | '.join(hint)}" if hint else "")
        )
    return "\n".join(lines)


def _heuristic_match_sheets(message, history, sheet_names):
    """Coba cocokkan nama sheet secara literal di pesan user, lalu di histori
    chat terbaru kalau tidak ketemu -> gratis, tanpa panggil LLM sama sekali."""
    text = (message or "").lower()
    hits = [s for s in sheet_names if s.lower() in text]
    if hits:
        return hits

    for h in reversed(history):
        content = (h.get("content") or "").lower()
        hits = [s for s in sheet_names if s.lower() in content]
        if hits:
            return hits
    return []


def _llm_select_sheets(file_id, message, history, sheet_names):
    """Fallback kalau pencocokan nama sheet gagal: minta LLM memilih sheet
    relevan HANYA berdasarkan index ringan (nama+kolom) + ekor histori chat,
    bukan berdasarkan data/profil penuh -> payload seleksi ini sengaja dibuat
    sekecil mungkin."""
    index_text = _sheet_index_text(file_id, sheet_names)
    tail = history[-SHEET_SELECT_HISTORY_TAIL:]
    convo_tail = "\n".join(f"{h['role']}: {h['content']}" for h in tail) or "(belum ada)"

    prompt = (
        "Daftar sheet yang tersedia di file ini beserta kolomnya:\n"
        f"{index_text}\n\n"
        f"Ekor percakapan terakhir:\n{convo_tail}\n\n"
        f"Pesan user sekarang: {message}\n\n"
        "Tugasmu HANYA memilih sheet mana yang relevan untuk menjawab pesan user "
        "ini, berdasarkan konteks obrolan di atas. Balas HANYA berupa JSON array "
        "berisi nama-nama sheet persis seperti tertulis di daftar, contoh: "
        '["Sheet1"]. Kalau pertanyaan bersifat umum, minta perbandingan/gabungan '
        "antar sheet, atau kamu tidak yakin, balas array berisi SEMUA nama sheet. "
        "Jangan ada teks atau penjelasan lain di luar JSON."
    )
    try:
        text, _ = _call_llm(
            "Kamu adalah router internal yang hanya memilih sheet relevan. "
            "Selalu balas JSON array valid saja, tanpa teks lain.",
            [],
            prompt,
        )
        cleaned = text.strip().strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
        picked = json.loads(cleaned)
        picked = [s for s in picked if s in sheet_names]
        if picked:
            return picked
    except Exception:
        pass
    # Fallback paling aman kalau seleksi gagal/ambigu: pakai semua sheet
    # (lebih baik boros token sesekali daripada salah/ke-skip datanya).
    return list(sheet_names)


def _select_relevant_sheets(file_id, message, history, sheet_names):
    if len(sheet_names) <= 1:
        return list(sheet_names)
    hits = _heuristic_match_sheets(message, history, sheet_names)
    if hits:
        return hits
    return _llm_select_sheets(file_id, message, history, sheet_names)


def _trim_history(history):
    """Batasi jumlah & panjang pesan histori supaya payload ke LLM (terutama
    Groq, yang punya limit ukuran request) tidak kena 413 Payload Too Large."""
    trimmed = history[-MAX_HISTORY_MESSAGES:]
    out = []
    for h in trimmed:
        content = h["content"]
        if len(content) > MAX_HISTORY_MSG_CHARS:
            content = content[:MAX_HISTORY_MSG_CHARS] + "…"
        out.append({"role": h["role"], "content": content})
    return out


# ---------- Panggilan LLM ----------

def _clean_error(e):
    """Pesan error aman ditampilkan: buang query string (?key=...) dan pola API key."""
    msg = re.sub(r"\?[^\s'\"]*", "", str(e))
    msg = re.sub(r"(AIza|AQ\.|gsk_|sk-)[A-Za-z0-9_\-\.]{10,}", "[disembunyikan]", msg)
    return msg


def _gemini_request(model, body):
    # API key lewat header (bukan ?key=) supaya tidak ikut muncul di pesan error/log.
    base = GEMINI_URL.rsplit("/models/", 1)[0]
    return requests.post(
        f"{base}/models/{model}:generateContent",
        headers={"x-goog-api-key": GEMINI_API_KEY},
        json=body,
        timeout=30,
    )


def _call_gemini(system_prompt, history, user_message):
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY belum diset")
    contents = []
    for h in history:
        role = "model" if h["role"] == "assistant" else "user"
        contents.append({"role": role, "parts": [{"text": h["content"]}]})
    contents.append({"role": "user", "parts": [{"text": user_message}]})

    body = {
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": contents,
    }
    # 503/429 = server Gemini sedang sibuk (sementara) -> coba ulang sebentar, lalu model cadangan.
    attempts = [GEMINI_MODEL, GEMINI_MODEL]
    if GEMINI_FALLBACK_MODEL and GEMINI_FALLBACK_MODEL != GEMINI_MODEL:
        attempts.append(GEMINI_FALLBACK_MODEL)
    resp = None
    for i, model in enumerate(attempts):
        resp = _gemini_request(model, body)
        if resp.status_code not in (429, 500, 502, 503, 504):
            break
        if i < len(attempts) - 1:
            time.sleep(1.5)
    resp.raise_for_status()
    data = resp.json()
    return data["candidates"][0]["content"]["parts"][0]["text"]


def _call_groq(system_prompt, history, user_message):
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY belum diset")
    messages = [{"role": "system", "content": system_prompt}]
    for h in history:
        messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": user_message})

    resp = requests.post(
        GROQ_URL,
        headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
        json={"model": GROQ_MODEL, "messages": messages, "temperature": 0.4},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"]


def _call_llm(system_prompt, history, user_message):
    errors = []
    for provider in (_call_gemini, _call_groq):
        try:
            text = provider(system_prompt, history, user_message)
            return text, provider.__name__.replace("_call_", "")
        except Exception as e:
            errors.append(f"{provider.__name__}: {_clean_error(e)}")
    raise RuntimeError("Semua provider LLM gagal -> " + " | ".join(errors))


# ---------- API dipakai route Flask ----------

_LANG_NAMES = {"id": "Indonesia", "en": "English"}


def _language_hint(lang):
    """Petunjuk bahasa jawaban: ikut bahasa yang dipakai user di pesannya; kalau pesannya
    ambigu/campuran/sangat singkat, ikut bahasa antarmuka yang sedang aktif di aplikasi."""
    name = _LANG_NAMES.get(lang, _LANG_NAMES["id"])
    return (
        "\n\nBahasa jawaban: gunakan bahasa yang dipakai user di pesan terakhirnya. "
        f"Kalau pesannya ambigu, campuran, atau sangat singkat, jawab dalam bahasa {name} "
        "(bahasa antarmuka aplikasi yang sedang aktif)."
    )


def ask(session_id, file_id, message, lang="id"):
    message = (message or "")[:MAX_USER_MESSAGE_CHARS]
    history = _trim_history(db.get_chat_history(session_id, file_id, limit=20))

    sheet_names = db.list_sheet_names(file_id)
    relevant_sheets = _select_relevant_sheets(file_id, message, history, sheet_names)
    profile = _build_data_profile(file_id, relevant_sheets, sheet_names)

    full_system = SYSTEM_PROMPT + _language_hint(lang) + "\n\nProfil data saat ini:\n" + profile
    answer, provider_used = _call_llm(full_system, history, message)

    db.save_chat_message(session_id, file_id, "user", message)
    db.save_chat_message(session_id, file_id, "assistant", answer)

    return {"answer": answer, "provider": provider_used, "sheets_used": relevant_sheets}


MAX_SLIDE_PROFILE_SHEETS = 6


def generate_slide_outline(file_id, spec):
    # Outline slide mencakup seluruh file (bukan satu pertanyaan), jadi profilkan beberapa sheet
    # pertama; sheet sisanya tetap disebut namanya di profil (lihat _build_data_profile).
    sheet_names = db.list_sheet_names(file_id)
    if not sheet_names:
        raise ValueError("File ini belum punya sheet yang bisa dibuat slide.")
    profile = _build_data_profile(file_id, sheet_names[:MAX_SLIDE_PROFILE_SHEETS], sheet_names)
    topic = spec.get("topic", "Ringkasan data")
    prompt = (
        f"Buatkan outline slide presentasi tentang: {topic}.\n"
        "Berdasarkan profil data berikut, balas HANYA dalam format JSON array, "
        "tanpa teks lain, tanpa markdown code fence. Setiap elemen array: "
        '{"title": "...", "bullets": ["...", "..."]}. Maksimal 8 slide.\n\n'
        f"Profil data:\n{profile}"
    )
    text, _ = _call_llm(
        "Kamu adalah asisten pembuat outline slide dari data. Selalu balas JSON valid saja.",
        [],
        prompt,
    )
    cleaned = text.strip().strip("`")
    if cleaned.lower().startswith("json"):
        cleaned = cleaned[4:].strip()
    slides = None
    for candidate in (cleaned, cleaned[cleaned.find("["): cleaned.rfind("]") + 1]):
        try:
            slides = json.loads(candidate)
            break
        except (json.JSONDecodeError, ValueError):
            continue
    if isinstance(slides, dict):  # LLM kadang membungkus: {"slides": [...]}
        slides = slides.get("slides")
    if not isinstance(slides, list) or not slides:
        slides = [{"title": "Ringkasan", "bullets": [text[:300]]}]
    return {"slides": slides}
