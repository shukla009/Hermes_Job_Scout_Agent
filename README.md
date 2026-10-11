# Hermes Job Scout Agent

Hermes is an automated job-search assistant that runs on GitHub Actions, even when your computer is switched off. It finds suitable jobs, filters out poor matches, sends verified opportunities to Telegram, and emails recruiters only when a genuine public email address is available.

## What the automation does

- Runs automatically three times every day.
- Can also be started at any time by sending `/run` to the configured Telegram bot.
- Searches official company recruitment systems and recent public LinkedIn results.
- Reads the complete job description when an official feed provides it.
- Checks the title, location, experience, employment type, shift, skills, salary, and posting status.
- Gives each eligible job a fit score out of 100.
- Sends suitable direct-application links to Telegram.
- Sends a personalised email automatically when a verified recruiter email is present in the source.
- Attaches the existing `Shivank_Shukla_Data_Analyst.pdf` résumé to every application email.
- Remembers previously handled jobs so they are not sent repeatedly.

## Optional Naukri profile refresh

- A separate, optional job can refresh your Naukri profile whenever this workflow runs, including Telegram `/run`.
- It removes and restores the final sentence's full stop with two saves, leaving your summary and portfolio URL exactly as before.
- The local test changed the profile update date to Today; improved recruiter visibility still needs to be measured.
- The job uses a private Naukri session and verifies the correct account before editing.
- Encrypted recovery protects interrupted edits, and unexpected text changes stop the refresh.
- Your computer can be off after cloud login has been configured and validated.
- The feature starts disabled and does not affect existing scouting or email delivery.
- Follow [the private setup and activation guide](docs/NAUKRI_REFRESH.md) to configure secrets, check cloud login, and enable it.

## Daily schedule

The GitHub Actions workflow runs at approximately:

- 08:17 AM IST
- 02:37 PM IST
- 08:23 PM IST

GitHub may occasionally start a scheduled workflow a few minutes late. If one run is already active, another requested run waits in the queue instead of cancelling it.

## Running it from Telegram

- Send `/run` to the configured Telegram bot.
- The Cloudflare Worker checks that the command came from the allowed Telegram chat.
- The Worker asks GitHub to run `.github/workflows/job_scout.yml` on the `main` branch.
- Telegram confirms whether GitHub accepted the request.
- The final discovery summary and suitable job alerts arrive in Telegram when the workflow finishes.

The `/run` command requires the deployed Cloudflare Worker and a valid `GITHUB_PAT` with permission to trigger GitHub Actions.

## Job sources

The primary sources are public feeds from official applicant-tracking systems:

- Greenhouse
- Lever
- SmartRecruiters

The employer list currently includes:

- Bosch
- Groww
- Meesho
- NielsenIQ
- Paytm
- Portcast
- ProcDNA
- Razorpay

These feeds do not use paid search credits. Employers can be added or removed in `config/employers.json`.

The automation also performs one rotating Google search for recent public LinkedIn hiring posts when `SERPAPI_KEY` is available. This is a supplementary source: the official ATS searches continue if SerpAPI is unavailable, out of quota, or returns no results.

## Preferred roles

The automation considers suitable junior or early-career roles such as:

- Data Analyst
- Business Analyst
- BI or Business Intelligence Analyst
- Reporting or MIS Analyst
- Research or Market Research Analyst
- Insights Analyst
- Data Operations or Data Quality Analyst
- Operations, Product, Commercial, Strategy, Risk, or Marketing Analyst when the requirements match
- Reporting Executive or Research Associate
- Junior ETL-related roles

The job description is also checked for useful skills such as SQL, Excel, Power BI, Python, Tableau, ETL, dashboards, data cleaning, reporting, and research.

## Accepted locations

- Delhi
- New Delhi
- Delhi-NCR
- Gurugram
- Gurgaon
- Noida
- Explicitly remote positions

Hybrid roles are accepted only when their location is in Delhi-NCR, Gurugram/Gurgaon, or Noida. A listing that says only “India” is not automatically accepted. Regular positions in Bengaluru, Mumbai, Hyderabad, Chennai, or other cities are rejected.

## Automatic rejection rules

A job is rejected when the listing clearly indicates any of the following:

- Senior, lead, manager, principal, director, or head-level position
- Internship
- Contract, temporary, or part-time employment
- More than two years of required experience, unless the wording clearly includes a suitable junior range
- Night shift, US shift, graveyard shift, or rotational shift
- Location outside the accepted locations and not explicitly remote
- Salary below ₹6.5 LPA when the salary is stated
- Expired application deadline
- LinkedIn or public-web post older than approximately 48 hours
- Unrelated role, training advertisement, course, or non-job content

An unspecified salary or shift is not automatically rejected. Salary is never mentioned in application emails.

## What happens to a suitable job

### When a verified recruiter email is available

- The email must appear exactly in the public source content.
- The automation never guesses or constructs an email address.
- A concise application email is generated for the specific role and company.
- The existing résumé is attached.
- The message is sent through the configured Gmail account.
- Successful delivery and the job fingerprint are saved in the history.
- Follow-ups are scheduled for approximately day 4 and day 9.

### When no recruiter email is available

- The job is not treated as irrelevant merely because it has no email.
- Telegram receives the role, company, location, fit reason, and official application link.
- You can open the link and apply directly.

## Email and follow-up safeguards

- Maximum 3 new application emails per run.
- Maximum 6 application and follow-up emails per UTC day.
- Maximum 2 follow-up emails per run.
- A recipient is not contacted again within 30 days.
- Previously handled job links are deduplicated.
- Placeholder, no-reply, support, privacy, and invalid addresses are rejected.
- The candidate's own email address cannot be selected as the recruiter address.
- Gmail IMAP is checked before a follow-up.
- A detected recruiter reply stops future follow-ups.
- A detected bounce stops the chain and adds the address to `suppressed_emails.json`.

## Use of AI

- The core filtering and scoring rules are deterministic and do not require an AI model.
- NVIDIA model review is optional and disabled by default.
- If the model is unavailable, the automation still discovers and filters jobs.
- A safe deterministic email template is used if AI email generation is unavailable.
- No job is accepted only because an AI model says it is suitable; hard rejection rules run first.

## Telegram run summary

At the end of a run, Telegram reports:

- Healthy data sources
- Jobs discovered
- Full job pages or ATS records fetched
- Direct-application leads
- Verified-email leads
- Emails accepted by Gmail SMTP
- Duplicate jobs
- Irrelevant jobs
- Safety-related skips
- Model errors
- Discovery-source warnings, if any

`no_matches` means the sources worked but no listing passed the filters. `partial` means at least one source or email service failed while the remaining parts continued. `safety_stop` means an email limit was reached.

## Important files

- `.github/workflows/job_scout.yml` — schedule, secrets, tests, execution, and state persistence
- `scout_agent.py` — discovery, filtering, scoring, alerts, emails, and follow-ups
- `config/employers.json` — official ATS employers searched during every run
- `applied_history.json` — jobs already handled
- `follow_up.json` — pending follow-up chains
- `suppressed_emails.json` — bounced or blocked recipient addresses
- `Shivank_Shukla_Data_Analyst.pdf` — résumé attached to application emails
- `tests/test_scout_agent.py` — automated regression tests
- `workers/telegram_trigger_worker.js` — Telegram `/run` Cloudflare Worker
- `.env.example` — supported environment variables

## Required GitHub secrets

- `GMAIL_SENDER_EMAIL`
- `GMAIL_APP_PASSWORD`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

Optional secrets:

- `SERPAPI_KEY` — enables the supplemental public LinkedIn search
- `NVIDIA_API_KEY` — enables optional AI review and email wording

The Cloudflare `/run` Worker separately requires its Telegram settings, `GITHUB_PAT`, `GITHUB_REPO`, `GITHUB_WORKFLOW_FILE`, and webhook secret.

## Cost and device requirements

- No new paid API is required.
- Official ATS feeds are public and free to read.
- The automation runs on GitHub-hosted infrastructure.
- Your computer and browser do not need to remain online.
- It does not copy your LinkedIn cookies or operate your personal Chrome profile.
- GitHub Actions and third-party free-tier limits still apply.

For the more technical architecture and safety explanation, see `ARCHITECTURE.md`.
