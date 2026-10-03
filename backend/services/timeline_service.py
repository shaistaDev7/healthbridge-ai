"""
timeline_service.py - turns many tables into ONE chronological patient timeline.

Every event gets a stable ID like  ENC-00012 / RX-00007 / DSP-00003 / LAB-00005.
These IDs are what the AI cites ("source_event_ids"), so a clinician can always
click back to the original record. This is the PRD's "traceability" requirement.
"""
from datetime import timedelta
from typing import Dict, List, Optional, Set

from sqlalchemy.orm import Session

from backend.database import utcnow
from backend.models import Dispensing, Encounter, LabResult, Prescription


def eid(prefix: str, num: int) -> str:
    return f"{prefix}-{num:05d}"


def build_timeline(db: Session, patient_id: int, allowed_types: Optional[Set[str]] = None) -> List[Dict]:
    """Return events (newest first). `allowed_types` hides event types the viewer may not see."""
    events: List[Dict] = []

    def want(t: str) -> bool:
        return allowed_types is None or t in allowed_types

    # ---- consultations & patient-reported history
    for e in db.query(Encounter).filter(Encounter.patient_id == patient_id).all():
        etype = "history" if e.source == "patient_reported" else "consultation"
        if not want(etype):
            continue
        title = e.chief_complaint or ("Past history (patient reported)" if etype == "history" else "Consultation")
        events.append({
            "event_id": eid("ENC", e.id), "type": etype, "date": e.date, "title": title,
            "summary": "; ".join(x for x in [
                f"Diagnosis: {e.diagnosis}" if e.diagnosis else "",
                f"Plan: {e.plan}" if e.plan else "",
                e.notes[:240] if e.notes else ""] if x),
            "actor": e.clinician.full_name if e.clinician else "Patient",
            "organization": e.organization.name if e.organization else "",
            "status": "verified" if e.verified else "UNVERIFIED (patient reported)",
            "details": {"vitals": e.vitals, "notes": e.notes, "diagnosis": e.diagnosis, "plan": e.plan,
                        "source": e.source, "verified": e.verified},
        })

    # ---- prescriptions
    for rx in db.query(Prescription).filter(Prescription.patient_id == patient_id).all():
        if not want("prescription"):
            break
        meds = ", ".join(f"{i.medicine} {i.strength} {i.frequency} x{i.duration_days}d" for i in rx.items)
        events.append({
            "event_id": rx.rx_number, "type": "prescription", "date": rx.issued_at,
            "title": f"Prescription {rx.rx_number}", "summary": meds,
            "actor": rx.doctor.full_name, "organization": rx.pharmacy.name + " (pharmacy)",
            "status": rx.status,
            "details": {"items": [
                {"medicine": i.medicine, "strength": i.strength, "dose": i.dose, "frequency": i.frequency,
                 "duration_days": i.duration_days, "quantity": i.quantity,
                 "dispensed_quantity": i.dispensed_quantity} for i in rx.items],
                "instructions": rx.instructions, "pharmacist_note": rx.pharmacist_note},
        })

    # ---- dispensing
    if want("dispensing"):
        q = db.query(Dispensing).join(Prescription).filter(Prescription.patient_id == patient_id)
        for d in q.all():
            events.append({
                "event_id": eid("DSP", d.id), "type": "dispensing", "date": d.timestamp,
                "title": f"Dispensed ({d.status}) for {d.prescription.rx_number}",
                "summary": ", ".join(f"{l['medicine']} x{l['quantity']}" for l in d.lines),
                "actor": d.pharmacist.full_name, "organization": d.pharmacy.name,
                "status": d.status, "details": {"lines": d.lines, "notes": d.notes,
                                                "prescription": d.prescription.rx_number},
            })

    # ---- labs
    if want("lab"):
        for l in db.query(LabResult).filter(LabResult.patient_id == patient_id).all():
            rng = f" (ref {l.ref_low}-{l.ref_high})" if l.ref_low is not None and l.ref_high is not None else ""
            events.append({
                "event_id": eid("LAB", l.id), "type": "lab", "date": l.date,
                "title": f"Lab: {l.test}", "summary": f"{l.value} {l.unit}{rng} - {l.flag.upper()}",
                "actor": l.source or "Laboratory", "organization": l.source,
                "status": l.flag, "details": {"test": l.test, "value": l.value, "unit": l.unit,
                                              "flag": l.flag, "ref_low": l.ref_low, "ref_high": l.ref_high},
            })

    events.sort(key=lambda e: e["date"], reverse=True)
    return events


def active_medications(db: Session, patient_id: int) -> List[Dict]:
    """
    Medicines the patient is probably still taking: prescribed (not rejected)
    and the course (issued date + duration) has not ended yet.
    """
    now = utcnow()
    result = []
    rxs = (
        db.query(Prescription)
        .filter(Prescription.patient_id == patient_id, Prescription.status.notin_(["rejected"]))
        .all()
    )
    for rx in rxs:
        for item in rx.items:
            ends = rx.issued_at + timedelta(days=item.duration_days)
            if ends > now:
                result.append({
                    "rx_number": rx.rx_number, "medicine": item.medicine, "generic_name": item.generic_name,
                    "strength": item.strength, "frequency": item.frequency,
                    "ends_on": ends.date().isoformat(), "days_left": (ends - now).days,
                    "status": rx.status, "dispensed": item.dispensed_quantity >= item.quantity,
                })
    return result
