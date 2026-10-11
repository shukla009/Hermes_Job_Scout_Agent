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


def detect_page_status(text, url):
    """Classify only known states; never return page content."""
    if re.search(r"access denied|request blocked|not authorized to access|don't have permission|do not have permission|error\s*403", text, re.I):
        return "access_denied"
    if re.search(r"captcha|verify you are human|security challenge|unusual traffic", text, re.I):
        return "challenge"
    parsed = urlparse(url)
    if parsed.path.startswith("/nlogin") or re.search(r"session (?:has )?expired|please (?:log|sign) in", text, re.I):
        return "needs_login"
    return None


def build_chrome_options():
    from selenium.webdriver.chrome.options import Options

    options = Options()
    options.add_argument("--window-size=1365,900")
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

    result = {"status": "ui_error", "read_only": True,
              "auth_method": "password" if username and password else "saved_session",
              "phase": "configuration"}
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
            name_field.send_keys(username)
            secret_field.send_keys(password)
            login = next((element for element in driver.find_elements(By.CSS_SELECTOR, "button[type='submit'], button")
                          if element.is_displayed() and element.text.strip().casefold() == "login"), None)
            if login is None:
                raise RefreshError("ui_error")
            _native_login_page(driver)
            login.click()

            def login_finished(current):
                _guard(current, allow_login=True)
                return not urlparse(current.current_url).path.startswith("/nlogin")

            try:
                WebDriverWait(driver, WAIT_SECONDS).until(login_finished)
            except RefreshError:
                raise
            except Exception:
                _guard(driver)
                raise RefreshError("needs_login") from None
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
    except Exception as error:
        result["status"] = str(error) if isinstance(error, RefreshError) else "ui_error"
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
