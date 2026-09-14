# Manual Steps — Step-by-Step Instructions

Complete these 6 steps in order. Steps 1-2 are security-critical (token rotation + history purge). Steps 3-4 restore API access. Step 5 secures the Telegram trigger. Step 6 is verification.

---

## STEP 1 — Rotate the Telegram Bot Token (5 min)

The old token is in your git history — anyone with repo access could control your bot.

1. Open Telegram → search **@BotFather** → open the chat
2. Type `/mybots` → select **@ShivankCareerAgent_bot**
3. Tap **API Token** → tap **Revoke current token** → confirm
4. BotFather shows a **new token** (format: `123456789:AAE...`) — copy it
5. Now update it in BOTH places:

**A. GitHub Secrets:**
- Go to https://github.com/shukla009/Hermes_Job_Scout_Agent/settings/secrets/actions
- Click **TELEGRAM_BOT_TOKEN** → **Update** → paste the new token → **Update secret**

**B. Cloudflare Worker:**
- Go to https://dash.cloudflare.com → **Workers & Pages** → **telegram-job-scout-trigger** → **Settings** → **Variables and Secrets**
- Find **TELEGRAM_BOT_TOKEN** → **Edit** → paste the new token → mark as **Secret** → **Save**

---

## STEP 2 — Purge the Token from Git History (10 min)

Scrubbing HEAD (already done) isn't enough — the token lives in 33 old commits. `git filter-repo` is installed on your machine already.

Run these commands one at a time in your terminal:

```bash
# 1. Create the replacement rules file (do NOT commit this)
cd C:/Users/shukl/Downloads/Job_Scout_Agent
echo '8838558466:===>8838558466:REDACTED' > ../token_replacements.txt
# (Replace the middle section with the FULL OLD token characters after the colon
#  if you still have .env around — open .env, copy the token, paste it as:
#  OLD_TOKEN_FULL===>REDACTED )

# 2. Make a safety backup of the repo first
cd C:/Users/shukl/Downloads
cp -r Job_Scout_Agent Job_Scout_Agent_backup_before_purge

# 3. Run the history rewrite (removes the token from ALL commits)
cd C:/Users/shukl/Downloads/Job_Scout_Agent
git filter-repo --replace-text ../token_replacements.txt --force

# 4. Re-add the remote (filter-repo removes it as a safety measure)
git remote add origin https://github.com/shukla009/Hermes_Job_Scout_Agent.git

# 5. Force-push the rewritten history
git push origin main --force

# 6. Verify the token is gone from history (should print nothing)
git log --all -p | grep -c "8838558466:AAE" || echo "CLEAN"
```

After this completes, also invalidate GitHub's cached views:
- Go to https://support.github.com/request → **Remove cached views** (repository: Hermes_Job_Scout_Agent, paths: DEPLOY_TELEGRAM_TRIGGER.md, telegram_trigger_worker.py)

---

## STEP 3 — Fix the SerpAPI Quota (5 min)

Confirmed exhausted: "Your account has run out of searches." No searches are possible until fixed.

**Option A — Wait for reset (free tier resets on the 1st of each month)**
- Nothing to do; the next run after the 1st will work

**Option B — New account/key:**
1. Go to https://serpapi.com → sign up with a new account (free 100 searches/month)
2. Dashboard → **API Key** → copy it
3. GitHub → https://github.com/shukla009/Hermes_Job_Scout_Agent/settings/secrets/actions
4. Click **SERPAPI_KEY** → **Update** → paste → **Update secret**
5. Optionally also update your local `.env` file (SERPAPI_KEY=...)

---

## STEP 4 — Fix the NVIDIA API Key (5 min)

The 401 error means the key is invalid/expired.

1. Go to https://build.nvidia.com → sign in
2. Click your profile icon → **Setup** (or https://org.ngc.nvidia.com)
3. **Personal Keys** → **Generate Personal Key**
4. Enable at minimum: **API Catalog → Invoke** (the "Generate" scope)
5. Copy the key (starts with `nvapi-`)
6. GitHub → https://github.com/shukla009/Hermes_Job_Scout_Agent/settings/secrets/actions
7. Click **NVIDIA_API_KEY** → **Update** → paste → **Update secret**
8. Quick test that the key + model work (paste in your terminal):
```bash
curl -s https://integrate.api.nvidia.com/v1/chat/completions \
  -H "Authorization: Bearer nvapi-YOUR_NEW_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model": "meta/llama-3.1-8b-instruct", "messages": [{"role": "user", "content": "ping"}], "max_tokens": 5}'
```
Expected: a JSON response with `"choices"`. If you get 401, the key/scopes are wrong.

---

## STEP 5 — Redeploy the Cloudflare Worker + Secure the Webhook (10 min)

The old worker code is dead (urllib) and the webhook accepts forgeries.

1. **Generate a webhook secret** (random string):
```bash
openssl rand -hex 32
```
Copy the output — this is your `TELEGRAM_WEBHOOK_SECRET`.

2. **Add it to the Worker:**
- Cloudflare Dashboard → **Workers & Pages** → **telegram-job-scout-trigger** → **Settings** → **Variables and Secrets** → **Add variable**
- Name: `TELEGRAM_WEBHOOK_SECRET` → Value: (paste) → mark **Secret** → Save

3. **Replace the worker code with the fixed version:**
- Open `C:/Users/shukl/Downloads/Job_Scout_Agent/telegram_trigger_worker.js` in a text editor
- Select all → copy
- Cloudflare → your Worker → **Edit code** → delete everything → paste → **Deploy**
- (Use the `.js` file — the `.py` variant requires extra Cloudflare flags; the `.js` runs anywhere)

4. **Verify the other Worker secrets exist:**
- Settings → Variables and Secrets → confirm these are set: `TELEGRAM_BOT_TOKEN` (new one from Step 1), `TELEGRAM_CHAT_ID` (`807588151`), `GITHUB_PAT`, `GITHUB_REPO` (`shukla009/Hermes_Job_Scout_Agent`), `GITHUB_WORKFLOW_FILE` (`job_scout.yml`)
- If `GITHUB_PAT` is missing/old: GitHub → Settings → Developer settings → Personal access tokens → Fine-grained → generate one with **Actions: Read & write** permission on the repo → paste into the Worker

5. **Re-register the webhook WITH the secret token** (critical — replaces the old webhook):
```bash
curl -X POST "https://api.telegram.org/botYOUR_NEW_BOT_TOKEN/setWebhook" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://telegram-job-scout-trigger.YOUR_SUBDOMAIN.workers.dev", "secret_token": "YOUR_WEBHOOK_SECRET", "allowed_updates": ["message"]}'
```
(Replace `YOUR_NEW_BOT_TOKEN`, `YOUR_SUBDOMAIN`, `YOUR_WEBHOOK_SECRET`.)
Expected response: `{"ok":true,"result":true,"description":"Webhook was set"}`

6. **Verify the webhook:**
```bash
curl "https://api.telegram.org/botYOUR_NEW_BOT_TOKEN/getWebhookInfo"
```
Expected: `"last_error_message"` absent (or empty), `"url"` set to your worker URL.

7. **Test the trigger:**
- Telegram → message **@ShivankCareerAgent_bot** → send `/run`
- Expected: "🚀 Trigger received!" → "✅ Workflow triggered successfully!"
- (The workflow itself will still fail at the SerpAPI step until Step 3 is done — that's expected.)

---

## STEP 6 — End-to-End Verification (5 min)

Once Steps 1-5 are done:

1. **Trigger a run**: Telegram → `/run`
2. **Watch it live**: https://github.com/shukla009/Hermes_Job_Scout_Agent/actions — click the running workflow
3. **Expected sequence in the logs:**
   - `Run Tests (mocked, no network)` → 40 passed
   - `Run Job Scout Agent` → `=== Job Scout Agent Starting ===`
   - `[Search] site:linkedin.com/posts ...`
   - `[Search] N candidate posts after filtering`
   - Per-post analysis with `[Freshness] rejected (...)` lines for anything older than 4 days
   - `✅ MATCH: ... | ...` for fresh leads with visible emails
   - `[Email Success] Sent to ...`
   - `=== Done ===`
4. **Expected on Telegram:**
   - "🤖 Job Scout Started" (with model name)
   - "🔍 Search complete: N posts to analyse"
   - "🚀 Email sent" per email
   - "✅ Run Complete" summary with counters
5. **If the summary shows 0 posts**: normal on quiet days — the 4-day gate is strict by design; wait for the next run or widen the window by adding `MAX_POST_AGE_DAYS: "7"` to the workflow env block (GitHub → edit `.github/workflows/job_scout.yml`)

---

## Quick Reference — Where Everything Lives

| Item | Location |
|---|---|
| GitHub Secrets | github.com/shukla009/Hermes_Job_Scout_Agent → Settings → Secrets and variables → Actions |
| Cloudflare Worker | dash.cloudflare.com → Workers & Pages → telegram-job-scout-trigger |
| BotFather | Telegram → @BotFather → /mybots → @ShivankCareerAgent_bot |
| SerpAPI dashboard | serpapi.com/dashboard |
| NVIDIA keys | org.ngc.nvidia.com → Personal Keys |
| Workflow file | .github/workflows/job_scout.yml (repo root) |
| New webhook command | Step 5.5 above |
