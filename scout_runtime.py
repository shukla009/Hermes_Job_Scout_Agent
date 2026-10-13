"""Bounded cloud orchestration. A notification is not an application."""
import hashlib
import html
import json
import os
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from scout_state import StateStore, StateCorruptError, IST


def read_state(path, kind, default):
    if not Path(path).exists():
        return default
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(value, kind):
            raise ValueError("Wrong state type")
        return value
    except (OSError, ValueError) as exc:
        raise StateCorruptError("Persistent state is unreadable; outreach disabled") from exc


def _history_aliases(agent, history):
    return {agent._normalize_url(row.get("url") or row.get("href") or ""): row
            for row in history if isinstance(row, dict)}


def reconcile_pending(agent, store):
    from scout_mail import sent_status
    checked = 0
    for intent in store.data["sends"].values():
        if intent.get("status") != "pending":
            continue
        if checked >= 5:
            return False
        checked += 1
        # Missing Sent evidence is not proof that SMTP did not accept a message.
        found = sent_status(agent, intent["message_id"])
        if not store.finish_send(intent["id"], "accepted" if found is True else "uncertain"):
            return False
    return True


def _daily_sends(store, agent, history):
    today = datetime.now(timezone.utc).date().isoformat()
    ledger = sum(str(row.get("created_at", "")).startswith(today)
                 and row.get("status") in {"pending", "uncertain", "accepted"}
                 for row in store.data["sends"].values())
    legacy = sum(str(row.get("timestamp", "")).startswith(today)
                 and row.get("route") in {"email", "follow_up"}
                 and not any(intent.get("job_id") == row.get("job_id")
                             and intent.get("recipient") == row.get("email")
                             for intent in store.data["sends"].values())
                 for row in history if isinstance(row, dict))
    return ledger + legacy


def _weekly(agent, store, notify):
    week = datetime.now(IST).strftime("%G-W%V")
    if not notify or store.data.get("reported_week") == week:
        return
    metrics = store.weekly_metrics()
    if agent.send_telegram("📊 <b>Job Scout — last 7 days</b>\n" + "\n".join(
            f"{html.escape(k.replace('_', ' ').title())}: {v}" for k, v in metrics.items())):
        store.data["reported_week"] = week
        store.save()


def run(agent, *, dry_run=False, notify=True):
    from scout_persistence import RuntimeBudget, GitHubWriter, BudgetExhausted
    started = time.monotonic()
    notify = notify and not dry_run
    cloud = os.getenv("GITHUB_ACTIONS", "").lower() == "true"
    token = os.getenv("GITHUB_TOKEN", "")
    verified = os.getenv("ZERO_COST_VERIFIED", "false").lower() == "true"
    if cloud and not verified:
        raise RuntimeError("Zero-spend deployment verification is required")
    if cloud and not token:
        raise RuntimeError("Durable GitHub persistence is required")
    report = {"status": "healthy", "results_discovered": 0, "pages_fetched": 0,
              "relevant_apply_leads": 0, "relevant_email_leads": 0,
              "emails_attempted": 0, "emails_accepted_by_smtp": 0,
              "duplicates": 0, "irrelevant": 0, "model_errors": 0,
              "safety_skips": 0, "query_errors": [], "queries_attempted": 0,
              "queries_successful": 0, "source_stats": {}, "rejection_reasons": {},
              "dry_run": dry_run, "follow_ups": {"sent": 0, "failed": 0, "pending": 0}}
    writer = GitHubWriter(token=token, repo=os.getenv("GITHUB_REPOSITORY", "")) if cloud and not dry_run else None
    budget = RuntimeBudget(max_requests=120, deadline_seconds=420)
    agent.ACTIVE_BUDGET = budget
    agent.ACTIVE_STORE = None
    history = []
    store = None
    safe_state = True
    try:
        history = read_state(agent.HISTORY_FILE, list, [])
        read_state(agent.FOLLOW_UP_FILE, list, [])
        read_state(agent.SUPPRESSION_FILE, list, [])
        store = StateStore(os.getenv("SCOUT_STATE_FILE", "state/scout.json"),
                           history_path=agent.HISTORY_FILE, commit_callback=writer,
                           followups_path=agent.FOLLOW_UP_FILE,
                           suppressions_path=agent.SUPPRESSION_FILE)
        store.ingest_feedback()
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        account_budget = store.data.setdefault("budget", {})
        if account_budget.get("month") != month:
            account_budget.update(month=month, used_minutes_estimate=0, runs=[], paused=False)
        account_budget["monthly_ceiling"] = 1200
        identity = os.getenv("GITHUB_RUN_ID", "") + ":" + os.getenv("GITHUB_RUN_ATTEMPT", "1")
        if cloud and identity not in account_budget["runs"]:
            if account_budget["used_minutes_estimate"] + 10 > 1200:
                first_pause = not account_budget.get("paused")
                account_budget["paused"] = True
                store.save()
                report["status"] = "free_quota_pause"
                agent._write_run_report(report)
                if notify and first_pause:
                    agent.send_telegram("⏸️ Job Scout paused at its free-usage ceiling. No application emails sent; no paid fallback enabled.")
                return report
            account_budget["runs"].append(identity)
            account_budget["used_minutes_estimate"] += 10
        account_budget["paused"] = False
        if not store.save():
            safe_state = False
        agent.ACTIVE_STORE = store if safe_state else None
    except StateCorruptError:
        safe_state = False
        report["status"] = "state_safety_stop"
    shadow = False
    if store and cloud and not dry_run:
        completed = store.data.get("shadow_runs_completed", 0)
        shadow = completed < int(os.getenv("SCOUT_SHADOW_RUNS", "3"))
    outbound = safe_state and agent.SEND_EMAILS and not dry_run and not shadow
    if outbound and not reconcile_pending(agent, store):
        outbound = False
        report["status"] = "state_safety_stop"
    if outbound and agent.SMTP_PREFLIGHT and not agent.smtp_preflight():
        outbound = False
        report["status"] = "partial"
    agent.OUTBOUND_READY = outbound
    if outbound:
        capacity = max(0, agent.MAX_EMAILS_PER_DAY - _daily_sends(store, agent, history))
        follow = agent.process_pending_follow_ups(min(agent.MAX_FOLLOWUPS_PER_RUN, capacity))
        for row in follow.pop("sent_records", []):
            history.append(dict(row, route="follow_up", timestamp=datetime.now(timezone.utc).isoformat()))
        report["follow_ups"] = follow
        agent.save_history(history)
        if not store.save():
            outbound = False
            agent.OUTBOUND_READY = False
            report["status"] = "state_safety_stop"
    try:
        posts = agent.search_recruiter_posts()
        report.update(agent.LAST_SEARCH_STATS)
    except Exception as exc:
        posts = []
        report["status"] = "partial"
        report["query_errors"].append(type(exc).__name__)
    report["results_discovered"] = len(posts)
    aliases = _history_aliases(agent, history)
    candidates = []
    reasons = Counter()
    for post in posts:
        if time.monotonic() - started >= 480:
            report["status"] = "budget_limited"
            break
        # Avoid spending enrichment budget on already-submitted/emailed vacancies.
        old = aliases.get(agent._normalize_url(post.get("application_url") or post.get("href", "")))
        if old and old.get("route") in {"email", "submitted", "follow_up"}:
            report["duplicates"] += 1
            continue
        try:
            enriched = agent.enrich_post(post)
            report["pages_fetched"] += int(bool(enriched.get("page_fetched")))
            analysis, verdict = agent.analyze_post_verbose(enriched)
        except BudgetExhausted:
            report["status"] = "budget_limited"
            break
        except Exception as exc:
            reasons["analysis error: " + type(exc).__name__] += 1
            continue
        if not analysis:
            if store:
                stale_id = agent._job_fingerprint(enriched)
                stale = store.data["jobs"].get(stale_id)
                if stale:
                    stale["archived"] = True
                    stale["exclusion_reason"] = enriched.get("rejection_reason") or "No longer qualifies"
            reasons[enriched.get("rejection_reason") or "Not eligible"] += 1
            report["irrelevant"] += 1
            continue
        if not store:
            candidates.append((analysis, enriched, verdict))
            continue
        record = store.upsert_job(analysis, enriched)
        analysis["job_id"] = record["id"]
        if record.get("submitted") or record.get("emailed") or record.get("outcome"):
            report["duplicates"] += 1
            continue
        candidates.append((analysis, enriched, verdict))
    candidates.sort(key=lambda x: (x[0].get("tier") == "stretch", -x[0].get("fit_score", 0)))
    report["rejection_reasons"] = dict(reasons)
    report["relevant_apply_leads"] = len(candidates)
    report["relevant_email_leads"] = sum(bool(row[0].get("recruiter_email")) for row in candidates)
    if store and not store.save():
        safe_state = outbound = False
        agent.OUTBOUND_READY = False
        report["status"] = "state_safety_stop"
    alerts = emails = 0
    for analysis, post, verdict in candidates:
        if not safe_state or not store:
            continue
        ident = analysis["job_id"]
        stretch = analysis.get("tier") == "stretch"
        verified_latest = not any("not verified latest" in warning for warning in analysis.get("warnings", []))
        if notify and not dry_run and verified_latest and store.can_alert(ident, stretch):
            if agent._send_apply_alert(analysis):
                if store.record_alert(ident, stretch):
                    agent._record_history(history, analysis, post, "alert")
                    alerts += 1
                else:
                    outbound = False
                    report["status"] = "state_safety_stop"
        recipient = analysis.get("recruiter_email")
        if not recipient or not outbound or not analysis.get("email_safe", False) or post.get("reverification_failed"):
            continue
        if (agent._is_suppressed(recipient) or agent._recipient_in_cooldown(history, recipient)
                or emails >= agent.MAX_EMAILS_PER_RUN
                or _daily_sends(store, agent, history) >= agent.MAX_EMAILS_PER_DAY):
            report["safety_skips"] += 1
            continue
        written, _ = agent.compose_email(post, analysis)
        if not written:
            continue
        intent = store.reserve_send(ident, recipient)
        if not intent:
            report["safety_skips"] += 1
            continue
        intent["body_hash"] = hashlib.sha256(written["email_body"].encode()).hexdigest()
        if not store.save():
            outbound = False
            report["status"] = "state_safety_stop"
            continue
        agent.ACTIVE_MESSAGE_ID = intent["message_id"]
        agent.ACTIVE_REFERENCES = None
        report["emails_attempted"] += 1
        accepted = agent.send_email(recipient, written["email_subject"], written["email_body"])
        if not store.finish_send(intent["id"], "accepted" if accepted else "uncertain"):
            outbound = False
            report["status"] = "state_safety_stop"
        if accepted:
            emails += 1
            report["emails_accepted_by_smtp"] += 1
            agent._record_history(history, analysis, post, "email", recipient)
            agent.schedule_follow_up(recipient, written["email_subject"], written["email_body"], ident)
            if not store.save():
                outbound = False
                report["status"] = "state_safety_stop"
            if notify:
                agent.send_telegram("✉️ <b>Application email accepted by SMTP</b>\n" +
                    html.escape(str(analysis.get("company", ""))) + " — " +
                    html.escape(str(analysis.get("job_title", ""))))
    if any(x.get("status") == "error" for x in report.get("source_stats", {}).values()) and report["status"] == "healthy":
        report["status"] = "partial"
    verified_sources = any(x.get("status") == "ok" for x in report.get("source_stats", {}).values())
    if shadow and store and safe_state and verified_sources and report["status"] not in {"state_safety_stop", "budget_limited"}:
        store.data["shadow_runs_completed"] = store.data.get("shadow_runs_completed", 0) + 1
    report["alerts_sent"] = alerts
    report["http_requests"] = getattr(budget, "attempts", 0)
    report["shadow_run"] = shadow
    report["runtime_seconds"] = round(time.monotonic() - started, 2)
    if any(x.get("status") == "error" for x in report.get("source_stats", {}).values()) and report["status"] == "healthy":
        report["status"] = "partial"
    if not candidates and report["status"] == "healthy":
        report["status"] = "no_matches"
    agent._write_run_report(report)
    if store:
        store.data["last_run"] = {k: report[k] for k in ("status", "results_discovered", "relevant_apply_leads", "alerts_sent", "emails_accepted_by_smtp", "shadow_run")}
        store.data["last_run"]["at"] = datetime.now(timezone.utc).isoformat()
        if not store.save():
            report["status"] = "state_safety_stop"
    agent._write_run_report(report)
    if notify:
        morning = datetime.now(IST).hour < 12
        important = report["status"] in {"state_safety_stop", "free_quota_pause", "budget_limited"}
        if alerts or morning or important or not store:
            agent.send_telegram("📋 <b>Job Scout — " + html.escape(report["status"]) + "</b>\n" +
                f"New actionable jobs: {len(candidates)}; alerts: {alerts}\n" +
                f"Application emails accepted: {report['emails_accepted_by_smtp']}\n" +
                ("Outreach is in validation mode; no emails sent.\n" if shadow else "") +
                "Top exclusions: " + html.escape("; ".join(f"{k}: {v}" for k, v in reasons.most_common(3)) or "none"))
        if store:
            _weekly(agent, store, notify)
    return report
