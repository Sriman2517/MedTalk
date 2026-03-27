from __future__ import annotations

import mimetypes
import os
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

from app.config import settings
from app.db import init_db
from app.repository import Repository
from app.sensory.transcriber import transcribe_audio
from app.sensory.vision import analyze_symptom_image
from app.triage import (
    detect_language,
    friendly_language_list,
    generate_case_summary,
    generate_real_audio,
    get_question,
    infer_specialty,
    infer_urgency,
    load_context,
    parse_language_choice,
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
    guessed = overrides.get(normalized) or mimetypes.guess_extension(normalized) or ".bin"
    debug_log("guess_media_suffix", content_type=content_type, chosen_suffix=guessed)
    return guessed


async def download_twilio_media(media_url: str, suffix: str) -> str:
    ensure_media_directories()

    account_sid = os.getenv("TWILIO_ACCOUNT_SID")
    auth_token = os.getenv("TWILIO_AUTH_TOKEN")
    if not account_sid or not auth_token:
        raise RuntimeError("Twilio credentials are not configured for media download.")

    sanitized_suffix = suffix if suffix.startswith(".") else f".{suffix}"
    output_path = DOWNLOADS_DIR / f"{uuid.uuid4().hex}{sanitized_suffix}"
    debug_log("download_twilio_media.start", media_url=media_url, suffix=sanitized_suffix, output_path=str(output_path))

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
    payload: dict[str, Any] = {
        "from_": from_number,
        "to": f"whatsapp:{to_phone}",
        "body": body,
    }
    if media_url:
        payload["media_url"] = [media_url]

    debug_log("send_whatsapp_message.start", to=payload["to"], from_=from_number, media_url=media_url)
    message = client.messages.create(**payload)
    debug_log("send_whatsapp_message.done", sid=message.sid, status=getattr(message, "status", None))
    return str(message.sid)


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


def handle_patient_message(phone: str, incoming_text: str, message_type: str, audio_url: str | None) -> str:
    repository.ensure_phone(phone)
    language = detect_language(incoming_text)
    conversation = repository.get_or_create_conversation(phone, language)
    debug_log(
        "handle_patient_message.start",
        phone=phone,
        message_type=message_type,
        stage=conversation["stage"],
        status=conversation["status"],
        patient_profile_id=conversation["patient_profile_id"],
        incoming_text=incoming_text,
    )
    context = load_context(conversation["context_json"])
    profiles = repository.get_profiles(phone)

    repository.add_message(
        conversation["id"],
        "patient",
        message_type,
        incoming_text,
        incoming_text if language == "en" else f"[English summary pending] {incoming_text}",
        audio_url,
    )

    stage = conversation["stage"]
    text = (incoming_text or "").strip()

    if text.lower() in {"reset", "start over"}:
        debug_log("handle_patient_message.reset", phone=phone, conversation_id=conversation["id"])
        context = {"draft_profile": {}, "interview_answers": {}, "interview_index": 0, "last_summary": None}
        repository.update_conversation(
            conversation["id"],
            stage="start",
            status="active",
            language=language,
            context=context,
        )
        stage = "start"
        conversation["patient_profile_id"] = None

    if stage == "start":
        if not profiles:
            repository.update_conversation(conversation["id"], stage="profile_name", status="active", language=language, context=context)
            reply = "Welcome to MedTalk. Let's create the first patient profile.\nWhat is the patient's name?"
            repository.add_message(conversation["id"], "bot", "text", reply, reply)
            return reply
        repository.update_conversation(conversation["id"], stage="profile_select", status="active", language=language, context=context)
        reply = profile_menu(profiles)
        repository.add_message(conversation["id"], "bot", "text", reply, reply)
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
                context={**context, "interview_answers": {}, "interview_index": 0},
            )
            first_q = translate_for_patient("Please describe the main problem in one or two sentences.", selected['preferred_language'])
            reply = f"Patient selected: {selected['name']}.\n{first_q}"
        elif choice == len(profiles) + 1:
            repository.update_conversation(conversation["id"], stage="profile_name", context=context)
            reply = "What is the new patient's name?"
        else:
            reply = "That profile number is not valid."
        repository.add_message(conversation["id"], "bot", "text", reply, reply)
        return reply

    if stage == "profile_name":
        if len(text) < 2:
            reply = "Please send a valid patient name."
        else:
            context["draft_profile"] = {"name": text}
            repository.update_conversation(conversation["id"], stage="profile_age", context=context)
            reply = "What is the patient's age?"
        repository.add_message(conversation["id"], "bot", "text", reply, reply)
        return reply

    if stage == "profile_age":
        age = safe_int(text)
        if age is None or age <= 0 or age > 120:
            reply = "Please enter age as a number."
        else:
            context["draft_profile"]["age"] = age
            repository.update_conversation(conversation["id"], stage="profile_gender", context=context)
            reply = "What is the patient's gender?"
        repository.add_message(conversation["id"], "bot", "text", reply, reply)
        return reply

    if stage == "profile_gender":
        if not text:
            reply = "Please enter the patient's gender."
        else:
            context["draft_profile"]["gender"] = text
            repository.update_conversation(conversation["id"], stage="profile_language", context=context)
            reply = f"Choose preferred language:\n{friendly_language_list()}"
        repository.add_message(conversation["id"], "bot", "text", reply, reply)
        return reply

    if stage == "profile_language":
        selected_language = parse_language_choice(text)
        if not selected_language:
            reply = f"Please choose a valid language.\n{friendly_language_list()}"
            repository.add_message(conversation["id"], "bot", "text", reply, reply)
            return reply
        draft = context["draft_profile"]
        profile_id = repository.create_profile(phone, draft["name"], int(draft["age"]), draft["gender"], selected_language)
        repository.update_conversation(
            conversation["id"],
            patient_profile_id=profile_id,
            stage="interview",
            status="active",
            language=selected_language,
            context={**context, "interview_answers": {}, "interview_index": 0},
        )
        first_q = translate_for_patient("Please describe the main problem in one or two sentences.", selected_language)
        reply = f"Profile created for {draft['name']}.\n{first_q}"
        repository.add_message(conversation["id"], "bot", "text", reply, reply)
        return reply

    if stage == "interview":
        current_profile = repository.get_profile(conversation["patient_profile_id"])
        if not current_profile:
            reply = "Profile not found. Send RESET to start again."
            repository.add_message(conversation["id"], "bot", "text", reply, reply)
            return reply
            
        english_text = incoming_text if language == "en" else f"[English summary pending] {incoming_text}"
        
        if infer_urgency(english_text) == "emergency":
            gp = repository.get_available_gp()
            case_id = repository.create_case(
                conversation["id"],
                current_profile["id"],
                f"🚨 EMERGENCY DETECTED: {english_text}",
                "emergency",
                "emergency_medicine",
                gp["id"] if gp else None,
            )
            debug_log("handle_patient_message.case_created", case_id=case_id, urgency="emergency", specialty="emergency_medicine")
            repository.update_conversation(
                conversation["id"],
                stage="queued_for_gp",
                status="queued_for_gp",
                language=current_profile["preferred_language"],
                context=context,
                active_case_id=case_id,
            )
            reply = translate_for_patient(
                "[URGENT_RED_FLAG] We have detected a medical emergency. Your case is escalated immediately to a doctor.",
                current_profile["preferred_language"],
            )
            repository.add_message(conversation["id"], "bot", "text", reply, "We have detected a medical emergency.")
            return reply

        try:
            db_messages = repository.list_messages(conversation["id"])
            reply_english = get_question(english_text, db_messages)
        except Exception:
            reply_english = "I am having trouble processing that. Can you please repeat?"
            reply = translate_for_patient(reply_english, current_profile["preferred_language"])
            repository.add_message(conversation["id"], "bot", "text", reply, reply_english)
            return reply
        if not reply_english or not reply_english.strip():
            reply_english = "I am having trouble processing that. Can you please repeat?"

        if "[INTERVIEW_COMPLETE]" in reply_english:
            summary_text, medical_brief = generate_case_summary(current_profile, db_messages)
            
            urgency = "emergency" if medical_brief.get("red_flags_detected") else "routine"
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
            debug_log("handle_patient_message.case_created", case_id=case_id, urgency=urgency, specialty=specialty)
            context["last_summary"] = summary_text
            repository.update_conversation(
                conversation["id"],
                stage="queued_for_gp",
                status="queued_for_gp",
                language=current_profile["preferred_language"],
                context=context,
                active_case_id=case_id,
            )
            reply_english = "Thank you. Your case has been prepared and sent to the general physician queue."
            reply = translate_for_patient(reply_english, current_profile["preferred_language"])
            repository.add_message(conversation["id"], "bot", "text", reply, reply_english)
            return reply

        reply = translate_for_patient(reply_english, current_profile["preferred_language"])
        repository.add_message(conversation["id"], "bot", "text", reply, reply_english)
        return reply

    if stage in {"queued_for_gp", "doctor_review", "referred"}:
        reply = translate_for_patient("Your case is under review. A doctor reply will reach you here.", conversation["language"])
        repository.add_message(conversation["id"], "bot", "text", reply, reply)
        return reply

    if stage == "doctor_replied":
        reply = translate_for_patient("This case is closed. Send RESET to start a new consultation.", conversation["language"])
        repository.add_message(conversation["id"], "bot", "text", reply, reply)
        return reply

    reply = "Unsupported state. Send RESET to restart."
    repository.add_message(conversation["id"], "bot", "text", reply, reply)
    return reply


@app.on_event("startup")
def startup() -> None:
    ensure_media_directories()
    debug_log(
        "startup",
        database_path=str(settings.database_path),
        public_base_url=PUBLIC_BASE_URL,
        text_only_whatsapp_replies=TEXT_ONLY_WHATSAPP_REPLIES,
    )
    init_db(settings.database_path)
    repository.seed_doctors()


@app.get("/")
def root() -> RedirectResponse:
    return RedirectResponse(url="/dashboard/gp")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "MedTalk running"}


@app.post("/webhook")
async def webhook(
    request: Request,
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

    if NumMedia > 0 and MediaContentType0 and MediaUrl0:
        guessed_suffix = guess_media_suffix(MediaContentType0)
        try:
            media_path = await download_twilio_media(MediaUrl0, guessed_suffix)
            if "audio" in media_type:
                message_type = "audio"
                transcribed_text = transcribe_audio(media_path)
                debug_log("webhook.audio_processed", media_path=media_path, transcription=transcribed_text)
                if incoming_text and transcribed_text:
                    incoming_text = f"{incoming_text}\n[Voice note transcript: {transcribed_text}]"
                else:
                    incoming_text = transcribed_text or incoming_text or "Voice note received"
            elif "image" in media_type:
                message_type = "image"
                analysis = analyze_symptom_image(media_path)
                debug_log("webhook.image_processed", media_path=media_path, analysis=analysis)
                image_note = f"[Patient uploaded an image. Vision Analysis: {analysis or 'Image received but analysis was inconclusive.'}]"
                incoming_text = f"{incoming_text}\n{image_note}".strip() if incoming_text else image_note
            else:
                message_type = "media"
                debug_log("webhook.unsupported_media", media_type=MediaContentType0, media_url=MediaUrl0)
                incoming_text = incoming_text or "Patient sent unsupported media."
        except Exception as exc:
            debug_log("webhook.media_processing_failed", media_type=MediaContentType0, media_url=MediaUrl0, error=str(exc))
            if "audio" in media_type:
                message_type = "audio"
                incoming_text = incoming_text or "I received your voice note, but I could not transcribe it. Please type the symptom in text."
            elif "image" in media_type:
                message_type = "image"
                image_note = "[Patient uploaded an image, but image analysis failed.]"
                incoming_text = f"{incoming_text}\n{image_note}".strip() if incoming_text else image_note
            else:
                message_type = "media"
                failure_note = "I couldn't process that media."
                incoming_text = f"{incoming_text}\n[{failure_note}]".strip() if incoming_text else failure_note

    debug_log(
        "webhook.incoming",
        form=form,
        phone=phone,
        message_type=message_type,
        body=incoming_text,
        media_count=NumMedia,
        media_type=MediaContentType0,
        media_path=media_path,
    )

    reply_text = handle_patient_message(phone, incoming_text, message_type, media_path)
    if not reply_text or not reply_text.strip():
        reply_text = "I am having trouble processing that. Please send RESET to restart."

    twiml = MessagingResponse()
    message = twiml.message(reply_text)
    if TEXT_ONLY_WHATSAPP_REPLIES:
        debug_log("webhook.text_only_reply", reply_text=reply_text)
    else:
        try:
            voice_language = repository.get_or_create_conversation(phone, detect_language(incoming_text)).get("language", "en")
            audio_url = generate_real_audio(reply_text, voice_language)
            public_audio_url = build_public_media_url(audio_url)
            if public_audio_url:
                message.media(public_audio_url)
            debug_log("webhook.tts_ready", voice_language=voice_language, audio_url=audio_url, public_audio_url=public_audio_url)
        except Exception as exc:
            debug_log("webhook.tts_failed", error=str(exc))
    xml_response = str(twiml)
    debug_log("webhook.outgoing", phone=phone, xml=xml_response)
    return Response(content=xml_response, media_type="text/xml")


@app.get("/dashboard/{role}", response_class=HTMLResponse)
def dashboard(request: Request, role: str) -> HTMLResponse:
    if role not in {"gp", "specialist"}:
        raise HTTPException(status_code=404, detail="Role not found")
    return templates.TemplateResponse(
        "dashboard.html",
        {"request": request, "role": role, "cases": repository.list_cases(role)},
    )


@app.get("/cases/{case_id}", response_class=HTMLResponse)
def case_detail(request: Request, case_id: int) -> HTMLResponse:
    case = repository.get_case(case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    return templates.TemplateResponse(
        "case_detail.html",
        {
            "request": request,
            "case": case,
            "messages": repository.list_messages(case["conversation_id"]),
            "specialists": repository.list_doctors("specialist"),
            "latest_response": repository.get_latest_response(case_id),
        },
    )


@app.post("/cases/{case_id}/refer")
def refer_case(case_id: int, specialist_id: int = Form(...), referral_note: str = Form(default="Please review this case.")) -> RedirectResponse:
    repository.refer_case(case_id, specialist_id, referral_note)
    return RedirectResponse(url=f"/cases/{case_id}", status_code=303)


@app.post("/cases/{case_id}/reply")
def reply_case(case_id: int, doctor_id: int = Form(...), english_reply: str = Form(...)) -> RedirectResponse:
    debug_log("reply_case.start", case_id=case_id, doctor_id=doctor_id, english_reply=english_reply)
    case = repository.get_case(case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    translated_reply = translate_for_patient(english_reply, case["patient_language"])
    audio_url = None
    public_audio_url = None
    if not TEXT_ONLY_WHATSAPP_REPLIES:
        try:
            audio_url = generate_real_audio(translated_reply, case["patient_language"])
            public_audio_url = build_public_media_url(audio_url)
        except Exception as exc:
            debug_log("reply_case.tts_failed", case_id=case_id, error=str(exc))
    else:
        debug_log("reply_case.text_only_send", case_id=case_id, translated_reply=translated_reply)

    try:
        message_sid = send_whatsapp_message(case["phone_number"], translated_reply, public_audio_url)
    except Exception as exc:
        debug_log("reply_case.send_failed", case_id=case_id, error=str(exc), phone=case["phone_number"])
        raise HTTPException(status_code=500, detail="Failed to send doctor reply to WhatsApp. Check server logs.") from exc

    completion = repository.complete_case(case_id, doctor_id, english_reply, translated_reply, audio_url)
    repository.add_message(completion["conversation_id"], "doctor", "text", translated_reply, english_reply, audio_url)
    debug_log("reply_case.done", case_id=case_id, message_sid=message_sid, stored_audio_url=audio_url)
    return RedirectResponse(url=f"/cases/{case_id}", status_code=303)
