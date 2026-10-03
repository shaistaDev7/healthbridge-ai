# 🩺 HealthBridge AI

**One consent-based medical timeline connecting doctors, pharmacies and patients.**

HealthBridge AI is a healthcare interoperability prototype. A doctor writes an e-prescription, a participating
pharmacy verifies and dispenses it, and every step lands on the patient's connected timeline. A multi-agent AI
layer (**CrewAI + Grok**) organises records, writes source-linked summaries and explains safety flags, while
**deterministic rules** do the actual medication-safety checks and **humans make every decision**.

> ⚠️ **Prototype with synthetic data only.** Not a medical device. Not for clinical use.
> Production use requires privacy, security, clinical-governance and regulatory review.

---

## The problem

Patients use many clinics, labs and pharmacies whose records are not connected. Doctors often prescribe without
the full medication history, pharmacy dispensing is invisible to the prescriber, and the patient becomes the
"manual bridge" carrying paper between systems. Pakistan's national Unified Medical Record effort is moving in the
right direction, but participating private clinics and pharmacies still need a practical, lightweight layer today.

## The solution

```
Doctor ──► e-prescription ──► Pharmacy verifies / asks / rejects ──► (partial) dispensing
   │                                                                        │
   └──────────────► ONE connected, consent-based patient timeline ◄─────────┘
                       (consultations · prescriptions · dispensing · labs · history)
                                         │
                       AI briefing + safety flags → always reviewed by a human
```

## Features

| Area | What you get |
|---|---|
| **Roles & security** | Doctor, Pharmacist, Patient, Admin · JWT login · role-based access control · salted password hashing |
| **Patient registry** | HealthBridge ID (HB-000001), allergies, chronic conditions, optional patient portal login |
| **Consultations** | Structured notes + vitals · optional AI "structure my note" (Record Agent) |
| **E-prescriptions** | Multi-medicine, brand-name aware (Panadol → paracetamol), auto-quantity, routed to a chosen pharmacy |
| **Medication safety rules** | Allergy (incl. drug classes) · interactions · duplicate therapy · **strength discrepancy** (500 mg → 1000 mg) · max daily dose · kidney-lab caution |
| **Pharmacy workflow** | Inbox · verify / reject / ask clarification · doctor reply loop · **partial dispensing** |
| **Connected timeline** | Consultations, prescriptions, dispensing, labs and patient-reported history in one Plotly timeline |
| **AI agents (CrewAI + Grok)** | Record · Prescription · Conflict · Timeline · Pharmacy agents + Orchestrator |
| **Traceability** | Every AI statement cites source event IDs (`RX-00007`, `LAB-00002`…); invented IDs are stripped |
| **Consent** | Patient grants/revokes access per doctor with scope (all / prescriptions / labs) and expiry |
| **Break-glass emergency access** | Time-limited, reason required, patient notified, fully audited |
| **Audit trail** | Every access and change is logged · patients can see *who looked at their data* |
| **Legacy history (no OCR)** | Patient-typed history labelled **unverified** until a doctor confirms |
| **FHIR R4 export** | Timeline as a standards-based Bundle (Patient, Encounter, MedicationRequest, MedicationDispense, Observation) |
| **Review queue** | Open safety flags awaiting a clinician decision (AI can never close a flag) |
| **Notifications & analytics** | In-app notifications · KPI dashboards for doctors and admins |
| **Works without AI** | No API key? Every feature still works using rule-based fallbacks |

## Architecture

```
┌────────────────────────┐   HTTP + JWT    ┌─────────────────────────────────────────────┐
│  Streamlit frontend    │ ──────────────► │  FastAPI backend                            │
│  (streamlit_app.py,    │ ◄────────────── │  routers → services (rules, access, FHIR)   │
│   frontend/)           │      JSON       │             │                               │
└────────────────────────┘                 │             ├─► SQLAlchemy ─► SQLite / PostgreSQL
                                           │             └─► Orchestrator ─► CrewAI agents ─► Grok (xAI API)
                                           │                       └─ rule-based fallback if AI is unavailable
                                           └─────────────────────────────────────────────┘
```

**Design principles:** AI organises, summarises and flags — it never diagnoses, prescribes or changes medication ·
safety checks are deterministic and testable · AI input is de-identified · AI output is schema-validated and
source-cited · the core workflow works with AI off.

## Tech stack

| Layer | Technology |
|---|---|
| Frontend | Streamlit, Plotly, pandas |
| Backend | FastAPI, Uvicorn, Pydantic v2 |
| Database | SQLAlchemy 2 · SQLite (demo) → PostgreSQL (production) |
| Auth | PyJWT, PBKDF2-SHA256 password hashing |
| Agents | **CrewAI** |
| LLM | **Grok (`grok-4.7`) via xAI's OpenAI-compatible API** |
| Interoperability | HL7 FHIR R4 (export) |
| Tests | pytest, FastAPI TestClient |

## Quick start (local)

Requires **Python 3.10 – 3.13**.

```bash
git clone https://github.com/<your-username>/healthbridge-ai.git
cd healthbridge-ai

python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env                 # then edit .env and add your XAI_API_KEY (optional)

streamlit run streamlit_app.py
```

Open <http://localhost:8501>. The FastAPI backend starts automatically inside the app and demo data is created
on first run. Without an `XAI_API_KEY` the app runs in **rule-based fallback mode** (a yellow dot in the sidebar);
with a key you'll see a green dot and real Grok agents.

### Run the backend separately (optional)

```bash
uvicorn backend.main:app --reload --port 8000     # API docs: http://localhost:8000/docs
API_URL=http://localhost:8000 streamlit run streamlit_app.py
```

## Demo accounts (synthetic data)

| Role | Username | Password | Notes |
|---|---|---|---|
| Doctor | `dr.ayesha` | `doctor123` | Shifa Family Clinic – owns the 4 demo patients |
| Doctor | `dr.bilal` | `doctor123` | City Care Clinic – **needs patient consent** |
| Pharmacist | `pharm.abc` | `pharma123` | ABC Pharmacy |
| Pharmacist | `pharm.medi` | `pharma123` | MediPlus Pharmacy |
| Patient | `ahmed` `sara` `imran` `fatima` | `patient123` | |
| Admin | `admin` | `admin123` | |

## 5-minute demo script

1. **Login** as `dr.ayesha` → open **Patient Workspace → Ahmed Khan**: consultation → prescription → dispensing timeline.
2. Register a **new patient**, record a consultation, write a prescription, send it to **ABC Pharmacy** (target: < 60 s).
3. Sign out, login as `pharm.abc` → the prescription is already in the inbox → **Verify** → **Dispense** (try partial).
4. Login as that patient (credentials shown at registration) → timeline shows consultation → prescription → dispensing.
5. **Conflict demo:** as `dr.ayesha` open **Imran Shah** → New Prescription → *Metformin, 1000 mg, BD, 30 days* →
   a **strength discrepancy vs RX-00003 (500 mg)** is flagged; confirm the alert to send; see it in **Review Queue**.
6. **Allergy demo:** open **Fatima Noor** (penicillin allergy, high creatinine) → *Augmentin* → critical allergy flag;
   *Ibuprofen* → kidney-lab caution.
7. **Consent demo:** login as `dr.bilal` → cannot open Imran. Login as `imran` → **Sharing & Consent** → grant `Dr. Bilal Raza`
   labs-only access → Bilal now sees only lab events. Revoke → access gone. Or use **break-glass** and watch Imran's
   **Access History**.
8. Click **AI Briefing** to see the Timeline Agent's source-cited summary.

## How the AI works

| Agent | Job | Output (Pydantic model) |
|---|---|---|
| **Record Agent** | Structure a free-text doctor note (adds nothing new) | `StructuredNote` |
| **Prescription Agent** | Describe the prescription, list points to double-check | `RxReview` |
| **Conflict Agent** | Explain each rule-engine flag + a question for the clinician | `ConflictReport` |
| **Timeline Agent** | Source-cited patient briefing | `TimelineSummary` |
| **Pharmacy Agent** | Verification checklist for the pharmacist | `PharmacyBrief` |
| **Orchestrator** (`orchestrator.py`) | Routes work, validates output, falls back to rules, checks citations | — |

Grok is reached through CrewAI's OpenAI-compatible provider (`openai/grok-4.7`, base URL `https://api.x.ai/v1`).
Change the model with `XAI_MODEL` in `.env`.

**Guardrails:** synthetic data only · patient name/phone/ID/DOB never sent to the LLM · every AI output is labelled
*AI-generated* · output validated by Pydantic · cited IDs verified against real records · agents cannot change a
prescription or close a flag · medication alerts are *flags for human review, not treatment advice*.

## Deploy on Streamlit Community Cloud

1. Push this project to a **GitHub** repository (`.env` and `secrets.toml` are git-ignored — never commit keys).
2. Go to <https://share.streamlit.io> → **New app** → choose your repo, branch `main`, main file `streamlit_app.py`.
3. **Advanced settings** → Python **3.12** → **Secrets**, paste:
   ```toml
   XAI_API_KEY = "xai-..."
   XAI_MODEL   = "grok-4.7"
   SECRET_KEY  = "a-long-random-string"
   ```
4. Click **Deploy**. The FastAPI backend runs *inside* the Streamlit app, so one deployment is all you need.

> **Note:** SQLite on Streamlit Cloud is **temporary** — data resets when the app restarts or sleeps; the demo data is
> re-created automatically. For persistent data, set `DATABASE_URL` to a free hosted PostgreSQL (Neon, Supabase…).

**Optional – separate backend:** deploy the `Dockerfile` (or `render.yaml`) to Render, then add
`API_URL = "https://<your-backend>.onrender.com"` to the Streamlit secrets.

## Run the tests

```bash
pip install -r requirements-dev.txt
pytest -v
```

12 tests cover login/RBAC, the full doctor→pharmacy→patient workflow (including clarification and partial dispensing),
all safety rules, consent/revocation/break-glass, FHIR export, analytics, and the **CrewAI→Grok pipeline** (using a
local fake Grok server, so no API key is needed).

## Project structure

```
healthbridge-ai/
├── streamlit_app.py            # App entry point: login, sidebar, role routing
├── frontend/
│   ├── api_client.py           # HTTP client for the backend
│   ├── backend_runner.py       # Starts FastAPI inside Streamlit (or uses API_URL)
│   ├── ui.py                   # Shared widgets: timeline chart, alert cards, AI banner
│   └── views/                  # doctor.py · pharmacist.py · patient.py · admin.py
├── backend/
│   ├── main.py                 # FastAPI app
│   ├── config.py · database.py · models.py · schemas.py · security.py · audit.py
│   ├── serializers.py · seed.py
│   ├── routers/                # auth · patients · prescriptions · timeline · consent · admin · ai
│   ├── services/               # drug_data · conflict_rules · access · timeline_service · fhir
│   └── agents/                 # llm · output_models · crew_agents · orchestrator · fallbacks
├── tests/                      # conftest · test_workflow · test_crewai_agents
├── docs/CODE_GUIDE.md          # Beginner's guide: every file explained
├── requirements.txt · requirements-dev.txt · Dockerfile · render.yaml · .env.example
└── .streamlit/                 # config.toml · secrets.toml.example
```

New to the code? Read **[docs/CODE_GUIDE.md](docs/CODE_GUIDE.md)** — it explains every file step by step.

## Roadmap

| Version | Focus |
|---|---|
| **V0 (this repo)** | End-to-end workflow, agents, consent, audit, FHIR export |
| **V1 – Pilot** | PostgreSQL, stronger auth (MFA), real accounts, notifications by SMS/WhatsApp, admin console |
| **V2 – Interoperability** | Full FHIR APIs, standard vocabularies (RxNorm/LOINC), clinic/lab/pharmacy integrations |
| **V3 – Document intelligence** | OCR for paper records with a human-verification queue |
| **V4 – Network platform** | Multi-tenant scale, mobile app, referrals, UMR integration |

## Limitations (be honest in your demo)

- The drug-safety knowledge base in `drug_data.py` is a small **demo list**, not a licensed clinical database.
- No identity-provider integration, MFA, encryption at rest, or regulatory compliance work has been done.
- Positioned as an interoperability layer for participating private providers — it does **not** replace national
  UMR / "One Patient, One ID" infrastructure.

## License

Add a license of your choice (e.g. MIT) before publishing.
#   r  
 