# Hermes Job Scout

Hermes is an unattended, zero-paid-API job discovery and outreach pipeline designed for GitHub Actions. It keeps the existing resume attachment (`Shivank_Shukla_Data_Analyst.pdf`) and does not require a personal computer to remain online.

## Discovery

Each run reads public, official ATS feeds listed in `config/employers.json`:

- Greenhouse Job Board API
- Lever Postings API
- SmartRecruiters Posting API

A single rotating public LinkedIn Google query supplements these feeds when `SERPAPI_KEY` is available. LinkedIn is not scraped with a logged-in session and no cookies are stored in GitHub.

The ATS feeds are free and remain the primary source if SerpAPI has no results, reaches quota, or is not configured. One source failing produces a partial run; other sources continue.

## Eligibility and ranking

Hard gates reject:

- senior, lead, manager, director, internship, contract, or part-time roles;
- explicit requirements above two years unless the role is clearly junior/fresher-friendly;
- locations outside Delhi-NCR unless the listing is explicitly remote;
- night, US, or rotational shifts;
- explicit compensation below 6.5 LPA;
- expired or unverifiably old public posts.

Surviving jobs receive a deterministic 0-100 score for role fit, relevant skills, location, experience, source quality, and a verified contact route. The optional NVIDIA model can review wording, but discovery, qualification, alerts, and email composition continue when it is unavailable.

## Delivery safeguards

- Email is sent only to an address present verbatim in the public source content.
- The candidate's own address, placeholders, no-reply addresses, suppressed addresses, and recently contacted recipients are rejected.
- Limits default to 3 initial emails per run and 6 per UTC day.
- Direct-apply jobs are sent to Telegram; they are recorded only after Telegram accepts the alert.
- Follow-ups default to day 4 and day 9. IMAP checks stop a chain after a reply or bounce, and bounced recipients are suppressed.
- Job fingerprints prevent duplicate alerts and applications.

## Runtime

GitHub Actions runs at 08:17, 14:37, and 20:23 IST. Runs are serialized to protect the JSON state files. The workflow tests the code before every live run and commits only durable outreach, follow-up, and suppression state.

The schedule and thresholds can be tuned with the environment variables documented in `.env.example`. Employer coverage can be expanded by adding a verified public ATS slug to `config/employers.json`.
