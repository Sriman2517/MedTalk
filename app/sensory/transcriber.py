from __future__ import annotations

import base64
import json
import mimetypes
import os
from pathlib import Path

import httpx


LANGUAGE_ALIASES = {
    "english": "en",
    "en": "en",
    "hindi": "hi",
    "hi": "hi",
    "telugu": "te",
    "te": "te",
    "tamil": "ta",
    "ta": "ta",
    "unknown": "unknown",
}


def transcribe_audio(audio_file_path: str) -> str:
    details = transcribe_audio_details(audio_file_path)
    return details["text"]


def _normalize_language(language: str | None) -> str:
    if not language:
        return "unknown"
    return LANGUAGE_ALIASES.get(str(language).strip().lower(), "unknown")


def _parse_json_text(raw_text: str) -> dict[str, str | bool]:
    cleaned = (raw_text or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        cleaned = cleaned.replace("json", "", 1).strip()
    data = json.loads(cleaned)
    return {
        "text": str(data.get("transcript", "")).strip(),
        "language": _normalize_language(str(data.get("language", ""))),
        "is_noisy": bool(data.get("is_noisy", False)),
        "reason": str(data.get("reason", "")).strip(),
        "provider": "gemini",
    }


def _gemini_transcribe(audio_path: Path) -> dict[str, str | bool]:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured.")

    mime_type = mimetypes.guess_type(audio_path.name)[0] or "audio/ogg"
    model = os.getenv("GEMINI_AUDIO_MODEL", "gemini-2.5-flash")
    prompt = (
        "You are a speech transcription assistant for a multilingual medical triage app. "
        "Listen to the audio and return strict JSON only with keys: "
        "transcript, language, is_noisy, reason. "
        "language must be one of: en, hi, te, ta, unknown. "
        "Use te for Telugu, hi for Hindi, ta for Tamil, en for English. "
        "If the speech is unclear, mixed, or unreliable, set is_noisy=true. "
        "Do not translate the transcript. Preserve what the speaker said as closely as possible."
    )

    encoded_audio = base64.b64encode(audio_path.read_bytes()).decode("utf-8")
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt},
                    {
                        "inline_data": {
                            "mime_type": mime_type,
                            "data": encoded_audio,
                        }
                    },
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.1,
            "responseMimeType": "application/json",
        },
    }

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    response = httpx.post(url, json=payload, timeout=60.0)
    response.raise_for_status()
    data = response.json()
    candidates = data.get("candidates", [])
    if not candidates:
        raise RuntimeError("Gemini returned no candidates.")

    parts = candidates[0].get("content", {}).get("parts", [])
    raw_text = "".join(part.get("text", "") for part in parts)
    parsed = _parse_json_text(raw_text)
    if not parsed["text"] and not parsed["is_noisy"]:
        parsed["is_noisy"] = True
        parsed["reason"] = parsed["reason"] or "Gemini returned an empty transcript."
    return parsed


def _groq_transcribe(audio_path: Path) -> dict[str, str | bool]:
    from groq import Groq

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is not configured.")

    client = Groq(api_key=api_key)
    with audio_path.open("rb") as audio_file:
        transcription = client.audio.transcriptions.create(
            file=(audio_path.name, audio_file.read()),
            model="whisper-large-v3-turbo",
            response_format="verbose_json",
        )

    text = getattr(transcription, "text", "") or ""
    language = getattr(transcription, "language", "") or ""
    return {
        "text": text.strip(),
        "language": _normalize_language(language),
        "is_noisy": False,
        "reason": "",
        "provider": "groq",
    }


def transcribe_audio_details(audio_file_path: str) -> dict[str, str | bool]:
    audio_path = Path(audio_file_path)
    if not audio_path.is_file():
        raise FileNotFoundError(f"Audio file not found: {audio_file_path}")

    errors: list[str] = []

    try:
        return _gemini_transcribe(audio_path)
    except Exception as exc:
        errors.append(f"gemini: {exc}")

    try:
        return _groq_transcribe(audio_path)
    except Exception as exc:
        errors.append(f"groq: {exc}")

    raise RuntimeError("Audio transcription failed. " + " | ".join(errors))
