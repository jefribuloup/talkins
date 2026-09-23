"""
Chat dengan data: kirim 'profil data' (schema + sample + statistik ringkas, bukan seluruh
isi file supaya hemat token) + histori + pertanyaan user ke LLM.
Primary: Gemini Flash (gratis). Fallback otomatis: Groq (Llama 3.3 70B, gratis) kalau
Gemini kena rate limit / error.
"""
import os
import json
import requests
import pandas as pd

from lib import db
from lib.excel_engine import records_to_df, _get_columns

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")
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
    "Jika informasi tidak cukup dari profil data, katakan dengan jujur keterbatasannya."
)


# ---------- Bangun 'profil data' hemat token ----------

def _profile_sheet(file_id, sheet_name, max_sample_rows=8, max_cat_values=5):
    columns = _get_columns(file_id, sheet_name)
    records = db.get_sheet(file_id, sheet_name, version="working")
    df = records_to_df(records, columns)

    lines = [f"### Sheet: {sheet_name} ({len(df)} baris, {len(columns)} kolom)"]
    lines.append(f"Kolom: {', '.join(columns)}")

    sample = df.head(max_sample_rows)
    lines.append("Contoh data:")
    lines.append(sample.to_csv(index=False))

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


def _build_data_profile(file_id):
    sheet_names = db.list_sheet_names(file_id)
    parts = [_profile_sheet(file_id, s) for s in sheet_names]
    return "\n\n".join(parts)


# ---------- Panggilan LLM ----------

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
    resp = requests.post(
        f"{GEMINI_URL}?key={GEMINI_API_KEY}",
        json=body,
        timeout=30,
    )
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
            errors.append(f"{provider.__name__}: {e}")
    raise RuntimeError("Semua provider LLM gagal -> " + " | ".join(errors))


# ---------- API dipakai route Flask ----------

def ask(session_id, file_id, message):
    profile = _build_data_profile(file_id)
    history = db.get_chat_history(session_id, file_id, limit=20)

    full_system = SYSTEM_PROMPT + "\n\nProfil data saat ini:\n" + profile
    answer, provider_used = _call_llm(full_system, history, message)

    db.save_chat_message(session_id, file_id, "user", message)
    db.save_chat_message(session_id, file_id, "assistant", answer)

    return {"answer": answer, "provider": provider_used}


def generate_slide_outline(file_id, spec):
    profile = _build_data_profile(file_id)
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
    try:
        slides = json.loads(cleaned)
    except json.JSONDecodeError:
        slides = [{"title": "Ringkasan", "bullets": [text[:300]]}]
    return {"slides": slides}
