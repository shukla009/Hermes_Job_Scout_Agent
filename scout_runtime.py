"""Bounded cloud orchestration. A notification is not an application."""
import hashlib
import html
import json
import os
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scout_state import StateStore, StateCorruptError, IST
from scout_storage import load_json


def read_state(path, kind, default):
    if not Path(path).exists():
        return default
    try:
        value = load_json(path)
        if not isinstance(value, kind):
            raise ValueError("Wrong state type")
        return value
    except (OSError, ValueError) as exc:
        raise StateCorruptError("Persistent state is unreadable; outreach disabled") from exc


def _history_aliases(agent, history):
    return {agent._normalize_url(row.get("url") or row.get("href") or ""): row
            for row in history if isinstance(row, dict)}


def _introduction_posts(store, posts, normalize, now=None):
    """A source polling cooldown must not hide an explicitly applied fresh job."""
    now = now or datetime.now(timezone.utc)
    seen = {normalize(p.get("application_url") or p.get("href") or "") for p in posts}
    pending = []
    for row in sorted(store.data["jobs"].values(), key=lambda r: str(r.get("applied_at", "")), reverse=True):
        url = row.get("apply_url") or row.get("application_url") or row.get("url")
        if not url or normalize(url) in seen or not store.introduction_eligible(row["id"]):
            continue
        try:
            published = datetime.fromisoformat(str(row.get("date_posted", "")).replace("Z", "+00:00"))
            if published.tzinfo is None or not timedelta(0) <= now - published <= timedelta(hours=48):
                continue
        except (ValueError, TypeError):
            continue
        pending.append({"href": url, "source_type": "direct_apply", "needs_reverification": True,
                        "outreach_recheck": True,
                        "job_title": row.get("job_title"), "company": row.get("company"),
                        "location": row.get("location"), "date_posted": row.get("date_posted")})
        seen.add(normalize(url))
        if len(pending) == 3:
            break
    return pending


def reconcile_pending(agent, store):
    from scout_mail import sent_status
    checked = 0
    for intent in store.data.get("applications", {}).values():
        if intent.get("status") == "pending":
            intent["status"] = "ambiguous"
            intent["reason"] = "Previous form request may have been accepted; automatic retry prohibited"
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
    today = datetime.now(IST).date()
    def same_day(value):
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(IST).date() == today
        except (ValueError, TypeError):
            return False
    ledger = sum(same_day(row.get("created_at"))
                 and row.get("status") in {"pending", "uncertain", "accepted"}
                 for row in store.data["sends"].values())
    legacy = sum(same_day(row.get("timestamp"))
                 and row.get("route") in {"email", "follow_up", "recruiter_intro"}
                 and not any(intent.get("job_id") == row.get("job_id")
                             and intent.get("recipient") == row.get("email")
                             for intent in store.data["sends"].values())
                 for row in history if isinstance(row, dict))
    forms = sum(same_day(row.get("created_at"))
                for row in store.data.get("applications", {}).values())
    return ledger + legacy + forms


def email_window(now=None):
    now = (now or datetime.now(IST)).astimezone(IST)
    return now.weekday() < 5 and 510 <= now.hour * 60 + now.minute < 1110


def repository_mode(writer):
    try:
        return "standard_public" if writer._call("GET", "").get("private") is False else "private_allowance"
    except Exception:
        return "private_allowance"


_ALERT_FAILURE_CATEGORIES = frozenset({
    "excluded_platform", "unsafe_url", "http_access_denied", "http_rate_limited",
    "http_not_found", "http_server_error", "http_client_error", "timeout", "tls_error",
    "connection_error", "request_budget_exhausted", "response_too_large", "redirect_limit",
    "unexpected_error", "job_board_only", "no_structured_vacancy", "missing_original_date",
    "invalid_original_date",
})


def discovery_health(source_stats):
    """Bounded fixed labels only; source exceptions and tracker URLs stay out."""
    rows = [row for row in source_stats.values() if isinstance(row, dict)]
    failing = sum(row.get("status") in {"error", "partial", "unavailable"}
                  or (isinstance(row.get("errors"), int) and row["errors"] > 0)
                  for row in rows)
    categories = Counter()
    alert = source_stats.get("gmail_alert_label", {})
    raw = alert.get("failure_categories", {}) if isinstance(alert, dict) else {}
    if isinstance(raw, dict):
        for label, count in raw.items():
            if isinstance(count, int) and not isinstance(count, bool) and count > 0:
                categories[label if label in _ALERT_FAILURE_CATEGORIES else "unexpected_error"] += count
    return {"failing_sources": failing, "alert_failure_categories": dict(categories)}


def _discovery_summary(health):
    top = sorted(health["alert_failure_categories"].items(), key=lambda row: (-row[1], row[0]))[:4]
    return (f"Failing discovery sources: {health['failing_sources']}\n" +
            "Alert link diagnostics: " + ("; ".join(f"{label}: {count}" for label, count in top) or "none"))


def _followups_today(store):
    today = datetime.now(IST).date()
    count = 0
    for row in store.data["sends"].values():
        try:
            if (str(row.get("step", "")).startswith("followup-")
                    and row.get("status") in {"pending", "uncertain", "accepted"}
                    and datetime.fromisoformat(row["created_at"]).astimezone(IST).date() == today):
                count += 1
        except (ValueError, KeyError):
            continue
    return count


def telegram_summary(report, *, shadow=False):
    """Outcome first; implementation diagnostics stay in /status details."""
    funnel = report.get("funnel", {})
    named = report.get("named_contacts", {})
    forms = report.get("applications", {})
    sent = report.get("emails_accepted_by_smtp", 0)
    intros = report.get("introductions_accepted_by_smtp", 0)
    paused = report.get("status") in {"state_safety_stop", "free_quota_pause"}
    smtp_failed = report.get("outreach", {}).get("smtp") == "failed"
    lines = ["📋 <b>Job Scout — " + ("needs attention" if paused or smtp_failed else "search update") + "</b>",
             f"Jobs checked: {report.get('results_discovered', 0)}",
             f"Fresh jobs (last 48 hours): {funnel.get('verified_fresh', 0)}",
             f"Suitable jobs: {funnel.get('suitable', 0)}",
             f"Application emails: {sent} accepted by Gmail"]
    if intros:
        lines.append(f"Recruiter introductions: {intros} accepted by Gmail")
    if forms.get("confirmed"):
        lines.append(f"Online applications confirmed: {forms['confirmed']}")
    if report.get("follow_ups", {}).get("sent"):
        lines.append(f"Follow-up emails: {report['follow_ups']['sent']}")
    if paused:
        reason, action = "Sending is paused to protect your saved application records.", "Check /status details before restarting."
    elif smtp_failed:
        reason, action = "The Gmail connection failed; no new application emails were sent.", "Check the saved Gmail login settings."
    elif sent or intros:
        reason, action = "Email acceptance does not confirm delivery or a recruiter reply.", "None. Replies will be reported here."
    elif forms.get("confirmed"):
        reason, action = "The application website confirmed submission.", "None. Replies will be reported here."
    elif not funnel.get("suitable"):
        reason, action = "No application email sent: no jobs passed all your requirements.", "None. The next search is automatic."
    elif shadow:
        reason, action = "No application email sent: automatic sending is completing its safety checks.", "None. Testing continues automatically."
    elif not funnel.get("reachable"):
        reason, action = "No application email sent: no verified contact or supported application form was found.", "None. The next search is automatic."
    elif "email_deferred_outside_business_hours" in report.get("coverage_gaps", {}):
        reason, action = "Email sending waits for weekday business hours.", "None. Freshness is checked again before sending."
    else:
        reason, action = "No new email sent: sending safeguards or the allowed sending hours prevented it.", "None. The next search is automatic."
    lines += ["", reason]
    if report.get("discovery_health", {}).get("failing_sources") or report.get("status") == "budget_limited":
        lines.append("Some job sources could not be checked; results may be incomplete.")
    if named.get("validation_mode") and named.get("cloud_backend_verified"):
        lines.append(f"New contact feature: safety checks {named.get('shadow_runs_completed', 0)}/3 completed.")
    if named.get("backend") == "free_quota_exhausted":
        lines.append("Extra web search is temporarily limited." + (" Company-page checks still work." if named.get("direct_pages") == "available" else ""))
    if report.get("manual_actions", {}).get("sent"):
        lines.append("Manual application links: in separate job cards; use /actions to view them again.")
        action = "Optional jobs to apply to yourself: /actions. No approval is required."
    else:
        lines.append("No new manual-application links in this run.")
    lines += ["", "Your action: " + action, "More detail: /status details"]
    return "\n".join(lines)


def _weekly(agent, store, notify):
    week = datetime.now(IST).strftime("%G-W%V")
    if not notify or store.data.get("reported_week") == week:
        return
    metrics = store.weekly_metrics()
    message = ("📊 <b>Your job search — last 7 days</b>\n"
               f"Jobs found: {metrics['discovered']}\n"
               f"Application emails: {metrics['emailed']}\n"
               f"Online/user-reported applications: {metrics['submitted']}\n"
               f"Recruiter replies: {metrics['replies']}\n"
               f"Interviews: {metrics['interviews']}\n\n"
               "Check /stats for your progress.")
    if agent.send_telegram(message):
        store.data["reported_week"] = week
        store.save()


def _finish_early(agent, report, started, *, store=None, notify=False):
    """Every admission/safety stop is visible; never overwrite the real run."""
    agent.OUTBOUND_READY = False
    report["runtime_seconds"] = round(time.monotonic() - started, 2)
    report["outreach"]["status"] = "not_started"
    attempt = {"at": datetime.now(timezone.utc).isoformat(),
               "status": report["status"], "reason": report.get("reason", ""),
               "next_due_at": report.get("next_due_at"),
               "identity": report.get("identity", ""),
               "trigger": report.get("trigger", "local"),
               "scheduled": report.get("scheduled", False),
               "schedule_validation": report.get("schedule_validation", False)}
    persisted = False
    if store:
        store.data["last_attempt"] = attempt
        try:
            persisted = store.save()
        except (OSError, RuntimeError):
            persisted = False
        if not persisted:
            report.update(status="state_safety_stop", reason="Admission/skip state could not be durably saved; no discovery or outreach performed")
    agent._write_run_report(report)
    if notify:
        today = datetime.now(IST).date().isoformat()
        marker = today + ":" + report["status"]
        previous = store.data.get("reported_skip") if store else None
        # A persistence failure cannot safely suppress its own notification.
        if not persisted or previous != marker:
            message = ("Job Scout — " + html.escape(report["status"]) + "\n" +
                       html.escape(report.get("reason", "")) + "\nNo discovery or application emails attempted.")
            if report.get("next_due_at"):
                try:
                    due = datetime.fromisoformat(str(report["next_due_at"]).replace("Z", "+00:00")).astimezone(IST)
                    message += "\nNext eligible time: " + due.strftime("%d %b %Y, %H:%M IST") + " (GitHub may start later)"
                except (ValueError, TypeError):
                    message += "\nNext eligible time unavailable; check /status."
            if agent.send_telegram(message) and persisted:
                store.data["reported_skip"] = marker
                try:
                    store.save()
                except (OSError, RuntimeError):
                    pass
    return report


def run(agent, *, dry_run=False, notify=True):
    from scout_persistence import RuntimeBudget, GitHubWriter, BudgetExhausted
    started = time.monotonic()
    notify = notify and not dry_run
    cloud = os.getenv("GITHUB_ACTIONS", "").lower() == "true"
    token = os.getenv("GITHUB_TOKEN", "")
    verified = os.getenv("ZERO_COST_VERIFIED", "false").lower() == "true"
    report = {"status": "healthy", "results_discovered": 0, "pages_fetched": 0,
              "relevant_apply_leads": 0, "relevant_email_leads": 0,
              "emails_attempted": 0, "emails_accepted_by_smtp": 0,
              "introductions_accepted_by_smtp": 0,
              "duplicates": 0, "irrelevant": 0, "model_errors": 0,
              "safety_skips": 0, "query_errors": [], "queries_attempted": 0,
              "queries_successful": 0, "source_stats": {}, "rejection_reasons": {},
              "dry_run": dry_run, "follow_ups": {"sent": 0, "failed": 0, "pending": 0}}
    report["outreach"] = {"smtp": "not_checked", "status": "discovery_pending"}
    report["reply_monitor"] = {"status": "not_checked", "checked": 0,
                               "new_human_replies": 0, "auto_replies": 0,
                               "out_of_office": 0, "bounces": 0, "ambiguous": 0}
    agent.OUTBOUND_READY = False
    trigger = os.getenv("GITHUB_EVENT_NAME", "local")
    validation = cloud and os.getenv("SCOUT_VERIFY_SCHEDULE", "false").lower() == "true"
    scheduled = cloud and (trigger == "schedule" or validation)
    identity = os.getenv("GITHUB_RUN_ID", "") + ":" + os.getenv("GITHUB_RUN_ATTEMPT", "1")
    report.update(trigger=trigger, scheduled=scheduled, schedule_validation=validation, identity=identity)
    if cloud and (not verified or not token):
        report.update(status="state_safety_stop", reason=("Zero-spend deployment verification is required" if not verified else "Durable GitHub persistence is required"))
        return _finish_early(agent, report, started, notify=notify)
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
        mode = repository_mode(writer) if writer else "local"
        from scout_schedule import reserve_execution
        if cloud and not dry_run:
            decision = reserve_execution(store.data, mode=mode, identity=identity,
                                         scheduled=scheduled, now=datetime.now(timezone.utc))
            report["admission"] = decision
            report["next_due_at"] = decision["next_due_at"]
            if decision["status"] != "admitted":
                report.update(status=decision["status"], reason=decision["reason"])
                return _finish_early(agent, report, started, store=store, notify=notify)
        else:
            store.data.setdefault("budget", {}).update(mode=mode)
        store.data["last_attempt"] = {"at": datetime.now(timezone.utc).isoformat(), "status": "admitted",
                                      "reason": "Execution admitted; work has not completed", "next_due_at": report.get("next_due_at"),
                                      "identity": identity, "trigger": trigger, "scheduled": scheduled,
                                      "schedule_validation": validation}
        if not store.save():
            report.update(status="state_safety_stop", reason="Admission reservation could not be durably saved; no work started")
            return _finish_early(agent, report, started, notify=notify)
        agent.ACTIVE_STORE = store
    except (StateCorruptError, ValueError, TypeError, OSError, RuntimeError):
        report.update(status="state_safety_stop", reason="Persistent state or admission ledger is unreadable; no work started")
        return _finish_early(agent, report, started, notify=notify)
    shadow = False
    if store and cloud and not dry_run:
        completed = store.data.get("shadow_runs_completed", 0)
        shadow = completed < int(os.getenv("SCOUT_SHADOW_RUNS", "3"))
    if store and safe_state and not dry_run:
        from scout_mail import monitor_replies
        report["reply_monitor"] = monitor_replies(agent, store, max_messages=100)
        if report["reply_monitor"]["status"] == "state_error":
            safe_state = False
            report["status"] = "state_safety_stop"
    outbound = safe_state and agent.SEND_EMAILS and not dry_run and not shadow
    if outbound and not reconcile_pending(agent, store):
        outbound = False
        report["status"] = "state_safety_stop"
    if outbound and agent.SMTP_PREFLIGHT:
        smtp_ok = agent.smtp_preflight()
        report["outreach"]["smtp"] = "passed" if smtp_ok else "failed"
        if not smtp_ok:
            report["status"] = "partial"
    agent.OUTBOUND_READY = outbound
    email_outbound = outbound and report["outreach"]["smtp"] != "failed" and email_window()
    if email_outbound:
        capacity = max(0, agent.MAX_EMAILS_PER_DAY - _daily_sends(store, agent, history))
        follow = agent.process_pending_follow_ups(min(agent.MAX_FOLLOWUPS_PER_RUN, capacity, max(0, 2 - _followups_today(store))))
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
    posts += _introduction_posts(store, posts, agent._normalize_url)
    from scout_evaluation import classify_batch, record_review, record_daily, revision as matching_revision, stage
    posts, vacancy_counts, source_funnel = classify_batch(store.data, posts, agent._normalize_url)
    report["vacancy_counts"] = vacancy_counts
    report["source_funnel"] = source_funnel
    decision_revision = matching_revision()
    audit_enabled = not cloud or os.getenv("SCOUT_ENCRYPT_STATE", "false").lower() == "true"
    report["decision_audit"] = {"recorded": 0, "encrypted_cloud_storage": cloud and audit_enabled}
    report["results_discovered"] = len(posts)
    report["discovery_health"] = discovery_health(report.get("source_stats", {}))
    if report["discovery_health"]["failing_sources"] and report["status"] == "healthy":
        report["status"] = "partial"
    aliases = _history_aliases(agent, history)
    from scout_contacts import ContactDiscovery, rollout_state, can_send_new_contact, contact_is_current, complete_contact_shadow
    from scout_actions import reserve_manual_card, funnel_counts, review_reach, eligibility_holds
    contacts = ContactDiscovery(agent._request_with_retries, store.data.setdefault("employer_contacts", {}))
    from scout_contact_search import NamedDiscovery, named_rollout, named_send_allowed, cloud_backend_verification, record_search_health, BACKEND_LABELS
    named = NamedDiscovery(agent._request_with_retries, store)
    named_phase = named_rollout(store.data, named.revision)
    named_shadow_this_run = named_phase.get("shadow_runs", 0) < 3
    named_cloud_verified, cloud_check_status = cloud_backend_verification(store.data, cloud=cloud,
        encrypted=os.getenv("SCOUT_ENCRYPT_STATE", "false").lower() == "true",
        legacy_revision=os.getenv("SCOUT_NAMED_BACKENDS_VERIFIED", ""),
        bootstrap_status=os.getenv("SCOUT_NAMED_BACKEND_STATUS", ""))
    bootstrap = os.getenv("SCOUT_NAMED_BACKEND_STATUS", "")
    if cloud and not store.data.get("contact_search_health") and bootstrap in BACKEND_LABELS and bootstrap != "verified":
        record_search_health(store.data, bootstrap, datetime.now(timezone.utc))
    search_health = store.data.get("contact_search_health", {})
    if search_health.get("status") not in {None, "verified"}:
        named.stats["backend"] = search_health.get("status", "backend_unavailable")
    named.jina_enabled = not cloud or store.data.get("named_backend_verification", {}).get("jina") == "verified"
    contact_rollout = rollout_state(store.data, contacts.revision)
    contact_shadow_this_run = contact_rollout.get("shadow_runs", 0) < 3
    candidates = []
    verified_fresh = 0
    reasons = Counter()
    for post in posts:
        if time.monotonic() - started >= 360:
            report["status"] = "budget_limited"
            break
        # Avoid spending enrichment budget on already-submitted/emailed vacancies.
        old = aliases.get(agent._normalize_url(post.get("application_url") or post.get("href", "")))
        if old and old.get("route") in {"email", "follow_up", "recruiter_intro"}:
            report["duplicates"] += 1
            continue
        try:
            enriched = agent.enrich_post(post)
            report["pages_fetched"] += int(bool(enriched.get("page_fetched")))
            is_fresh, freshness_reason = agent._structured_freshness(enriched)
            verified_fresh += int(is_fresh)
            if is_fresh:
                stage(source_funnel, post, "fresh")
            analysis, verdict = agent.analyze_post_verbose(enriched)
        except BudgetExhausted:
            report["status"] = "budget_limited"
            break
        except Exception as exc:
            reasons["analysis error: " + type(exc).__name__] += 1
            continue
        assessment = enriched.get("eligibility_assessment") or {"eligible": bool(analysis), "email_safe": bool((analysis or {}).get("email_safe")),
            "reason": enriched.get("rejection_reason", ""), "requirements": (analysis or {}).get("requirements", {})}
        if audit_enabled:
            record_review(store.data, enriched, assessment, is_fresh, freshness_reason, decision_revision=decision_revision)
            report["decision_audit"]["recorded"] += 1
        stage(source_funnel, post, "suitable" if assessment.get("email_safe") and is_fresh else "held" if analysis else "rejected")
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
        intro = store.introduction_eligible(record["id"])
        if ((not intro and (record.get("submitted") or store.application_reserved(record["id"])))
                or record.get("emailed") or record.get("intro_emailed") or record.get("outcome")
                or record.get("manual_action_status") in {"skipped", "referral_requested"}
                or any(row.get("job_id") == record["id"] and row.get("status") in {"pending", "uncertain", "accepted"}
                       for row in store.data["sends"].values())):
            report["duplicates"] += 1
            continue
        if intro:
            analysis["outreach_purpose"] = "recruiter_intro"
            analysis["application_evidence_kind"] = "portal_confirmed" if record.get("application_route") == "public_form" else "user_reported"
        candidates.append((analysis, enriched, verdict))
    candidates.sort(key=lambda x: (x[0].get("tier") == "stretch", {"research": 0, "process": 1, "reporting": 1}.get(x[0].get("family"), 2), -x[0].get("fit_score", 0)))
    candidates = named.prioritize_candidates(candidates)
    # Enrich the best fully eligible vacancies first, not whichever source ran first.
    for analysis, enriched, verdict in candidates:
        if analysis.get("recruiter_email"):
            analysis["contact_route"] = "vacancy_email"
        elif analysis.get("email_safe") and agent._structured_freshness(enriched)[0]:
            enriched.update(company=analysis.get("company"), job_title=analysis.get("job_title"),
                            vacancy_reference=analysis.get("vacancy_reference"))
            proof = None
            if not cloud or os.getenv("SCOUT_ENCRYPT_STATE", "false").lower() == "true":
                proof = named.find(enriched)
            else:
                named.reason("encrypted_state_required")
            if proof:
                analysis.update(recruiter_email=proof["email"], contact_route="named_recruiter")
            else:
                found = contacts.find(enriched, getattr(agent, "CANDIDATE_EMAIL", ""))
                if found:
                    analysis.update(recruiter_email=found[0], contact_route="official_careers_inbox")
        record = store.data["jobs"][analysis["job_id"]]
        record["contact_route"] = analysis.get("contact_route", "unavailable")
        if enriched.get("recruitment_contact_evidence"):
            record["recruitment_contact_evidence"] = enriched["recruitment_contact_evidence"][:3]
    report["rejection_reasons"] = dict(reasons)
    report["relevant_apply_leads"] = sum(row[0].get("outreach_purpose") != "recruiter_intro" for row in candidates)
    report["relevant_intro_leads"] = sum(row[0].get("outreach_purpose") == "recruiter_intro" for row in candidates)
    report["relevant_email_leads"] = sum(bool(row[0].get("recruiter_email")) for row in candidates)
    if named.stats["backend"] == "state_safety_stop":
        safe_state = outbound = False
        agent.OUTBOUND_READY = False
        report["status"] = "state_safety_stop"
    if store and not store.save():
        safe_state = outbound = False
        agent.OUTBOUND_READY = False
        report["status"] = "state_safety_stop"
    from scout_applications import prepare_application, submit_application, load_answers, adapter_readiness
    report["applications"] = {"attempted": 0, "confirmed": 0, "ambiguous": 0, "blocked": 0,
                              "unsupported": 0, "validation_mode": False, **adapter_readiness()}
    rollout = store.data.setdefault("forms_rollout", {"shadow_runs": 0}) if store else {"shadow_runs": 0}
    revision = hashlib.sha256(json.dumps(report["applications"].get("adapters", []), sort_keys=True).encode()).hexdigest()
    if rollout.get("revision") != revision:
        rollout.clear()
        rollout.update(revision=revision, shadow_runs=0)
    form_shadow = bool(report["applications"].get("enabled_adapters")) and rollout.get("shadow_runs", 0) < 3
    report["applications"]["validation_mode"] = form_shadow
    try:
        answers = load_answers()
    except (ValueError, TypeError):
        answers = None
    report["coverage_gaps"] = Counter()
    report["eligibility_holds"] = Counter()
    report["contact_discovery"] = {**contacts.stats, "http_requests": contacts.fetches,
                                    "shadow_runs_completed": contact_rollout.get("shadow_runs", 0),
                                    "validation_mode": contact_shadow_this_run}
    report["manual_actions"] = {"sent": 0, "daily_limit": 3}
    reachable = 0
    alerts = emails = forms = 0
    for analysis, post, verdict in candidates:
        if not safe_state or not store:
            continue
        ident = analysis["job_id"]
        stretch = analysis.get("tier") == "stretch"
        verified_latest = not any("not verified latest" in warning for warning in analysis.get("warnings", []))
        if os.getenv("HANDS_OFF_ONLY", "true").lower() != "true" and notify and not dry_run and verified_latest and store.can_alert(ident, stretch):
            if agent._send_apply_alert(analysis):
                if store.record_alert(ident, stretch):
                    agent._record_history(history, analysis, post, "alert")
                    alerts += 1
                else:
                    outbound = False
                    report["status"] = "state_safety_stop"
        recipient = analysis.get("recruiter_email")
        if not analysis.get("email_safe", False) or post.get("reverification_failed"):
            report["coverage_gaps"]["unverified_fit_or_requirements"] += 1
            report["eligibility_holds"].update(eligibility_holds(analysis, post))
            continue
        if not recipient:
            if analysis.get("outreach_purpose") == "recruiter_intro":
                report["coverage_gaps"]["introduction_no_verified_contact"] += 1
                continue  # Already applied; never submit a second form.
            def public_request(method, url, **kwargs):
                return agent._request_with_retries(url, method=method, return_http_errors=True, max_retries=0, **kwargs)
            plan = prepare_application(post, analysis, public_request, answers=answers)
            if plan.get("status") != "ready":
                key = "unsupported" if plan.get("status") == "unsupported" else "blocked"
                report["applications"][key] += 1
                report["coverage_gaps"][plan.get("reason", "no_supported_submission_route")] += 1
                store.data["jobs"][ident]["reachability_route"] = "unsupported_or_manual"
                if (notify and not dry_run and os.getenv("SCOUT_OPTIONAL_ACTIONS", "true").lower() == "true"
                        and reserve_manual_card(store, analysis)):
                    manual = dict(analysis, manual_optional=True, manual_draft=store.data["jobs"][ident]["manual_draft"],
                                  manual_route=store.data["jobs"][ident].get("manual_route", "application"),
                                  manual_expires_at=store.data["jobs"][ident].get("manual_expires_at"))
                    delivered = agent._send_apply_alert(manual)
                    store.data["jobs"][ident]["manual_action_status"] = "notified" if delivered else "notification_uncertain"
                    if not store.save():
                        outbound = False
                        report["status"] = "state_safety_stop"
                    report["manual_actions"]["sent"] += int(bool(delivered))
                continue
            reachable += 1
            stage(source_funnel, post, "reachable")
            store.data["jobs"][ident]["reachability_route"] = "public_form"
            if not outbound or form_shadow or dry_run:
                continue
            limit = agent.MAX_EMAILS_PER_DAY
            if rollout.get("live_since"):
                since = datetime.fromisoformat(rollout["live_since"])
                if (datetime.now(timezone.utc) - since).days < 7:
                    limit = min(limit, 2)
            else:
                rollout["live_since"] = datetime.now(timezone.utc).isoformat()
                limit = min(limit, 2)
            if _daily_sends(store, agent, history) >= limit or report["applications"]["attempted"] + report["emails_attempted"] >= agent.MAX_EMAILS_PER_RUN:
                report["safety_skips"] += 1
                continue
            fresh, _ = agent._structured_freshness(post)
            if not fresh:
                continue
            intent = store.reserve_application(ident, plan["adapter_id"])
            if not intent:
                report["safety_skips"] += 1
                continue
            report["applications"]["attempted"] += 1
            result = submit_application(plan, public_request)
            if not store.finish_application(intent["id"], result):
                outbound = False
                report["status"] = "state_safety_stop"
            kind = result.get("status", "ambiguous")
            report["applications"][kind if kind in {"confirmed", "ambiguous", "blocked"} else "blocked"] += 1
            forms += 1
            if kind == "confirmed":
                stage(source_funnel, post, "portal_confirmed")
                agent._record_history(history, analysis, post, "submitted")
                if notify:
                    from scout_actions import posting_markup
                    agent.send_telegram("Application confirmed: " + html.escape(str(analysis.get("company", ""))) + " — " + html.escape(str(analysis.get("job_title", ""))), posting_markup(analysis))
            continue
        reachable += 1
        store.data["jobs"][ident]["reachability_route"] = analysis.get("contact_route", "vacancy_email")
        stage(source_funnel, post, "reachable")
        new_route = analysis.get("contact_route") == "official_careers_inbox"
        named_route = analysis.get("contact_route") == "named_recruiter" or analysis.get("outreach_purpose") == "recruiter_intro"
        if named_route:
            allowed, phase = named_send_allowed(store.data, named_phase, named_cloud_verified)
            if not allowed:
                report["coverage_gaps"][phase] += 1
                continue
            if not contact_is_current(post, recipient):
                report["coverage_gaps"]["named_contact_evidence_expired"] += 1
                continue
        if new_route:
            allowed, phase = can_send_new_contact(store.data, contact_rollout)
            if not allowed:
                report["coverage_gaps"][phase] += 1
                continue
            if not contact_is_current(post, recipient):
                report["coverage_gaps"]["careers_inbox_evidence_expired"] += 1
                continue
        if not email_outbound or not outbound or not email_window():
            report["coverage_gaps"]["email_deferred_outside_business_hours" if not email_window() else "outbound_disabled"] += 1
            continue
        if (agent._is_suppressed(recipient) or agent._recipient_in_cooldown(history, recipient)
                or report["emails_attempted"] + report["applications"]["attempted"] >= agent.MAX_EMAILS_PER_RUN
                or _daily_sends(store, agent, history) >= agent.MAX_EMAILS_PER_DAY):
            report["safety_skips"] += 1
            continue
        written, _ = agent.compose_email(post, analysis)
        if not written:
            continue
        fresh, _ = agent._structured_freshness(post)
        if not fresh:
            continue
        purpose = analysis.get("outreach_purpose", "application")
        intent = store.reserve_send(ident, recipient, outreach_purpose=purpose)
        if not intent:
            report["safety_skips"] += 1
            continue
        intent["body_hash"] = hashlib.sha256(written["email_body"].encode()).hexdigest()
        intent["contact_route"] = analysis.get("contact_route", "vacancy_email")
        if new_route:
            contact_rollout.setdefault("live_since", datetime.now(timezone.utc).isoformat())
        if named_route:
            named_phase.setdefault("live_since", datetime.now(timezone.utc).isoformat())
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
            is_intro = purpose == "recruiter_intro"
            stage(source_funnel, post, "introductions_accepted" if is_intro else "application_emails_accepted")
            report["introductions_accepted_by_smtp" if is_intro else "emails_accepted_by_smtp"] += 1
            agent._record_history(history, analysis, post, "recruiter_intro" if is_intro else "email", recipient)
            extra = {"outreach_purpose": purpose} if is_intro else {}
            agent.schedule_follow_up(recipient, written["email_subject"], written["email_body"], ident, **extra)
            if not store.save():
                outbound = False
                report["status"] = "state_safety_stop"
            if notify:
                from scout_actions import posting_markup
                agent.send_telegram(("✉️ <b>Recruiter introduction accepted by Gmail</b>\n" if is_intro else "✉️ <b>Application email accepted by Gmail</b>\n") +
                    html.escape(str(analysis.get("company", ""))) + " — " +
                    html.escape(str(analysis.get("job_title", ""))), posting_markup(analysis))
    if any(x.get("status") == "error" for x in report.get("source_stats", {}).values()) and report["status"] == "healthy":
        report["status"] = "partial"
    verified_sources = any(x.get("status") == "ok" for x in report.get("source_stats", {}).values())
    if (cloud and trigger == "schedule" and not dry_run and safe_state and verified_sources
            and report["status"] not in {"state_safety_stop", "budget_limited"}):
        complete_contact_shadow(contact_rollout, identity.split(":", 1)[0])
        if named_cloud_verified:
            complete_contact_shadow(named_phase, identity.split(":", 1)[0])
    report["named_contacts"] = {**named.stats, "cloud_backend_verified": named_cloud_verified,
                                "cloud_verification_status": cloud_check_status,
                                "direct_pages": "available" if named_cloud_verified else "not_cloud_verified",
                                "jina": "available" if named.jina_enabled else "not_cloud_verified",
                                "search_retry_at": store.data.get("contact_search_health", {}).get("retry_at"),
                                "shadow_runs_completed": named_phase.get("shadow_runs", 0),
                                "validation_mode": named_shadow_this_run,
                                "introductions_accepted_by_smtp": report["introductions_accepted_by_smtp"]}
    report["contact_discovery"]["shadow_runs_completed"] = contact_rollout.get("shadow_runs", 0)
    report["funnel"] = funnel_counts(store, discovered=len(posts), fresh=verified_fresh,
                                    suitable=sum(bool(a.get("email_safe")) for a, _, _ in candidates),
                                    reachable=reachable, emails=report["emails_accepted_by_smtp"],
                                    forms=report["applications"]["confirmed"])
    if store and cloud and os.getenv("GITHUB_EVENT_NAME") == "schedule" and not dry_run and safe_state and verified_sources and report["applications"].get("enabled_adapters"):
        rollout["shadow_runs"] = min(3, rollout.get("shadow_runs", 0) + 1)
    report["coverage_gaps"] = dict(report["coverage_gaps"])
    report["eligibility_holds"] = dict(report["eligibility_holds"])
    if shadow and store and safe_state and verified_sources and report["status"] not in {"state_safety_stop", "budget_limited"}:
        store.data["shadow_runs_completed"] = store.data.get("shadow_runs_completed", 0) + 1
    report["alerts_sent"] = alerts
    report["outreach"]["status"] = (
        "emails_accepted" if report["emails_accepted_by_smtp"] else
        "introductions_accepted" if report["introductions_accepted_by_smtp"] else
        "forms_confirmed" if report["applications"]["confirmed"] else
        "validation_mode" if shadow else
        "smtp_failed" if report["outreach"]["smtp"] == "failed" else
        "state_safety_stop" if not safe_state else
        "sending_disabled" if not agent.SEND_EMAILS or dry_run else
        "no_eligible_vacancies" if not candidates else
        "no_eligible_email_leads" if not report["relevant_email_leads"] else
        "safety_or_daily_limit")
    report["http_requests"] = getattr(budget, "attempts", 0)
    report["shadow_run"] = shadow
    report["runtime_seconds"] = round(time.monotonic() - started, 2)
    if any(x.get("status") == "error" for x in report.get("source_stats", {}).values()) and report["status"] == "healthy":
        report["status"] = "partial"
    if not candidates and report["status"] == "healthy":
        report["status"] = "no_matches"
    agent._write_run_report(report)
    if store:
        record_daily(store.data, report, identity if cloud else "")
        store.data["last_run"] = {k: report[k] for k in ("status", "results_discovered", "relevant_apply_leads", "alerts_sent", "emails_accepted_by_smtp", "shadow_run")}
        store.data["last_run"]["at"] = datetime.now(timezone.utc).isoformat()
        store.data["last_run"]["outreach"] = report["outreach"]
        store.data["last_run"]["reply_monitor"] = report["reply_monitor"]
        store.data["last_run"]["applications"] = report["applications"]
        store.data["last_run"]["coverage_gaps"] = report["coverage_gaps"]
        store.data["last_run"]["runtime_seconds"] = report["runtime_seconds"]
        store.data["last_run"]["discovery_health"] = report["discovery_health"]
        for key in ("funnel", "contact_discovery", "manual_actions", "eligibility_holds", "named_contacts", "vacancy_counts", "source_funnel", "decision_audit"):
            store.data["last_run"][key] = report[key]
        store.data["last_attempt"] = {"at": store.data["last_run"]["at"], "status": report["status"],
                                      "reason": "Discovery run completed", "next_due_at": report.get("next_due_at"),
                                      "identity": identity, "trigger": trigger, "scheduled": scheduled,
                                      "schedule_validation": validation}
        if cloud and store.data["budget"].get("mode") == "standard_public":
            store.data["budget"]["used_minutes_estimate"] += round(report["runtime_seconds"] / 60, 2)
        outreach_log = store.data.setdefault("outreach_runs", [])
        outreach_log.append({"at": store.data["last_run"]["at"], **report["outreach"],
                             "eligible_email_leads": report["relevant_email_leads"],
                             "attempted": report["emails_attempted"],
                             "smtp_accepted": report["emails_accepted_by_smtp"]})
        store.data["outreach_runs"] = outreach_log[-30:]
        if not store.save():
            report["status"] = "state_safety_stop"
    agent._write_run_report(report)
    if notify:
        today = datetime.now(IST).date().isoformat()
        morning = datetime.now(IST).hour >= 8 and (not store or store.data.get("reported_day") != today)
        important = report["status"] in {"state_safety_stop", "free_quota_pause", "budget_limited"}
        backend_problem = named.stats["backend"] not in {"not_needed", "ready"}
        important = important or (backend_problem and store.data.get("reported_named_backend") != named.stats["backend"])
        if alerts or emails or morning or important or not store or report["applications"]["confirmed"]:
            delivered = agent.send_telegram(telegram_summary(report, shadow=shadow))
            if store and delivered:
                store.data["reported_day"] = today
                store.data["reported_named_backend"] = named.stats["backend"]
                store.save()
        if report["reply_monitor"]["new_human_replies"]:
            agent.send_telegram("💬 <b>Recruiter reply detected</b>\n" +
                str(report["reply_monitor"]["new_human_replies"]) +
                " new reply(s) found. Open Gmail to respond; the agent has not replied for you.")
        if store:
            review = review_reach(store.data)
            if review and agent.send_telegram(html.escape(review)):
                store.data["reach_review_started_at"] = datetime.now(timezone.utc).isoformat()
            store.save()
            _weekly(agent, store, notify)
    return report
