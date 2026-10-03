"""
api_client.py - how the Streamlit app talks to the FastAPI backend.

The frontend NEVER touches the database. It only sends HTTP requests:
    api.get("/patients")        ->  GET  http://backend/patients
    api.post("/prescriptions", json={...})
The login token is attached to every request automatically.
"""
from typing import Any, Optional

import requests


class APIError(Exception):
    """Raised when the backend answers with an error (4xx/5xx)."""

    def __init__(self, status_code: int, detail: Any):
        self.status_code = status_code
        self.detail = detail
        super().__init__(self.message)

    @property
    def message(self) -> str:
        d = self.detail
        if isinstance(d, dict):
            return d.get("message", str(d))
        if isinstance(d, list):  # FastAPI validation errors
            return "; ".join(f"{'.'.join(str(x) for x in e.get('loc', [])[1:])}: {e.get('msg')}" for e in d)
        return str(d)


class API:
    def __init__(self, base_url: str, token: Optional[str] = None):
        self.base_url = base_url.rstrip("/")
        self.token = token

    def _request(self, method: str, path: str, **kwargs) -> Any:
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        try:
            resp = requests.request(method, self.base_url + path, headers=headers, timeout=120, **kwargs)
        except requests.ConnectionError:
            raise APIError(503, "Cannot reach the backend. Is it running?")
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail", resp.text)
            except ValueError:
                detail = resp.text
            raise APIError(resp.status_code, detail)
        return resp.json()

    def get(self, path: str, **params) -> Any:
        return self._request("GET", path, params={k: v for k, v in params.items() if v is not None})

    def post(self, path: str, json: Optional[dict] = None) -> Any:
        return self._request("POST", path, json=json or {})

    def patch(self, path: str, json: dict) -> Any:
        return self._request("PATCH", path, json=json)

    def delete(self, path: str) -> Any:
        return self._request("DELETE", path)

    def login(self, username: str, password: str) -> dict:
        data = self._request("POST", "/auth/login", json={"username": username, "password": password})
        self.token = data["access_token"]
        return data["user"]
