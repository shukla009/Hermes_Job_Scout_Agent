"""Read-only cloud login diagnostic with standard, headed Selenium Chrome.

No profile save, browser identity spoofing, challenge solving, or session export.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urlparse

from naukri_refresh import PROFILE_URL, RefreshError, _notify, validate_storage_state

LOGIN_URL = "https://www.naukri.com/nlogin/login"
WAIT_SECONDS = 15
SAFE_FAILURE_STATUSES = {"access_denied", "challenge", "needs_login", "needs_setup",
                         "invalid_session", "account_unverified", "ui_error", "navigation_failed",
                         "invalid_credentials", "otp_required", "form_input_failed",
                         "auth_network_denied", "rate_limited"}
INVALID_CREDENTIALS_PATTERN = r"incorrect\s+(?:password|credentials)|invalid\s+(?:password|login|credentials)|(?:email|password)\s+(?:is\s+)?incorrect|wrong\s+password"
OTP_PATTERN = r"\benter\s+(?:the\s+)?(?:otp|one[- ]time\s+(?:password|code))\b"


def detect_page_status(text, url):
    """Classify only known states; never return page content."""
    if re.search(r"access denied|request blocked|not authorized to access|don't have permission|do not have permission|error\s*403", text, re.I):
        return "access_denied"
    if re.search(r"captcha|verify you are human|security challenge|unusual traffic", text, re.I):
        return "challenge"
    if re.search(INVALID_CREDENTIALS_PATTERN, text, re.I):
        return "invalid_credentials"
    if re.search(OTP_PATTERN, text, re.I):
        return "otp_required"
    parsed = urlparse(url)
    if parsed.path.startswith("/nlogin") or re.search(r"session (?:has )?expired|please (?:log|sign) in", text, re.I):
        return "needs_login"
    return None


def build_chrome_options():
    from selenium.webdriver.chrome.options import Options

    options = Options()
    options.add_argument("--window-size=1365,900")
    options.set_capability("goog:loggingPrefs", {"performance": "ALL"})
    if os.getenv("NAUKRI_CHROME_BINARY"):
        options.binary_location = os.environ["NAUKRI_CHROME_BINARY"]
    return options


def _body(driver):
    from selenium.webdriver.common.by import By

    elements = driver.find_elements(By.TAG_NAME, "body")
    return elements[0].text if elements else ""


def _guard(driver, allow_login=False):
    parsed = urlparse(driver.current_url)
    if parsed.scheme != "https" or parsed.hostname not in {"www.naukri.com", "naukri.com"} or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise RefreshError("account_unverified")
    status = detect_page_status(_body(driver), driver.current_url)
    if status and not (allow_login and status == "needs_login"):
        raise RefreshError(status)
    if _first_visible(driver, "input[autocomplete='one-time-code']") is not None:
        raise RefreshError("otp_required")


def _native_login_page(driver):
    _guard(driver, allow_login=True)
    parsed = urlparse(driver.current_url)
    if parsed.hostname != "www.naukri.com" or parsed.path.rstrip("/") != "/nlogin/login":
        raise RefreshError("account_unverified")


def _navigate(driver, url, allow_login=False):
    try:
        driver.get(url)
    except Exception:
        _guard(driver, allow_login=allow_login)
        raise RefreshError("navigation_failed") from None
    _guard(driver, allow_login=allow_login)


def _visible_account(driver, email):
    """Require the expected address as an entire visible line or element."""
    from selenium.webdriver.common.by import By

    parsed = urlparse(driver.current_url)
    if parsed.hostname != "www.naukri.com" or not parsed.path.startswith("/mnjuser/profile"):
        return False
    if any(line.strip().casefold() == email.casefold() for line in _body(driver).splitlines()):
        return True
    return any(element.is_displayed() and element.text.strip().casefold() == email.casefold()
               for element in driver.find_elements(By.CSS_SELECTOR, "[title], [aria-label], .email"))


def _first_visible(driver, selector):
    from selenium.webdriver.common.by import By

    return next((element for element in driver.find_elements(By.CSS_SELECTOR, selector)
                 if element.is_displayed()), None)


def _submit_candidates(driver, password_field=None):
    from selenium.webdriver.common.by import By

    scope = driver
    if password_field is not None:
        forms = password_field.find_elements(By.XPATH, "./ancestor::form[1]")
        if forms:
            scope = forms[0]
    return [element for element in scope.find_elements(By.CSS_SELECTOR, "button[type='submit']")
            if element.is_displayed() and element.is_enabled()
            and element.text.strip().casefold() == "login"]


def safe_network_statuses(driver):
    return safe_network_diagnostics(driver)["auth_http_statuses"]


def safe_network_diagnostics(driver):
    """Keep counts, integer status codes and fixed errors; discard network data."""
    types = {"Document", "Stylesheet", "Image", "Media", "Font", "Script", "TextTrack",
             "XHR", "Fetch", "Prefetch", "EventSource", "WebSocket", "Manifest", "Other"}
    result = {"log_capture_available": False, "resource_request_counts": {},
              "auth_request_count": 0, "naukri_http_statuses": [],
              "auth_http_statuses": [], "network_errors": []}
    try:
        records = driver.get_log("performance")
    except Exception:
        return result
    result["log_capture_available"] = True
    naukri_ids = set()
    failures = []

    def classify_url(url):
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        is_naukri = parsed.scheme == "https" and (host == "naukri.com" or host.endswith(".naukri.com"))
        is_auth = is_naukri and bool(re.search(r"(?:^|[/_-])(?:login|auth|authenticate|authentication|signin)(?:[/_.-]|$)", parsed.path, re.I))
        return is_naukri, is_auth

    for record in records[-2000:]:
        try:
            event = json.loads(record["message"])["message"]
            parameters = event["params"]
            method = event.get("method")
            if method == "Network.requestWillBeSent":
                is_naukri, is_auth = classify_url(parameters["request"]["url"])
                if is_naukri:
                    naukri_ids.add(parameters["requestId"])
                    kind = parameters.get("type", "Other")
                    kind = kind if kind in types else "Other"
                    result["resource_request_counts"][kind] = min(2000, result["resource_request_counts"].get(kind, 0) + 1)
                    result["auth_request_count"] += int(is_auth and kind in {"XHR", "Fetch"})
                continue
            if method == "Network.loadingFailed":
                failures.append((parameters.get("requestId"), parameters.get("errorText", "")))
                continue
            if method != "Network.responseReceived":
                continue
            if parameters.get("type") not in {"XHR", "Fetch"}:
                continue
            response = parameters["response"]
            is_naukri, is_auth = classify_url(response["url"])
            if not is_naukri:
                continue
            status = response["status"]
            if isinstance(status, bool) or not isinstance(status, (int, float)) or not 100 <= status <= 599 or int(status) != status:
                continue
            for key in (["naukri_http_statuses", "auth_http_statuses"] if is_auth else ["naukri_http_statuses"]):
                if int(status) not in result[key] and len(result[key]) < 20:
                    result[key].append(int(status))
        except Exception:
            continue
    known_errors = {"ERR_FAILED", "ERR_ABORTED", "ERR_TIMED_OUT", "ERR_CONNECTION_TIMED_OUT",
                    "ERR_CONNECTION_RESET", "ERR_CONNECTION_REFUSED", "ERR_CONNECTION_CLOSED",
                    "ERR_NAME_NOT_RESOLVED", "ERR_INTERNET_DISCONNECTED", "ERR_NETWORK_CHANGED",
                    "ERR_HTTP2_PROTOCOL_ERROR", "ERR_QUIC_PROTOCOL_ERROR", "ERR_SSL_PROTOCOL_ERROR",
                    "ERR_CERT_AUTHORITY_INVALID", "ERR_BLOCKED_BY_CLIENT", "ERR_BLOCKED_BY_RESPONSE"}
    for request_id, text in failures:
        if request_id not in naukri_ids:
            continue
        match = re.search(r"\b(ERR_[A-Z0-9_]+)\b", text)
        if match and match.group(1) in known_errors and match.group(1) not in result["network_errors"]:
            result["network_errors"].append(match.group(1))
    return result


def auth_diagnostics(driver):
    """Return fixed booleans and a URL category, never raw page data."""
    from selenium.webdriver.common.by import By

    diagnostics = {"invalid_credentials": False, "otp_required": False,
                   "username_field_visible": False, "password_field_visible": False,
                   "login_button_visible": False, "submit_count": 0, "url_kind": "other",
                   "username_aria_invalid": False, "password_aria_invalid": False,
                   "username_html_invalid": False, "password_html_invalid": False}
    try:
        path = urlparse(driver.current_url).path
        if path.startswith("/nlogin"):
            diagnostics["url_kind"] = "login"
        elif path.startswith("/mnjuser/profile"):
            diagnostics["url_kind"] = "profile"
        text = _body(driver)
        diagnostics["invalid_credentials"] = bool(re.search(INVALID_CREDENTIALS_PATTERN, text, re.I))
        diagnostics["otp_required"] = bool(re.search(OTP_PATTERN, text, re.I))
        diagnostics["otp_required"] |= _first_visible(driver, "input[autocomplete='one-time-code']") is not None
        diagnostics["username_field_visible"] = _first_visible(driver, "#usernameField") is not None
        diagnostics["password_field_visible"] = _first_visible(driver, "#passwordField") is not None
        secret_field = _first_visible(driver, "#passwordField")
        candidates = _submit_candidates(driver, secret_field)
        diagnostics["submit_count"] = len(candidates)
        diagnostics["login_button_visible"] = bool(candidates)
        for prefix, selector in (("username", "#usernameField"), ("password", "#passwordField")):
            field = _first_visible(driver, selector)
            if field is not None:
                diagnostics[prefix + "_aria_invalid"] = field.get_attribute("aria-invalid") == "true"
                validity = field.get_property("validity")
                diagnostics[prefix + "_html_invalid"] = isinstance(validity, dict) and validity.get("valid") is False
    except Exception:
        pass
    return diagnostics


def _read_summary(driver):
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait

    selector = "textarea#profileSummaryTxt, textarea[aria-label='Profile summary']"
    field = _first_visible(driver, selector)
    if field is None:
        edit = _first_visible(driver, "button[aria-label='Edit profile summary'], [role='button'][aria-label='Edit profile summary']")
        if edit is None:
            quick = _first_visible(driver, "button[aria-label='Profile summary'], [role='button'][aria-label='Profile summary']")
            if quick is None:
                quick = next((element for element in driver.find_elements(By.CSS_SELECTOR, "button, [role='button']")
                              if element.is_displayed() and element.text.strip() == "Profile summary"), None)
            if quick is not None:
                quick.click()
            edit = WebDriverWait(driver, WAIT_SECONDS).until(
                lambda current: _first_visible(current, "button[aria-label='Edit profile summary'], [role='button'][aria-label='Edit profile summary']"))
        edit.click()
        field = WebDriverWait(driver, WAIT_SECONDS).until(lambda current: _first_visible(current, selector))
    value = field.get_attribute("value")
    if not isinstance(value, str) or not value.strip():
        raise RefreshError("ui_error")
    return value


def _import_session(driver, state):
    """Import only validated Naukri state; never export authentication data."""
    state = validate_storage_state(state)
    for cookie in state["cookies"]:
        # A www login document cannot set cookies owned by other Naukri hosts.
        if cookie["domain"].lstrip(".").lower() not in {"www.naukri.com", "naukri.com"}:
            continue
        expires = cookie.get("expires")
        if isinstance(expires, (int, float)) and expires > 0 and expires <= time.time():
            continue
        converted = {key: cookie[key] for key in ("name", "value", "domain", "path", "secure", "httpOnly") if key in cookie}
        if isinstance(expires, (int, float)) and expires > 0:
            converted["expiry"] = int(expires)
        if cookie.get("sameSite") in {"Strict", "Lax", "None"}:
            converted["sameSite"] = cookie["sameSite"]
        driver.add_cookie(converted)
    for origin in state.get("origins", []):
        if urlparse(origin["origin"]).hostname not in {"www.naukri.com", "naukri.com"}:
            continue
        _navigate(driver, origin["origin"], allow_login=True)
        actual, expected = urlparse(driver.current_url), urlparse(origin["origin"])
        if actual.scheme != expected.scheme or actual.hostname != expected.hostname or actual.port != expected.port:
            raise RefreshError("account_unverified")
        for item in origin.get("localStorage", []):
            if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not isinstance(item.get("value"), str):
                raise RefreshError("invalid_session")
            driver.execute_script("window.localStorage.setItem(arguments[0], arguments[1]);", item["name"], item["value"])


def check_session(driver, expected_email, state=None, username=None, password=None):
    """Run a strictly read-only check. All returned fields are safe to log."""
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.common.keys import Keys

    result = {"status": "ui_error", "read_only": True,
              "auth_method": "password" if username and password else "saved_session",
              "phase": "configuration"}
    input_matches = {}
    submitted = False
    network_diagnostics = None
    try:
        if not expected_email or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", expected_email):
            raise RefreshError("needs_setup")
        if username and password and username.casefold() != expected_email.casefold():
            raise RefreshError("account_unverified")
        if not (username and password):
            state = validate_storage_state(state)
        driver.set_page_load_timeout(45)
        result["phase"] = "login_navigation"
        _navigate(driver, LOGIN_URL, allow_login=True)
        if username and password:
            result["phase"] = "native_login"
            _native_login_page(driver)
            name_field = WebDriverWait(driver, WAIT_SECONDS).until(
                lambda current: _first_visible(current, "#usernameField"))
            secret_field = _first_visible(driver, "#passwordField")
            if secret_field is None:
                raise RefreshError("needs_login")
            _native_login_page(driver)
            name_field.clear()
            secret_field.clear()
            name_field.send_keys(username)
            secret_field.send_keys(password)
            input_matches = {"username_input_match": name_field.get_attribute("value") == username,
                             "password_input_match": secret_field.get_attribute("value") == password}
            if not all(input_matches.values()):
                raise RefreshError("form_input_failed")
            candidates = _submit_candidates(driver, secret_field)
            if len(candidates) != 1:
                raise RefreshError("ui_error")
            _native_login_page(driver)
            try:
                driver.get_log("performance")  # Discard navigation logs before submitting.
            except Exception:
                pass
            login = candidates[0]
            login.send_keys(Keys.ENTER)
            submitted = True
            result["phase"] = "credentials_submitted"

            def login_finished(current):
                _guard(current, allow_login=True)
                return not urlparse(current.current_url).path.startswith("/nlogin")

            try:
                WebDriverWait(driver, WAIT_SECONDS).until(login_finished)
            except RefreshError:
                raise
            except Exception:
                # A SPA can establish a session without leaving its login URL.
                # Check the profile once, but never repeat the login submission.
                _guard(driver, allow_login=True)
            network_diagnostics = safe_network_diagnostics(driver)
            if 429 in network_diagnostics["auth_http_statuses"]:
                raise RefreshError("rate_limited")
            if 403 in network_diagnostics["auth_http_statuses"]:
                raise RefreshError("auth_network_denied")
        else:
            result["phase"] = "session_import"
            _import_session(driver, state)
        result["phase"] = "profile_navigation"
        _navigate(driver, PROFILE_URL)
        result["phase"] = "account_verification"
        try:
            WebDriverWait(driver, WAIT_SECONDS).until(lambda current: _visible_account(current, expected_email))
        except Exception:
            _guard(driver)
            raise RefreshError("account_unverified") from None
        result["phase"] = "summary_read"
        _guard(driver)
        _read_summary(driver)
        _guard(driver)
        if not _visible_account(driver, expected_email):
            raise RefreshError("account_unverified")
        result["status"] = "session_valid"
        if network_diagnostics is not None:
            result["diagnostics"] = network_diagnostics
    except Exception as error:
        status = str(error) if isinstance(error, RefreshError) else "ui_error"
        result["status"] = status if status in SAFE_FAILURE_STATUSES else "ui_error"
        result["diagnostics"] = auth_diagnostics(driver)
        result["diagnostics"].update(input_matches)
        if submitted:
            network_diagnostics = network_diagnostics if network_diagnostics is not None else safe_network_diagnostics(driver)
            result["diagnostics"].update(network_diagnostics)
            statuses = network_diagnostics["auth_http_statuses"]
            if result["status"] in {"needs_login", "ui_error", "navigation_failed"}:
                if 429 in statuses:
                    result["status"] = "rate_limited"
                elif 403 in statuses:
                    result["status"] = "auth_network_denied"
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-session", action="store_true", help="Read-only check (also the default)")
    parser.parse_args(argv)
    result = {"status": "ui_error", "read_only": True, "phase": "browser_launch",
              "auth_method": "password" if os.getenv("NAUKRI_USERNAME") and os.getenv("NAUKRI_PASSWORD") else "saved_session"}
    driver = None
    try:
        from selenium import webdriver

        driver = webdriver.Chrome(options=build_chrome_options())
        result = check_session(driver,
                               os.getenv("NAUKRI_EXPECTED_EMAIL", "work.shuklashivank@gmail.com"),
                               state=os.getenv("NAUKRI_STORAGE_STATE"),
                               username=os.getenv("NAUKRI_USERNAME"),
                               password=os.getenv("NAUKRI_PASSWORD"))
    except Exception:
        pass
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass
    notification = dict(result)
    if result["status"] == "challenge":
        notification["status"] = "needs_login"
    elif result["status"] == "navigation_failed":
        notification["status"] = "ui_error"
    result["telegram_notified"] = bool(_notify(notification))
    Path(os.getenv("NAUKRI_REPORT_PATH", "naukri_selenium_check_report.json")).write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result))
    return 0 if result["status"] == "session_valid" else 1


if __name__ == "__main__":
    raise SystemExit(main())
