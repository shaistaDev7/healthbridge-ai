"""
fallbacks.py - RULE-BASED versions of every AI output.

PRD non-functional requirement: "Core workflow works if AI is unavailable."
When there is no API key, the network is down, Grok times out, or Grok returns
something that fails validation, the orchestrator calls these instead.
They produce the SAME output models, so the UI does not care which was used.
"""
import re
from typing import Dict, List

from backend.agents.output_models import (
    ConflictExplanation, ConflictReport, KeyPoint, PharmacyBrief, RxReview,
    StructuredNote, TimelineSummary,
)


def structure_note(text: str) -> StructuredNote:
    """Very simple parser: looks for words like 'Dx:' / 'Plan:' / 'complaint:'."""
    def grab(label_regex: str) -> str:
        m = re.search(label_regex + r"\s*[:\-]\s*(.+?)(?=\n[A-Za-z ]{2,20}[:\-]|\Z)", text, re.I | re.S)
        return m.group(1).strip() if m else ""

    first_line = text.strip().split("\n")[0][:120]
    complaint = grab(r"(?:chief complaint|cc|complaint)") or first_line
    return StructuredNote(
        chief_complaint=complaint,
        history_summary=text.strip()[:500],
        diagnosis_as_stated=grab(r"(?:diagnosis|dx|impression)"),
        plan_as_stated=grab(r"(?:plan|advice|rx)"),
        missing_information=["Rule-based parsing only - please review every field."],
    )


def review_prescription(items: List[Dict], alerts: List[Dict]) -> RxReview:
    names = ", ".join(f"{i['medicine']} {i['strength']} {i['frequency']} for {i['duration_days']} day(s)"
                      for i in items)
    return RxReview(
        plain_summary=f"Prescription for: {names}.",
        points_to_check=[a["message"] for a in alerts] or ["No automated safety flags were raised."],
    )


def explain_conflicts(alerts: List[Dict]) -> ConflictReport:
    return ConflictReport(explanations=[
        ConflictExplanation(
            alert_index=i, explanation=a["message"],
            question_for_clinician="Is this intended? Please review the linked source records and confirm.")
        for i, a in enumerate(alerts)
    ])


def timeline_summary(patient: Dict, events: List[Dict], meds: List[Dict], alerts: List[Dict]) -> TimelineSummary:
    if not events:
        return TimelineSummary(headline="No recorded events yet.")
    types = sorted({e["type"] for e in events})
    points = [KeyPoint(text=f"{e['date']}: {e['title']} - {e['summary'][:140]}", source_ids=[e["event_id"]])
              for e in events[:6]]
    return TimelineSummary(
        headline=f"{len(events)} events ({', '.join(types)}); most recent on {events[0]['date']}.",
        key_points=points,
        current_medications=[f"{m['medicine']} {m['strength']} {m['frequency']} (until {m['ends_on']})" for m in meds],
        open_flags=[a["message"] for a in alerts if a.get("status", "open") == "open"],
    )


def pharmacy_brief(rx: Dict, patient: Dict, alerts: List[Dict]) -> PharmacyBrief:
    checklist = [
        "Confirm the patient's identity matches the prescription.",
        f"Check recorded allergies: {', '.join(patient['allergies']) or 'none recorded'}.",
        "Confirm medicine, strength, frequency, duration and quantity are clear.",
        "Check stock and expiry before dispensing.",
    ]
    return PharmacyBrief(
        summary=f"{len(rx['items'])} medicine line(s) from the prescriber.",
        verification_checklist=checklist,
        concerns=[a["message"] for a in alerts],
    )
