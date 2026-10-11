"""Privacy checks for the interactive session setup helper; no login is performed."""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from naukri_refresh import PROFILE_URL, RefreshError
from scripts.naukri_session_setup import filtered_session, verify_profile


def test_google_session_data_is_excluded_from_export():
    state = {
        "cookies": [
            {"domain": ".naukri.com", "name": "auth", "value": "naukri-token"},
            {"domain": ".google.com", "name": "SID", "value": "google-token"},
            {"domain": "naukri.com.evil.test", "name": "fake", "value": "foreign-token"},
        ],
        "origins": [
            {"origin": "https://www.naukri.com", "localStorage": []},
            {"origin": "https://accounts.google.com", "localStorage": [{"name": "auth", "value": "secret"}]},
        ],
    }
    exported = filtered_session(state)
    assert [cookie["domain"] for cookie in exported["cookies"]] == [".naukri.com"]
    assert [origin["origin"] for origin in exported["origins"]] == ["https://www.naukri.com"]
    assert "google-token" not in str(exported)
    assert "foreign-token" not in str(exported)


def test_completed_login_in_another_naukri_tab_is_opened_and_verified():
    google = MagicMock(url="https://accounts.google.com/")
    google.is_closed.return_value = False
    naukri = MagicMock(url="https://www.naukri.com/mnjuser/homepage")
    naukri.is_closed.return_value = False
    naukri.goto.side_effect = lambda *a, **k: setattr(naukri, "url", PROFILE_URL)
    context = SimpleNamespace(pages=[google, naukri])
    assert verify_profile(context, "work.shuklashivank@gmail.com") is naukri
    naukri.goto.assert_called_once_with(PROFILE_URL, wait_until="domcontentloaded", timeout=45000)
    naukri.get_by_text.assert_called_once_with("work.shuklashivank@gmail.com", exact=True)
    google.goto.assert_not_called()


def test_missing_naukri_tab_is_rejected_without_navigation():
    google = MagicMock(url="https://accounts.google.com/")
    google.is_closed.return_value = False
    with pytest.raises(RefreshError):
        verify_profile(SimpleNamespace(pages=[google]), "work.shuklashivank@gmail.com")
    google.goto.assert_not_called()
