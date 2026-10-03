"""
ui.py - small reusable UI pieces shared by every page:
styling, severity badges, the Plotly timeline, alert cards, AI result card.
"""
from datetime import datetime
from typing import Dict, List

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

CSS = """
<style>
  .block-container {padding-top: 1.6rem; max-width: 1250px;}
  h1, h2, h3 {letter-spacing: -0.01em;}
  .hb-badge {display:inline-block; padding:2px 10px; border-radius:999px; font-size:0.78rem;
             font-weight:600; color:#fff; margin-right:6px;}
  .hb-card {border:1px solid rgba(128,128,128,.28); border-left:5px solid var(--c,#0f9d8a);
            border-radius:10px; padding:10px 14px; margin-bottom:8px;}
  .hb-muted {opacity:.7; font-size:.85rem;}
  .hb-ai {border:1px dashed #7c5cff; border-radius:10px; padding:10px 14px; margin:8px 0;}
</style>
"""

SEVERITY_COLOR = {"info": "#6b7280", "moderate": "#d97706", "high": "#dc2626", "critical": "#7f1d1d"}
STATUS_COLOR = {
    "sent": "#2563eb", "verified": "#0f9d8a", "partially_dispensed": "#d97706", "dispensed": "#15803d",
    "rejected": "#dc2626", "clarification_requested": "#9333ea",
}
TYPE_COLOR = {"consultation": "#2563eb", "prescription": "#9333ea", "dispensing": "#15803d",
              "lab": "#d97706", "history": "#6b7280"}
TYPE_ICON = {"consultation": "🩺", "prescription": "📝", "dispensing": "💊", "lab": "🧪", "history": "📂"}


def inject_css() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def badge(text: str, color: str) -> str:
    return f'<span class="hb-badge" style="background:{color}">{text}</span>'


def status_badge(status: str) -> str:
    return badge(status.replace("_", " ").title(), STATUS_COLOR.get(status, "#6b7280"))


def fmt_dt(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%d %b %Y, %H:%M")
    except (ValueError, TypeError):
        return str(iso)


def show_alerts(alerts: List[Dict], title: str = "") -> None:
    """Render safety alerts as coloured cards."""
    if title:
        st.markdown(f"**{title}**")
    for a in alerts:
        sev = a["severity"]
        src = ", ".join(a.get("source_event_ids") or [])
        extra = f"<div class='hb-muted'>Sources: {src}</div>" if src else ""
        ai = f"<div class='hb-muted'>🤖 {a['ai_explanation']}</div>" if a.get("ai_explanation") else ""
        status = f" · <i>{a['status']}</i>" if a.get("status") and a["status"] != "open" else ""
        st.markdown(
            f"<div class='hb-card' style='--c:{SEVERITY_COLOR[sev]}'>"
            f"{badge(sev.upper(), SEVERITY_COLOR[sev])}<b>{a['kind'].replace('_', ' ').title()}</b>{status}<br>"
            f"{a['message']}{extra}{ai}</div>", unsafe_allow_html=True)


def ai_banner(result: Dict) -> None:
    """Label shown above every AI output (PRD guardrail)."""
    who = result.get("generated_by", "")
    note = f" - {result['note']}" if result.get("note") else ""
    st.markdown(
        f"<div class='hb-ai'>🤖 <b>AI-generated</b> · {who}{note}<br>"
        f"<span class='hb-muted'>{result.get('disclaimer', '')}</span></div>", unsafe_allow_html=True)


def timeline_chart(events: List[Dict]) -> None:
    """Interactive Plotly timeline: one row per event type."""
    if not events:
        st.info("No events yet.")
        return
    df = pd.DataFrame(events)
    df["date"] = pd.to_datetime(df["date"])
    fig = go.Figure()
    for etype, grp in df.groupby("type"):
        fig.add_trace(go.Scatter(
            x=grp["date"], y=[etype] * len(grp), mode="markers", name=etype,
            marker=dict(size=16, color=TYPE_COLOR.get(etype, "#6b7280"), line=dict(width=1, color="white")),
            text=grp["title"] + "<br>" + grp["event_id"], hovertemplate="%{text}<br>%{x|%d %b %Y %H:%M}<extra></extra>"))
    fig.update_layout(height=260, margin=dict(l=10, r=10, t=10, b=10), showlegend=False,
                      yaxis=dict(categoryorder="array", categoryarray=["history", "lab", "consultation", "prescription", "dispensing"]))
    st.plotly_chart(fig, width="stretch")


def timeline_cards(events: List[Dict], key: str = "tl") -> None:
    for e in events:
        icon = TYPE_ICON.get(e["type"], "•")
        color = TYPE_COLOR.get(e["type"], "#6b7280")
        with st.expander(f"{icon} {fmt_dt(e['date'])} · {e['title']}  ({e['event_id']})"):
            st.markdown(f"{badge(e['type'], color)} {status_badge(e['status']) if e['type'] in ('prescription', 'dispensing') else '`' + e['status'] + '`'}",
                        unsafe_allow_html=True)
            st.write(e["summary"] or "-")
            st.caption(f"By {e['actor']} · {e['organization'] or '—'}")
            details = e.get("details") or {}
            if e["type"] == "consultation" and details.get("vitals"):
                st.caption("Vitals: " + ", ".join(f"{k}: {v}" for k, v in details["vitals"].items()))
            if e["type"] == "prescription":
                st.dataframe(pd.DataFrame(details["items"]), hide_index=True, width="stretch")


def render_ai_summary(result: Dict) -> None:
    """Show the Timeline Agent briefing with clickable-looking source IDs."""
    ai_banner(result)
    data, sources = result["data"], result.get("sources", {})
    st.subheader(data["headline"])
    for kp in data["key_points"]:
        ids = " ".join(f"`{s}`" for s in kp["source_ids"])
        st.markdown(f"- {kp['text']}  {ids}")
    if data["current_medications"]:
        st.markdown("**Current medications:** " + "; ".join(data["current_medications"]))
    if data["open_flags"]:
        st.warning("**Open flags for human review:**\n\n" + "\n".join(f"- {f}" for f in data["open_flags"]))
    with st.expander("Source records referenced"):
        cited = sorted({s for kp in data["key_points"] for s in kp["source_ids"]})
        for s in cited:
            info = sources.get(s)
            if info:
                st.markdown(f"`{s}` — {info['type']}: {info['title']} ({info['date'][:10]})")
