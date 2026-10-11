import pytest

from naukri_refresh import RefreshError, browser_launch_options


def test_default_browser_configuration(monkeypatch):
    monkeypatch.delenv("NAUKRI_BROWSER_CHANNEL", raising=False)
    monkeypatch.delenv("NAUKRI_HEADLESS", raising=False)
    assert browser_launch_options() == {"headless": True}


def test_standard_windows_chrome_configuration(monkeypatch):
    monkeypatch.setenv("NAUKRI_BROWSER_CHANNEL", "chrome")
    monkeypatch.setenv("NAUKRI_HEADLESS", "false")
    assert browser_launch_options() == {"headless": False, "channel": "chrome"}


def test_http_protocol_compatibility_preserves_browser_security(monkeypatch):
    monkeypatch.setenv("NAUKRI_DISABLE_HTTP2", "true")
    monkeypatch.setenv("NAUKRI_BROWSER_CHANNEL", "chrome")
    assert browser_launch_options()["args"] == ["--disable-http2"]


@pytest.mark.parametrize("name,value", [("NAUKRI_HEADLESS", "maybe"), ("NAUKRI_BROWSER_CHANNEL", "unknown")])
def test_invalid_browser_configuration_is_rejected(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(RefreshError, match="needs_setup"):
        browser_launch_options()
