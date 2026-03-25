# app/main.py  (full file)
from fastapi import FastAPI, Request, Form
from fastapi.responses import PlainTextResponse
from twilio.twiml.messaging_response import MessagingResponse
from dotenv import load_dotenv
import os

load_dotenv()
app = FastAPI(title="MedTalk")

@app.get("/")
def root():
    return {"status": "MedTalk running"}

@app.post("/webhook", response_class=PlainTextResponse)
async def webhook(
    From: str = Form(...),
    Body: str = Form(default=""),
    NumMedia: int = Form(default=0),
    MediaContentType0: str = Form(default=None),
):
    phone = From.replace("whatsapp:", "")
    print(f"From: {phone} | Body: {Body!r} | Media: {NumMedia}")

    if NumMedia > 0 and MediaContentType0 and "audio" in MediaContentType0:
        reply = "Voice note received! (pipeline coming in Step 5)"
    elif Body.strip():
        reply = f"MedTalk got: {Body.strip()}"
    else:
        reply = "Empty or unsupported message."

    twiml = MessagingResponse()
    twiml.message(reply)
    return str(twiml)