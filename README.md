# drVibey Web Services API (Genify prototype)

drVibey is a data-driven Web API and companion app that learns a listener’s taste from **playlist/library screenshots + a short chat**, builds a structured **listener profile (“musical DNA”)**, assigns an **MBTI-style listener type code**, and lets users **share that profile with a public link**. It then generates **new original songs** tailored to the user’s mood/activity and optional constraints (genre, BPM, reference track).

This repository contains a working prototype (Flask + Postgres + Redis/RQ) with an end-to-end flow: OCR → profile synthesis → generation → playback/download → favourites/history.

## Coursework framing (COMP3011 CW1)

For COMP3011 Coursework 1, this project is positioned as a REST-style JSON API backed by Postgres.
The **primary assessed CRUD model is `Generation`** (Create/Read/Update/Delete persisted in DB).
The submission also includes API documentation and report artifacts in PDF format.

### Coursework compliance mapping

| Requirement | Status | Evidence |
|---|---|---|
| At least 1 DB model with full CRUD | ✅ | `Generation` model + `POST/GET/PATCH/DELETE /api/generation*` in `app/routes/api.py` |
| At least 4 HTTP JSON endpoints | ✅ | Multiple `/api/*` endpoints (generation/profile/chat/analytics) |
| Correct status/error handling | ✅ | Standardized JSON helpers and status codes on coursework-critical generation endpoints |
| API documentation PDF | ✅ | `docs/API_Documentation.pdf` |
| Technical/API report PDF | ✅ | `docs/Technical_API_Report.pdf` |
| GenAI declaration/log excerpts | ✅ | `docs/GenAI_Declaration_Appendix.pdf` |
| Presentation slides (PPTX) | ✅ | `docs/COMP3011_Presentation_Slides.pptx` |
| Demonstrable run instructions | ✅ | Docker Compose run + API demo steps below |

## What’s implemented (prototype)

- **Taste capture without platform APIs**: upload 3–10 screenshots (Spotify/Apple Music/YouTube Music/etc) and extract track + artist text via local OCR.
- **drVibey chat flow**: 10 short questions (screenshots first, then quick profiling).
- **Profile synthesis (“musical DNA”)**: generates a structured JSON profile + a short “Vibe Diagnosis”.
- **MBTI-style listener typing**: derives a 4-letter listener code (e.g. `FVPD`) and maps it to named archetypes like *Neon Oracle*.
- **Profile sharing**: creates a secure share token and public profile URL (`/vibe/<profile_id>/<token>`) so users can share their vibe identity.
- **Generation controls**:
  - Mood + **intensity** slider
  - Activity selection
  - **Instrumental vs Vocal** toggle
  - Optional fine-tuning: **song reference**, **genre**, **BPM**
- **Music generation pipeline**: creates a prompt from the profile + controls, sends it to the configured generation provider, and polls until stream audio is available.
- **Accounts & persistence**:
  - Firebase Auth (Google + passwordless email link)
  - Save profiles + generations in Postgres
  - Recents list, favourites, and download URL polling

## Tech stack

- **Backend**: Flask 3, SQLAlchemy, Postgres
- **Jobs/queue**: Redis + RQ worker
- **OCR**: Tesseract via `pytesseract` (Docker image installs multiple languages)
- **LLM**: Cerebras Cloud SDK (used for OCR cleanup + profile synthesis + diagnosis + prompt shaping)
- **Generation**: Suno API via a thin `SunoClient`
- **Frontend**: vanilla HTML/CSS/JS (single-page flow in `app/templates/public.html` + `app/static/public.js`)
- **E2E tests**: Playwright

## Repository layout (high level)

- `app/`
  - `routes/`: Flask routes (`/api/*`, auth, public UI)
  - `services/`: OCR, chat, prompt shaping, provider clients
  - `jobs/`: RQ queue + background tasks
  - `models.py`: DB models (users, profiles, generations, etc.)
  - `templates/`: UI templates (`public.html` is the main product UI)
  - `static/`: JS/CSS assets
- `migrations/`: SQL migrations for generations metadata fields
- `docker-compose.yml`: Postgres + Redis + web + worker (+ optional Cloudflare tunnel)
- `Dockerfile`: installs Python deps + Tesseract

## Running locally (recommended: Docker Compose)

### 1) Create a `.env`

This repo expects a `.env` file (ignored by git). Minimum environment variables depend on which features you want to run.

Common variables:

- `FLASK_SECRET_KEY`: random string
- `DATABASE_URL`: Postgres connection string (Compose example: `postgresql+psycopg2://drvibey:drvibey_pw@db:5432/drvibey`)
- `REDIS_URL`: Compose example: `redis://redis:6379/0`

LLM + generation:

- `CEREBRAS_API_KEY`: required for OCR cleanup + profile synthesis + diagnosis + prompt generation
- `LLM_MODEL`: optional (default `gpt-oss-120b`)
- `SUNO_API_KEY`: required to generate music
- `SUNO_BASE_URL`: optional (default `https://api.sunoapi.org`)
- `SUNO_MODEL`: optional (defaults to `V5`/mapped by code)

Firebase auth (optional for anonymous demo; required to save/restore across sessions):

- `FIREBASE_PROJECT_ID`
- `FIREBASE_WEB_API_KEY`
- `FIREBASE_AUTH_DOMAIN`
- `FIREBASE_STORAGE_BUCKET`
- `FIREBASE_MESSAGING_SENDER_ID`
- `FIREBASE_APP_ID`
- `GOOGLE_APPLICATION_CREDENTIALS` (optional): path to service account JSON (or place `firebase-service-account.json` in repo root for local dev)

### 2) Start services

```bash
docker compose up --build
```

Compose starts:

- `db` (Postgres on host port `5433`)
- `redis` (host port `6379`)
- `web` (Flask app on host port `7777`, runs `python run.py --init-db`)
- `worker` (RQ worker, runs `python worker.py`)

Open `http://localhost:7777/`.

### API base URL

The API blueprint is currently mounted at:

- `http://localhost:7777/api`

## Running without Docker (advanced)

You’ll need:

- Python 3.11+
- Postgres + Redis running locally
- Tesseract installed on your OS (plus languages if desired)

Then:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export $(cat .env | xargs)  # or set env vars manually
python run.py --init-db
python worker.py
```

## Key flows (how the product works)

### Chat → profile

1. Frontend calls `POST /api/chat/message` with `{ init: true }` to get Q1 (screenshot prompt).
2. User uploads screenshots to `POST /api/chat/upload-screenshots` (multipart). Backend:
   - saves to temp
   - runs OCR (`app/services/ocr.py`)
   - optionally cleans OCR output via Cerebras
   - returns a dynamic Q2 chip-select question built from extracted tracks
3. Frontend collects chat history and calls `POST /api/chat/build-profile` to synthesize:
   - `profile_json` (structured musical DNA)
   - `diagnosis_json` (short “Vibe Diagnosis”)
   - stored as a `ListenerProfile` row

### MBTI-style profile + sharing

1. After profile synthesis, the app resolves a listener type code (e.g. `FVPD`, `NICR`) using profile traits such as novelty/discovery, emotional depth, intensity, and listening orientation.
2. Type codes are mapped to named archetypes in `app/services/type_catalog.py` (e.g. `FVPD -> Neon Oracle`).
3. On the profile screen, users can press **Share profile**. Frontend calls `POST /api/profile/share`.
4. Backend stores (or reuses) a per-profile `share_token` and returns a public URL:
   - `/vibe/<listener_profile_id>/<share_token>`
5. Anyone with that link can open a read-only share page (`app/templates/vibe_share.html`) showing:
   - type code + archetype name/description
   - key profile stats (emotional depth, curiosity, power, nostalgia)
   - core genres/artists/suggested artists
   - generated avatar (if available)

### Profile → generation

1. Frontend collects generation controls (mood, intensity, activity, instrumental/vocal, song reference, genre, BPM).
2. `POST /api/generate` creates a `Generation` row and enqueues `run_generation_pipeline`.
3. Worker:
   - uses Cerebras to generate a short provider prompt (`app/services/openai_prompt.py`)
   - calls Suno API (`app/services/suno_client.py`)
   - polls until streaming audio URL is available
4. Frontend polls `GET /api/generation/<id>` and renders playback immediately; it separately polls `GET /api/generation/<id>/download-url` to enable final MP3 download when ready.

## API endpoints (selected)

- `POST /api/chat/message` (init + step-through questions)
- `POST /api/chat/upload-screenshots`
- `POST /api/chat/build-profile`
- `GET /api/profile`
- `POST /api/profile/share`
- `POST /api/generate` *(Create for primary CRUD model)*
- `GET /api/generation/<id>` *(Read for primary CRUD model)*
- `PATCH /api/generation/<id>` *(Update for primary CRUD model)*
- `DELETE /api/generation/<id>` *(Delete for primary CRUD model)*
- `GET /api/generation/<id>/download-url`
- `GET /api/generations`
- `GET /api/generations/favourites`
- `PATCH /api/generation/<id>/favourite`
- `GET /api/analytics/generations/summary?days=30`
- `GET /api/auth/me`
- `POST /api/auth/verify-token`
- `POST /api/auth/logout`

Public route:

- `GET /vibe/<profile_id>/<token>` (shared listener profile page)

## Testing

Playwright is set up in `tests/example.spec.ts` and `.github/workflows/playwright.yml`.

Coursework API-focused tests are provided in:

- `tests/test_api_generation_crud.py`

These cover create/read/update/delete lifecycle plus auth and validation errors for the `Generation` resource.

## Coursework demo steps (assessor quick path)

1. Start services:

```bash
docker compose up --build
```

2. Open app:

- `http://localhost:7777/`

3. Authenticate (or enter via anonymous demo flow), then run CRUD sequence against the API base URL:

- `POST /api/generate`
- `GET /api/generation/<id>`
- `PATCH /api/generation/<id>`
- `DELETE /api/generation/<id>`
- `GET /api/generation/<id>` (expect 404)

4. Run analytics endpoint:

- `GET /api/analytics/generations/summary?days=30`

## Deliverables (coursework artifacts)

- API documentation PDF: `docs/API_Documentation.pdf`
- Technical/API report PDF: `docs/Technical_API_Report.pdf`
- GenAI declaration appendix PDF: `docs/GenAI_Declaration_Appendix.pdf`
- Presentation slides (PPTX): `docs/COMP3011_Presentation_Slides.pptx`
- API docs source: `docs/api-documentation.md`
- Report source: `docs/technical-report.md`
- GenAI declaration source: `docs/genai-declaration.md`
- Slides source: `docs/presentation-slides.md`

```bash
npm ci
npx playwright test
```

## Research direction (ARG-L)

The long-term research concept in `ARG-L_Research_Pitch.docx` explores **Artist‑Rewarding Generative Listening (ARG‑L)**: auditable, embedding-grounded generative radio where value flows back to opt‑in artists whose released work defines high-satisfaction regions—rather than paying the generator simply for “being used.”

