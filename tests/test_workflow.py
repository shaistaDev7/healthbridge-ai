"""
test_workflow.py - end-to-end tests of the PRD demo script and safety rules.

Run:  pytest -v
Tests share one temporary database, so they are written to run in order.
"""
from tests.conftest import login

ABC, MEDI = 3, 4  # pharmacy organization ids created by seed.py


def find_patient(client, headers, name):
    r = client.get("/patients/search", params={"q": name}, headers=headers)
    assert r.status_code == 200
    return r.json()[0]["id"]


# ------------------------------------------------------------------ auth ----
def test_login_and_roles(client, doctor, pharm_abc, admin):
    assert client.get("/auth/me", headers=doctor).json()["role"] == "doctor"
    assert client.get("/auth/me", headers=pharm_abc).json()["role"] == "pharmacist"
    assert client.post("/auth/login", json={"username": "dr.ayesha", "password": "wrong"}).status_code == 401
    assert client.get("/patients").status_code == 401           # no token
    assert client.get("/admin/users", headers=doctor).status_code == 403  # wrong role
    assert client.get("/admin/users", headers=admin).status_code == 200


# ------------------------------------------- full doctor -> pharmacy -> patient --
def test_full_prescription_workflow(client, doctor, pharm_abc, pharm_medi):
    # 1. doctor creates patient (+ portal login)
    r = client.post("/patients", headers=doctor, json={
        "full_name": "Test Patient", "dob": "1990-01-01", "gender": "male", "allergies": [], "chronic_conditions": []})
    assert r.status_code == 201
    pid = r.json()["patient"]["id"]
    creds = r.json()["portal_login"]
    patient_h = login(client, creds["username"], creds["temporary_password"])

    # 2. consultation
    r = client.post(f"/patients/{pid}/encounters", headers=doctor,
                    json={"chief_complaint": "Cough", "diagnosis": "Common cold"})
    assert r.status_code == 201

    # 3. prescription (quantity auto-calculated: 1 x TDS x 5 days = 15)
    r = client.post("/prescriptions", headers=doctor, json={
        "patient_id": pid, "pharmacy_id": ABC,
        "items": [{"medicine": "Paracetamol", "strength": "500 mg", "dose": 1, "frequency": "TDS", "duration_days": 5}]})
    assert r.status_code == 201, r.text
    rx = r.json()["prescription"]
    assert rx["items"][0]["quantity"] == 15 and rx["status"] == "sent"

    # 4. pharmacy inbox: right pharmacy sees it, other pharmacy does not
    inbox = client.get("/prescriptions", headers=pharm_abc, params={"status": "sent"}).json()
    assert rx["id"] in [x["id"] for x in inbox]
    other = client.get("/prescriptions", headers=pharm_medi).json()
    assert rx["id"] not in [x["id"] for x in other]
    assert client.post(f"/prescriptions/{rx['id']}/decision", headers=pharm_medi,
                       json={"decision": "verify"}).status_code == 403

    # 5. cannot dispense before verification
    item_id = rx["items"][0]["id"]
    assert client.post(f"/prescriptions/{rx['id']}/dispense", headers=pharm_abc,
                       json={"lines": [{"item_id": item_id, "quantity": 5}]}).status_code == 409

    # 6. clarification loop
    r = client.post(f"/prescriptions/{rx['id']}/decision", headers=pharm_abc,
                    json={"decision": "clarify", "note": "Is 5 days correct?"})
    assert r.json()["status"] == "clarification_requested"
    r = client.post(f"/prescriptions/{rx['id']}/respond", headers=doctor, json={"message": "Yes, 5 days."})
    assert r.json()["status"] == "sent"

    # 7. verify, then partial + full dispensing
    assert client.post(f"/prescriptions/{rx['id']}/decision", headers=pharm_abc,
                       json={"decision": "verify"}).json()["status"] == "verified"
    r = client.post(f"/prescriptions/{rx['id']}/dispense", headers=pharm_abc,
                    json={"lines": [{"item_id": item_id, "quantity": 10}]})
    assert r.json()["status"] == "partially_dispensed"
    too_much = client.post(f"/prescriptions/{rx['id']}/dispense", headers=pharm_abc,
                           json={"lines": [{"item_id": item_id, "quantity": 99}]})
    assert too_much.status_code == 422
    r = client.post(f"/prescriptions/{rx['id']}/dispense", headers=pharm_abc,
                    json={"lines": [{"item_id": item_id, "quantity": 5}]})
    assert r.json()["status"] == "dispensed"

    # 8. timeline has 3+ event types, visible to doctor and patient
    for h in (doctor, patient_h):
        events = client.get(f"/patients/{pid}/timeline", headers=h).json()["events"]
        types = {e["type"] for e in events}
        assert {"consultation", "prescription", "dispensing"} <= types

    # 9. notifications reached the patient
    notes = client.get("/notifications", headers=patient_h).json()
    assert any("Prescription" in n["title"] or "Medicine" in n["title"] for n in notes)

    # 10. pharmacist can NOT read the full timeline
    assert client.get(f"/patients/{pid}/timeline", headers=pharm_medi).status_code == 403


# ------------------------------------------------------- safety rule engine --
def test_strength_discrepancy_imran(client, doctor):
    pid = find_patient(client, doctor, "Imran")
    body = {"patient_id": pid, "pharmacy_id": MEDI,
            "items": [{"medicine": "Metformin", "strength": "1000 mg", "dose": 1, "frequency": "BD", "duration_days": 30}]}
    # high severity -> must be acknowledged by the doctor
    r = client.post("/prescriptions", headers=doctor, json=body)
    assert r.status_code == 409
    alerts = r.json()["detail"]["alerts"]
    assert any(a["kind"] == "strength_discrepancy" and a["severity"] == "high" for a in alerts)
    assert alerts[0]["source_event_ids"], "alert must cite the earlier prescription"

    body["acknowledge_alerts"] = True
    r = client.post("/prescriptions", headers=doctor, json=body)
    assert r.status_code == 201
    stored = client.get(f"/patients/{pid}/alerts", headers=doctor).json()
    assert any(a["kind"] == "strength_discrepancy" and a["status"] == "open" for a in stored)

    # human review of the flag
    alert_id = next(a["id"] for a in stored if a["kind"] == "strength_discrepancy")
    r = client.post(f"/alerts/{alert_id}/review", headers=doctor,
                    json={"status": "acknowledged", "note": "Dose increase intended after HbA1c review"})
    assert r.json()["status"] == "acknowledged" and r.json()["reviewed_by"]


def test_allergy_and_kidney_rules(client, doctor):
    pid = find_patient(client, doctor, "Fatima")
    r = client.post("/prescriptions/check", headers=doctor, json={
        "patient_id": pid, "items": [{"medicine": "Augmentin", "strength": "625 mg", "frequency": "BD", "duration_days": 5}]})
    kinds = {a["kind"]: a["severity"] for a in r.json()["alerts"]}
    assert kinds.get("allergy") == "critical"          # brand name Augmentin -> amoxicillin -> penicillin class

    r = client.post("/prescriptions/check", headers=doctor, json={
        "patient_id": pid, "items": [{"medicine": "Ibuprofen", "strength": "400 mg", "frequency": "BD", "duration_days": 5}]})
    assert any(a["kind"] == "lab_caution" for a in r.json()["alerts"])  # high creatinine + NSAID


def test_dose_limit_and_interaction(client, doctor):
    pid = find_patient(client, doctor, "Ahmed")
    r = client.post("/prescriptions/check", headers=doctor, json={
        "patient_id": pid, "items": [{"medicine": "Paracetamol", "strength": "1000 mg", "dose": 2, "frequency": "TDS", "duration_days": 3}]})
    assert any(a["kind"] == "dose_limit" for a in r.json()["alerts"])  # 6000 mg/day

    r = client.post("/prescriptions/check", headers=doctor, json={
        "patient_id": pid, "items": [
            {"medicine": "Warfarin", "strength": "5 mg", "frequency": "OD", "duration_days": 10},
            {"medicine": "Diclofenac", "strength": "50 mg", "frequency": "BD", "duration_days": 5}]})
    assert any(a["kind"] == "interaction" for a in r.json()["alerts"])


# ---------------------------------------------------------------- consent ----
def test_consent_scope_revoke_and_break_glass(client, doctor, doctor2):
    pid = find_patient(client, doctor, "Imran")
    # doctor 2 (other clinic) has no access
    assert client.get(f"/patients/{pid}/timeline", headers=doctor2).status_code == 403
    assert client.get("/patients", headers=doctor2).json() == []

    # patient grants access to labs only
    patient_h = login(client, "imran", "patient123")
    doctors = client.get("/directory/doctors", headers=patient_h).json()
    bilal = next(d["id"] for d in doctors if "Bilal" in d["full_name"])
    r = client.post("/consents", headers=patient_h, json={"grantee_user_id": bilal, "scope": "labs", "days": 7})
    assert r.status_code == 201
    consent_id = r.json()["id"]

    events = client.get(f"/patients/{pid}/timeline", headers=doctor2).json()["events"]
    assert events and {e["type"] for e in events} == {"lab"}          # scope respected
    # labs-only consent is not enough to prescribe
    assert client.post("/prescriptions/check", headers=doctor2, json={
        "patient_id": pid, "items": [{"medicine": "Panadol", "strength": "500 mg"}]}).status_code == 403

    # revoke -> access gone
    assert client.delete(f"/consents/{consent_id}", headers=patient_h).status_code == 200
    assert client.get(f"/patients/{pid}/timeline", headers=doctor2).status_code == 403

    # break-glass: needs a real reason, gives access, notifies the patient, is audited
    assert client.post("/consents/break-glass", headers=doctor2,
                       json={"patient_id": pid, "reason": "short"}).status_code == 422
    r = client.post("/consents/break-glass", headers=doctor2,
                    json={"patient_id": pid, "reason": "Unconscious in emergency room, history needed"})
    assert r.status_code == 201
    assert client.get(f"/patients/{pid}/timeline", headers=doctor2).status_code == 200
    notes = client.get("/notifications", headers=patient_h).json()
    assert any("Emergency" in n["title"] for n in notes)
    log = client.get(f"/patients/{pid}/access-log", headers=patient_h).json()
    assert any(row["action"] == "consent.break_glass" for row in log)


# --------------------------------------------------------------------- AI ----
def test_ai_summary_fallback_is_traceable(client, doctor):
    pid = find_patient(client, doctor, "Fatima")
    r = client.post(f"/patients/{pid}/ai/summary", headers=doctor)
    assert r.status_code == 200
    data = r.json()
    assert data["ai_used"] is False and "rule-based" in data["generated_by"]
    assert "does not diagnose" in data["disclaimer"]
    for kp in data["data"]["key_points"]:
        assert kp["source_ids"] and all(s in data["sources"] for s in kp["source_ids"])

    r = client.post("/ai/structure-note", headers=doctor,
                    json={"text": "Chief complaint: cough 3 days\nDiagnosis: bronchitis\nPlan: rest and fluids"})
    assert r.json()["data"]["diagnosis_as_stated"].lower().startswith("bronchitis")


# ------------------------------------------------------------ FHIR + admin ----
def test_fhir_and_history(client, doctor):
    pid = find_patient(client, doctor, "Ahmed")
    bundle = client.get(f"/patients/{pid}/fhir", headers=doctor).json()
    kinds = {e["resource"]["resourceType"] for e in bundle["entry"]}
    assert {"Patient", "Encounter", "MedicationRequest", "MedicationDispense"} <= kinds

    patient_h = login(client, "ahmed", "patient123")
    r = client.post(f"/patients/{pid}/history", headers=patient_h,
                    json={"title": "Appendix surgery", "approx_date": "2015-06-01"})
    assert r.status_code == 201 and r.json()["verified"] is False
    events = client.get(f"/patients/{pid}/timeline", headers=doctor).json()["events"]
    hist = [e for e in events if e["type"] == "history"]
    assert hist and "UNVERIFIED" in hist[0]["status"]


def test_admin_analytics_and_audit(client, admin, doctor):
    a = client.get("/admin/analytics", headers=admin).json()
    assert a["totals"]["patients"] >= 5 and a["totals"]["prescriptions"] >= 4
    audit = client.get("/admin/audit", headers=admin).json()
    assert any(row["action"] == "prescription.create" for row in audit)
    assert client.get("/prescriptions", headers=admin).status_code == 403  # admin can't read clinical data
