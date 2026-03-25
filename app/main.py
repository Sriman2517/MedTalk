from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from twilio.twiml.messaging_response import MessagingResponse

from app.config import settings
from app.db import init_db
from app.repository import Repository
from app.triage import (
    build_audio_placeholder,
    detect_language,
    friendly_language_list,
    generate_case_summary,
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
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


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
    audio_url = None

    if NumMedia > 0 and MediaContentType0 and "audio" in MediaContentType0:
        message_type = "audio"
        audio_url = MediaUrl0
        incoming_text = incoming_text or "Voice note received"

    print(
        f"Incoming webhook form={form!r} parsed_phone={phone!r} "
        f"type={message_type!r} body={incoming_text!r} media_count={NumMedia!r}",
        file=sys.stderr,
        flush=True,
    )

    reply_text = handle_patient_message(phone, incoming_text, message_type, audio_url)
    if not reply_text or not reply_text.strip():
        reply_text = "I am having trouble processing that. Please send RESET to restart."

    twiml = MessagingResponse()
    twiml.message(reply_text)
    xml_response = str(twiml)
    print(f"Outgoing webhook phone={phone!r} xml={xml_response!r}", file=sys.stderr, flush=True)
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
    case = repository.get_case(case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    translated_reply = translate_for_patient(english_reply, case["patient_language"])
    audio_url = build_audio_placeholder(english_reply, case["patient_language"])
    completion = repository.complete_case(case_id, doctor_id, english_reply, translated_reply, audio_url)
    repository.add_message(completion["conversation_id"], "doctor", "text", translated_reply, english_reply, audio_url)
    return RedirectResponse(url=f"/cases/{case_id}", status_code=303)
