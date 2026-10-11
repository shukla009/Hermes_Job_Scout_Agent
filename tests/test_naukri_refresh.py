"""Offline checks for exact restoration and private Naukri refresh state."""

import json
import sys
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

import naukri_refresh as nr

REAL_ACCOUNT_VERIFIED = nr._account_verified
REAL_READ = nr._read
REAL_READ_PERSISTED = nr._read_persisted
REAL_SAVE = nr._save
REAL_TIMESTAMP = nr._timestamp

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class FakeProfile:
    """Model persisted server text, including ambiguous save responses."""

    def __init__(self, summary, email="work.shuklashivank@gmail.com", outcomes=()):
        self.summary = summary
        self.email = email
        self.outcomes = list(outcomes)
        self.saves = []
        self.account_checks = []

    def verify_account(self, email):
        self.account_checks.append(email)
        return self.email == email

    def read_summary(self):
        return self.summary

    def save_summary(self, text):
        self.saves.append(text)
        outcome = self.outcomes.pop(0) if self.outcomes else "ok"
        if outcome == "persist_then_raise":
            self.summary = text
            raise TimeoutError("private browser failure containing summary")
        if outcome == "raise":
            raise TimeoutError("private browser failure containing summary")
        if outcome == "concurrent_edit":
            self.summary = "User replaced their summary."
            raise TimeoutError("save response lost")
        self.summary = text


EMAIL = "work.shuklashivank@gmail.com"
SUMMARY = "I build reliable dashboards.\nSQL, Excel and Power BI experience.\n\nPortfolio: https://shivank-shukla-portfolio.vercel.app/\n"


@pytest.fixture
def journal(tmp_path):
    return nr.EncryptedJournal(tmp_path / "private" / "recovery.enc", Fernet.generate_key().decode())


@pytest.fixture(autouse=True)
def fake_ui(monkeypatch):
    def account(page, expected):
        if not page.verify_account(expected):
            raise nr.RefreshError("account_unverified")
    monkeypatch.setattr(nr, "_account_verified", account)
    monkeypatch.setattr(nr, "_timestamp", lambda page: "Today" if page.saves else "09 Oct 2026")
    monkeypatch.setattr(nr, "_read", lambda page: page.read_summary())
    monkeypatch.setattr(nr, "_read_persisted", lambda page: page.read_summary())
    monkeypatch.setattr(nr, "_save", lambda page, value: page.save_summary(value))


def test_removes_final_prose_period_before_url_preserving_exact_whitespace():
    expected = SUMMARY.replace("experience.", "experience", 1)
    assert nr.remove_prose_full_stop(SUMMARY) == expected


@pytest.mark.parametrize("suffix", [
    "\nPortfolio: https://example.com/a.b?q=x.y#z.",
    "\nLinkedIn: www.linkedin.com/in/person.",
    "\nContact: person.first@example.com",
])
def test_url_email_punctuation_is_preserved(suffix):
    assert nr.remove_prose_full_stop("I analyse data." + suffix) == "I analyse data" + suffix


@pytest.mark.parametrize("summary", ["", "No period", "https://example.com/", "SQL version 3.14", "Dr.", "Wait..."])
def test_unsafe_summary_skips_without_mutation(summary, journal):
    page = FakeProfile(summary)
    result = nr.refresh_profile(page, journal, EMAIL)
    assert result["status"] == "unsafe_summary"
    assert page.saves == []
    assert journal.load() is None


def test_success_requires_two_saves_and_exact_restoration(journal):
    page = FakeProfile(SUMMARY)
    result = nr.refresh_profile(page, journal, EMAIL)
    assert result["status"] == "completed"
    assert result["original_restored"] is True
    assert page.summary == SUMMARY
    assert page.saves == [nr.remove_prose_full_stop(SUMMARY), SUMMARY]
    assert len(page.account_checks) >= 3
    assert journal.load() is None


def test_wrong_account_never_edits_or_writes_recovery(journal):
    page = FakeProfile(SUMMARY, email="other@example.com")
    assert nr.refresh_profile(page, journal, EMAIL)["status"] == "account_unverified"
    assert page.saves == []
    assert journal.load() is None


@pytest.mark.parametrize("outcomes", [["raise"], ["persist_then_raise"]])
def test_failed_first_save_restores_original_even_if_response_lost(journal, outcomes):
    page = FakeProfile(SUMMARY, outcomes=outcomes)
    result = nr.refresh_profile(page, journal, EMAIL)
    assert result["original_restored"] is True
    assert page.summary == SUMMARY
    assert journal.load() is None
    assert "private browser failure" not in json.dumps(result)
    assert SUMMARY not in json.dumps(result)


@pytest.mark.parametrize("outcome", ["raise", "persist_then_raise"])
def test_uncertain_second_save_verifies_and_restores(journal, outcome):
    page = FakeProfile(SUMMARY, outcomes=["ok", outcome])
    result = nr.refresh_profile(page, journal, EMAIL)
    assert result["original_restored"] is True
    assert page.summary == SUMMARY
    assert journal.load() is None


def test_unsaved_editor_original_cannot_mask_persisted_intermediate(journal, monkeypatch):
    page = FakeProfile(SUMMARY)
    page.draft = SUMMARY
    def save(profile, text):
        profile.saves.append(text)
        profile.draft = text
        if len(profile.saves) == 2:
            # Original is visible in the editor, but failed to reach the server.
            raise TimeoutError("private selector error")
        profile.summary = text
    monkeypatch.setattr(nr, "_save", save)
    monkeypatch.setattr(nr, "_read", lambda profile: profile.draft)
    monkeypatch.setattr(nr, "_read_persisted", lambda profile: profile.summary)
    result = nr.refresh_profile(page, journal, EMAIL)
    assert result["original_restored"] is True
    assert page.summary == SUMMARY
    assert page.saves == [nr.remove_prose_full_stop(SUMMARY), SUMMARY, SUMMARY]
    assert journal.load() is None


def test_failed_restore_retains_recoverable_journal(journal):
    page = FakeProfile(SUMMARY, outcomes=["ok", "raise", "raise"])
    result = nr.refresh_profile(page, journal, EMAIL)
    assert result["status"] == "restoration_pending"
    assert result["original_restored"] is False
    assert page.summary == nr.remove_prose_full_stop(SUMMARY)
    assert journal.load()["original"] == SUMMARY


def test_concurrent_edit_is_never_overwritten(journal):
    page = FakeProfile(SUMMARY, outcomes=["concurrent_edit"])
    result = nr.refresh_profile(page, journal, EMAIL)
    assert result["status"] == "concurrent_edit"
    assert page.summary == "User replaced their summary."
    assert len(page.saves) == 1
    assert journal.load()["original"] == SUMMARY


def test_pending_journal_restores_before_attempting_new_refresh(journal):
    intermediate = nr.remove_prose_full_stop(SUMMARY)
    journal.write({"original": SUMMARY, "intermediate": intermediate, "email": EMAIL})
    page = FakeProfile(intermediate)
    result = nr.refresh_profile(page, journal, EMAIL)
    assert result["status"] == "recovered"
    assert page.saves == [SUMMARY]
    assert journal.load() is None


def test_pending_journal_wrong_account_does_not_restore(journal):
    intermediate = nr.remove_prose_full_stop(SUMMARY)
    journal.write({"original": SUMMARY, "intermediate": intermediate, "email": "other@example.com"})
    page = FakeProfile(intermediate)
    result = nr.refresh_profile(page, journal, EMAIL)
    assert result["status"] == "recovery_account_mismatch"
    assert page.saves == []


def test_journal_is_encrypted_and_wrong_key_fails_closed(journal):
    record = {"original": SUMMARY, "intermediate": nr.remove_prose_full_stop(SUMMARY), "email": EMAIL}
    journal.write(record)
    raw = journal.path.read_bytes()
    assert SUMMARY.encode() not in raw
    assert EMAIL.encode() not in raw
    assert journal.load() == record
    other = nr.EncryptedJournal(journal.path, Fernet.generate_key().decode())
    with pytest.raises(nr.RefreshError, match="^recovery_unreadable$"):
        other.load()
    assert journal.path.read_bytes() == raw


def test_prepare_has_no_profile_mutation_and_execute_has_two_saves(journal, monkeypatch):
    monkeypatch.setenv("GITHUB_RUN_ID", "123")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    page = FakeProfile(SUMMARY)
    assert nr.prepare_profile(page, journal, EMAIL)["status"] == "prepared"
    assert page.saves == []
    assert journal.load()["operation_id"]
    assert nr.refresh_profile(page, journal, EMAIL, require_prepared=True)["status"] == "completed"
    assert page.saves == [nr.remove_prose_full_stop(SUMMARY), SUMMARY]


@pytest.mark.parametrize("run_id,attempt", [("124", "1"), ("123", "2")])
def test_execute_refuses_journal_from_another_run_or_attempt(journal, monkeypatch, run_id, attempt):
    monkeypatch.setenv("GITHUB_RUN_ID", "123")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    page = FakeProfile(SUMMARY)
    nr.prepare_profile(page, journal, EMAIL)
    monkeypatch.setenv("GITHUB_RUN_ID", run_id)
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", attempt)
    result = nr.refresh_profile(page, journal, EMAIL, require_prepared=True)
    assert result["status"] == "preparation_missing"
    assert page.saves == []


def test_execute_without_preparation_never_mutates(journal):
    page = FakeProfile(SUMMARY)
    assert nr.refresh_profile(page, journal, EMAIL, require_prepared=True)["status"] == "preparation_missing"
    assert page.saves == []


def test_login_challenge_never_reaches_summary(monkeypatch, journal):
    def challenge(page, email):
        raise nr.RefreshError("needs_login")
    monkeypatch.setattr(nr, "_account_verified", challenge)
    page = FakeProfile(SUMMARY)
    assert nr.refresh_profile(page, journal, EMAIL)["status"] == "needs_login"
    assert page.saves == []
    assert journal.load() is None


@pytest.mark.parametrize("body", ["Sign in", "work.shuklashivank@gmail.com Verify you are human", "work.shuklashivank@gmail.com CAPTCHA"])
def test_real_account_checker_rejects_login_and_challenge(body):
    class Body:
        def inner_text(self, **kwargs):
            return body
    class Page:
        url = nr.PROFILE_URL
        def get_by_text(self, *args, **kwargs):
            class Match:
                @property
                def first(self):
                    return self
                def wait_for(self, **kwargs):
                    if EMAIL not in body:
                        raise TimeoutError()
            return Match()
        def locator(self, selector):
            assert selector == "body"
            return Body()
    with pytest.raises(nr.RefreshError):
        REAL_ACCOUNT_VERIFIED(Page(), EMAIL)


@pytest.mark.parametrize("body,status", [("Access Denied", "access_denied"), ("Please sign in", "needs_login"), ("Loading", "account_unverified")])
def test_cloud_login_errors_are_classified_without_exposing_content(body, status):
    from unittest.mock import MagicMock
    page = MagicMock(url=nr.PROFILE_URL)
    page.get_by_text.return_value.first.wait_for.side_effect = TimeoutError()
    page.locator.return_value.inner_text.return_value = body
    with pytest.raises(nr.RefreshError, match="^" + status + "$"):
        REAL_ACCOUNT_VERIFIED(page, EMAIL)


def test_crash_journal_uses_last_verified_original_when_recovering(journal):
    intermediate = nr.remove_prose_full_stop(SUMMARY)
    journal.write({"original": SUMMARY, "intermediate": intermediate, "email": EMAIL})
    page = FakeProfile(intermediate, outcomes=["persist_then_raise"])
    result = nr.prepare_profile(page, journal, EMAIL)
    assert result["status"] == "prepared"
    assert result["recovered"] is True
    assert page.summary == SUMMARY
    assert page.saves == [SUMMARY]


@pytest.mark.parametrize("lazy_section", [False, True])
def test_real_browser_helpers_restore_server_persisted_summary_offline(monkeypatch, journal, lazy_section):
    playwright_api = pytest.importorskip("playwright.sync_api")
    for name, implementation in [("_account_verified", REAL_ACCOUNT_VERIFIED), ("_read", REAL_READ), ("_read_persisted", REAL_READ_PERSISTED), ("_save", REAL_SAVE), ("_timestamp", REAL_TIMESTAMP)]:
        monkeypatch.setattr(nr, name, implementation)
    html = """<!doctype html><html><body>
    <p>work.shuklashivank@gmail.com</p><p>Profile last updated Today</p>
    <button aria-label="Profile summary" onclick="document.querySelector('#edit').hidden=false">Go to summary</button>
    <button id="edit" aria-label="Edit profile summary" LAZY onclick="document.querySelector('textarea').hidden=false">Edit</button>
    <textarea aria-label="Profile summary" hidden></textarea>
    <button onclick="localStorage.setItem('summary', document.querySelector('textarea').value); document.querySelector('#success').hidden=false">Save</button>
    <p id="success" hidden>Profile updated successfully</p>
    <script>document.querySelector('textarea').value=localStorage.getItem('summary') || INITIAL;</script>
    </body></html>""".replace("INITIAL", json.dumps(SUMMARY)).replace("LAZY", "hidden" if lazy_section else "")
    with playwright_api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except playwright_api.Error as exc:
            if "Executable doesn't exist" in str(exc):
                pytest.skip("Chromium not installed; run python -m playwright install chromium")
            raise
        context = browser.new_context()
        context.route("**/*", lambda route: route.fulfill(status=200, content_type="text/html", body=html))
        page = context.new_page()
        page.goto(nr.PROFILE_URL)
        result = nr.refresh_profile(page, journal, EMAIL)
        assert result["status"] == "completed"
        assert result["original_restored"] is True
        assert nr._read_persisted(page) == SUMMARY
        assert journal.load() is None
        browser.close()


def valid_session():
    return {"cookies": [{"name": "auth", "value": "PRIVATE_TOKEN", "domain": ".naukri.com", "path": "/", "secure": True, "httpOnly": True, "sameSite": "Lax", "expires": -1}], "origins": [{"origin": "https://www.naukri.com", "localStorage": []}]}


def test_valid_naukri_session_is_accepted():
    session = valid_session()
    assert nr.validate_storage_state(json.dumps(session)) == session


@pytest.mark.parametrize("domain", ["google.com", ".accounts.google.com", "naukri.com.evil.test", "evilnaukri.com"])
def test_foreign_cookies_rejected(domain):
    session = valid_session()
    session["cookies"][0]["domain"] = domain
    with pytest.raises(nr.RefreshError, match="^invalid_session$"):
        nr.validate_storage_state(session)


@pytest.mark.parametrize("origin", ["https://accounts.google.com", "http://naukri.com", "https://naukri.com.evil.test", "https://user:pass@naukri.com", "https://naukri.com:1234"])
def test_foreign_or_insecure_origins_rejected(origin):
    session = valid_session()
    session["origins"][0]["origin"] = origin
    with pytest.raises(nr.RefreshError, match="^invalid_session$"):
        nr.validate_storage_state(session)


@pytest.mark.parametrize("raw", ["PRIVATE_TOKEN", "{}", "[]", '{"cookies": [], "origins": []}', {"cookies": [{}]}, {"cookies": "bad"}])
def test_malformed_session_is_redacted(raw):
    with pytest.raises(nr.RefreshError, match="^invalid_session$"):
        nr.validate_storage_state(raw)


def test_missing_session_exits_before_browser_and_redacts_report(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sys, "argv", ["naukri_refresh.py"])
    monkeypatch.setenv("NAUKRI_REFRESH_ENABLED", "true")
    monkeypatch.delenv("NAUKRI_STORAGE_STATE", raising=False)
    monkeypatch.setenv("NAUKRI_REPORT_PATH", str(tmp_path / "report.json"))
    monkeypatch.setattr(nr, "_notify", lambda result: None)
    assert nr.main() == 1
    report = (tmp_path / "report.json").read_text()
    assert "PRIVATE_TOKEN" not in report + capsys.readouterr().out
    assert json.loads(report)["original_restored"] is False
