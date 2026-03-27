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
    is_affirmative_language_switch,
    is_negative_language_switch,
    load_context,
    parse_language_choice,
    should_prompt_language_switch,
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
    payload: dict[str, Any] = {
        "from_": from_number,
        "to": f"whatsapp:{to_phone}",
        "body": body,
    }
    if media_url:
        payload["media_url"] = [media_url]

    debug_log("send_whatsapp_message.start", to=payload["to"], media_url=media_url)
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


def transcribe_media_audio(audio_file_path: str) -> str:
    from app.sensory.transcriber import transcribe_audio

    return transcribe_audio(audio_file_path)


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


def handle_patient_message(phone: str, incoming_text: str, message_type: str, media_url: str | None) -> str:
    repository.ensure_phone(phone)
    language = detect_language(incoming_text)
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

    repository.add_message(
        conversation["id"],
        "patient",
        message_type,
        incoming_text,
        incoming_text if language == "en" else f"[English summary pending] {incoming_text}",
        media_url,
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
                context={**context, "interview_answers": {}, "interview_index": 0, "temp_symptom": None, "temp_detected_language": None},
            )
            first_question = translate_for_patient("Please describe the main problem in one or two sentences.", selected["preferred_language"])
            reply = f"Patient selected: {selected['name']}.\n{first_question}"
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
            context={**context, "interview_answers": {}, "interview_index": 0, "temp_symptom": None, "temp_detected_language": None},
        )
        first_question = translate_for_patient("Please describe the main problem in one or two sentences.", selected_language)
        reply = f"Profile created for {draft['name']}.\n{first_question}"
        repository.add_message(conversation["id"], "bot", "text", reply, reply)
        return reply

    if stage == "confirm_language_switch":
        current_profile = repository.get_profile(conversation["patient_profile_id"])
        if not current_profile:
            reply = "Profile not found. Send RESET to start again."
            repository.add_message(conversation["id"], "bot", "text", reply, reply)
            return reply

        choice = safe_int(text)
        temp_symptom = context.get("temp_symptom") or text
        detected_language = context.get("temp_detected_language") or current_profile["preferred_language"]

        if choice == 1 or is_affirmative_language_switch(text):
            repository.update_profile(current_profile["id"], detected_language)
            current_profile["preferred_language"] = detected_language
            context["temp_symptom"] = None
            context["temp_detected_language"] = None
            repository.update_conversation(conversation["id"], stage="interview", language=detected_language, context=context)
            return handle_patient_message(phone, temp_symptom, "text", media_url)

        if choice == 2 or is_negative_language_switch(text):
            context["temp_symptom"] = None
            context["temp_detected_language"] = None
            repository.update_conversation(conversation["id"], stage="interview", context=context)
            return handle_patient_message(phone, temp_symptom, "text", media_url)

        reply = build_language_switch_prompt(current_profile["preferred_language"], detected_language)
        repository.add_message(conversation["id"], "bot", "text", reply, reply)
        return reply

    if stage == "interview":
        current_profile = repository.get_profile(conversation["patient_profile_id"])
        if not current_profile:
            reply = "Profile not found. Send RESET to start again."
            repository.add_message(conversation["id"], "bot", "text", reply, reply)
            return reply

        preferred_language = current_profile["preferred_language"]
        if should_prompt_language_switch(preferred_language, language, text) and not context.get("temp_symptom"):
            context["temp_symptom"] = incoming_text
            context["temp_detected_language"] = language
            repository.update_conversation(conversation["id"], stage="confirm_language_switch", context=context)
            reply = build_language_switch_prompt(preferred_language, language)
            repository.add_message(conversation["id"], "bot", "text", reply, reply)
            return reply

        english_text = incoming_text if language == "en" else f"[English summary pending] {incoming_text}"
        if infer_urgency(english_text) == "emergency":
            gp = repository.get_available_gp()
            urgency_reason = explain_urgency(english_text, urgency="emergency")
            emergency_summary = "\n".join(
                [
                    f"Patient: {current_profile['name']}, {current_profile['age']} years, {current_profile['gender']}.",
                    f"Preferred language: {get_language_name(current_profile['preferred_language'])}.",
                    f"Chief complaint: {english_text}.",
                    "Urgency: emergency.",
                    f"Urgency reason: {urgency_reason}",
                    "Suggested specialty: emergency_medicine.",
                ]
            )
            case_id = repository.create_case(
                conversation["id"],
                current_profile["id"],
                emergency_summary,
                "emergency",
                "emergency_medicine",
                gp["id"] if gp else None,
            )
            repository.update_conversation(
                conversation["id"],
                stage="queued_for_gp",
                status="queued_for_gp",
                language=preferred_language,
                context=context,
                active_case_id=case_id,
            )
            reply = translate_for_patient(
                "We have detected a medical emergency. Your case has been escalated immediately to a doctor.",
                preferred_language,
            )
            repository.add_message(conversation["id"], "bot", "text", reply, "We have detected a medical emergency.")
            return reply

        db_messages = repository.list_messages(conversation["id"])
        reply_english = get_question(english_text, db_messages)
        if not reply_english or not reply_english.strip():
            reply_english = "I am having trouble processing that. Can you please repeat?"

        if "[INTERVIEW_COMPLETE]" in reply_english:
            summary_text, medical_brief = generate_case_summary(current_profile, db_messages)
            urgency = "emergency" if medical_brief.get("red_flags_detected") else infer_urgency(summary_text)
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
                context=context,
                active_case_id=case_id,
            )
            reply_english = "Thank you. Your case has been prepared and sent to the general physician queue."

        reply = translate_for_patient(reply_english.replace("[INTERVIEW_COMPLETE]", "").strip(), preferred_language)
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
    init_db(settings.database_path)
    repository.seed_doctors()
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
    return RedirectResponse(url=f"/dashboard/{user['role']}", status_code=303)


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
    response = RedirectResponse(url=f"/dashboard/{user['role']}", status_code=303)
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
    response = RedirectResponse(url=f"/dashboard/{user['role']}", status_code=303)
    set_auth_cookie(response, token)
    return response


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
                transcription = transcribe_media_audio(media_path)
                if incoming_text and transcription:
                    incoming_text = f"{incoming_text}\n[Voice note transcript: {transcription}]"
                else:
                    incoming_text = transcription or incoming_text or "Voice note received"
                debug_log("webhook.audio_processed", media_path=media_path, transcription=transcription)
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
        reply_text = handle_patient_message(phone, incoming_text, message_type, media_path)
    if not reply_text or not reply_text.strip():
        reply_text = "I am having trouble processing that. Please send RESET to restart."

    twiml = MessagingResponse()
    message = twiml.message(reply_text)
    if TEXT_ONLY_WHATSAPP_REPLIES:
        debug_log("webhook.text_only_reply", reply_text=reply_text)
    else:
        try:
            conversation = repository.get_or_create_conversation(phone, detect_language(incoming_text))
            voice_language = conversation.get("language", "en")
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
def dashboard(request: Request, role: str) -> Response:
    auth = guest_redirect(request)
    if isinstance(auth, RedirectResponse):
        return auth
    if role not in {"gp", "specialist"} or auth["role"] != role:
        raise HTTPException(status_code=403, detail="Forbidden")
    cases = [annotate_case(case) for case in repository.list_cases(role, int(auth["doctor_id"]))]
    return templates.TemplateResponse("dashboard.html", {"request": request, "role": role, "cases": cases, "current_user": auth})


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

    return templates.TemplateResponse(
        "case_detail.html",
        {
            "request": request,
            "case": annotate_case(case),
            "messages": repository.list_messages(case["conversation_id"]),
            "specialists": repository.list_doctors("specialist"),
            "latest_response": repository.get_latest_response(case_id),
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
def reply_case(request: Request, case_id: int, english_reply: str = Form(...)) -> RedirectResponse:
    user = require_user(request, {"gp", "specialist"})
    case = repository.get_case(case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    if user["role"] == "gp" and case.get("assigned_doctor_id") != user["doctor_id"]:
        raise HTTPException(status_code=403, detail="Forbidden")
    if user["role"] == "specialist" and case.get("assigned_specialist_id") != user["doctor_id"]:
        raise HTTPException(status_code=403, detail="Forbidden")

    translated_reply = translate_for_patient(english_reply, case["patient_language"])
    audio_url = None
    public_audio_url = None

    if not TEXT_ONLY_WHATSAPP_REPLIES:
        try:
            audio_url = generate_real_audio(translated_reply, case["patient_language"])
            public_audio_url = build_public_media_url(audio_url)
        except Exception as exc:
            debug_log("reply_case.tts_failed", case_id=case_id, error=str(exc))

    try:
        message_sid = send_whatsapp_message(case["phone_number"], translated_reply, public_audio_url)
    except Exception as exc:
        debug_log("reply_case.send_failed", case_id=case_id, error=str(exc), phone=case["phone_number"])
        raise HTTPException(status_code=500, detail="Failed to send doctor reply to WhatsApp. Check server logs.") from exc

    completion = repository.complete_case(case_id, int(user["doctor_id"]), english_reply, translated_reply, audio_url or "")
    repository.add_message(completion["conversation_id"], "doctor", "text", translated_reply, english_reply, audio_url)
    debug_log("reply_case.done", case_id=case_id, doctor_id=user["doctor_id"], sid=message_sid, audio_url=audio_url)
    return RedirectResponse(url=f"/cases/{case_id}", status_code=303)
