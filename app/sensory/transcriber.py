import os
from groq import Groq
from dotenv import load_dotenv

load_dotenv()
client = Groq(api_key=os.environ.get("GROQ_API_KEY"))


def transcribe_audio(audio_file_path: str) -> str:
    """
    Takes a local audio file path, sends it to Groq's Whisper model,
    and returns the transcribed text. Automatically handles 12+ Indian languages.
    """
    if not os.path.exists(audio_file_path):
        return "Error: Audio file not found."

    try:
        with open(audio_file_path, "rb") as file:
            transcription = client.audio.transcriptions.create(
                file=(os.path.basename(audio_file_path), file.read()),
                model="whisper-large-v3",
                response_format="json",
                # Whisper auto-detects the language by default
            )
        return transcription.text
    except Exception as e:
        print(f"Transcription Error: {e}")
        return "Error: Could not process audio."


# --- Quick Local Test ---
if __name__ == "__main__":
    # To test this, put a short .mp3 or .wav or .ogg file in the same folder
    test_file = "test_patient_audio.wav"

    print(f"Testing Whisper Transcription on {test_file}...")
    if os.path.exists(test_file):
        result = transcribe_audio(test_file)
        print(f"\nPatient said: '{result}'")
    else:
        print(f"\nPlease place an audio file named '{test_file}' in this directory to test.")