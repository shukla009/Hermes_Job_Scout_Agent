/**
 * Cloudflare Worker (JavaScript) to handle Telegram /run command
 * and trigger GitHub Actions workflow_dispatch via API.
 * Deploy to: Cloudflare Workers (Free tier: 100k requests/day)
 *
 * SECURITY: Telegram signs webhook calls with the X-Telegram-Bot-Api-Secret-Token
 * header (set via secret_token in setWebhook). This header is validated on every
 * request BEFORE the body is parsed — chat_id alone is attacker-controlled in the
 * POST body and is NOT an authenticity check.
 */

const GITHUB_REPO_FALLBACK = "shukla009/Hermes_Job_Scout_Agent";
const WORKFLOW_FILE_FALLBACK = "job_scout.yml";

function failureMessage(status, body) {
  // Map GitHub API failures to specific, actionable Telegram replies.
  if (status === 401) {
    return "❌ <b>GitHub auth failed (401)</b>\nGITHUB_PAT is invalid/expired or lacks the <code>workflow</code> scope. Regenerate the PAT with workflow scope and update the Worker secret.";
  }
  if (status === 404) {
    return "❌ <b>Not found (404)</b>\nCheck GITHUB_REPO (must be " + GITHUB_REPO_FALLBACK + ") and GITHUB_WORKFLOW_FILE (" + WORKFLOW_FILE_FALLBACK + ").";
  }
  if (status === 422) {
    return "❌ <b>Cannot dispatch (422)</b>\njob_scout.yml must declare <code>on: workflow_dispatch</code> and the ref (main) must exist. Check the workflow file.";
  }
  const snippet = body ? body.slice(0, 180) : "(no body)";
  return "❌ <b>GitHub API error (HTTP " + status + ")</b>\n<code>" + snippet + "</code>";
}

export default {
  async fetch(request, env, ctx) {
    // Webhook authenticity FIRST — reject forgeries before touching the body
    const expectedSecret = env.TELEGRAM_WEBHOOK_SECRET;
    if (!expectedSecret || request.headers.get("X-Telegram-Bot-Api-Secret-Token") !== expectedSecret) {
      return new Response("Forbidden", { status: 403 });
    }

    // Only accept POST from Telegram webhook
    if (request.method !== "POST") {
      return new Response("Method not allowed", { status: 405 });
    }

    let update;
    try {
      update = await request.json();
    } catch {
      return new Response("Invalid JSON", { status: 400 });
    }

    // Get secrets from Cloudflare Worker Environment Variables
    const botToken = env.TELEGRAM_BOT_TOKEN;
    const allowedChatId = env.TELEGRAM_CHAT_ID;
    const githubPat = env.GITHUB_PAT;
    const githubRepo = env.GITHUB_REPO || GITHUB_REPO_FALLBACK;
    const workflowFile = env.GITHUB_WORKFLOW_FILE || WORKFLOW_FILE_FALLBACK;

    if (!botToken || !allowedChatId || !githubPat) {
      return new Response("Missing Worker secrets", { status: 500 });
    }

    // Verify this is our /run command from our chat
    const message = update.message;
    if (!message) {
      return new Response("OK", { status: 200 }); // Silent ignore non-message updates
    }

    const chatId = String(message.chat?.id);
    const text = (message.text || "").trim().toLowerCase();

    if (chatId !== allowedChatId || !text.startsWith("/run")) {
      return new Response("OK", { status: 200 }); // Silent ignore
    }

    // Send immediate acknowledgment
    await sendTelegramReply(botToken, chatId, "🚀 <b>Trigger received!</b> Kicking off Job Scout Agent on GitHub Actions...");

    // Trigger the workflow
    const { ok, status, body } = await triggerGitHubWorkflow(githubPat, githubRepo, workflowFile);

    if (ok) {
      await sendTelegramReply(botToken, chatId, "✅ <b>Workflow triggered successfully!</b>\n\nCheck GitHub Actions in ~30s. Results will arrive here when complete.");
    } else {
      await sendTelegramReply(botToken, chatId, failureMessage(status, body));
    }

    return new Response("OK", { status: 200 });
  }
};

async function sendTelegramReply(botToken, chatId, text) {
  const url = `https://api.telegram.org/bot${botToken}/sendMessage`;
  const payload = {
    chat_id: chatId,
    text: text,
    parse_mode: "HTML"
  };

  try {
    const resp = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal: AbortSignal.timeout(10000)
    });
    if (resp.status !== 200) {
      console.error("[Telegram Reply Error] HTTP", resp.status);
    }
  } catch (e) {
    console.error("[Telegram Reply Error]", e);
  }
}

async function triggerGitHubWorkflow(githubPat, repo, workflowFile) {
  const url = `https://api.github.com/repos/${repo}/actions/workflows/${workflowFile}/dispatches`;
  const payload = { ref: "main" };

  try {
    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${githubPat}`,
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "User-Agent": "Telegram-Job-Scout-Trigger"
      },
      body: JSON.stringify(payload),
      signal: AbortSignal.timeout(15000)
    });
    const body = response.status === 204 ? "" : await response.text().catch(() => "");
    return { ok: response.status === 204, status: response.status, body };
  } catch (e) {
    console.error("[GitHub Trigger Error]", e);
    return { ok: false, status: 0, body: String(e) };
  }
}
