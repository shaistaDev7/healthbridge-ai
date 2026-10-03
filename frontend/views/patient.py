"""
views/patient.py - the PATIENT portal.

The patient sees their own timeline, medicines, prescription status, and
controls privacy: who may see the record, and who already looked at it.
"""
import json

import pandas as pd
import streamlit as st

from frontend import ui
from frontend.api_client import API, APIError


def my_record(api: API, user: dict) -> None:
    pid = user["patient_id"]
    st.title(f"Hello, {user['full_name']}")
    profile = api.get(f"/patients/{pid}")
    st.markdown(f"**HealthBridge ID:** `{profile['hb_id']}` · blood group {profile['blood_group'] or '—'}  \n"
                f"⚠️ Allergies: {', '.join(profile['allergies']) or 'none recorded'}")

    tab_tl, tab_rx, tab_ai = st.tabs(["Timeline", "Prescriptions & medicines", "AI summary"])
    with tab_tl:
        events = api.get(f"/patients/{pid}/timeline")["events"]
        ui.timeline_chart(events)
        ui.timeline_cards(events)

    with tab_rx:
        meds = api.get(f"/patients/{pid}/medications")
        st.subheader("Medicines I should be taking now")
        if meds:
            st.dataframe(pd.DataFrame(meds)[["medicine", "strength", "frequency", "ends_on", "days_left"]],
                         hide_index=True, width="stretch")
        else:
            st.caption("No active medicines.")
        st.subheader("My prescriptions")
        for r in api.get("/prescriptions"):
            with st.container(border=True):
                st.markdown(f"**{r['rx_number']}** · {r['pharmacy_name']} · {ui.fmt_dt(r['issued_at'])}  "
                            + ui.status_badge(r["status"]), unsafe_allow_html=True)
                st.dataframe(pd.DataFrame(r["items"])[["medicine", "strength", "frequency", "duration_days",
                                                        "quantity", "dispensed_quantity"]], hide_index=True,
                             width="stretch")

    with tab_ai:
        st.caption("A plain overview generated from your own record. It is not medical advice.")
        if st.button("Generate my summary", type="primary"):
            with st.spinner("Preparing..."):
                st.session_state["my_summary"] = api.post(f"/patients/{pid}/ai/summary")
        if "my_summary" in st.session_state:
            ui.render_ai_summary(st.session_state["my_summary"])


def consent_page(api: API, user: dict) -> None:
    st.title("Who can see my record")
    st.caption("You decide. Doctors at the clinic that treated you can already see your record; "
               "other doctors need your permission. You can take permission back at any time.")
    doctors = api.get("/directory/doctors")
    with st.form("grant"):
        labels = {d["id"]: f"{d['full_name']} — {d['organization_name']}" for d in doctors}
        who = st.selectbox("Share with", list(labels), format_func=lambda x: labels[x])
        scope = st.selectbox("What can they see?", ["all", "prescriptions", "labs"],
                             format_func={"all": "Everything", "prescriptions": "Prescriptions & dispensing only",
                                          "labs": "Lab results only"}.get)
        days = st.slider("For how many days?", 1, 180, 30)
        if st.form_submit_button("Grant access", type="primary"):
            try:
                api.post("/consents", {"grantee_user_id": who, "scope": scope, "days": days})
                st.success("Access granted.")
                st.rerun()
            except APIError as e:
                st.error(e.message)

    st.subheader("Current and past permissions")
    for c in api.get("/consents/mine"):
        c1, c2 = st.columns([4, 1])
        tag = ui.badge("EMERGENCY ACCESS", "#dc2626") if c["kind"] == "break_glass" else ""
        c1.markdown(f"{tag} **{c['grantee_name']}** · {c['scope']} · {c['status']} · until "
                    f"{ui.fmt_dt(c['expires_at']) if c['expires_at'] else '—'}"
                    + (f"  \n<span class='hb-muted'>Reason: {c['reason']}</span>" if c["reason"] else ""),
                    unsafe_allow_html=True)
        if c["status"] == "active" and c2.button("Revoke", key=f"rev_{c['id']}"):
            api.delete(f"/consents/{c['id']}")
            st.rerun()


def access_log(api: API, user: dict) -> None:
    st.title("Who looked at my data?")
    rows = api.get(f"/patients/{user['patient_id']}/access-log")
    if rows:
        df = pd.DataFrame(rows)
        df["timestamp"] = df["timestamp"].map(ui.fmt_dt)
        st.dataframe(df, hide_index=True, width="stretch")
    else:
        st.info("No activity yet.")


def add_history(api: API, user: dict) -> None:
    st.title("Add past medical history")
    st.caption("For older paper records. Entries are clearly labelled **patient reported / unverified** "
               "until a doctor confirms them.")
    with st.form("hist"):
        title = st.text_input("What happened? *", placeholder="Appendix surgery")
        when = st.text_input("Approximate date (YYYY-MM-DD)")
        notes = st.text_area("Details (hospital, medicines, doctor…)")
        if st.form_submit_button("Add to my timeline", type="primary"):
            try:
                api.post(f"/patients/{user['patient_id']}/history",
                         {"title": title, "notes": notes, "approx_date": when or None})
                st.success("Added as unverified history.")
            except APIError as e:
                st.error(e.message)


def export_page(api: API, user: dict) -> None:
    st.title("Export my record (FHIR)")
    st.caption("HL7 FHIR R4 Bundle — the international format that lets other systems read your data.")
    bundle = api.get(f"/patients/{user['patient_id']}/fhir")
    st.metric("Resources in bundle", bundle["total"])
    st.download_button("⬇️ Download FHIR JSON", json.dumps(bundle, indent=2), "my-healthbridge-record.fhir.json",
                       "application/fhir+json")
    with st.expander("Preview"):
        st.json(bundle, expanded=1)


PAGES = {
    "My Record": my_record,
    "Sharing & Consent": consent_page,
    "Access History": access_log,
    "Add Past History": add_history,
    "Export (FHIR)": export_page,
}
