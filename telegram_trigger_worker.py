"""
Cloudflare Worker (Python) to handle Telegram /run command
and trigger GitHub Actions workflow_dispatch via API.

IMPORTANT: Cloudflare Python Workers have NO raw TCP sockets — urllib/socket
cannot work. All outbound HTTP MUST go through the JS fetch API via the FFI.
Deploy with the python_workers compatibility flag (pywrangler/wrangler), or
prefer telegram_trigger_worker.js which runs anywhere Workers run.
"""

import json
from js import Response, Headers, Object
from pyodide.ffi import to_js

# ============================================================
# CONFIGURATION - SET THESE IN CLOUDFLARE WORKER ENV VARS
# ============================================================
# TELEGRAM_BOT_TOKEN      - from @BotFather (secret)
# TELEGRAM_CHAT_ID        - your chat ID (secret)
# TELEGRAM_WEBHOOK_SECRET - random hex string, also passed as secret_token
#                           in setWebhook (secret)
# GITHUB_PAT              - fine-grained PAT with workflow scope (secret)
# GITHUB_REPO             - shukla009/Hermes_Job_Scout_Agent
# GITHUB_WORKFLOW_FILE    - job_scout.yml
# ============================================================


def _js_headers(pairs):
    return Headers.new(to_js(pairs, dict_converter=Object.fromEntries))


async def _fetch_json(url, method="GET", headers=None, body=None):
    """fetch() via the JS FFI — the only outbound HTTP available in Python Workers."""
    kwargs = {"method": method}
    if headers:
        kwargs["headers"] = headers
    if body is not None:
        kwargs["body"] = body
    resp = await fetch(url, to_js(kwargs, dict_converter=Object.fromEntries))
    text = await resp.text()
    return resp.status, text


def verify_webhook_secret(request, env):
    """Authenticity check: Telegram signs webhook calls with
    X-Telegram-Bot-Api-Secret-Token (set via secret_token in setWebhook).
    chat_id alone is attacker-controlled in the POST body and is NOT auth."""
    expected = getattr(env, "TELEGRAM_WEBHOOK_SECRET", None)
    if not expected:
        return False
    got = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    return got == expected


def verify_telegram_request(update: dict, allowed_chat_id: str) -> bool:
    """Verify the message is from the allowed chat and has /run command."""
    message = update.get("message", {})
    chat_id = str(message.get("chat", {}).get("id"))
    text = message.get("text", "").strip().lower()
    if chat_id != allowed_chat_id:
        return False
    if not text.startswith("/run"):
        return False
    return True


async def send_telegram_reply(bot_token: str, chat_id: str, text: str):
    """Send a reply message back to Telegram (via JS fetch)."""
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = json.dumps({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
    })
    headers = _js_headers([["Content-Type", "application/json"]])
    try:
        status, _ = await _fetch_json(url, method="POST", headers=headers, body=payload)
        if status != 200:
            print(f"[Telegram Reply Error] HTTP {status}")
    except Exception as e:
        print(f"[Telegram Reply Error] {e}")


def _failure_message(status: int, body: str) -> str:
    """Map GitHub API failures to specific, actionable Telegram replies."""
    if status == 401:
        return ("❌ <b>GitHub auth failed (401)</b>\n"
                "GITHUB_PAT is invalid/expired or lacks the <code>workflow</code> scope. "
                "Regenerate the PAT with workflow scope and update the Worker secret.")
    if status == 404:
        return ("❌ <b>Not found (404)</b>\n"
                "Check GITHUB_REPO (must be shukla009/Hermes_Job_Scout_Agent) and "
                "GITHUB_WORKFLOW_FILE (job_scout.yml).")
    if status == 422:
        return ("❌ <b>Cannot dispatch (422)</b>\n"
                "job_scout.yml must declare <code>on: workflow_dispatch</code> and the "
                "ref (main) must exist. Check the workflow file.")
    snippet = body[:180] if body else "(no body)"
    return f"❌ <b>GitHub API error (HTTP {status})</b>\n<code>{snippet}</code>"


async def trigger_github_workflow(github_pat: str, repo: str, workflow_file: str):
    """Trigger GitHub Actions workflow_dispatch via REST API (via JS fetch).
    Returns (ok: bool, status: int, body: str)."""
    url = f"https://api.github.com/repos/{repo}/actions/workflows/{workflow_file}/dispatches"
    payload = json.dumps({"ref": "main"})
    headers = _js_headers([
        ["Authorization", f"Bearer {github_pat}"],
        ["Accept", "application/vnd.github+json"],
        ["Content-Type", "application/json"],
        ["User-Agent", "Telegram-Job-Scout-Trigger"],
    ])
    try:
        status, body = await _fetch_json(url, method="POST", headers=headers, body=payload)
        return status == 204, status, body
    except Exception as e:
        print(f"[GitHub Trigger Error] {e}")
        return False, 0, str(e)


async def on_fetch(request, env):
    """Cloudflare Worker entry point (Python Workers use on_fetch)."""

    # Webhook authenticity FIRST — reject forgeries before touching the body
    if not verify_webhook_secret(request, env):
        return Response.new("Forbidden", status=403)

    # Only accept POST from Telegram webhook
    if request.method != "POST":
        return Response.new("Method not allowed", status=405)

    try:
        update = await request.json()
    except Exception:
        return Response.new("Invalid JSON", status=400)

    # Get secrets from Cloudflare Worker Environment Variables
    bot_token = env.TELEGRAM_BOT_TOKEN
    allowed_chat_id = env.TELEGRAM_CHAT_ID
    github_pat = env.GITHUB_PAT
    github_repo = env.GITHUB_REPO or "shukla009/Hermes_Job_Scout_Agent"
    workflow_file = env.GITHUB_WORKFLOW_FILE or "job_scout.yml"

    if not all([bot_token, allowed_chat_id, github_pat]):
        return Response.new("Missing Worker secrets", status=500)

    # Verify this is our /run command from our chat
    if not verify_telegram_request(update, allowed_chat_id):
        return Response.new("OK", status=200)  # Silent ignore

    message = update.get("message", {})
    chat_id = str(message.get("chat", {}).get("id"))

    # Send immediate acknowledgment
    await send_telegram_reply(bot_token, chat_id, "🚀 <b>Trigger received!</b> Kicking off Job Scout Agent on GitHub Actions...")

    # Trigger the workflow
    ok, status, body = await trigger_github_workflow(github_pat, github_repo, workflow_file)

    if ok:
        await send_telegram_reply(bot_token, chat_id, "✅ <b>Workflow triggered successfully!</b>\n\nCheck GitHub Actions in ~30s. Results will arrive here when complete.")
    else:
        await send_telegram_reply(bot_token, chat_id, _failure_message(status, body))

    return Response.new("OK", status=200)
