"""
output_models.py - the EXACT shape every AI agent must answer in.

We ask CrewAI to return these Pydantic models (`output_pydantic=`). If the AI
returns text that doesn't fit the shape, validation fails and we fall back to
the rule-based version. This is the PRD's "Validate outputs with Pydantic".
"""
from typing import List

from pydantic import BaseModel, Field


class StructuredNote(BaseModel):
    """Record Agent output: a doctor's free-text note organised into fields."""
    chief_complaint: str = Field(default="", description="Main reason for visit, short")
    symptoms: List[str] = Field(default_factory=list)
    history_summary: str = Field(default="", description="Relevant history exactly as written")
    diagnosis_as_stated: str = Field(default="", description="ONLY if the doctor wrote one, else empty")
    plan_as_stated: str = Field(default="", description="ONLY what the doctor wrote, else empty")
    missing_information: List[str] = Field(default_factory=list, description="Things worth asking the doctor")


class RxReview(BaseModel):
    """Prescription Agent output."""
    plain_summary: str = Field(description="One or two plain sentences describing the prescription")
    points_to_check: List[str] = Field(default_factory=list, description="Things the doctor should double-check")


class ConflictExplanation(BaseModel):
    alert_index: int = Field(description="0-based index of the alert in the list provided")
    explanation: str = Field(description="Plain-language reason why this was flagged")
    question_for_clinician: str = Field(description="A question for the human to resolve; never an instruction")


class ConflictReport(BaseModel):
    """Conflict Agent output."""
    explanations: List[ConflictExplanation] = Field(default_factory=list)


class KeyPoint(BaseModel):
    text: str
    source_ids: List[str] = Field(default_factory=list, description="event_ids supporting this point")


class TimelineSummary(BaseModel):
    """Timeline Agent output."""
    headline: str
    key_points: List[KeyPoint] = Field(default_factory=list)
    current_medications: List[str] = Field(default_factory=list)
    open_flags: List[str] = Field(default_factory=list)


class PharmacyBrief(BaseModel):
    """Pharmacy Agent output."""
    summary: str
    verification_checklist: List[str] = Field(default_factory=list)
    concerns: List[str] = Field(default_factory=list)
