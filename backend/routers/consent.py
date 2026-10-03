"""
routers/consent.py - the patient controls who sees their record.

  * Patient grants access to a doctor (scope + number of days) and can revoke it.
  * Doctor in an emergency can use BREAK-GLASS: instant, time-limited access
    that requires a written reason, is clearly labelled in the audit log and
    immediately notifies the patient.
  * Patient can see an "access history": who looked at their data and when.
"""
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend import audit
from backend.config import settings
from backend.database import get_db, utcnow
from backend.models import AuditEvent, Consent, Patient, User
from backend.schemas import BreakGlass, ConsentCreate
from backend.security import require_roles
from backend.serializers import consent_out

router = APIRouter(tags=["consent"])


@router.get("/consents/mine")
def my_consents(db: Session = Depends(get_db), patient_user: User = Depends(require_roles("patient"))):
    rows = db.query(Consent).filter(Consent.patient_id == patient_user.patient_id) \
        .order_by(Consent.created_at.desc()).all()
    return [consent_out(c) for c in rows]


@router.post("/consents", status_code=201)
def grant(body: ConsentCreate, db: Session = Depends(get_db),
          patient_user: User = Depends(require_roles("patient"))):
    doctor = db.get(User, body.grantee_user_id)
    if doctor is None or doctor.role != "doctor":
        raise HTTPException(422, "Please choose a valid doctor")
    consent = Consent(patient_id=patient_user.patient_id, grantee_user_id=doctor.id, scope=body.scope,
                      kind="patient_granted", status="active",
                      expires_at=utcnow() + timedelta(days=body.days))
    db.add(consent)
    db.flush()
    audit.log(db, patient_user, "consent.grant", "consent", consent.id, patient_user.patient_id,
              f"to {doctor.full_name}, scope={body.scope}, {body.days} days")
    audit.notify(db, doctor.id, "Access granted", f"{patient_user.full_name} shared their record with you ({body.scope}).")
    db.commit()
    return consent_out(consent)


@router.delete("/consents/{consent_id}")
def revoke(consent_id: int, db: Session = Depends(get_db),
           patient_user: User = Depends(require_roles("patient"))):
    c = db.get(Consent, consent_id)
    if c is None or c.patient_id != patient_user.patient_id:
        raise HTTPException(404, "Consent not found")
    c.status = "revoked"
    audit.log(db, patient_user, "consent.revoke", "consent", c.id, c.patient_id, f"from {c.grantee.full_name}")
    db.commit()
    return consent_out(c)


@router.post("/consents/break-glass", status_code=201)
def break_glass(body: BreakGlass, db: Session = Depends(get_db),
                doctor: User = Depends(require_roles("doctor"))):
    patient = db.get(Patient, body.patient_id)
    if patient is None:
        raise HTTPException(404, "Patient not found")
    c = Consent(patient_id=patient.id, grantee_user_id=doctor.id, scope="all", kind="break_glass",
                reason=body.reason, status="active",
                expires_at=utcnow() + timedelta(hours=settings.BREAK_GLASS_HOURS))
    db.add(c)
    db.flush()
    audit.log(db, doctor, "consent.break_glass", "consent", c.id, patient.id, f"EMERGENCY ACCESS: {body.reason}")
    patient_user = db.query(User).filter(User.role == "patient", User.patient_id == patient.id).first()
    if patient_user:
        audit.notify(db, patient_user.id, "Emergency access used",
                     f"{doctor.full_name} accessed your record in an emergency. Reason: {body.reason}")
    db.commit()
    return consent_out(c)


@router.get("/patients/{patient_id}/access-log")
def access_log(patient_id: int, db: Session = Depends(get_db),
               patient_user: User = Depends(require_roles("patient"))):
    """Who accessed MY data? Only the patient themselves can read this."""
    if patient_user.patient_id != patient_id:
        raise HTTPException(403, "You can only view your own access history")
    rows = db.query(AuditEvent).filter(AuditEvent.patient_id == patient_id) \
        .order_by(AuditEvent.timestamp.desc()).limit(200).all()
    return [{"timestamp": r.timestamp.isoformat(), "actor": r.actor_name, "role": r.actor_role,
             "action": r.action, "detail": r.detail} for r in rows]
