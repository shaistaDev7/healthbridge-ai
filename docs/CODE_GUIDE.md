# HealthBridge AI — Beginner's Code Guide

This guide explains **every file** in the project, how it works, and how the files connect.
You do not need to know everything first. Read it top to bottom once, then keep it open while you read the code.

---

## 1. The big picture (a restaurant analogy)

| Restaurant | HealthBridge AI | Technology |
|---|---|---|
| Dining room, menu, waiter | What the user sees and clicks | **Streamlit** (`streamlit_app.py`, `frontend/`) |
| The kitchen | Business logic and rules | **FastAPI** (`backend/`) |
| The pantry / storage room | Saved patients, prescriptions, audit log | **SQLite database** via **SQLAlchemy** |
| Security guard at the door | Login, roles, consent | `security.py`, `services/access.py` |
| Specialist consultants | AI agents that read, summarise, explain | **CrewAI + Grok** (`backend/agents/`) |

The most important rule of the design:

> The **frontend never touches the database**. It only sends HTTP requests to the **backend**, which checks who you
> are, applies the rules, reads/writes the database, and sends JSON back.

```
 Browser ─► Streamlit page ─► api_client.py ──HTTP──► FastAPI router ─► service/rules ─► Database
                                                          │
                                                          └─► orchestrator ─► CrewAI agents ─► Grok
```

---

## 2. Follow ONE click from start to finish

**A doctor presses “📤 Send e-prescription”.** Here is every step, with the file doing it:

1. `frontend/views/doctor.py` (`_prescription_form`) collects the medicines and calls `api.post("/prescriptions", {...})`.
2. `frontend/api_client.py` adds the login token to the request and sends it over HTTP.
3. `backend/main.py` receives it and hands it to the router that owns `/prescriptions`.
4. `backend/security.py` (`require_roles("doctor")`) reads the token, finds the user, and refuses anyone who is not a doctor.
5. `backend/schemas.py` (`RxCreate`) checks the data shape (e.g. `frequency` must be OD/BD/TDS…). Bad data → automatic error.
6. `backend/routers/prescriptions.py` (`create_prescription`) runs:
   1. `services/access.py` – may this doctor treat this patient?
   2. `_prepare_items` – brand → generic name (`drug_data.py`), strength → mg, auto-calculates quantity.
   3. `services/conflict_rules.py` – runs the **safety rules** (allergy, interaction, duplicate, strength change, dose limit, kidney labs).
   4. If any alert is **high/critical** and the doctor has not confirmed → responds `409` with the alerts (the UI then shows them and asks to confirm).
   5. Otherwise saves the prescription (`models.py` tables) and one `ConflictAlert` per alert.
   6. `audit.py` writes an audit record and notifies the pharmacist and patient.
7. The JSON response travels back; Streamlit shows “Sent RX-00004 to ABC Pharmacy”.
8. **Later (optional)**: the doctor clicks “AI review” → `routers/prescriptions.py:ai_review` → `agents/orchestrator.py` → `agents/crew_agents.py` → Grok. AI is *enrichment*; sending never waits for it.

---

## 3. Words you will meet

| Word | Meaning |
|---|---|
| **API / endpoint** | A URL the backend answers, e.g. `POST /prescriptions`. |
| **Router** | A file grouping related endpoints (all prescription URLs live in `routers/prescriptions.py`). |
| **ORM (SQLAlchemy)** | Lets you use Python classes instead of writing SQL. A class = a table; an object = a row. |
| **Session** | A “shopping basket” of database changes; `commit()` saves them all at once. |
| **Pydantic model** | A class that describes and validates the shape of data (types, min length, allowed values). |
| **JWT token** | A signed string proving who you are after login; sent with every request. |
| **RBAC** | Role-Based Access Control: what you may do depends on your role. |
| **Dependency (`Depends`)** | FastAPI feature that runs a helper (get DB session, get logged-in user) before your endpoint. |
| **Agent / Task / Crew** | CrewAI: an agent is a worker with a role; a task is a job; a crew runs tasks in order. |
| **Fallback** | Rule-based code that replaces the AI when it is unavailable. |
| **De-identification** | Removing name/phone/ID before sending data to the AI. |
| **FHIR** | International standard format for health data exchange. |

---

## 4. Backend files (read in this order)

### 4.1 `backend/config.py` — settings in one place
**What:** reads secrets and options from environment variables / the `.env` file and exposes them as `settings`.
**How:** `load_dotenv()` loads `.env`; the `Settings` class has one attribute per option (`XAI_API_KEY`, `DATABASE_URL`, `SECRET_KEY`, …). The `ai_available` property is `True` only when AI is enabled **and** a key exists.
**Why:** secrets must never be written inside code or pushed to GitHub.
**Connected to:** imported by almost every backend file (`from backend.config import settings`).

### 4.2 `backend/database.py` — connect to the database
**What:** creates the `engine` (the connection), `SessionLocal` (makes sessions), `Base` (parent of all tables), and `get_db()`.
**How:** `get_db()` is a FastAPI dependency: it opens a session for one request and always closes it afterwards. `utcnow()` gives a consistent timestamp.
**Connected to:** `models.py` (uses `Base`), every router (uses `get_db`).

### 4.3 `backend/models.py` — the tables
**What:** each class is a table: `Organization`, `User`, `Patient`, `Encounter` (consultation), `Prescription`, `PrescriptionItem`, `PrescriptionMessage`, `Dispensing`, `LabResult`, `Consent`, `AuditEvent`, `ConflictAlert`, `AISummary`, `Notification`.
**How to read one:**
```python
class LabResult(Base):
    __tablename__ = "lab_results"                       # table name
    id: Mapped[int] = mapped_column(primary_key=True)   # column
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"))  # link to a patient
```
`relationship()` lines let you write `prescription.items` instead of a database join.
**Notable design choices:** `Encounter.source` is `clinic` or `patient_reported` (+ `verified`) so unverified history is never confused with doctor-made records; `Prescription.status` follows the state machine described in its docstring; `ConflictAlert` stores safety flags so a human can review them later.
**Connected to:** everything that reads/writes data.

### 4.4 `backend/security.py` — passwords, tokens, roles
1. `hash_password` — salted PBKDF2 hash; we **never store real passwords**.
2. `verify_password` — recomputes the hash and compares in constant time.
3. `create_access_token` — builds a JWT containing the user id, role and expiry.
4. `get_current_user` — reads the `Authorization: Bearer …` header, decodes the token, loads the user (401 if invalid/disabled).
5. `require_roles("doctor")` — returns a dependency that rejects other roles with 403.
**Connected to:** every router; `seed.py` uses `hash_password`.

### 4.5 `backend/audit.py` — the paper trail
Two tiny helpers: `log(...)` adds an `AuditEvent` (who did what to which record) and `notify(...)` adds a `Notification`. They only *add* to the session; the calling endpoint commits, so an action and its audit record are saved together.

### 4.6 `backend/schemas.py` — request shapes
Pydantic classes such as `RxCreate`, `RxItem`, `ConsentCreate`. FastAPI uses them to validate input and generate the `/docs` page. Example: `frequency: Literal["OD","BD","TDS","QID","HS","PRN"]` makes any other value an automatic error.

### 4.7 `backend/serializers.py` — database object → JSON
Functions like `rx_out`, `patient_full`, `patient_basic` decide **exactly which fields leave the server** (so a password hash can never leak). `patient_basic` is the minimum identity used in search results and the pharmacy inbox.

### 4.8 `backend/services/` — the business logic

| File | Purpose |
|---|---|
| `drug_data.py` | Small demo knowledge base: brand→generic names, drug classes, interactions, max daily doses; helpers `normalize_medicine`, `parse_strength`. |
| `conflict_rules.py` | `check_prescription()` runs all safety rules and returns alerts with severity and **source record IDs**. No AI inside — predictable and testable. |
| `access.py` | “Who may see which patient?” — own record (patient), same clinic or active consent or break-glass (doctor), only routed prescriptions (pharmacist). Also scope filtering (`all` / `prescriptions` / `labs`). |
| `timeline_service.py` | `build_timeline()` merges encounters, prescriptions, dispensing, labs into one sorted list with IDs like `ENC-00001`; `active_medications()` works out what the patient is still taking. |
| `fhir.py` | Translates the timeline into a FHIR R4 Bundle. |

**How `conflict_rules.py` thinks** (read `check_prescription` top to bottom): for each new medicine it checks (1) allergies, (2) interactions with other new/active medicines, (3) same medicine earlier with a different strength → *strength discrepancy*, else already active → *duplicate*, same class → *therapeutic duplication*, (4) daily dose vs maximum, (5) abnormal kidney labs vs risky drugs. Results are sorted by severity.

### 4.9 `backend/agents/` — the AI layer (CrewAI + Grok)

| File | Purpose |
|---|---|
| `output_models.py` | Pydantic models the AI must answer in (`StructuredNote`, `RxReview`, `ConflictReport`, `TimelineSummary`, `PharmacyBrief`). Invalid AI output is rejected. |
| `llm.py` | `get_llm()` makes the Grok connection: model `openai/grok-4.7` + `base_url=https://api.x.ai/v1` (xAI copies OpenAI’s API format, so CrewAI’s OpenAI provider works). Also `deidentify_patient/events` (privacy). |
| `crew_agents.py` | Defines the 5 agents (role/goal/backstory + a shared **safety rule**), the task texts with `{placeholders}`, and `run_crew()` which builds Agents → Tasks → Crew and runs it. |
| `fallbacks.py` | Rule-based versions of every output, returning the *same* Pydantic models. |
| `orchestrator.py` | The 6th agent, written as plain Python. `_run()` tries AI; on **any** failure (no key, timeout, network, invalid JSON) returns the fallback. `summarize_timeline` additionally removes citations that do not exist. |

**Reading `run_crew` step by step:** create the shared Grok `llm` → for each requested task create (once) the matching `Agent` → create a `Task` with `output_pydantic=` → give later tasks `context=` of earlier tasks (the Conflict Agent sees the Prescription Agent’s result) → `Crew(...).kickoff(inputs=...)` fills the `{placeholders}` and runs → collect each task’s validated `.pydantic` result.

**The five agents:** Record (organise a note), Prescription (describe + things to check), Conflict (explain each flag + a question for the human), Timeline (source-cited briefing), Pharmacy (verification checklist).

### 4.10 `backend/routers/` — the URLs

| File | Endpoints (summary) |
|---|---|
| `auth.py` | `POST /auth/login`, `GET /auth/me`, directories of doctors/pharmacies (for dropdowns) |
| `patients.py` | create/search/list/get/update patient · add consultation, lab, patient-reported history · verify history |
| `prescriptions.py` | `check` (dry-run safety), create, list, detail, `decision` (verify/reject/clarify), `dispense`, `respond`, `ai-review`, `ai-brief` |
| `timeline.py` | timeline, medications, labs, AI summary, alerts + human review, FHIR export |
| `consent.py` | grant / revoke consent, break-glass, patient access log |
| `admin.py` | analytics, user management, audit log, notifications |
| `ai.py` | AI status, structure-note |

Every patient-related endpoint calls `require_patient_access(...)` first — consent is enforced **in one place**.

### 4.11 `backend/seed.py` — demo data
Creates 2 clinics, 2 pharmacies, users, and the 4 patients from the PRD (Ahmed happy path, Sara pending, Imran 500 mg metformin for the conflict demo, Fatima lab→consultation + allergy). Runs automatically when the database is empty.

### 4.12 `backend/main.py` — puts it all together
Creates the FastAPI `app`, runs `seed_if_empty()` at start-up (`lifespan`), adds CORS, and `include_router(...)` for each router. `GET /health` lets hosts check the app is alive. Run with `uvicorn backend.main:app --reload` and open `/docs` to try every endpoint.

---

## 5. Frontend files

### 5.1 `streamlit_app.py` — entry point
Streamlit **re-runs the whole file on every click**; `st.session_state` is the memory between runs. Flow of `main()`: inject CSS → `get_backend_url()` → build `API` client → if not logged in show `login_screen` (demo account buttons) → else `sidebar()` (menu by role, notification count, AI mode badge) → call the page function from `ROLE_PAGES[user.role]`.

### 5.2 `frontend/backend_runner.py`
`get_backend_url()` returns the backend address. If `API_URL` is set it uses that; otherwise it starts FastAPI (uvicorn) **in a background thread inside Streamlit** and waits for `/health`. `@st.cache_resource` makes it happen once. This is why a single Streamlit Cloud deployment is enough.

### 5.3 `frontend/api_client.py`
Class `API` with `get/post/patch/delete`; attaches the token; turns HTTP errors into `APIError` (with `status_code` and `detail`, so the UI can react to `409` safety alerts).

### 5.4 `frontend/ui.py`
Shared pieces: CSS, coloured badges, `show_alerts` (severity cards), `ai_banner` (the “AI-generated” label), `timeline_chart` (Plotly), `timeline_cards`, `render_ai_summary` (shows source IDs and the records behind them).

### 5.5 `frontend/views/` — one file per role
Each file defines page functions plus a `PAGES` dict (menu name → function).
- `doctor.py`: Dashboard · Patients (search, register, break-glass) · **Patient Workspace** (tabs: timeline, AI briefing, alerts, meds & labs, consultation, prescription, labs, profile) · Prescriptions · Review Queue.
- `pharmacist.py`: Inbox in 4 tabs; each prescription card offers AI checklist, Verify / Ask / Reject, partial dispensing.
- `patient.py`: My Record · Sharing & Consent · Access History · Add Past History · Export (FHIR).
- `admin.py`: Analytics · Users · Audit Trail.

---

## 6. Tests

- `tests/conftest.py` — uses a **temporary database** and turns AI off; provides logged-in fixtures (`doctor`, `pharm_abc`, …).
- `tests/test_workflow.py` — login/RBAC, the full prescription lifecycle, all safety rules, consent/revoke/break-glass, traceable fallback AI, FHIR, admin.
- `tests/test_crewai_agents.py` — starts a **fake Grok server**, points `XAI_BASE_URL` at it, and runs the real CrewAI pipeline; verifies structured output, citation checking, the two-agent crew, and fallback when Grok is unreachable.

Run: `pytest -v`.

---

## 7. Config & deployment files

| File | Purpose |
|---|---|
| `requirements.txt` / `requirements-dev.txt` | Python packages (runtime / + test tools). |
| `.env.example` | Template for local secrets (copy to `.env`). |
| `.streamlit/config.toml` | Theme (teal) and server options. |
| `.streamlit/secrets.toml.example` | Template for Streamlit Cloud “Secrets”. |
| `.gitignore` | Keeps secrets, the database and virtual env out of GitHub. |
| `Dockerfile`, `render.yaml` | Optional: host the FastAPI backend separately. |
| `README.md` | GitHub front page. |

---

## 8. Dependency map (who imports whom)

```
streamlit_app.py ─► frontend/views/* ─► frontend/ui.py, frontend/api_client.py
        └─► frontend/backend_runner.py ─► backend/main.py

backend/main.py ─► routers/* ─► security.py, database.py, models.py, schemas.py, serializers.py, audit.py
                                └─► services/* (access, conflict_rules, timeline_service, fhir, drug_data)
                                └─► agents/orchestrator.py ─► crew_agents.py ─► llm.py ─► config.py
                                                           └─► fallbacks.py, output_models.py
```

---

## 9. Practice exercises (best way to learn)

1. **Add a drug interaction:** append a tuple to `INTERACTIONS` in `drug_data.py`, then write a test in `test_workflow.py`.
2. **Add a brand name:** add `"disprin": "aspirin"`-style entries to `BRAND_TO_GENERIC`.
3. **Change a rule threshold:** in `conflict_rules.py`, make strength changes of ≥ 1.5× “high”.
4. **Add a field:** add `weight_kg` to `Patient` in `models.py` → delete `healthbridge.db` → show it in `serializers.patient_full` and `doctor.py`.
5. **Add an agent:** add an entry to `AGENT_DEFS` + `TASKS` in `crew_agents.py`, an output model, a fallback, an orchestrator function and an endpoint.
6. **Tune Grok:** change `XAI_MODEL` in `.env`, or lower/raise `temperature` in `llm.py`.

## 10. Troubleshooting

| Problem | Fix |
|---|---|
| “Cannot reach the backend” | Run `uvicorn backend.main:app` and set `API_URL`, or unset `API_URL` to use the built-in backend. |
| Yellow dot “rule-based fallback” | No/invalid `XAI_API_KEY`. Add it to `.env` (local) or Streamlit Secrets (cloud). |
| AI note says “AI unavailable (…)” | Key, quota or network problem; the app keeps working. Details are in the server log. |
| Data vanished on Streamlit Cloud | SQLite there is temporary; use a hosted PostgreSQL `DATABASE_URL`. |
| Changed `models.py` but columns missing | The database file is old: delete `healthbridge.db` (demo data is recreated). |
