from __future__ import annotations

import os


LANGUAGE_NAMES = {
    "en": "English",
    "hi": "Hindi",
    "te": "Telugu",
    "ta": "Tamil",
}


def translate_text(text: str, target_language: str) -> str:
    target_language = (target_language or "en").strip().lower()
    if target_language == "en":
        return text

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is not configured.")

    from groq import Groq

    client = Groq(api_key=api_key)
    language_name = LANGUAGE_NAMES.get(target_language, "English")
    system_prompt = (
        "You are a medical translation assistant. Translate the user's message accurately "
        f"into natural, simple {language_name}. Keep the meaning unchanged. "
        "Return only the translated text with no explanation."
    )

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ],
        temperature=0.2,
        max_tokens=300,
    )
    translated = response.choices[0].message.content or ""
    return translated.strip()
