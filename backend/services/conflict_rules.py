"""
conflict_rules.py - DETERMINISTIC medication safety checks (no AI involved).

Why rules and not AI for this? Because safety flags must be predictable and
testable. The AI "Conflict Agent" (backend/agents) only EXPLAINS these flags
in plain language - it never creates, removes or overrides them.

Input : the patient + the medicines being prescribed now
Output: a list of alert dicts:
    {"kind", "severity" (info|moderate|high|critical), "medicine", "message",
     "source_event_ids": [...]}   <- which past records caused the alert

Checks: allergy, drug interaction, duplicate therapy (same drug / same class),
        strength discrepancy vs earlier prescriptions, max daily dose,
        kidney-lab caution.
"""
from datetime import timedelta
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from backend.database import utcnow
from backend.models import LabResult, Patient, Prescription
from backend.services import drug_data as dd
from backend.services.timeline_service import eid

SEVERITY_ORDER = {"info": 0, "moderate": 1, "high": 2, "critical": 3}
HISTORY_DAYS = 180  # how far back we compare strengths


def _alert(kind, severity, medicine, message, sources=None) -> Dict:
    return {"kind": kind, "severity": severity, "medicine": medicine,
            "message": message, "source_event_ids": sources or []}


def _daily_dose_mg(strength_mg: Optional[float], dose: float, frequency: str) -> Optional[float]:
    if strength_mg is None:
        return None
    return strength_mg * dose * dd.FREQ_PER_DAY.get(frequency, 0)


def check_prescription(
    db: Session, patient: Patient, new_items: List[Dict], exclude_rx_id: Optional[int] = None
) -> List[Dict]:
    """Run every safety rule and return the alerts (highest severity first)."""
    alerts: List[Dict] = []
    now = utcnow()

    # Prepare the new items: generic name, strength in mg, classes
    prepared = []
    for it in new_items:
        generic = dd.normalize_medicine(it["medicine"])
        mg, unit = dd.parse_strength(it.get("strength", ""))
        prepared.append({**it, "generic": generic, "mg": mg if unit == "mg" else None,
                         "classes": dd.classes_of(generic)})

    # Earlier prescriptions (not rejected) inside the look-back window
    since = now - timedelta(days=HISTORY_DAYS)
    q = db.query(Prescription).filter(
        Prescription.patient_id == patient.id,
        Prescription.status != "rejected",
        Prescription.issued_at >= since,
    )
    if exclude_rx_id:
        q = q.filter(Prescription.id != exclude_rx_id)
    history = []  # (rx, item, still_active)
    for rx in q.all():
        for item in rx.items:
            active = rx.issued_at + timedelta(days=item.duration_days) > now
            history.append((rx, item, active))

    # ------------------------------------------------ 1. allergies
    allergies = [a.lower().strip() for a in (patient.allergies or [])]
    for p in prepared:
        for allergy in allergies:
            hit_class = allergy in dd.DRUG_CLASSES and allergy in p["classes"]
            hit_drug = allergy == p["generic"] or allergy in p["generic"]
            if hit_class or hit_drug:
                alerts.append(_alert(
                    "allergy", "critical", p["medicine"],
                    f"Patient has a recorded allergy to '{allergy}'. {p['medicine']} is "
                    f"{'in that drug class' if hit_class else 'that drug'}."))

    # ------------------------------------------------ 2. interactions
    # compare (new vs new) and (new vs active old)
    # "others" = the other new medicines + medicines the patient is still taking
    others = [(q["generic"], q["classes"], q["medicine"], None) for q in prepared]
    others += [(i.generic_name, dd.classes_of(i.generic_name), i.medicine, rx.rx_number)
               for rx, i, active in history if active]
    seen = set()
    for idx, p in enumerate(prepared):
        for j, (g2, cls2, name2, src) in enumerate(others):
            if src is None and j == idx:
                continue  # a medicine is not compared with itself
            for a, b, sev, msg in dd.INTERACTIONS:
                match = ((a == p["generic"] or a in p["classes"]) and (b == g2 or b in cls2)) or \
                        ((b == p["generic"] or b in p["classes"]) and (a == g2 or a in cls2))
                if not match:
                    continue
                key = (frozenset([p["generic"], g2]), msg)
                if key in seen:
                    continue
                seen.add(key)
                where = f" (existing medicine from {src})" if src else ""
                alerts.append(_alert("interaction", sev, f"{p['medicine']} + {name2}",
                                     msg + where, [src] if src else []))

    # ------------------------------------------------ 3. strength discrepancy / duplicates
    for p in prepared:
        flagged_strength = False
        for rx, item, active in history:
            if item.generic_name != p["generic"]:
                continue
            days_ago = (now - rx.issued_at).days
            if p["mg"] and item.strength_mg and abs(p["mg"] - item.strength_mg) > 0.001:
                ratio = max(p["mg"], item.strength_mg) / min(p["mg"], item.strength_mg)
                sev = "high" if ratio >= 2 else "moderate"
                alerts.append(_alert(
                    "strength_discrepancy", sev, p["medicine"],
                    f"{p['medicine']} was previously prescribed as {item.strength} "
                    f"({rx.rx_number}, {days_ago} day(s) ago); now {p['strength']}. "
                    f"Please confirm this change is intended.", [rx.rx_number]))
                flagged_strength = True
                break
        if not flagged_strength:
            for rx, item, active in history:
                if active and item.generic_name == p["generic"]:
                    alerts.append(_alert(
                        "duplicate_therapy", "moderate", p["medicine"],
                        f"{p['medicine']} is already active from {rx.rx_number} "
                        f"(course ends in about {max((rx.issued_at + timedelta(days=item.duration_days) - now).days, 0)} day(s)).",
                        [rx.rx_number]))
                    break
        # same class, different drug (e.g. two NSAIDs)
        for rx, item, active in history:
            if active and item.generic_name != p["generic"]:
                shared = p["classes"] & dd.classes_of(item.generic_name)
                shared -= {"penicillin"}  # not an issue by itself
                if shared:
                    alerts.append(_alert(
                        "therapeutic_duplication", "moderate", p["medicine"],
                        f"{p['medicine']} and {item.medicine} ({rx.rx_number}) are both '{sorted(shared)[0]}' "
                        f"medicines - possible duplicate therapy.", [rx.rx_number]))
                    break

    # ------------------------------------------------ 4. max daily dose
    for p in prepared:
        total = _daily_dose_mg(p["mg"], float(p.get("dose", 1)), p.get("frequency", "OD"))
        limit = dd.MAX_DAILY_MG.get(p["generic"])
        if total and limit and total > limit:
            alerts.append(_alert(
                "dose_limit", "high", p["medicine"],
                f"Total daily dose = {total:g} mg, above the usual adult maximum of {limit} mg/day."))

    # ------------------------------------------------ 5. kidney labs
    kidney = (
        db.query(LabResult)
        .filter(LabResult.patient_id == patient.id, LabResult.flag != "normal")
        .order_by(LabResult.date.desc()).all()
    )
    kidney = [l for l in kidney if any(k in l.test.lower() for k in ("creatinine", "egfr"))]
    if kidney:
        lab = kidney[0]
        for p in prepared:
            if p["generic"] in dd.RENAL_CAUTION or (p["classes"] & dd.RENAL_CAUTION):
                alerts.append(_alert(
                    "lab_caution", "moderate", p["medicine"],
                    f"Latest {lab.test} is {lab.flag.upper()} ({lab.value} {lab.unit}). "
                    f"{p['medicine']} needs caution with reduced kidney function.", [eid("LAB", lab.id)]))

    alerts.sort(key=lambda a: -SEVERITY_ORDER[a["severity"]])
    return alerts


def worst_severity(alerts: List[Dict]) -> str:
    if not alerts:
        return "none"
    return max((a["severity"] for a in alerts), key=lambda s: SEVERITY_ORDER[s])
