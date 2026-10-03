"""
views/admin.py - platform administrator: numbers, user accounts, audit trail.
(Admins can NOT open patient records.)
"""
import pandas as pd
import streamlit as st

from frontend import ui
from frontend.api_client import API, APIError


def analytics(api: API, user: dict) -> None:
    st.title("Platform analytics")
    s = api.get("/admin/analytics")
    t = s["totals"]
    cols = st.columns(len(t))
    for col, (k, v) in zip(cols, t.items()):
        col.metric(k.replace("_", " ").title(), v)
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Prescriptions by status")
        if s["prescriptions_by_status"]:
            st.bar_chart(pd.Series(s["prescriptions_by_status"]))
    with c2:
        st.subheader("Safety flags by severity")
        if s["alerts_by_severity"]:
            st.bar_chart(pd.Series(s["alerts_by_severity"]))
    st.subheader("Prescriptions per day (14 days)")
    if s["prescriptions_per_day"]:
        st.line_chart(pd.Series(s["prescriptions_per_day"]))
    st.metric("Average minutes: sent → verified", s["avg_minutes_to_verify"] or "—")


def users_page(api: API, user: dict) -> None:
    st.title("Users & organisations")
    users = api.get("/admin/users")
    st.dataframe(pd.DataFrame(users)[["id", "username", "full_name", "role", "organization_name", "status"]],
                 hide_index=True, width="stretch")

    orgs = api.get("/admin/organizations")
    with st.form("new_user"):
        st.subheader("Create account")
        c = st.columns(3)
        username = c[0].text_input("Username")
        full_name = c[1].text_input("Full name")
        password = c[2].text_input("Password (min 6)", type="password")
        role = c[0].selectbox("Role", ["doctor", "pharmacist", "admin"])
        org_map = {o["id"]: f"{o['name']} ({o['type']})" for o in orgs}
        org = c[1].selectbox("Organisation", list(org_map), format_func=org_map.get)
        if st.form_submit_button("Create", type="primary"):
            try:
                api.post("/admin/users", {"username": username, "password": password, "full_name": full_name,
                                          "role": role, "organization_id": org})
                st.success("Created.")
                st.rerun()
            except APIError as e:
                st.error(e.message)

    st.subheader("Enable / disable")
    target = st.selectbox("Account", [u["id"] for u in users],
                          format_func=lambda i: next(f"{u['username']} ({u['status']})" for u in users if u["id"] == i))
    c1, c2 = st.columns(2)
    for col, status in ((c1, "active"), (c2, "disabled")):
        if col.button(f"Set {status}", key=f"st_{status}"):
            try:
                api.patch(f"/admin/users/{target}", {"status": status})
                st.rerun()
            except APIError as e:
                st.error(e.message)


def audit_page(api: API, user: dict) -> None:
    st.title("Audit trail")
    rows = api.get("/admin/audit", limit=500)
    df = pd.DataFrame(rows)
    if df.empty:
        st.info("No events.")
        return
    df["timestamp"] = df["timestamp"].map(ui.fmt_dt)
    action = st.multiselect("Filter by action", sorted(df["action"].unique()))
    if action:
        df = df[df["action"].isin(action)]
    st.dataframe(df, hide_index=True, width="stretch")


PAGES = {"Analytics": analytics, "Users": users_page, "Audit Trail": audit_page}
