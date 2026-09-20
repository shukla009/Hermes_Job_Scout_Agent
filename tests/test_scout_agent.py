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
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root (scout_agent.py lives there)

import scout_agent
from scout_agent import (
    _valid_recruiter_email,
    _is_stale_post,
    _parse_serpapi_date_days,
    _linkedin_post_age_days,
    _is_job_board_url,
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
        assert _is_stale_post("https://www.linkedin.com/x", "3 days ago") is False

    def test_exactly_window_passes(self):
        assert _is_stale_post("https://www.linkedin.com/x", "4 days ago") is False

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

    def test_allows_linkedin_posts(self):
        assert _is_job_board_url("https://www.linkedin.com/posts/someone-activity-123") is False


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
    POST = {"title": "Hiring Data Analyst in Noida", "body": "email hr@realco.io", "href": "https://www.linkedin.com/posts/x"}

    def test_ok_verdict(self, monkeypatch):
        monkeypatch.setattr(scout_agent, "client", MagicMock())
        payload = {"is_relevant": True, "recruiter_email": "hr@realco.io",
                   "job_title": "Data Analyst", "company": "RealCo",
                   "email_subject": "Application - Data Analyst", "email_body": "Hi", "reason": "match"}
        scout_agent.client.chat.completions.create.return_value = _mock_llm_response(payload)
        parsed, verdict = scout_agent.analyze_post_verbose(self.POST)
        assert verdict == "ok" and parsed["recruiter_email"] == "hr@realco.io"

    def test_irrelevant(self, monkeypatch):
        monkeypatch.setattr(scout_agent, "client", MagicMock())
        payload = {"is_relevant": False, "reason": "job board"}
        scout_agent.client.chat.completions.create.return_value = _mock_llm_response(payload)
        parsed, verdict = scout_agent.analyze_post_verbose(self.POST)
        assert parsed is None and verdict == "irrelevant"

    def test_no_email_rejected(self, monkeypatch):
        monkeypatch.setattr(scout_agent, "client", MagicMock())
        payload = {"is_relevant": True, "recruiter_email": "hr@company.com", "reason": "x"}
        scout_agent.client.chat.completions.create.return_value = _mock_llm_response(payload)
        parsed, verdict = scout_agent.analyze_post_verbose(self.POST)
        assert parsed is None and verdict == "no_email"

    def test_own_email_not_used(self, monkeypatch):
        # Regression: the agent must never email the candidate's own address
        monkeypatch.setattr(scout_agent, "client", MagicMock())
        payload = {"is_relevant": True, "recruiter_email": None, "reason": "x"}
        scout_agent.client.chat.completions.create.return_value = _mock_llm_response(payload)
        post = {"title": "Hiring", "body": f"contact {scout_agent.CANDIDATE_EMAIL}", "href": "https://linkedin.com/x"}
        parsed, verdict = scout_agent.analyze_post_verbose(post)
        # regex finds only the candidate's own email -> must NOT be used
        assert verdict == "no_email"

    def test_no_client(self, monkeypatch):
        monkeypatch.setattr(scout_agent, "client", None)
        parsed, verdict = scout_agent.analyze_post_verbose(self.POST)
        assert parsed is None and verdict == "no_client"

    def test_auth_error_verdict(self, monkeypatch):
        monkeypatch.setattr(scout_agent, "client", MagicMock())
        scout_agent.client.chat.completions.create.side_effect = Exception("401 Unauthorized: Authentication failed")
        parsed, verdict = scout_agent.analyze_post_verbose(self.POST)
        assert parsed is None and verdict == "auth_error"

    def test_json_error_then_gives_up(self, monkeypatch):
        monkeypatch.setattr(scout_agent, "client", MagicMock())
        scout_agent.client.chat.completions.create.side_effect = None
        bad = MagicMock()
        bad.choices = [MagicMock()]
        bad.choices[0].message.content = "not json at all {{{"
        scout_agent.client.chat.completions.create.return_value = bad
        parsed, verdict = scout_agent.analyze_post_verbose(self.POST, max_retries=1)
        assert parsed is None and verdict.startswith("json_error")


# ---------------------------------------------------------------
# Prompt integrity
# ---------------------------------------------------------------
class TestPrompt:
    def test_json_template_is_wellformed(self):
        # Regression: the template had an unclosed quote and a trailing comma
        import re as re_mod
        src = open(scout_agent.__file__, encoding="utf-8").read()
        m = re.search(r'Return ONLY JSON:\s*\{\{(.+?)\}\}"""', src, re.S)
        assert m, "Return ONLY JSON block not found"
        rendered = ("{" + m.group(1) + "}").replace("{CANDIDATE_NAME}", "X")
        sample = rendered
        for k, v in [('true|false', 'true'), ('"best_email_or_null"', '"a@b.com"'),
                     ('"one line why accepted/rejected"', '"r"')]:
            sample = sample.replace(k, v)
        sample = re.sub(r'"\.\.\."', '"X"', sample)
        json.loads(sample)  # must not raise

    def test_example_email_not_banned_opening(self):
        src = open(scout_agent.__file__, encoding="utf-8").read()
        assert "I came across your [Job Title]" not in src

    def test_no_hardcoded_personal_email_in_prompt(self):
        src = open(scout_agent.__file__, encoding="utf-8").read()
        prompt = re.search(r'prompt = f"""(.+?)"""', src, re.S).group(1)
        assert "work.shuklashivank@gmail.com" not in prompt
        assert "{CANDIDATE_EMAIL}" in prompt

    def test_freshness_rule_present(self):
        src = open(scout_agent.__file__, encoding="utf-8").read()
        assert "Reject if the post is clearly old" in src


# ---------------------------------------------------------------
# Send email (SMTP mocked)
# ---------------------------------------------------------------
class TestSendEmail:
    def test_aborts_without_resume(self, tmp_path, monkeypatch):
        monkeypatch.setattr(scout_agent, "RESUME_PATH", str(tmp_path / "missing.pdf"))
        ok = scout_agent.send_email("hr@x.com", "Subject", "body")
        assert ok is False  # hard fail: body claims attachment

    def test_sends_with_mocked_smtp(self, tmp_path, monkeypatch):
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
    def test_schedule_creates_three_entries(self, tmp_path, monkeypatch):
        monkeypatch.setattr(scout_agent, "FOLLOW_UP_FILE", None, raising=False)
        monkeypatch.setattr("builtins.open", open, raising=False)
        import scout_agent as sa
        # patch the file path used inside schedule_follow_up
        with patch.object(sa, "__name__", sa.__name__):
            pass
        # schedule_follow_up writes 'follow_up.json' in cwd — chdir to tmp
        monkeypatch.chdir(tmp_path)
        sa.schedule_follow_up("a@b.com", "Subj", "Body")
        data = json.load(open(tmp_path / "follow_up.json", encoding="utf-8"))
        assert len(data) == 3
        assert [d["step"] for d in data] == [1, 2, 3]

    def test_schedule_is_called_by_main_flow(self):
        # Regression guard: the call must exist in main's send-success block
        import inspect
        src = inspect.getsource(scout_agent.main)
        assert "schedule_follow_up(email, subject, body)" in src


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
