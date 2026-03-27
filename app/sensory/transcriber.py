from __future__ import annotations

import os
from pathlib import Path


def transcribe_audio(audio_file_path: str) -> str:
    from groq import Groq

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is not configured.")

    audio_path = Path(audio_file_path)
    if not audio_path.is_file():
        raise FileNotFoundError(f"Audio file not found: {audio_file_path}")

    client = Groq(api_key=api_key)
    with audio_path.open("rb") as audio_file:
        transcription = client.audio.transcriptions.create(
            file=(audio_path.name, audio_file.read()),
            model="whisper-large-v3-turbo",
            response_format="verbose_json",
        )

    text = getattr(transcription, "text", "") or ""
    return text.strip()
