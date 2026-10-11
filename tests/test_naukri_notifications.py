"""Actionable Telegram notices, with no real network calls."""
import io
import json

import pytest

import naukri_refresh as nr


@pytest.mark.parametrize("status,expected", [
    ("needs_login", "OTP or CAPTCHA"),
    ("account_unverified", "session may have expired"),
    ("restoration_pending", "Check your Naukri summary manually"),
    ("completed", "original summary verified"),
    ("backup_failed", "new refresh was not started"),
])
def test_telegram_uses_requested_label_and_explains_action(monkeypatch, status, expected):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    sent = []

    def fake_urlopen(request, **kwargs):
        sent.append(json.loads(request.data))
        return io.BytesIO(b'{"ok": true}')

    monkeypatch.setattr(nr, "urlopen", fake_urlopen)
    assert nr._notify({"status": status, "original_restored": status == "completed"})
    assert sent[0]["text"].startswith("Naukri Action : ")
    assert expected in sent[0]["text"]


def test_telegram_failure_is_reported_without_exception_text(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    monkeypatch.setattr(nr, "urlopen", lambda *a, **k: io.BytesIO(b'{"ok": false}'))
    assert nr._notify({"status": "needs_login"}) is False
