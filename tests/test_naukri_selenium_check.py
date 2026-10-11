"""Network-free Selenium checks for safe cloud login diagnostics."""

import json

import pytest

import naukri_selenium_check as ns


EMAIL = "work.shuklashivank@gmail.com"
PASSWORD = "private-password-value"
SUMMARY = "Private summary should never appear in reports."
STATE = {"cookies": [{"name": "session", "value": "private-session-value",
                       "domain": ".naukri.com", "path": "/"}], "origins": []}


class Element:
    def __init__(self, text="", value=None, click=None, displayed=True):
        self.text = text
        self.value = value
        self.on_click = click
        self.displayed = displayed
        self.keys = []
        self.clicks = 0

    def is_displayed(self):
        return self.displayed

    def send_keys(self, value):
        self.keys.append(value)

    def click(self):
        self.clicks += 1
        if self.on_click:
            self.on_click()

    def get_attribute(self, name):
        return self.value if name == "value" else None


class Driver:
    """Emulate the native login and editor; record every mutation attempt."""
    def __init__(self, login_text="Login", account=EMAIL, login_redirect=None):
        self.current_url = ns.LOGIN_URL
        self.login_text = login_text
        self.account = account
        self.login_redirect = login_redirect
        self.destinations = []
        self.cookies = []
        self.scripts = []
        self.username = Element()
        self.password = Element()
        self.submit = Element("Login", click=self.finish_login)
        self.summary = Element(value=SUMMARY)
        self.save = Element("Save")
        self.editor_visible = True
        self.edit = Element("Edit profile summary", click=self.open_editor)
        self.quit_called = False

    def finish_login(self):
        self.current_url = ns.PROFILE_URL

    def open_editor(self):
        self.editor_visible = True

    def set_page_load_timeout(self, seconds):
        self.timeout = seconds

    def get(self, url):
        self.destinations.append(url)
        self.current_url = self.login_redirect if url == ns.LOGIN_URL and self.login_redirect else url

    def find_elements(self, by, selector):
        if selector == "body":
            return [Element(self.login_text if "/nlogin" in self.current_url else self.account)]
        if selector == "#usernameField":
            return [self.username]
        if selector == "#passwordField":
            return [self.password]
        if selector == "button[type='submit'], button":
            return [self.submit, self.save]
        if selector == "textarea#profileSummaryTxt, textarea[aria-label='Profile summary']":
            return [self.summary] if self.editor_visible else []
        if selector == "button[aria-label='Edit profile summary'], [role='button'][aria-label='Edit profile summary']":
            return [self.edit]
        return []

    def add_cookie(self, cookie):
        self.cookies.append(cookie)

    def execute_script(self, script, *args):
        self.scripts.append((script, args))

    def quit(self):
        self.quit_called = True


@pytest.fixture(autouse=True)
def immediate_wait(monkeypatch):
    monkeypatch.setattr(ns, "WAIT_SECONDS", 0)


@pytest.mark.parametrize("body,expected", [
    ("Access Denied", "access_denied"),
    ("Error 403", "access_denied"),
    ("Please verify you are human", "challenge"),
    ("CAPTCHA", "challenge"),
    ("Your session has expired", "needs_login"),
])
def test_recognizes_known_blocking_states(body, expected):
    assert ns.detect_page_status(body, ns.PROFILE_URL) == expected


@pytest.mark.parametrize("text", ["Access Denied", "CAPTCHA", "security challenge"])
def test_initial_block_prevents_credential_entry(text):
    driver = Driver(login_text=text)
    report = ns.check_session(driver, EMAIL, username=EMAIL, password=PASSWORD)
    assert report["status"] in {"access_denied", "challenge"}
    assert not driver.username.keys and not driver.password.keys
    assert driver.submit.clicks == 0
    assert driver.destinations == [ns.LOGIN_URL]
    assert PASSWORD not in json.dumps(report)


def test_foreign_login_redirect_never_receives_credentials():
    driver = Driver(login_redirect="https://example.com/nlogin/login")
    report = ns.check_session(driver, EMAIL, username=EMAIL, password=PASSWORD)
    assert report["status"] == "account_unverified"
    assert not driver.username.keys and not driver.password.keys


def test_foreign_session_rejected_before_browser_navigation():
    state = {"cookies": [{"name": "session", "value": "private", "domain": ".google.com"}]}
    driver = Driver()
    report = ns.check_session(driver, EMAIL, state=state)
    assert report["status"] == "invalid_session"
    assert driver.destinations == [] and driver.cookies == []


def test_wrong_login_account_rejected_before_browser_navigation():
    driver = Driver()
    report = ns.check_session(driver, EMAIL, username="other@example.com", password=PASSWORD)
    assert report["status"] == "account_unverified"
    assert not driver.destinations


def test_native_login_verifies_summary_without_saving_or_reporting_private_data():
    driver = Driver()
    driver.editor_visible = False
    report = ns.check_session(driver, EMAIL, username=EMAIL, password=PASSWORD)
    assert report["status"] == "session_valid"
    assert report["read_only"] is True
    assert report["auth_method"] == "password"
    assert driver.username.keys == [EMAIL] and driver.password.keys == [PASSWORD]
    assert driver.submit.clicks == 1 and driver.edit.clicks == 1
    assert driver.save.clicks == 0 and driver.summary.keys == []
    assert all(private not in json.dumps(report) for private in (EMAIL, PASSWORD, SUMMARY))


def test_saved_session_reads_verified_profile_without_any_form_submission():
    driver = Driver()
    report = ns.check_session(driver, EMAIL, state=STATE)
    assert report["status"] == "session_valid"
    assert report["auth_method"] == "saved_session"
    assert driver.cookies[0]["value"] == STATE["cookies"][0]["value"]
    assert driver.submit.clicks == driver.save.clicks == 0
    assert not driver.username.keys and not driver.password.keys
    assert STATE["cookies"][0]["value"] not in json.dumps(report)


@pytest.mark.parametrize("account", ["different@example.com", "prefix" + EMAIL, EMAIL + " suffix"])
def test_wrong_or_partial_account_is_not_accepted(account):
    driver = Driver(account=account)
    report = ns.check_session(driver, EMAIL, state=STATE)
    assert report["status"] == "account_unverified"
    assert driver.edit.clicks == driver.save.clicks == 0


def test_challenge_on_authenticated_profile_stops_before_reading_summary():
    driver = Driver(account="CAPTCHA\n" + EMAIL)
    report = ns.check_session(driver, EMAIL, state=STATE)
    assert report["status"] == "challenge"
    assert driver.edit.clicks == driver.save.clicks == 0


def test_unexpected_browser_exception_is_redacted(monkeypatch):
    driver = Driver()
    def private_failure(current):
        raise RuntimeError(PASSWORD + SUMMARY)
    monkeypatch.setattr(ns, "_read_summary", private_failure)
    report = ns.check_session(driver, EMAIL, state=STATE)
    assert report["status"] == "ui_error"
    assert PASSWORD not in json.dumps(report) and SUMMARY not in json.dumps(report)


def test_options_use_headed_standard_chrome(monkeypatch):
    monkeypatch.delenv("NAUKRI_CHROME_BINARY", raising=False)
    arguments = ns.build_chrome_options().arguments
    assert not any("headless" in arg or "AutomationControlled" in arg or "disable-web-security" in arg for arg in arguments)


def test_main_cleans_up_and_reports_challenge_without_leaking_secrets(tmp_path, monkeypatch, capsys):
    from selenium import webdriver
    driver = Driver(login_text="CAPTCHA")
    monkeypatch.setattr(webdriver, "Chrome", lambda **kwargs: driver)
    monkeypatch.setenv("NAUKRI_USERNAME", EMAIL)
    monkeypatch.setenv("NAUKRI_PASSWORD", PASSWORD)
    destination = tmp_path / "report.json"
    monkeypatch.setenv("NAUKRI_REPORT_PATH", str(destination))
    notices = []
    monkeypatch.setattr(ns, "_notify", lambda report: notices.append(report) or True)
    assert ns.main([]) == 1
    report = json.loads(destination.read_text())
    assert report["status"] == "challenge"
    assert notices[0]["status"] == "needs_login"
    assert report["telegram_notified"] is True and driver.quit_called
    output = capsys.readouterr().out
    assert PASSWORD not in output and EMAIL not in output
