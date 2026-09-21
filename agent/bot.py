import asyncio
import json
import os
import secrets
import sys
from datetime import datetime
from loguru import logger

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import PlainTextResponse

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import TTSSpeakFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
)
from pipecat.serializers.telnyx import TelnyxFrameSerializer
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.deepgram.tts import DeepgramTTSService
from pipecat.services.llm_service import FunctionCallParams
from pipecat.services.openai.responses.llm import OpenAIResponsesLLMService
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)

from prompts import SYSTEM_PROMPT, TOOLS
from tools import (
    lookup_patient_by_phone,
    mark_verified,
    normalize_date,
    normalize_otp,
    normalize_state,
    save_demographics,
    send_otp_sms,
    update_patient,
)

load_dotenv(override=True)

logger.remove()
logger.add(sys.stdout, level="DEBUG")

DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY")
TELNYX_API_KEY = os.getenv("TELNYX_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
LLM_BASE_URL = os.getenv("LLM_BASE_URL")
LLM_MODEL = os.getenv("LLM_MODEL")
PORT = int(os.getenv("PORT", "8765"))

app = FastAPI(title="Patient Registration Agent")
_pending_lookups: dict[str, asyncio.Task] = {}

REQUIRED_FIELDS = {
    "first_name", "last_name", "date_of_birth", "sex",
    "address_line_1", "city", "state", "zip_code"
}
ALLOWED_FIELDS = {
    "first_name", "last_name", "date_of_birth", "sex", "email",
    "address_line_1", "address_line_2", "city", "state", "zip_code",
    "insurance_provider", "insurance_member_id",
    "emergency_contact_name", "emergency_contact_phone",
    "preferred_language",
}
DOB_MAX_ATTEMPTS = 3
OTP_MAX_ATTEMPTS = 3

GREETING_TEXT = [
    "Thank you for calling Patient Registration, where your health is our priority. ",
    "Gracias por llamar al Registro de Pacientes, donde su salud es nuestra prioridad. ",
    "If this is a medical emergency, please hang up and dial 911. ",
    "Si esto es una emergencia médica, por favor cuelgue y llame al 911. ",
    "Please wait a moment while we connect your call to one of our representative."
]

async def patient_lookup(phone: str) -> dict:
    try:
        result = await lookup_patient_by_phone(phone)
        return result if isinstance(result, dict) else {"found": False}
    except Exception:
        logger.exception("Patient lookup failed for {}", phone)
        return {"found": False}


def lookup_context(result: dict, phone: str) -> str:
    if not result.get("found"):
        return (
            "CALL DATA\n"
            f"caller_phone: {phone}\n"
            "lookup.found: false"
        )

    patient = result.get("patient", {})
    lines = []
    name = f"{patient.get('first_name', '')} {patient.get('last_name', '')}".strip()
    if name:
        lines.append(f"Name: {name}")
    for field, label in (
        ("date_of_birth", "Date of Birth"),
        ("sex", "Sex"),
        ("phone_number", "Phone"),
        ("email", "Email"),
        ("preferred_language", "Preferred Language"),
    ):
        if patient.get(field):
            lines.append(f"{label}: {patient[field]}")

    address = ", ".join(
        x for x in (
            patient.get("address_line_1"),
            patient.get("address_line_2"),
            patient.get("city"),
            patient.get("state"),
            patient.get("zip_code"),
        ) if x
    )
    if address:
        lines.append(f"Address: {address}")

    if patient.get("insurance_provider"):
        lines.append(f"Insurance: {patient['insurance_provider']}")
        if patient.get("insurance_member_id"):
            lines.append(f"Member ID: {patient['insurance_member_id']}")

    if patient.get("emergency_contact_name"):
        lines.append(f"Emergency Contact: {patient['emergency_contact_name']}")
        if patient.get("emergency_contact_phone"):
            lines.append(f"Emergency Phone: {patient['emergency_contact_phone']}")

    return (
        "CALL DATA\n"
        f"caller_phone: {phone}\n"
        "lookup.found: true\n"
        f"patient_id: {patient.get('patient_id', '')}\n\n"
        "CURRENT RECORD\n" + ("\n".join(lines) or "(no fields on file)")
    )


@app.post("/incoming-call")
async def incoming_call(request: Request) -> PlainTextResponse:
    logger.debug("Incoming call received")
    form = await request.form()
    logger.debug(f"Form data: {dict(form)}")
    phone = str(form.get("From") or form.get("from") or "unknown")
    logger.debug(f"Caller phone: {phone}")

    if phone != "unknown":
        _pending_lookups[phone] = asyncio.create_task(patient_lookup(phone))

    host = request.headers.get("host", "localhost")
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    scheme = "wss" if proto == "https" else "ws"
    stream_url = f"{scheme}://{host}/ws"

    texml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f'<Say language="en-US" voice="Polly.Joanna">{GREETING_TEXT[0]}</Say>'
        '<Pause length="1"/>'
        f'<Say language="es-US" voice="Polly.Lupe">{GREETING_TEXT[1]}</Say>'
        '<Pause length="1"/>'
        f'<Say language="en-US" voice="Polly.Joanna">{GREETING_TEXT[2]}</Say>'
        '<Pause length="1"/>'
        f'<Say language="es-US" voice="Polly.Lupe">{GREETING_TEXT[3]}</Say>'
        '<Pause length="2"/>'
        f'<Say language="en-US" voice="Polly.Joanna">{GREETING_TEXT[4]}</Say>'
        "<Connect>"
        f'<Stream url="{stream_url}" bidirectionalMode="rtp">'
        f'<Parameter name="caller_phone" value="{phone}"/>'
        "</Stream>"
        "</Connect>"
        "</Response>"
    )
    
    return PlainTextResponse(texml, media_type="application/xml")

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    stream_sid = None
    caller_phone = "unknown"

    try:
        while True:
            message = await asyncio.wait_for(websocket.receive(), timeout=10)
            if message.get("type") == "websocket.disconnect":
                return

            raw = message.get("text")
            if not raw:
                continue

            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue

            logger.debug(f"WS event received: {event.get('event')}")

            if event.get("event") != "start":
                continue

            logger.debug(f"WS start payload: {event}")
            start = event.get("start", {})
            
            stream_sid = event.get("stream_id")
            call_control_id = start.get("call_control_id")
            custom_params = start.get("custom_parameters") or start.get("customParameters") or {}
            caller_phone = custom_params.get("caller_phone", "unknown")
            
            logger.debug(f"stream_sid={stream_sid} caller_phone={caller_phone}")
            break

        if not stream_sid:
            await websocket.close()
            return

        await run_pipeline(websocket, stream_sid, call_control_id, caller_phone)

    except WebSocketDisconnect:
        logger.debug("Telnyx disconnected: {}", stream_sid)
    except asyncio.TimeoutError:
        logger.error("Timed out waiting for Telnyx start event")
        await websocket.close()
    except Exception:
        logger.exception("WebSocket error: {}", stream_sid)
        await websocket.close()


async def run_pipeline(
    websocket: WebSocket,
    stream_sid: str,
    call_control_id: str,
    caller_phone: str,
) -> None:
    lookup_task = _pending_lookups.pop(caller_phone, None)
    if lookup_task is None:
        lookup_task = asyncio.create_task(patient_lookup(caller_phone))

    lookup = await lookup_task
    patient = lookup.get("patient", {}) if lookup.get("found") else {}

    session = {
        "is_returning": bool(lookup.get("found")),
        "patient_id": str(patient.get("patient_id", "")),
        "existing_record": patient,
        "otp": None,
        "otp_attempts": 0,
        "dob_attempts": 0,
    }

    context = LLMContext(tools=TOOLS)
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(context)

    transport = FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            add_wav_header=False,
            vad_analyzer=SileroVADAnalyzer(
                params=VADParams(
                    confidence=0.7,
                    start_secs=0.2,
                    stop_secs=0.8,
                    min_volume=0.05,
                )
            ),
            serializer=TelnyxFrameSerializer(
                stream_id=stream_sid,
                call_control_id=call_control_id,
                outbound_encoding="PCMU",
                inbound_encoding="PCMU",
                api_key=TELNYX_API_KEY,
                params=TelnyxFrameSerializer.InputParams(auto_hang_up=False),
            ),
        ),
    )

    stt = DeepgramSTTService(
        api_key=DEEPGRAM_API_KEY,
        encoding="linear16",
        sample_rate=8000,
    )

    tts = DeepgramTTSService(
        api_key=DEEPGRAM_API_KEY,
        encoding="linear16",
        sample_rate=8000,
    )
    
    llm = OpenAIResponsesLLMService(
        api_key=OPENAI_API_KEY,
        base_url=LLM_BASE_URL,
        settings=OpenAIResponsesLLMService.Settings(
            model=LLM_MODEL,
            system_instruction=SYSTEM_PROMPT,
        ),
    )

    task_ref: list[PipelineTask] = []

    async def verify_dob(params: FunctionCallParams) -> None:
        session["dob_attempts"] += 1
        attempts_left = max(0, DOB_MAX_ATTEMPTS - session["dob_attempts"])

        try:
            supplied = datetime.strptime(
                normalize_date(params.arguments.get("date_of_birth")),
                "%m/%d/%Y",
            ).date()
        except ValueError:
            await params.result_callback({
                "verified": False,
                "attempts_remaining": attempts_left,
            })
            return

        stored = str(session["existing_record"].get("date_of_birth", ""))
        stored_date = None
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y"):
            try:
                stored_date = datetime.strptime(stored, fmt).date()
                break
            except ValueError:
                pass

        await params.result_callback({
            "verified": stored_date == supplied,
            "attempts_remaining": attempts_left,
            **(
                {"first_name": session["existing_record"].get("first_name", "")}
                if stored_date == supplied
                else {}
            ),
        })

    async def submit_registration(params: FunctionCallParams) -> None:
        payload = dict(params.arguments)
        missing = [
            field for field in REQUIRED_FIELDS
            if not str(payload.get(field) or "").strip()
        ]

        if missing:
            await params.result_callback({
                "success": False,
                "status": "incomplete",
                "missing_fields": sorted(missing),
            })
            return

        payload["date_of_birth"] = normalize_date(payload["date_of_birth"])
        payload["state"] = normalize_state(payload["state"])

        required = {f: payload[f] for f in REQUIRED_FIELDS}
        required["phone_number"] = caller_phone

        optional = {
            f: payload[f]
            for f in ALLOWED_FIELDS - REQUIRED_FIELDS
            if payload.get(f) and str(payload[f]).strip()
        }

        try:
            if session["is_returning"]:
                patient_id = session["patient_id"]
                if not patient_id:
                    raise RuntimeError("Missing patient_id for returning patient")

                await update_patient(patient_id, **required, **optional)
                await params.result_callback({
                    "success": True,
                    "status": "complete",
                    "registration_complete": True,
                    "patient_id": patient_id,
                })
                return

            result = await save_demographics(**required, **optional)
            patient_id = result.get("record_id", "")
            session["patient_id"] = patient_id

            otp = "123456"
            session["otp"] = otp
            session["otp_attempts"] = 0

            # await send_otp_sms(otp=otp, caller_phone=caller_phone)

            await params.result_callback({
                "success": True,
                "status": "otp_sent",
                "registration_complete": True,
                "patient_id": patient_id,
            })
        except Exception as exc:
            logger.exception("submit_registration failed")
            await params.result_callback({
                "success": False,
                "status": "error",
                "error": str(exc),
            })

    async def verify_otp(params: FunctionCallParams) -> None:
        session["otp_attempts"] += 1
        attempts_left = max(0, OTP_MAX_ATTEMPTS - session["otp_attempts"])
        code = normalize_otp(str(params.arguments.get("code", "")))

        if code != session["otp"]:
            await params.result_callback({
                "status": "failed",
                "attempts_remaining": attempts_left,
            })
            return

        try:
            await mark_verified(session["patient_id"])
        except Exception as exc:
            logger.exception("mark_verified failed")
            await params.result_callback({
                "status": "error",
                "error": str(exc),
            })
            return

        await params.result_callback({"status": "verified"})

    async def end_call(params: FunctionCallParams) -> None:
        await params.result_callback({"status": "ended"})
        if task_ref:
            await task_ref[0].cancel()

    llm.register_function("verify_dob", verify_dob)
    llm.register_function("submit_registration", submit_registration)
    llm.register_function("verify_otp", verify_otp)
    llm.register_function("end_call", end_call)

    pipeline = Pipeline([
        transport.input(),
        stt,
        user_aggregator,
        llm,
        tts,
        transport.output(),
        assistant_aggregator,
    ])
    
    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            audio_in_sample_rate=8000,
            audio_out_sample_rate=8000,
            allow_interruptions=False,
            enable_metrics=True,
            enable_usage_metrics=True,
        ),
        enable_rtvi=False,
    )
    task_ref.append(task)

    context.add_message({
        "role": "developer",
        "content": lookup_context(lookup, caller_phone),
    })

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client) -> None:
        logger.info("on_client_connected fired: caller={}", caller_phone)
        opening = (
            "Hi, thanks for waiting. I'm Emma. "
            "To verify your identity, could you please share your date of birth?"
            if session["is_returning"]
            else
            "Hi, thanks for waiting. I'm Emma. "
            "I can help you register as a new patient today. "
            "Do you prefer to continue in English or Spanish?"
        )
        context.add_message({"role": "assistant", "content": opening})
        await task.queue_frames([
            TTSSpeakFrame(opening, append_to_context=False)
        ])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client) -> None:
        logger.info("on_client_disconnected fired: caller={}", caller_phone)
        await task.cancel()

    logger.info("Starting pipeline: stream_sid={}", stream_sid)
    try:
        await PipelineRunner(handle_sigint=False).run(task)
    except Exception:
        logger.exception("Pipeline error: stream_sid={}", stream_sid)
        raise
    finally:
        logger.info("Pipeline finished: stream_sid={}", stream_sid)


if __name__ == "__main__":
    uvicorn.run("bot:app", host="0.0.0.0", port=PORT, reload=False)
