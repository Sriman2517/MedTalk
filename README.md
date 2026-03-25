# MedTalk
Multilingual Voice-First Symptom Narratives for Low-Literacy Patients

## What is built
- WhatsApp webhook with Twilio-compatible TwiML replies
- Multi-profile patient onboarding under one phone number
- Guided symptom interview flow
- Structured English case summary generation
- GP queue and specialist queue dashboards
- Referral workflow
- Doctor reply flow with translated-text placeholder and voice placeholder
- SQLite persistence for demos

## Run locally
```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open:
- `http://127.0.0.1:8000/dashboard/gp`
- `http://127.0.0.1:8000/dashboard/specialist`

## Twilio webhook
Point your Twilio WhatsApp sandbox webhook to:

```text
POST /webhook
```

For local testing with ngrok, use:

```text
https://<your-ngrok-url>/webhook
```

## Current MVP notes
- Voice notes are accepted and stored, but transcription is still a placeholder.
- Patient translation and TTS are placeholders for now, designed so you can swap in a real LLM/STT/TTS provider fast.
- Database is stored in `medtalk.db` by default.

## Best next upgrades
1. Plug in real speech-to-text for incoming audio.
2. Plug in real translation + text-to-speech for outgoing doctor replies.
3. Replace the rule-based interview with an LLM-driven questioning service.
4. Add authentication for doctor dashboards before public demos.
