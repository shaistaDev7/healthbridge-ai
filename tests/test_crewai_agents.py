"""
test_crewai_agents.py - tests the REAL CrewAI -> "Grok" pipeline without a real key.

We start a tiny fake server on localhost that speaks the OpenAI/xAI chat format
and returns canned JSON. Then we point XAI_BASE_URL at it. Everything else
(CrewAI Agent/Task/Crew, Pydantic validation, orchestrator, source-ID checking)
is the real code. This proves the wiring works; real Grok answers will differ in
wording but must fit the same Pydantic models.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest


CANNED = {
    "Clinical Record Agent": {
        "chief_complaint": "Cough", "symptoms": ["cough"], "history_summary": "3 days",
        "diagnosis_as_stated": "", "plan_as_stated": "", "missing_information": []},
    "Prescription Review Agent": {"plain_summary": "Metformin 1000 mg twice daily.", "points_to_check": ["confirm dose"]},
    "Medication Conflict Agent": {"explanations": [
        {"alert_index": 0, "explanation": "Strength differs from earlier Rx.", "question_for_clinician": "Is the change intended?"}]},
    "Patient Timeline Agent": {
        "headline": "Diabetic patient on metformin",
        "key_points": [{"text": "Started metformin", "source_ids": ["RX-00003", "FAKE-99999"]}],
        "current_medications": ["metformin"], "open_flags": []},
    "Pharmacy Workflow Agent": {"summary": "One line", "verification_checklist": ["check id"], "concerns": []},
}


class FakeGrok(BaseHTTPRequestHandler):
    def log_message(self, *a):  # silence
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        text = json.dumps(body["messages"])
        content = next((json.dumps(v) for k, v in CANNED.items() if k in text), "{}")
        reply = {"id": "x", "object": "chat.completion", "created": 0, "model": body.get("model", "grok-4.7"),
                 "choices": [{"index": 0, "finish_reason": "stop",
                              "message": {"role": "assistant", "content": content}}],
                 "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}
        data = json.dumps(reply).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture()
def fake_grok(monkeypatch):
    server = HTTPServer(("127.0.0.1", 0), FakeGrok)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    from backend.config import settings
    monkeypatch.setattr(settings, "XAI_API_KEY", "test-key")
    monkeypatch.setattr(settings, "XAI_BASE_URL", f"http://127.0.0.1:{server.server_port}/v1")
    yield
    server.shutdown()


def test_crew_runs_and_validates_output(client, doctor, fake_grok):
    r = client.post("/ai/structure-note", headers=doctor, json={"text": "Cough for three days, no fever."})
    assert r.status_code == 200
    out = r.json()
    assert out["ai_used"] is True, out["note"]
    assert "grok-4.7" in out["generated_by"]
    assert out["data"]["chief_complaint"] == "Cough"


def test_timeline_citations_are_verified(client, doctor, fake_grok):
    pid = client.get("/patients/search", params={"q": "Imran"}, headers=doctor).json()[0]["id"]
    out = client.post(f"/patients/{pid}/ai/summary", headers=doctor).json()
    assert out["ai_used"] is True, out["note"]
    cited = [s for kp in out["data"]["key_points"] for s in kp["source_ids"]]
    assert "FAKE-99999" not in cited            # invented ID removed
    assert all(c in out["sources"] for c in cited)


def test_two_agent_prescription_review_and_fallback_on_failure(client, doctor, monkeypatch, fake_grok):
    pid = client.get("/patients/search", params={"q": "Imran"}, headers=doctor).json()[0]["id"]
    r = client.post("/prescriptions", headers=doctor, json={
        "patient_id": pid, "pharmacy_id": 4, "acknowledge_alerts": True,
        "items": [{"medicine": "Metformin", "strength": "2000 mg", "frequency": "BD", "duration_days": 10}]})
    rx_id = r.json()["prescription"]["id"]
    out = client.post(f"/prescriptions/{rx_id}/ai-review", headers=doctor).json()
    assert out["ai_used"] is True, out["note"]
    assert out["data"]["review"]["plain_summary"]
    assert out["alert_records"][0]["ai_explanation"]        # stored on the alert

    # Grok unreachable -> graceful fallback (core workflow must not break)
    from backend.config import settings
    monkeypatch.setattr(settings, "XAI_BASE_URL", "http://127.0.0.1:9/v1")
    out = client.post(f"/prescriptions/{rx_id}/ai-brief", headers=doctor).json()
    assert out["ai_used"] is False and "rule-based" in out["generated_by"]
