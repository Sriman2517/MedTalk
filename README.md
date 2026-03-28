# MedTalk

Multilingual, WhatsApp-native, voice-first medical triage for low-literacy and underserved patients.

MedTalk helps patients describe symptoms through WhatsApp using text, voice notes, and images. The system guides intake, summarizes the case into a structured clinical brief, assigns urgency, routes the case to the right doctor workflow, and supports patient-friendly doctor follow-up.

## Highlights

- WhatsApp-first patient intake through Twilio
- Multi-profile family onboarding under one phone number
- Text, audio, and image symptom input
- Multilingual flow with mid-conversation language switching
- LLM-guided symptom interview
- Structured case summary generation
- 3-tier urgency handling: `HIGH`, `MEDIUM`, `LOW`
- GP queue, specialist referral, and admin dashboard
- Secure doctor authentication with role-based access
- Patient confidence notification when a doctor takes ownership
- Past summary view for continuity of care

## Current Product Flow

1. A patient sends a WhatsApp message, voice note, or image.
2. Twilio forwards the request to `/webhook`.
3. MedTalk:
   - downloads media if present
   - transcribes audio
   - analyzes uploaded images
   - detects language and interview stage
4. The patient selects or creates a profile.
5. The AI-guided interview continues in the preferred language.
6. If a strong language shift is detected, MedTalk shows a language menu.
7. Once enough information is collected, MedTalk creates:
   - a structured English summary
   - an urgency level
   - a specialty hint
8. The case enters the doctor workflow.
9. A GP or Specialist reviews the case and sends a structured reply.
10. The patient receives WhatsApp updates and doctor responses.

## Key Design Decisions

### Temporary Chat, Permanent Summary

Live patient-bot interview turns are kept in temporary conversation context during intake. Only the final structured summary and workflow state are saved permanently. This keeps the doctor view cleaner and reduces noisy raw-chat persistence.

### Structured Doctor Communication

Doctor replies are split into two sections:

- `Possible Cause / Reason`
- `Care Plan`

This makes responses more compassionate, easier to understand, and safer for patient communication.

### Urgency-Aware Workflow

MedTalk supports three urgency levels:

- `HIGH`: emergency-style warning and immediate escalation behavior
- `MEDIUM`: urgent but non-emergency review
- `LOW`: routine queue processing

## AI / Model Stack

### LLM and Reasoning

- **Interview follow-up generation**
  - Provider: Groq
  - Model: `llama-3.3-70b-versatile`

- **Case summarization**
  - Provider: Groq
  - Model: `openai/gpt-oss-120b`

- **Translation**
  - Provider: Groq
  - Model: `llama-3.3-70b-versatile`

- **Specialty classification**
  - Provider: Groq
  - Model: `openai/gpt-oss-120b`

### Speech and Vision

- **Speech transcription**
  - Primary: Google Gemini audio pipeline
  - Fallback: Groq Whisper `whisper-large-v3-turbo`

- **Symptom image analysis**
  - Provider: Google Gemini Vision

- **Text-to-speech**
  - Engine: gTTS
  - Currently disabled in normal runtime when `TEXT_ONLY_WHATSAPP_REPLIES=1`

## Supported Languages

The language menu currently supports:

1. Hindi
2. Telugu
3. Tamil
4. English
5. Bengali
6. Marathi
7. Kannada
8. Urdu

## Roles

### GP

- reviews queued cases
- sends patient replies
- refers cases to specialists

### Specialist

- reviews referred cases
- responds to specialist-assigned cases

### Admin

- monitors platform activity
- views patient-side and doctor-side system stacks
- sees recent cases and doctor workload overview

## Tech Stack

- **Backend:** FastAPI
- **Templates:** Jinja2
- **Styling:** custom CSS
- **Database:** SQLite
- **Messaging:** Twilio WhatsApp
- **HTTP/media handling:** httpx
- **Auth:** PBKDF2 password hashing + JWT session cookies
- **Runtime config:** python-dotenv

## Project Structure

```text
app/
  auth.py               # password hashing and session token handling
  config.py             # environment-driven settings
  db.py                 # SQLite schema initialization
  main.py               # FastAPI routes, webhook, dashboards, auth flow
  repository.py         # persistence layer and workflow queries
  triage.py             # language, triage, summary helpers, business logic
  llm/
    interviewer.py      # LLM follow-up question generation
    summarizer.py       # structured medical brief generation
    translator.py       # patient-facing translation
    classifier.py       # specialty recommendation
    guardrails.py       # emergency red-flag logic
  sensory/
    transcriber.py      # audio transcription and language detection
    synthesizer.py      # TTS generation
    vision.py           # image analysis
  templates/
    *.html              # doctor, auth, profile, admin pages
  static/
    styles.css          # UI styling
    downloads/          # temporary media downloads
    audio/              # generated audio files
```

## Database Entities

The app persists workflow and continuity data through SQLite.

Main entities include:

- `phones`
- `patient_profiles`
- `conversations`
- `messages`
- `cases`
- `doctor_responses`
- `doctors`
- `users`
- `ownership_notifications`

## Setup

### 1. Install dependencies

```powershell
pip install -r requirements.txt
```

### 2. Configure environment

Create or update [`.env`](./.env) with values like:

```env
APP_NAME=MedTalk
DATABASE_PATH=medtalk.db
DEFAULT_LANGUAGE=en

TWILIO_ACCOUNT_SID=your_twilio_account_sid
TWILIO_AUTH_TOKEN=your_twilio_auth_token
TWILIO_WHATSAPP_FROM=whatsapp:+14155238886

GROQ_API_KEY=your_groq_api_key
GEMINI_API_KEY=your_gemini_api_key
PUBLIC_BASE_URL=https://your-ngrok-or-public-domain

AUTH_SECRET_KEY=replace_with_a_long_random_secret
AUTH_COOKIE_NAME=medtalk_session
AUTH_COOKIE_SECURE=false

ADMIN_EMAIL=admin@medtalk.local
ADMIN_PASSWORD=Admin1234
ADMIN_FULL_NAME=MedTalk Admin

TEXT_ONLY_WHATSAPP_REPLIES=1
```

### 3. Run the app

```powershell
uvicorn app.main:app --reload
```

### 4. Open the app

- Login: `http://127.0.0.1:8000/login`
- Register: `http://127.0.0.1:8000/register`
- GP dashboard: `http://127.0.0.1:8000/dashboard/gp`
- Specialist dashboard: `http://127.0.0.1:8000/dashboard/specialist`
- Admin dashboard: `http://127.0.0.1:8000/dashboard/admin`

## Twilio / WhatsApp Setup

Point your Twilio WhatsApp sandbox webhook to:

```text
https://<your-public-url>/webhook
```

Use:

- method: `POST`
- a public HTTPS URL, typically from ngrok during local development

If you are serving generated media back to Twilio, `PUBLIC_BASE_URL` must match the same public domain.

## Authentication

Doctor authentication is role-based and currently supports:

- `gp`
- `specialist`
- `admin`

Features:

- secure password hashing with PBKDF2
- signed session cookie
- role-protected dashboards
- doctor-linked case access restrictions

Admin users are seeded from environment configuration if the credentials are provided.

## Dashboards

### Doctor Dashboard

The doctor workspace includes:

- urgency-prioritized queue
- dashboard stack cards
- case detail view
- structured doctor reply form
- referral controls
- patient confidence update tracking
- past case summaries for the same patient

### Admin Dashboard

The admin dashboard shows:

- patient-side intake volume
- doctor-side active users
- urgency pressure
- recent cases
- doctor workload snapshots

## Media Handling

### Audio

- Twilio media is downloaded to `app/static/downloads/`
- audio is transcribed and language-tagged
- noisy or unreliable transcripts are rejected and the patient is asked to repeat or type the symptom

### Image

- uploaded symptom images are analyzed for visible findings
- image analysis is converted into patient-context text before the clinical flow continues

## Text vs Audio Replies

MedTalk currently runs in **text-only patient reply mode** by default:

```env
TEXT_ONLY_WHATSAPP_REPLIES=1
```

That means:

- WhatsApp replies are sent as text only
- no audio reply is generated or delivered
- TTS API usage is avoided

If you switch this off later, the backend already contains the logic to generate and send patient-language voice replies.

## Safety Features

- red-flag emergency handling
- noisy transcript rejection
- language-switch safety menu
- structured, patient-friendly doctor communication
- continuity through past summary visibility
- doctor ownership notifications to reassure patients

## Current Limitations

- SQLite is used for demo and hackathon workflow, not production scale
- Twilio sandbox limits can affect WhatsApp delivery
- speech quality still depends on input quality and provider performance
- some multilingual fallback text paths still exist if external APIs fail
- README and code are now aligned better, but the project is still a prototype, not a production hospital system

## Recommended Next Steps

1. Move from SQLite to PostgreSQL for production-style deployment.
2. Add audit logs and admin filtering by date, urgency, and doctor.
3. Improve Indic speech robustness with stronger STT evaluation and fallback strategies.
4. Add production-grade observability and error monitoring.
5. Integrate with hospital or EMR systems for real-world pilots.

## Demo Summary

MedTalk demonstrates a full AI-assisted care-intake loop:

- patient speaks in their own language
- AI structures the case
- doctors receive a cleaner summary
- urgency is surfaced early
- patients stay informed through WhatsApp

This makes MedTalk especially useful for multilingual, low-literacy, and access-constrained care settings.
