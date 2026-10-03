"""
views/doctor.py - everything a DOCTOR sees.

Pages: Dashboard, Patients, Patient Workspace, Prescriptions, Review Queue.
Each page is a function; `render()` at the bottom picks the right one.
"""
import pandas as pd
import streamlit as st

from frontend import ui
from frontend.api_client import API, APIError

FREQ_CHOICES = ["OD", "BD", "TDS", "QID", "HS", "PRN"]
FREQ_HELP = "OD=once daily · BD=twice · TDS=3 times · QID=4 times · HS=bedtime · PRN=when needed"


# ================================================================ Dashboard ==
def dashboard(api: API, user: dict) -> None:
    st.title(f"Good day, {user['full_name']}")
    st.caption(f"{user['organization_name']} · doctor dashboard")
    stats = api.get("/admin/analytics")
    t = stats["totals"]
    c = st.columns(5)
    c[0].metric("Patients on platform", t["patients"])
    c[1].metric("Prescriptions", t["prescriptions"])
    c[2].metric("Dispensed events", t["dispensings"])
    c[3].metric("Open safety flags", t["open_alerts"])
    c[4].metric("Avg. minutes to verify", stats["avg_minutes_to_verify"] or "—")

    left, right = st.columns(2)
    with left:
        st.subheader("Prescriptions by status")
        if stats["prescriptions_by_status"]:
            df = pd.DataFrame({"status": list(stats["prescriptions_by_status"]),
                               "count": list(stats["prescriptions_by_status"].values())})
            st.bar_chart(df, x="status", y="count")
    with right:
        st.subheader("Safety flags by type")
        if stats["alerts_by_kind"]:
            df = pd.DataFrame({"type": list(stats["alerts_by_kind"]), "count": list(stats["alerts_by_kind"].values())})
            st.bar_chart(df, x="type", y="count")
        else:
            st.info("No safety flags yet.")

    st.subheader("Recent prescriptions from your clinic")
    rxs = api.get("/prescriptions")[:8]
    if rxs:
        st.dataframe(pd.DataFrame([{
            "Rx": r["rx_number"], "Patient": r["patient"]["full_name"], "Pharmacy": r["pharmacy_name"],
            "Status": r["status"].replace("_", " "), "Issued": ui.fmt_dt(r["issued_at"])} for r in rxs]),
            hide_index=True, width="stretch")


# ================================================================= Patients ==
def patients_page(api: API, user: dict) -> None:
    st.title("Patients")
    tab_mine, tab_search, tab_new = st.tabs(["My patients", "Search all", "Register new patient"])

    with tab_mine:
        rows = api.get("/patients")
        if not rows:
            st.info("No patients yet. Register one, or ask a patient to grant you access.")
        for p in rows:
            c1, c2, c3 = st.columns([3, 2, 1])
            c1.markdown(f"**{p['full_name']}**  \n<span class='hb-muted'>{p['hb_id']} · {p['gender']} · {p['dob'] or ''}</span>",
                        unsafe_allow_html=True)
            c2.markdown("" if p["full_access"] else ui.badge("limited (consent scope)", "#d97706"), unsafe_allow_html=True)
            if c3.button("Open", key=f"open_{p['id']}"):
                st.session_state.patient_id = p["id"]
                st.session_state.page = "Patient Workspace"
                st.rerun()

    with tab_search:
        q = st.text_input("Name, HealthBridge ID (HB-…) or phone", key="pt_search")
        if len(q.strip()) >= 2:
            results = api.get("/patients/search", q=q)
            if not results:
                st.warning("No match.")
            for p in results:
                with st.container(border=True):
                    c1, c2 = st.columns([3, 1])
                    c1.markdown(f"**{p['full_name']}** · {p['hb_id']} · {p['gender']} · {p['dob'] or ''}")
                    if p["has_access"]:
                        if c2.button("Open", key=f"sopen_{p['id']}"):
                            st.session_state.patient_id = p["id"]
                            st.session_state.page = "Patient Workspace"
                            st.rerun()
                    else:
                        c2.markdown(ui.badge("no access", "#dc2626"), unsafe_allow_html=True)
                        st.caption("Ask the patient to share their record from their portal (Consent page), "
                                   "or — only in a real emergency — use emergency access below.")
                        with st.expander("🚨 Emergency (break-glass) access"):
                            reason = st.text_area("Reason (min 10 characters; the patient is notified and this is audited)",
                                                  key=f"bg_reason_{p['id']}")
                            if st.button("Request emergency access", key=f"bg_{p['id']}", type="primary"):
                                try:
                                    api.post("/consents/break-glass", {"patient_id": p["id"], "reason": reason})
                                    st.success("Access granted for a limited time. The patient has been notified.")
                                    st.rerun()
                                except APIError as e:
                                    st.error(e.message)

    with tab_new:
        with st.form("new_patient"):
            c1, c2 = st.columns(2)
            name = c1.text_input("Full name *")
            dob = c2.text_input("Date of birth (YYYY-MM-DD)")
            gender = c1.selectbox("Gender", ["male", "female", "other"])
            phone = c2.text_input("Phone")
            blood = c1.text_input("Blood group")
            allergies = c2.text_input("Allergies (comma separated)", help="e.g. penicillin, sulfa")
            conditions = st.text_input("Chronic conditions (comma separated)")
            portal = st.checkbox("Create a patient portal login", value=True)
            submitted = st.form_submit_button("Register patient", type="primary")
        if submitted:
            try:
                res = api.post("/patients", {
                    "full_name": name, "dob": dob or None, "gender": gender, "phone": phone, "blood_group": blood,
                    "allergies": [a for a in allergies.split(",") if a.strip()],
                    "chronic_conditions": [c for c in conditions.split(",") if c.strip()],
                    "create_portal_login": portal})
                st.success(f"Registered {res['patient']['full_name']} — {res['patient']['hb_id']}")
                if res["portal_login"]:
                    st.info(f"Portal login (show once): **{res['portal_login']['username']}** / "
                            f"**{res['portal_login']['temporary_password']}**")
                st.session_state.patient_id = res["patient"]["id"]
            except APIError as e:
                st.error(e.message)


# ========================================================= Patient workspace ==
def workspace(api: API, user: dict) -> None:
    patients = api.get("/patients")
    if not patients:
        st.info("You have no accessible patients yet.")
        return
    labels = {p["id"]: f"{p['full_name']} ({p['hb_id']})" for p in patients}
    ids = list(labels)
    current = st.session_state.get("patient_id")
    idx = ids.index(current) if current in ids else 0
    pid = st.selectbox("Patient", ids, index=idx, format_func=lambda i: labels[i])
    st.session_state.patient_id = pid
    full = next(p["full_access"] for p in patients if p["id"] == pid)

    profile = api.get(f"/patients/{pid}")
    st.title(profile["full_name"])
    allergies = ", ".join(profile["allergies"]) or "none recorded"
    st.markdown(
        f"{profile['hb_id']} · {profile['gender']} · DOB {profile['dob'] or '—'} · blood {profile['blood_group'] or '—'}  \n"
        f"⚠️ **Allergies:** {allergies} · **Conditions:** {', '.join(profile['chronic_conditions']) or 'none recorded'}")
    if not full:
        st.warning("You have LIMITED access (consent scope). Writing notes/prescriptions is disabled.")

    names = ["Timeline", "AI Briefing", "Safety Alerts", "Medications & Labs"]
    if full:
        names += ["New Consultation", "New Prescription", "Add Lab", "Profile"]
    tabs = st.tabs(names)
    t = dict(zip(names, tabs))

    with t["Timeline"]:
        data = api.get(f"/patients/{pid}/timeline")
        events = data["events"]
        c1, c2 = st.columns([2, 3])
        wanted = c1.multiselect("Show event types", data["visible_types"], default=data["visible_types"])
        events = [e for e in events if e["type"] in wanted]
        ui.timeline_chart(events)
        ui.timeline_cards(events)
        for e in events:
            if e["type"] == "history" and "UNVERIFIED" in e["status"] and full:
                if st.button(f"Mark '{e['title']}' as verified", key=f"ver_{e['event_id']}"):
                    api.post(f"/encounters/{int(e['event_id'].split('-')[1])}/verify")
                    st.rerun()

    with t["AI Briefing"]:
        st.caption("Timeline Agent (CrewAI + Grok). A de-identified copy of the visible events is used; "
                   "every statement cites source records.")
        if st.button("Generate AI briefing", type="primary"):
            with st.spinner("Agents are reading the timeline..."):
                try:
                    st.session_state[f"brief_{pid}"] = api.post(f"/patients/{pid}/ai/summary")
                except APIError as e:
                    st.error(e.message)
        if f"brief_{pid}" in st.session_state:
            ui.render_ai_summary(st.session_state[f"brief_{pid}"])

    with t["Safety Alerts"]:
        alerts = api.get(f"/patients/{pid}/alerts") if full else []
        if not full:
            st.info("Alerts need full access.")
        elif not alerts:
            st.success("No safety flags recorded for this patient.")
        for a in alerts:
            ui.show_alerts([a])
            if a["status"] == "open":
                c1, c2, c3 = st.columns([3, 1, 1])
                note = c1.text_input("Review note", key=f"note_{a['id']}", label_visibility="collapsed",
                                     placeholder="Review note (what did you decide?)")
                if c2.button("Acknowledge", key=f"ack_{a['id']}"):
                    api.post(f"/alerts/{a['id']}/review", {"status": "acknowledged", "note": note})
                    st.rerun()
                if c3.button("Dismiss", key=f"dis_{a['id']}"):
                    api.post(f"/alerts/{a['id']}/review", {"status": "dismissed", "note": note})
                    st.rerun()

    with t["Medications & Labs"]:
        meds = api.get(f"/patients/{pid}/medications")
        st.subheader("Current medications")
        if meds:
            st.dataframe(pd.DataFrame(meds)[["medicine", "strength", "frequency", "rx_number", "ends_on", "days_left", "status"]],
                         hide_index=True, width="stretch")
        else:
            st.caption("None active.")
        st.subheader("Lab results")
        labs = api.get(f"/patients/{pid}/labs")
        if labs:
            df = pd.DataFrame(labs)
            st.dataframe(df[["date", "test", "value", "unit", "flag", "source"]], hide_index=True, width="stretch")
            for test, grp in df.groupby("test"):
                if len(grp) > 1:
                    st.line_chart(grp, x="date", y="value", y_label=test)

    if full:
        with t["New Consultation"]:
            _consultation_form(api, pid)
        with t["New Prescription"]:
            _prescription_form(api, pid, profile)
        with t["Add Lab"]:
            with st.form("lab_form"):
                c = st.columns(5)
                test = c[0].text_input("Test", placeholder="Serum Creatinine")
                value = c[1].number_input("Value", value=0.0, format="%.2f")
                unit = c[2].text_input("Unit", placeholder="mg/dL")
                low = c[3].number_input("Ref low", value=0.0, format="%.2f")
                high = c[4].number_input("Ref high", value=0.0, format="%.2f")
                source = st.text_input("Lab / source", placeholder="LabLink Diagnostics")
                if st.form_submit_button("Save lab result", type="primary"):
                    try:
                        res = api.post(f"/patients/{pid}/labs", {
                            "test": test, "value": value, "unit": unit, "source": source,
                            "ref_low": low if high > low else None, "ref_high": high if high > low else None})
                        st.success(f"Saved ({res['flag']}).")
                    except APIError as e:
                        st.error(e.message)
        with t["Profile"]:
            with st.form("profile_form"):
                al = st.text_input("Allergies (comma separated)", ", ".join(profile["allergies"]))
                co = st.text_input("Chronic conditions", ", ".join(profile["chronic_conditions"]))
                if st.form_submit_button("Update profile"):
                    api.patch(f"/patients/{pid}", {"allergies": [a for a in al.split(",") if a.strip()],
                                                    "chronic_conditions": [c for c in co.split(",") if c.strip()]})
                    st.success("Updated.")
                    st.rerun()


def _consultation_form(api: API, pid: int) -> None:
    st.caption("Optional: paste a free-text note and let the Record Agent organise it. You review before saving.")
    st.session_state.setdefault("note_ver", 0)
    raw = st.text_area("Free-text note (optional)", key=f"raw_note_{pid}", height=100,
                       placeholder="e.g. Fever 2 days, sore throat. Throat red. Dx viral pharyngitis. Plan: fluids, paracetamol.")
    if st.button("✨ Structure with AI") and raw.strip():
        with st.spinner("Record Agent working..."):
            try:
                res = api.post("/ai/structure-note", {"text": raw})
                st.session_state.structured = res
                st.session_state.note_ver += 1
                st.rerun()
            except APIError as e:
                st.error(e.message)
    s = st.session_state.get("structured")
    if s:
        ui.ai_banner(s)
    d = s["data"] if s else {}
    k = st.session_state.note_ver
    with st.form(f"enc_form_{k}"):
        cc = st.text_input("Chief complaint *", value=d.get("chief_complaint", ""))
        notes = st.text_area("Clinical notes", value=d.get("history_summary", ""))
        c1, c2 = st.columns(2)
        dx = c1.text_input("Diagnosis", value=d.get("diagnosis_as_stated", ""))
        plan = c2.text_input("Plan", value=d.get("plan_as_stated", ""))
        v = st.columns(5)
        bp = v[0].text_input("BP", placeholder="120/80")
        pulse = v[1].number_input("Pulse", 0, 250, 0)
        temp = v[2].number_input("Temp °C", 0.0, 45.0, 0.0, step=0.1)
        wt = v[3].number_input("Weight kg", 0.0, 400.0, 0.0, step=0.5)
        spo2 = v[4].number_input("SpO₂ %", 0, 100, 0)
        if st.form_submit_button("Save consultation", type="primary"):
            vitals = {"bp": bp, "pulse": pulse or None, "temp_c": temp or None, "weight_kg": wt or None, "spo2": spo2 or None}
            try:
                res = api.post(f"/patients/{pid}/encounters", {
                    "chief_complaint": cc, "notes": notes, "diagnosis": dx, "plan": plan,
                    "vitals": {x: y for x, y in vitals.items() if y}})
                st.success(f"Saved as {res['event_id']}")
                st.session_state.structured = None
            except APIError as e:
                st.error(e.message)


def _prescription_form(api: API, pid: int, profile: dict) -> None:
    pharmacies = api.get("/directory/pharmacies")
    rows_key = f"rx_rows_{pid}"
    st.session_state.setdefault(rows_key, 1)
    c1, c2, _ = st.columns([1, 1, 6])
    if c1.button("➕ medicine", key="add_row"):
        st.session_state[rows_key] += 1
        st.rerun()
    if c2.button("➖ remove", key="del_row") and st.session_state[rows_key] > 1:
        st.session_state[rows_key] -= 1
        st.rerun()

    items = []
    st.caption(FREQ_HELP + " · Quantity is calculated automatically if left at 0. Brand names (e.g. Panadol, Augmentin) are understood.")
    for i in range(st.session_state[rows_key]):
        c = st.columns([3, 1.4, 1, 1.2, 1.2, 1.2])
        med = c[0].text_input("Medicine", key=f"med_{pid}_{i}", placeholder="Metformin")
        strength = c[1].text_input("Strength", key=f"str_{pid}_{i}", placeholder="500 mg")
        dose = c[2].number_input("Dose", 0.5, 20.0, 1.0, step=0.5, key=f"dose_{pid}_{i}")
        freq = c[3].selectbox("Frequency", FREQ_CHOICES, index=1, key=f"freq_{pid}_{i}")
        days = c[4].number_input("Days", 1, 365, 5, key=f"days_{pid}_{i}")
        qty = c[5].number_input("Qty", 0, 5000, 0, key=f"qty_{pid}_{i}")
        if med.strip() and strength.strip():
            items.append({"medicine": med, "strength": strength, "dose": dose, "frequency": freq,
                          "duration_days": int(days), "quantity": int(qty) or None})

    pharm_map = {p["id"]: f"{p['name']} ({p['city']})" for p in pharmacies}
    pharm_id = st.selectbox("Send to pharmacy", list(pharm_map), format_func=lambda x: pharm_map[x], key=f"ph_{pid}")
    instructions = st.text_input("Instructions for patient/pharmacist", key=f"ins_{pid}", placeholder="After meals…")

    if st.button("🛡️ Run safety check"):
        if not items:
            st.warning("Add at least one medicine.")
        else:
            try:
                res = api.post("/prescriptions/check", {"patient_id": pid, "items": items})
                if res["alerts"]:
                    ui.show_alerts(res["alerts"], "Safety check results (rule engine)")
                else:
                    st.success("No safety flags raised.")
            except APIError as e:
                st.error(e.message)

    pending = st.session_state.get(f"pending_alerts_{pid}")
    if pending:
        ui.show_alerts(pending, "⚠️ These alerts must be reviewed before sending")
    ack = st.checkbox("I have reviewed the safety alerts and still want to send this prescription",
                      key=f"rx_ack_{pid}", value=False) if pending else False

    if st.button("📤 Send e-prescription", type="primary"):
        if not items:
            st.warning("Add at least one medicine (name + strength).")
            return
        try:
            res = api.post("/prescriptions", {"patient_id": pid, "pharmacy_id": pharm_id, "items": items,
                                              "instructions": instructions, "acknowledge_alerts": ack})
            rx = res["prescription"]
            st.session_state[f"pending_alerts_{pid}"] = None
            st.success(f"Sent {rx['rx_number']} to {rx['pharmacy_name']}.")
            if res["alerts"]:
                ui.show_alerts(res["alerts"], "Flags stored for human review")
            st.session_state["last_rx_id"] = rx["id"]
        except APIError as e:
            if e.status_code == 409 and isinstance(e.detail, dict):
                st.session_state[f"pending_alerts_{pid}"] = e.detail["alerts"]
                st.rerun()
            st.error(e.message)

    if st.session_state.get("last_rx_id"):
        rx_id = st.session_state["last_rx_id"]
        if st.button("🤖 AI review of last prescription (Prescription + Conflict agents)", key="ai_rev"):
            with st.spinner("Agents reviewing..."):
                try:
                    res = api.post(f"/prescriptions/{rx_id}/ai-review")
                    ui.ai_banner(res)
                    st.write(res["data"]["review"]["plain_summary"])
                    for pt in res["data"]["review"]["points_to_check"]:
                        st.markdown(f"- {pt}")
                    if res["alert_records"]:
                        ui.show_alerts(res["alert_records"], "Flags with AI explanations")
                except APIError as e:
                    st.error(e.message)


# ============================================================ Prescriptions ==
def prescriptions_page(api: API, user: dict) -> None:
    st.title("Prescriptions from your clinic")
    status = st.selectbox("Status", ["all", "sent", "verified", "partially_dispensed", "dispensed",
                                     "rejected", "clarification_requested"])
    rows = api.get("/prescriptions", status=None if status == "all" else status)
    if not rows:
        st.info("Nothing here.")
    for r in rows:
        header = f"{r['rx_number']} · {r['patient']['full_name']} · {r['pharmacy_name']} · {r['status'].replace('_', ' ')}"
        with st.expander(header, expanded=r["status"] == "clarification_requested"):
            st.markdown(ui.status_badge(r["status"]) + f" issued {ui.fmt_dt(r['issued_at'])}", unsafe_allow_html=True)
            st.dataframe(pd.DataFrame(r["items"])[["medicine", "strength", "dose", "frequency", "duration_days",
                                                    "quantity", "dispensed_quantity"]], hide_index=True, width="stretch")
            for m in r["messages"]:
                st.markdown(f"**{m['author_name']}** ({m['author_role']}): {m['text']}")
            if r["safety_alerts"]:
                ui.show_alerts(r["safety_alerts"], "Safety flags at time of sending")
            if r["status"] == "clarification_requested":
                reply = st.text_input("Your reply to the pharmacist", key=f"reply_{r['id']}")
                if st.button("Send reply", key=f"send_reply_{r['id']}", type="primary") and reply.strip():
                    api.post(f"/prescriptions/{r['id']}/respond", {"message": reply})
                    st.rerun()


# ============================================================= Review queue ==
def review_queue(api: API, user: dict) -> None:
    st.title("Safety review queue")
    st.caption("Flags raised by the rule engine that still need a human decision. AI never closes a flag.")
    alerts = api.get("/alerts")
    if not alerts:
        st.success("Queue is empty.")
    for a in alerts:
        st.markdown(f"**{a['patient_name']}** ({a['patient_hb_id']})")
        ui.show_alerts([a])
        c1, c2, c3 = st.columns([3, 1, 1])
        note = c1.text_input("Note", key=f"qn_{a['id']}", label_visibility="collapsed", placeholder="Review note")
        if c2.button("Acknowledge", key=f"qa_{a['id']}"):
            api.post(f"/alerts/{a['id']}/review", {"status": "acknowledged", "note": note})
            st.rerun()
        if c3.button("Dismiss", key=f"qd_{a['id']}"):
            api.post(f"/alerts/{a['id']}/review", {"status": "dismissed", "note": note})
            st.rerun()


PAGES = {
    "Dashboard": dashboard,
    "Patients": patients_page,
    "Patient Workspace": workspace,
    "Prescriptions": prescriptions_page,
    "Review Queue": review_queue,
}
