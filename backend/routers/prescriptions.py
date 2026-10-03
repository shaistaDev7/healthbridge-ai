"""
routers/prescriptions.py - the heart of the product:
    doctor writes Rx -> safety check -> routed to pharmacy -> pharmacist
    verifies / rejects / asks clarification -> (partial) dispensing -> timeline.

Status flow:
    sent --verify--> verified --dispense--> partially_dispensed --dispense--> dispensed
    sent --reject--> rejected
    sent --clarify-> clarification_requested --doctor responds--> sent
"""
import math
from typing import List, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend import audit
from backend.agents import orchestrator
from backend.agents.llm import deidentify_patient
from backend.database import get_db, utcnow
from backend.models import (
    ConflictAlert, Dispensing, Organization, Prescription, PrescriptionItem, PrescriptionMessage, User,
)
from backend.schemas import DispenseRequest, RxCheck, RxCreate, RxDecision, RxRespond
from backend.security import get_current_user, require_roles
from backend.serializers import alert_out, patient_basic, rx_out
from backend.services import access
from backend.services import drug_data as dd
from backend.services.conflict_rules import check_prescription, worst_severity

router = APIRouter(tags=["prescriptions"])


# ------------------------------------------------------------------ helpers --
def _prepare_items(raw_items) -> List[dict]:
    """Add generic name, strength in mg and auto-calculated quantity to each item."""
    prepared = []
    for it in raw_items:
        d = it.model_dump()
        d["generic_name"] = dd.normalize_medicine(d["medicine"])
        mg, unit = dd.parse_strength(d["strength"])
        d["strength_mg"] = mg if unit == "mg" else None
        if d["quantity"] is None:
            per_day = dd.FREQ_PER_DAY[d["frequency"]]
            if per_day == 0:
                raise HTTPException(422, f"'{d['medicine']}' is PRN (as needed): please enter a quantity")
            d["quantity"] = max(1, math.ceil(d["dose"] * per_day * d["duration_days"]))
        prepared.append(d)
    return prepared


def _get_rx(db: Session, rx_id: int) -> Prescription:
    rx = db.get(Prescription, rx_id)
    if rx is None:
        raise HTTPException(404, "Prescription not found")
    return rx


def _check_rx_access(db: Session, user: User, rx: Prescription) -> None:
    if user.role == "patient" and user.patient_id == rx.patient_id:
        return
    if user.role == "pharmacist" and user.organization_id == rx.pharmacy_id:
        return
    if user.role == "doctor" and (rx.doctor_id == user.id or access.has_full_access(db, user, rx.patient_id)):
        return
    raise HTTPException(403, "No access to this prescription")


def _pharmacist_of(db: Session, rx: Prescription) -> List[User]:
    return db.query(User).filter(User.role == "pharmacist", User.organization_id == rx.pharmacy_id).all()


def _patient_user_id(db: Session, patient_id: int) -> Optional[int]:
    u = db.query(User).filter(User.role == "patient", User.patient_id == patient_id).first()
    return u.id if u else None


# ----------------------------------------------------------- safety pre-check --
@router.post("/prescriptions/check")
def precheck(body: RxCheck, db: Session = Depends(get_db), doctor: User = Depends(require_roles("doctor"))):
    """Dry run: shows safety alerts WITHOUT saving anything. Used for live feedback in the form."""
    patient = access.require_patient_access(db, doctor, body.patient_id, full=True)
    alerts = check_prescription(db, patient, _prepare_items(body.items))
    return {"alerts": alerts, "worst_severity": worst_severity(alerts)}


# ------------------------------------------------------------------- create --
@router.post("/prescriptions", status_code=201)
def create_prescription(body: RxCreate, db: Session = Depends(get_db),
                        doctor: User = Depends(require_roles("doctor"))):
    patient = access.require_patient_access(db, doctor, body.patient_id, full=True)
    pharmacy = db.get(Organization, body.pharmacy_id)
    if pharmacy is None or pharmacy.type != "pharmacy":
        raise HTTPException(422, "Please choose a valid participating pharmacy")

    items = _prepare_items(body.items)
    alerts = check_prescription(db, patient, items)
    severity = worst_severity(alerts)

    # High/critical alerts: the doctor must explicitly confirm they have reviewed them.
    if severity in ("high", "critical") and not body.acknowledge_alerts:
        raise HTTPException(409, detail={
            "message": "Safety alerts need your confirmation before sending.",
            "alerts": alerts, "worst_severity": severity})

    rx = Prescription(
        rx_number=f"TMP-{uuid4().hex[:10]}", patient_id=patient.id, doctor_id=doctor.id,
        encounter_id=body.encounter_id, pharmacy_id=pharmacy.id, status="sent",
        instructions=body.instructions, safety_alerts=alerts, alerts_acknowledged=bool(alerts),
    )
    for d in items:
        rx.items.append(PrescriptionItem(
            medicine=d["medicine"], generic_name=d["generic_name"], strength=d["strength"],
            strength_mg=d["strength_mg"], dose=d["dose"], frequency=d["frequency"],
            duration_days=d["duration_days"], quantity=d["quantity"]))
    db.add(rx)
    db.flush()
    rx.rx_number = f"RX-{rx.id:05d}"

    # Store each alert as a ConflictAlert row so a human can review it later.
    for a in alerts:
        db.add(ConflictAlert(
            patient_id=patient.id, prescription_id=rx.id, kind=a["kind"], severity=a["severity"],
            message=a["message"], source_event_ids=a["source_event_ids"]))

    audit.log(db, doctor, "prescription.create", "prescription", rx.rx_number, patient.id,
              f"to {pharmacy.name}; alerts={len(alerts)} worst={severity}")
    for ph in _pharmacist_of(db, rx):
        audit.notify(db, ph.id, "New prescription", f"{rx.rx_number} for {patient.full_name} from {doctor.full_name}")
    audit.notify(db, _patient_user_id(db, patient.id), "Prescription issued",
                 f"{rx.rx_number} was sent to {pharmacy.name}")
    db.commit()
    return {"prescription": rx_out(rx), "alerts": alerts}


# --------------------------------------------------------------------- list --
@router.get("/prescriptions")
def list_prescriptions(status: Optional[str] = Query(None), patient_id: Optional[int] = None,
                       db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    q = db.query(Prescription)
    if user.role == "pharmacist":
        q = q.filter(Prescription.pharmacy_id == user.organization_id)
    elif user.role == "patient":
        q = q.filter(Prescription.patient_id == user.patient_id)
    elif user.role == "doctor":
        colleague_ids = [u.id for u in db.query(User).filter(User.organization_id == user.organization_id)]
        q = q.filter(Prescription.doctor_id.in_(colleague_ids))
        if patient_id:
            q = q.filter(Prescription.patient_id == patient_id)
    else:
        raise HTTPException(403, "Admins cannot view clinical prescriptions")
    if status:
        q = q.filter(Prescription.status == status)
    rows = q.order_by(Prescription.issued_at.desc()).limit(200).all()
    return [{**rx_out(rx), "patient": patient_basic(rx.patient)} for rx in rows]


@router.get("/prescriptions/{rx_id}")
def get_prescription(rx_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rx = _get_rx(db, rx_id)
    _check_rx_access(db, user, rx)
    audit.log(db, user, "prescription.view", "prescription", rx.rx_number, rx.patient_id)
    db.commit()
    p = rx.patient
    patient = ({**patient_basic(p), "allergies": p.allergies or [], "blood_group": p.blood_group}
               if user.role == "pharmacist" else
               {**patient_basic(p), "allergies": p.allergies or [], "chronic_conditions": p.chronic_conditions or []})
    alerts = db.query(ConflictAlert).filter(ConflictAlert.prescription_id == rx.id).order_by(ConflictAlert.id).all()
    return {**rx_out(rx), "patient": patient, "alert_records": [alert_out(a) for a in alerts]}


# ---------------------------------------------------------- pharmacist steps --
@router.post("/prescriptions/{rx_id}/decision")
def decide(rx_id: int, body: RxDecision, db: Session = Depends(get_db),
           pharmacist: User = Depends(require_roles("pharmacist"))):
    rx = _get_rx(db, rx_id)
    if rx.pharmacy_id != pharmacist.organization_id:
        raise HTTPException(403, "This prescription was sent to a different pharmacy")
    if rx.status != "sent":
        raise HTTPException(409, f"Cannot decide on a prescription that is '{rx.status}'")
    if body.decision in ("reject", "clarify") and not body.note.strip():
        raise HTTPException(422, "Please write a note explaining your reason")

    new_status = {"verify": "verified", "reject": "rejected", "clarify": "clarification_requested"}[body.decision]
    rx.status = new_status
    rx.pharmacist_note = body.note
    if body.decision == "verify":
        rx.verified_at = utcnow()
    if body.note.strip():
        rx.messages.append(PrescriptionMessage(
            author_id=pharmacist.id, author_name=pharmacist.full_name, author_role="pharmacist", text=body.note))

    audit.log(db, pharmacist, f"prescription.{body.decision}", "prescription", rx.rx_number, rx.patient_id, body.note)
    audit.notify(db, rx.doctor_id, f"Prescription {new_status.replace('_', ' ')}", f"{rx.rx_number}: {body.note}")
    if body.decision != "clarify":
        audit.notify(db, _patient_user_id(db, rx.patient_id), f"Prescription {new_status}", rx.rx_number)
    db.commit()
    return rx_out(rx)


@router.post("/prescriptions/{rx_id}/dispense")
def dispense(rx_id: int, body: DispenseRequest, db: Session = Depends(get_db),
             pharmacist: User = Depends(require_roles("pharmacist"))):
    rx = _get_rx(db, rx_id)
    if rx.pharmacy_id != pharmacist.organization_id:
        raise HTTPException(403, "This prescription was sent to a different pharmacy")
    if rx.status not in ("verified", "partially_dispensed"):
        raise HTTPException(409, f"Verify the prescription first (current status: '{rx.status}')")

    items = {i.id: i for i in rx.items}
    lines = []
    for line in body.lines:
        item = items.get(line.item_id)
        if item is None:
            raise HTTPException(422, f"Item {line.item_id} is not part of {rx.rx_number}")
        remaining = item.quantity - item.dispensed_quantity
        if line.quantity > remaining:
            raise HTTPException(422, f"{item.medicine}: only {remaining} left to dispense (you entered {line.quantity})")
        item.dispensed_quantity += line.quantity
        lines.append({"item_id": item.id, "medicine": f"{item.medicine} {item.strength}", "quantity": line.quantity})

    complete = all(i.dispensed_quantity >= i.quantity for i in rx.items)
    rx.status = "dispensed" if complete else "partially_dispensed"
    if complete:
        rx.completed_at = utcnow()
    db.add(Dispensing(
        prescription_id=rx.id, pharmacist_id=pharmacist.id, pharmacy_id=pharmacist.organization_id,
        status="full" if complete else "partial", lines=lines, notes=body.notes))

    audit.log(db, pharmacist, "prescription.dispense", "prescription", rx.rx_number, rx.patient_id,
              f"{'full' if complete else 'partial'}: {lines}")
    audit.notify(db, rx.doctor_id, f"Prescription {rx.status.replace('_', ' ')}", rx.rx_number)
    audit.notify(db, _patient_user_id(db, rx.patient_id), f"Medicine {rx.status.replace('_', ' ')}",
                 f"{rx.rx_number} at {rx.pharmacy.name}")
    db.commit()
    return rx_out(rx)


# ------------------------------------------------------------- doctor replies --
@router.post("/prescriptions/{rx_id}/respond")
def respond(rx_id: int, body: RxRespond, db: Session = Depends(get_db),
            doctor: User = Depends(require_roles("doctor"))):
    rx = _get_rx(db, rx_id)
    _check_rx_access(db, doctor, rx)
    if rx.status != "clarification_requested":
        raise HTTPException(409, "This prescription is not waiting for a clarification")
    rx.messages.append(PrescriptionMessage(
        author_id=doctor.id, author_name=doctor.full_name, author_role="doctor", text=body.message))
    rx.status = "sent"  # goes back to the pharmacist's inbox
    audit.log(db, doctor, "prescription.respond", "prescription", rx.rx_number, rx.patient_id, body.message)
    for ph in _pharmacist_of(db, rx):
        audit.notify(db, ph.id, "Clarification answered", f"{rx.rx_number}: {body.message}")
    db.commit()
    return rx_out(rx)


# --------------------------------------------------------------- AI endpoints --
@router.post("/prescriptions/{rx_id}/ai-review")
def ai_review(rx_id: int, db: Session = Depends(get_db), doctor: User = Depends(require_roles("doctor"))):
    """Prescription Agent + Conflict Agent explain the rule-engine flags (stored for later)."""
    rx = _get_rx(db, rx_id)
    _check_rx_access(db, doctor, rx)
    alerts = rx.safety_alerts or []
    items = [{"medicine": i.medicine, "strength": i.strength, "dose": i.dose, "frequency": i.frequency,
              "duration_days": i.duration_days, "quantity": i.quantity} for i in rx.items]
    result = orchestrator.review_prescription(deidentify_patient(rx.patient), items, alerts)

    explanations = result["data"]["conflicts"]["explanations"]
    rows = db.query(ConflictAlert).filter(ConflictAlert.prescription_id == rx.id).order_by(ConflictAlert.id).all()
    for ex in explanations:
        if 0 <= ex["alert_index"] < len(rows):
            rows[ex["alert_index"]].ai_explanation = f"{ex['explanation']} Question: {ex['question_for_clinician']}"
    audit.log(db, doctor, "ai.prescription_review", "prescription", rx.rx_number, rx.patient_id, result["generated_by"])
    db.commit()
    return {**result, "alert_records": [alert_out(r) for r in rows]}


@router.post("/prescriptions/{rx_id}/ai-brief")
def ai_brief(rx_id: int, db: Session = Depends(get_db),
             user: User = Depends(require_roles("pharmacist", "doctor"))):
    """Pharmacy Agent: verification checklist for the pharmacist."""
    rx = _get_rx(db, rx_id)
    _check_rx_access(db, user, rx)
    rx_data = {"items": [{"medicine": i.medicine, "strength": i.strength, "dose": i.dose,
                          "frequency": i.frequency, "duration_days": i.duration_days, "quantity": i.quantity}
                         for i in rx.items], "instructions": rx.instructions}
    result = orchestrator.pharmacy_brief(deidentify_patient(rx.patient), rx_data, rx.safety_alerts or [])
    audit.log(db, user, "ai.pharmacy_brief", "prescription", rx.rx_number, rx.patient_id, result["generated_by"])
    db.commit()
    return result
