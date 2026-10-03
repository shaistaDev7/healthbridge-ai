"""
schemas.py - the SHAPE of data the API accepts (request bodies).

FastAPI uses these Pydantic classes to automatically:
  * check incoming JSON (wrong type / missing field -> clear 422 error)
  * generate the interactive docs at /docs
Think of each class as a form with rules.
"""
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

Frequency = Literal["OD", "BD", "TDS", "QID", "HS", "PRN"]


# ------------------------------------------------------------------ auth ----
class LoginRequest(BaseModel):
    username: str
    password: str


# -------------------------------------------------------------- patients ----
class PatientCreate(BaseModel):
    full_name: str = Field(min_length=2, max_length=120)
    dob: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")  # YYYY-MM-DD
    gender: Literal["male", "female", "other", ""] = ""
    phone: str = ""
    blood_group: str = ""
    allergies: List[str] = []
    chronic_conditions: List[str] = []
    create_portal_login: bool = True


class PatientUpdate(BaseModel):
    phone: Optional[str] = None
    blood_group: Optional[str] = None
    allergies: Optional[List[str]] = None
    chronic_conditions: Optional[List[str]] = None


# -------------------------------------------------------------- clinical ----
class Vitals(BaseModel):
    bp: str = ""
    pulse: Optional[int] = None
    temp_c: Optional[float] = None
    weight_kg: Optional[float] = None
    spo2: Optional[int] = None


class EncounterCreate(BaseModel):
    chief_complaint: str = Field(min_length=2, max_length=255)
    notes: str = ""
    diagnosis: str = ""
    plan: str = ""
    vitals: Vitals = Vitals()


class HistoryEntry(BaseModel):
    """Past history typed in manually (patient or doctor) - labelled UNVERIFIED."""
    title: str = Field(min_length=2, max_length=255)
    notes: str = ""
    approx_date: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")


class LabCreate(BaseModel):
    test: str
    value: float
    unit: str = ""
    ref_low: Optional[float] = None
    ref_high: Optional[float] = None
    source: str = ""


# --------------------------------------------------------- prescriptions ----
class RxItem(BaseModel):
    medicine: str = Field(min_length=2, max_length=120)
    strength: str = Field(min_length=1, max_length=40)  # "500 mg"
    dose: float = Field(default=1, gt=0, le=20)         # units per intake
    frequency: Frequency = "BD"
    duration_days: int = Field(default=5, ge=1, le=365)
    quantity: Optional[int] = Field(default=None, ge=1)  # auto-calculated if empty

    @field_validator("medicine", "strength")
    @classmethod
    def strip_text(cls, v: str) -> str:
        return v.strip()


class RxCreate(BaseModel):
    patient_id: int
    pharmacy_id: int
    encounter_id: Optional[int] = None
    items: List[RxItem] = Field(min_length=1, max_length=15)
    instructions: str = ""
    acknowledge_alerts: bool = False  # doctor confirms they reviewed critical/high alerts


class RxCheck(BaseModel):
    patient_id: int
    items: List[RxItem] = Field(min_length=1)


class RxDecision(BaseModel):
    decision: Literal["verify", "reject", "clarify"]
    note: str = ""


class DispenseLine(BaseModel):
    item_id: int
    quantity: int = Field(gt=0)


class DispenseRequest(BaseModel):
    lines: List[DispenseLine] = Field(min_length=1)
    notes: str = ""


class RxRespond(BaseModel):
    message: str = Field(min_length=1)


# --------------------------------------------------------------- consent ----
class ConsentCreate(BaseModel):
    grantee_user_id: int
    scope: Literal["all", "prescriptions", "labs"] = "all"
    days: int = Field(default=30, ge=1, le=365)


class BreakGlass(BaseModel):
    patient_id: int
    reason: str = Field(min_length=10, max_length=255)


# ----------------------------------------------------------------- alerts ---
class AlertReview(BaseModel):
    status: Literal["acknowledged", "dismissed"]
    note: str = ""


# ------------------------------------------------------------------- ai -----
class NoteText(BaseModel):
    text: str = Field(min_length=5, max_length=6000)


# ------------------------------------------------------------------ admin ---
class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=60)
    password: str = Field(min_length=6)
    full_name: str
    role: Literal["doctor", "pharmacist", "admin"]
    organization_id: Optional[int] = None


class UserStatus(BaseModel):
    status: Literal["active", "disabled"]
