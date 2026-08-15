/**
 * Cloudflare Worker (JavaScript) to handle Telegram /run command
 * and trigger GitHub Actions workflow_dispatch via API.
 * Deploy to: Cloudflare Workers (Free tier: 100k requests/day)
 */

export default {
  async fetch(request, env, ctx) {
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
    const githubRepo = env.GITHUB_REPO;
    const workflowFile = env.GITHUB_WORKFLOW_FILE;

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
    const success = await triggerGitHubWorkflow(githubPat, githubRepo, workflowFile);

    if (success) {
      await sendTelegramReply(botToken, chatId, "✅ <b>Workflow triggered successfully!</b>\n\nCheck GitHub Actions in ~30s. Results will arrive here when complete.");
    } else {
      await sendTelegramReply(botToken, chatId, "❌ <b>Failed to trigger workflow.</b> Check Cloudflare Worker logs.");
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
    await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
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
      body: JSON.stringify(payload)
    });
    return response.status === 204;
  } catch (e) {
    console.error("[GitHub Trigger Error]", e);
    return false;
  }
}