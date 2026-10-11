# Optional Naukri profile refresh

- Purpose: refresh the profile's visible update date while preserving the summary exactly.
- The local test changed the date from 09 Oct 2026 to Today and restored all 933 characters.
- This does not establish a ranking boost. Track search appearances, recruiter actions, and relevant calls to judge results.
- Baseline observed on 11 Oct 2026: 521 search appearances and 42 recruiter actions. The display's reporting window was not independently verified.
- The feature starts disabled. Existing scouting, email delivery, Telegram `/run`, and resume attachment continue unchanged.

## How it works

- When enabled, every scheduled or manually triggered Job Scout workflow also starts an independent Naukri job.
- GitHub runs a separate headless Chromium browser; your laptop can be off.
- It verifies the logged-in profile belongs to `work.shuklashivank@gmail.com`.
- It reads the current summary instead of storing a fixed copy in source code.
- It removes the final usable sentence full stop. URL dots, domain names, decimals, and abbreviations are protected.
- In the tested profile, this means the period after `environments`, immediately before the `Portfolio:` line.
- It saves, reloads, and verifies the change; restores the exact original text; saves, reloads, and verifies again.
- It reports the observed timestamp and whether restoration was verified.
- Telegram messages start with `Naukri Action :` and explain when login, OTP, CAPTCHA, recovery, or workflow setup needs attention.
- A failed Naukri job does not block the scouting job.

## Private setup on your computer

The existing Google login in your personal Chrome profile is not copied automatically. The setup tool creates a separate temporary Chrome profile and waits for you to sign in manually.

1. Install Python and GitHub CLI (`gh`) from their official sources if they are not already installed.
2. Clone this repository and open a terminal in its folder.
3. Install the optional dependencies:

   ```powershell
   python -m pip install -r requirements-naukri.txt
   ```

4. Authenticate GitHub CLI to your account using `gh auth login` if necessary.
5. Run:

   ```powershell
   python scripts/naukri_session_setup.py --publish
   ```

6. Sign in yourself in the new Chrome window using a supported Naukri login option, then press Enter in the terminal. The helper opens the profile automatically, including when login lands on the homepage or another Naukri tab. If verification fails, the window stays open for another attempt.

- The helper verifies the account and exports only Naukri-domain session data.
- Google cookies are excluded from the exported session.
- The session is sent directly to `NAUKRI_STORAGE_STATE` in GitHub Secrets through stdin.
- `NAUKRI_RECOVERY_KEY` is created only if missing. An existing recovery key is preserved.
- The temporary browser profile is removed after the browser closes.
- Without `--publish`, the helper writes `.naukri-private/session.json`. Treat that file as an account credential; it is ignored by Git.
- Never paste session data, passwords, or OTPs into chat or source files.
- If Google rejects the automation-launched browser, stop. The helper does not bypass Google login protections. A different supported authentication method will need to be evaluated.

## Validate and enable

- Open GitHub Actions and run **Check Naukri Session (read only)**.
- This workflow checks whether GitHub-hosted Chromium can access the correct account and summary. It does not change the profile.
- A successful check returns `session_valid`, but only validates the browser tested. A Selenium or Windows Chrome success does not validate the production headless Chromium job.
- Keep the feature disabled until the production browser's login and complete two-save restoration have been verified. Selenium currently has no production editing backend.
- After that verification, go to repository Settings → Secrets and variables → Actions → Variables.
- Set repository variable `NAUKRI_REFRESH_ENABLED` to `true`.
- Future scheduled runs and Telegram `/run` requests will include the refresh.
- Set the variable to `false` to disable it immediately for future runs.
- Session expiry or a login challenge requires another manual session setup and read-only check.

## Additional read-only Selenium experiment

- The session-check workflow also offers `ubuntu-selenium`.
- It uses standard Chrome with Selenium and an Xvfb virtual display on GitHub's Ubuntu runner. Your computer is not used for execution.
- If `NAUKRI_PASSWORD` is configured as a GitHub Secret, it tests Naukri's native email/password login from a fresh browser.
- The username remains `work.shuklashivank@gmail.com`; this must be a Naukri password, not your Gmail password.
- Without that secret, it checks the previously saved Naukri session instead and reports `auth_method: saved_session`.
- This is a diagnostic experiment, not an automatic production replacement. It never saves profile changes or uploads a resume.
- It submits the native form once using the keyboard. If the login redirect stalls without an explicit error, it opens the profile once to check whether authentication actually succeeded. It does not retry the password.
- Diagnostics contain only fixed validation flags, bounded request counts, HTTP status numbers, and network-error codes; no URLs, passwords, response bodies, or session tokens.
- A successful result requires both the expected account email and a readable summary. A green unit-test run alone is not proof of cloud access.
- Access denial, OTP, and CAPTCHA stop the experiment and produce a `Naukri Action :` notice.
- It uses no stealth flags, proxy service, or paid infrastructure; GitHub free allocations still apply.
- The earlier Linux/Windows checks did not validate cloud access. Production refresh stays disabled until an alternative passes validation and a verified update.

## Recovery and privacy

- Before the first save, an encrypted recovery record contains the original and intermediate summaries.
- The workflow backs up that record in GitHub Actions cache and verifies the exact backup exists before editing.
- Cache keys include the branch, workflow run, and run attempt.
- On completion, a newer encrypted completion marker replaces pending recovery state.
- On an interrupted run, a future run restores the summary only when it still matches the expected intermediate text.
- If you changed the summary yourself, recovery stops instead of overwriting your edit.
- Keep `NAUKRI_RECOVERY_KEY` unchanged when renewing the session. Losing or replacing it prevents decryption of pending recovery.
- GitHub cache can be evicted. Recovery is best effort if both the runner and its cached record are lost; manually verify the summary in that case.
- Logs and reports contain statuses and sanitized dates, not summaries, session tokens, screenshots, traces, or cookies.
- The encrypted recovery cache contains private data and requires the separate secret key to read it.

## Result statuses

- `session_valid`: cloud login was verified without a profile edit.
- `completed`: both saves completed and the original summary was verified.
- `recovered`: an interrupted edit was restored.
- In the scheduled two-phase workflow, recovery can happen during preparation; a subsequent successful refresh reports `completed` with recovery performed during preparation.
- `disabled`: the feature is off.
- `needs_login` / `account_unverified`: session, account, or login challenge needs attention.
- `access_denied`: Naukri blocked the cloud browser; this requires a supported access route, not repeated login attempts.
- `invalid_session` / `needs_setup`: private configuration is missing or malformed.
- `unsafe_summary`: no safe sentence-ending full stop was found.
- `concurrent_edit`: the text changed unexpectedly; it was not overwritten.
- `restoration_pending`: original text could not be verified; check your profile before another edit.
- `recovery_unreadable`: the recovery key or cached data needs attention.

## Limitations

- No paid API, proxy, CAPTCHA service, or stealth browser is used.
- Github Actions runner-minute and cache limits still apply. This optional job adds browser startup and installation time.
- Naukri may expire a session, reject a cloud IP, challenge authentication, or change its editor.
- The feature stops on login challenges and does not solve CAPTCHAs automatically.
- Profile freshness was verified locally; cloud authentication must be validated separately before enabling.
- Review Naukri's current terms before enabling automated access. A punctuation refresh has no verified guarantee of more recruiter calls.
