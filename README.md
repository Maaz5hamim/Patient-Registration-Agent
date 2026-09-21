# Patient Registration Agent

A production-grade voice AI agent that handles patient registration over the phone. Callers are greeted by **Emma**, a natural-sounding intake specialist, who collects registration details, saves them to a database, and verifies identity via a one-time passcode — entirely through spoken conversation with no keypad menus.

---

## How It Works

```
Caller dials Telnyx number
        │
        ▼
Telnyx plays hold message → POSTs webhook to /incoming-call
        │
        ├─ Agent pre-fetches patient record by phone number (async, in background)
        │
        ▼
Telnyx opens bidirectional RTP WebSocket → /ws
        │
        ▼
Pipecat pipeline starts
        │
  ┌─────▼──────┐     ┌──────────────┐     ┌─────────────┐     ┌─────────────┐
  │ Telnyx RTP │────▶│ Deepgram STT │────▶│ OpenAI LLM  │────▶│ Deepgram TTS│
  │ (µ-law in) │     │ (LINEAR16)   │     │ (Emma)      │     │ (LINEAR16)  │
  └────────────┘     └──────────────┘     └──────┬──────┘     └──────┬──────┘
                                                  │                   │
                                         Tool calls│                   │ PCM → µ-law
                                     (Python owns │                   │ (serializer)
                                      all state)  │                   ▼
                                                  │            Telnyx RTP out
                                                  ▼
                                         MongoDB (via backend API)
                                         Telnyx SMS (OTP)
```

---

## Tech Stack

### Agent Service (`/agent`)
| Component | Technology |
|---|---|
| Voice pipeline | [Pipecat](https://github.com/pipecat-ai/pipecat) 1.11.0 |
| Web server | FastAPI + Uvicorn |
| Speech-to-text | Deepgram Nova-2 (`linear16`, 8 kHz) |
| Text-to-speech | Deepgram Aura-2 (`linear16`, 8 kHz) |
| LLM | OpenAI Responses API (configurable model) |
| Voice activity detection | Silero VAD |
| Telephony transport | `TelnyxFrameSerializer` + `FastAPIWebsocketTransport` |
| HTTP client | httpx (async) |

### Backend Service (`/backend`)
| Component | Technology |
|---|---|
| Web server | FastAPI + Uvicorn |
| Database | MongoDB (async via Motor) |
| Validation | Pydantic v2 |
| Auth | API key header (`X-API-Key`) |

### Infrastructure
| Component | Technology |
|---|---|
| Telephony | Telnyx (TeXML + bidirectional RTP WebSocket) |
| SMS | Telnyx Messages API |
| Containerisation | Docker + Docker Compose |

---

## Architecture

### Two-service split

The system runs as two independent Docker services that communicate over an internal network:

- **Agent** (`port 8765`) — owns the real-time voice pipeline, all LLM interactions, and state for an active call. Stateless across calls.
- **Backend** (`port 8000`) — owns persistent storage and exposes a REST API. The agent calls it for patient lookups and record saves.

This separation means the backend can be swapped (Postgres, Firebase, etc.) without touching the voice pipeline, and the agent can scale horizontally since it holds no cross-call state.

### Call lifecycle

1. Telnyx delivers the call as a TeXML webhook to `/incoming-call`.
2. The agent immediately fires an **async background task** to look up the caller by phone number — this runs in parallel with Telnyx's hold message so the result is usually ready before the WebSocket connects.
3. Telnyx opens a **bidirectional RTP WebSocket** to `/ws`. The agent waits for the `start` event to extract `call_control_id` and `caller_phone` from the stream parameters.
4. Pipecat assembles the pipeline and the call begins.
5. On call end, `end_call` cancels the pipeline task and cleans up.

### LLM function architecture

Python owns all state and API calls. The LLM (Emma) owns only conversation. This is enforced by the tool design:

| Tool | What Python does | What LLM receives back |
|---|---|---|
| `verify_dob` | Compares supplied DOB against stored record, tracks attempt count | `{verified, attempts_remaining}` |
| `submit_registration` | Validates fields, normalises formats, saves to backend, sends OTP SMS | `{success, status, patient_id}` |
| `verify_otp` | Compares code against session secret, calls `mark_verified` | `{status}` |
| `end_call` | Cancels pipeline task | `{status}` |

The LLM never sees raw patient records or internal state — it only receives structured results designed to drive the next conversational turn.

---

## Optimisations

### Pre-call patient lookup
When the Telnyx webhook fires (`/incoming-call`), an `asyncio.Task` is launched immediately to query MongoDB for the caller's phone number. By the time the WebSocket connects and the pipeline starts, the lookup is usually complete — eliminating an otherwise synchronous database call at the start of every call.

### LINEAR16 audio throughout
Telnyx delivers µ-law (PCMU) audio. `TelnyxFrameSerializer` converts it to LINEAR16 PCM before passing it into the pipeline, and converts PCM back to µ-law on output. Both Deepgram services are configured with `encoding="linear16"` so they receive and send raw PCM — no re-encoding at any stage. An earlier version used `encoding="mulaw"` on Deepgram, which caused double-encoding (µ-law treated as PCM → re-encoded to µ-law → garbage audio).

### Bidirectional RTP transport
TeXML uses `<Stream bidirectionalMode="rtp">` which keeps a single persistent WebSocket open for both audio directions. This avoids the latency of separate inbound/outbound connections and matches how `TelnyxFrameSerializer` expects to operate.

### Bilingual support
The system prompt instructs Emma to ask for language preference at the start of each new-patient call and switch entirely to Spanish if requested — same pipeline, same tools, no second model or TTS voice needed.

---

## Project Structure

```
.
├── agent/
│   ├── bot.py            # FastAPI app, WebSocket handler, Pipecat pipeline
│   ├── prompts.py        # System prompt + tool schemas (FunctionSchema)
│   ├── tools.py          # Backend API calls, normalisation helpers
│   ├── requirements.txt
│   └── Dockerfile
├── backend/
│   └── app/
│       ├── main.py       # FastAPI app entry point
│       ├── models.py     # MongoDB collection names
│       ├── schemas.py    # Pydantic models (PatientCreate, PatientUpdate…)
│       ├── database.py   # Motor async client
│       ├── auth.py       # API key dependency
│       └── routers/
│           └── demographics.py  # CRUD endpoints for /patients
├── docker-compose.yml
└── test.html             # Browser-based WebSocket test client
```

---

## Environment Variables

Create a `.env` file in the project root:

```env
# Deepgram
DEEPGRAM_API_KEY=

# OpenAI (or compatible endpoint)
OPENAI_API_KEY=
LLM_BASE_URL=
LLM_MODEL=

# Telnyx
TELNYX_API_KEY=
TELNYX_PHONE_NUMBER=

# Backend
BACKEND_API_KEY=        # shared secret between agent and backend
BACKEND_URL=            # set automatically in docker-compose

# Agent
PORT=8765
```

---

## Running Locally

```bash
docker compose up --build
```

The agent is available at `http://localhost:8765` and the backend at `http://localhost:8000`.

### Testing without a real phone call (`test.html`)

`test.html` is a browser-based test client that connects directly to the agent's WebSocket, streams your microphone audio through the same µ-law codec Telnyx uses, and plays the agent's audio back through your speakers. You can have a full conversation with Emma without making an actual phone call.

**Steps:**

1. Start the stack:
   ```bash
   docker compose up --build
   ```

2. Open `test.html` directly in your browser (no server needed — just double-click the file or drag it into Chrome/Firefox).

3. The **WebSocket URL** field defaults to `ws://localhost:8765/ws`. Leave it as-is for local testing.

4. Set a **Caller Phone** number (e.g. `+15551234567`). This is passed as the `caller_phone` parameter so the agent can simulate a patient lookup. Use a number that exists in your database to test the returning-patient flow, or any fake number for the new-patient flow.

5. Click **Connect & Call**. Your browser will ask for microphone permission — allow it.

6. The status dot turns green and the pipeline starts. Speak naturally; the mic level bar shows your input volume.

7. Click **Hang Up** to end the session.

**What the log shows:**
- Yellow — connection events and pipeline state
- Blue — audio frames sent to the agent
- Green — audio frames received from the agent
- Red — errors

> **Note:** `test.html` bypasses Telnyx entirely — it connects directly to `/ws` and injects a fake `start` event with the phone number you entered. This tests the full voice pipeline (VAD → STT → LLM → TTS) but not the TeXML webhook or SMS delivery.

### Testing with a real Telnyx call

For end-to-end testing through an actual phone call you need a public URL. Use a tunnel:

```bash
ngrok http 8765
```

Set the TeXML webhook URL in your Telnyx number settings to `https://<your-ngrok-subdomain>.ngrok.io/incoming-call`.

---

## Backend API

| Method | Path | Description |
|---|---|---|
| `GET` | `/patients/` | List patients (filter by phone, last name, DOB) |
| `POST` | `/patients/` | Create a new patient record |
| `GET` | `/patients/{id}` | Get a patient by ID |
| `PUT` | `/patients/{id}` | Partial update |
| `DELETE` | `/patients/{id}` | Soft delete |
| `PATCH` | `/patients/{id}/verify` | Mark record as verified (called after OTP) |

All endpoints require `X-API-Key` header.

---

## Production Considerations

- This system collects Protected Health Information (PHI). A HIPAA-compliant host (e.g. Azure with a signed BAA) is required before handling real patient data.
- 10DLC campaign registration is required for the OTP SMS to be delivered by US carriers. Register a Brand and Campaign in the Telnyx portal and link the sending number to the campaign.
- The agent is stateless between calls — horizontal scaling is safe.
