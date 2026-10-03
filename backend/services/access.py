"""
access.py - "WHO may see WHICH patient?"  (privacy + consent logic)

Rules in plain English:
  * patient  -> only their own record
  * doctor   -> a patient if:
        (a) the patient was registered by / treated at the doctor's clinic, OR
        (b) the patient gave this doctor CONSENT (not expired, not revoked), OR
        (c) the doctor used "break-glass" emergency access (logged + patient is told)
  * pharmacist -> NOT the full record. Only prescriptions routed to their pharmacy.
  * admin    -> no clinical records at all (only system admin pages)

Consent scope narrows what a consented doctor sees:
  all           -> everything
  prescriptions -> prescriptions + dispensing only
  labs          -> lab results only
"""
from typing import Optional, Set

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from backend.database import utcnow
from backend.models import Consent, Encounter, Patient, Prescription, User

ALL_TYPES = {"consultation", "prescription", "dispensing", "lab", "history"}
SCOPE_TYPES = {
    "all": ALL_TYPES,
    "prescriptions": {"prescription", "dispensing"},
    "labs": {"lab"},
}


def active_consents(db: Session, patient_id: int, user_id: int):
    now = utcnow()
    return (
        db.query(Consent)
        .filter(
            Consent.patient_id == patient_id,
            Consent.grantee_user_id == user_id,
            Consent.status == "active",
            or_(Consent.expires_at.is_(None), Consent.expires_at > now),
        )
        .all()
    )


def allowed_event_types(db: Session, user: User, patient_id: int) -> Optional[Set[str]]:
    """
    Which timeline event types may this user see for this patient?
    Returns a set (maybe empty). Empty set = no access at all.
    """
    if user.role == "patient":
        return set(ALL_TYPES) if user.patient_id == patient_id else set()

    if user.role == "doctor":
        patient = db.get(Patient, patient_id)
        if patient is None:
            return set()
        # (a) same clinic: registered there, or any consultation done there
        if user.organization_id is not None:
            if patient.created_by_org_id == user.organization_id:
                return set(ALL_TYPES)
            treated = (
                db.query(Encounter.id)
                .filter(Encounter.patient_id == patient_id, Encounter.organization_id == user.organization_id)
                .first()
            )
            if treated:
                return set(ALL_TYPES)
        # (b)/(c) consent rows
        allowed: Set[str] = set()
        for c in active_consents(db, patient_id, user.id):
            allowed |= SCOPE_TYPES.get(c.scope, set())
        return allowed

    if user.role == "pharmacist":
        # pharmacists only see what is attached to prescriptions sent to them
        return {"prescription", "dispensing"} if pharmacist_has_rx(db, user, patient_id) else set()

    return set()


def pharmacist_has_rx(db: Session, user: User, patient_id: int) -> bool:
    return (
        db.query(Prescription.id)
        .filter(Prescription.patient_id == patient_id, Prescription.pharmacy_id == user.organization_id)
        .first()
        is not None
    )


def can_view_patient(db: Session, user: User, patient_id: int) -> bool:
    return bool(allowed_event_types(db, user, patient_id))


def has_full_access(db: Session, user: User, patient_id: int) -> bool:
    return allowed_event_types(db, user, patient_id) == ALL_TYPES


def require_patient_access(db: Session, user: User, patient_id: int, full: bool = False) -> Patient:
    """Raise 404/403 unless the user may see this patient. Returns the Patient row."""
    patient = db.get(Patient, patient_id)
    if patient is None:
        raise HTTPException(404, "Patient not found")
    ok = has_full_access(db, user, patient_id) if full else can_view_patient(db, user, patient_id)
    if not ok:
        raise HTTPException(
            403, "No access to this patient. Ask the patient for consent (or use emergency access)."
        )
    return patient
