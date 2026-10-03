"""
streamlit_app.py - the ENTRY POINT of the whole web app.

Run locally :  streamlit run streamlit_app.py
Deploy      :  push to GitHub -> share.streamlit.io -> pick this file.

What this file does, top to bottom:
  1. page settings + styling
  2. make sure a backend is available (frontend/backend_runner.py)
  3. if nobody is logged in -> show the login screen
  4. otherwise -> show a sidebar menu and the page that matches the user's role

Streamlit re-runs this whole script on every click. `st.session_state` is the
memory that survives between runs (login token, selected patient, ...).
"""
import streamlit as st

st.set_page_config(page_title="HealthBridge AI", page_icon="🩺", layout="wide")

from frontend import ui  # noqa: E402
from frontend.api_client import API, APIError  # noqa: E402
from frontend.backend_runner import get_backend_url  # noqa: E402
from frontend.views import admin, doctor, patient, pharmacist  # noqa: E402

ROLE_PAGES = {
    "doctor": doctor.PAGES,
    "pharmacist": pharmacist.PAGES,
    "patient": patient.PAGES,
    "admin": admin.PAGES,
}

DEMO_ACCOUNTS = [
    ("Doctor (Shifa Clinic)", "dr.ayesha", "doctor123"),
    ("Doctor (City Care, needs consent)", "dr.bilal", "doctor123"),
    ("Pharmacist (ABC)", "pharm.abc", "pharma123"),
    ("Pharmacist (MediPlus)", "pharm.medi", "pharma123"),
    ("Patient: Imran Shah", "imran", "patient123"),
    ("Patient: Fatima Noor", "fatima", "patient123"),
    ("Admin", "admin", "admin123"),
]


def login_screen(api: API) -> None:
    st.markdown("# 🩺 HealthBridge AI")
    st.markdown("**One consent-based medical timeline** connecting doctors, pharmacies and patients.")
    st.warning("Prototype with **synthetic data only**. Not for clinical use.")
    left, right = st.columns([1, 1])
    with left:
        with st.form("login"):
            username = st.text_input("Username", key="login_user")
            password = st.text_input("Password", type="password", key="login_pass")
            if st.form_submit_button("Sign in", type="primary"):
                _do_login(api, username, password)
    with right:
        st.markdown("##### Demo accounts")
        for label, u, p in DEMO_ACCOUNTS:
            if st.button(f"{label} — `{u}`", key=f"demo_{u}", width="stretch"):
                _do_login(api, u, p)


def _do_login(api: API, username: str, password: str) -> None:
    try:
        user = api.login(username.strip().lower(), password)
    except APIError as e:
        st.error(e.message)
        return
    st.session_state.token = api.token
    st.session_state.user = user
    st.session_state.page = next(iter(ROLE_PAGES[user["role"]]))
    st.rerun()


def sidebar(api: API, user: dict) -> str:
    pages = list(ROLE_PAGES[user["role"]]) + ["Notifications"]
    with st.sidebar:
        st.markdown("## 🩺 HealthBridge AI")
        st.markdown(f"**{user['full_name']}**  \n{ui.badge(user['role'], '#0f9d8a')} "
                    f"<span class='hb-muted'>{user.get('organization_name') or ''}</span>", unsafe_allow_html=True)
        try:
            unread = sum(1 for n in api.get("/notifications") if not n["is_read"])
            ai = api.get("/ai/status")
        except APIError:
            unread, ai = 0, {"mode": "unknown", "ai_available": False}
        labels = {p: (f"{p} ({unread})" if p == "Notifications" and unread else p) for p in pages}
        current = st.session_state.get("page", pages[0])
        choice = st.radio("Menu", pages, index=pages.index(current) if current in pages else 0,
                          format_func=labels.get, label_visibility="collapsed")
        st.session_state.page = choice
        st.divider()
        st.caption(("🟢 " if ai["ai_available"] else "🟡 ") + f"AI mode: {ai['mode']}")
        if st.button("Sign out"):
            for k in list(st.session_state.keys()):
                del st.session_state[k]
            st.rerun()
    return choice


def notifications_page(api: API, user: dict) -> None:
    st.title("Notifications")
    rows = api.get("/notifications")
    if rows and any(not n["is_read"] for n in rows):
        if st.button("Mark all as read"):
            api.post("/notifications/read-all")
            st.rerun()
    if not rows:
        st.info("No notifications.")
    for n in rows:
        mark = "🔵 " if not n["is_read"] else ""
        st.markdown(f"<div class='hb-card'>{mark}<b>{n['title']}</b> "
                    f"<span class='hb-muted'>{ui.fmt_dt(n['created_at'])}</span><br>{n['message']}</div>",
                    unsafe_allow_html=True)


def main() -> None:
    ui.inject_css()
    base_url = get_backend_url()
    api = API(base_url, st.session_state.get("token"))

    user = st.session_state.get("user")
    if not user:
        login_screen(api)
        return

    page = sidebar(api, user)
    try:
        if page == "Notifications":
            notifications_page(api, user)
        else:
            ROLE_PAGES[user["role"]][page](api, user)
    except APIError as e:
        if e.status_code == 401:  # token expired
            st.session_state.clear()
            st.rerun()
        st.error(e.message)


main()
