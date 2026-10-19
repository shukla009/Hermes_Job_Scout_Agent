/** GitHub-backed Telegram queue: no database, no workflow run for feedback. */
const REPO = "shukla009/Hermes_Job_Scout_Agent";
const ACTIONS = new Set(["applied", "notfit", "snooze", "reply", "interview", "rejected"]);
const escapeHTML = value => String(value ?? "").replace(/[&<>]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));
async function stateKey(env) {
  const secret = env.SCOUT_STATE_KEY || env.TELEGRAM_BOT_TOKEN;
  if (!secret) throw new Error("State key unavailable");
  const raw = await crypto.subtle.digest("SHA-256", new TextEncoder().encode("hermes-private-state-v1\0" + secret));
  return crypto.subtle.importKey("raw", raw, {name: "AES-GCM"}, false, ["encrypt", "decrypt"]);
}
const from64 = value => Uint8Array.from(atob(value), c => c.charCodeAt(0));
const to64 = bytes => { let text = ""; for (const byte of bytes) text += String.fromCharCode(byte); return btoa(text); };
export async function decodeState(value, env, context) {
  if (value?.format !== "hermes.aesgcm.v1") return value;
  if (value.context !== context) throw new Error("Invalid state context");
  const plain = await crypto.subtle.decrypt({name:"AES-GCM", iv:from64(value.nonce), additionalData:new TextEncoder().encode(context)}, await stateKey(env), from64(value.ciphertext));
  return JSON.parse(new TextDecoder().decode(plain));
}
export async function encodeState(value, env, context) {
  const nonce = crypto.getRandomValues(new Uint8Array(12));
  const ciphertext = await crypto.subtle.encrypt({name:"AES-GCM", iv:nonce, additionalData:new TextEncoder().encode(context)}, await stateKey(env), new TextEncoder().encode(JSON.stringify(value)));
  return {format:"hermes.aesgcm.v1", context, nonce:to64(nonce), ciphertext:to64(new Uint8Array(ciphertext))};
}
async function api(env, path, options = {}, state = false) {
  return fetch(`https://api.github.com/repos/${env.GITHUB_REPO || REPO}/${path}`, {...options,
    headers: {Authorization: `Bearer ${state ? (env.GITHUB_STATE_PAT || env.GITHUB_PAT) : env.GITHUB_PAT}`, Accept: "application/vnd.github+json", "Content-Type": "application/json", "User-Agent": "Hermes-Telegram", ...options.headers}, signal: AbortSignal.timeout(12000)});
}
async function telegram(env, method, body) {
  const response = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/${method}`, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body), signal: AbortSignal.timeout(10000)});
  if (!response.ok || !(await response.json()).ok) throw new Error("Telegram operation failed");
}
async function reply(env, chat, text) { return telegram(env, "sendMessage", {chat_id: chat, text, parse_mode: "HTML", disable_web_page_preview: true}); }
async function stateFile(env) {
  const response = await api(env, "contents/state/scout.json?ref=main", {}, true);
  if (!response.ok) throw new Error(`state_read_http_${response.status}`);
  const result = await response.json();
  const bytes = Uint8Array.from(atob(result.content.replace(/\s/g, "")), c => c.charCodeAt(0));
  const value = await decodeState(JSON.parse(new TextDecoder().decode(bytes)), env, "state/scout.json");
  if (value.version !== 1 || !value.jobs || !value.sends) throw new Error("Invalid state");
  return value;
}
export async function verifyCallback(value, token, now = Math.floor(Date.now() / 1000)) {
  const match = /^(applied|notfit|snooze|reply|interview|rejected):([a-f0-9]{24}):(\d{10}):([a-f0-9]{12})$/.exec(value || "");
  if (!match || !ACTIONS.has(match[1]) || Number(match[3]) < now || Number(match[3]) > now + 7 * 86400) return null;
  const payload = match.slice(1, 4).join(":");
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(token), {name: "HMAC", hash: "SHA-256"}, false, ["sign"]);
  const signed = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(payload));
  const expected = [...new Uint8Array(signed)].map(v => v.toString(16).padStart(2, "0")).join("").slice(0, 12);
  let difference = 0;
  for (let i = 0; i < expected.length; i++) difference |= expected.charCodeAt(i) ^ match[4].charCodeAt(i);
  return difference === 0 ? {action: match[1], job_id: match[2]} : null;
}
export async function createCallback(action, jobId, token, now = Math.floor(Date.now() / 1000)) {
  if (!ACTIONS.has(action) || !/^[a-f0-9]{24}$/.test(jobId)) throw new Error("Invalid callback target");
  const payload = `${action}:${jobId}:${now + 7 * 86400}`;
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(token), {name: "HMAC", hash: "SHA-256"}, false, ["sign"]);
  const signed = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(payload));
  const signature = [...new Uint8Array(signed)].map(v => v.toString(16).padStart(2, "0")).join("").slice(0, 12);
  return `${payload}:${signature}`;
}
async function queueCard(env, job) {
  const actions = [["Applied", "applied"], ["Not suitable", "notfit"], ["Snooze", "snooze"], ["Reply", "reply"], ["Interview", "interview"], ["Rejected", "rejected"]];
  const buttons = await Promise.all(actions.map(async ([text, action]) => ({text, callback_data: await createCallback(action, job.id, env.TELEGRAM_BOT_TOKEN)})));
  const text = `${escapeHTML(job.job_title || job.title || "Relevant role")} — ${escapeHTML(job.company || "Employer")}\n${escapeHTML(job.apply_url || job.application_url || job.url || "Check listing")}\n${escapeHTML(job.fit_reason || job.reason || "Review fit and requirements before applying")}`;
  return telegram(env, "sendMessage", {chat_id: env.TELEGRAM_CHAT_ID, text, parse_mode: "HTML", disable_web_page_preview: true,
    reply_markup: {inline_keyboard: [buttons.slice(0,3), buttons.slice(3)]}});
}
export async function storeFeedback(env, updateId, event) {
  if (!Number.isSafeInteger(updateId) || updateId < 0) throw new Error("Invalid update ID");
  const path = `contents/state/feedback/${updateId}.json`;
  const bytes = new TextEncoder().encode(JSON.stringify(await encodeState({...event, update_id: updateId}, env, `state/feedback/${updateId}.json`)));
  const content = btoa(String.fromCharCode(...bytes));
  const response = await api(env, path, {method: "PUT", body: JSON.stringify({message: `Record Telegram outcome ${updateId}`, content, branch: "main"})}, true);
  if (response.ok) return;
  if (response.status === 409 || response.status === 422) {
    const existing = await api(env, `${path}?ref=main`, {}, true);
    if (existing.ok) {
      const stored = await decodeState(JSON.parse(new TextDecoder().decode(from64((await existing.json()).content.replace(/\s/g, "")))), env, `state/feedback/${updateId}.json`);
      if (stored.update_id === updateId && stored.job_id === event.job_id && stored.action === event.action && stored.sender_id === event.sender_id) return;
    }
  }
  throw new Error("Feedback was not stored");
}
export async function reserveDispatch(env, updateId, senderId) {
  if (!Number.isSafeInteger(updateId) || updateId < 0) throw new Error("Invalid update ID");
  const path = `contents/state/commands/${updateId}.json`;
  const event = {update_id: updateId, action: "run", sender_id: String(senderId), status: "reserved", created_at: new Date().toISOString()};
  const content = btoa(JSON.stringify(event));
  // Create-only intent: existing intent means possibly accepted dispatch, never retry.
  const response = await api(env, path, {method: "PUT", body: JSON.stringify({message: `Reserve Telegram run ${updateId}`, content, branch: "main"})}, true);
  if (response.ok) return true;
  if (response.status === 409 || response.status === 422) {
    const existing = await api(env, `${path}?ref=main`, {}, true);
    if (existing.ok) {
      const stored = JSON.parse(atob((await existing.json()).content.replace(/\s/g, "")));
      if (stored.update_id === updateId && stored.action === "run" && stored.sender_id === String(senderId)) return false;
    }
  }
  throw new Error("Dispatch intent was not safely stored");
}
export default {
  async fetch(request, env) {
    if (!env.TELEGRAM_WEBHOOK_SECRET || request.headers.get("X-Telegram-Bot-Api-Secret-Token") !== env.TELEGRAM_WEBHOOK_SECRET) return new Response("Forbidden", {status: 403});
    if (request.method !== "POST") return new Response("Method not allowed", {status: 405});
    let update;
    try { update = await request.json(); } catch { return new Response("Invalid JSON", {status: 400}); }
    if (!env.TELEGRAM_BOT_TOKEN || !env.TELEGRAM_CHAT_ID || !env.GITHUB_PAT) return new Response("Missing configuration", {status: 500});
    const callback = update.callback_query;
    const message = callback?.message || update.message;
    const sender = callback?.from || update.message?.from;
    const allowedSender = String(env.TELEGRAM_USER_ID || env.TELEGRAM_CHAT_ID);
    if (!message || String(message.chat?.id) !== String(env.TELEGRAM_CHAT_ID) || String(sender?.id) !== allowedSender) return new Response("OK");
    try {
      if (callback) {
        const event = await verifyCallback(callback.data, env.TELEGRAM_BOT_TOKEN);
        const state = await stateFile(env);
        if (!event || !state.jobs[event.job_id]) await telegram(env, "answerCallbackQuery", {callback_query_id: callback.id, text: "Expired or invalid action. Use /queue."});
        else {
          await storeFeedback(env, update.update_id, {...event, created_at: new Date().toISOString(), sender_id: allowedSender});
          await telegram(env, "answerCallbackQuery", {callback_query_id: callback.id, text: "Saved. Scout will apply this outcome on its next run."});
        }
      } else {
        const command = String(message.text || "").trim().split(/\s/)[0].split("@")[0].toLowerCase();
        if (!["/run", "/queue", "/status", "/stats"].includes(command)) return new Response("OK");
        const state = await stateFile(env);
        const jobs = Object.values(state.jobs);
        if (command === "/run") {
          if (state.budget?.paused) await reply(env, env.TELEGRAM_CHAT_ID, "Scout paused by its zero-cost budget guard. No run was started.");
          else {
            if (await reserveDispatch(env, update.update_id, allowedSender)) {
              let result;
              try { result = await api(env, `actions/workflows/${encodeURIComponent(env.GITHUB_WORKFLOW_FILE || "job_scout.yml")}/dispatches`, {method: "POST", body: JSON.stringify({ref: "main"})}); }
              catch {
                await reply(env, env.TELEGRAM_CHAT_ID, "Run acceptance is uncertain. This request will not be repeated automatically. Check GitHub Actions before sending a new /run.");
                return new Response("OK");
              }
              await reply(env, env.TELEGRAM_CHAT_ID, result.status === 204 ? "Scout started. Results will arrive here when complete." : `Unable to start scout (GitHub HTTP ${result.status}). Check Worker token and workflow settings before sending a new /run.`);
            }
          }
        } else if (command === "/queue") {
          if (env.HANDS_OFF_ONLY !== "false") {
            const run = state.last_run || {};
            const forms = run.applications || {};
            await reply(env, env.TELEGRAM_CHAT_ID, `Hands-off application processing\nSMTP accepted in last run: ${run.emails_accepted_by_smtp || 0}\nConfirmed portal applications: ${forms.confirmed || 0}\nVerified form adapters enabled: ${forms.enabled_adapters || 0}\nUnsupported/blocked routes: ${(forms.unsupported || 0) + (forms.blocked || 0)}\n${escapeHTML(Object.keys(run.coverage_gaps || {}).slice(0,3).join(", ") || "No pending action required")}\nNo manual approval queue. Replies and interviews will be reported here.`);
            const recent = jobs.filter(j => (j.emailed || j.submitted) && /^[a-f0-9]{24}$/.test(j.id)).sort((a,b) => String(b.applied_at || b.emailed_at || "").localeCompare(String(a.applied_at || a.emailed_at || ""))).slice(0,3);
            for (const job of recent) {
              const actions = [["Reply", "reply"], ["Interview", "interview"], ["Rejected", "rejected"]];
              const buttons = await Promise.all(actions.map(async ([text, action]) => ({text, callback_data: await createCallback(action, job.id, env.TELEGRAM_BOT_TOKEN)})));
              await telegram(env, "sendMessage", {chat_id:env.TELEGRAM_CHAT_ID, parse_mode:"HTML", text:`Record an outcome: ${escapeHTML(job.job_title || job.title || "Application")} — ${escapeHTML(job.company || "Employer")}`, reply_markup:{inline_keyboard:[buttons]}});
            }
            return new Response("OK");
          }
          const cutoff = new Date(Date.now() - 21 * 86400000).toISOString();
          const pending = jobs.filter(j => ["core","stretch"].includes(j.tier) && String(j.last_seen || "") >= cutoff && !j.archived && !j.submitted && !j.outcome && (!j.snoozed_until || j.snoozed_until < new Date().toISOString())).sort((a,b) => (b.fit_score || b.score || 0) - (a.fit_score || a.score || 0)).slice(0,5);
          if (!pending.length) await reply(env, env.TELEGRAM_CHAT_ID, "No pending recommendations yet.");
          else for (const job of pending) await queueCard(env, job);
        } else if (command === "/status") {
          const run = state.last_run || {};
          await reply(env, env.TELEGRAM_CHAT_ID, `Last run: ${escapeHTML(run.status || "No completed run")}\nAt: ${escapeHTML(run.at || "Unknown")}\nDiscovered: ${run.results_discovered || 0}\nQualified: ${run.relevant_apply_leads || 0}\nSMTP accepted: ${run.emails_accepted_by_smtp || 0}\nPortal confirmed: ${run.applications?.confirmed || 0}\nVerified form adapters: ${run.applications?.enabled_adapters || 0}\nBudget: ${state.budget?.paused ? "Paused" : state.budget?.mode === "standard_public" ? "Free public standard runners" : "Within configured limits"}\nRuntime minutes estimate: ${state.budget?.used_minutes_estimate || 0}${state.budget?.mode === "standard_public" ? " (no private-minute ceiling)" : "/" + (state.budget?.monthly_ceiling || 1200)}\nFeedback is reconciled on the next scout run.`);
        } else {
          const cutoff = new Date(Date.now() - 7 * 86400000).toISOString();
          const count = key => jobs.filter(j => String(j[key] || "") >= cutoff).length;
          await reply(env, env.TELEGRAM_CHAT_ID, `Last 7 days\nDiscovered: ${count("first_seen")}\nAlerted: ${count("alerted_at")}\nConfirmed applied: ${count("applied_at")}\nEmailed: ${count("emailed_at")}\nReplies: ${jobs.filter(j => j.outcome === "reply" && String(j.outcome_at || "") >= cutoff).length}\nInterviews: ${jobs.filter(j => j.outcome === "interview" && String(j.outcome_at || "") >= cutoff).length}\nFeedback is reconciled on the next scout run.`);
        }
      }
      return new Response("OK");
    } catch (error) {
      const reason = /^state_read_http_\d{3}$/.test(error?.message || "") ? error.message : "Temporary service failure";
      return new Response(reason, {status: 503});
    }
  }
};
