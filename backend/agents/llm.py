"""
llm.py - connects CrewAI to Grok (xAI) + privacy helpers.

HOW GROK IS CONNECTED
  xAI's API copies the OpenAI API format. CrewAI has a built-in "openai"
  provider, so we simply point it at xAI's address:
        model    = "openai/grok-4.7"        (the "openai/" prefix = use OpenAI format)
        base_url = "https://api.x.ai/v1"    (but send it to xAI, not OpenAI)
        api_key  = your XAI_API_KEY
"""
from typing import Dict, List

from backend.config import settings


def get_llm():
    """Create the Grok language model object that every agent shares."""
    from crewai import LLM  # imported here so the app starts fast even without AI

    return LLM(
        model=f"openai/{settings.XAI_MODEL}",
        api_key=settings.XAI_API_KEY,
        base_url=settings.XAI_BASE_URL,
        temperature=0.1,  # low = more consistent, less "creative" - good for medicine
    )


# ------------------------------------------------------------------ privacy --
def age_from_dob(dob: str) -> str:
    from datetime import date

    try:
        y, m, d = (int(x) for x in dob.split("-"))
        today = date.today()
        return str(today.year - y - ((today.month, today.day) < (m, d)))
    except Exception:
        return "unknown"


def deidentify_patient(patient) -> Dict:
    """
    Send the LLM only what it needs: age, gender, allergies, conditions.
    NOT the name, phone, HealthBridge ID or date of birth (data minimisation).
    """
    return {
        "age_years": age_from_dob(patient.dob or ""),
        "gender": patient.gender or "unknown",
        "allergies": patient.allergies or [],
        "chronic_conditions": patient.chronic_conditions or [],
    }


def deidentify_events(events: List[Dict], limit: int = 40) -> List[Dict]:
    """Keep only clinical content of each event; drop names of people/organisations."""
    out = []
    for e in events[:limit]:
        out.append({
            "event_id": e["event_id"], "type": e["type"], "date": e["date"].date().isoformat(),
            "title": e["title"], "summary": e["summary"], "status": e["status"],
        })
    return out
