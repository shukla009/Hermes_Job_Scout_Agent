"""Optional phone-friendly applications; never an approval gate for automation."""
import re
from datetime import datetime, timedelta, timezone

from scout_sources import safe_url
from scout_state import IST


def manual_draft(analysis):
    title = re.sub(r"\s+", " ", str(analysis.get("job_title", "this role")))[:140]
    company = re.sub(r"\s+", " ", str(analysis.get("company", "your company")))[:120]
    points = [str(p)[:220] for p in analysis.get("evidence", [])[:1]]
    evidence = (" My background includes: " + points[0]) if points else ""
    return (f"Hi, I'm interested in the {title} opening at {company}.{evidence} "
            "If you know my work and feel I am a fit, would you be comfortable referring me? "
            "No worries if not. I can share my resume. Thank you, Shivank Shukla.")


def reserve_manual_card(store, analysis, *, now=None):
    """Persist notification intent first; an uncertain send is not retried."""
    now = now or datetime.now(timezone.utc)
    ident = analysis["job_id"]
    record = store.data["jobs"][ident]
    url = analysis.get("application_url", "")
    if (not analysis.get("email_safe") or not safe_url(url) or record.get("archived") or
            record.get("submitted") or record.get("emailed") or record.get("outcome") or
            record.get("manual_action_status") or store.application_reserved(ident) or
            any(s.get("job_id") == ident and s.get("status") in {"pending", "accepted", "uncertain"}
                for s in store.data["sends"].values())):
        return False
    try:
        published = datetime.fromisoformat(str(analysis["date_posted"]).replace("Z", "+00:00"))
        if published.tzinfo is None or not timedelta(0) <= now - published <= timedelta(hours=48):
            return False
    except (KeyError, TypeError, ValueError):
        return False
    ledger = store.data.setdefault("manual_alert_days", {})
    day = now.astimezone(IST).date().isoformat()
    if type(ledger.get(day, 0)) is not int or not 0 <= ledger.get(day, 0) < 3:
        return False
    ledger[day] = ledger.get(day, 0) + 1
    for old in sorted(ledger)[:-14]:
        ledger.pop(old)
    record.update(manual_action_status="notification_reserved", manual_alerted_at=now.isoformat(),
                  manual_expires_at=(published + timedelta(hours=48)).isoformat(),
                  manual_draft=manual_draft(analysis), reachability_route="manual_optional")
    return store.save()


def funnel_counts(store, *, discovered, fresh, suitable, reachable, emails, forms):
    """No SMTP-to-delivery inference; outcomes remain separate from submissions."""
    jobs = store.data["jobs"].values()
    return {"discovered": discovered, "verified_fresh": fresh, "suitable": suitable,
            "reachable": reachable, "smtp_accepted": emails, "portal_confirmed": forms,
            "human_replies_total": sum(j.get("outcome") in {"reply", "interview"} for j in jobs),
            "interviews_total": sum(j.get("outcome") == "interview" for j in jobs)}


def eligibility_holds(analysis, post=None):
    """Fixed diagnostic labels only: no vacancy text or contact values in logs."""
    mandatory = analysis.get("requirements", {}).get("mandatory", {})
    unknown = mandatory.get("unknown", [])
    warnings = analysis.get("warnings", [])
    labels = []
    if "Minimum experience not stated" in unknown:
        labels.append("minimum_experience_not_stated_for_nonjunior_scope")
    if any(value != "Minimum experience not stated" for value in unknown):
        labels.append("mandatory_requirement_unverified")
    if mandatory.get("unmet"):
        labels.append("mandatory_requirement_not_met")
    if "Full-time employment not confirmed" in warnings:
        labels.append("full_time_unconfirmed")
    if "Location unconfirmed" in warnings or "Remote country eligibility unconfirmed" in warnings:
        labels.append("location_or_remote_eligibility_unconfirmed")
    if analysis.get("risk_assessment", {}).get("requires_review"):
        labels.append("recruitment_safety_review")
    if (post or {}).get("reverification_failed"):
        labels.append("vacancy_reverification_failed")
    return labels or ["other_eligibility_hold"]


def review_reach(data, now=None):
    """Once per 14-day period; counts, not promised response probabilities."""
    now = now or datetime.now(timezone.utc)
    start = data.setdefault("reach_review_started_at", now.isoformat())
    try:
        since = datetime.fromisoformat(start)
        if since.tzinfo is None or now - since < timedelta(days=14):
            return None
    except (ValueError, TypeError):
        return None
    rows = [j for j in data["jobs"].values() if j.get("first_seen", "") >= start]
    applied = sum(bool(j.get("submitted") or j.get("emailed")) for j in rows)
    replies = sum(j.get("outcome") in {"reply", "interview"} for j in rows)
    advice = ("Prioritise source and verified-contact coverage; applications are still sparse."
              if applied < 10 else "Review targeting and supporting evidence, not sending volume."
              if not replies else "Prioritise sources and role families producing human responses.")
    return f"14-day reach review: {applied} jobs applied/emailed; {replies} human responses. {advice}"
