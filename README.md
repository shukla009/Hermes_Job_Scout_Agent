# Hermes Job Scout Agent

Hermes helps Shivank find relevant Indian-market vacancies and track applications.
It runs on GitHub Actions while his computer is off. A successful workflow is not
an application or an interview: those outcomes are tracked separately.

## What happens each day

- Scheduled runs: approximately **08:17, 14:37 and 20:23 IST**. GitHub schedules can be delayed.
- Read public official job listings and opted-in alerts, within request/time limits.
- Match duties, mandatory requirements and résumé evidence—not title keywords alone.
- Recommend up to five new jobs per IST day, including at most one clearly labelled stretch role.
- Show official application links, fit evidence, missing requirements and salary/shift uncertainty.
- Email a suitable recruiter only when the vacancy explicitly provides a recruitment contact.
- Attach the updated `Shivank_Shukla_Data_Analyst.pdf`; never edit it or invent achievements.
- Reconcile feedback, replies and send history before considering follow-ups.
- Keep failures visible without repeating unchanged routine messages.

## Your matching preferences

- Delhi-NCR, Delhi, Noida, Gurgaon/Gurugram; remote roles must allow India-based applicants.
- Full-time jobs; no explicit night, US or rotational shifts. An undisclosed shift is flagged.
- Reject a stated maximum salary below ₹6.5 LPA; undisclosed pay is flagged. Salary is never put in recruiter emails.
- Prioritise research/market intelligence, MIS/reporting, junior BI/data analysis, data quality and process/business analysis.
- ProSca internship: January–July 2026; full-time employment started August 2026.
- Both degrees are formally completed. Internships, full-time experience and projects remain distinct.
- Overlapping internships are not double-counted; the word “Lead” does not establish managerial seniority.
- SQL listed as a skill does not establish years of professional SQL engineering.
- Core recommendations first; evidence-backed experience-stretch jobs are separate, not automatic-email targets.

Candidate facts and evidence live in `config/candidate_profile.json`. The résumé
is the attachment, not an instruction source.

## Where jobs come from

- A 30-employer registry prioritises NCR research, consulting, analytics-services and GCC employers.
- Public Greenhouse, Lever, SmartRecruiters, Ashby and Workable interfaces are supported when employers actually use them.
- Official career-page structured data and readable listing links supplement those feeds.
- Oracle/Workday career pages are monitored through their public pages; no private or reverse-engineered login API is used.
- Registry membership **does not mean every employer is currently readable or has a suitable vacancy**.
- JavaScript-only, blocked or changed pages are reported as unreadable/link-only; they do not produce invented jobs.
- Source outcomes and cursors are recorded so polling can be rotated without allowing one large board to dominate.
- Optional job-alert ingestion uses only a dedicated Gmail label, `HermesJobAlerts`, not unrelated inbox messages.
- Paid-model and paid-search integrations are **off by default**. Public discovery and deterministic matching do not depend on them.
- Naukri remains excluded. No authenticated job-board scraping, CAPTCHA solving, proxies or guessed recruiter addresses.

Employers and polling tiers are configured in `config/employers.json`. A failed
source does not make all other sources fail.

## Telegram and application tracking

- `/run`: request a run; a persisted budget pause blocks dispatch.
- `/queue`: view outstanding recommendations.
- `/status` and `/stats`: inspect recorded activity and outcomes.
- Recommendation buttons: **Applied**, **Not suitable**, **Snooze**, **Reply**, **Interview**, **Rejected**.
- Opening an application link does not mean you applied. Press **Applied** only after submission.
- Outcomes are acknowledged only after being saved privately in GitHub, then reconciled on the next run.
- Old direct-apply alerts migrate to “submission unconfirmed”; historical recruiter emails remain separate.
- The Worker is deployed and the secured webhook accepts `message` and `callback_query` updates. Private-state commands require the existing repository-scoped token to have Contents read/write as well as Actions write.

See `workers/DEPLOY_TELEGRAM_TRIGGER.md` for deployment and permissions. On 13 October
2026, GitHub Actions validation passed three shadow runs and a post-validation run;
three qualified vacancies were found. The repository-only token permissions were
updated and the secured Worker state-access check passed on the same date.
Live `/status`, `/stats`, `/queue` and `/run` checks also passed. Signed action buttons
are enabled on future recommendations; mark **Applied** only after you submit a real application.
Scheduled discovery and outgoing Telegram alerts do not depend on that Worker token.

## Email safeguards

- Recruitment intent and a vacancy-linked address are required; a footer email is insufficient.
- Candidate, placeholder, no-reply, suppressed and recently contacted addresses are rejected.
- A durable pending-send record is saved before SMTP. Uncertain sends are not blindly resent.
- Replies, bounces and confirmed outcomes stop inappropriate follow-ups.
- Mailbox or persistence errors stop unsafe outreach while discovery may continue.
- The agent generates only truthful role-specific proof points from the candidate profile.

## Keeping it ₹0 and device-independent

- Standard GitHub-hosted Ubuntu runners; no always-on personal device.
- Existing Gmail, Telegram and Cloudflare Free services; no new database or paid hosting.
- Bounded HTTP requests, concurrency, retries and runtime; no automatic paid fallback.
- Quota exhaustion reduces coverage or pauses execution instead of upgrading a service.
- GitHub's Actions budget was verified at **$0 with stop usage enabled on 11 October 2026**. Included usage is shared across the account; other workflows may exhaust it.
- Keep that billing stop enabled and do not upgrade Cloudflare or optional APIs automatically.

Free operation cannot guarantee a daily supply of matching jobs or interviews.
Measure relevance, confirmed submissions, replies and interview invitations—not
just discovered counts or green workflow badges.

## Development checks

Run the Python regression suite and Worker fixtures before deployment:

```powershell
python -m pytest -q
node tests/worker_test.mjs
```

State and feedback are private GitHub files. Credentials belong in GitHub/Worker
secrets, never tracked files or Telegram messages. See `ARCHITECTURE.md` for the
implementation boundaries.
