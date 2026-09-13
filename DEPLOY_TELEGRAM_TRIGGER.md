# Deploying Telegram /run Trigger to Cloudflare Workers (Free)

## 1. Prerequisites
- Cloudflare account (free at dash.cloudflare.com)
- GitHub PAT with `workflow` scope (fine-grained token)

## 2. Create Cloudflare Worker
1. Go to **Cloudflare Dashboard** → **Workers & Pages** → **Create application** → **Create Worker**
2. Name it: `telegram-job-scout-trigger`
3. Select **JavaScript** as the language (or "Start from scratch" and paste code)
4. Delete default code and paste content from `telegram_trigger_worker.js`
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
Click **Save and Deploy**. Note your Worker URL:
`https://telegram-job-scout-trigger.YOUR_SUBDOMAIN.workers.dev`

## 5. Set Telegram Webhook
Run this ONE command in your terminal (replace YOUR_WORKER_URL):

```bash
curl -X POST "https://api.telegram.org/botYOUR_BOT_TOKEN/setWebhook" \
     -H "Content-Type: application/json" \
     -d '{"url": "https://telegram-job-scout-trigger.YOUR_SUBDOMAIN.workers.dev", "secret_token": "YOUR_WEBHOOK_SECRET", "allowed_updates": ["message"]}'
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
- **403 from GitHub?** Ensure PAT has `workflow` scope and repo access
- **Webhook not working?** Verify Worker URL is HTTPS and accessible