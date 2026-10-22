"""Recruiter-reach regressions. Every address and submitted request is synthetic."""
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from scout_contacts import (ContactDiscovery, vacancy_belongs_to, contact_is_current,
                            rollout_state, can_send_new_contact, complete_contact_shadow)
from scout_actions import reserve_manual_card, review_reach
from scout_matching import evaluate_job
from scout_state import StateStore, callback_data

NOW = datetime(2026, 10, 23, 5, tzinfo=timezone.utc)
EMPLOYER = {"name": "Fixture Research", "official_domains": ["fixture.org"],
            "careers_url": "https://www.fixture.org/careers",
            "recruitment_pages": ["https://www.fixture.org/careers"]}


def vacancy(**changes):
    value = {"company": "Fixture Research", "title": "Junior Research Analyst",
             "location": "Noida, India", "employment_type": "Full-time",
             "source_text": "Assist the research team with market research, competitor analysis and reports. Day shift.",
             "page_fetched": True, "href": "https://www.fixture.org/jobs/42",
             "date_posted": (NOW - timedelta(hours=1)).isoformat()}
    value.update(changes)
    return value


def finder(content="Send your resume to careers@fixture.org for job applications.", cache=None, now=NOW):
    calls = []
    def get(url, **kwargs):
        calls.append(url)
        return SimpleNamespace(status_code=200, url=url, text=content)
    return ContactDiscovery(get, cache if cache is not None else {}, registry=[EMPLOYER], now=now), calls


def test_join_official_careers_inbox_to_distinct_vacancy():
    lookup, calls = finder()
    post = vacancy()
    assert lookup.find(post) == ["careers@fixture.org"]
    proof = post["recruitment_contact_evidence"][0]
    assert proof["scope"] == "employer_recruitment"
    assert proof["source_url"] == EMPLOYER["careers_url"]
    assert "Send your resume" in proof["excerpt"]
    assert contact_is_current(post, "careers@fixture.org", NOW)
    assert not contact_is_current(post, "careers@fixture.org", NOW + timedelta(hours=25))
    assert len(calls) == 1


@pytest.mark.parametrize("changes", [
    {"company": "Unrelated Company"}, {"href": "https://attacker.org/jobs/42"},
    {"href": "https://www.fixture.org.attacker.org/jobs/42"}, {"page_fetched": False},
    {"reverification_failed": True}, {"source_text": "Apply only through our portal. Research."},
    {"source_text": "No email applications. Research."},
])
def test_no_cross_employer_or_portal_only_join(changes):
    lookup, calls = finder()
    assert not lookup.find(vacancy(**changes))
    assert not calls


@pytest.mark.parametrize("content", [
    "Email sales@fixture.org for a demo.",
    "Privacy and accessibility support. Apply by email to support@fixture.org.",
    "Send your resume to careers@unrelated.org.",
    "Send your resume to sales@fixture.org.",
    "<footer>Send your resume to careers@fixture.org</footer>",
    "<script>Send your resume to careers@fixture.org</script>",
    "Apply only through our portal. Send your CV to careers@fixture.org.",
])
def test_careers_context_and_owned_domain_required(content):
    lookup, _ = finder(content)
    assert not lookup.find(vacancy())


def test_apply_online_through_email_is_an_explicit_application_cta():
    lookup, _ = finder("If interested please apply online through: hr@fixture.org or call us.")
    assert lookup.find(vacancy()) == ["hr@fixture.org"]


def test_contact_cache_expires_and_never_reuses_failed_refresh():
    cache = {}
    lookup, calls = finder(cache=cache)
    assert lookup.find(vacancy())
    assert lookup.find(vacancy())
    assert len(calls) == 1
    refresh, calls = finder("Contact support for enquiries.", cache, NOW + timedelta(hours=25))
    assert not refresh.find(vacancy())
    assert len(calls) == 1
    assert cache["fixtureresearch"]["evidence"] == []


def test_crawl_budget_and_official_only_depth():
    content = '<a href="/careers/team">Careers</a><a href="https://attacker.org/careers">careers</a>'
    lookup, calls = finder(content)
    lookup.find(vacancy())
    assert len(calls) == 2 and all("www.fixture.org" in url for url in calls)
    lookup.fetches = 6
    lookup.cache.clear()
    lookup.find(vacancy())
    assert len(calls) == 2


def test_hosted_ats_namespace_must_match_employer():
    employer = dict(EMPLOYER, ats="lever", slug="fixture")
    assert vacancy_belongs_to(vacancy(href="https://jobs.lever.co/fixture/42"), employer)
    assert not vacancy_belongs_to(vacancy(href="https://jobs.lever.co/attacker/42"), employer)


def test_three_distinct_shadow_runs_then_two_daily_canary_attempts():
    data = {"sends": {}}
    rollout = rollout_state(data, "revision-1")
    for identity in ("1", "1", "2"):
        complete_contact_shadow(rollout, identity)
    assert rollout["shadow_runs"] == 2
    assert can_send_new_contact(data, rollout, NOW)[0] is False
    complete_contact_shadow(rollout, "3")
    assert can_send_new_contact(data, rollout, NOW) == (True, "canary")
    rollout["live_since"] = NOW.isoformat()
    data["sends"] = {str(i): {"contact_route": "official_careers_inbox", "created_at": NOW.isoformat(), "status": "uncertain"} for i in range(2)}
    assert not can_send_new_contact(data, rollout, NOW)[0]
    assert can_send_new_contact(data, rollout, NOW + timedelta(days=8)) == (True, "live")
    assert rollout_state(data, "revision-2")["shadow_runs"] == 0


def test_corrupt_rollout_does_not_enable_contacts():
    data = {"contacts_rollout": {"revision": "x", "shadow_runs": "3", "scheduled_ids": []}}
    rollout = rollout_state(data, "x")
    complete_contact_shadow(rollout, "1")
    assert not can_send_new_contact(data, rollout, NOW)[0]


def test_junior_missing_tenure_allowed_without_inventing_requirements():
    result = evaluate_job(vacancy(), now=NOW)
    assert result["email_safe"] and result["junior_experience_exception"]
    assert "Experience requirement undisclosed" in result["warnings"]


@pytest.mark.parametrize("changes", [
    {"source_text": "Full-time research reports. Minimum 2 years professional experience."},
    {"source_text": "Full-time research reports. Azure certification mandatory."},
    {"source_text": "Full-time research reports. Manage a team of research analysts."},
    {"source_text": "Research reports.", "employment_type": ""},
    {"source_text": "Full-time research reports. MBA mandatory."},
])
def test_junior_exception_does_not_bypass_other_gates(changes):
    assert not evaluate_job(vacancy(**changes), now=NOW)["email_safe"]


def test_generic_analyst_title_is_not_enough_for_junior_exception():
    assert not evaluate_job(vacancy(title="Research Analyst", source_text="Full-time research and analytical reports."), now=NOW)["email_safe"]


def test_optional_cards_have_independent_daily_limit_and_no_application_side_effect(tmp_path):
    store = StateStore(tmp_path / "scout.json", tmp_path / "history.json")
    for i in range(4):
        analysis = {"application_url": f"https://fixture.org/jobs/{i}", "date_posted": (NOW-timedelta(hours=1)).isoformat(),
                    "email_safe": True, "company": "Fixture", "job_title": "Junior Research Analyst", "evidence": ["Wrote reports."]}
        analysis["job_id"] = store.upsert_job(analysis)["id"]
        assert reserve_manual_card(store, analysis, now=NOW) is (i < 3)
        assert not reserve_manual_card(store, analysis, now=NOW)
        assert not store.known_submitted(analysis["job_id"])
    assert len(store.data["sends"]) == 0
    assert store.data["manual_alert_days"]["2026-10-23"] == 3


@pytest.mark.parametrize("action", ["skipped", "referral"])
def test_manual_feedback_blocks_conflicting_outreach_and_survives_reload(tmp_path, action):
    store = StateStore(tmp_path / "scout.json", tmp_path / "history.json")
    job = store.upsert_job({"application_url": "https://fixture.org/jobs/1", "company": "Fixture"})
    store.save()
    directory = tmp_path / "feedback"
    directory.mkdir()
    (directory / "1.json").write_text(json.dumps({"job_id": job["id"], "action": action}), encoding="utf-8")
    assert store.ingest_feedback(directory) == 1
    reloaded = StateStore(store.path, store.history_path)
    assert reloaded.reserve_send(job["id"], "hr@fixture.org") is None
    assert reloaded.reserve_application(job["id"], "fixture") is None
    assert not reloaded.known_submitted(job["id"])
    assert len(callback_data(job["id"], action, "fixture-token").encode()) <= 64


def test_14_day_review_is_outcome_based_not_a_volume_promise():
    data = {"jobs": {}}
    assert review_reach(data, NOW) is None
    assert "coverage" in review_reach(data, NOW + timedelta(days=14))


def test_alert_body_supplies_destinations_not_original_dates():
    from scout_mail import alert_destinations
    content = '<script type="application/ld+json">' + json.dumps({"@type":"JobPosting", "title":"Research Analyst", "datePosted":"2099-01-01", "url":"https://fixture.org/jobs/42"}) + '</script><a href="https://email.linkedin.com/t/1">Jobs</a>'
    links = alert_destinations("text/html", content)
    assert links[0] == ("https://fixture.org/jobs/42", "Research Analyst")
    assert "2099" not in str(links)


def test_alert_explicit_tracker_destination_precedes_blocked_outer_tracker():
    from scout_mail import alert_destinations
    links = alert_destinations("text/plain", "https://email.linkedin.com/click?url=https%3A%2F%2Ffixture.org%2Fjobs%2F42")
    assert links == [("https://fixture.org/jobs/42", "")]
    assert alert_destinations("text/plain", "https://email.linkedin.com/click?url=https%3A%2F%2F127.0.0.1%2Fjobs%2F42") == []


def test_nested_job_locations_do_not_crash_source_parser():
    from scout_sources import _locations
    assert _locations([{"address": [{"addressLocality": "Gurugram", "addressCountry": "India"}]}]) == "Gurugram, India"
    assert _locations({"address": None}) == ""


def test_registry_prioritisation_retains_every_source_and_exploration():
    from scout_sources import prioritize_registry
    registry = [{"name": str(i), "ats": "html"} for i in range(5)]
    jobs = {"a": {"source_name":"html:4", "first_seen":NOW.isoformat(), "outcome":"interview"}}
    ranked = prioritize_registry(registry, jobs, NOW)
    assert sorted(e["name"] for e in ranked) == [str(i) for i in range(5)]
    assert ranked.index(registry[4]) <= 1


def test_optional_action_notification_has_six_signed_controls(monkeypatch):
    import scout_agent
    notices = []
    monkeypatch.setenv("TELEGRAM_TRACKING_READY", "true")
    monkeypatch.setattr(scout_agent, "TELEGRAM_BOT_TOKEN", "fixture-token")
    monkeypatch.setattr(scout_agent, "send_telegram", lambda message, markup: notices.append((message,markup)) or True)
    assert scout_agent._send_apply_alert({"job_id":"a"*24, "manual_optional":True,
             "manual_draft":"Fixture draft", "job_title":"Junior Analyst", "application_url":"https://fixture.org/jobs/1"})
    text, markup = notices[0]
    assert "not an approval" in text
    buttons = sum(markup["inline_keyboard"], [])
    assert [b["text"] for b in buttons][:3] == ["Applied", "Skipped", "Referral requested"]
    assert all(len(b["callback_data"].encode()) <= 64 for b in buttons)


def test_full_runtime_shadows_new_contacts_without_stopping_vacancy_emails(tmp_path, monkeypatch):
    import copy
    import scout_agent as agent
    import scout_runtime
    import scout_contacts
    monkeypatch.chdir(tmp_path)
    for attr, filename in (("HISTORY_FILE", "history.json"), ("FOLLOW_UP_FILE", "follow.json"),
                           ("SUPPRESSION_FILE", "suppress.json"), ("RUN_REPORT_FILE", "report.json")):
        monkeypatch.setattr(agent, attr, str(tmp_path / filename))
    for key, value in {"GITHUB_ACTIONS":"true", "GITHUB_TOKEN":"fixture", "GITHUB_REPOSITORY":"owner/repo",
                       "ZERO_COST_VERIFIED":"true", "GITHUB_EVENT_NAME":"schedule", "SCOUT_SHADOW_RUNS":"0",
                       "HANDS_OFF_ONLY":"true", "SCOUT_OPTIONAL_ACTIONS":"false", "SCOUT_ENCRYPT_STATE":"false"}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(scout_runtime, "GitHubWriter", None, raising=False)
    class Writer:
        def __init__(self, **kwargs): pass
        def _call(self, *args): return {"private":False}
        def __call__(self, *args): return True
    monkeypatch.setattr("scout_persistence.GitHubWriter", Writer)
    monkeypatch.setattr("scout_mail.monitor_replies", lambda *a, **kw: {"status":"ok", "new_human_replies":0})
    monkeypatch.setattr(scout_runtime, "email_window", lambda: True)
    monkeypatch.setattr(agent, "SEND_EMAILS", True)
    monkeypatch.setattr(agent, "SMTP_PREFLIGHT", False)
    monkeypatch.setattr(agent, "process_pending_follow_ups", lambda *a: {"sent":0,"failed":0,"pending":0})
    monkeypatch.setattr(agent, "schedule_follow_up", lambda *a: None)
    monkeypatch.setattr(agent, "_is_suppressed", lambda *a: False)
    monkeypatch.setattr(agent, "_recipient_in_cooldown", lambda *a: False)
    monkeypatch.setattr(agent, "enrich_post", lambda p: dict(p))
    monkeypatch.setattr(scout_contacts, "contact_registry", lambda: [EMPLOYER])
    monkeypatch.setattr(agent, "_request_with_retries", lambda url, **kw: SimpleNamespace(status_code=200,url=url,text="Send your resume to careers@fixture.org."))
    sent = []
    monkeypatch.setattr(agent, "send_email", lambda *args: sent.append(args) or True)
    original = datetime.now(timezone.utc).isoformat()
    posts = [vacancy(date_posted=original),
             vacancy(company="Other Employer", href="https://other.org/jobs/1", date_posted=original,
                     source_text="Full-time day shift. Assist research analysts with reports. Send your resume to recruiter@other.org.")]
    monkeypatch.setattr(agent, "search_recruiter_posts", lambda: copy.deepcopy(posts))
    monkeypatch.setattr(agent, "LAST_SEARCH_STATS", {"source_stats":{"fixture":{"status":"ok"}}})
    for i in range(1, 5):
        monkeypatch.setenv("GITHUB_RUN_ID", str(i))
        result = agent.main(notify=False)
        assert result["contact_discovery"]["shadow_runs_completed"] == min(3, i)
        assert result["funnel"]["verified_fresh"] >= 1
        assert result["funnel"]["reachable"] >= 1
        if i < 4:
            assert not any(args[0] == "careers@fixture.org" for args in sent)
    assert [args[0] for args in sent] == ["recruiter@other.org", "careers@fixture.org"]
    assert result["emails_accepted_by_smtp"] == 1
    assert "careers@fixture.org" not in json.dumps(result)
    assert "recruiter@other.org" not in json.dumps(result)


def test_manual_and_dry_runs_do_not_advance_contact_shadow(tmp_path, monkeypatch):
    # Completion is restricted at the orchestration boundary, not by a UI flag.
    from pathlib import Path
    source = (Path(__file__).parents[1] / "scout_runtime.py").read_text(encoding="utf-8")
    assert 'cloud and trigger == "schedule" and not dry_run' in source
    assert 'complete_contact_shadow(contact_rollout, identity.split(":", 1)[0])' in source


def test_existing_submission_is_not_reopened_by_new_contact_metadata(tmp_path):
    store = StateStore(tmp_path / "scout.json", tmp_path / "history.json")
    analysis = {"application_url":"https://fixture.org/jobs/1", "company":"Fixture Research"}
    old = store.upsert_job(analysis)
    old.update(submitted=True, application_route="user_reported")
    store.save()
    reloaded = StateStore(store.path, store.history_path)
    new = reloaded.upsert_job(dict(analysis, contact_route="official_careers_inbox", email_safe=True))
    assert new["submitted"] and new["application_route"] == "user_reported"
    assert reloaded.reserve_send(new["id"], "careers@fixture.org") is None
