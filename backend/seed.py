"""
seed.py - fills an EMPTY database with SYNTHETIC demo data (PRD Appendix A).

All people, clinics and numbers are made up. Never put real patient data in a
hackathon project. Runs automatically at start-up when the database is empty
(controlled by AUTO_SEED in config.py) or manually:  python -m backend.seed

Demo logins (password in brackets):
  doctor      dr.ayesha (doctor123)   - Shifa Family Clinic  (owns all 4 patients)
  doctor      dr.bilal  (doctor123)   - City Care Clinic     (needs consent!)
  pharmacist  pharm.abc (pharma123)   - ABC Pharmacy
  pharmacist  pharm.medi(pharma123)   - MediPlus Pharmacy
  patient     ahmed / sara / imran / fatima  (patient123)
  admin       admin (admin123)
"""
from datetime import timedelta

from sqlalchemy.orm import Session

from backend.database import Base, SessionLocal, engine, utcnow
from backend.models import (
    Dispensing, Encounter, LabResult, Organization, Patient, Prescription, PrescriptionItem, User,
)
from backend.security import hash_password
from backend.services import drug_data as dd


def _user(db, username, password, name, role, org=None, patient=None):
    u = User(username=username, password_hash=hash_password(password), full_name=name,
             role=role, organization_id=org.id if org else None, patient_id=patient.id if patient else None)
    db.add(u)
    return u


def _patient(db, org, n, name, dob, gender, phone, blood, allergies, conditions):
    p = Patient(hb_id=f"HB-{n:06d}", full_name=name, dob=dob, gender=gender, phone=phone,
                blood_group=blood, allergies=allergies, chronic_conditions=conditions, created_by_org_id=org.id)
    db.add(p)
    db.flush()
    return p


def _rx(db, patient, doctor, pharmacy, items, days_ago, status, enc=None, dispense_all=False):
    """Create a prescription (and optionally a full dispensing record)."""
    issued = utcnow() - timedelta(days=days_ago)
    rx = Prescription(rx_number=f"TMP-{patient.id}-{days_ago}-{len(items)}", patient_id=patient.id,
                      doctor_id=doctor.id, pharmacy_id=pharmacy.id, encounter_id=enc.id if enc else None,
                      status=status, issued_at=issued, safety_alerts=[])
    for medicine, strength, dose, freq, days in items:
        mg, unit = dd.parse_strength(strength)
        qty = max(1, round(dose * dd.FREQ_PER_DAY[freq] * days))
        rx.items.append(PrescriptionItem(
            medicine=medicine, generic_name=dd.normalize_medicine(medicine), strength=strength,
            strength_mg=mg if unit == "mg" else None, dose=dose, frequency=freq, duration_days=days,
            quantity=qty, dispensed_quantity=qty if dispense_all else 0))
    db.add(rx)
    db.flush()
    rx.rx_number = f"RX-{rx.id:05d}"
    if status in ("verified", "dispensed"):
        rx.verified_at = issued + timedelta(minutes=12)
    if dispense_all:
        pharmacist = db.query(User).filter(User.role == "pharmacist", User.organization_id == pharmacy.id).first()
        rx.completed_at = issued + timedelta(minutes=35)
        db.add(Dispensing(
            prescription_id=rx.id, pharmacist_id=pharmacist.id, pharmacy_id=pharmacy.id, status="full",
            lines=[{"item_id": i.id, "medicine": f"{i.medicine} {i.strength}", "quantity": i.quantity} for i in rx.items],
            notes="Counselled patient", timestamp=issued + timedelta(minutes=35)))
    return rx


def seed(db: Session) -> None:
    clinic1 = Organization(name="Shifa Family Clinic", type="clinic", city="Lahore")
    clinic2 = Organization(name="City Care Clinic", type="clinic", city="Karachi")
    abc = Organization(name="ABC Pharmacy", type="pharmacy", city="Lahore")
    medi = Organization(name="MediPlus Pharmacy", type="pharmacy", city="Lahore")
    platform = Organization(name="HealthBridge Platform", type="platform", city="")
    db.add_all([clinic1, clinic2, abc, medi, platform])
    db.flush()

    ayesha = _user(db, "dr.ayesha", "doctor123", "Dr. Ayesha Malik", "doctor", clinic1)
    _user(db, "dr.bilal", "doctor123", "Dr. Bilal Raza", "doctor", clinic2)
    _user(db, "pharm.abc", "pharma123", "Hamza Siddiqui", "pharmacist", abc)
    _user(db, "pharm.medi", "pharma123", "Nida Farooq", "pharmacist", medi)
    _user(db, "admin", "admin123", "System Admin", "admin", platform)
    db.flush()

    ahmed = _patient(db, clinic1, 1, "Ahmed Khan", "1988-04-12", "male", "0300-1111111", "B+", [], ["hypertension"])
    sara = _patient(db, clinic1, 2, "Sara Ali", "1995-09-03", "female", "0301-2222222", "A+", ["sulfa"], [])
    imran = _patient(db, clinic1, 3, "Imran Shah", "1975-01-20", "male", "0302-3333333", "O+", [], ["type 2 diabetes"])
    fatima = _patient(db, clinic1, 4, "Fatima Noor", "1990-06-25", "female", "0303-4444444", "AB+", ["penicillin"], [])
    for key, pat in (("ahmed", ahmed), ("sara", sara), ("imran", imran), ("fatima", fatima)):
        _user(db, key, "patient123", pat.full_name, "patient", patient=pat)

    now = utcnow()

    # Ahmed: consultation -> Rx -> dispensed (happy path)
    e = Encounter(patient_id=ahmed.id, clinician_id=ayesha.id, organization_id=clinic1.id,
                  date=now - timedelta(days=3), chief_complaint="Fever and sore throat for 2 days",
                  notes="Temp 38.4 C, throat congested, no cough. Tolerating fluids.",
                  diagnosis="Acute pharyngitis", plan="Symptomatic treatment, review if no improvement in 3 days",
                  vitals={"bp": "130/85", "temp_c": 38.4, "pulse": 88})
    db.add(e)
    db.flush()
    _rx(db, ahmed, ayesha, abc, [("Panadol", "500 mg", 1, "TDS", 5)], 3, "dispensed", e, dispense_all=True)

    # Sara: Rx sent, pharmacy still pending
    e = Encounter(patient_id=sara.id, clinician_id=ayesha.id, organization_id=clinic1.id,
                  date=now - timedelta(hours=20), chief_complaint="Recurrent headache for one week",
                  notes="No visual symptoms. Stress at work. BP normal.", diagnosis="Tension-type headache",
                  plan="Short course of analgesic, hydration, sleep hygiene", vitals={"bp": "118/76", "pulse": 74})
    db.add(e)
    db.flush()
    _rx(db, sara, ayesha, abc, [("Ibuprofen", "400 mg", 1, "BD", 3)], 1, "sent", e)

    # Imran: existing metformin 500 mg dispensed -> demo new 1000 mg conflict
    e = Encounter(patient_id=imran.id, clinician_id=ayesha.id, organization_id=clinic1.id,
                  date=now - timedelta(days=12), chief_complaint="Diabetes follow-up",
                  notes="Fatigue, increased thirst. Fasting sugar high.", diagnosis="Type 2 diabetes mellitus",
                  plan="Start metformin, diet advice, repeat HbA1c in 3 months", vitals={"bp": "128/82", "weight_kg": 84})
    db.add(e)
    db.flush()
    db.add(LabResult(patient_id=imran.id, ordered_by_id=ayesha.id, test="HbA1c", value=8.1, unit="%",
                     ref_low=4.0, ref_high=5.6, flag="high", date=now - timedelta(days=13), source="LabLink Diagnostics"))
    _rx(db, imran, ayesha, medi, [("Metformin", "500 mg", 1, "BD", 30)], 12, "dispensed", e, dispense_all=True)

    # Fatima: lab result first, then consultation (integrated timeline)
    db.add(LabResult(patient_id=fatima.id, ordered_by_id=ayesha.id, test="Serum Creatinine", value=1.6,
                     unit="mg/dL", ref_low=0.6, ref_high=1.1, flag="high", date=now - timedelta(days=5),
                     source="LabLink Diagnostics"))
    db.add(Encounter(patient_id=fatima.id, clinician_id=ayesha.id, organization_id=clinic1.id,
                     date=now - timedelta(days=4), chief_complaint="Joint pain and swelling in knees",
                     notes="Reviewed creatinine report. Pain on stairs. No fever.",
                     diagnosis="Knee osteoarthritis (suspected)", plan="Review after imaging",
                     vitals={"bp": "122/80", "weight_kg": 68}))
    db.commit()


def seed_if_empty() -> bool:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        if db.query(Organization).count() == 0:
            seed(db)
            return True
        return False
    finally:
        db.close()


if __name__ == "__main__":
    print("Seeded demo data." if seed_if_empty() else "Database already has data - nothing to do.")
