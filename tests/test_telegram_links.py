import pytest

from scout_actions import posting_markup
from scout_runtime import telegram_summary


@pytest.mark.parametrize("tracking", ["true", "false"])
def test_job_link_button_exists_with_or_without_feedback(monkeypatch, tracking):
    import scout_agent
    sent = []
    monkeypatch.setenv("TELEGRAM_TRACKING_READY", tracking)
    monkeypatch.setattr(scout_agent, "TELEGRAM_BOT_TOKEN", "fixture-token")
    monkeypatch.setattr(scout_agent, "send_telegram", lambda text, markup=None: sent.append((text, markup)) or True)
    analysis = {"job_id": "a" * 24, "manual_optional": True, "apply_url": "https://careers.example.org/jobs/42"}
    assert scout_agent._send_apply_alert(analysis)
    text, markup = sent[-1]
    assert "href='https://careers.example.org/jobs/42'" in text
    assert markup["inline_keyboard"][0] == [{"text": "Open job posting", "url": analysis["apply_url"]}]
    if tracking == "true":
        assert len([b for row in markup["inline_keyboard"] for b in row if "callback_data" in b]) == 6


def test_invalid_link_never_becomes_a_button():
    assert posting_markup({"application_url": "javascript:alert(1)"}) is None
    assert posting_markup({"application_url": "https://naukri.com/jobs/42"}) is None


def test_summary_explains_when_no_manual_links_are_generated():
    message = telegram_summary({"funnel": {"suitable": 0}, "manual_actions": {"sent": 0}})
    assert "No new manual-application links in this run." in message
    assert "no jobs passed all your requirements" in message
