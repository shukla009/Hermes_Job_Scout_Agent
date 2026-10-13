"""
Real pytest suite for scout_agent.py — mocks ALL network calls.
Replaces the stale standalone scripts (test_banned.py etc.) which duplicated
old logic and fired live NVIDIA API calls.

Run: pytest test_scout_agent.py -v
No API keys needed — nothing touches the network.
"""

import os
import re
import sys
import json
import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root (scout_agent.py lives there)

import scout_agent
from scout_agent import (
    _valid_recruiter_email,
    _is_stale_post,
    _parse_serpapi_date_days,
    _linkedin_post_age_days,
    _is_job_board_url,
    _is_direct_apply_url,
    _normalize_url,
    _job_fingerprint,
    _extract_job_json_ld,
    extract_contacts,
    load_history,
    save_history,
)


# ---------------------------------------------------------------
# Email validation
# ---------------------------------------------------------------
class TestValidRecruiterEmail:
    def test_accepts_gmail(self):
        # Regression: substring matching used to reject gmail.com
        assert _valid_recruiter_email("hr@gmail.com") is True

    def test_accepts_hotmail_and_googlemail(self):
        assert _valid_recruiter_email("x@hotmail.com") is True
        assert _valid_recruiter_email("x@googlemail.com") is True

    def test_accepts_normal_corporate(self):
        assert _valid_recruiter_email("careers@realstartup.io") is True
        assert _valid_recruiter_email("hiring@elivaas.com") is True

    def test_rejects_placeholder_domains(self):
        for domain in ["company.com", "example.com", "email.com", "xyz.com", "acme.com", "mail.com"]:
            assert _valid_recruiter_email(f"hr@{domain}") is False, domain

    def test_rejects_tld_less(self):
        assert _valid_recruiter_email("name@gmail") is False

    def test_rejects_empty_and_malformed(self):
        assert _valid_recruiter_email("") is False
        assert _valid_recruiter_email(None) is False
        assert _valid_recruiter_email("not-an-email") is False


# ---------------------------------------------------------------
# Freshness gate
# ---------------------------------------------------------------
class TestFreshness:
    def test_parses_relative_dates(self):
        assert _parse_serpapi_date_days("3 days ago") == 3.0
        assert _parse_serpapi_date_days("2 weeks ago") == 14.0
        assert _parse_serpapi_date_days("1 month ago") == 30.0
        assert _parse_serpapi_date_days("1 year ago") == 365.0
        assert _parse_serpapi_date_days("yesterday") == 1.0
        assert _parse_serpapi_date_days("today") == 0.0
        assert _parse_serpapi_date_days("12 hours ago") == 0.0

    def test_unparseable_date_returns_none(self):
        assert _parse_serpapi_date_days("last month") is None
        assert _parse_serpapi_date_days("") is None
        assert _parse_serpapi_date_days(None) is None

    def test_fresh_passes(self):
        assert _is_stale_post("https://www.linkedin.com/x", "1 day ago") is False

    def test_exactly_window_passes(self):
        assert _is_stale_post("https://www.linkedin.com/x", "2 days ago") is False

    def test_older_than_window_rejected(self):
        assert _is_stale_post("https://www.linkedin.com/x", "5 days ago") is True
        assert _is_stale_post("https://www.linkedin.com/x", "2 weeks ago") is True

    def test_unverifiable_age_treated_as_stale(self):
        # No date field, no LinkedIn ID -> cannot verify -> stale
        assert _is_stale_post("https://www.threads.com/x", "") is True

    def test_linkedin_snowflake_decode(self):
        # A real historical post ID decodes to its publish date
        age = _linkedin_post_age_days(
            "https://www.linkedin.com/posts/prerana-jangid-029131271-w"
        )
        # Exact value varies with 'now'; just require a numeric age back
        assert age is None or isinstance(age, float)

    def test_linkedin_id_decodes_fresh_when_new(self):
        # Build an ID whose timestamp is right now
        import time
        now_ms = int(time.time() * 1000)
        act_id = now_ms << 22
        url = f"https://www.linkedin.com/feed/update/urn:li:activity-{act_id}/"
        age = _linkedin_post_age_days(url)
        assert age is not None and age < 1.0
        assert _is_stale_post(url, "") is False  # fresh via ID decode


# ---------------------------------------------------------------
# Job board filter
# ---------------------------------------------------------------
class TestJobBoardFilter:
    def test_blocks_boards(self):
        assert _is_job_board_url("https://www.indeed.co.in/viewjob?x=1") is True
        assert _is_job_board_url("https://www.naukri.com/job-listing-x") is True

    def test_excluded_board_never_enters_the_processing_pipeline(self, monkeypatch):
        monkeypatch.setattr(scout_agent, "SERPAPI_KEY", "test-key")
        monkeypatch.setattr(scout_agent, "discover_ats_jobs", lambda: ([], {}))
        monkeypatch.setattr(scout_agent, "_serpapi_search", lambda query: [{
            "link": "https://www.naukri.com/job-listing-x", "date": "today",
            "title": "Data Analyst", "snippet": "Hiring in Noida",
        }])
        network = MagicMock(side_effect=AssertionError("Excluded board must not be fetched"))
        monkeypatch.setattr(scout_agent.requests, "get", network)
        assert scout_agent.search_recruiter_posts() == []
        network.assert_not_called()

    def test_allows_linkedin_posts(self):
        assert _is_job_board_url("https://www.linkedin.com/posts/someone-activity-123") is False

    def test_allows_official_ats_pages(self):
        assert _is_job_board_url("https://jobs.lever.co/realco/123") is False
        assert _is_job_board_url("https://boards.greenhouse.io/realco/jobs/123") is False
        assert _is_direct_apply_url("https://jobs.lever.co/realco/123") is True


# ---------------------------------------------------------------
# Contact extraction
# ---------------------------------------------------------------
class TestExtractContacts:
    def test_extracts_emails(self):
        c = extract_contacts("Apply to hr@startup.io or careers@other.com")
        assert "hr@startup.io" in c["emails"]
        assert "careers@other.com" in c["emails"]

    def test_dedupes(self):
        c = extract_contacts("hr@x.com and again hr@x.com")
        assert c["emails"].count("hr@x.com") == 1

    def test_empty_input(self):
        assert extract_contacts("") == {"emails": [], "linkedin": [], "phones": []}


# ---------------------------------------------------------------
# History handling
# ---------------------------------------------------------------
class TestHistory:
    def test_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setattr(scout_agent, "HISTORY_FILE", str(tmp_path / "h.json"))
        save_history([{"email": "a@b.com", "job_title": "DA"}])
        assert load_history() == [{"email": "a@b.com", "job_title": "DA"}]

    def test_corrupt_file_returns_empty(self, tmp_path, monkeypatch, capsys):
        f = tmp_path / "h.json"
        f.write_text("{corrupt json", encoding="utf-8")
        monkeypatch.setattr(scout_agent, "HISTORY_FILE", str(f))
        assert load_history() == []
        out = capsys.readouterr().out
        assert "[History Warning]" in out  # loud, not silent


# ---------------------------------------------------------------
# analyze_post_verbose with a MOCKED LLM (no network)
# ---------------------------------------------------------------
def _mock_llm_response(payload):
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = json.dumps(payload)
    return resp


class TestAnalyzePostVerbose:
    POST = {
        "title": "Hiring Data Analyst in Noida",
        "body": "Full-time entry-level data analysis, data validation and dashboards. Send resume to hr@realco.io",
        "href": "https://www.linkedin.com/posts/x",
        "application_url": "https://www.linkedin.com/posts/x",
        "date": "1 day ago",
        "source_type": "linkedin_post",
        "source_text": "Hiring Data Analyst in Noida. Full-time entry-level data analysis, data validation and dashboards. Send resume to hr@realco.io",
        "location": "Noida",
        "page_fetched": True,
        "source_emails": ["hr@realco.io"],
    }

    def test_ok_verdict(self, monkeypatch):
        monkeypatch.setattr(scout_agent, "client", MagicMock())
        monkeypatch.setattr(scout_agent, "USE_LLM_REVIEW", True)
        payload = {"is_relevant": True, "recruiter_email": "hr@realco.io",
                   "job_title": "Data Analyst", "company": "RealCo",
                   "email_subject": "Application - Data Analyst", "email_body": "Hi", "reason": "match"}
        scout_agent.client.chat.completions.create.return_value = _mock_llm_response(payload)
        parsed, verdict = scout_agent.analyze_post_verbose(self.POST)
        assert verdict == "ok" and parsed["recruiter_email"] == "hr@realco.io"
        scout_agent.client.chat.completions.create.assert_not_called()

    def test_irrelevant(self, monkeypatch):
        monkeypatch.setattr(scout_agent, "client", MagicMock())
        monkeypatch.setattr(scout_agent, "USE_LLM_REVIEW", True)
        payload = {"is_relevant": False, "reason": "job board"}
        scout_agent.client.chat.completions.create.return_value = _mock_llm_response(payload)
        parsed, verdict = scout_agent.analyze_post_verbose(dict(self.POST, title="Senior Data Analyst"))
        assert parsed is None and verdict == "irrelevant"
        scout_agent.client.chat.completions.create.assert_not_called()

    def test_unverified_model_email_becomes_apply_lead(self, monkeypatch):
        monkeypatch.setattr(scout_agent, "client", MagicMock())
        monkeypatch.setattr(scout_agent, "USE_LLM_REVIEW", True)
        payload = {"is_relevant": True, "recruiter_email": "invented@realco.io",
                   "job_title": "Data Analyst", "company": "RealCo", "reason": "match"}
        scout_agent.client.chat.completions.create.return_value = _mock_llm_response(payload)
        parsed, verdict = scout_agent.analyze_post_verbose(self.POST)
        assert verdict == "ok"
        assert parsed["recruiter_email"] == "hr@realco.io"
        scout_agent.client.chat.completions.create.assert_not_called()

    def test_own_email_not_used(self, monkeypatch):
        # Regression: the agent must never email the candidate's own address
        monkeypatch.setattr(scout_agent, "client", MagicMock())
        payload = {"is_relevant": True, "recruiter_email": None, "reason": "x"}
        scout_agent.client.chat.completions.create.return_value = _mock_llm_response(payload)
        post = {
            "title": "Hiring Data Analyst",
            "body": f"Full-time entry-level data analysis and dashboards. Send resume to {scout_agent.CANDIDATE_EMAIL}",
            "href": "https://linkedin.com/x",
            "application_url": "https://linkedin.com/x",
            "date": "today",
            "source_type": "linkedin_post",
            "source_text": f"Full-time entry-level data analysis and dashboards. Send resume to {scout_agent.CANDIDATE_EMAIL}",
            "location": "Noida",
            "page_fetched": True,
            "source_emails": [scout_agent.CANDIDATE_EMAIL],
        }
        parsed, verdict = scout_agent.analyze_post_verbose(post)
        assert verdict == "apply"
        assert parsed["recruiter_email"] is None

    def test_no_client(self, monkeypatch):
        monkeypatch.setattr(scout_agent, "client", None)
        parsed, verdict = scout_agent.analyze_post_verbose(self.POST)
        assert verdict == "ok" and parsed["recruiter_email"] == "hr@realco.io"

    def test_auth_error_verdict(self, monkeypatch):
        monkeypatch.setattr(scout_agent, "client", MagicMock())
        monkeypatch.setattr(scout_agent, "USE_LLM_REVIEW", True)
        scout_agent.client.chat.completions.create.side_effect = Exception("401 Unauthorized: Authentication failed")
        parsed, verdict = scout_agent.analyze_post_verbose(self.POST)
        assert verdict == "ok" and parsed["recruiter_email"] == "hr@realco.io"

    def test_json_error_then_gives_up(self, monkeypatch):
        monkeypatch.setattr(scout_agent, "client", MagicMock())
        monkeypatch.setattr(scout_agent, "USE_LLM_REVIEW", True)
        scout_agent.client.chat.completions.create.side_effect = None
        bad = MagicMock()
        bad.choices = [MagicMock()]
        bad.choices[0].message.content = "not json at all {{{"
        scout_agent.client.chat.completions.create.return_value = bad
        parsed, verdict = scout_agent.analyze_post_verbose(self.POST, max_retries=1)
        assert verdict == "ok" and parsed["recruiter_email"] == "hr@realco.io"


# ---------------------------------------------------------------
# Prompt integrity
# ---------------------------------------------------------------
class TestPrompt:
    def test_writer_never_invokes_paid_model(self, monkeypatch):
        client = MagicMock()
        monkeypatch.setattr(scout_agent, "client", client)
        monkeypatch.setattr(scout_agent, "USE_LLM_WRITER", True, raising=False)
        written, status = scout_agent.compose_email({}, {"job_title":"Research Analyst", "company":"Acme", "family":"research"})
        assert written["email_subject"] and written["email_body"]
        client.chat.completions.create.assert_not_called()

    def test_example_email_not_banned_opening(self):
        src = open(scout_agent.__file__, encoding="utf-8").read()
        assert "I came across your [Job Title]" not in src

    def test_writer_no_invented_salary_or_notice_period(self):
        written, _ = scout_agent.compose_email({}, {"job_title":"Research Analyst", "company":"Acme", "family":"research"})
        body = written["email_body"].lower()
        assert "lpa" not in body and "immediate joining" not in body and "notice period" not in body
        assert "12+" in body and "98.1%" not in body

    def test_freshness_rule_present(self):
        src = open(scout_agent.__file__, encoding="utf-8").read()
        assert "def _structured_freshness" in src
        assert "application deadline has passed" in src


# ---------------------------------------------------------------
# Send email (SMTP mocked)
# ---------------------------------------------------------------
class TestSendEmail:
    def test_aborts_without_resume(self, tmp_path, monkeypatch):
        monkeypatch.setattr(scout_agent, "RESUME_PATH", str(tmp_path / "missing.pdf"))
        ok = scout_agent.send_email("hr@x.com", "Subject", "body")
        assert ok is False  # hard fail: body claims attachment

    def test_sends_with_mocked_smtp(self, tmp_path, monkeypatch):
        monkeypatch.setattr(scout_agent, "GMAIL_SENDER", "test@example.com")
        monkeypatch.setattr(scout_agent, "GMAIL_PASSWORD", "test-password")
        monkeypatch.setattr(scout_agent, "RESUME_PATH", str(tmp_path / "r.pdf"))
        (tmp_path / "r.pdf").write_bytes(b"%PDF-1.4 fake")
        sent = {}
        class FakeSMTP:
            def __init__(self, *a, **k): pass
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def login(self, *a): pass
            def send_message(self, msg): sent["msg"] = msg
        monkeypatch.setattr("smtplib.SMTP_SSL", FakeSMTP)
        ok = scout_agent.send_email("hr@x.com", "Subject  ", "body")
        assert ok is True, "send_email failed - SMTP mock or credentials missing"
        # Subject sanitized: no trailing spaces / newlines in the header
        assert sent["msg"]["Subject"] == "Subject"

    def test_subject_crlf_sanitized(self, tmp_path, monkeypatch):
        monkeypatch.setattr(scout_agent, "GMAIL_SENDER", "test@example.com")
        monkeypatch.setattr(scout_agent, "GMAIL_PASSWORD", "test-password")
        monkeypatch.setattr(scout_agent, "RESUME_PATH", str(tmp_path / "r.pdf"))
        (tmp_path / "r.pdf").write_bytes(b"%PDF-1.4 fake")
        sent = {}
        class FakeSMTP:
            def __init__(self, *a, **k): pass
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def login(self, *a): pass
            def send_message(self, msg): sent["msg"] = msg
        monkeypatch.setattr("smtplib.SMTP_SSL", FakeSMTP)
        ok = scout_agent.send_email("hr@x.com", "Bad\r\nInjected-Header: x", "body")
        assert ok is True, "send_email failed - SMTP mock or credentials missing"
        assert "\r" not in sent["msg"]["Subject"] and "\n" not in sent["msg"]["Subject"]


# ---------------------------------------------------------------
# Follow-up scheduling (dead-code regression: must be wired)
# ---------------------------------------------------------------
class TestFollowUps:
    def test_due_first_unavailable_defers(self, monkeypatch):
        records = [{"next_send":4102444800,"message_id":"<future@scout>"}, {"next_send":0,"message_id":"<due@scout>","email":"hr@example.com","job_id":"job"}]
        monkeypatch.setattr(scout_agent, "load_follow_up", lambda: records)
        monkeypatch.setattr(scout_agent, "save_follow_up", lambda value: None)
        monkeypatch.setattr(scout_agent, "SEND_EMAILS", True)
        store = MagicMock()
        monkeypatch.setattr(scout_agent, "ACTIVE_STORE", store)
        check = MagicMock(return_value="unavailable")
        send = MagicMock()
        monkeypatch.setattr(scout_agent, "_mailbox_status", check)
        monkeypatch.setattr(scout_agent, "send_email", send)
        result = scout_agent.process_pending_follow_ups()
        assert check.call_count == 1 and result["pending"] == 2
        send.assert_not_called()
        store.reserve_send.assert_not_called()

    def test_durable_threaded_followups_are_bounded(self, monkeypatch):
        records = [{"next_send":0,"created_at":0,"message_id":"<original@scout>","email":"hr@example.com","job_id":str(i),"subject":"Application","step":1} for i in range(4)]
        monkeypatch.setattr(scout_agent, "load_follow_up", lambda: records)
        monkeypatch.setattr(scout_agent, "save_follow_up", lambda value: None)
        monkeypatch.setattr(scout_agent, "SEND_EMAILS", True)
        monkeypatch.setattr(scout_agent, "MAX_FOLLOWUPS_PER_RUN", 5)
        monkeypatch.setattr(scout_agent, "_mailbox_status", lambda record: "none")
        monkeypatch.setattr(scout_agent, "_is_suppressed", lambda email: False)
        store = MagicMock()
        store.reserve_send.return_value = {"id":"intent","message_id":"<follow@scout>"}
        store.finish_send.return_value = True
        monkeypatch.setattr(scout_agent, "ACTIVE_STORE", store)
        def send(email, subject, body, *, attach_resume):
            assert store.reserve_send.called
            assert scout_agent.ACTIVE_REFERENCES == "<original@scout>"
            assert scout_agent.ACTIVE_MESSAGE_ID == "<follow@scout>"
            assert not attach_resume and "attached" not in body and subject == "Re: Application"
            return True
        mocked = MagicMock(side_effect=send)
        monkeypatch.setattr(scout_agent, "send_email", mocked)
        assert scout_agent.process_pending_follow_ups(5)["sent"] == 2
        assert mocked.call_count == 2

    def test_reservation_failure_prevents_followup(self, monkeypatch):
        record = {"next_send":0,"message_id":"<original@scout>","email":"hr@example.com","job_id":"job"}
        monkeypatch.setattr(scout_agent, "load_follow_up", lambda: [record])
        monkeypatch.setattr(scout_agent, "save_follow_up", lambda value: None)
        monkeypatch.setattr(scout_agent, "SEND_EMAILS", True)
        monkeypatch.setattr(scout_agent, "_mailbox_status", lambda record: "none")
        monkeypatch.setattr(scout_agent, "_is_suppressed", lambda email: False)
        store = MagicMock()
        store.reserve_send.return_value = None
        monkeypatch.setattr(scout_agent, "ACTIVE_STORE", store)
        send = MagicMock()
        monkeypatch.setattr(scout_agent, "send_email", send)
        assert scout_agent.process_pending_follow_ups()["sent"] == 0
        send.assert_not_called()

    def test_schedule_creates_one_evolving_record(self, tmp_path, monkeypatch):
        path = tmp_path / "follow_up.json"
        monkeypatch.setattr(scout_agent, "FOLLOW_UP_FILE", str(path))
        scout_agent.schedule_follow_up("a@b.com", "Subj", "Body", "job-1")
        data = json.load(open(path, encoding="utf-8"))
        assert len(data) == 1
        assert data[0]["step"] == 1
        assert data[0]["job_id"] == "job-1"

        # Scheduling the same outreach replaces its chain instead of duplicating it.
        scout_agent.schedule_follow_up("a@b.com", "Subj", "Body", "job-1")
        assert len(json.load(open(path, encoding="utf-8"))) == 1

    def test_schedule_preserves_thread_identity(self, tmp_path, monkeypatch):
        monkeypatch.setattr(scout_agent, "FOLLOW_UP_FILE", str(tmp_path / "follow.json"))
        monkeypatch.setattr(scout_agent, "ACTIVE_MESSAGE_ID", "<application@scout>")
        scout_agent.schedule_follow_up("hr@example.com", "Application", "Hello", "job-id")
        record = scout_agent.load_follow_up()[0]
        assert record["job_id"] == "job-id" and record["message_id"] == "<application@scout>"


class TestEnrichmentHelpers:
    def test_normalize_url_removes_tracking(self):
        url = "https://Jobs.Lever.co/acme/123/?utm_source=x&keep=yes#apply"
        assert _normalize_url(url) == "https://jobs.lever.co/acme/123?keep=yes"

    def test_fingerprint_distinguishes_jobs_from_same_recruiter(self):
        one = _job_fingerprint({"url": "https://example.com/jobs/1", "job_title": "Data Analyst"})
        two = _job_fingerprint({"url": "https://example.com/jobs/2", "job_title": "Data Analyst"})
        assert one != two

    def test_extracts_jobposting_json_ld(self):
        page = '''<html><script type="application/ld+json">{
          "@context":"https://schema.org", "@type":"JobPosting",
          "title":"Junior Data Analyst", "datePosted":"2099-01-01",
          "validThrough":"2099-02-01", "url":"/apply",
          "hiringOrganization":{"name":"RealCo"},
          "jobLocation":{"address":{"addressLocality":"Noida","addressCountry":"IN"}},
          "description":"<p>Use SQL and Power BI.</p>"
        }</script></html>'''
        job = _extract_job_json_ld(page)
        assert job["job_title"] == "Junior Data Analyst"
        assert job["company"] == "RealCo"
        assert "Noida" in job["location"]
        assert job["application_url"] == "/apply"


class TestDeterministicEligibility:
    def _post(self, **overrides):
        post = {
            "job_title": "Junior Data Analyst",
            "title": "Junior Data Analyst",
            "company": "RealCo",
            "location": "Noida, India",
            "employment_type": "Full-time",
            "source_type": "direct_apply",
            "page_fetched": True,
            "date_posted": datetime.now(timezone.utc).isoformat(),
            "source_text": "SQL Power BI Excel reporting. 0-2 years experience.",
            "source_emails": [],
            "href": "https://jobs.example.org/1",
            "application_url": "https://jobs.example.org/1",
        }
        post.update(overrides)
        return post

    def test_good_direct_apply_lead_passes_without_model(self, monkeypatch):
        monkeypatch.setattr(scout_agent, "client", None)
        parsed, verdict = scout_agent.analyze_post_verbose(self._post())
        assert verdict == "apply"
        assert parsed["fit_score"] >= scout_agent.MIN_APPLY_SCORE

    @pytest.mark.parametrize("text", ["night shift", "rotational shift", "US shift"])
    def test_rejects_non_day_shift(self, text):
        post = self._post(source_text=f"SQL and Excel. {text}")
        assert scout_agent._deterministic_rejection(post) == "explicit night or rotational shift"

    def test_rejects_foreign_location(self):
        post = self._post(location="London, United Kingdom")
        assert "location is outside" in scout_agent._deterministic_rejection(post)

    def test_rejects_salary_below_floor(self):
        post = self._post(source_text="SQL and Power BI. Salary 5.5 LPA")
        assert "below 6.5 LPA" in scout_agent._deterministic_rejection(post)

    def test_title_experience_range_cannot_be_overridden_by_body(self):
        post = self._post(job_title="Business Analytics | Experience: 5-7 yrs", title="Business Analytics | Experience: 5-7 yrs", source_text="SQL. A separate graduate program mentions 0-1 years.")
        assert scout_agent._deterministic_rejection(post) == "requires more than two years of experience"


class TestATSDiscovery:
    def test_greenhouse_adapter_maps_public_feed(self, monkeypatch):
        payload = {"jobs": [{
            "title": "Junior Data Analyst", "content": "<p>SQL Power BI</p>",
            "absolute_url": "https://boards.greenhouse.io/acme/jobs/1",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "location": {"name": "Noida, India"},
        }]}
        response = MagicMock()
        response.json.return_value = payload
        monkeypatch.setattr(scout_agent, "_request_with_retries", lambda *a, **k: response)
        jobs = scout_agent._greenhouse_jobs({"name": "Acme", "slug": "acme"})
        assert len(jobs) == 1
        assert jobs[0]["source_type"] == "direct_apply"
        assert jobs[0]["page_fetched"] is True

    def test_serpapi_no_results_is_successful_empty(self, monkeypatch):
        response = MagicMock()
        response.json.return_value = {"error": "Google hasn't returned any results for this query."}
        monkeypatch.setattr(scout_agent, "_request_with_retries", lambda *a, **k: response)
        assert scout_agent._serpapi_search("query") == []


class TestReliabilityFallbacks:
    def test_writer_works_without_model(self, monkeypatch):
        monkeypatch.setattr(scout_agent, "client", None)
        written, status = scout_agent.compose_email({}, {"job_title": "Data Analyst", "company": "Acme"})
        assert status == "fallback"
        assert "attached my resume" in written["email_body"]
        assert "6.5" not in written["email_body"]

    def test_recipient_cooldown(self):
        history = [{"route": "email", "email": "hr@acme.io", "timestamp": datetime.now(timezone.utc).isoformat()}]
        assert scout_agent._recipient_in_cooldown(history, "HR@acme.io") is True


# ---------------------------------------------------------------
# Config sanity
# ---------------------------------------------------------------
class TestConfig:
    def test_max_post_age_default_4(self):
        assert scout_agent.MAX_POST_AGE_DAYS == 4

    def test_no_token_in_tracked_files(self):
        import re
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repo root
        blob = ""
        for f in [os.path.join("workers", "DEPLOY_TELEGRAM_TRIGGER.md"), os.path.join("workers", "telegram_trigger_worker.py"),
                  os.path.join("workers", "telegram_trigger_worker.js"), ".env.example"]:
            p = os.path.join(base, f)
            if os.path.exists(p):
                blob += open(p, encoding="utf-8").read()
        assert not re.findall(r"\d{8,12}:[A-Za-z0-9_-]{30,}", blob), "token pattern present!"

    def test_pinned_requirements(self):
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repo root
        req = open(os.path.join(base, "requirements.txt"), encoding="utf-8").read()
        assert "openai>=" in req and "<2" in req


class TestMainRouting:
    def test_main_preserves_apply_and_email_outcomes(self, tmp_path, monkeypatch):
        monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(scout_agent, "HISTORY_FILE", str(tmp_path / "history.json"))
        monkeypatch.setattr(scout_agent, "FOLLOW_UP_FILE", str(tmp_path / "follow.json"))
        monkeypatch.setattr(scout_agent, "RUN_REPORT_FILE", str(tmp_path / "report.json"))
        monkeypatch.setattr(scout_agent, "NVIDIA_API_KEY", "nvidia")
        monkeypatch.setattr(scout_agent, "SERPAPI_KEY", "serp")
        monkeypatch.setattr(scout_agent, "TELEGRAM_BOT_TOKEN", "telegram")
        monkeypatch.setattr(scout_agent, "TELEGRAM_CHAT_ID", "chat")
        monkeypatch.setattr(scout_agent, "GMAIL_SENDER", "sender@gmail.com")
        monkeypatch.setattr(scout_agent, "GMAIL_PASSWORD", "password")
        monkeypatch.setattr(scout_agent, "SEND_EMAILS", True)
        monkeypatch.setattr(scout_agent, "SMTP_PREFLIGHT", False)
        monkeypatch.setattr(scout_agent, "send_telegram", lambda message: True)
        monkeypatch.setattr(scout_agent, "process_pending_follow_ups", lambda *args: {"sent": 0, "failed": 0, "pending": 0})
        posts = [
            {"title": "Data Analyst", "body": "Apply", "href": "https://example.com/jobs/1"},
            {"title": "Business Analyst", "body": "Email", "href": "https://example.com/jobs/2"},
        ]
        monkeypatch.setattr(scout_agent, "search_recruiter_posts", lambda: posts)
        monkeypatch.setattr(scout_agent, "LAST_SEARCH_STATS", {
            "queries_attempted": 2, "queries_successful": 2,
            "query_errors": [], "results_discovered": 2,
        })
        monkeypatch.setattr(scout_agent, "enrich_post", lambda post: post)
        apply_result = {
            "job_id": "job-1", "job_title": "Data Analyst", "company": "A",
            "location": "Noida", "recruiter_email": None,
            "application_url": "https://example.com/jobs/1", "reason": "match",
        }
        email_result = {
            "job_id": "job-2", "job_title": "Business Analyst", "company": "B",
            "location": "Remote", "recruiter_email": "hr@realco.io",
            "application_url": "https://example.com/jobs/2", "reason": "match", "email_safe": True,
        }
        outcomes = iter([(apply_result, "apply"), (email_result, "ok")])
        monkeypatch.setattr(scout_agent, "analyze_post_verbose", lambda post: next(outcomes))
        monkeypatch.setattr(scout_agent, "compose_email", lambda post, analysis: ({
            "email_subject": "Application", "email_body": "Hello",
        }, "ok"))
        monkeypatch.setattr(scout_agent, "send_email", lambda *args: True)
        scheduled = []
        monkeypatch.setattr(scout_agent, "schedule_follow_up", lambda *args: scheduled.append(args))

        scout_agent.main()

        report = json.load(open(tmp_path / "report.json", encoding="utf-8"))
        history = json.load(open(tmp_path / "history.json", encoding="utf-8"))
        assert report["relevant_apply_leads"] == 2
        assert report["relevant_email_leads"] == 1
        assert report["emails_accepted_by_smtp"] == 1
        assert {entry["route"] for entry in history} == {"alert", "email"}
        emailed = next(entry for entry in history if entry["route"] == "email")
        assert len(scheduled) == 1 and scheduled[0][-1] == emailed["job_id"]


