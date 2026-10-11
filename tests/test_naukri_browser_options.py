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


@pytest.mark.parametrize("name,value", [("NAUKRI_HEADLESS", "maybe"), ("NAUKRI_BROWSER_CHANNEL", "unknown")])
def test_invalid_browser_configuration_is_rejected(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(RefreshError, match="needs_setup"):
        browser_launch_options()
