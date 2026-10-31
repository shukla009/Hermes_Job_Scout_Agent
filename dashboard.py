"""Single-file Streamlit dashboard reading Job Scout JSON state.

Reads state/scout.json (jobs / sends / alerts) and state/sources.json
(source health). No database, no new hosting, no state mutation — the
dashboard only reads existing JSON files. Runs locally on localhost:8501.

Run:  streamlit run dashboard.py
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import streamlit as st

BASE = Path(__file__).resolve().parent
SCOUT_STATE = BASE / "state" / "scout.json"
SOURCES_STATE = BASE / "state" / "sources.json"

STAGES = ("discovered", "alerted", "emailed", "submitted", "replies", "interviews", "rejected")


def _load_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _iso(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def main():
    st.set_page_config(page_title="Job Scout — Dashboard", layout="wide", page_icon="🔎")
    st.title("🔎 Job Scout — Infrastructure Dashboard")
    st.caption("Reads existing JSON state only — no database, no new hosting. localhost:8501")

    state = _load_json(SCOUT_STATE)
    if not state or not isinstance(state.get("jobs"), dict):
        st.error("Unable to read state/scout.json. Run the scout to generate state first.")
        st.stop()

    jobs = list(state["jobs"].values())
    now = datetime.now(timezone.utc)
    cutoff7 = now - timedelta(days=7)
    cutoff30 = now - timedelta(days=30)

    def in_window(value, cutoff):
        dt = _iso(value)
        return dt is not None and dt >= cutoff

    # Weekly metrics (last 7 days)
    weekly = {
        "discovered": sum(in_window(j.get("first_seen"), cutoff7) for j in jobs),
        "alerted": sum(bool(j.get("alerted")) and in_window(j.get("alerted_at"), cutoff7) for j in jobs),
        "emailed": sum(bool(j.get("emailed")) and in_window(j.get("emailed_at"), cutoff7) for j in jobs),
        "submitted": sum(bool(j.get("submitted")) and in_window(j.get("applied_at"), cutoff7) for j in jobs),
        "replies": sum(j.get("outcome") == "reply" and in_window(j.get("outcome_at"), cutoff7) for j in jobs),
        "interviews": sum(j.get("outcome") == "interview" and in_window(j.get("outcome_at"), cutoff7) for j in jobs),
    }

    st.subheader("Weekly metrics (last 7 days)")
    labels = [
        ("discovered", "Discovered"),
        ("alerted", "Alerted"),
        ("emailed", "Emailed"),
        ("submitted", "Submitted"),
        ("replies", "Replies"),
        ("interviews", "Interviews"),
    ]
    cols = st.columns(6)
    for col, (key, label) in zip(cols, labels):
        col.metric(label, weekly[key])

    # 30-day conversion cohorts by source and role family
    recent = [j for j in jobs if in_window(j.get("first_seen"), cutoff30)]

    def empty_counts():
        return {stage: 0 for stage in STAGES}

    src_cohorts, fam_cohorts = {}, {}
    for j in recent:
        source = str(j.get("source_name") or "unknown")[:80]
        family = str(j.get("family") or "unknown")[:80]
        for bucket, key in ((src_cohorts, source), (fam_cohorts, family)):
            counts = bucket.setdefault(key, empty_counts())
            counts["discovered"] += 1
            counts["alerted"] += int(bool(j.get("alerted")))
            counts["emailed"] += int(bool(j.get("emailed")))
            counts["submitted"] += int(bool(j.get("submitted")))
            counts["replies"] += int(j.get("outcome") == "reply")
            counts["interviews"] += int(j.get("outcome") == "interview")
            counts["rejected"] += int(j.get("outcome") in ("rejected", "notfit"))

    def cohort_rows(cohorts):
        return [
            {"Source / Family": name, **counts}
            for name, counts in sorted(cohorts.items(), key=lambda kv: -sum(kv[1].values()))
        ]

    st.subheader("Funnel by source (last 30 days)")
    st.dataframe(cohort_rows(src_cohorts), use_container_width=True, hide_index=True)

    st.subheader("Funnel by role family (last 30 days)")
    st.dataframe(cohort_rows(fam_cohorts), use_container_width=True, hide_index=True)

    # Recent jobs table
    st.subheader("Recent jobs")
    rows = []
    for j in sorted(jobs, key=lambda x: x.get("last_seen") or x.get("first_seen") or "", reverse=True)[:100]:
        rows.append({
            "Job": j.get("job_title") or j.get("title") or "",
            "Company": j.get("company") or "",
            "Location": j.get("location") or "",
            "Source": j.get("source_name") or "unknown",
            "Family": j.get("family") or "unknown",
            "Alerted": "yes" if j.get("alerted") else "",
            "Emailed": "yes" if j.get("emailed") else "",
            "Submitted": "yes" if j.get("submitted") else "",
            "Outcome": j.get("outcome") or "",
            "Last seen": j.get("last_seen") or "",
        })
    st.dataframe(rows, use_container_width=True, hide_index=True)

    # Source health (auto-pause)
    st.subheader("Source health (auto-pause)")
    sources = _load_json(SOURCES_STATE) or {}
    health_rows = []
    if isinstance(sources, dict):
        for key, entry in sorted(sources.items()):
            if not isinstance(entry, dict):
                continue
            health = entry.get("health", {})
            health_rows.append({
                "Source": key,
                "Consecutive failures": int(health.get("consecutive_failures", 0)),
                "Auto-paused": "yes" if health.get("auto_pause") else "",
                "Last successful poll": health.get("last_successful_poll") or "",
                "Last poll": entry.get("last_poll") or "",
            })
    if health_rows:
        st.dataframe(health_rows, use_container_width=True, hide_index=True)
    else:
        st.info("No source health records yet. Run a discovery run to populate state/sources.json.")


if __name__ == "__main__":
    main()
