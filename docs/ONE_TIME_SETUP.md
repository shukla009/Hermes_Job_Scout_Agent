# One-time setup (free services only)

## Job alerts

- Create the Gmail label `HermesJobAlerts` in the existing work mailbox.
- Create a Gmail filter for genuine job alerts from LinkedIn, Indeed, Foundit,
  Cutshort and Instahyre; apply that label without moving unrelated mail into it.
- In each platform's existing free account, choose Delhi/Noida/Gurugram and
  India-remote, full-time, junior analyst/research/reporting roles. Alerts are
  discovery hints: their arrival date cannot prove the vacancy is under 48 hours.
- The scout reads only this label for discovery and authenticates allowed senders.
  Missing label, disabled alerts and access failures appear as distinct statuses.
- Add exact authenticated employer sender domains with repository variable
  `SCOUT_ALERT_SENDER_DOMAINS` (comma-separated). This grants no account access.

## Optional application answers

Public forms are disabled until their exact employer contract is verified. Email
applications do not need this additional secret. A verified form may need answers
that are absent from the resume, such as notice period or work authorisation.

Store the following structure privately as GitHub Actions secret
`SCOUT_APPLICATION_ANSWERS`. Replace placeholders with verified facts. Never put
passwords, identity documents, bank information or this secret in the repository.

```json
{
  "verified": false,
  "verified_at": "",
  "values": {
    "full_name": "Shivank Shukla",
    "email": "work.shuklashivank@gmail.com",
    "phone": "",
    "notice_period_days": "",
    "india_work_authorisation": "",
    "expected_salary_lpa": ""
  },
  "consents": {}
}
```

Set `verified` to true and `verified_at` to the verification timestamp only after
checking every supplied fact. Omit unknown answers; required missing answers block
the form. Consents are exact audited adapter keys, never a blanket permission.
Salary is never included in recruitment emails. The existing PDF is unchanged.

## Private application state

- Deploy the updated Telegram Worker before setting `SCOUT_ENCRYPT_STATE=true`.
- State and feedback use AES-256-GCM with path-bound authentication. The key is
  derived separately from the existing shared bot secret unless `SCOUT_STATE_KEY`
  is supplied identically to Actions and the Worker.
- Keep a secure recovery copy of the secret. Before bot-secret/key rotation,
  migrate state with the old key; changing it first makes state unreadable.
- Encryption protects new versions; it does not remove plaintext already present
  in older Git commits. No history rewrite is performed automatically.
- The repository can stay private. Standard runners consume the owner's included
  private-repository allowance; keep paid overages stopped. The scout's internal
  1,200-minute estimate is not account-wide accounting for CI or other repositories.
- Three daily triggers target 08:47, 14:47 and 20:47 IST. Private scheduled work
  is admitted by persisted reservations (three per IST day, five-hour interval),
  not exact runner start hours. Every skip emits its reason and next eligible time.
- `/run` remains a normal manual run within budget and outreach safety limits.
  The optional GitHub dispatch input `verify_schedule=true` makes a manual test
  obey scheduled cadence too; it never bypasses quota or application controls.
