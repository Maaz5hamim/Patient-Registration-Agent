from contextlib import asynccontextmanager

from dotenv import load_dotenv
load_dotenv()  # picks up .env from the project root before any os.getenv() calls

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pymongo import ASCENDING, DESCENDING
from starlette.exceptions import HTTPException as StarletteHTTPException

from .database import close_db, connect_db, get_db
from .models import PATIENTS_COLLECTION
from .routers import demographics


@asynccontextmanager
async def lifespan(app: FastAPI):
    await connect_db()

    # Ensure indexes exist (idempotent)
    col = get_db()[PATIENTS_COLLECTION]
    await col.create_index([("last_name", ASCENDING)])
    await col.create_index([("phone_number", ASCENDING)])
    await col.create_index([("date_of_birth", ASCENDING)])
    await col.create_index([("created_at", DESCENDING)])
    await col.create_index([("deleted_at", ASCENDING)])

    yield

    await close_db()


app = FastAPI(
    title="Patient Intake API",
    description="REST API for storing and managing patient demographic records.",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(demographics.router)


# ── Consistent error envelope for all responses ───────────────────────────────

@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return JSONResponse(
        content={"data": None, "error": exc.detail},
        status_code=exc.status_code,
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = [
        f"{' → '.join(str(loc) for loc in e['loc'])}: {e['msg']}"
        for e in exc.errors()
    ]
    return JSONResponse(
        content={"data": None, "error": "; ".join(errors)},
        status_code=422,
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        content={"data": None, "error": "Internal server error"},
        status_code=500,
    )


@app.get("/health", tags=["ops"])
async def health() -> dict:
    return {"data": {"status": "ok"}, "error": None}
