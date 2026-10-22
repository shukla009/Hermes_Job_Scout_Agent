"""Durable admission policy, independent of scheduler arrival time.

Private runs reserve the whole ten-minute workflow timeout before doing work.
Skipped arrivals reserve one additional runner minute for setup/persistence; this
is an estimate, not an account-wide GitHub billing meter.
"""
from datetime import datetime, timedelta, timezone
from math import isfinite

from scout_state import IST

PRIVATE_MONTHLY_MINUTES = 1200
FULL_RUN_MINUTES = 10
SKIP_MINUTES = 1
SCHEDULED_DAILY_LIMIT = 3
MIN_SCHEDULE_GAP = timedelta(hours=5)


def _stamp(value):
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Naive schedule timestamp")
    return parsed.astimezone(timezone.utc)


def reserve_execution(data, *, mode, identity, scheduled, now):
    """Mutate an admission reservation; caller MUST save before any work.

    The cadence ledger lives outside monthly accounting so a month boundary
    cannot reset the rolling gap. Failed/aborted admitted runs consume a slot:
    uncertainty must not permit duplicate outreach or unbounded runner usage.
    """
    now = now.astimezone(timezone.utc)
    budget = data.setdefault("budget", {})
    if not isinstance(budget, dict):
        raise ValueError("Invalid usage ledger")
    month = now.strftime("%Y-%m")
    if budget.get("month") != month:
        budget.update(month=month, used_minutes_estimate=0, runs=[], paused=False)
    used = budget.get("used_minutes_estimate", 0)
    runs = budget.get("runs", [])
    if isinstance(used, bool) or not isinstance(used, (int, float)) or not isfinite(used) or used < 0 or not isinstance(runs, list):
        raise ValueError("Invalid usage ledger")
    budget.setdefault("used_minutes_estimate", used)
    budget.setdefault("runs", runs)
    private = mode == "private_allowance"
    budget.update(mode=mode, monthly_ceiling=PRIVATE_MONTHLY_MINUTES if private else None)
    cadence = data.setdefault("schedule", {"reservations": []})
    if not isinstance(cadence, dict):
        raise ValueError("Invalid cadence ledger")
    rows = cadence.setdefault("reservations", [])
    if not isinstance(rows, list):
        raise ValueError("Invalid cadence ledger")
    parsed = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("identity"):
            raise ValueError("Invalid cadence reservation")
        at = _stamp(row.get("at"))
        if at > now:
            raise ValueError("Schedule ledger is ahead of the clock")
        parsed.append((row, at))
    today = now.astimezone(IST).date()
    todays = [at for _, at in parsed if at.astimezone(IST).date() == today]
    last = max((at for _, at in parsed), default=None)
    next_due = last + MIN_SCHEDULE_GAP if last else now
    if len(todays) >= SCHEDULED_DAILY_LIMIT:
        tomorrow = datetime.combine(today + timedelta(days=1), datetime.min.time(), tzinfo=IST)
        next_due = max(next_due, tomorrow.astimezone(timezone.utc))
    reason = "admitted"
    status = "admitted"
    if identity in runs:
        status, reason = "duplicate_run_skip", "This workflow attempt already has a durable reservation"
    elif private and scheduled and len(todays) >= SCHEDULED_DAILY_LIMIT:
        status, reason = "private_cadence_skip", "Three scheduled runs already reserved for this IST day"
    elif private and scheduled and now < next_due:
        status, reason = "private_cadence_skip", "Waiting for the five-hour minimum scheduled-run interval"
    charge = (FULL_RUN_MINUTES if status == "admitted" else SKIP_MINUTES) if private else 0
    if identity in runs:
        charge = 0
    if private and used + charge > PRIVATE_MONTHLY_MINUTES:
        status, reason = "free_quota_pause", "Conservative private-run monthly allowance exhausted"
        month_end = (now.replace(day=28, hour=0, minute=0, second=0, microsecond=0) + timedelta(days=4)).replace(day=1)
        next_due = month_end
        budget["paused"] = True
    else:
        if identity not in runs:
            budget["runs"] = (runs + [identity])[-1500:]
            if private:
                budget["used_minutes_estimate"] = used + charge
        if status == "admitted":
            budget["paused"] = False
            if private and scheduled:
                rows.append({"identity": identity, "at": now.isoformat()})
                next_due = now + MIN_SCHEDULE_GAP
                if len(todays) + 1 >= SCHEDULED_DAILY_LIMIT:
                    tomorrow = datetime.combine(today + timedelta(days=1), datetime.min.time(), tzinfo=IST)
                    next_due = max(next_due, tomorrow.astimezone(timezone.utc))
        cadence["reservations"] = [row for row in rows if _stamp(row["at"]) >= now - timedelta(days=7)]
    return {"status": status, "reason": reason,
            "next_due_at": next_due.isoformat() if (private and scheduled) or status == "free_quota_pause" else None,
            "reserved_minutes": charge if status != "free_quota_pause" else 0,
            "scheduled_runs_today": len(todays) + int(status == "admitted" and private and scheduled),
            "monthly_used_minutes_estimate": budget["used_minutes_estimate"]}
