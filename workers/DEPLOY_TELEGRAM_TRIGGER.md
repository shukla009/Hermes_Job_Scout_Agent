# Deploying Telegram /run Trigger to Cloudflare Workers (Free)

## Queue upgrade: required configuration

The JavaScript Worker now supports `/run`, `/queue`, `/actions`, `/status`, `/stats`, and signed
outcome buttons. Outcomes are immutable encrypted GitHub files when encryption is enabled; buttons do
not start Actions runs or require a database. Deploy the updated JavaScript source
to the existing Worker; a repository push alone does not deploy Cloudflare.

- Add secret `GITHUB_STATE_PAT`: a fine-grained token restricted to this
  repository, **Contents read/write** only. `GITHUB_PAT` retains **Actions write**
  for dispatch. Existing tokens may be used temporarily if they have both rights.
- Set `TELEGRAM_USER_ID` to the authorised sender ID. For a private chat, omission
  defaults to `TELEGRAM_CHAT_ID`; groups require the explicit sender ID.
- Keep `TELEGRAM_WEBHOOK_SECRET` and `TELEGRAM_BOT_TOKEN` secret.
- Set webhook `allowed_updates` to `["message", "callback_query"]`, not messages
  alone. Never paste bot tokens into chat or store them in tracked files.
- Callbacks expire after seven days, bind to a registered job, and authenticate
  HMAC signatures with the bot token. Failed GitHub writes are not acknowledged.
- Saved outcomes appear in statistics on the next scheduled scout run. In the
  default hands-off mode, `/queue` shows recent automated applications and
  Reply/Interview/Rejected buttons, not a manual approval queue. An SMTP
  acceptance is not proof of delivery or a recruiter reply.
- Validate with `node tests/worker_test.mjs` before deployment and check `/status`
  after the first successful scout state migration.
- `/actions` is optional, not an approval queue. It exposes only independently verified
  fresh suitable jobs that the scout cannot submit automatically. Applied is user-reported;
  Skipped and Referral requested hold conflicting outreach on the next scout reconciliation.
- Retain the Cloudflare Free plan and stopped paid upgrades. No new paid services
  or database are required. GitHub budget enforcement remains in the workflow;
  a persisted `budget.paused` blocks manual dispatch in the Worker too.

## 1. Prerequisites
- Cloudflare account (free at dash.cloudflare.com)
- Fine-grained GitHub token restricted to this repository: Actions write for
  `/run`, and Contents read/write for state and outcomes (separate tokens preferred).

## 2. Create Cloudflare Worker
1. Go to **Cloudflare Dashboard** → **Workers & Pages** → **Create application** → **Create Worker**
2. Name it: `telegram-job-scout-trigger`
3. Select **JavaScript** as the language (or "Start from scratch" and paste code)
4. Delete default code and paste content from `workers/telegram_trigger_worker.js` (this repo, workers/ folder)
   (RECOMMENDED: the .js worker runs on standard Workers; the .py variant needs the
   python_workers compatibility flag + pywrangler and uses JS fetch interop)

## 3. Set Environment Variables (Secrets)
In Worker settings → **Variables and Secrets** → **Add variable** (mark as **Secret** for sensitive ones):

| Variable Name | Value | Secret? |
|--------------|-------|---------|
| `TELEGRAM_BOT_TOKEN` | `YOUR_BOT_TOKEN` | ✅ Yes |
| `TELEGRAM_CHAT_ID` | `YOUR_CHAT_ID` | ✅ Yes |
| `TELEGRAM_WEBHOOK_SECRET` | `YOUR_WEBHOOK_SECRET` (openssl rand -hex 32) | ✅ Yes |
| `GITHUB_PAT` | `github_pat_XXXXXXXXXXXX` (your fine-grained PAT) | ✅ Yes |
| `GITHUB_REPO` | `shukla009/Hermes_Job_Scout_Agent` | No |
| `GITHUB_WORKFLOW_FILE` | `job_scout.yml` | No |

## 4. Deploy Worker
Click **Save and Deploy** in the existing Worker. If the editor does not load,
use Cloudflare's official Wrangler CLI after authorizing its requested scopes:

```powershell
npx --yes wrangler login --scopes account:read user:read workers_scripts:write
npx --yes wrangler deploy workers/telegram_trigger_worker.js --name telegram-job-scout-trigger --keep-vars --compatibility-date 2026-10-13 --no-autoconfig
```

`--keep-vars` preserves the existing dashboard variables; deployments preserve
secrets. This updates the existing Worker, not a new Worker or paid plan.
Confirm the account and Worker name before deploying. Note your Worker URL:
`https://telegram-job-scout-trigger.YOUR_SUBDOMAIN.workers.dev`

## 5. Set Telegram Webhook
Run this ONE command in your terminal (replace YOUR_WORKER_URL):

```bash
curl -X POST "https://api.telegram.org/botYOUR_BOT_TOKEN/setWebhook" \
     -H "Content-Type: application/json" \
     -d '{"url": "https://telegram-job-scout-trigger.YOUR_SUBDOMAIN.workers.dev", "secret_token": "YOUR_WEBHOOK_SECRET", "allowed_updates": ["message", "callback_query"]}'
```

Expected response: `{"ok":true,"result":true,"description":"Webhook was set"}`

## 6. Test It!
Open Telegram → Message `@ShivankCareerAgent_bot`:
```
/run
```

You should get an instant reply: "🚀 Trigger received! Kicking off Job Scout Agent..."

## 7. Verify Webhook (Optional)
```bash
curl "https://api.telegram.org/botYOUR_BOT_TOKEN/getWebhookInfo"
```

---

## Free Tier Limits (Cloudflare Workers)
- **100,000 requests/day** free
- **10 ms CPU time/request** free
- Your usage: ~4-5 requests/day → **0.005% of free tier**

---

## Troubleshooting
- **No reply?** Check Worker logs in Cloudflare Dashboard → Workers → your worker → Logs
- **403 from GitHub?** Check repository access and the relevant fine-grained
  Actions or Contents permission; do not grant access to unrelated repositories.
- **Webhook not working?** Verify Worker URL is HTTPS and accessible
