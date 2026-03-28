from __future__ import annotations

import mimetypes
import os
import re
import sys
import uuid
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from twilio.rest import Client
from twilio.twiml.messaging_response import MessagingResponse

from app.auth import create_session_token, decode_session_token, hash_password, verify_password
from app.config import settings
from app.db import init_db
from app.repository import Repository
from app.triage import (
    assess_transcript_quality,
    build_language_switch_prompt,
    detect_language,
    explain_urgency,
    friendly_language_list,
    generate_case_summary,
    generate_real_audio,
    get_language_name,
    get_question,
    infer_specialty,
    infer_urgency,
    load_context,
    parse_language_switch_choice,
    parse_language_choice,
    should_prompt_language_switch,
    triage_case,
    translate_for_patient,
)

app = FastAPI(title=settings.app_name)
repository = Repository(settings.database_path)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DOWNLOADS_DIR = STATIC_DIR / "downloads"
AUDIO_DIR = STATIC_DIR / "audio"
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "https://YOUR-NGROK-URL").rstrip("/")
TEXT_ONLY_WHATSAPP_REPLIES = os.getenv("TEXT_ONLY_WHATSAPP_REPLIES", "1") == "1"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def debug_log(event: str, **kwargs: Any) -> None:
    details = " ".join(f"{key}={value!r}" for key, value in kwargs.items())
    print(f"[main.{event}] {details}", file=sys.stderr, flush=True)


def ensure_media_directories() -> None:
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)


def guess_media_suffix(content_type: str) -> str:
    normalized = (content_type or "").split(";")[0].strip().lower()
    overrides = {
        "audio/ogg": ".ogg",
        "audio/opus": ".opus",
        "audio/webm": ".webm",
        "image/jpeg": ".jpg",
    }
    return overrides.get(normalized) or mimetypes.guess_extension(normalized) or ".bin"


async def download_twilio_media(media_url: str, suffix: str, account_sid_override: str | None = None) -> str:
    ensure_media_directories()
    account_sid = account_sid_override or os.getenv("TWILIO_ACCOUNT_SID")
    auth_token = os.getenv("TWILIO_AUTH_TOKEN")
    if not account_sid or not auth_token:
        raise RuntimeError("Twilio credentials are not configured for media download.")

    sanitized_suffix = suffix if suffix.startswith(".") else f".{suffix}"
    output_path = DOWNLOADS_DIR / f"{uuid.uuid4().hex}{sanitized_suffix}"
    debug_log("download_twilio_media.start", media_url=media_url, account_sid=account_sid, output_path=str(output_path))

    async with httpx.AsyncClient(auth=(account_sid, auth_token), follow_redirects=True, timeout=30.0) as client:
        response = await client.get(media_url)
        response.raise_for_status()
        output_path.write_bytes(response.content)

    debug_log("download_twilio_media.done", saved_to=str(output_path.resolve()), bytes_written=output_path.stat().st_size)
    return str(output_path.resolve())


def build_public_media_url(relative_url: str | None) -> str | None:
    if not relative_url:
        return None
    if PUBLIC_BASE_URL == "https://YOUR-NGROK-URL":
        debug_log("build_public_media_url.skipped", reason="PUBLIC_BASE_URL not configured", relative_url=relative_url)
        return None
    return f"{PUBLIC_BASE_URL}{relative_url}"


def send_whatsapp_message(to_phone: str, body: str, media_url: str | None = None) -> str:
    account_sid = os.getenv("TWILIO_ACCOUNT_SID")
    auth_token = os.getenv("TWILIO_AUTH_TOKEN")
    from_number = os.getenv("TWILIO_WHATSAPP_FROM")
    if not account_sid or not auth_token or not from_number:
        raise RuntimeError("Twilio credentials are not fully configured for outbound WhatsApp messaging.")

    client = Client(account_sid, auth_token)
    text_payload: dict[str, Any] = {
        "from_": from_number,
        "to": f"whatsapp:{to_phone}",
        "body": body,
    }
    debug_log("send_whatsapp_message.start", to=text_payload["to"], media_url=media_url)
    text_message = client.messages.create(**text_payload)
    media_message_sid = None
    if media_url and not TEXT_ONLY_WHATSAPP_REPLIES:
        media_payload: dict[str, Any] = {
            "from_": from_number,
            "to": f"whatsapp:{to_phone}",
            "body": "Voice reply",
            "media_url": [media_url],
        }
        media_message = client.messages.create(**media_payload)
        media_message_sid = getattr(media_message, "sid", None)
    debug_log(
        "send_whatsapp_message.done",
        sid=text_message.sid,
        media_sid=media_message_sid,
        status=getattr(text_message, "status", None),
    )
    return str(text_message.sid)


def profile_menu(profiles: list[dict[str, Any]]) -> str:
    lines = ["Select patient profile:"]
    for index, profile in enumerate(profiles, start=1):
        lines.append(f"{index}. {profile['name']} ({profile['age']}/{profile['gender']})")
    lines.append(f"{len(profiles) + 1}. Add new profile")
    return "\n".join(lines)


def safe_int(value: str) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def set_auth_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        settings.auth_cookie_name,
        token,
        httponly=True,
        samesite="lax",
        secure=settings.auth_cookie_secure,
        max_age=60 * 60 * 24 * 7,
    )


def validate_registration_input(full_name: str, email: str, password: str, role: str, specialty: str) -> str | None:
    if len(full_name.strip()) < 2:
        return "Full name must be at least 2 characters."
    if not EMAIL_RE.match(email.strip()):
        return "Please enter a valid email address."
    if len(password) < 8:
        return "Password must be at least 8 characters."
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        return "Password must include at least one letter and one number."
    if role == "specialist" and len(specialty.strip()) < 2:
        return "Specialists must provide a specialty."
    return None


def get_role_dashboard_path(role: str) -> str:
    return "/dashboard/admin" if role == "admin" else f"/dashboard/{role}"


def current_user(request: Request) -> dict[str, Any] | None:
    token = request.cookies.get(settings.auth_cookie_name)
    if not token:
        return None
    payload = decode_session_token(token)
    if not payload:
        return None
    return repository.get_user_by_id(int(payload["sub"]))


def require_user(request: Request, allowed_roles: set[str] | None = None) -> dict[str, Any]:
    user = current_user(request)
    if not user or not user.get("is_active"):
        raise HTTPException(status_code=401, detail="Authentication required")
    if allowed_roles and user["role"] not in allowed_roles:
        raise HTTPException(status_code=403, detail="Forbidden")
    return user


def guest_redirect(request: Request) -> dict[str, Any] | RedirectResponse:
    user = current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    return user


def annotate_case(case: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(case)
    enriched["urgency_explanation"] = explain_urgency(enriched.get("summary_english", ""), urgency=enriched.get("urgency"))
    return enriched


def build_dashboard_stacks(cases: list[dict[str, Any]], role: str) -> list[dict[str, str | int]]:
    total_cases = len(cases)
    high_cases = sum(1 for case in cases if case.get("urgency") == "high")
    medium_cases = sum(1 for case in cases if case.get("urgency") == "medium")
    low_cases = sum(1 for case in cases if case.get("urgency") == "low")
    top_focus = cases[0]["patient_name"] if cases else "No active case"

    return [
        {
            "label": "Active Queue",
            "value": total_cases,
            "tone": "neutral",
            "caption": "Cases currently assigned to you and waiting for review.",
        },
        {
            "label": "Critical First",
            "value": high_cases,
            "tone": "high",
            "caption": "Emergency-priority cases that should be opened before everything else.",
        },
        {
            "label": "Urgent Review",
            "value": medium_cases,
            "tone": "medium",
            "caption": "Faster follow-up needed, but not immediate emergency escalation.",
        },
        {
            "label": "Queue Focus",
            "value": top_focus,
            "tone": "low" if low_cases else "neutral",
            "caption": (
                "Highest-priority case at the top of your stack."
                if cases
                else f"No {role} cases are waiting right now."
            ),
        },
    ]


def build_admin_stacks(metrics: dict[str, Any]) -> dict[str, list[dict[str, str | int]]]:
    patient_side = [
        {
            "label": "Registered Families",
            "value": metrics["phones"],
            "tone": "neutral",
            "caption": "Unique phone numbers connected to MedTalk intake.",
        },
        {
            "label": "Patient Profiles",
            "value": metrics["profiles"],
            "tone": "low",
            "caption": "Profiles created for family members under shared numbers.",
        },
        {
            "label": "Active Intake Sessions",
            "value": metrics["active_intakes"],
            "tone": "medium" if metrics["active_intakes"] else "neutral",
            "caption": "Patients currently in onboarding or AI interview flow.",
        },
        {
            "label": "Waiting For Doctor",
            "value": metrics["waiting_patients"],
            "tone": "high" if metrics["waiting_patients"] else "neutral",
            "caption": "Cases already summarized and waiting in doctor-side workflow.",
        },
    ]
    doctor_side = [
        {
            "label": "Active Doctors",
            "value": metrics["total_doctors"],
            "tone": "neutral",
            "caption": "All active GP and specialist accounts in the system.",
        },
        {
            "label": "General Physicians",
            "value": metrics["gps"],
            "tone": "low",
            "caption": "Doctors currently available for first-line review.",
        },
        {
            "label": "Specialists",
            "value": metrics["specialists"],
            "tone": "medium",
            "caption": "Domain specialists available for referred cases.",
        },
        {
            "label": "Completed Consultations",
            "value": metrics["completed_cases"],
            "tone": "neutral",
            "caption": "Cases fully responded to by the doctor workflow.",
        },
    ]
    care_ops = [
        {
            "label": "High Urgency Open",
            "value": metrics["open_high"],
            "tone": "high" if metrics["open_high"] else "neutral",
            "caption": "Critical open cases currently requiring fast attention.",
        },
        {
            "label": "Medium Urgency Open",
            "value": metrics["open_medium"],
            "tone": "medium" if metrics["open_medium"] else "neutral",
            "caption": "Urgent but non-emergency cases still pending review.",
        },
    ]
    return {
        "patient_side": patient_side,
        "doctor_side": doctor_side,
        "care_ops": care_ops,
    }


def transcribe_media_audio_details(audio_file_path: str) -> dict[str, str]:
    from app.sensory.transcriber import transcribe_audio_details

    return transcribe_audio_details(audio_file_path)


def analyze_media_image(image_path: str) -> str:
    from app.sensory.vision import analyze_symptom_image

    return analyze_symptom_image(image_path)


def get_patient_reply_language(phone: str) -> str:
    conversation = repository.get_or_create_conversation(phone, "en")
    profile_id = conversation.get("patient_profile_id")
    if profile_id:
        profile = repository.get_profile(int(profile_id))
        if profile and profile.get("preferred_language"):
            return profile["preferred_language"]
    return conversation.get("language", "en")


def build_emergency_patient_alert(language: str) -> str:
    return translate_for_patient(
        "Warning. This may be a medical emergency. Do not wait for a reply. Visit the nearest hospital immediately or call 108.",
        language,
    )


def build_repeat_voice_prompt(language: str) -> str:
    return translate_for_patient(
        "I could not clearly understand the voice note. Please say it again slowly or type the symptom in text.",
        language,
    )


def build_structured_doctor_reply(possible_cause: str, care_plan: str) -> str:
    cause = possible_cause.strip()
    plan = care_plan.strip()
    return (
        "Possible Cause / Reason\n"
        f"{cause}\n\n"
        "Care Plan\n"
        f"{plan}\n\n"
        "If symptoms worsen, new symptoms appear, or improvement does not happen as expected, please seek further medical care."
    )


def append_temp_chat_turn(context: dict[str, Any], role: str, content: str) -> None:
    cleaned = (content or "").strip()
    if not cleaned:
        return
    turns = list(context.get("chat_turns", []))
    turns.append({"role": role, "content": cleaned})
    context["chat_turns"] = turns[-12:]


def get_doctor_display_specialty(doctor: dict[str, Any]) -> str:
    role = (doctor.get("role") or "").strip().lower()
    specialty = (doctor.get("specialty") or "").strip()
    if role == "gp":
        return "General Physician"
    return specialty or "Specialist"


def build_patient_confidence_notification(doctor: dict[str, Any], patient_language: str) -> str:
    lines = [
        "Your case has been accepted and is now being reviewed by a doctor.",
        "",
        f"Doctor: {doctor.get('name', 'Assigned Doctor')}",
        f"Specialization: {get_doctor_display_specialty(doctor)}",
    ]
    if doctor.get("organization"):
        lines.append(f"Hospital / Organization: {doctor['organization']}")
    if doctor.get("location"):
        lines.append(f"Location: {doctor['location']}")
    lines.extend(
        [
            "",
            "You are now connected to a healthcare professional who is reviewing the details you shared.",
            "The doctor will respond here after review.",
            "If your symptoms suddenly become much worse or you feel unsafe, please seek urgent medical care immediately.",
        ]
    )
    return translate_for_patient("\n".join(lines), patient_language)


def maybe_notify_patient_doctor_ownership(case: dict[str, Any], user: dict[str, Any]) -> str | None:
    if case.get("status") in {"responded", "closed", "superseded"}:
        return None

    notification_role = "gp_review" if user["role"] == "gp" else "specialist_review"
    if repository.has_ownership_notification(case["id"], int(user["doctor_id"]), notification_role):
        return None

    doctor = repository.get_doctor(int(user["doctor_id"]))
    if not doctor:
        return None

    patient_message = build_patient_confidence_notification(doctor, case["patient_language"])
    audio_url = None
    public_audio_url = None
    if not TEXT_ONLY_WHATSAPP_REPLIES:
        try:
            audio_url = generate_real_audio(patient_message, case["patient_language"])
            public_audio_url = build_public_media_url(audio_url)
        except Exception as exc:
            debug_log("ownership_notification.tts_failed", case_id=case["id"], doctor_id=user["doctor_id"], error=str(exc))

    delivery_status = "sent"
    delivery_error = None
    try:
        send_whatsapp_message(case["phone_number"], patient_message, public_audio_url)
    except Exception as exc:
        delivery_status = "failed"
        delivery_error = str(exc)
        debug_log(
            "ownership_notification.send_failed",
            case_id=case["id"],
            doctor_id=user["doctor_id"],
            phone=case["phone_number"],
            error=delivery_error,
        )
        repository.add_message(
            case["conversation_id"],
            "system",
            "text",
            "Patient confidence update could not be delivered on WhatsApp, but the doctor has taken ownership of the case.",
            delivery_error,
            None,
        )
    else:
        repository.add_message(
            case["conversation_id"],
            "system",
            "text",
            "Patient confidence update sent after doctor accepted the case for review.",
            patient_message,
            audio_url,
        )
        debug_log("ownership_notification.sent", case_id=case["id"], doctor_id=user["doctor_id"])

    repository.record_ownership_notification(
        case["id"],
        int(user["doctor_id"]),
        notification_role,
        delivery_status,
        delivery_error,
    )
    return delivery_status


def handle_patient_message(
    phone: str,
    incoming_text: str,
    message_type: str,
    media_url: str | None,
    detected_language_override: str | None = None,
    allow_language_switch_prompt: bool = True,
) -> str:
    repository.ensure_phone(phone)
    language = detected_language_override or detect_language(incoming_text)
    conversation = repository.get_or_create_conversation(phone, language)
    context = load_context(conversation["context_json"])
    profiles = repository.get_profiles(phone)

    debug_log(
        "handle_patient_message.start",
        phone=phone,
        stage=conversation["stage"],
        status=conversation["status"],
        message_type=message_type,
        incoming_text=incoming_text,
    )

    stage = conversation["stage"]
    text = (incoming_text or "").strip()

    if text.lower() in {"reset", "start over"}:
        context = load_context(None)
        repository.update_conversation(
            conversation["id"],
            stage="start",
            status="active",
            language=language,
            context=context,
        )
        stage = "start"
        conversation["patient_profile_id"] = None
        debug_log("handle_patient_message.reset", conversation_id=conversation["id"])

    if stage == "start":
        if not profiles:
            repository.update_conversation(conversation["id"], stage="profile_name", status="active", language=language, context=context)
            reply = "Welcome to MedTalk. Let's create the first patient profile.\nWhat is the patient's name?"
            return reply
        repository.update_conversation(conversation["id"], stage="profile_select", status="active", language=language, context=context)
        reply = profile_menu(profiles)
        return reply

    if stage == "profile_select":
        choice = safe_int(text)
        if not choice:
            reply = "Please reply with the profile number."
        elif 1 <= choice <= len(profiles):
            selected = profiles[choice - 1]
            repository.update_conversation(
                conversation["id"],
                patient_profile_id=selected["id"],
                stage="interview",
                status="active",
                language=selected["preferred_language"],
                context={**context, "interview_answers": {}, "interview_index": 0, "chat_turns": [], "temp_symptom": None, "temp_detected_language": None},
            )
            first_question = translate_for_patient("Please describe the main problem in one or two sentences.", selected["preferred_language"])
            reply = f"Patient selected: {selected['name']}.\n{first_question}"
        elif choice == len(profiles) + 1:
            repository.update_conversation(conversation["id"], stage="profile_name", context=context)
            reply = "What is the new patient's name?"
        else:
            reply = "That profile number is not valid."
        return reply

    if stage == "profile_name":
        if len(text) < 2:
            reply = "Please send a valid patient name."
        else:
            context["draft_profile"] = {"name": text}
            repository.update_conversation(conversation["id"], stage="profile_age", context=context)
            reply = "What is the patient's age?"
        return reply

    if stage == "profile_age":
        age = safe_int(text)
        if age is None or age <= 0 or age > 120:
            reply = "Please enter age as a number."
        else:
            context["draft_profile"]["age"] = age
            repository.update_conversation(conversation["id"], stage="profile_gender", context=context)
            reply = "What is the patient's gender?"
        return reply

    if stage == "profile_gender":
        if not text:
            reply = "Please enter the patient's gender."
        else:
            context["draft_profile"]["gender"] = text
            repository.update_conversation(conversation["id"], stage="profile_language", context=context)
            reply = f"Choose preferred language:\n{friendly_language_list()}"
        return reply

    if stage == "profile_language":
        selected_language = parse_language_choice(text)
        if not selected_language:
            reply = f"Please choose a valid language.\n{friendly_language_list()}"
            return reply
        draft = context["draft_profile"]
        profile_id = repository.create_profile(phone, draft["name"], int(draft["age"]), draft["gender"], selected_language)
        repository.update_conversation(
            conversation["id"],
            patient_profile_id=profile_id,
            stage="interview",
            status="active",
            language=selected_language,
            context={**context, "interview_answers": {}, "interview_index": 0, "chat_turns": [], "temp_symptom": None, "temp_detected_language": None},
        )
        first_question = translate_for_patient("Please describe the main problem in one or two sentences.", selected_language)
        reply = f"Profile created for {draft['name']}.\n{first_question}"
        return reply

    if stage == "confirm_language_switch":
        current_profile = repository.get_profile(conversation["patient_profile_id"])
        if not current_profile:
            reply = "Profile not found. Send RESET to start again."
            return reply

        temp_symptom = context.get("temp_symptom") or text
        selected_language = parse_language_switch_choice(text)
        if not selected_language:
            reply = build_language_switch_prompt(current_profile["preferred_language"])
            return reply

        transcript_quality = assess_transcript_quality(temp_symptom, detected_language=selected_language)
        if bool(transcript_quality["is_noisy"]):
            context["temp_symptom"] = None
            context["temp_detected_language"] = None
            repository.update_conversation(conversation["id"], stage="interview", language=selected_language, context=context)
            reply = build_repeat_voice_prompt(selected_language)
            return reply

        repository.update_profile(current_profile["id"], selected_language)
        context["temp_symptom"] = None
        context["temp_detected_language"] = None
        repository.update_conversation(conversation["id"], stage="interview", language=selected_language, context=context)
        return handle_patient_message(
            phone,
            temp_symptom,
            "text",
            media_url,
            detected_language_override=selected_language,
            allow_language_switch_prompt=False,
        )

    if stage == "interview":
        current_profile = repository.get_profile(conversation["patient_profile_id"])
        if not current_profile:
            reply = "Profile not found. Send RESET to start again."
            return reply

        preferred_language = current_profile["preferred_language"]
        if allow_language_switch_prompt and should_prompt_language_switch(preferred_language, language, text) and not context.get("temp_symptom"):
            context["temp_symptom"] = incoming_text
            context["temp_detected_language"] = language
            repository.update_conversation(conversation["id"], stage="confirm_language_switch", context=context)
            reply = build_language_switch_prompt(preferred_language)
            return reply

        english_text = incoming_text if language == "en" else f"[English summary pending] {incoming_text}"
        append_temp_chat_turn(context, "user", english_text)
        if infer_urgency(english_text) == "high":
            gp = repository.get_available_gp()
            triage_result = triage_case(english_text)
            urgency_reason = triage_result["reason"]
            emergency_summary = "\n".join(
                [
                    f"Patient: {current_profile['name']}, {current_profile['age']} years, {current_profile['gender']}.",
                    f"Preferred language: {get_language_name(current_profile['preferred_language'])}.",
                    f"Chief complaint: {english_text}.",
                    f"Urgency: {triage_result['urgency_level'].lower()}.",
                    f"Urgency reason: {urgency_reason}",
                    "Suggested specialty: emergency_medicine.",
                ]
            )
            case_id = repository.create_case(
                conversation["id"],
                current_profile["id"],
                emergency_summary,
                triage_result["urgency_level"].lower(),
                "emergency_medicine",
                gp["id"] if gp else None,
            )
            repository.update_conversation(
                conversation["id"],
                stage="queued_for_gp",
                status="queued_for_gp",
                language=preferred_language,
                context={**context, "chat_turns": []},
                active_case_id=case_id,
            )
            reply = build_emergency_patient_alert(preferred_language)
            return reply

        chat_turns = context.get("chat_turns", [])
        reply_english = get_question(english_text, chat_turns)
        if not reply_english or not reply_english.strip():
            reply_english = "I am having trouble processing that. Can you please repeat?"

        if "[URGENT_RED_FLAG]" in reply_english:
            gp = repository.get_available_gp()
            triage_result = triage_case(english_text)
            urgency_reason = triage_result["reason"]
            emergency_summary = "\n".join(
                [
                    f"Patient: {current_profile['name']}, {current_profile['age']} years, {current_profile['gender']}.",
                    f"Preferred language: {get_language_name(current_profile['preferred_language'])}.",
                    f"Chief complaint: {english_text}.",
                    f"Urgency: {triage_result['urgency_level'].lower()}.",
                    f"Urgency reason: {urgency_reason}",
                    "Suggested specialty: emergency_medicine.",
                ]
            )
            case_id = repository.create_case(
                conversation["id"],
                current_profile["id"],
                emergency_summary,
                triage_result["urgency_level"].lower(),
                "emergency_medicine",
                gp["id"] if gp else None,
            )
            repository.update_conversation(
                conversation["id"],
                stage="queued_for_gp",
                status="queued_for_gp",
                language=preferred_language,
                context={**context, "chat_turns": []},
                active_case_id=case_id,
            )
            reply = build_emergency_patient_alert(preferred_language)
            return reply

        if "[INTERVIEW_COMPLETE]" in reply_english:
            summary_text, medical_brief = generate_case_summary(current_profile, chat_turns)
            urgency = "high" if medical_brief.get("red_flags_detected") else infer_urgency(summary_text)
            specialty = infer_specialty(medical_brief)
            gp = repository.get_available_gp()
            case_id = repository.create_case(
                conversation["id"],
                current_profile["id"],
                summary_text,
                urgency,
                specialty,
                gp["id"] if gp else None,
            )
            context["last_summary"] = summary_text
            repository.update_conversation(
                conversation["id"],
                stage="queued_for_gp",
                status="queued_for_gp",
                language=preferred_language,
                context={**context, "chat_turns": []},
                active_case_id=case_id,
            )
            reply_english = "Thank you. Your case has been prepared and sent to the general physician queue."
        else:
            reply_english = reply_english.replace("[INTERVIEW_COMPLETE]", "").strip()
            append_temp_chat_turn(context, "assistant", reply_english)
            repository.update_conversation(conversation["id"], context=context)

        reply = translate_for_patient(reply_english.replace("[INTERVIEW_COMPLETE]", "").strip(), preferred_language)
        return reply

    if stage in {"queued_for_gp", "doctor_review", "referred"}:
        reply = translate_for_patient("Your case is under review. A doctor reply will reach you here.", conversation["language"])
        return reply

    if stage == "doctor_replied":
        reply = translate_for_patient("This case is closed. Send RESET to start a new consultation.", conversation["language"])
        return reply

    reply = "Unsupported state. Send RESET to restart."
    return reply


@app.on_event("startup")
def startup() -> None:
    ensure_media_directories()
    init_db(settings.database_path)
    repository.seed_doctors()
    if settings.admin_email and settings.admin_password:
        password_hash, password_salt = hash_password(settings.admin_password)
        repository.ensure_admin_user(
            full_name=settings.admin_full_name,
            email=settings.admin_email,
            password_hash=password_hash,
            password_salt=password_salt,
        )
    debug_log(
        "startup",
        database_path=str(settings.database_path),
        public_base_url=PUBLIC_BASE_URL,
        text_only_whatsapp_replies=TEXT_ONLY_WHATSAPP_REPLIES,
    )


@app.get("/")
def root(request: Request) -> RedirectResponse:
    user = current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    return RedirectResponse(url=get_role_dashboard_path(user["role"]), status_code=303)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "MedTalk running"}


@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("register.html", {"request": request, "current_user": current_user(request), "error": None})


@app.post("/register", response_class=HTMLResponse)
def register(
    request: Request,
    full_name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    role: str = Form(...),
    specialty: str = Form(default="general_medicine"),
) -> Response:
    normalized_role = role.strip().lower()
    normalized_specialty = specialty.strip().lower() or "general_medicine"
    if normalized_role not in {"gp", "specialist"}:
        return templates.TemplateResponse("register.html", {"request": request, "current_user": current_user(request), "error": "Invalid role."}, status_code=400)

    validation_error = validate_registration_input(full_name, email, password, normalized_role, normalized_specialty)
    if validation_error:
        return templates.TemplateResponse("register.html", {"request": request, "current_user": current_user(request), "error": validation_error}, status_code=400)

    if normalized_role == "gp":
        normalized_specialty = "general_medicine"
    if repository.get_user_by_email(email):
        return templates.TemplateResponse("register.html", {"request": request, "current_user": current_user(request), "error": "Email already registered."}, status_code=400)

    password_hash, password_salt = hash_password(password)
    user_id = repository.create_user(
        full_name=full_name.strip(),
        email=email.strip(),
        password_hash=password_hash,
        password_salt=password_salt,
        role=normalized_role,
        specialty=normalized_specialty,
    )
    user = repository.get_user_by_id(user_id)
    token = create_session_token(user)
    response = RedirectResponse(url=get_role_dashboard_path(user["role"]), status_code=303)
    set_auth_cookie(response, token)
    return response


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("login.html", {"request": request, "current_user": current_user(request), "error": None})


@app.post("/login", response_class=HTMLResponse)
def login(request: Request, email: str = Form(...), password: str = Form(...)) -> Response:
    user = repository.get_user_by_email(email.strip())
    if not user or not user.get("is_active") or not verify_password(password, user["password_hash"], user["password_salt"]):
        return templates.TemplateResponse("login.html", {"request": request, "current_user": current_user(request), "error": "Invalid email or password."}, status_code=401)

    token = create_session_token(user)
    response = RedirectResponse(url=get_role_dashboard_path(user["role"]), status_code=303)
    set_auth_cookie(response, token)
    return response


@app.get("/profile", response_class=HTMLResponse)
def profile_page(request: Request) -> HTMLResponse:
    auth = guest_redirect(request)
    if isinstance(auth, RedirectResponse):
        return auth
    doctor = repository.get_doctor(int(auth["doctor_id"]))
    return templates.TemplateResponse(
        "profile.html",
        {
            "request": request,
            "current_user": auth,
            "doctor": doctor,
        },
    )


@app.post("/logout")
def logout() -> RedirectResponse:
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(settings.auth_cookie_name)
    return response


@app.post("/webhook")
async def webhook(
    request: Request,
    AccountSid: str = Form(default=""),
    From: str = Form(...),
    Body: str = Form(default=""),
    NumMedia: int = Form(default=0),
    MediaContentType0: str | None = Form(default=None),
    MediaUrl0: str | None = Form(default=None),
) -> Response:
    form = dict(await request.form())
    phone = From.replace("whatsapp:", "")
    message_type = "text"
    incoming_text = Body.strip()
    media_path = None
    media_type = (MediaContentType0 or "").lower()
    early_reply_text: str | None = None

    if NumMedia > 0 and MediaContentType0 and MediaUrl0:
        guessed_suffix = guess_media_suffix(MediaContentType0)
        configured_sid = os.getenv("TWILIO_ACCOUNT_SID", "")
        if configured_sid and AccountSid and configured_sid != AccountSid:
            debug_log("webhook.twilio_sid_mismatch", configured_sid=configured_sid, request_sid=AccountSid)
        try:
            media_path = await download_twilio_media(MediaUrl0, guessed_suffix, account_sid_override=AccountSid or None)
            if "audio" in media_type:
                message_type = "audio"
                transcription_details = transcribe_media_audio_details(media_path)
                transcription = transcription_details.get("text", "")
                transcription_language = transcription_details.get("language", "")
                transcription_provider = transcription_details.get("provider", "")
                transcription_reason = transcription_details.get("reason", "")
                if bool(transcription_details.get("is_noisy")):
                    early_reply_text = build_repeat_voice_prompt(get_patient_reply_language(phone))
                    incoming_text = ""
                    debug_log(
                        "webhook.audio_noisy_provider",
                        media_path=media_path,
                        provider=transcription_provider,
                        transcription=transcription,
                        transcription_language=transcription_language,
                        reason=transcription_reason,
                    )
                    form["DetectedAudioLanguage"] = transcription_language
                    raise RuntimeError(transcription_reason or "Transcription marked noisy by provider.")
                transcript_quality = assess_transcript_quality(
                    transcription,
                    detected_language=transcription_language,
                )
                if bool(transcript_quality["is_noisy"]):
                    early_reply_text = build_repeat_voice_prompt(get_patient_reply_language(phone))
                    incoming_text = ""
                    debug_log(
                        "webhook.audio_noisy",
                        media_path=media_path,
                        transcription=transcription,
                        transcription_language=transcription_language,
                        reason=transcript_quality["reason"],
                    )
                    form["DetectedAudioLanguage"] = transcription_language
                    raise RuntimeError(str(transcript_quality["reason"]))
                if incoming_text and transcription:
                    incoming_text = f"{incoming_text}\n[Voice note transcript: {transcription}]"
                else:
                    incoming_text = transcription or incoming_text or "Voice note received"
                if transcription_language:
                    form["DetectedAudioLanguage"] = transcription_language
                debug_log(
                    "webhook.audio_processed",
                    media_path=media_path,
                    provider=transcription_provider,
                    transcription=transcription,
                    transcription_language=transcription_language,
                )
            elif "image" in media_type:
                message_type = "image"
                analysis = analyze_media_image(media_path)
                image_note = f"[Patient uploaded an image. Vision Analysis: {analysis or 'Image received but analysis was inconclusive.'}]"
                incoming_text = f"{incoming_text}\n{image_note}".strip() if incoming_text else image_note
                debug_log("webhook.image_processed", media_path=media_path, analysis=analysis)
            else:
                message_type = "media"
                incoming_text = incoming_text or "Patient sent unsupported media."
                debug_log("webhook.unsupported_media", media_type=MediaContentType0, media_url=MediaUrl0)
        except Exception as exc:
            debug_log("webhook.media_processing_failed", media_type=MediaContentType0, media_url=MediaUrl0, error=str(exc))
            if "audio" in media_type:
                message_type = "audio"
                if not early_reply_text:
                    reply_language = get_patient_reply_language(phone)
                    early_reply_text = translate_for_patient(
                        "I received your voice note, but I could not transcribe it. Please send it again or type the symptom in text.",
                        reply_language,
                    )
                incoming_text = ""
            elif "image" in media_type:
                message_type = "image"
                image_note = "[Patient uploaded an image, but image analysis failed.]"
                incoming_text = f"{incoming_text}\n{image_note}".strip() if incoming_text else image_note
            else:
                message_type = "media"
                incoming_text = incoming_text or "I couldn't process that media."

    debug_log("webhook.incoming", form=form, phone=phone, message_type=message_type, body=incoming_text, media_path=media_path)

    if early_reply_text:
        reply_text = early_reply_text
        debug_log("webhook.early_reply", phone=phone, reply_text=reply_text)
    else:
        reply_text = handle_patient_message(
            phone,
            incoming_text,
            message_type,
            media_path,
            detected_language_override=form.get("DetectedAudioLanguage"),
        )
    if not reply_text or not reply_text.strip():
        reply_text = "I am having trouble processing that. Please send RESET to restart."

    twiml = MessagingResponse()
    twiml.message(reply_text)
    if TEXT_ONLY_WHATSAPP_REPLIES:
        debug_log("webhook.text_only_reply", reply_text=reply_text)
    else:
        try:
            detected_language = form.get("DetectedAudioLanguage") or detect_language(incoming_text)
            conversation = repository.get_or_create_conversation(phone, detected_language)
            voice_language = conversation.get("language", "en")
            audio_url = generate_real_audio(reply_text, voice_language)
            public_audio_url = build_public_media_url(audio_url)
            if public_audio_url:
                media_message = twiml.message("Voice reply")
                media_message.media(public_audio_url)
            debug_log("webhook.tts_ready", voice_language=voice_language, audio_url=audio_url, public_audio_url=public_audio_url)
        except Exception as exc:
            debug_log("webhook.tts_failed", error=str(exc))

    xml_response = str(twiml)
    debug_log("webhook.outgoing", phone=phone, xml=xml_response)
    return Response(content=xml_response, media_type="text/xml")


@app.get("/dashboard/{role}", response_class=HTMLResponse)
def dashboard(request: Request, role: str) -> Response:
    auth = guest_redirect(request)
    if isinstance(auth, RedirectResponse):
        return auth
    if role == "admin":
        if auth["role"] != "admin":
            raise HTTPException(status_code=403, detail="Forbidden")
        metrics = repository.get_admin_metrics()
        return templates.TemplateResponse(
            "admin_dashboard.html",
            {
                "request": request,
                "current_user": auth,
                "admin_stacks": build_admin_stacks(metrics),
                "recent_cases": [annotate_case(case) for case in repository.list_admin_recent_cases()],
                "doctor_workloads": repository.list_doctor_workloads(),
            },
        )

    if role not in {"gp", "specialist"} or auth["role"] != role:
        raise HTTPException(status_code=403, detail="Forbidden")
    cases = [annotate_case(case) for case in repository.list_cases(role, int(auth["doctor_id"]))]
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "role": role,
            "cases": cases,
            "dashboard_stacks": build_dashboard_stacks(cases, role),
            "current_user": auth,
        },
    )


@app.get("/cases/{case_id}", response_class=HTMLResponse)
def case_detail(request: Request, case_id: int) -> Response:
    auth = guest_redirect(request)
    if isinstance(auth, RedirectResponse):
        return auth

    case = repository.get_case(case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    if auth["role"] == "gp" and case.get("assigned_doctor_id") != auth["doctor_id"]:
        raise HTTPException(status_code=403, detail="Forbidden")
    if auth["role"] == "specialist" and case.get("assigned_specialist_id") != auth["doctor_id"]:
        raise HTTPException(status_code=403, detail="Forbidden")

    ownership_status = maybe_notify_patient_doctor_ownership(case, auth)

    return templates.TemplateResponse(
        "case_detail.html",
        {
            "request": request,
            "case": annotate_case(case),
            "patient_history": repository.list_patient_case_history(case["patient_profile_id"], exclude_case_id=case_id),
            "specialists": repository.list_doctors("specialist"),
            "latest_response": repository.get_latest_response(case_id),
            "delivery_status": request.query_params.get("delivery"),
            "ownership_status": ownership_status,
            "current_user": auth,
        },
    )


@app.post("/cases/{case_id}/refer")
def refer_case(request: Request, case_id: int, specialist_id: int = Form(...), referral_note: str = Form(default="Please review this case.")) -> RedirectResponse:
    user = require_user(request, {"gp"})
    case = repository.get_case(case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    if case.get("assigned_doctor_id") != user["doctor_id"]:
        raise HTTPException(status_code=403, detail="Forbidden")
    repository.refer_case(case_id, specialist_id, referral_note)
    return RedirectResponse(url=f"/cases/{case_id}", status_code=303)


@app.post("/cases/{case_id}/reply")
def reply_case(
    request: Request,
    case_id: int,
    possible_cause: str = Form(...),
    care_plan: str = Form(...),
) -> RedirectResponse:
    user = require_user(request, {"gp", "specialist"})
    case = repository.get_case(case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    if user["role"] == "gp" and case.get("assigned_doctor_id") != user["doctor_id"]:
        raise HTTPException(status_code=403, detail="Forbidden")
    if user["role"] == "specialist" and case.get("assigned_specialist_id") != user["doctor_id"]:
        raise HTTPException(status_code=403, detail="Forbidden")

    if len(possible_cause.strip()) < 10 or len(care_plan.strip()) < 10:
        raise HTTPException(status_code=400, detail="Both doctor reply sections must be completed.")

    english_reply = build_structured_doctor_reply(possible_cause, care_plan)
    translated_reply = translate_for_patient(english_reply, case["patient_language"])
    audio_url = None
    public_audio_url = None
    delivery_status = "sent"
    message_sid = None

    if not TEXT_ONLY_WHATSAPP_REPLIES:
        try:
            audio_url = generate_real_audio(translated_reply, case["patient_language"])
            public_audio_url = build_public_media_url(audio_url)
        except Exception as exc:
            debug_log("reply_case.tts_failed", case_id=case_id, error=str(exc))

    completion = repository.complete_case(case_id, int(user["doctor_id"]), english_reply, translated_reply, audio_url or "")
    repository.add_message(completion["conversation_id"], "doctor", "text", translated_reply, english_reply, audio_url)

    try:
        message_sid = send_whatsapp_message(case["phone_number"], translated_reply, public_audio_url)
    except Exception as exc:
        delivery_status = "failed"
        error_text = str(exc)
        debug_log("reply_case.send_failed", case_id=case_id, error=error_text, phone=case["phone_number"])
        repository.add_message(
            completion["conversation_id"],
            "system",
            "text",
            "Doctor reply was saved, but WhatsApp delivery failed. Please share the dashboard copy manually or try again later.",
            error_text,
            None,
        )
    else:
        debug_log("reply_case.send_succeeded", case_id=case_id, doctor_id=user["doctor_id"], sid=message_sid)

    debug_log(
        "reply_case.done",
        case_id=case_id,
        doctor_id=user["doctor_id"],
        sid=message_sid,
        audio_url=audio_url,
        delivery_status=delivery_status,
    )
    return RedirectResponse(url=f"/cases/{case_id}?delivery={delivery_status}", status_code=303)
