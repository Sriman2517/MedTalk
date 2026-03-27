from __future__ import annotations

from pathlib import Path


LANGUAGE_MAP = {
    "en": "en",
    "hi": "hi",
    "te": "te",
    "ta": "ta",
}


def generate_audio_reply(text: str, language_code: str, output_filename: str) -> str:
    from gtts import gTTS

    output_path = Path(output_filename)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    tts = gTTS(text=text, lang=LANGUAGE_MAP.get(language_code, "en"))
    tts.save(str(output_path))
    return str(output_path.resolve())
