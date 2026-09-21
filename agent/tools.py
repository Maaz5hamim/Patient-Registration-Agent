import os
import re
from datetime import datetime
from typing import Optional

import httpx
from loguru import logger

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")
BACKEND_API_KEY = os.getenv("BACKEND_API_KEY", "")

# ── Normalisation helpers ─────────────────────────────────────────────────────

_WORD_DIGITS = {
    "zero": "0", "oh": "0", "one": "1", "won": "1",
    "two": "2", "to": "2", "too": "2", "three": "3",
    "four": "4", "for": "4", "fore": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "ate": "8",
    "nine": "9", "nein": "9",
}


def normalize_otp(value: str) -> str:
    """Normalize spoken or numeric OTP input to a six-digit string."""
    digits = re.sub(r"\D", "", value)
    if len(digits) == 6:
        return digits
    result = ""
    for token in value.lower().split():
        token = re.sub(r"[^a-z]", "", token)
        result += _WORD_DIGITS.get(token, "")
    return result if len(result) == 6 else digits


def normalize_date(value: object) -> str:
    """Normalize common date formats to MM/DD/YYYY."""
    value = str(value or "").strip()
    for fmt in (
        "%m/%d/%Y", "%m-%d-%Y", "%Y-%m-%d",
        "%B %d %Y", "%B %d, %Y", "%b %d %Y", "%b %d, %Y",
    ):
        try:
            return datetime.strptime(value, fmt).strftime("%m/%d/%Y")
        except ValueError:
            pass
    return value


def normalize_state(value: object) -> str:
    """Normalize a full state name or abbreviation to a two-letter code."""
    _STATES = {
        "alabama": "AL", "alaska": "AK", "arizona": "AZ",
        "arkansas": "AR", "california": "CA", "colorado": "CO",
        "connecticut": "CT", "delaware": "DE", "florida": "FL",
        "georgia": "GA", "hawaii": "HI", "idaho": "ID",
        "illinois": "IL", "indiana": "IN", "iowa": "IA",
        "kansas": "KS", "kentucky": "KY", "louisiana": "LA",
        "maine": "ME", "maryland": "MD", "massachusetts": "MA",
        "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
        "missouri": "MO", "montana": "MT", "nebraska": "NE",
        "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ",
        "new mexico": "NM", "new york": "NY", "north carolina": "NC",
        "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
        "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI",
        "south carolina": "SC", "south dakota": "SD", "tennessee": "TN",
        "texas": "TX", "utah": "UT", "vermont": "VT", "virginia": "VA",
        "washington": "WA", "west virginia": "WV", "wisconsin": "WI",
        "wyoming": "WY",
    }
    value = str(value or "").strip()
    return _STATES.get(value.lower(), value.upper())


def _normalize_phone(phone_number: str) -> str:
    """Strip non-digits and remove leading country code 1."""
    digits = re.sub(r"\D", "", phone_number)
    if digits.startswith("1") and len(digits) == 11:
        digits = digits[1:]
    return digits


async def lookup_patient_by_phone(phone_number: str) -> dict:
    """Return the most recent patient record matching the given phone number, or found=false."""
    normalized = _normalize_phone(phone_number)

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{BACKEND_URL}/patients/",
            params={"phone_number": normalized},
            headers={"X-API-Key": BACKEND_API_KEY},
        )
        resp.raise_for_status()
        data = resp.json()

    patients = data.get("data", [])
    if patients:
        patient = patients[0]
        logger.info("Patient found: patient_id={} phone={}", patient.get("patient_id"), normalized)
        return {"found": True, "patient": patient}

    logger.info("No patient found for phone={}", normalized)
    return {"found": False}


async def save_demographics(
    first_name: str,
    last_name: str,
    date_of_birth: str,
    sex: str,
    address_line_1: str,
    city: str,
    state: str,
    zip_code: str,
    phone_number: str,
    # Optional fields
    email: Optional[str] = None,
    address_line_2: Optional[str] = None,
    insurance_provider: Optional[str] = None,
    insurance_member_id: Optional[str] = None,
    preferred_language: str = "English",
    emergency_contact_name: Optional[str] = None,
    emergency_contact_phone: Optional[str] = None,
) -> dict:
    payload: dict = {
        "first_name": first_name,
        "last_name": last_name,
        "date_of_birth": date_of_birth,
        "sex": sex,
        "phone_number": phone_number,
        "address_line_1": address_line_1,
        "city": city,
        "state": state,
        "zip_code": zip_code,
        "preferred_language": preferred_language,
    }
    for key, value in {
        "email": email,
        "address_line_2": address_line_2,
        "insurance_provider": insurance_provider,
        "insurance_member_id": insurance_member_id,
        "emergency_contact_name": emergency_contact_name,
        "emergency_contact_phone": emergency_contact_phone,
    }.items():
        if value is not None:
            payload[key] = value

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            f"{BACKEND_URL}/patients/",
            json=payload,
            headers={"X-API-Key": BACKEND_API_KEY},
        )
        resp.raise_for_status()
        data = resp.json()

    patient_id = str(data["data"]["patient_id"])
    logger.info("Demographics saved: patient_id=%s phone=%s", patient_id, phone_number)
    return {
        "status": "saved",
        "record_id": patient_id,
        "message": f"Record saved for {first_name} {last_name}.",
    }


async def update_patient(patient_id: str, **fields) -> dict:
    """Partially update an existing patient record. Only fields explicitly passed are changed."""
    delta = {k: v for k, v in fields.items() if v is not None}

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.put(
            f"{BACKEND_URL}/patients/{patient_id}",
            json=delta,
            headers={"X-API-Key": BACKEND_API_KEY},
        )
        resp.raise_for_status()

    logger.info("Patient updated: patient_id=%s fields=%s", patient_id, list(delta.keys()))
    return {"status": "updated", "message": "Record updated successfully."}


async def send_otp_sms(otp: str, caller_phone: str) -> dict:
    """Send a one-time passcode to the caller's phone for identity verification."""
    api_key = os.getenv("TELNYX_API_KEY", "")
    from_number = os.getenv("TELNYX_PHONE_NUMBER", "")

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            "https://api.telnyx.com/v2/messages",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "from": from_number,
                "to": caller_phone if caller_phone.startswith("+") else f"+1{caller_phone}",
                "text": f"Your patient registration verification code is: {otp}. Please say this code to the agent to complete your registration.",
            },
        )
        resp.raise_for_status()
        data = resp.json()

    message_id = data["data"]["id"]
    logger.info("OTP SMS sent: id=%s to=%s otp=%s", message_id, caller_phone, otp)
    return {"status": "sent", "message_id": message_id}


async def mark_verified(record_id: str) -> dict:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.patch(
            f"{BACKEND_URL}/patients/{record_id}/verify",
            json={"verified_status": True},
            headers={"X-API-Key": BACKEND_API_KEY},
        )
        resp.raise_for_status()

    logger.info("Record verified: patient_id=%s", record_id)
    return {"status": "verified", "message": "Record marked as verified."}
