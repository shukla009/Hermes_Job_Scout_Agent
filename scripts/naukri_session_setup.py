"""Capture a Naukri-only session after the user signs in manually.

No personal Chrome profile is read. Google cookies are never exported.
Run this interactively on your own computer, not in GitHub Actions.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from naukri_refresh import PROFILE_URL, RefreshError, validate_storage_state


def naukri_host(value):
    host = str(value).lower().lstrip(".")
    return host == "naukri.com" or host.endswith(".naukri.com")


def filtered_session(state):
    filtered = {
        "cookies": [cookie for cookie in state.get("cookies", []) if naukri_host(cookie.get("domain", ""))],
        "origins": [origin for origin in state.get("origins", [])
                    if naukri_host(urlparse(origin.get("origin", "")).hostname or "")],
    }
    return validate_storage_state(filtered)


def github(args, payload=None):
    try:
        result = subprocess.run(["gh", *args], input=payload, text=True,
                                capture_output=True, check=False)
    except OSError:
        raise RefreshError("GitHub CLI is unavailable; install gh and sign in first.") from None
    if result.returncode:
        raise RefreshError("GitHub operation failed. Check gh authentication and repository permissions.")
    return result.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publish", action="store_true", help="Send the Naukri session directly to GitHub Secrets via stdin")
    parser.add_argument("--repo", default="shukla009/Hermes_Job_Scout_Agent")
    parser.add_argument("--expected-email", default="work.shuklashivank@gmail.com")
    parser.add_argument("--output", default=".naukri-private/session.json", help="Used only without --publish; this file grants account access")
    args = parser.parse_args()
    if args.publish:
        # Verify gh before asking the user to log in. Do not print credential data.
        github(["repo", "view", args.repo, "--json", "name"])
    from playwright.sync_api import sync_playwright
    with tempfile.TemporaryDirectory(prefix="hermes-naukri-login-") as profile_dir:
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(profile_dir, channel="chrome", headless=False)
            page = context.new_page()
            page.goto(PROFILE_URL, wait_until="domcontentloaded", timeout=45000)
            print("Sign in yourself in the new Chrome window, using Continue with Google.")
            print("Open your Naukri profile after login. Do not paste passwords or codes into this terminal.")
            input("When your Naukri profile is visible, press Enter here: ")
            parsed = urlparse(page.url)
            if not naukri_host(parsed.hostname or "") or parsed.path.rstrip("/") != "/mnjuser/profile":
                raise RefreshError("The selected tab is not the Naukri profile. Complete login and try again.")
            page.get_by_text(args.expected_email, exact=True).first.wait_for(state="visible", timeout=15000)
            # Capture only this explicitly created browser session, then remove
            # every non-Naukri cookie/origin before persistence or transmission.
            state = filtered_session(context.storage_state())
            context.close()
    payload = json.dumps(state, separators=(",", ":"))
    if len(payload.encode()) > 48000:
        raise RefreshError("Session exceeds GitHub's secret size limit. Setup stopped without publishing.")
    if args.publish:
        secrets = json.loads(github(["secret", "list", "--repo", args.repo, "--json", "name"]))
        if "NAUKRI_RECOVERY_KEY" not in {item["name"] for item in secrets}:
            from cryptography.fernet import Fernet
            github(["secret", "set", "NAUKRI_RECOVERY_KEY", "--repo", args.repo], Fernet.generate_key().decode())
        # Preserve an existing key: encrypted pending recovery must remain readable.
        github(["secret", "set", "NAUKRI_STORAGE_STATE", "--repo", args.repo], payload)
        print("Naukri-only session stored privately in GitHub Secrets. Recovery key preserved or created.")
        print("Run 'Check Naukri Session (read only)' in GitHub Actions before enabling the feature.")
    else:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
        print("Naukri-only session saved in the private output file. Treat it like a password.")
        print("Do not commit, share, or paste that file into chat.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Setup cancelled.")
        raise SystemExit(1)
    except Exception as error:
        print(str(error) if isinstance(error, RefreshError) else "Setup failed. Login may need attention; no raw authentication details were printed.")
        raise SystemExit(1)
