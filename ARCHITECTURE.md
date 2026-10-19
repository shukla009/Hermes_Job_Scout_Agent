# Hermes architecture

## Current hands-off upgrade

- Every-two-hour public-runner discovery; weekday IST email window; strict original-date 48h gate.
- Workday/Oracle public discovery is separate from authenticated employer application APIs.
- Gmail alert intake uses authenticated senders, UID cursors and a bounded unresolved-link queue.
- The public-form engine prepares only exact audited no-login/no-challenge forms.
  Zero production adapters are enabled until real contracts are verified. A tested engine
  is not proof that an employer accepts automated submissions.
- Form and email ledgers share job identity, employer cooldown and daily action caps.
  Pending/uncertain results cannot retry through another contact or route.
- GitHub state and immutable feedback support path-authenticated AES-GCM envelopes.
  The Worker accepts legacy JSON during rollout. Enable encryption only after Worker deployment.
- Public visibility is checked each run; private/unknown mode retains conservative quota/cadence fallback.
- Hands-off mode suppresses manual recommendation alerts; outcome buttons remain for already-sent
  applications. See README and docs/ONE_TIME_SETUP.md for current operation.

The sections below describe the legacy-compatible components. Recommendation
cards remain available only when hands-off mode is explicitly disabled.

## Runtime and zero-cost boundary

The scheduled GitHub Actions job and Telegram `/run` use the same scout entrypoint.
Standard Ubuntu runners, bounded runtime/requests and the existing Cloudflare Free
Worker keep the device-independent design. Paid search and model calls default to
disabled. Included account usage is shared: the verified $0 Actions stop-usage
budget is an essential deployment control, not a promise that quotas never expire.

## Discovery and matching

1. Rotate the employer registry by polling tier, preserving cursors and source status.
2. Read public official feeds or structured career pages; optionally read the opted-in Gmail label.
3. Deduplicate before enrichment. Keep source publication dates separate from first-seen dates.
4. Apply location, employment, shift, pay and mandatory-qualification gates.
5. Score responsibilities, résumé evidence, experience, location and freshness.
6. Separate core recommendations from experience-stretch roles.

The registry has 30 employers but is not a claim of 30 working adapters. Blocked,
JavaScript-only and unreadable pages remain explicitly unavailable/link-only.
No authenticated scraping or protective-control bypass is used; Naukri is excluded.

The candidate profile records confirmed ProSca internship January–July 2026 and
full-time employment from August 2026, completed degrees, distinct internships and
project evidence. Titles cannot override missing mandatory experience. The updated
PDF is attached unchanged to permitted recruiter emails.

## Durable state and outcomes

`state/scout.json` stores versioned job metadata, alert quotas, outcomes and send
intents. Raw source pages and unrelated inbox messages are not stored there.

- Discovery, alerting, submission and email acceptance are independent facts.
- Legacy direct-apply entries migrate to alerted/unconfirmed, not submitted.
- A pre-send intent is persisted remotely before SMTP; pending/uncertain intents cannot blindly resend.
- Repository commits preserve concurrent feedback and unrelated changes.
- Corrupt state or failed persistence closes unsafe outreach rather than returning empty history.
- Up to 500 jobs retain active descriptive metadata; compact historical dedup evidence remains.

Telegram buttons authenticate the webhook, chat and sender, then verify a
seven-day HMAC callback bound to a known 24-hex job ID. The Worker writes immutable
`state/feedback/<update_id>.json` files and acknowledges only successful storage.
The next scout consumes events in update-ID order, idempotently; stale callbacks
cannot roll back newer outcomes. Buttons do not start Actions runs.

## Delivery and failure modes

At most five new alerts per IST day include at most one stretch role. Apply links
require user submission; pressing Applied confirms it. Autonomous emails require
a core match and an explicit vacancy-linked recruitment address. Personalisation
uses evidence-backed templates; no mandatory paid inference service is involved.

Mailbox checks gate follow-ups and reconcile uncertain sends. Reply/bounce/outcome
evidence stops a chain; ambiguous or unavailable mailbox checks defer it. Source
failure degrades coverage without fabricating jobs or blocking independent sources.

## Worker deployment

Repository changes do not automatically deploy Cloudflare. Deploy the JavaScript
Worker to the existing Free service and enable message plus callback_query webhook
updates. A dedicated repository-scoped Contents token is recommended; the existing
GITHUB_PAT may serve as a fallback only if it already has the necessary permission.
No new credential scope is silently added. See the Worker deployment guide.

Commands read private state; a known paused budget prevents `/run`, and unavailable
state fails closed. Statistics reflect reconciled records, not unprocessed feedback.
Regression tests cover legacy migration, caps, signatures, idempotence, persistence
failures, uncertain sends and budget-pause dispatch prevention.
