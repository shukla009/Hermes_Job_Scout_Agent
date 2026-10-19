# Hermes Job Scout Agent

- Finds suitable Indian-market jobs and sends truthful applications automatically.
- Runs on GitHub Actions while your computer is off; `/run` starts the same workflow.
- Uses existing Gmail, Telegram and Cloudflare Free. No paid model/search service is required.
- A green workflow means execution succeeded; it does not mean an application or interview happened.

## What the scout does

- Checks public sources every two hours. GitHub schedules may be delayed.
- Fetches real vacancy details from working employer feeds, then checks your fit.
- Accepts only verified original publication dates within **48 hours**. An unknown date,
  refreshed listing, future closing date or new alert cannot make an old job new.
- Reads authenticated job alerts only from the dedicated Gmail label `HermesJobAlerts`.
- Resolves bounded tracking links and verifies public vacancy evidence. Failed links have a small retry queue.
- Rejects unsuitable roles, unsupported mandatory requirements, closed jobs and risky offers.
- Sends a personalised email with the existing `Shivank_Shukla_Data_Analyst.pdf` only when
  the vacancy provides a verified recruitment address and permits email applications.
- Sends initial emails on weekdays between **08:30 and 18:30 IST**. Other runs can discover jobs.
- Rechecks freshness before sending; queued jobs expire after 48 hours.
- Uses at most three application attempts per run and six outbound actions per IST day,
  including at most two follow-ups daily. Uncertain attempts count toward limits.
- Stops duplicate applications across email/form routes, including uncertain submissions.
- Follows up on days four and nine only after checking the mailbox and vacancy is still open.
- Reports human replies immediately; it never conducts a recruiter conversation automatically.

## What can currently submit an application?

- **Verified recruitment email: operational.** Delivery is reported as SMTP acceptance,
  separately from human replies and interviews.
- **Public form engine: implemented and tested, with zero live adapters enabled.**
  Four employer forms were audited, but none met the required freshness, eligibility and
  submission/confirmation checks. It would be misleading to claim those applications work.
- A form can be enabled only after its exact fields, consent, resume upload and confirmation
  have been verified. Login, OTP, CAPTCHA, unknown answers and changed forms are skipped.
- New adapters first run three scheduled shadow runs, then a two-application daily canary
  cap for seven days. Ambiguous submissions are held without automatic retries.
- No daily approval queue is required. Unsupported routes appear as coverage gaps.

## Jobs and locations

- Delhi-NCR, Delhi, Noida, Gurgaon/Gurugram. Remote vacancies must permit India-based applicants.
- Permanent full-time work. Explicit night, US and rotational shifts are rejected.
- Salary maxima below ₹6.5 LPA are rejected. Undisclosed salary or shift is flagged, but allowed.
- Salary is never included in application emails.
- Emails highlight three role-relevant ProSca achievements in bullet points,
  preserving the resume's metrics, and include the LinkedIn profile link.
  Other internships remain available for job matching, not email highlight bullets.
- The free email composer uses a fetched job description to identify supported
  responsibilities and select up to four resume-listed skills. Knowledge-only
  skills are labelled; no ProSca use of those tools is implied. Unverified or
  unsupported descriptions receive no invented alignment sentence.
- Emails end with Portfolio, LinkedIn, Email and Phone together, then
  "Best regards, Shivank Shukla". Follow-ups use the same contact footer.
- Prioritises research/market intelligence, MIS/reporting, junior BI/data analysis,
  data quality/operations and process/business analysis based on actual responsibilities.
- Verified listed SQL knowledge can meet a knowledge requirement; it cannot establish
  professional production experience. Alternative requirements are distinguished from AND requirements.
- ProSca internship ended July 2026; full-time role began August 2026. Both degrees are completed.
- Internships, projects and full-time work remain separate; overlapping internships are not double-counted.
- The current resume stays unchanged. No invented experience, guessed recruiter emails or fake claims.

## Where jobs come from

- The registry contains 31 sources, not a promise of 31 productive employers.
- Public Greenhouse, Lever, SmartRecruiters, Ashby and Workable connectors where supported.
- **Genpact Workday:** public search and vacancy details, including NCR location filtering.
- **EXL Oracle Recruiting:** public search and details, including original posting dates.
- Workday/Oracle listing access does not provide employer credentials for application APIs.
- Himalayas' free feed supplies a bounded slice of full-time India-compatible remote jobs.
- Official readable career pages and structured vacancy data supplement feeds.
- Authenticated alerts from supported free platforms provide additional discovery hints.
- Diagnostics distinguish productive feeds, verified feeds without matches, discovery-only
  pages and temporarily failing sources. Blocked sources never produce invented vacancies.
- Generic watchlists rotate and cool down; sources share request and runtime limits.
- **Naukri is excluded:** no requests, profile actions or Telegram features.
- No authenticated job-board scraping, CAPTCHA bypass, paid proxies or paid fallback.

## Telegram

- `/run`: start the scout while your device is off.
- `/queue`: show hands-off processing status and outcome buttons for already-sent applications.
- `/status`: show the last run, SMTP acceptance, portal confirmation and form readiness.
- `/stats`: show recorded applications, replies and interviews separately.
- Reply/Interview/Rejected buttons record outcomes; they are not application approvals.
- Routine unchanged zero-result runs stay quiet apart from the daily summary; important failures remain visible.

## Privacy and ₹0 operation

- Sensitive state can be encrypted with AES-256-GCM before it reaches the public repo.
- Deploy the compatible Worker first, then enable `SCOUT_ENCRYPT_STATE`; a repository
  push does not deploy Cloudflare automatically.
- Credentials and the optional verified application-answer bank belong in secrets.
- No private email bodies, answer payloads or form tokens are persisted in reports.
- Encryption does not erase plaintext in old Git history. Key rotation needs migration with the old key.
- Standard Ubuntu runners are free for this public repo. Private/unknown visibility activates
  a conservative 1,200-minute guard and three scheduled runs daily; no paid fallback is selected.
- Runs are bounded to 120 HTTP requests, six minutes of discovery and a ten-minute workflow timeout.
- Regression tests run on code pushes; scheduled runs use a lightweight import/compile check.
- Source yield, confirmed submissions, human replies and interviews matter more than raw job counts.

## Setup and development

- See [one-time setup](docs/ONE_TIME_SETUP.md) for free alerts, answer-bank format and encryption rollout.
- Configure sources in `config/employers.json`; public forms in `config/application_adapters.json`.
- Keep the existing Cloudflare plan Free and paid overages stopped.

```powershell
python -m pytest tests -q
node tests/worker_test.mjs
```

No source or automation can guarantee enough matching vacancies, recruiter responses or interviews.
