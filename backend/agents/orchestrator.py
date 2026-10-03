"""
orchestrator.py - the "6th agent": routes work to the right crew, validates
the answer, and falls back to rules if anything goes wrong.

The API routers only ever call functions in THIS file. They don't know or
care whether Grok or the fallback produced the answer.

Every function returns:
    {"data": {...}, "generated_by": "...", "ai_used": True/False, "note": "..."}

Guarantees:
  1. If AI is off / no key / error / timeout / bad JSON -> rule-based fallback.
  2. Timeline source IDs are checked: IDs that don't exist are removed
     (so the AI cannot cite a record that is not real).
  3. The label "AI-generated, needs clinician verification" travels with output.
"""
import json
import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from typing import Callable, Dict, List

from backend.agents import fallbacks
from backend.agents.crew_agents import run_crew
from backend.config import settings

log = logging.getLogger("healthbridge.agents")
AI_TIMEOUT_SECONDS = 75
DISCLAIMER = "AI-generated assistance. It does not diagnose or prescribe. A clinician must verify before acting."

_pool = ThreadPoolExecutor(max_workers=4)


def _j(obj) -> str:
    return json.dumps(obj, default=str, ensure_ascii=False)


def _run(ai_call: Callable[[], Dict], fallback_call: Callable[[], Dict]) -> Dict:
    """Try the AI; on ANY problem return the rule-based result instead."""
    if not settings.ai_available:
        why = "AI disabled" if not settings.AI_ENABLED else "No XAI_API_KEY configured"
        return {"data": fallback_call(), "generated_by": "rule-based fallback", "ai_used": False,
                "note": why, "disclaimer": DISCLAIMER}
    try:
        future = _pool.submit(ai_call)
        data = future.result(timeout=AI_TIMEOUT_SECONDS)
        return {"data": data, "generated_by": f"{settings.XAI_MODEL} via CrewAI", "ai_used": True,
                "note": "", "disclaimer": DISCLAIMER}
    except FutureTimeout:
        note = "AI timed out - showing rule-based result"
    except Exception as exc:  # network, auth, invalid JSON, ...
        log.warning("AI call failed: %s", exc)
        note = f"AI unavailable ({type(exc).__name__}) - showing rule-based result"
    return {"data": fallback_call(), "generated_by": "rule-based fallback", "ai_used": False,
            "note": note, "disclaimer": DISCLAIMER}


# ---------------------------------------------------------------- workflows --
def structure_note(text: str) -> Dict:
    """Record Agent."""
    def ai():
        (out,) = run_crew(["record"], {"note_text": text})
        return out.model_dump()
    return _run(ai, lambda: fallbacks.structure_note(text).model_dump())


def review_prescription(patient: Dict, items: List[Dict], alerts: List[Dict]) -> Dict:
    """Prescription Agent -> Conflict Agent (2-agent crew). Returns review + per-alert explanations."""
    def ai():
        rx_review, conflicts = run_crew(
            ["prescription", "conflict"],
            {"patient_json": _j(patient), "items_json": _j(items), "alerts_json": _j(alerts)})
        return {"review": rx_review.model_dump(), "conflicts": conflicts.model_dump()}

    def fb():
        return {"review": fallbacks.review_prescription(items, alerts).model_dump(),
                "conflicts": fallbacks.explain_conflicts(alerts).model_dump()}
    return _run(ai, fb)


def summarize_timeline(patient: Dict, events: List[Dict], meds: List[Dict], alerts: List[Dict]) -> Dict:
    """Timeline Agent (+ Conflict Agent explains open flags). Source IDs are verified."""
    valid_ids = {e["event_id"] for e in events}

    def ai():
        (summary,) = run_crew(["timeline"], {
            "patient_json": _j(patient), "events_json": _j(events), "meds_json": _j(meds),
            "alerts_json": _j([a for a in alerts if a.get("status", "open") == "open"])})
        data = summary.model_dump()
        # Anti-hallucination: keep only citations that really exist.
        for kp in data["key_points"]:
            kp["source_ids"] = [s for s in kp["source_ids"] if s in valid_ids]
        return data

    result = _run(ai, lambda: fallbacks.timeline_summary(patient, events, meds, alerts).model_dump())
    return result


def pharmacy_brief(patient: Dict, rx: Dict, alerts: List[Dict]) -> Dict:
    """Pharmacy Agent."""
    def ai():
        (out,) = run_crew(["pharmacy"], {"patient_json": _j(patient), "rx_json": _j(rx), "alerts_json": _j(alerts)})
        return out.model_dump()
    return _run(ai, lambda: fallbacks.pharmacy_brief(rx, patient, alerts).model_dump())
