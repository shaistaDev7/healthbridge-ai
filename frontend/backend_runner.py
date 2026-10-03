"""
backend_runner.py - decides WHERE the FastAPI backend lives.

Two modes:
  1. API_URL is set (environment variable or Streamlit secret)
       -> use that remote backend (e.g. deployed on Render). Production style.
  2. API_URL is not set
       -> start FastAPI INSIDE this Streamlit process, in a background thread.
          This lets you deploy the whole project on Streamlit Community Cloud
          (which can only run ONE app) and also makes local testing easy.

`@st.cache_resource` makes sure the backend starts only once, not on every click.
"""
import os
import socket
import threading
import time

import requests
import streamlit as st


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _configured_url() -> str:
    url = os.getenv("API_URL", "")
    if not url:
        try:
            url = st.secrets.get("API_URL", "")  # .streamlit/secrets.toml or Cloud "Secrets"
        except Exception:
            url = ""
    return url.rstrip("/")


@st.cache_resource(show_spinner="Starting HealthBridge backend...")
def get_backend_url() -> str:
    url = _configured_url()
    if url:
        return url

    # Copy Streamlit Cloud secrets (XAI_API_KEY etc.) into environment variables
    # BEFORE the backend reads its settings.
    for key in ("XAI_API_KEY", "XAI_MODEL", "XAI_BASE_URL", "SECRET_KEY", "DATABASE_URL", "AI_ENABLED"):
        try:
            if key in st.secrets and key not in os.environ:
                os.environ[key] = str(st.secrets[key])
        except Exception:
            pass

    import uvicorn

    from backend.main import app

    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()

    base = f"http://127.0.0.1:{port}"
    for _ in range(60):  # wait up to ~15 seconds
        try:
            if requests.get(base + "/health", timeout=1).ok:
                return base
        except requests.RequestException:
            time.sleep(0.25)
    raise RuntimeError("Backend did not start in time")
