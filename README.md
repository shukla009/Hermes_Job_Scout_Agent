# Hermes Job Scout Agent

Hermes helps Shivank find relevant Indian-market vacancies and track applications.
It runs on GitHub Actions while his computer is off. A successful workflow is not
an application or an interview: those outcomes are tracked separately.

## What happens each day

- Scheduled runs: approximately **08:17, 14:37 and 20:23 IST**. GitHub schedules can be delayed.
- Read public official job listings and opted-in alerts, within request/time limits.
- Match duties, mandatory requirements and résumé evidence—not title keywords alone.
- Separate mandatory and preferred requirements: unsupported mandatory skills can reject a job,
  uncertain requirements are labelled for review, and preferred gaps only reduce fit points.
- Reject field-sales/acquisition work even when its title contains “market intelligence”.
- Include dashboard, data-validation, research-executive and business-process title variants.
- New-job alerts require freshness evidence within 48 hours. Older dated jobs are rejected;
  open jobs with an unknown original date remain available for review in `/queue`, clearly
  labelled “not verified latest”, and do not trigger automatic application emails.
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

- A 31-source registry prioritises NCR research, consulting, analytics-services and GCC employers.
- Public Greenhouse, Lever, SmartRecruiters, Ashby and Workable interfaces are supported when employers actually use them.
- Greenhouse detail records are checked only for shortlisted jobs whose list response lacks an
  original publication date; this preserves the strict 48-hour rule without excessive requests.
- Himalayas' public remote-jobs API is polled in a small cached window. Only permanent full-time
  roles open to India/worldwide/APAC and compatible with IST continue to matching.
- Official career-page structured data and readable listing links supplement those feeds.
- Oracle/Workday career pages are monitored through their public pages; no private or reverse-engineered login API is used.
- Registry membership **does not mean every employer is currently readable or has a suitable vacancy**.
- JavaScript-only, blocked or changed pages are reported as unreadable/link-only; they do not produce invented jobs.
- Productive public feeds are checked often enough for the 48-hour window. Low-yield generic
  pages rotate in bounded six-source batches and failed pages cool down instead of being hammered.
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

- The current email introduces Shivank's full-time ProSca role and completed degrees,
  uses role-specific evidence and supported matching skills, and links the exact vacancy.
- The updated résumé is attached as a PDF. Older already-sent emails are not rewritten
  or resent simply because the résumé/template changed.
- The last 30 outreach run summaries record SMTP authentication and the reason for
  no sending, separately from job discovery and SMTP acceptance.

- Recruitment intent and a vacancy-linked address are required; a footer email is insufficient.
- Candidate, placeholder, no-reply, suppressed and recently contacted addresses are rejected.
- A durable pending-send record is saved before SMTP. Uncertain sends are not blindly resent.
- Replies, bounces and confirmed outcomes stop inappropriate follow-ups.
- One bounded read-only inbox scan classifies exact-thread human replies, out-of-office messages,
  auto-acknowledgements, bounces and ambiguous senders. Only an exact sender-and-thread human
  reply changes the recorded outcome; the agent never replies automatically.
- Payment/deposit, paid-equipment or training, credential requests, shortened/lookalike links,
  free-mail contacts and unidentified agencies are surfaced as review signals. Critical risks
  always block automatic résumé sending; free-mail alone is never labelled as fraud.
- Mailbox or persistence errors stop unsafe outreach while discovery may continue.
- The agent generates only truthful role-specific proof points from the candidate profile.

## Keeping it ₹0 and device-independent

- Standard GitHub-hosted Ubuntu runners; no always-on personal device.
- Existing Gmail, Telegram and Cloudflare Free services; no new database or paid hosting.
- Bounded HTTP requests, concurrency, retries and runtime; no automatic paid fallback.
- Full regression tests run on code pushes and pull requests. Scheduled scouting uses a fast
  compile/import integrity check, avoiding repeated test-minute spend on unchanged code.
- Quota exhaustion reduces coverage or pauses execution instead of upgrading a service.
- GitHub's Actions budget was verified at **$0 with stop usage enabled on 11 October 2026**. Included usage is shared across the account; other workflows may exhaust it.
- Keep that billing stop enabled and do not upgrade Cloudflare or optional APIs automatically.

Free operation cannot guarantee a daily supply of matching jobs or interviews.
Measure relevance, confirmed submissions, replies and interview invitations—not
just discovered counts or green workflow badges. The weekly Telegram report now includes
30-day source and role-family funnel counts so low-yield coverage can be tuned using outcomes.

## Development checks

Run the Python regression suite and Worker fixtures before deployment:

```powershell
python -m pytest -q
node tests/worker_test.mjs
```

State and feedback are private GitHub files. Credentials belong in GitHub/Worker
secrets, never tracked files or Telegram messages. See `ARCHITECTURE.md` for the
implementation boundaries.
