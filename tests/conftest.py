"""
conftest.py - shared test setup (pytest reads this file automatically).

Important: we point DATABASE_URL at a temporary file BEFORE the app is imported,
so tests never touch your real healthbridge.db. AI is switched off so tests run
offline and are deterministic.
"""
import os
import tempfile

import pytest

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["XAI_API_KEY"] = ""
os.environ["AUTO_SEED"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from backend.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:  # "with" runs the start-up (creates tables + seeds data)
        yield c


def login(client, username, password):
    r = client.post("/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture(scope="session")
def doctor(client):
    return login(client, "dr.ayesha", "doctor123")


@pytest.fixture(scope="session")
def doctor2(client):
    return login(client, "dr.bilal", "doctor123")


@pytest.fixture(scope="session")
def pharm_abc(client):
    return login(client, "pharm.abc", "pharma123")


@pytest.fixture(scope="session")
def pharm_medi(client):
    return login(client, "pharm.medi", "pharma123")


@pytest.fixture(scope="session")
def admin(client):
    return login(client, "admin", "admin123")
