# Hermes Job Scout Agent

- Finds suitable Indian-market jobs and sends truthful applications automatically.
- Runs on GitHub Actions while your computer is off; `/run` starts the same workflow.
- Uses existing Gmail, Telegram and Cloudflare Free. No paid model/search service is required.
- A green workflow means execution succeeded; it does not mean an application or interview happened.

## What the scout does

- Scheduled triggers are **08:47, 14:47 and 20:47 IST**. GitHub may start them later.
- For a private repository, at most three scheduled searches run per IST day,
  separated by at least five hours. Delayed starts no longer need an exact clock hour.
- Every attempted run emits a `RUN_REPORT`. A skipped attempt has its own reason
  and next eligible time; it does not overwrite the last real search results.
- Fetches real vacancy details from working employer feeds, then checks your fit.
- Accepts only verified original publication dates within **48 hours**. An unknown date,
  refreshed listing, future closing date or new alert cannot make an old job new.
- Reads authenticated job alerts only from the dedicated Gmail label `HermesJobAlerts`.
- Resolves explicit public employer destinations and verifies vacancy evidence.
  Only transient failures are retried, with a small bounded queue. Access-denied,
  unsafe and permanently unavailable links are classified without exposing URL tokens.
- Rejects unsuitable roles, unsupported mandatory requirements, closed jobs and risky offers.
- Sends a personalised email with the existing `Shivank_Shukla_Data_Analyst.pdf` only when
  the vacancy provides a verified recruitment address, a named recruitment contact is
  evidenced on its employer's official site, or that employer publishes an official
  careers inbox explicitly accepting applications. Vacancy-specific portal-only
  instructions always override a general inbox. No guessed or unrelated contacts.
- Careers-inbox joins require matching employer identity and an owned vacancy URL/ATS tenant,
  exact contact evidence and verification within 24 hours. At most six contact-page requests
  run per search, two per employer, within the existing shared HTTP/runtime budget.
- The new careers-inbox route starts with **three actual scheduled shadow executions**.
  It then permits at most **two new-route attempts daily for seven days**, inside the existing
  six-action ceiling. Original vacancy-email outreach continues during this validation.
- Sends initial emails on weekdays between **08:30 and 18:30 IST**. Other runs can discover jobs.
- Rechecks freshness before sending; queued jobs expire after 48 hours.
- Uses at most three application attempts per run and six outbound actions per IST day,
  including at most two follow-ups daily. Uncertain attempts count toward limits.
- Stops duplicate applications across email/form routes, including uncertain submissions.
- Follows up on days four and nine only after checking the mailbox and vacancy is still open.
- Reports human replies immediately; it never conducts a recruiter conversation automatically.

## Named recruiters and personalised outreach (free cloud route)

- Agent Reach supplies backend guidance, not an email database. The workflow directly uses
  Exa's unauthenticated hosted MCP **web_search_exa only**, plus optional Jina Reader.
  It needs no running computer, Hunter account, paid key, browser cookies or Codex session.
- Checks the vacancy first, then verified employer recruitment pages. Search results only
  suggest pages: snippets, social posts and employee databases cannot authorise sending.
- Requires a published work address, named person, current employer and recruitment function.
  Exact-role hiring managers take priority over relevant recruiters, HR recruitment contacts,
  then the verified general careers inbox. Unrelated department managers do not qualify.
- For ATS vacancies, the official employer site must link the exact configured ATS tenant.
  Unknown domains also require an employer-website assertion in the trusted ATS vacancy;
  a search ranking/name match alone is not enough. Unverified bindings stay blocked.
- Never guesses addresses, probes inboxes, scrapes logged-in LinkedIn, uses private addresses,
  or ignores portal-only/no-unsolicited-email instructions. No alternate recipient after silence.
- Named contacts without a resume CTA can qualify only with the person/employer/recruitment
  evidence above. Public PDFs need explicit name/designation/email labels; unsupported layouts
  are skipped. Reader is optional after a successful public original fetch, never an access bypass.
- Uses a verified first-name greeting when available, the actual role/requisition and supported
  responsibilities. Existing ProSca bullets, metrics, grouped links and PDF resume remain unchanged.
- After a confirmed portal submission or explicit **Applied** feedback, permits at most one
  separately tracked recruiter introduction. A user report is not called a portal receipt.
  Prior application email, pending/ambiguous submission or uncertain send blocks an introduction.
- Both purposes share the six-outbound-actions daily ceiling, cooldowns, reply/bounce suppression
  and day-four/day-nine follow-up rules. An introduction is not counted as another application.
- Searches are limited to **two per scout run, six per IST day**; failures count too.
  At most **four evidence/Reader requests per run** share the existing 120-request/420-second budget.
  Discoveries are cached, revalidated within 24 hours and unused cache entries expire after 30 days.
  Contact names, exact excerpts and provenance are stored in encrypted state, not public logs.
- Authentication/payment demands, free-quota exhaustion and changed schemas stop enrichment;
  there is no paid fallback, key rotation or limit evasion. Existing outreach continues safely.
- The `Free contact backend verification` Actions workflow checks Exa search and Jina without
  candidate data or Gmail credentials. New sending requires a successful **cloud** check and
  the repository variable `SCOUT_NAMED_BACKENDS_VERIFIED=named-recruitment-v1`.
- Then three distinct actual scheduled shadow runs precede a seven-day canary capped at two
  new-route attempts daily. Manual reruns do not accelerate it. Existing vacancy emails remain live.
- `/status` and Telegram summaries distinguish contacts found, relevance confirmed, rejected
  contacts, backend status, SMTP-accepted introductions and human replies. `/actions` remains
  optional for unreachable opportunities, not an approval queue. Published email evidence does
  not prove deliverability or guarantee a reply/interview.

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
- When relevant, a verified existing portfolio case study is named separately from the
  ProSca work bullets. Links remain grouped in the footer; no project impact is invented.
- Emails end with Portfolio, LinkedIn, Email and Phone together, then
  "Best regards, Shivank Shukla". Follow-ups use the same contact footer.
- Prioritises research/market intelligence, MIS/reporting, junior BI/data analysis,
  data quality/operations and process/business analysis based on actual responsibilities.
- Verified listed SQL knowledge can meet a knowledge requirement; it cannot establish
  professional production experience. Alternative requirements are distinguished from AND requirements.
- Missing minimum experience alone no longer blocks clearly junior/associate/support-scope
  roles. The undisclosed-experience warning remains; generic analyst titles are not enough.
  Explicit experience deficits, unknown mandatory skills and unconfirmed full-time work still block.
- ProSca internship ended July 2026; full-time role began August 2026. Both degrees are completed.
- Internships, projects and full-time work remain separate; overlapping internships are not double-counted.
- The current resume stays unchanged. No invented experience, guessed recruiter emails or fake claims.

## Where jobs come from

- The registry contains 32 sources, not a promise of 32 productive employers.
- All original 31 were checked in a bounded public audit; see `config/source_audit.json`.
  Moody's now polls as a readable dated source; Gartner's nested-address parser is repaired.
  Source ordering favours recorded interviews/replies/reachable applications, with a rotating
  exploration slot. This does not increase the shared request limit.
- Wood Mackenzie's verified Workday feed adds Gurugram research coverage with original
  posting dates. It is a discovery source, not a promise of fresh matches or auto-apply access.
- 6Wresearch, TechSci Research and MarkNtel were investigated separately. Their undated pages
  are **not** presented as fresh jobs. The contact registry can join an official inbox only
  to an independently verified fresh suitable vacancy from that same employer.
- Public Greenhouse, Lever, SmartRecruiters, Ashby and Workable connectors where supported.
- **Genpact Workday:** public search and vacancy details, including NCR location filtering
  and two bounded result pages per keyword. This is not an exhaustive scan of all jobs.
- **EXL Oracle Recruiting:** public search and details, including original posting dates.
- Workday/Oracle listing access does not provide employer credentials for application APIs.
- Himalayas' free feed supplies a bounded slice of full-time India-compatible remote jobs.
- Official readable career pages and structured vacancy data supplement feeds.
- Authenticated alerts from supported free platforms provide additional discovery hints.
- Alert parsing prioritises explicit employer destinations embedded in authenticated content,
  including nested tracker targets and structured email URLs; email dates never prove job age.
- Native platform subscriptions are a one-time candidate-account setting, not something a
  GitHub workflow can create without your account interaction. See [free alert setup](docs/FREE_ALERT_SETUP.md).
- Diagnostics distinguish productive feeds, verified feeds without matches, discovery-only
  pages and temporarily failing sources. Blocked sources never produce invented vacancies.
- Generic watchlists rotate and cool down; sources share request and runtime limits.
- **Naukri is excluded:** no requests, profile actions or Telegram features.
- No authenticated job-board scraping, CAPTCHA bypass, paid proxies or paid fallback.

## Telegram

- `/run`: start the scout while your device is off.
- `/queue`: show hands-off processing status and outcome buttons for already-sent applications.
- `/status`: show the latest attempt/skip separately from the last real search,
  SMTP acceptance, portal confirmation and form readiness.
- `/stats`: show recorded applications, replies and interviews separately.
- `/actions`: show up to three fresh optional phone-friendly opportunities with a referral
  draft and Applied/Skipped/Referral requested buttons. A maximum of three new cards is sent
  per IST day. These are **not approvals**; automatic outreach continues independently.
- Referral requested holds conflicting automatic outreach for that vacancy. Skipped suppresses
  it. Neither is counted as an application; Applied is labelled user-reported, not portal receipt.
- The funnel now separates discovered, verified fresh, suitable, reachable, SMTP accepted,
  portal confirmed, human replies and interviews. Every 14 days, an outcome review recommends
  coverage improvements or targeting/evidence changes rather than higher sending volume.
- Reply/Interview/Rejected buttons record outcomes; they are not application approvals.
- Routine unchanged zero-result runs stay quiet apart from the daily summary; important failures remain visible.

## Privacy and ₹0 operation

- Sensitive state can be encrypted with AES-256-GCM before it reaches GitHub storage.
- Deploy the compatible Worker first, then enable `SCOUT_ENCRYPT_STATE`; a repository
  push does not deploy Cloudflare automatically.
- Credentials and the optional verified application-answer bank belong in secrets.
- No private email bodies, answer payloads or form tokens are persisted in reports.
- Encryption does not erase plaintext in old Git history. Key rotation needs migration with the old key.
- The repository is public by the owner's choice and uses free standard Ubuntu
  runners. If visibility later becomes private or cannot be verified, a conservative
  1,200-minute monthly estimate, three scheduled-run daily limit and five-hour interval apply.
- Full private runs reserve ten minutes before any work; skipped setup reserves one
  minute. These are estimates for this workflow, not an account-wide billing meter:
  CI and other repositories also consume allowance. Keep paid overages stopped.
- No paid runner, model, search service or fallback is selected. If the allowance is
  unavailable, the scout must pause rather than purchase more capacity.
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
