"""
views/pharmacist.py - the PHARMACY inbox.

Flow on screen:  New (sent) -> verify / reject / ask clarification
                 Ready to dispense (verified / partially) -> enter quantities -> dispense
"""
import pandas as pd
import streamlit as st

from frontend import ui
from frontend.api_client import API, APIError

TABS = [
    ("📥 New", ["sent"]),
    ("💬 Waiting for doctor", ["clarification_requested"]),
    ("💊 Ready to dispense", ["verified", "partially_dispensed"]),
    ("✅ Completed", ["dispensed", "rejected"]),
]


def inbox(api: API, user: dict) -> None:
    st.title(f"{user['organization_name']} — prescription inbox")
    all_rx = api.get("/prescriptions")
    tabs = st.tabs([f"{name} ({sum(1 for r in all_rx if r['status'] in sts)})" for name, sts in TABS])
    for tab, (_, statuses) in zip(tabs, TABS):
        with tab:
            rows = [r for r in all_rx if r["status"] in statuses]
            if not rows:
                st.info("Nothing here.")
            for r in rows:
                _prescription_card(api, r)


def _prescription_card(api: API, r: dict) -> None:
    with st.expander(f"{r['rx_number']} · {r['patient']['full_name']} · from {r['doctor_name']} · {ui.fmt_dt(r['issued_at'])}"):
        detail = api.get(f"/prescriptions/{r['id']}")
        p = detail["patient"]
        st.markdown(ui.status_badge(r["status"]), unsafe_allow_html=True)
        st.markdown(f"**Patient:** {p['full_name']} ({p['hb_id']}) · {p['gender']} · DOB {p['dob'] or '—'}  \n"
                    f"⚠️ **Allergies:** {', '.join(p['allergies']) or 'none recorded'}")
        st.dataframe(pd.DataFrame(detail["items"])[["id", "medicine", "strength", "dose", "frequency",
                                                    "duration_days", "quantity", "dispensed_quantity", "remaining"]],
                     hide_index=True, width="stretch")
        if detail["instructions"]:
            st.markdown(f"**Instructions:** {detail['instructions']}")
        if detail["safety_alerts"]:
            ui.show_alerts(detail["alert_records"] or detail["safety_alerts"], "Safety flags raised when prescribed")
        for m in detail["messages"]:
            st.markdown(f"💬 **{m['author_name']}** ({m['author_role']}): {m['text']}")

        # ---- Pharmacy Agent briefing
        if st.button("🤖 AI verification checklist", key=f"brief_{r['id']}"):
            with st.spinner("Pharmacy Agent preparing..."):
                try:
                    res = api.post(f"/prescriptions/{r['id']}/ai-brief")
                    ui.ai_banner(res)
                    d = res["data"]
                    st.write(d["summary"])
                    st.markdown("**Verify before dispensing:**\n" + "\n".join(f"- [ ] {c}" for c in d["verification_checklist"]))
                    if d["concerns"]:
                        st.warning("**Concerns:**\n" + "\n".join(f"- {c}" for c in d["concerns"]))
                except APIError as e:
                    st.error(e.message)

        # ---- decisions
        if r["status"] == "sent":
            note = st.text_input("Note (required for reject / clarification)", key=f"note_{r['id']}")
            c1, c2, c3 = st.columns(3)
            for col, label, decision in ((c1, "✅ Verify", "verify"), (c2, "❓ Ask clarification", "clarify"),
                                          (c3, "⛔ Reject", "reject")):
                if col.button(label, key=f"{decision}_{r['id']}", width="stretch"):
                    try:
                        api.post(f"/prescriptions/{r['id']}/decision", {"decision": decision, "note": note})
                        st.rerun()
                    except APIError as e:
                        st.error(e.message)

        # ---- dispensing
        if r["status"] in ("verified", "partially_dispensed"):
            st.markdown("**Dispense** (enter the quantity handed over now — partial dispensing is allowed)")
            lines = []
            for item in detail["items"]:
                if item["remaining"] > 0:
                    q = st.number_input(f"{item['medicine']} {item['strength']} — remaining {item['remaining']}",
                                        0, item["remaining"], item["remaining"], key=f"dq_{r['id']}_{item['id']}")
                    if q > 0:
                        lines.append({"item_id": item["id"], "quantity": int(q)})
            notes = st.text_input("Dispensing notes", key=f"dn_{r['id']}")
            if st.button("💊 Record dispensing", key=f"disp_{r['id']}", type="primary") and lines:
                try:
                    api.post(f"/prescriptions/{r['id']}/dispense", {"lines": lines, "notes": notes})
                    st.rerun()
                except APIError as e:
                    st.error(e.message)

        if detail["dispensings"]:
            st.markdown("**Dispensing history**")
            for d in detail["dispensings"]:
                st.caption(f"{ui.fmt_dt(d['timestamp'])} · {d['status']} · "
                           + ", ".join(f"{l['medicine']} ×{l['quantity']}" for l in d["lines"]))


PAGES = {"Inbox": inbox}
