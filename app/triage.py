from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


SUPPORTED_LANGUAGES = {
    "en": "English",
    "hi": "Hindi",
    "te": "Telugu",
    "ta": "Tamil",
}

NATIVE_LANGUAGE_NAMES = {
    "en": "English",
    "hi": "हिंदी",
    "te": "తెలుగు",
    "ta": "தமிழ்",
}

LANGUAGE_SWITCH_LINES = {
    "en": "It seems you are speaking {language_name}. Do you want to switch the language?",
    "hi": "आप {language_name} बोल रहे हैं। क्या आप भाषा बदलना चाहते हैं?",
    "te": "మీరు {language_name} మాట్లాడుతున్నారు. మీరు భాషను మార్చాలనుకుంటున్నారా?",
    "ta": "நீங்கள் {language_name} பேசுகிறீர்கள் போலுள்ளது. மொழியை மாற்ற விரும்புகிறீர்களா?",
}

YES_NO_LABELS = {
    "en": ("Yes", "No"),
    "hi": ("हाँ", "नहीं"),
    "te": ("అవును", "వద్దు"),
    "ta": ("ஆம்", "இல்லை"),
}

QUESTION_FLOW = [
    (
        "chief_complaint",
        {
            "en": "Please describe the main problem in one or two sentences.",
            "hi": "Kripya apni mukhya takleef ek ya do vakyon mein batayiye.",
            "te": "Dayachesi mee mukhya samasya ni okati leka rendu vaakyalalo cheppandi.",
            "ta": "Ungal mukkiya pirachanaiyai oru allathu rendu vakkiyangalil sollunga.",
        },
    ),
    (
        "duration",
        {
            "en": "How long have you had this problem?",
            "hi": "Yeh takleef kitne samay se hai?",
            "te": "Ee samasya entha kalam nundi undi?",
            "ta": "Indha pirachanai evvalavu naal irukku?",
        },
    ),
    (
        "severity",
        {
            "en": "How severe is it right now on a scale from 1 to 10?",
            "hi": "Abhi dard ya takleef 1 se 10 tak kitni hai?",
            "te": "Ippudu ee ibbandi 1 nundi 10 varaku entha undi?",
            "ta": "Ippozhudhu indha kashtam 1 mudhal 10 varai evvalavu?",
        },
    ),
    (
        "associated_symptoms",
        {
            "en": "Any other symptoms like fever, cough, vomiting, dizziness, or pain elsewhere?",
            "hi": "Kya aur koi lakshan hain jaise bukhar, khansi, ulti, chakkar ya kahin aur dard?",
            "te": "Jwaram, daggu, vanti, talatiragadam, leka vere chotla noppi laanti inka lakshanalu unnaya?",
            "ta": "Kaichchal, irumal, vaandhi, mayakkam, allathu vera vali maadhiri innum lakshanangal ulladha?",
        },
    ),
    (
        "history",
        {
            "en": "Do you have any medical conditions, medicines, or allergies we should know about?",
            "hi": "Koi purani bimaari, chal rahi dawai, ya allergy hai kya?",
            "te": "Mee daggara unna rogalu, teesukuntunna mandulu, leka allergies emaina unnaya?",
            "ta": "Ungalukku mun irundha noi, eduthukondirukkum marundhu, allathu allergy irukka?",
        },
    ),
]

RED_FLAG_PATTERNS = {
    "emergency": [
        "chest pain",
        "trouble breathing",
        "breathing difficulty",
        "unconscious",
        "stroke",
        "severe bleeding",
        "fits",
        "seizure",
    ],
    "high": [
        "high fever",
        "vomiting continuously",
        "dehydration",
        "pregnant and bleeding",
    ],
}

SPECIALTY_HINTS = {
    "cardio": ["chest pain", "palpitation", "heart", "bp"],
    "pulmonology": ["cough", "breathing", "asthma", "wheezing"],
    "dermatology": ["rash", "itching", "skin", "eczema"],
    "gastroenterology": ["stomach", "abdomen", "vomit", "diarrhea"],
    "neurology": ["headache", "seizure", "stroke", "numbness", "dizziness"],
}


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
    return language if language in SUPPORTED_LANGUAGES else "en"


def get_language_name(language: str | None) -> str:
    return SUPPORTED_LANGUAGES.get(normalize_language(language), "English")


def get_native_language_name(language: str | None) -> str:
    return NATIVE_LANGUAGE_NAMES.get(normalize_language(language), "English")


def friendly_language_list() -> str:
    return "🗣️ Please choose your language:\n1️⃣ English\n2️⃣ हिंदी (Hindi)\n3️⃣ తెలుగు (Telugu)\n4️⃣ தமிழ் (Tamil)"


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


def build_language_switch_prompt(current_language: str, detected_language: str) -> str:
    current_language = normalize_language(current_language)
    detected_language = normalize_language(detected_language)
    language_name_english = get_language_name(detected_language)
    language_name_native = get_native_language_name(detected_language)

    current_line = LANGUAGE_SWITCH_LINES.get(current_language, LANGUAGE_SWITCH_LINES["en"]).format(
        language_name=language_name_english
    )
    detected_line = LANGUAGE_SWITCH_LINES.get(detected_language, LANGUAGE_SWITCH_LINES["en"]).format(
        language_name=language_name_native
    )
    yes_native, no_native = YES_NO_LABELS.get(detected_language, YES_NO_LABELS["en"])

    return (
        f"🔄 {current_line}\n"
        f"{detected_line}\n\n"
        f"👍 1. Yes / {yes_native}\n"
        f"👎 2. No / {no_native}"
    )


def is_affirmative_language_switch(text: str) -> bool:
    cleaned = (text or "").strip().lower()
    return cleaned in {"1", "yes", "y", "haan", "ha", "हाँ", "avunu", "అవును", "aam", "ஆம்"}


def is_negative_language_switch(text: str) -> bool:
    cleaned = (text or "").strip().lower()
    return cleaned in {"2", "no", "n", "nahin", "nahi", "नहीं", "vaddu", "వద్దు", "illai", "இல்லை"}


def should_prompt_language_switch(current_language: str, detected_language: str, text: str) -> bool:
    current_language = normalize_language(current_language)
    detected_language = normalize_language(detected_language)
    cleaned = (text or "").strip()
    if not cleaned or current_language == detected_language:
        return False

    # Ignore tiny fragments, numbers, durations, and mixed shorthand like "3 hrs".
    if len(cleaned) < 8:
        return False
    tokens = re.findall(r"\w+", cleaned.lower())
    if len(tokens) <= 2 and any(char.isdigit() for char in cleaned):
        return False
    common_fragment_tokens = {
        "hr", "hrs", "hour", "hours", "day", "days", "week", "weeks",
        "month", "months", "kg", "cm", "mm", "yes", "no", "ok",
    }
    if tokens and all(token in common_fragment_tokens or token.isdigit() for token in tokens):
        return False

    script_patterns = {
        "hi": r"[\u0900-\u097F]",
        "te": r"[\u0C00-\u0C7F]",
        "ta": r"[\u0B80-\u0BFF]",
    }
    if detected_language in script_patterns:
        return len(re.findall(script_patterns[detected_language], cleaned)) >= 4

    # Switching from a non-English language to English should require a stronger signal.
    if detected_language == "en" and current_language != "en":
        english_letters = len(re.findall(r"[A-Za-z]", cleaned))
        return english_letters >= 12 and len(tokens) >= 3

    return False


def default_context() -> dict[str, Any]:
    return {
        "draft_profile": {},
        "interview_answers": {},
        "interview_index": 0,
        "last_summary": None,
        "temp_symptom": None,
        "temp_detected_language": None,
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


def build_chat_history(db_messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    chat_history: list[dict[str, str]] = []
    for msg in db_messages:
        sender_role = msg.get("sender_role")
        if sender_role == "patient":
            role = "user"
        else:
            role = "assistant"
        content = msg.get("translated_text") or msg.get("original_text") or ""
        if content.strip():
            chat_history.append({"role": role, "content": content.strip()})
    return chat_history


def get_question(index_or_message: int | str, language_or_messages: str | list[dict[str, Any]]) -> str:
    if isinstance(index_or_message, int):
        _, prompts = QUESTION_FLOW[index_or_message]
        language = normalize_language(str(language_or_messages))
        return prompts.get(language, prompts["en"])

    user_message = str(index_or_message)
    db_messages = language_or_messages if isinstance(language_or_messages, list) else []
    chat_history = build_chat_history(db_messages)

    try:
        from app.llm.interviewer import get_next_question

        reply = get_next_question(user_message, chat_history)
        if reply and reply.strip():
            return reply.strip()
    except Exception:
        pass

    patient_turns = sum(1 for msg in db_messages if msg.get("sender_role") == "patient")
    fallback_index = min(max(patient_turns - 1, 0), len(QUESTION_FLOW) - 1)
    return get_question(fallback_index, "en")


def infer_urgency(summary_text: str) -> str:
    lowered = (summary_text or "").lower()
    for keyword in RED_FLAG_PATTERNS["emergency"]:
        if keyword in lowered:
            return "emergency"
    for keyword in RED_FLAG_PATTERNS["high"]:
        if keyword in lowered:
            return "high"

    try:
        from app.llm.guardrails import check_for_red_flags

        is_emergency, _ = check_for_red_flags(summary_text)
        if is_emergency:
            return "emergency"
    except Exception:
        pass

    return "routine"


def explain_urgency(summary_text: str, urgency: str | None = None, medical_brief: dict[str, Any] | None = None) -> str:
    resolved_urgency = (urgency or infer_urgency(summary_text)).lower()
    lowered = (summary_text or "").lower()
    trigger = next(
        (
            keyword
            for label in ("emergency", "high")
            for keyword in RED_FLAG_PATTERNS[label]
            if keyword in lowered
        ),
        None,
    )

    if resolved_urgency == "emergency":
        if trigger:
            return f"Marked emergency because the symptoms include '{trigger}', which needs immediate doctor attention."
        if medical_brief and medical_brief.get("red_flags_detected"):
            return "Marked emergency because the clinical summary suggests warning signs that need urgent review."
        return "Marked emergency because the symptoms suggest an urgent medical risk."

    if resolved_urgency == "high":
        if trigger:
            return f"Marked high priority because the case mentions '{trigger}', which needs quick review."
        return "Marked high priority because the symptoms may worsen without timely care."

    return "Kept in the routine queue because no emergency warning signs were detected."


def infer_specialty(summary_or_brief: str | dict[str, Any]) -> str:
    if isinstance(summary_or_brief, dict):
        try:
            from app.llm.classifier import classify_specialty

            classification = classify_specialty(summary_or_brief)
            recommendation = str(classification.get("recommended_specialty", "")).lower()
            if "cardio" in recommendation:
                return "cardio"
            if "pulmonol" in recommendation:
                return "pulmonology"
            if "derm" in recommendation:
                return "dermatology"
            if "gastro" in recommendation:
                return "gastroenterology"
            if "neuro" in recommendation:
                return "neurology"
        except Exception:
            summary_text = json.dumps(summary_or_brief)
        else:
            return "general_medicine"
    else:
        summary_text = summary_or_brief

    lowered = (summary_text or "").lower()
    for specialty, keywords in SPECIALTY_HINTS.items():
        if any(keyword in lowered for keyword in keywords):
            return specialty
    return "general_medicine"


def _fallback_medical_brief(profile: dict[str, Any], interview_answers: dict[str, str]) -> dict[str, Any]:
    associated = interview_answers.get("associated_symptoms", "Not captured")
    return {
        "chief_complaint": interview_answers.get("chief_complaint", "Not captured"),
        "duration": interview_answers.get("duration", "Not captured"),
        "severity": interview_answers.get("severity", "Not captured"),
        "associated_symptoms": [associated] if associated and associated != "Not captured" else [],
        "patient_narrative": interview_answers.get("chief_complaint", "Not captured"),
        "red_flags_detected": infer_urgency(" ".join(interview_answers.values())) == "emergency",
        "preferred_language": get_language_name(profile.get("preferred_language")),
        "history": interview_answers.get("history", "Not captured"),
    }


def generate_case_summary(profile: dict[str, Any], interview_source: dict[str, str] | list[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    medical_brief: dict[str, Any]

    if isinstance(interview_source, list):
        chat_history = build_chat_history(interview_source)
        try:
            from app.llm.summarizer import generate_medical_brief

            medical_brief = generate_medical_brief(chat_history)
        except Exception:
            patient_text = " ".join(
                msg["content"] for msg in chat_history if msg["role"] == "user"
            )
            fallback_answers = {
                "chief_complaint": patient_text or "Not captured",
                "duration": "Not captured",
                "severity": "Not captured",
                "associated_symptoms": "Not captured",
                "history": "Not captured",
            }
            medical_brief = _fallback_medical_brief(profile, fallback_answers)
    else:
        medical_brief = _fallback_medical_brief(profile, interview_source)

    assoc_symptoms = medical_brief.get("associated_symptoms", [])
    if isinstance(assoc_symptoms, list):
        symptoms_str = ", ".join(assoc_symptoms) if assoc_symptoms else "Not captured"
    else:
        symptoms_str = str(assoc_symptoms or "Not captured")

    summary_lines = [
        f"Patient: {profile.get('name', 'Unknown')}, {profile.get('age', 'Unknown')} years, {profile.get('gender', 'Unknown')}.",
        f"Preferred language: {get_language_name(profile.get('preferred_language'))}.",
        f"Chief complaint: {medical_brief.get('chief_complaint', 'Not captured')}.",
        f"Duration: {medical_brief.get('duration', 'Not captured')}.",
        f"Severity: {medical_brief.get('severity', 'Not captured')}.",
        f"Associated symptoms: {symptoms_str}.",
        f"Narrative: {medical_brief.get('patient_narrative', 'Not captured')}.",
        f"History/medicines/allergies: {medical_brief.get('history', 'Not captured')}.",
    ]

    urgency = "emergency" if medical_brief.get("red_flags_detected") else infer_urgency(" ".join(summary_lines))
    specialty = infer_specialty(medical_brief)
    summary_lines.append(f"Urgency: {urgency}.")
    summary_lines.append(f"Urgency reason: {explain_urgency(' '.join(summary_lines), urgency=urgency, medical_brief=medical_brief)}")
    summary_lines.append(f"Suggested specialty: {specialty}.")
    return "\n".join(summary_lines), medical_brief


def translate_for_patient(text: str, language: str) -> str:
    language = normalize_language(language)
    if language == "en":
        return text

    try:
        from app.llm.translator import translate_text

        translated = translate_text(text, language)
        if translated and translated.strip():
            return translated.strip()
    except Exception:
        pass

    return f"[{SUPPORTED_LANGUAGES[language]}] {text}"


def generate_real_audio(text: str, language: str) -> str:
    audio_dir = Path(__file__).resolve().parent / "static" / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    safe_language = normalize_language(language)
    audio_hash = hashlib.sha256(f"{safe_language}:{text}".encode("utf-8")).hexdigest()[:16]
    filename = f"{audio_hash}.mp3"
    output_path = audio_dir / filename

    try:
        from app.sensory.synthesizer import generate_audio_reply

        generated_path = generate_audio_reply(text, safe_language, str(output_path.resolve()))
    except Exception:
        return ""

    if not generated_path:
        return ""
    return f"/static/audio/{filename}"
