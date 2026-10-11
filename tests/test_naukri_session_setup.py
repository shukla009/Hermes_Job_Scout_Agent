"""Privacy checks for the interactive session setup helper; no login is performed."""
from scripts.naukri_session_setup import filtered_session


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
