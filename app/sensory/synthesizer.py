import os
from gtts import gTTS

# Mapping your supported DB languages to gTTS language codes
GTTS_LANG_MAP = {
    "en": "en",  # English
    "hi": "hi",  # Hindi
    "te": "te",  # Telugu
    "ta": "ta"  # Tamil
}


def generate_audio_reply(text: str, language_code: str, output_filename: str = "reply.mp3") -> str:
    """
    Converts text to an MP3 audio file in the specified language.
    Returns the file path to the generated audio.
    """
    gtts_lang = GTTS_LANG_MAP.get(language_code, "en")

    try:
        # slow=False keeps the pacing natural.
        # For extremely low-literacy demographics, you can set slow=True for clearer diction.
        tts = gTTS(text=text, lang=gtts_lang, slow=False)

        output_path = os.path.join(os.getcwd(), output_filename)
        tts.save(output_path)

        return output_path
    except Exception as e:
        print(f"TTS Error: {e}")
        return ""


# --- Quick Local Test ---
if __name__ == "__main__":
    print("Testing gTTS Voice Synthesis...\n")

    test_text_hi = "नमस्ते, मैं मेडटॉक हूँ। मैं आपकी कैसे मदद कर सकता हूँ?"
    print(f"Generating Hindi audio for: '{test_text_hi}'")

    audio_path = generate_audio_reply(test_text_hi, "hi", "test_hindi_reply.mp3")

    if os.path.exists(audio_path):
        print(f"✅ Success! Audio saved to: {audio_path}")
        print("Play the file on your computer to hear the output.")