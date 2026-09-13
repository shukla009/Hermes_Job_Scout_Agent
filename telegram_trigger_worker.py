"""
Cloudflare Worker (Python) to handle Telegram /run command
and trigger GitHub Actions workflow_dispatch via API.
Deploy to: Cloudflare Workers (Free tier: 100k requests/day)
"""

import json
import os
from urllib import request, parse
from js import Response

# ============================================================
# CONFIGURATION - SET THESE IN CLOUDFLARE WORKER ENV VARS
# ============================================================
# TELEGRAM_BOT_TOKEN = "8838558466:AAEHW82-XA17OpIRsWp_p1_qUQjbwbmOuuE"
# TELEGRAM_CHAT_ID = "807588151"  # Only allow your chat ID
# GITHUB_PAT = "github_pat_XXXXXXXXXXXX"  # Fine-grained PAT with workflow scope
# GITHUB_REPO = "YOUR_GITHUB_USERNAME/hermes-job-scout"
# GITHUB_WORKFLOW_FILE = "job_scout.yml"  # or workflow_id
# ============================================================

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

def send_telegram_reply(bot_token: str, chat_id: str, text: str):
    """Send a reply message back to Telegram."""
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = json.dumps({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML"
    }).encode("utf-8")
    
    req = request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"[Telegram Reply Error] {e}")

def trigger_github_workflow(github_pat: str, repo: str, workflow_file: str) -> bool:
    """Trigger GitHub Actions workflow_dispatch via REST API."""
    url = f"https://api.github.com/repos/{repo}/actions/workflows/{workflow_file}/dispatches"
    payload = json.dumps({"ref": "main"}).encode("utf-8")
    
    headers = {
        "Authorization": f"Bearer {github_pat}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "User-Agent": "Telegram-Job-Scout-Trigger"
    }
    
    req = request.Request(url, data=payload, headers=headers, method="POST")
    try:
        response = request.urlopen(req, timeout=15)
        return response.status == 204
    except Exception as e:
        print(f"[GitHub Trigger Error] {e}")
        if hasattr(e, 'read'):
            print(e.read().decode())
        return False

async def on_fetch(request, env):
    """Cloudflare Worker entry point (Python Workers use on_fetch)."""
    
    # Only accept POST from Telegram webhook
    if request.method != "POST":
        return Response("Method not allowed", status=405)
    
    try:
        update = await request.json()
    except:
        return Response("Invalid JSON", status=400)
    
    # Get secrets from Cloudflare Worker Environment Variables
    bot_token = env.TELEGRAM_BOT_TOKEN
    allowed_chat_id = env.TELEGRAM_CHAT_ID
    github_pat = env.GITHUB_PAT
    github_repo = env.GITHUB_REPO
    workflow_file = env.GITHUB_WORKFLOW_FILE
    
    # Verify this is our /run command from our chat
    if not verify_telegram_request(update, allowed_chat_id):
        return Response("OK", status=200)  # Silent ignore
    
    message = update.get("message", {})
    chat_id = str(message.get("chat", {}).get("id"))
    text = message.get("text", "").strip().lower()
    
    # Send immediate acknowledgment
    send_telegram_reply(bot_token, chat_id, "🚀 <b>Trigger received!</b> Kicking off Job Scout Agent on GitHub Actions...")
    
    # Trigger the workflow
    success = trigger_github_workflow(github_pat, github_repo, workflow_file)
    
    if success:
        send_telegram_reply(bot_token, chat_id, "✅ <b>Workflow triggered successfully!</b>\n\nCheck GitHub Actions in ~30s. Results will arrive here when complete.")
    else:
        send_telegram_reply(bot_token, chat_id, "❌ <b>Failed to trigger workflow.</b> Check Cloudflare Worker logs.")
    
    return Response("OK", status=200)