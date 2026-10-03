"""
crew_agents.py - the 5 CrewAI agents + a helper that runs them.

CrewAI in 30 seconds:
  Agent = a worker with a ROLE, GOAL and BACKSTORY (this shapes how it behaves)
  Task  = a job description for ONE agent + the output shape we expect
  Crew  = a team: agents + tasks, run in order (Process.sequential)
  crew.kickoff(inputs={...}) fills {placeholders} in the task text and runs it.

Our 5 agents (the 6th, the Orchestrator, is plain Python in orchestrator.py):
  record        - organises a doctor's free-text note
  prescription  - reviews a new prescription
  conflict      - explains safety flags raised by the rule engine
  timeline      - writes the source-grounded patient briefing
  pharmacy      - prepares a verification briefing for the pharmacist

SAFETY RULES are in `SAFETY` and are added to EVERY agent's backstory.
"""
from typing import Dict, List, Tuple, Type

from pydantic import BaseModel

from backend.agents import output_models as om
from backend.agents.llm import get_llm

SAFETY = (
    "ABSOLUTE RULES: You organise, summarise and flag. You NEVER diagnose, NEVER prescribe, "
    "NEVER change or suggest changing a medicine or dose, and NEVER give treatment advice. "
    "Use ONLY the data given to you; if something is not in the data, say it is not recorded. "
    "Never invent facts, names or IDs. Your output is reviewed by a licensed clinician."
)

AGENT_DEFS: Dict[str, Dict[str, str]] = {
    "record": {
        "role": "Clinical Record Agent",
        "goal": "Turn a clinician's free-text note into a clean structured record without adding anything new.",
        "backstory": "You are a careful medical scribe who only restructures what the doctor wrote.",
    },
    "prescription": {
        "role": "Prescription Review Agent",
        "goal": "Describe a prescription clearly and list things the prescriber may want to double-check.",
        "backstory": "You are a meticulous clinical pharmacist assistant who supports, never overrides, the prescriber.",
    },
    "conflict": {
        "role": "Medication Conflict Agent",
        "goal": "Explain, in plain language, why each automated safety flag was raised and what question a human should answer.",
        "backstory": "You translate rule-engine safety flags into short clear explanations. You never dismiss a flag.",
    },
    "timeline": {
        "role": "Patient Timeline Agent",
        "goal": "Write a concise briefing of the patient's history where EVERY statement cites source event IDs.",
        "backstory": "You are a clinical documentation specialist who prepares hand-over summaries for busy doctors.",
    },
    "pharmacy": {
        "role": "Pharmacy Workflow Agent",
        "goal": "Prepare a verification briefing so a pharmacist can quickly and safely verify a prescription.",
        "backstory": "You support pharmacists by listing what to check before dispensing.",
    },
}

# A task spec = (agent key, task description, expected output text, output model)
TaskSpec = Tuple[str, str, str, Type[BaseModel]]

# ---- Task texts. Words in {curly_braces} are filled from `inputs` at run time.
TASKS: Dict[str, TaskSpec] = {
    "record": (
        "record",
        "Organise this clinician note into the structured fields. Do not add diagnoses or advice that are "
        "not in the text. Leave a field empty if the note does not say it.\n\nNOTE:\n{note_text}",
        "A StructuredNote JSON object.",
        om.StructuredNote,
    ),
    "prescription": (
        "prescription",
        "Review this new prescription. Describe it in one or two plain sentences, then list points the "
        "prescriber may want to double-check (unclear instructions, automated flags below). Do not suggest "
        "changing any medicine.\n\nPATIENT (de-identified):\n{patient_json}\n\nPRESCRIPTION ITEMS:\n{items_json}"
        "\n\nAUTOMATED SAFETY FLAGS:\n{alerts_json}",
        "An RxReview JSON object.",
        om.RxReview,
    ),
    "conflict": (
        "conflict",
        "For EACH automated safety flag in the list, write a short plain-language explanation of why it was "
        "raised and ONE question for the human clinician to resolve. Use alert_index = position in the list "
        "starting at 0. Do not recommend a treatment change.\n\nFLAGS:\n{alerts_json}",
        "A ConflictReport JSON object with one explanation per flag.",
        om.ConflictReport,
    ),
    "timeline": (
        "timeline",
        "Write a briefing for a clinician about this patient. Give a one-line headline, 4-8 key points, the "
        "current medications and any open flags. EVERY key point must list the source_ids (event_id values) "
        "it is based on, copied exactly from the events. Use only the events below.\n\n"
        "PATIENT (de-identified):\n{patient_json}\n\nEVENTS (newest first):\n{events_json}\n\n"
        "CURRENT MEDICATIONS:\n{meds_json}\n\nOPEN SAFETY FLAGS:\n{alerts_json}",
        "A TimelineSummary JSON object.",
        om.TimelineSummary,
    ),
    "pharmacy": (
        "pharmacy",
        "Prepare a verification briefing for the pharmacist: a one-sentence summary, a checklist of things to "
        "verify before dispensing, and concerns (use the automated flags). Do not change the prescription.\n\n"
        "PATIENT (de-identified):\n{patient_json}\n\nPRESCRIPTION:\n{rx_json}\n\nAUTOMATED FLAGS:\n{alerts_json}",
        "A PharmacyBrief JSON object.",
        om.PharmacyBrief,
    ),
}


def run_crew(task_keys: List[str], inputs: Dict[str, str]) -> List[BaseModel]:
    """
    Build a Crew with one task per key (run in order) and return each task's
    validated Pydantic output. Later tasks can read earlier ones via `context`.
    """
    from crewai import Agent, Crew, Process, Task

    llm = get_llm()
    agents: Dict[str, Agent] = {}
    tasks: List[Task] = []

    for key in task_keys:
        agent_key, description, expected, model = TASKS[key]
        if agent_key not in agents:
            d = AGENT_DEFS[agent_key]
            agents[agent_key] = Agent(
                role=d["role"], goal=d["goal"], backstory=f"{d['backstory']} {SAFETY}",
                llm=llm, allow_delegation=False, verbose=False, max_iter=3,
            )
        tasks.append(Task(
            description=description, expected_output=expected,
            agent=agents[agent_key], output_pydantic=model,
            context=list(tasks) if tasks else None,  # later tasks see earlier results
        ))

    crew = Crew(agents=list(agents.values()), tasks=tasks, process=Process.sequential, verbose=False)
    result = crew.kickoff(inputs=inputs)

    outputs = []
    for t, out in zip(task_keys, result.tasks_output):
        parsed = out.pydantic
        if parsed is None:
            raise ValueError(f"Agent '{t}' did not return valid structured output")
        outputs.append(parsed)
    return outputs
