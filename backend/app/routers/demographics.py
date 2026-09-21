import uuid
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from ..auth import verify_api_key
from ..database import get_db
from ..models import PATIENTS_COLLECTION
from ..schemas import (
    PatientCreate,
    PatientResponse,
    PatientUpdate,
    PatientVerify,
    normalize_phone,
    parse_dob,
)

router = APIRouter(prefix="/patients", tags=["patients"])


# ── Helpers ───────────────────────────────────────────────────────────────────

def _col():
    return get_db()[PATIENTS_COLLECTION]


def _ok(data, status_code: int = 200) -> JSONResponse:
    return JSONResponse(
        content={"data": jsonable_encoder(data), "error": None},
        status_code=status_code,
    )


def _err(detail: str, status_code: int) -> JSONResponse:
    return JSONResponse(
        content={"data": None, "error": detail},
        status_code=status_code,
    )


def _to_response(doc: dict) -> PatientResponse:
    """Map a MongoDB document to a PatientResponse (remaps _id → patient_id)."""
    d = dict(doc)
    d["patient_id"] = str(d.pop("_id"))
    return PatientResponse.model_validate(d)


def _prepare_insert(payload: PatientCreate) -> dict:
    """Build the document dict for insertion, serialising non-BSON-native types."""
    data = payload.model_dump()
    # date → ISO string ("YYYY-MM-DD"); BSON has no native date-only type
    if isinstance(data.get("date_of_birth"), date):
        data["date_of_birth"] = data["date_of_birth"].isoformat()
    now = datetime.utcnow()
    return {
        "_id": str(uuid.uuid4()),
        **data,
        "verified_status": False,
        "created_at": now,
        "updated_at": now,
        "deleted_at": None,
    }


def _prepare_update(payload: PatientUpdate) -> dict:
    """Return only the fields that were explicitly provided."""
    delta = payload.model_dump(exclude_unset=True)
    if "date_of_birth" in delta and delta["date_of_birth"] is not None:
        delta["date_of_birth"] = delta["date_of_birth"].isoformat()
    delta["updated_at"] = datetime.utcnow()
    return {"$set": delta}


# ── GET /patients ─────────────────────────────────────────────────────────────

@router.get("/", summary="List patients", dependencies=[Depends(verify_api_key)])
async def list_patients(
    last_name: Optional[str] = Query(None, description="Partial, case-insensitive match"),
    date_of_birth: Optional[str] = Query(None, description="Exact match, MM/DD/YYYY"),
    phone_number: Optional[str] = Query(None, description="Exact match, any U.S. format"),
) -> JSONResponse:
    filt: dict = {"deleted_at": None}

    if last_name:
        filt["last_name"] = {"$regex": last_name, "$options": "i"}

    if date_of_birth:
        try:
            filt["date_of_birth"] = parse_dob(date_of_birth).isoformat()
        except ValueError as exc:
            return _err(str(exc), 400)

    if phone_number:
        try:
            filt["phone_number"] = normalize_phone(phone_number)
        except ValueError as exc:
            return _err(str(exc), 400)

    docs = await _col().find(filt).sort("created_at", -1).to_list(None)
    return _ok([_to_response(d) for d in docs])


# ── GET /patients/:id ─────────────────────────────────────────────────────────

@router.get("/{patient_id}", summary="Get a patient by ID", dependencies=[Depends(verify_api_key)])
async def get_patient(patient_id: str) -> JSONResponse:
    doc = await _col().find_one({"_id": patient_id, "deleted_at": None})
    if doc is None:
        raise HTTPException(status_code=404, detail="Patient not found")
    return _ok(_to_response(doc))


# ── POST /patients ────────────────────────────────────────────────────────────

@router.post("/", summary="Create a new patient", status_code=201, dependencies=[Depends(verify_api_key)])
async def create_patient(payload: PatientCreate) -> JSONResponse:
    doc = _prepare_insert(payload)
    await _col().insert_one(doc)
    return _ok(_to_response(doc), status_code=201)


# ── PUT /patients/:id ─────────────────────────────────────────────────────────

@router.put("/{patient_id}", summary="Update a patient (partial update allowed)", dependencies=[Depends(verify_api_key)])
async def update_patient(patient_id: str, payload: PatientUpdate) -> JSONResponse:
    result = await _col().find_one_and_update(
        {"_id": patient_id, "deleted_at": None},
        _prepare_update(payload),
        return_document=True,  # return the updated document
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Patient not found")
    return _ok(_to_response(result))


# ── DELETE /patients/:id (soft-delete) ───────────────────────────────────────

@router.delete("/{patient_id}", summary="Soft-delete a patient (sets deleted_at)", dependencies=[Depends(verify_api_key)])
async def delete_patient(patient_id: str) -> JSONResponse:
    deleted_at = datetime.utcnow()
    result = await _col().find_one_and_update(
        {"_id": patient_id, "deleted_at": None},
        {"$set": {"deleted_at": deleted_at, "updated_at": deleted_at}},
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Patient not found")
    return _ok({"patient_id": patient_id, "deleted": True, "deleted_at": deleted_at.isoformat()})


# ── PATCH /patients/:id/verify (internal — called by the intake agent) ────────

@router.patch("/{patient_id}/verify", summary="Mark a patient as verified", dependencies=[Depends(verify_api_key)])
async def verify_patient(patient_id: str, payload: PatientVerify) -> JSONResponse:
    now = datetime.utcnow()
    result = await _col().find_one_and_update(
        {"_id": patient_id, "deleted_at": None},
        {"$set": {"verified_status": payload.verified_status, "updated_at": now}},
        return_document=True,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Patient not found")
    return _ok(_to_response(result))
