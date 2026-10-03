"""
routers/ai.py - standalone AI helpers.

  GET  /ai/status          -> is Grok configured? (UI shows a badge)
  POST /ai/structure-note  -> Record Agent: tidy a doctor's free-text note
"""
from fastapi import APIRouter, Depends

from backend import audit
from backend.agents import orchestrator
from backend.config import settings
from backend.database import get_db
from backend.models import User
from backend.schemas import NoteText
from backend.security import get_current_user, require_roles
from sqlalchemy.orm import Session

router = APIRouter(tags=["ai"])


@router.get("/ai/status")
def ai_status(user: User = Depends(get_current_user)):
    return {"ai_available": settings.ai_available, "model": settings.XAI_MODEL,
            "mode": "Grok via CrewAI" if settings.ai_available else "rule-based fallback"}


@router.post("/ai/structure-note")
def structure_note(body: NoteText, db: Session = Depends(get_db),
                   doctor: User = Depends(require_roles("doctor"))):
    result = orchestrator.structure_note(body.text)
    audit.log(db, doctor, "ai.structure_note", "note", "", None, result["generated_by"])
    db.commit()
    return result
