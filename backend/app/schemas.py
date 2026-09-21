import re
import uuid
from datetime import date, datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, EmailStr, field_validator, model_validator

# ── Constants ────────────────────────────────────────────────────────────────

US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
    "DC", "AS", "GU", "MP", "PR", "VI",
}

SexChoice = Literal["Male", "Female", "Other", "Decline to Answer"]

# ── Shared validation helpers ─────────────────────────────────────────────────

def normalize_phone(v: str | None) -> str | None:
    if v is None:
        return None
    digits = re.sub(r"\D", "", v)
    if digits.startswith("1") and len(digits) == 11:
        digits = digits[1:]
    if len(digits) != 10:
        raise ValueError("Must be a valid U.S. 10-digit phone number")
    return digits


def parse_dob(v) -> date:
    if isinstance(v, date):
        return v
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"):
        try:
            return datetime.strptime(str(v), fmt).date()
        except ValueError:
            continue
    raise ValueError("date_of_birth must be in MM/DD/YYYY format")


# ── Response envelope ─────────────────────────────────────────────────────────

class APIResponse(BaseModel):
    data: Any = None
    error: Optional[str] = None


# ── Create schema ─────────────────────────────────────────────────────────────

class PatientCreate(BaseModel):
    first_name: str
    last_name: str
    date_of_birth: date
    sex: SexChoice
    phone_number: str
    email: Optional[EmailStr] = None
    address_line_1: str
    address_line_2: Optional[str] = None
    city: str
    state: str
    zip_code: str
    insurance_provider: Optional[str] = None
    insurance_member_id: Optional[str] = None
    preferred_language: str = "English"
    emergency_contact_name: Optional[str] = None
    emergency_contact_phone: Optional[str] = None

    @field_validator("first_name", "last_name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        v = v.strip()
        if not 1 <= len(v) <= 50:
            raise ValueError("Must be 1–50 characters")
        if not re.match(r"^[A-Za-z\-']+$", v):
            raise ValueError("Only letters, hyphens, and apostrophes are allowed")
        return v

    @field_validator("date_of_birth", mode="before")
    @classmethod
    def parse_date(cls, v) -> date:
        return parse_dob(v)

    @model_validator(mode="after")
    def dob_not_in_future(self) -> "PatientCreate":
        if self.date_of_birth > date.today():
            raise ValueError("date_of_birth cannot be in the future")
        return self

    @field_validator("phone_number")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        result = normalize_phone(v)
        if result is None:
            raise ValueError("phone_number is required")
        return result

    @field_validator("state")
    @classmethod
    def validate_state(cls, v: str) -> str:
        v = v.upper().strip()
        if v not in US_STATES:
            raise ValueError(f"'{v}' is not a valid 2-letter U.S. state abbreviation")
        return v

    @field_validator("zip_code")
    @classmethod
    def validate_zip(cls, v: str) -> str:
        v = v.strip()
        if not re.match(r"^\d{5}(-\d{4})?$", v):
            raise ValueError("Must be 5-digit or ZIP+4 format (e.g. 12345 or 12345-6789)")
        return v

    @field_validator("city")
    @classmethod
    def validate_city(cls, v: str) -> str:
        v = v.strip()
        if not 1 <= len(v) <= 100:
            raise ValueError("Must be 1–100 characters")
        return v

    @field_validator("emergency_contact_phone")
    @classmethod
    def validate_ec_phone(cls, v: str | None) -> str | None:
        return normalize_phone(v)

    @field_validator("insurance_member_id")
    @classmethod
    def validate_member_id(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not re.match(r"^[A-Za-z0-9]+$", v):
            raise ValueError("Must be alphanumeric")
        return v


# ── Partial update schema (PUT) ───────────────────────────────────────────────

class PatientUpdate(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    date_of_birth: Optional[date] = None
    sex: Optional[SexChoice] = None
    phone_number: Optional[str] = None
    email: Optional[EmailStr] = None
    address_line_1: Optional[str] = None
    address_line_2: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    insurance_provider: Optional[str] = None
    insurance_member_id: Optional[str] = None
    preferred_language: Optional[str] = None
    emergency_contact_name: Optional[str] = None
    emergency_contact_phone: Optional[str] = None
    verified_status: Optional[bool] = None

    @field_validator("first_name", "last_name")
    @classmethod
    def validate_name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not 1 <= len(v) <= 50:
            raise ValueError("Must be 1–50 characters")
        if not re.match(r"^[A-Za-z\-']+$", v):
            raise ValueError("Only letters, hyphens, and apostrophes are allowed")
        return v

    @field_validator("date_of_birth", mode="before")
    @classmethod
    def parse_date(cls, v) -> date | None:
        return None if v is None else parse_dob(v)

    @field_validator("phone_number")
    @classmethod
    def validate_phone(cls, v: str | None) -> str | None:
        return normalize_phone(v)

    @field_validator("state")
    @classmethod
    def validate_state(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.upper().strip()
        if v not in US_STATES:
            raise ValueError(f"'{v}' is not a valid 2-letter U.S. state abbreviation")
        return v

    @field_validator("zip_code")
    @classmethod
    def validate_zip(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not re.match(r"^\d{5}(-\d{4})?$", v):
            raise ValueError("Must be 5-digit or ZIP+4 format")
        return v

    @field_validator("emergency_contact_phone")
    @classmethod
    def validate_ec_phone(cls, v: str | None) -> str | None:
        return normalize_phone(v)


# ── Verify-only schema (internal, called by the intake agent) ─────────────────

class PatientVerify(BaseModel):
    verified_status: bool


# ── Response schema ───────────────────────────────────────────────────────────

class PatientResponse(BaseModel):
    patient_id: uuid.UUID
    first_name: str
    last_name: str
    date_of_birth: date
    sex: str
    phone_number: str
    email: Optional[str]
    address_line_1: str
    address_line_2: Optional[str]
    city: str
    state: str
    zip_code: str
    insurance_provider: Optional[str]
    insurance_member_id: Optional[str]
    preferred_language: str
    emergency_contact_name: Optional[str]
    emergency_contact_phone: Optional[str]
    verified_status: bool
    created_at: datetime
    updated_at: datetime
