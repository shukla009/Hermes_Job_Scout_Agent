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
    def __init__(self, text="", value=None, click=None, displayed=True, enabled=True):
        self.text = text
        self.value = value
        self.on_click = click
        self.displayed = displayed
        self.enabled = enabled
        self.keys = []
        self.clicks = 0
        self.clears = 0
        self.descendants = {}
        self.on_enter = None

    def is_displayed(self):
        return self.displayed

    def send_keys(self, value):
        self.keys.append(value)
        if value == "\ue007":
            if self.on_enter:
                self.on_enter()
            elif self.on_click:
                self.on_click()
            return
        self.value = (self.value or "") + value

    def clear(self):
        self.clears += 1
        self.value = ""

    def is_enabled(self):
        return self.enabled

    def find_elements(self, by, selector):
        return self.descendants.get(selector, [])

    def click(self):
        self.clicks += 1
        if self.on_click:
            self.on_click()

    def get_attribute(self, name):
        return self.value if name == "value" else None


class Driver:
    """Emulate the native login and editor; record every mutation attempt."""
    def __init__(self, login_text="Login", account=EMAIL, login_redirect=None, otp_visible=False):
        self.current_url = ns.LOGIN_URL
        self.login_text = login_text
        self.account = account
        self.login_redirect = login_redirect
        self.otp_visible = otp_visible
        self.destinations = []
        self.cookies = []
        self.scripts = []
        self.username = Element()
        self.password = Element()
        self.submit = Element("Login", click=self.finish_login)
        self.password.on_enter = lambda: self.submit.on_click() if self.submit.on_click else None
        self.header_login = Element("Login")
        self.summary = Element(value=SUMMARY)
        self.save = Element("Save")
        self.editor_visible = True
        self.edit = Element("Edit profile summary", click=self.open_editor)
        self.quit_called = False
        self.performance_logs = []
        self.profile_redirect_login = False

    def finish_login(self):
        self.current_url = ns.PROFILE_URL

    def open_editor(self):
        self.editor_visible = True

    def set_page_load_timeout(self, seconds):
        self.timeout = seconds

    def get(self, url):
        self.destinations.append(url)
        self.current_url = self.login_redirect if url == ns.LOGIN_URL and self.login_redirect else url
        if url == ns.PROFILE_URL and self.profile_redirect_login:
            self.current_url = ns.LOGIN_URL

    def find_elements(self, by, selector):
        if selector == "body":
            return [Element(self.login_text if "/nlogin" in self.current_url else self.account)]
        if selector == "#usernameField":
            return [self.username]
        if selector == "#passwordField":
            return [self.password]
        if "one-time-code" in selector:
            return [Element(displayed=True)] if self.otp_visible else []
        if selector == "button[type='submit'], button":
            return [self.header_login, self.submit, self.save]
        if selector == "button[type='submit']":
            return [self.submit]
        if selector == "textarea#profileSummaryTxt, textarea[aria-label='Profile summary']":
            return [self.summary] if self.editor_visible else []
        if selector == "button[aria-label='Edit profile summary'], [role='button'][aria-label='Edit profile summary']":
            return [self.edit]
        return []

    def add_cookie(self, cookie):
        self.cookies.append(cookie)

    def execute_script(self, script, *args):
        self.scripts.append((script, args))
        return False

    def get_log(self, name):
        return self.performance_logs.pop(0) if self.performance_logs else []

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
    assert driver.submit.keys == ["\ue007"]
    assert driver.submit.clicks == 0 and driver.edit.clicks == 1
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


def test_arbitrary_refresh_error_is_not_exposed(monkeypatch):
    driver = Driver()
    def private_failure(current):
        raise ns.RefreshError(PASSWORD + SUMMARY)
    monkeypatch.setattr(ns, "_read_summary", private_failure)
    report = ns.check_session(driver, EMAIL, state=STATE)
    assert report["status"] == "ui_error"
    assert PASSWORD not in json.dumps(report) and SUMMARY not in json.dumps(report)


@pytest.mark.parametrize("text", [
    "Invalid credentials", "Incorrect password", "Enter the OTP", "Enter one-time password",
])
def test_authentication_failure_diagnostics_contain_only_safe_values(text):
    driver = Driver(login_text=text)
    report = ns.check_session(driver, EMAIL, username=EMAIL, password=PASSWORD)
    assert report["status"] in {"invalid_credentials", "otp_required"}
    assert all(private not in json.dumps(report) for private in (EMAIL, PASSWORD, SUMMARY, text))
    diagnostics = report["diagnostics"]
    assert all(isinstance(diagnostics[key], bool) for key in (
        "invalid_credentials", "otp_required", "username_field_visible", "password_field_visible"))
    assert diagnostics["url_kind"] == "login"


def test_optional_login_using_otp_does_not_mark_otp_as_required():
    driver = Driver(login_text="Login\nLogin using OTP")
    diagnostics = ns.auth_diagnostics(driver)
    assert diagnostics["otp_required"] is False
    report = ns.check_session(driver, EMAIL, username=EMAIL, password=PASSWORD)
    assert report["status"] == "session_valid"
    assert driver.submit.clicks == 0
    assert driver.password.keys == [PASSWORD] and driver.submit.keys == ["\ue007"]


def test_visible_otp_entry_requires_human_login_before_password_submission():
    driver = Driver(login_text="Login using OTP", otp_visible=True)
    report = ns.check_session(driver, EMAIL, username=EMAIL, password=PASSWORD)
    assert report["status"] == "otp_required"
    assert report["diagnostics"]["otp_required"] is True
    assert not driver.username.keys and not driver.password.keys
    assert driver.submit.clicks == 0


@pytest.mark.parametrize("post_submit_text,expected", [
    ("Invalid credentials", "invalid_credentials"),
    ("Enter the OTP", "otp_required"),
    ("Login\nLogin using OTP", "needs_login"),
])
def test_failed_submission_records_phase_and_never_retries(post_submit_text, expected):
    driver = Driver()
    driver.profile_redirect_login = True
    def remain_on_login():
        driver.login_text = post_submit_text
    driver.submit.on_click = remain_on_login
    report = ns.check_session(driver, EMAIL, username=EMAIL, password=PASSWORD)
    assert report["status"] == expected
    assert report["phase"] == ("profile_navigation" if expected == "needs_login" else "credentials_submitted")
    assert driver.submit.clicks == 0
    assert driver.password.keys == [PASSWORD] and driver.submit.keys == ["\ue007"]
    assert driver.destinations == ([ns.LOGIN_URL, ns.PROFILE_URL] if expected == "needs_login" else [ns.LOGIN_URL])
    assert PASSWORD not in json.dumps(report)


def test_header_login_is_ignored_and_form_fields_are_cleared():
    driver = Driver()
    driver.username.value = "prefilled@example.com"
    driver.password.value = "prefilled-private-password"
    report = ns.check_session(driver, EMAIL, username=EMAIL, password=PASSWORD)
    assert report["status"] == "session_valid"
    assert driver.header_login.clicks == 0 and driver.submit.clicks == 0
    assert driver.password.keys == [PASSWORD] and driver.submit.keys == ["\ue007"]
    assert driver.username.clears == driver.password.clears == 1
    assert driver.username.value == EMAIL and driver.password.value == PASSWORD


def test_no_submit_button_does_not_click_generic_header_login(monkeypatch):
    driver = Driver()
    original = driver.find_elements
    monkeypatch.setattr(driver, "find_elements", lambda by, selector: [] if selector == "button[type='submit']" else original(by, selector))
    report = ns.check_session(driver, EMAIL, username=EMAIL, password=PASSWORD)
    assert report["status"] != "session_valid"
    assert driver.header_login.clicks == driver.submit.clicks == 0


def test_ambiguous_submit_buttons_stop_without_clicking(monkeypatch):
    driver = Driver()
    original = driver.find_elements
    other = Element("Login")
    monkeypatch.setattr(driver, "find_elements", lambda by, selector: [driver.submit, other] if selector == "button[type='submit']" else original(by, selector))
    report = ns.check_session(driver, EMAIL, username=EMAIL, password=PASSWORD)
    assert report["status"] != "session_valid"
    assert driver.submit.clicks == other.clicks == 0


def test_disabled_submit_is_not_clicked():
    driver = Driver()
    driver.submit.enabled = False
    report = ns.check_session(driver, EMAIL, username=EMAIL, password=PASSWORD)
    assert report["status"] != "session_valid"
    assert driver.submit.clicks == driver.header_login.clicks == 0


def test_submit_is_scoped_to_password_field_form(monkeypatch):
    driver = Driver()
    form = Element()
    form.descendants["button[type='submit']"] = [driver.submit]
    driver.password.descendants["./ancestor::form[1]"] = [form]
    other = Element("Login")
    original = driver.find_elements
    monkeypatch.setattr(driver, "find_elements", lambda by, selector: [other, driver.submit] if selector == "button[type='submit']" else original(by, selector))
    report = ns.check_session(driver, EMAIL, username=EMAIL, password=PASSWORD)
    assert report["status"] == "session_valid"
    assert driver.submit.clicks == 0 and other.clicks == 0
    assert driver.password.keys == [PASSWORD] and driver.submit.keys == ["\ue007"]


def test_password_readback_mismatch_stops_before_submission(monkeypatch):
    driver = Driver()
    monkeypatch.setattr(driver.password, "get_attribute", lambda name: "" if name == "value" else None)
    report = ns.check_session(driver, EMAIL, username=EMAIL, password=PASSWORD)
    assert report["status"] == "form_input_failed"
    assert driver.submit.clicks == 0
    assert report["diagnostics"]["password_input_match"] is False
    assert PASSWORD not in json.dumps(report)


def network_response(status, url="https://www.naukri.com/private-login?token=secret", resource="XHR"):
    return {"message": json.dumps({"message": {"method": "Network.responseReceived", "params": {
        "type": resource, "response": {"status": status, "url": url,
                                       "headers": {"Authorization": PASSWORD}, "body": SUMMARY}}}})}


@pytest.mark.parametrize("status,expected", [(403, "auth_network_denied"), (429, "rate_limited")])
def test_failed_login_reports_only_sanitized_http_status(status, expected):
    driver = Driver()
    driver.profile_redirect_login = True
    def blocked_submit():
        driver.performance_logs = [[network_response(status)]]
    driver.submit.on_click = blocked_submit
    report = ns.check_session(driver, EMAIL, username=EMAIL, password=PASSWORD)
    assert report["status"] == expected
    assert report["diagnostics"]["auth_http_statuses"] == [status]
    assert driver.submit.clicks == 0 and driver.password.keys == [PASSWORD] and driver.submit.keys == ["\ue007"]
    assert all(private not in json.dumps(report) for private in (PASSWORD, SUMMARY, "private-login", "secret", "Authorization"))


def test_http_diagnostics_ignore_foreign_non_xhr_and_malformed_entries():
    driver = Driver()
    driver.performance_logs = [[network_response(403, "https://example.com/login"),
                                network_response(429, resource="Document"),
                                network_response(200), network_response(200),
                                {"message": "invalid-json"}]]
    assert ns.safe_network_statuses(driver) == [200]


def test_unrelated_naukri_api_error_is_not_an_authentication_failure():
    driver = Driver()
    driver.performance_logs = [[network_response(403, "https://www.naukri.com/recommendations"), network_response(200)]]
    assert ns.safe_network_statuses(driver) == [200]


def test_pre_submit_http_errors_do_not_label_login_as_blocked():
    driver = Driver()
    driver.profile_redirect_login = True
    driver.performance_logs = [[network_response(403)], []]
    driver.submit.on_click = lambda: None
    report = ns.check_session(driver, EMAIL, username=EMAIL, password=PASSWORD)
    assert report["status"] == "needs_login"
    assert report["diagnostics"]["auth_http_statuses"] == []


def test_login_url_stuck_but_verified_profile_can_still_pass():
    driver = Driver()
    driver.submit.on_click = lambda: None
    report = ns.check_session(driver, EMAIL, username=EMAIL, password=PASSWORD)
    assert report["status"] == "session_valid"
    assert driver.destinations == [ns.LOGIN_URL, ns.PROFILE_URL]
    assert driver.submit.keys == ["\ue007"] and driver.submit.clicks == 0
    assert driver.summary.keys == [] and driver.save.clicks == 0


@pytest.mark.parametrize("text,expected", [
    ("Incorrect password", "invalid_credentials"),
    ("Enter the OTP", "otp_required"),
    ("CAPTCHA", "challenge"),
])
def test_explicit_post_submit_errors_prevent_optimistic_profile_visit(text, expected):
    driver = Driver()
    driver.submit.on_click = lambda: setattr(driver, "login_text", text)
    report = ns.check_session(driver, EMAIL, username=EMAIL, password=PASSWORD)
    assert report["status"] == expected
    assert driver.destinations == [ns.LOGIN_URL]
    assert driver.submit.keys == ["\ue007"]


def test_unrelated_naukri_403_never_becomes_authentication_denial():
    driver = Driver()
    driver.profile_redirect_login = True
    driver.submit.on_click = lambda: setattr(driver, "performance_logs", [[
        network_response(403, "https://www.naukri.com/analytics/profile-view?private=secret")]])
    report = ns.check_session(driver, EMAIL, username=EMAIL, password=PASSWORD)
    assert report["status"] == "needs_login"
    assert report["diagnostics"]["auth_http_statuses"] == []
    assert report["diagnostics"]["naukri_http_statuses"] == [403]
    assert "analytics" not in json.dumps(report) and "secret" not in json.dumps(report)


def test_network_capture_counts_and_allowlisted_errors_without_raw_data():
    def event(method, parameters):
        return {"message": json.dumps({"message": {"method": method, "params": parameters}})}
    driver = Driver()
    driver.performance_logs = [[
        event("Network.requestWillBeSent", {"requestId": "private-id", "type": "Fetch",
              "request": {"url": "https://www.naukri.com/auth/login?token=secret",
                          "headers": {"Authorization": PASSWORD}, "postData": SUMMARY}}),
        event("Network.loadingFailed", {"requestId": "private-id",
              "errorText": "net::ERR_CONNECTION_RESET private details " + PASSWORD}),
        event("Network.loadingFailed", {"requestId": "private-id", "errorText": "ERR_SECRET_VALUE"}),
        event("Network.loadingFailed", {"requestId": "foreign-id", "errorText": "ERR_FAILED"}),
    ]]
    diagnostics = ns.safe_network_diagnostics(driver)
    assert diagnostics["log_capture_available"] is True
    assert diagnostics["resource_request_counts"] == {"Fetch": 1}
    assert diagnostics["auth_request_count"] == 1
    assert diagnostics["network_errors"] == ["ERR_CONNECTION_RESET"]
    assert all(private not in json.dumps(diagnostics) for private in (
        "private-id", "token", "secret", PASSWORD, SUMMARY, "Authorization", "ERR_SECRET_VALUE"))


def test_unavailable_performance_log_has_safe_empty_diagnostics(monkeypatch):
    driver = Driver()
    def unavailable(name):
        raise RuntimeError(PASSWORD)
    monkeypatch.setattr(driver, "get_log", unavailable)
    diagnostics = ns.safe_network_diagnostics(driver)
    assert diagnostics["log_capture_available"] is False
    assert diagnostics["auth_http_statuses"] == []
    assert PASSWORD not in json.dumps(diagnostics)


def test_options_use_headed_standard_chrome(monkeypatch):
    monkeypatch.delenv("NAUKRI_CHROME_BINARY", raising=False)
    arguments = ns.build_chrome_options().arguments
    assert not any("headless" in arg or "AutomationControlled" in arg or "disable-web-security" in arg for arg in arguments)


@pytest.mark.parametrize("enabled", [False, True])
def test_http2_transport_option_preserves_browser_security(monkeypatch, enabled):
    monkeypatch.delenv("NAUKRI_CHROME_BINARY", raising=False)
    monkeypatch.setenv("NAUKRI_DISABLE_HTTP2", "true" if enabled else "false")
    options = ns.build_chrome_options()
    expected = ["--window-size=1365,900"] + (["--disable-http2"] if enabled else [])
    assert options.arguments == expected
    capabilities = options.to_capabilities()
    assert capabilities["goog:loggingPrefs"] == {"performance": "ALL"}
    assert capabilities.get("acceptInsecureCerts", False) is False
    assert capabilities["goog:chromeOptions"].get("excludeSwitches", []) == []


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
