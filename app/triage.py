from __future__ import annotations

import json
import re
from typing import Any


SUPPORTED_LANGUAGES = {
    "en": "English",
    "hi": "Hindi",
    "te": "Telugu",
    "ta": "Tamil",
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


def get_question(index: int, language: str) -> str:
    _, prompts = QUESTION_FLOW[index]
    return prompts.get(language, prompts["en"])


def infer_urgency(summary_text: str) -> str:
    lowered = summary_text.lower()
    for keyword in RED_FLAG_PATTERNS["emergency"]:
        if keyword in lowered:
            return "emergency"
    for keyword in RED_FLAG_PATTERNS["high"]:
        if keyword in lowered:
            return "high"
    return "routine"


def infer_specialty(summary_text: str) -> str:
    lowered = summary_text.lower()
    for specialty, keywords in SPECIALTY_HINTS.items():
        if any(keyword in lowered for keyword in keywords):
            return specialty
    return "general_medicine"


def generate_case_summary(profile: dict[str, Any], interview_answers: dict[str, str]) -> str:
    lines = [
        f"Patient: {profile['name']}, {profile['age']} years, {profile['gender']}.",
        f"Preferred language: {SUPPORTED_LANGUAGES.get(profile['preferred_language'], 'English')}.",
        f"Chief complaint: {interview_answers.get('chief_complaint', 'Not captured')}.",
        f"Duration: {interview_answers.get('duration', 'Not captured')}.",
        f"Severity: {interview_answers.get('severity', 'Not captured')}.",
        f"Associated symptoms: {interview_answers.get('associated_symptoms', 'Not captured')}.",
        f"History/medicines/allergies: {interview_answers.get('history', 'Not captured')}.",
    ]
    urgency = infer_urgency(" ".join(lines))
    specialty = infer_specialty(" ".join(lines))
    lines.append(f"Urgency: {urgency}.")
    lines.append(f"Suggested specialty: {specialty}.")
    return "\n".join(lines)


def translate_for_patient(text: str, language: str) -> str:
    language = normalize_language(language)
    if language == "en":
        return text
    return f"[{SUPPORTED_LANGUAGES[language]}] {text}"


def build_audio_placeholder(text: str, language: str) -> str:
    return f"tts://{normalize_language(language)}/{abs(hash(text))}"
