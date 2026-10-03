"""
fhir.py - export a patient's timeline as an HL7 FHIR R4 "Bundle".

FHIR is the international standard for exchanging health data. The PRD says
"use FHIR for external APIs instead of inventing our own format" - this file
is the first step: our internal records are translated into FHIR resources:

  Patient       -> FHIR Patient
  Encounter     -> FHIR Encounter
  Prescription  -> FHIR MedicationRequest   (one per medicine line)
  Dispensing    -> FHIR MedicationDispense
  LabResult     -> FHIR Observation

This is a simplified mapping for demonstration (no coding systems like
RxNorm/LOINC yet). A production mapping must be validated against profiles.
"""
from typing import Dict, Set

from sqlalchemy.orm import Session

from backend.models import Dispensing, Encounter, LabResult, Patient, Prescription


def _iso(dt) -> str:
    return dt.isoformat() + "Z"


def patient_bundle(db: Session, patient: Patient, allowed_types: Set[str]) -> Dict:
    pid = f"patient-{patient.id}"
    entries = []

    name_parts = patient.full_name.split(" ", 1)
    entries.append({"resource": {
        "resourceType": "Patient", "id": pid,
        "identifier": [{"system": "https://healthbridge.example/hb-id", "value": patient.hb_id}],
        "name": [{"text": patient.full_name, "family": name_parts[-1], "given": name_parts[:1]}],
        "gender": (patient.gender or "unknown").lower(),
        "birthDate": patient.dob,
    }})

    if allowed_types & {"consultation", "history"}:
        for e in db.query(Encounter).filter(Encounter.patient_id == patient.id).all():
            kind = "history" if e.source == "patient_reported" else "consultation"
            if kind not in allowed_types:
                continue
            entries.append({"resource": {
                "resourceType": "Encounter", "id": f"enc-{e.id}", "status": "finished",
                "class": {"code": "AMB", "display": "ambulatory"},
                "subject": {"reference": f"Patient/{pid}"}, "period": {"start": _iso(e.date)},
                "reasonCode": [{"text": e.chief_complaint}] if e.chief_complaint else [],
                "extension": [{"url": "https://healthbridge.example/verified", "valueBoolean": e.verified}],
            }})

    if "prescription" in allowed_types:
        for rx in db.query(Prescription).filter(Prescription.patient_id == patient.id).all():
            status_map = {"rejected": "cancelled", "dispensed": "completed"}
            for item in rx.items:
                entries.append({"resource": {
                    "resourceType": "MedicationRequest", "id": f"rx-{rx.id}-{item.id}",
                    "status": status_map.get(rx.status, "active"), "intent": "order",
                    "medicationCodeableConcept": {"text": f"{item.medicine} {item.strength}"},
                    "subject": {"reference": f"Patient/{pid}"}, "authoredOn": _iso(rx.issued_at),
                    "identifier": [{"value": rx.rx_number}],
                    "dosageInstruction": [{"text": f"{item.dose:g} unit(s) {item.frequency} for {item.duration_days} days"}],
                    "dispenseRequest": {"quantity": {"value": item.quantity}},
                }})

    if "dispensing" in allowed_types:
        q = db.query(Dispensing).join(Prescription).filter(Prescription.patient_id == patient.id)
        for d in q.all():
            for line in d.lines:
                entries.append({"resource": {
                    "resourceType": "MedicationDispense", "id": f"dsp-{d.id}-{line['item_id']}",
                    "status": "completed", "subject": {"reference": f"Patient/{pid}"},
                    "medicationCodeableConcept": {"text": line["medicine"]},
                    "quantity": {"value": line["quantity"]}, "whenHandedOver": _iso(d.timestamp),
                    "authorizingPrescription": [{"display": d.prescription.rx_number}],
                }})

    if "lab" in allowed_types:
        for l in db.query(LabResult).filter(LabResult.patient_id == patient.id).all():
            entries.append({"resource": {
                "resourceType": "Observation", "id": f"lab-{l.id}", "status": "final",
                "code": {"text": l.test}, "subject": {"reference": f"Patient/{pid}"},
                "effectiveDateTime": _iso(l.date),
                "valueQuantity": {"value": l.value, "unit": l.unit},
                "interpretation": [{"text": l.flag}],
            }})

    return {"resourceType": "Bundle", "type": "collection", "total": len(entries), "entry": entries}
