"""
models.py - the DATABASE TABLES, written as Python classes.

Each class = one table. Each `Mapped[...] = mapped_column(...)` = one column.
`ForeignKey("patients.id")` means "this column points to a row in patients".
`relationship(...)` lets us write `prescription.items` instead of writing joins.

Mapping to the PRD data model (section 13):
  User, Organization, Patient, Encounter, Prescription, PrescriptionItem,
  Dispensing, LabResult, Consent, AuditEvent
Extra tables we added: ConflictAlert, AISummary, Notification, PrescriptionMessage
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base, utcnow


class Organization(Base):
    """A clinic, pharmacy or lab that takes part in the network."""
    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    type: Mapped[str] = mapped_column(String(20))  # clinic | pharmacy | lab | platform
    city: Mapped[str] = mapped_column(String(60), default="")


class User(Base):
    """A person who can log in. `role` decides what they may do (RBAC)."""
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(20))  # doctor | pharmacist | patient | admin
    organization_id: Mapped[Optional[int]] = mapped_column(ForeignKey("organizations.id"), nullable=True)
    # Only for role == "patient": which patient record is this login?
    patient_id: Mapped[Optional[int]] = mapped_column(ForeignKey("patients.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | disabled
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    organization: Mapped[Optional[Organization]] = relationship()


class Patient(Base):
    __tablename__ = "patients"

    id: Mapped[int] = mapped_column(primary_key=True)
    hb_id: Mapped[str] = mapped_column(String(20), unique=True, index=True)  # e.g. HB-000001
    full_name: Mapped[str] = mapped_column(String(120), index=True)
    dob: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)  # YYYY-MM-DD
    gender: Mapped[str] = mapped_column(String(10), default="")
    phone: Mapped[str] = mapped_column(String(30), default="")
    blood_group: Mapped[str] = mapped_column(String(5), default="")
    allergies: Mapped[list] = mapped_column(JSON, default=list)            # ["penicillin"]
    chronic_conditions: Mapped[list] = mapped_column(JSON, default=list)   # ["type 2 diabetes"]
    created_by_org_id: Mapped[Optional[int]] = mapped_column(ForeignKey("organizations.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Encounter(Base):
    """A consultation (or a patient-reported past-history entry)."""
    __tablename__ = "encounters"

    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), index=True)
    clinician_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    organization_id: Mapped[Optional[int]] = mapped_column(ForeignKey("organizations.id"), nullable=True)
    date: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    type: Mapped[str] = mapped_column(String(30), default="consultation")
    chief_complaint: Mapped[str] = mapped_column(String(255), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    diagnosis: Mapped[str] = mapped_column(String(255), default="")
    plan: Mapped[str] = mapped_column(Text, default="")
    vitals: Mapped[dict] = mapped_column(JSON, default=dict)  # {"bp": "120/80", "temp_c": 37}
    # "clinic" = created by a doctor; "patient_reported" = typed by patient (UNVERIFIED)
    source: Mapped[str] = mapped_column(String(20), default="clinic")
    verified: Mapped[bool] = mapped_column(Boolean, default=True)

    clinician: Mapped[Optional[User]] = relationship()
    organization: Mapped[Optional[Organization]] = relationship()


class Prescription(Base):
    __tablename__ = "prescriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    rx_number: Mapped[str] = mapped_column(String(20), unique=True, index=True)  # RX-00001
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), index=True)
    doctor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    encounter_id: Mapped[Optional[int]] = mapped_column(ForeignKey("encounters.id"), nullable=True)
    pharmacy_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"))
    # sent -> verified -> partially_dispensed -> dispensed
    #      -> rejected | clarification_requested (doctor replies -> sent)
    status: Mapped[str] = mapped_column(String(30), default="sent", index=True)
    issued_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    instructions: Mapped[str] = mapped_column(Text, default="")
    pharmacist_note: Mapped[str] = mapped_column(Text, default="")
    safety_alerts: Mapped[list] = mapped_column(JSON, default=list)  # alerts shown when sent
    alerts_acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)

    patient: Mapped[Patient] = relationship()
    doctor: Mapped[User] = relationship()
    pharmacy: Mapped[Organization] = relationship()
    items: Mapped[list["PrescriptionItem"]] = relationship(
        back_populates="prescription", cascade="all, delete-orphan"
    )
    messages: Mapped[list["PrescriptionMessage"]] = relationship(
        cascade="all, delete-orphan", order_by="PrescriptionMessage.created_at"
    )
    dispensings: Mapped[list["Dispensing"]] = relationship(
        back_populates="prescription", cascade="all, delete-orphan"
    )


class PrescriptionItem(Base):
    """One medicine line inside a prescription."""
    __tablename__ = "prescription_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    prescription_id: Mapped[int] = mapped_column(ForeignKey("prescriptions.id"))
    medicine: Mapped[str] = mapped_column(String(120))
    generic_name: Mapped[str] = mapped_column(String(120), default="")  # normalised, for safety rules
    strength: Mapped[str] = mapped_column(String(40))        # "500 mg"
    strength_mg: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    dose: Mapped[float] = mapped_column(Float, default=1)    # units per intake (1 tablet)
    frequency: Mapped[str] = mapped_column(String(10))       # OD | BD | TDS | QID | HS | PRN
    duration_days: Mapped[int] = mapped_column(Integer, default=5)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    dispensed_quantity: Mapped[int] = mapped_column(Integer, default=0)

    prescription: Mapped[Prescription] = relationship(back_populates="items")


class PrescriptionMessage(Base):
    """Small chat between pharmacist and doctor about ONE prescription."""
    __tablename__ = "prescription_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    prescription_id: Mapped[int] = mapped_column(ForeignKey("prescriptions.id"))
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    author_name: Mapped[str] = mapped_column(String(120))
    author_role: Mapped[str] = mapped_column(String(20))
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Dispensing(Base):
    """One hand-over of medicine at a pharmacy (full or partial)."""
    __tablename__ = "dispensings"

    id: Mapped[int] = mapped_column(primary_key=True)
    prescription_id: Mapped[int] = mapped_column(ForeignKey("prescriptions.id"), index=True)
    pharmacist_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    pharmacy_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"))
    status: Mapped[str] = mapped_column(String(20))  # full | partial
    lines: Mapped[list] = mapped_column(JSON, default=list)  # [{"item_id":1,"medicine":"..","quantity":10}]
    notes: Mapped[str] = mapped_column(Text, default="")
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    prescription: Mapped[Prescription] = relationship(back_populates="dispensings")
    pharmacist: Mapped[User] = relationship()
    pharmacy: Mapped[Organization] = relationship()


class LabResult(Base):
    __tablename__ = "lab_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), index=True)
    ordered_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    test: Mapped[str] = mapped_column(String(120))
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(30), default="")
    ref_low: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    ref_high: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    flag: Mapped[str] = mapped_column(String(10), default="normal")  # normal | low | high
    date: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    source: Mapped[str] = mapped_column(String(120), default="")  # lab / organisation name


class Consent(Base):
    """Patient says: 'this doctor may see my records'. Can be revoked."""
    __tablename__ = "consents"

    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), index=True)
    grantee_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    scope: Mapped[str] = mapped_column(String(20), default="all")  # all | prescriptions | labs
    kind: Mapped[str] = mapped_column(String(20), default="patient_granted")  # or break_glass
    reason: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | revoked
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    grantee: Mapped[User] = relationship()


class AuditEvent(Base):
    """Append-only log: WHO did WHAT to WHICH record WHEN."""
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    actor_name: Mapped[str] = mapped_column(String(120), default="system")
    actor_role: Mapped[str] = mapped_column(String(20), default="")
    action: Mapped[str] = mapped_column(String(60), index=True)
    entity_type: Mapped[str] = mapped_column(String(40), default="")
    entity_id: Mapped[str] = mapped_column(String(40), default="")
    patient_id: Mapped[Optional[int]] = mapped_column(Integer, index=True, nullable=True)
    detail: Mapped[str] = mapped_column(Text, default="")
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class ConflictAlert(Base):
    """A safety flag that needs a HUMAN to review. AI never resolves it itself."""
    __tablename__ = "conflict_alerts"

    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), index=True)
    prescription_id: Mapped[Optional[int]] = mapped_column(ForeignKey("prescriptions.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(40))       # strength_discrepancy, allergy, interaction ...
    severity: Mapped[str] = mapped_column(String(10))   # info | moderate | high | critical
    message: Mapped[str] = mapped_column(Text)
    ai_explanation: Mapped[str] = mapped_column(Text, default="")
    source_event_ids: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="open")  # open | acknowledged | dismissed
    reviewed_by: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    review_note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class AISummary(Base):
    """Saved AI briefings so every AI output can be traced back later."""
    __tablename__ = "ai_summaries"

    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), index=True)
    requested_by: Mapped[str] = mapped_column(String(120), default="")
    content: Mapped[dict] = mapped_column(JSON, default=dict)
    generated_by: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(120))
    message: Mapped[str] = mapped_column(Text, default="")
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
