"""Optional, isolated Naukri profile refresh using an explicitly supplied session."""
from __future__ import annotations

import json
import argparse
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

PROFILE_URL = "https://www.naukri.com/mnjuser/profile"


class RefreshError(Exception):
    """An intentionally redacted, machine-readable failure."""


def validate_storage_state(raw):
    """Reject foreign authentication data instead of passing it to Chromium."""
    try:
        state = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(state, dict) or set(state) - {"cookies", "origins"}:
            raise ValueError()
        cookies, origins = state.get("cookies", []), state.get("origins", [])
        if not isinstance(cookies, list) or not isinstance(origins, list) or not cookies:
            raise ValueError()
        for cookie in cookies:
            domain = cookie["domain"].lstrip(".").lower()
            if domain != "naukri.com" and not domain.endswith(".naukri.com"):
                raise ValueError()
            if not isinstance(cookie.get("name"), str) or not isinstance(cookie.get("value"), str):
                raise ValueError()
        for origin in origins:
            parsed = urlparse(origin["origin"])
            host = (parsed.hostname or "").lower()
            if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443):
                raise ValueError()
            if host != "naukri.com" and not host.endswith(".naukri.com"):
                raise ValueError()
        return state
    except Exception:
        raise RefreshError("invalid_session") from None


def remove_prose_full_stop(summary):
    """Remove the last unambiguous prose terminator, preserving all other bytes."""
    if not isinstance(summary, str) or not summary.strip():
        raise RefreshError("unsafe_summary")
    # Protect entire URL/email tokens, domain tokens, decimals, abbreviations and
    # ellipses. A sentence terminator must have whitespace or EOF on its right.
    protected = []
    token_pattern = r"https?://\S+|www\.\S+|[\w.+-]+@[\w.-]+|\b[\w-]+(?:\.[\w-]+)+\b"
    for match in re.finditer(token_pattern, summary, re.I):
        protected.append((match.start(), match.end()))
    candidates = []
    for match in re.finditer(r"\.(?=\s|$)", summary):
        i = match.start()
        if any(start <= i < end for start, end in protected):
            continue
        if i == 0 or summary[i - 1] in ".!?" or not summary[i - 1].isalnum():
            continue
        word = re.search(r"([A-Za-z]+)$", summary[:i])
        if word and (len(word.group(1)) == 1 or word.group(1).lower() in {"mr", "mrs", "ms", "dr", "prof", "etc", "vs", "approx", "inc", "ltd"}):
            continue
        candidates.append(i)
    if not candidates:
        raise RefreshError("unsafe_summary")
    i = candidates[-1]
    return summary[:i] + summary[i + 1:]


class EncryptedJournal:
    """Crash recovery data is encrypted and flushed before any profile save."""

    def __init__(self, path, secret):
        from cryptography.fernet import Fernet
        if not secret:
            raise RefreshError("needs_setup")
        self.path = Path(path)
        try:
            self.cipher = Fernet(secret.encode())
        except Exception:
            raise RefreshError("needs_setup") from None

    def load(self):
        if not self.path.exists():
            return None
        try:
            record = json.loads(self.cipher.decrypt(self.path.read_bytes()))
            if record == {"completed": True}:
                return None
            if not isinstance(record, dict) or set(record) - {"original", "intermediate", "email", "operation_id"} or not {"original", "intermediate", "email"}.issubset(record):
                raise ValueError()
            if not all(isinstance(v, str) for v in record.values()):
                raise ValueError()
            if remove_prose_full_stop(record["original"]) != record["intermediate"]:
                raise ValueError()
            return record
        except Exception:
            raise RefreshError("recovery_unreadable") from None

    def write(self, record):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = self.cipher.encrypt(json.dumps(record, ensure_ascii=False).encode())
        fd, temporary = tempfile.mkstemp(dir=self.path.parent, prefix="recovery-", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            if os.name != "nt":
                directory = os.open(str(self.path.parent), os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def clear(self):
        self.write({"completed": True})


def _account_verified(page, email):
    parsed = urlparse(page.url)
    if (parsed.hostname or "").lower() != "www.naukri.com" or not parsed.path.startswith("/mnjuser/profile"):
        raise RefreshError("needs_login")
    try:
        page.get_by_text(email, exact=True).first.wait_for(state="visible", timeout=15000)
    except Exception:
        text = page.locator("body").inner_text(timeout=15000)
        if re.search(r"access denied|request blocked|not authorized to access|don't have permission|do not have permission", text, re.I):
            raise RefreshError("access_denied") from None
        if re.search(r"captcha|verify you are human|security challenge|login|log in|sign in", text, re.I):
            raise RefreshError("needs_login") from None
        raise RefreshError("account_unverified") from None
    text = page.locator("body").inner_text(timeout=15000)
    if re.search(r"captcha|verify you are human|security challenge", text, re.I):
        raise RefreshError("needs_login")
    if email.lower() not in text.lower():
        raise RefreshError("account_unverified")


def _timestamp(page):
    text = page.locator("body").inner_text(timeout=15000)
    match = re.search(r"Profile last updated\s*[-–:]?\s*([^\r\n]+)", text, re.I)
    # Only return a known date label. Never emit arbitrary page content.
    if not match:
        return None
    label = match.group(1).strip()
    if re.fullmatch(r"Today|Yesterday|\d{1,2}\s*[A-Za-z]{3,9}\s*,?\s+\d{4}", label, re.I):
        return label
    return None


def _open_summary(page):
    field = page.get_by_role("textbox", name="Profile summary", exact=True)
    if not field.is_visible():
        edit = page.get_by_role("button", name="Edit profile summary", exact=True)
        # Naukri lazily renders lower profile sections. Its observed quick-link
        # button scrolls to and loads the summary before the edit control exists.
        if not edit.is_visible():
            quick_link = page.get_by_role("button", name="Profile summary", exact=True)
            if quick_link.count() == 1:
                quick_link.click(timeout=15000)
        edit.click(timeout=15000)
    field.wait_for(state="visible", timeout=15000)
    return field


def _save(page, value):
    field = _open_summary(page)
    field.fill(value)
    page.get_by_role("button", name="Save", exact=True).click(timeout=15000)
    # Both UI success variants are accepted. Do not infer success from a click.
    page.get_by_text(re.compile(r"Profile updated successfully|Profile Summary has been successfully saved\.", re.I)).first.wait_for(state="visible", timeout=15000)
    close = page.get_by_role("button", name="Close", exact=True)
    if close.count() and close.first.is_visible():
        close.first.click(timeout=5000)
    # Reload ensures verification reads server-persisted content.
    page.reload(wait_until="domcontentloaded", timeout=30000)


def _read(page):
    return _open_summary(page).input_value(timeout=15000)


def _read_persisted(page):
    page.reload(wait_until="domcontentloaded", timeout=30000)
    return _read(page)


def _operation_id():
    return os.getenv("GITHUB_RUN_ID", "local") + "/" + os.getenv("GITHUB_RUN_ATTEMPT", "1")


def prepare_profile(page, journal, expected_email):
    """Durably prepare the exact operation before the workflow saves its cache."""
    result = {"status": "failed", "before_timestamp": None, "after_timestamp": None,
              "original_restored": False, "recovered": False}
    try:
        _account_verified(page, expected_email)
        result["before_timestamp"] = _timestamp(page)
        record = journal.load()
        current = _read(page)
        if record:
            if record["email"].lower() != expected_email.lower():
                raise RefreshError("recovery_account_mismatch")
            if current == record["intermediate"]:
                try:
                    _save(page, record["original"])
                except Exception:
                    # A timeout can occur after the server accepted the save.
                    pass
                current = _read_persisted(page)
                if current != record["original"]:
                    raise RefreshError("restoration_pending")
                result["recovered"] = True
            elif current != record["original"]:
                raise RefreshError("concurrent_edit")
        intermediate = remove_prose_full_stop(current)
        journal.write({"original": current, "intermediate": intermediate,
                       "email": expected_email, "operation_id": _operation_id()})
        result.update(status="prepared", original_restored=True, after_timestamp=_timestamp(page))
    except Exception as error:
        result["status"] = str(error) if isinstance(error, RefreshError) else "ui_error"
    return result


def refresh_profile(page, journal, expected_email, require_prepared=False):
    """Perform or recover a two-save transaction. All returned fields are safe."""
    result = {"status": "failed", "before_timestamp": None, "after_timestamp": None,
              "original_restored": False, "recovered": False}
    record = None
    try:
        _account_verified(page, expected_email)
        result["before_timestamp"] = _timestamp(page)
        record = journal.load()
        current = _read(page)
        if require_prepared:
            if not record or record.get("operation_id") != _operation_id():
                raise RefreshError("preparation_missing")
            if record["email"].lower() != expected_email.lower():
                raise RefreshError("recovery_account_mismatch")
            if current != record["original"]:
                raise RefreshError("concurrent_edit")
            intermediate = record["intermediate"]
            _account_verified(page, expected_email)
            if _read(page) != current:
                raise RefreshError("concurrent_edit")
            _save(page, intermediate)
            if _read(page) != intermediate:
                raise RefreshError("concurrent_edit")
            _account_verified(page, expected_email)
            _save(page, record["original"])
            if _read(page) != record["original"]:
                raise RefreshError("restoration_pending")
            journal.clear()
            result.update(status="completed", original_restored=True, after_timestamp=_timestamp(page))
            return result
        if record:
            if record["email"].lower() != expected_email.lower():
                raise RefreshError("recovery_account_mismatch")
            if current == record["intermediate"]:
                _save(page, record["original"])
                if _read(page) != record["original"]:
                    raise RefreshError("restoration_pending")
            elif current != record["original"]:
                raise RefreshError("concurrent_edit")
            journal.clear()
            result.update(status="recovered", original_restored=True, recovered=True,
                          after_timestamp=_timestamp(page))
            return result
        intermediate = remove_prose_full_stop(current)
        record = {"original": current, "intermediate": intermediate, "email": expected_email}
        journal.write(record)
        # Re-check both identity and value immediately before the first write.
        _account_verified(page, expected_email)
        if _read(page) != current:
            raise RefreshError("concurrent_edit")
        _save(page, intermediate)
        if _read(page) != intermediate:
            raise RefreshError("concurrent_edit")
        _account_verified(page, expected_email)
        _save(page, current)
        if _read(page) != current:
            raise RefreshError("restoration_pending")
        journal.clear()
        result.update(status="completed", original_restored=True, after_timestamp=_timestamp(page))
    except Exception as error:
        result["status"] = str(error) if isinstance(error, RefreshError) else "ui_error"
        if record and record.get("email", "").lower() == expected_email.lower() and (not require_prepared or record.get("operation_id") == _operation_id()):
            try:
                _account_verified(page, expected_email)
                current = _read_persisted(page)
                if current == record["intermediate"]:
                    try:
                        _save(page, record["original"])
                    except Exception:
                        pass
                    current = _read_persisted(page)
                if current == record["original"]:
                    journal.clear()
                    result["original_restored"] = True
                elif current != record["intermediate"]:
                    result["status"] = "concurrent_edit"
                else:
                    result["status"] = "restoration_pending"
            except Exception:
                result["status"] = "restoration_pending"
    return result


def _notify(result):
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return False
    descriptions = {
        "completed": "Profile refresh completed; original summary verified.",
        "recovered": "An interrupted edit was restored successfully.",
        "session_valid": "GitHub cloud login verified; no profile change made.",
        "needs_login": "Sign-in needs attention. Complete any Google login, OTP or CAPTCHA manually and renew the Naukri session.",
        "account_unverified": "Could not verify the expected account. The session may have expired or a login challenge may be present; renew it manually.",
        "access_denied": "Naukri blocked access from the cloud browser. Refresh remains disabled; job scouting continues independently.",
        "needs_setup": "Private session or recovery-key setup is missing. Complete session setup before enabling refresh.",
        "invalid_session": "The saved session is invalid. Repeat the private Naukri session setup.",
        "restoration_pending": "Original summary could not be verified. Check your Naukri summary manually before another edit.",
        "concurrent_edit": "The summary changed unexpectedly. Check the profile and pending recovery record before retrying.",
        "recovery_unreadable": "Recovery data could not be decrypted. Keep the existing recovery key and check your summary manually.",
        "unsafe_summary": "No safe sentence-ending full stop was found; refresh was skipped.",
        "ui_error": "Naukri could not be accessed or its editor did not respond. Check login and the profile; scouting continues independently.",
        "backup_failed": "The encrypted recovery backup could not be verified. The new refresh was not started.",
        "workflow_failed": "The refresh workflow encountered a setup or infrastructure problem. Check the GitHub Actions run and your profile if needed.",
    }
    status = result.get("status", "workflow_failed")
    if status not in descriptions:
        status = "workflow_failed"
    message = "Naukri Action : " + status + "\n" + descriptions[status]
    if result.get("read_only"):
        message += "\nProfile changes: None (read-only check)"
    elif status != "session_valid":
        message += "\nOriginal summary verified: " + ("Yes" if result.get("original_restored") else "Not confirmed")
    if result.get("after_timestamp"):
        message += "\nProfile updated: " + result["after_timestamp"]
    try:
        request = Request("https://api.telegram.org/bot" + token + "/sendMessage",
                          data=json.dumps({"chat_id": chat, "text": message}).encode(),
                          headers={"Content-Type": "application/json"})
        with urlopen(request, timeout=15) as response:
            return json.load(response).get("ok") is True
    except Exception:
        return False


def main():
    parser = argparse.ArgumentParser()
    phases = parser.add_mutually_exclusive_group()
    phases.add_argument("--prepare", action="store_true")
    phases.add_argument("--execute", action="store_true")
    phases.add_argument("--check-session", action="store_true")
    args = parser.parse_args()
    result = {"status": "disabled", "before_timestamp": None, "after_timestamp": None,
              "original_restored": False, "recovered": False}
    diagnostics = {}
    if os.getenv("NAUKRI_REFRESH_ENABLED", "false").lower() == "true":
        try:
            secret = os.getenv("NAUKRI_STORAGE_STATE", "")
            if not secret:
                raise RefreshError("needs_setup")
            state = validate_storage_state(secret)
            journal = None
            if not args.check_session:
                journal = EncryptedJournal(os.getenv("NAUKRI_RECOVERY_PATH", ".naukri-private/recovery.enc"), os.getenv("NAUKRI_RECOVERY_KEY", ""))
                # Check decryption before launching a browser or doing any writes.
                journal.load()
            from playwright.sync_api import sync_playwright
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                context = browser.new_context(storage_state=state)
                page = context.new_page()
                page.set_default_timeout(15000)
                navigation = page.goto(PROFILE_URL, wait_until="domcontentloaded", timeout=45000)
                if args.check_session:
                    diagnostics["http_status"] = navigation.status if navigation else None
                email = os.getenv("NAUKRI_EXPECTED_EMAIL", "work.shuklashivank@gmail.com")
                if args.check_session:
                    _account_verified(page, email)
                    _read(page)
                    result.update(status="session_valid", before_timestamp=_timestamp(page))
                elif args.prepare:
                    result = prepare_profile(page, journal, email)
                else:
                    result = refresh_profile(page, journal, email, require_prepared=bool(os.getenv("GITHUB_RUN_ID")))
                context.close()
                browser.close()
        except Exception as error:
            result["status"] = str(error) if isinstance(error, RefreshError) else "ui_error"
        result["read_only"] = args.check_session
        if not args.prepare or result["status"] != "prepared":
            result["telegram_notified"] = bool(_notify(result))
    if diagnostics:
        result["diagnostics"] = diagnostics
    report = Path(os.getenv("NAUKRI_REPORT_PATH", "naukri_refresh_report.json"))
    report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result))
    return 0 if result["status"] in {"disabled", "prepared", "completed", "recovered", "session_valid"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
