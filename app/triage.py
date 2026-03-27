from __future__ import annotations
import json
import hashlib
import re
from pathlib import Path
from typing import Any


SUPPORTED_LANGUAGES = {
    "en": "English",
    "hi": "Hindi",
    "te": "Telugu",
    "ta": "Tamil",
}

from app.llm.guardrails import check_for_red_flags
from app.llm.interviewer import get_next_question
from app.llm.summarizer import generate_medical_brief
from app.llm.classifier import classify_specialty
from app.sensory.synthesizer import generate_audio_reply


def detect_language(text: str | None) -> str:
    if not text:
        return "en"
    if re.search(r"[\u0C00-\u0C7F]", text):
        return "te"
    if re.search(r"[\u0B80-\u0BFF]", text):
        return "ta"
    if re.search(r"[\u0900-\u097F]", text):
        return "hi"
    return "en"


def normalize_language(language: str | None) -> str:
    if language in SUPPORTED_LANGUAGES:
        return language
    return "en"


def friendly_language_list() -> str:
    return "1. English\n2. Hindi\n3. Telugu\n4. Tamil"


def parse_language_choice(text: str) -> str | None:
    cleaned = (text or "").strip().lower()
    mapping = {
        "1": "en",
        "english": "en",
        "2": "hi",
        "hindi": "hi",
        "3": "te",
        "telugu": "te",
        "4": "ta",
        "tamil": "ta",
    }
    return mapping.get(cleaned)


def default_context() -> dict[str, Any]:
    return {
        "draft_profile": {},
        "interview_answers": {},
        "interview_index": 0,
        "last_summary": None,
    }


def load_context(raw: str | None) -> dict[str, Any]:
    if not raw:
        return default_context()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return default_context()
    merged = default_context()
    merged.update(data)
    return merged


def dump_context(context: dict[str, Any]) -> str:
    merged = default_context()
    merged.update(context)
    return json.dumps(merged)


def get_question(user_message: str, db_messages: list[dict[str, Any]]) -> str:
    chat_history = []
    for msg in db_messages:
        # Ignore unsupported roles or empty inputs if necessary
        role = "user" if msg["sender_role"] == "patient" else "assistant"
        content = msg.get("translated_text") or msg.get("original_text") or ""
        chat_history.append({"role": role, "content": content})
    
    return get_next_question(user_message, chat_history)


def infer_urgency(summary_text: str) -> str:
    is_emergency, _ = check_for_red_flags(summary_text)
    if is_emergency:
        return "emergency"
    return "routine"


def infer_specialty(medical_brief_json: dict[str, Any]) -> str:
    classification = classify_specialty(medical_brief_json)
    rec = classification.get("recommended_specialty", "").lower()
    
    if "cardio" in rec:
        return "cardio"
    elif "pulmonol" in rec:
        return "pulmonology"
    elif "derm" in rec:
        return "dermatology"
    elif "gastro" in rec:
        return "gastroenterology"
    return "general_medicine"


def generate_case_summary(profile: dict[str, Any], db_messages: list[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    chat_history = []
    for msg in db_messages:
        role = "user" if msg["sender_role"] == "patient" else "assistant"
        content = msg.get("translated_text") or msg.get("original_text") or ""
        chat_history.append({"role": role, "content": content})
        
    medical_brief = generate_medical_brief(chat_history)
    
    assoc_symptoms = medical_brief.get("associated_symptoms", [])
    symptoms_str = ", ".join(assoc_symptoms) if isinstance(assoc_symptoms, list) else str(assoc_symptoms)
    
    lines = [
        f"Patient: {profile.get('name', 'Unknown')}, {profile.get('age', 'Unknown')} years, {profile.get('gender', 'Unknown')}.",
        f"Preferred language: {SUPPORTED_LANGUAGES.get(profile.get('preferred_language', 'en'), 'English')}.",
        f"Chief complaint: {medical_brief.get('chief_complaint', 'Not captured')}.",
        f"Duration: {medical_brief.get('duration', 'Not captured')}.",
        f"Severity: {medical_brief.get('severity', 'Not captured')}.",
        f"Associated symptoms: {symptoms_str}.",
        f"Narrative: {medical_brief.get('patient_narrative', 'Not captured')}.",
    ]
    
    urgency = "emergency" if medical_brief.get("red_flags_detected") else "routine"
    specialty = infer_specialty(medical_brief)
    
    lines.append(f"Urgency: {urgency}.")
    lines.append(f"Suggested specialty: {specialty}.")
    
    return "\n".join(lines), medical_brief


def translate_for_patient(text: str, language: str) -> str:
    language = normalize_language(language)
    if language == "en":
        return text
    return f"[{SUPPORTED_LANGUAGES[language]}] {text}"


def generate_real_audio(text: str, language: str) -> str:
    normalized_language = normalize_language(language)
    audio_dir = Path(__file__).resolve().parent / "static" / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{hashlib.sha256(f'{normalized_language}:{text}'.encode('utf-8')).hexdigest()}.mp3"
    output_path = audio_dir / filename

    print(
        f"[triage.generate_real_audio] language={normalized_language!r} output_path={str(output_path)!r}",
        flush=True,
    )
    generate_audio_reply(text, normalized_language, str(output_path.resolve()))
    print(
        f"[triage.generate_real_audio] generated public_url={'/static/audio/' + filename!r}",
        flush=True,
    )
    return f"/static/audio/{filename}"
