"""
main.py - the FastAPI application. START HERE when reading the backend.

Run it:   uvicorn backend.main:app --reload --port 8000
Docs:     http://localhost:8000/docs   (interactive - you can try every endpoint!)

What happens at start-up (the `lifespan` function):
  1. create all database tables (if they don't exist yet)
  2. load demo data when the database is empty
Then every router (a group of URLs) is plugged into the app.
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.config import settings
from backend.routers import admin, ai, auth, consent, patients, prescriptions, timeline
from backend.seed import seed_if_empty


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.AUTO_SEED:
        seed_if_empty()
    else:
        from backend.database import Base, engine
        Base.metadata.create_all(bind=engine)
    yield  # app runs here


app = FastAPI(
    title="HealthBridge AI API",
    description="Consent-based healthcare interoperability layer: clinics, pharmacies and patients "
                "in one connected timeline. SYNTHETIC DATA ONLY - not for clinical use.",
    version="3.0.0",
    lifespan=lifespan,
)

# CORS lets a browser-based frontend on another address call this API.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

for r in (auth, patients, prescriptions, timeline, consent, admin, ai):
    app.include_router(r.router)


@app.get("/health", tags=["system"])
def health():
    """Used by the frontend (and hosting platforms) to check the API is alive."""
    return {"status": "ok", "ai": "grok" if settings.ai_available else "rule-based fallback"}
