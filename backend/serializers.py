"""
serializers.py - convert database rows into plain dicts (JSON-friendly).

Why? A database row is a Python object; the API must send JSON. These small
functions decide exactly WHICH fields leave the server (so we never leak
something like a password hash by accident).
"""
from backend.models import (
    ConflictAlert, Consent, LabResult, Notification, Organization, Patient, Prescription, User,
)


def user_out(u: User) -> dict:
    return {
        "id": u.id, "username": u.username, "full_name": u.full_name, "role": u.role,
        "organization_id": u.organization_id,
        "organization_name": u.organization.name if u.organization else None,
        "patient_id": u.patient_id, "status": u.status,
    }


def org_out(o: Organization) -> dict:
    return {"id": o.id, "name": o.name, "type": o.type, "city": o.city}


def patient_basic(p: Patient) -> dict:
    """Minimum identity info - safe to show in search results / pharmacy inbox."""
    return {"id": p.id, "hb_id": p.hb_id, "full_name": p.full_name, "dob": p.dob, "gender": p.gender}


def patient_full(p: Patient) -> dict:
    return {**patient_basic(p), "phone": p.phone, "blood_group": p.blood_group,
            "allergies": p.allergies or [], "chronic_conditions": p.chronic_conditions or []}


def rx_out(rx: Prescription, include_patient: bool = False) -> dict:
    data = {
        "id": rx.id, "rx_number": rx.rx_number, "patient_id": rx.patient_id, "status": rx.status,
        "doctor_id": rx.doctor_id, "doctor_name": rx.doctor.full_name,
        "pharmacy_id": rx.pharmacy_id, "pharmacy_name": rx.pharmacy.name,
        "issued_at": rx.issued_at.isoformat(),
        "verified_at": rx.verified_at.isoformat() if rx.verified_at else None,
        "completed_at": rx.completed_at.isoformat() if rx.completed_at else None,
        "instructions": rx.instructions, "pharmacist_note": rx.pharmacist_note,
        "safety_alerts": rx.safety_alerts or [], "alerts_acknowledged": rx.alerts_acknowledged,
        "items": [{
            "id": i.id, "medicine": i.medicine, "strength": i.strength, "dose": i.dose,
            "frequency": i.frequency, "duration_days": i.duration_days, "quantity": i.quantity,
            "dispensed_quantity": i.dispensed_quantity, "remaining": i.quantity - i.dispensed_quantity,
        } for i in rx.items],
        "messages": [{"author_name": m.author_name, "author_role": m.author_role, "text": m.text,
                      "created_at": m.created_at.isoformat()} for m in rx.messages],
        "dispensings": [{"id": d.id, "status": d.status, "lines": d.lines, "notes": d.notes,
                         "pharmacist": d.pharmacist.full_name, "timestamp": d.timestamp.isoformat()}
                        for d in rx.dispensings],
    }
    if include_patient:
        p = rx.patient if hasattr(rx, "patient") else None
        data["patient"] = patient_full(p) if p else None
    return data


def lab_out(l: LabResult) -> dict:
    return {"id": l.id, "test": l.test, "value": l.value, "unit": l.unit, "flag": l.flag,
            "ref_low": l.ref_low, "ref_high": l.ref_high, "date": l.date.isoformat(), "source": l.source}


def alert_out(a: ConflictAlert) -> dict:
    return {"id": a.id, "patient_id": a.patient_id, "prescription_id": a.prescription_id, "kind": a.kind,
            "severity": a.severity, "message": a.message, "ai_explanation": a.ai_explanation,
            "source_event_ids": a.source_event_ids or [], "status": a.status,
            "reviewed_by": a.reviewed_by, "review_note": a.review_note,
            "created_at": a.created_at.isoformat(),
            "reviewed_at": a.reviewed_at.isoformat() if a.reviewed_at else None}


def consent_out(c: Consent) -> dict:
    return {"id": c.id, "patient_id": c.patient_id, "grantee_user_id": c.grantee_user_id,
            "grantee_name": c.grantee.full_name, "scope": c.scope, "kind": c.kind, "reason": c.reason,
            "status": c.status, "created_at": c.created_at.isoformat(),
            "expires_at": c.expires_at.isoformat() if c.expires_at else None}


def notification_out(n: Notification) -> dict:
    return {"id": n.id, "title": n.title, "message": n.message, "is_read": n.is_read,
            "created_at": n.created_at.isoformat()}
