"""
routers/timeline.py - the connected patient timeline and everything built on it:
timeline, current medications, AI briefing, safety alerts, FHIR export.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend import audit
from backend.agents import orchestrator
from backend.agents.llm import deidentify_events, deidentify_patient
from backend.database import get_db, utcnow
from backend.models import AISummary, ConflictAlert, LabResult, Patient, User
from backend.schemas import AlertReview
from backend.security import get_current_user, require_roles
from backend.serializers import alert_out, lab_out
from backend.services import access
from backend.services.fhir import patient_bundle
from backend.services.timeline_service import active_medications, build_timeline

router = APIRouter(tags=["timeline"])


def _jsonable(events):
    return [{**e, "date": e["date"].isoformat()} for e in events]


@router.get("/patients/{patient_id}/timeline")
def timeline(patient_id: int, types: Optional[str] = Query(None, description="comma list: consultation,prescription,..."),
             db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    patient = access.require_patient_access(db, user, patient_id)
    allowed = access.allowed_event_types(db, user, patient_id)
    if types:
        allowed = allowed & {t.strip() for t in types.split(",")}
    events = build_timeline(db, patient_id, allowed)
    audit.log(db, user, "timeline.view", "patient", patient.id, patient.id, f"{len(events)} events")
    db.commit()
    return {"patient_id": patient_id, "events": _jsonable(events),
            "visible_types": sorted(access.allowed_event_types(db, user, patient_id))}


@router.get("/patients/{patient_id}/medications")
def medications(patient_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    access.require_patient_access(db, user, patient_id)
    allowed = access.allowed_event_types(db, user, patient_id)
    if "prescription" not in allowed:
        return []
    return active_medications(db, patient_id)


@router.get("/patients/{patient_id}/labs")
def labs(patient_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    access.require_patient_access(db, user, patient_id)
    if "lab" not in access.allowed_event_types(db, user, patient_id):
        return []
    rows = db.query(LabResult).filter(LabResult.patient_id == patient_id).order_by(LabResult.date).all()
    return [lab_out(r) for r in rows]


# ---------------------------------------------------------------------- AI --
@router.post("/patients/{patient_id}/ai/summary")
def ai_summary(patient_id: int, db: Session = Depends(get_db),
               user: User = Depends(require_roles("doctor", "patient"))):
    """
    Timeline Agent briefing. Only uses events THIS user is allowed to see,
    sends a de-identified copy to the AI, and verifies every cited source ID.
    """
    patient = access.require_patient_access(db, user, patient_id)
    allowed = access.allowed_event_types(db, user, patient_id)
    events = build_timeline(db, patient_id, allowed)
    meds = active_medications(db, patient_id) if "prescription" in allowed else []
    alerts = [alert_out(a) for a in db.query(ConflictAlert).filter(
        ConflictAlert.patient_id == patient_id, ConflictAlert.status == "open").all()]

    result = orchestrator.summarize_timeline(
        deidentify_patient(patient), deidentify_events(events), meds, alerts)

    db.add(AISummary(patient_id=patient_id, requested_by=user.full_name,
                     content=result["data"], generated_by=result["generated_by"]))
    audit.log(db, user, "ai.timeline_summary", "patient", patient.id, patient.id, result["generated_by"])
    db.commit()
    # Attach a lookup so the UI can show the source record behind every citation
    return {**result, "sources": {e["event_id"]: {"title": e["title"], "date": e["date"].isoformat(),
                                                  "type": e["type"]} for e in events}}


# ------------------------------------------------------------------ alerts --
@router.get("/patients/{patient_id}/alerts")
def patient_alerts(patient_id: int, db: Session = Depends(get_db),
                   user: User = Depends(require_roles("doctor", "patient"))):
    access.require_patient_access(db, user, patient_id, full=True)
    rows = db.query(ConflictAlert).filter(ConflictAlert.patient_id == patient_id) \
        .order_by(ConflictAlert.created_at.desc()).all()
    return [alert_out(a) for a in rows]


@router.post("/alerts/{alert_id}/review")
def review_alert(alert_id: int, body: AlertReview, db: Session = Depends(get_db),
                 doctor: User = Depends(require_roles("doctor"))):
    """A HUMAN clinician reviews a flag. (Agents are never allowed to do this.)"""
    alert = db.get(ConflictAlert, alert_id)
    if alert is None:
        raise HTTPException(404, "Alert not found")
    access.require_patient_access(db, doctor, alert.patient_id, full=True)
    alert.status = body.status
    alert.review_note = body.note
    alert.reviewed_by = doctor.full_name
    alert.reviewed_at = utcnow()
    audit.log(db, doctor, f"alert.{body.status}", "alert", alert.id, alert.patient_id, body.note)
    db.commit()
    return alert_out(alert)


@router.get("/alerts")
def open_alerts(db: Session = Depends(get_db), doctor: User = Depends(require_roles("doctor"))):
    """All open alerts across the doctor's accessible patients (the 'review queue')."""
    out = []
    for a in db.query(ConflictAlert).filter(ConflictAlert.status == "open") \
            .order_by(ConflictAlert.created_at.desc()).all():
        if access.has_full_access(db, doctor, a.patient_id):
            p = db.get(Patient, a.patient_id)
            out.append({**alert_out(a), "patient_name": p.full_name, "patient_hb_id": p.hb_id})
    return out


# -------------------------------------------------------------------- FHIR --
@router.get("/patients/{patient_id}/fhir")
def fhir_export(patient_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    patient = access.require_patient_access(db, user, patient_id)
    allowed = access.allowed_event_types(db, user, patient_id)
    audit.log(db, user, "fhir.export", "patient", patient.id, patient.id)
    db.commit()
    return patient_bundle(db, patient, allowed)
