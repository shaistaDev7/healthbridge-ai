"""
routers/patients.py - patient registry, consultations, labs, past history.

Every endpoint that touches a patient calls `require_patient_access(...)`
first. That single function enforces consent + roles (see services/access.py).
"""
import secrets

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session

from backend import audit
from backend.database import get_db, utcnow
from backend.models import Encounter, LabResult, Patient, User
from backend.schemas import EncounterCreate, HistoryEntry, LabCreate, PatientCreate, PatientUpdate
from backend.security import get_current_user, hash_password, require_roles
from backend.serializers import lab_out, patient_basic, patient_full
from backend.services import access
from backend.services.timeline_service import eid

router = APIRouter(tags=["patients"])


def _next_hb_id(db: Session) -> str:
    last = db.query(Patient).order_by(Patient.id.desc()).first()
    return f"HB-{(last.id + 1 if last else 1):06d}"


@router.post("/patients", status_code=201)
def create_patient(body: PatientCreate, db: Session = Depends(get_db),
                   doctor: User = Depends(require_roles("doctor"))):
    p = Patient(
        hb_id=_next_hb_id(db), full_name=body.full_name.strip(), dob=body.dob, gender=body.gender,
        phone=body.phone, blood_group=body.blood_group,
        allergies=[a.strip().lower() for a in body.allergies if a.strip()],
        chronic_conditions=[c.strip().lower() for c in body.chronic_conditions if c.strip()],
        created_by_org_id=doctor.organization_id,
    )
    db.add(p)
    db.flush()  # gives p.id without committing yet

    credentials = None
    if body.create_portal_login:
        username = p.hb_id.lower()
        temp_password = secrets.token_urlsafe(6)
        db.add(User(username=username, password_hash=hash_password(temp_password),
                    full_name=p.full_name, role="patient", patient_id=p.id))
        credentials = {"username": username, "temporary_password": temp_password}

    audit.log(db, doctor, "patient.create", "patient", p.id, p.id, p.hb_id)
    db.commit()
    return {"patient": patient_full(p), "portal_login": credentials}


@router.get("/patients/search")
def search_patients(q: str = Query(min_length=2), db: Session = Depends(get_db),
                    doctor: User = Depends(require_roles("doctor"))):
    """
    Search by name, HB-ID or phone. Returns ONLY basic identity (no clinical data),
    so a doctor can find a patient and then ask for consent / use emergency access.
    """
    like = f"%{q.strip()}%"
    rows = db.query(Patient).filter(
        or_(Patient.full_name.ilike(like), Patient.hb_id.ilike(like), Patient.phone.ilike(like))
    ).limit(20).all()
    audit.log(db, doctor, "patient.search", "patient", "", None, f"query='{q}'")
    db.commit()
    return [{**patient_basic(p), "has_access": access.can_view_patient(db, doctor, p.id)} for p in rows]


@router.get("/patients")
def my_patients(db: Session = Depends(get_db), doctor: User = Depends(require_roles("doctor"))):
    """Patients this doctor can open (their clinic's patients + consented ones)."""
    out = []
    for p in db.query(Patient).order_by(Patient.full_name).all():
        if access.can_view_patient(db, doctor, p.id):
            out.append({**patient_basic(p), "full_access": access.has_full_access(db, doctor, p.id)})
    return out


@router.get("/patients/{patient_id}")
def get_patient(patient_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    p = access.require_patient_access(db, user, patient_id)
    audit.log(db, user, "patient.view", "patient", p.id, p.id)
    db.commit()
    if user.role == "pharmacist":
        # pharmacists get identity + allergies only (needed for safe dispensing)
        return {**patient_basic(p), "allergies": p.allergies or [], "blood_group": p.blood_group}
    return patient_full(p)


@router.patch("/patients/{patient_id}")
def update_patient(patient_id: int, body: PatientUpdate, db: Session = Depends(get_db),
                   doctor: User = Depends(require_roles("doctor"))):
    p = access.require_patient_access(db, doctor, patient_id, full=True)
    changes = body.model_dump(exclude_none=True)
    for field, value in changes.items():
        if field in ("allergies", "chronic_conditions"):
            value = [v.strip().lower() for v in value if v.strip()]
        setattr(p, field, value)
    audit.log(db, doctor, "patient.update", "patient", p.id, p.id, ", ".join(changes))
    db.commit()
    return patient_full(p)


# ------------------------------------------------------------ consultations --
@router.post("/patients/{patient_id}/encounters", status_code=201)
def add_encounter(patient_id: int, body: EncounterCreate, db: Session = Depends(get_db),
                  doctor: User = Depends(require_roles("doctor"))):
    p = access.require_patient_access(db, doctor, patient_id, full=True)
    enc = Encounter(
        patient_id=p.id, clinician_id=doctor.id, organization_id=doctor.organization_id,
        chief_complaint=body.chief_complaint, notes=body.notes, diagnosis=body.diagnosis,
        plan=body.plan, vitals=body.vitals.model_dump(exclude_none=True), source="clinic", verified=True,
    )
    db.add(enc)
    db.flush()
    audit.log(db, doctor, "encounter.create", "encounter", enc.id, p.id)
    db.commit()
    return {"id": enc.id, "event_id": eid("ENC", enc.id)}


# ------------------------------------------------------------- past history --
@router.post("/patients/{patient_id}/history", status_code=201)
def add_history(patient_id: int, body: HistoryEntry, db: Session = Depends(get_db),
                user: User = Depends(require_roles("patient", "doctor"))):
    """
    Manually entered legacy/paper history (the MVP has NO OCR).
    Always stored as source='patient_reported', verified=False.
    """
    p = access.require_patient_access(db, user, patient_id, full=user.role == "doctor")
    date = utcnow()
    if body.approx_date:
        y, m, d = (int(x) for x in body.approx_date.split("-"))
        from datetime import datetime
        date = datetime(y, m, d)
    enc = Encounter(
        patient_id=p.id, clinician_id=None, organization_id=None, date=date, type="history",
        chief_complaint=body.title, notes=body.notes, source="patient_reported", verified=False,
    )
    db.add(enc)
    db.flush()
    audit.log(db, user, "history.add", "encounter", enc.id, p.id, "patient-reported / unverified")
    db.commit()
    return {"id": enc.id, "event_id": eid("ENC", enc.id), "verified": False}


@router.post("/encounters/{encounter_id}/verify")
def verify_history(encounter_id: int, db: Session = Depends(get_db),
                   doctor: User = Depends(require_roles("doctor"))):
    """A doctor confirms a patient-reported entry is accurate."""
    enc = db.get(Encounter, encounter_id)
    if enc is None:
        raise HTTPException(404, "Entry not found")
    access.require_patient_access(db, doctor, enc.patient_id, full=True)
    enc.verified = True
    enc.clinician_id = doctor.id
    enc.organization_id = doctor.organization_id
    audit.log(db, doctor, "history.verify", "encounter", enc.id, enc.patient_id)
    db.commit()
    return {"id": enc.id, "verified": True}


# --------------------------------------------------------------------- labs --
@router.post("/patients/{patient_id}/labs", status_code=201)
def add_lab(patient_id: int, body: LabCreate, db: Session = Depends(get_db),
            doctor: User = Depends(require_roles("doctor"))):
    p = access.require_patient_access(db, doctor, patient_id, full=True)
    flag = "normal"
    if body.ref_low is not None and body.value < body.ref_low:
        flag = "low"
    elif body.ref_high is not None and body.value > body.ref_high:
        flag = "high"
    lab = LabResult(patient_id=p.id, ordered_by_id=doctor.id, test=body.test, value=body.value,
                    unit=body.unit, ref_low=body.ref_low, ref_high=body.ref_high, flag=flag,
                    source=body.source or (doctor.organization.name if doctor.organization else ""))
    db.add(lab)
    db.flush()
    audit.log(db, doctor, "lab.create", "lab", lab.id, p.id, f"{body.test}={body.value}")
    db.commit()
    return lab_out(lab)
