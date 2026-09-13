import os
import re
import html
import json
import time
import smtplib
import urllib.request
import urllib.parse
import sys  # Added for sys.exit
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from dotenv import load_dotenv
import requests
from openai import OpenAI
from datetime import datetime, timedelta, timezone


# ----------------------------- Config -----------------------------------
load_dotenv()

NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY")
GMAIL_SENDER = os.getenv("GMAIL_SENDER_EMAIL")
GMAIL_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
SERPAPI_KEY = os.getenv("SERPAPI_KEY")
CANDIDATE_NAME = os.getenv("CANDIDATE_NAME", "Shivank Shukla")
CANDIDATE_PORTFOLIO = os.getenv("CANDIDATE_PORTFOLIO", "https://shivank-shukla-portfolio.vercel.app/")
CANDIDATE_LINKEDIN = os.getenv("CANDIDATE_LINKEDIN", "https://www.linkedin.com/in/shivank-shukla09")

HISTORY_FILE = "applied_history.json"
RESUME_PATH = "Shivank_Shukla_Data_Analyst.pdf"
NVIDIA_MODEL = os.getenv("NVIDIA_MODEL", "meta/llama-3.1-8b-instruct")  # tested/working model; override via env
MAX_POST_AGE_DAYS = int(os.getenv("MAX_POST_AGE_DAYS", "4"))  # freshness gate: only email leads posted within this window

client = None
if NVIDIA_API_KEY:
    client = OpenAI(base_url="https://integrate.api.nvidia.com/v1", api_key=NVIDIA_API_KEY)
# -------------------------------------------------------------------------


# ----------------------------- Helpers ------------------------------------
def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[History Warning] applied_history.json unreadable ({e}) - starting with empty history")
            return []
    return []


def save_history(history):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2)


def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[Telegram] Missing token or chat ID.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = json.dumps({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=15)
        print("[Telegram] Alert sent successfully.")
    except Exception as e:
        print(f"[Telegram Error] {e}")


def send_email(to_email, subject, body_text):
    if not GMAIL_SENDER or not GMAIL_PASSWORD:
        print("[Email Error] Missing Gmail credentials.")
        return False

    # Sanitize LLM-provided subject: collapse whitespace/newlines (CRLF could break the header)
    subject = " ".join(str(subject).split())
    msg = MIMEMultipart()
    msg["From"] = f"{CANDIDATE_NAME} <{GMAIL_SENDER}>"
    msg["To"] = to_email
    msg["Subject"] = subject
    # Read-receipt request (best-effort; most servers ignore it)
    msg.add_header('Disposition-Notification-To', GMAIL_SENDER)
    msg.attach(MIMEText(body_text, "plain"))

    if os.path.exists(RESUME_PATH):
        try:
            with open(RESUME_PATH, "rb") as f:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(f.read())
                encoders.encode_base64(part)
                part.add_header("Content-Disposition",
                                f'attachment; filename="{RESUME_PATH}"')
                msg.attach(part)
                print(f"[Email] Attached {RESUME_PATH}")
        except Exception as e:
            print(f"[Email Warning] Could not attach resume: {e}")
    else:
        print("[Email Warning] Resume PDF not found.")

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
            server.login(GMAIL_SENDER, GMAIL_PASSWORD.replace(" ", ""))
            server.send_message(msg)
        print(f"[Email Success] Sent to {to_email}")
        return True
    except Exception as e:
        print(f"[Email Error] Failed to send to {to_email}: {e}")
        return False


def extract_contacts(text):
    """Extract email, LinkedIn profile URLs, and phone numbers from text.
    Returns a dict with keys: 'emails' (list), 'linkedin' (list), 'phones' (list).
    """
    if not text:
        return {"emails": [], "linkedin": [], "phones": []}
    # Email regex (same as before)
    email_pat = r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"
    emails = list(dict.fromkeys(re.findall(email_pat, text)))
    # LinkedIn profile URLs (simple pattern)
    linkedin_pat = r"linkedin\.com/in/[\w-]+"
    linkedin = list(dict.fromkeys(re.findall(linkedin_pat, text)))
    # Phone numbers (India/international simple pattern)
    phone_pat = r"(\+\d{1,3}[\s-]?\d{10})"
    phones = list(dict.fromkeys(re.findall(phone_pat, text)))
    return {"emails": emails, "linkedin": linkedin, "phones": phones}


def _valid_recruiter_email(email):
    """Reject placeholder/invalid emails before sending. Never invent-send."""
    if not email or "@" not in email:
        return False
    parts = email.split("@")
    if len(parts) != 2 or not parts[0]:
        return False
    domain = parts[1].lower()
    placeholder_domains = {
        "company.com", "example.com", "email.com", "yourcompany.com",
        "placeholder.com", "sample.com", "acme.com",
        "xyz.com", "abc.com", "mail.com", "none.com",
    }
    # Exact-domain match: substring matching wrongly rejected gmail.com,
    # hotmail.com, googlemail.com, rediffmail.com ('mail.com' is a substring).
    if domain in placeholder_domains:
        print(f"  -> rejected: placeholder email domain ({domain})")
        return False
    # Reject bare TLD-less domains (e.g. name@gmail, name@outlook) — must have a dot
    if "." not in domain:
        print(f"  -> rejected: malformed email domain ({domain})")
        return False
    return True


def _is_job_board_url(url):
    blocked = [
        "indeed.", "naukri.", "linkedin.com/jobs", "glassdoor.", "shine.",
        "monster.", "timesjobs.", "placementindia.", "jobhai.", "freshersworld.",
        "cutshort.", "instahyre.", "wellfound.", "internshala.", "upwork.",
        "freelancer.", "remoteok.", "weworkremotely.", "flexjobs.",
        "remoterocketship.", "jobsearch.", "careerbuilder.", "dice.",
        "simplyhired.", "ziprecruiter.", "jobvite.", "lever.co", "greenhouse.",
        "smartrecruiters.", "workday.", "bamboohr.", "icims.", "taleo.",
        "myamcat.", "michaelpage.", "roberthalf.", "robertwalters.", "hays.",
        "randstad.", "manpower.", "kellyservices.", "adecco.", "remotive.",
        "remote.co", "workingnomads.", "jobspresso.", "himalayas.",
        "remoteleaf.", "jobgether.", "arc.dev", "capitalonecareers.",
        "careers.", "jobs.", "job-", "recruit", "orange.jobs",
        "getmereferred.", "interviewchacha.", "ambitionbox.", "fishbowlapp.",
        "capitalplacement.", "jobnexusindia.", "hicareer.",
        "traveltechessentialist.", "n-ix.com", "unitedhealthgroup.",
        "instagram.", "twitter.", "mobile.twitter.", "wikipedia.",
        "enhancv.", "geeksforgeeks.", "cloud.google.", "study.com",
        "bloomfire.", "merriam-webster.", "diligent-actor-263.notion.site",
        "reddit.", "cryptomize.", "friendsjob.", "rojgargroup.",
        "dataanalyst.com", "uplers.",
    ]
    u = url.lower()
    return any(d in u for d in blocked)


def _parse_serpapi_date_days(date_str):
    """Parse SerpAPI relative date strings ('3 days ago', '2 weeks ago', '12 hours ago')
    into an age in days. Returns None when absent or unparseable."""
    if not date_str:
        return None
    s = str(date_str).lower().strip()
    if "yesterday" in s:
        return 1.0
    if "today" in s or "hour" in s or "minute" in s:
        return 0.0
    m = re.match(r"(\d+)\s*(day|week|month|year)s?\s+ago", s)
    if not m:
        return None
    n = int(m.group(1))
    unit = m.group(2)
    return float(n) * {"day": 1, "week": 7, "month": 30, "year": 365}[unit]


def _linkedin_post_age_days(url):
    """Decode a LinkedIn activity/ugcPost snowflake ID from the URL into the post's
    age in days (LinkedIn IDs encode epoch-ms in the top 41 bits: id >> 22).
    Returns None when the URL carries no decodable ID."""
    if not url:
        return None
    m = re.search(r"(?:activity|ugcPost)[-_](\d{15,25})", url)
    if not m:
        return None
    try:
        ms = int(m.group(1)) >> 22
        post_dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
        return (datetime.now(timezone.utc) - post_dt).total_seconds() / 86400
    except Exception:
        return None


def _is_stale_post(url, date_str):
    """Freshness gate: True when the post is older than MAX_POST_AGE_DAYS or its age
    cannot be verified. Only provably fresh posts pass (fresh leads only)."""
    age = _parse_serpapi_date_days(date_str)
    if age is None:
        age = _linkedin_post_age_days(url)
    if age is None:
        return True  # unverifiable age -> treat as stale
    return age > MAX_POST_AGE_DAYS


# ----------------------------- Search -------------------------------------
def search_recruiter_posts():
    """
    Strategy: only two highly-targeted Google queries per run.
    - SerpAPI num=10 keeps each query cheap (still counts as 1 credit each).
    - We rely on 'site:linkedin.com/posts' plus '@' to surface personal posts,
      not job boards.
    """
    if not SERPAPI_KEY:
        print("[Search] SERPAPI_KEY not configured.")
        return []

    roles = ('("Data Analyst" OR "Business Analyst" OR "Research Analyst" '
         'OR "BI Analyst" OR "BI Developer" OR "Power BI Developer" '
         'OR "Reporting Analyst" OR "Data Operations Analyst" '
         'OR "Junior Business Analyst" OR "MIS Analyst")')
    locations = '("Delhi" OR "Delhi NCR" OR "Noida" OR "Gurgaon" OR "Gurugram" OR "Remote" OR "Hybrid")'
    contact = '("email" OR "@" OR "reach out" OR "dm" OR "resume" OR "cv" OR "referral" OR "hiring" OR "linkedin.com/in" OR "phone")'

    queries = [
        # 1) LinkedIn posts with roles + location + contact (primary source)
        f'site:linkedin.com/posts {roles} {locations} {contact}',
        # 2) AngelList (startup posts; not blocked by the job-board filter)
        f'site:angel.co {roles} {locations} {contact} -jobs -careers -apply',
        # 3) Wider Google search (freshness gate filters stale hits)
        f'{roles} {locations} {contact} -jobs -careers -apply',
    ]

    results = []
    quota_errors = 0
    total_queries = len(queries)
    for q in queries:
        print(f"[Search] {q[:120]}...")
        try:
            r = requests.get(
                "https://serpapi.com/search",
                params={"engine": "google", "q": q, "api_key": SERPAPI_KEY,
                        "num": 10, "hl": "en", "gl": "in", "tbs": "qdr:d4"},
                timeout=30,
            )
            data = r.json()
            if "error" in data:
                err_msg = data["error"]
                print(f"[SerpAPI Error] {err_msg}")
                if "run out of searches" in err_msg or "quota" in err_msg.lower():
                    quota_errors += 1
                continue
            for item in data.get("organic_results", []):
                url = item.get("link", "")
                if _is_job_board_url(url):
                    continue
                if _is_stale_post(url, item.get("date", "")):
                    print(f"  [Freshness] rejected (> {MAX_POST_AGE_DAYS}d or unverifiable age): {url[:80]}")
                    continue
                results.append({
                    "title": item.get("title", ""),
                    "body": item.get("snippet", ""),
                    "href": url,
                })
        except Exception as e:
            print(f"[Search Error] {e}")
        time.sleep(2)

    # If every query hit quota, abort run
    if quota_errors == total_queries and total_queries > 0:
        err_msg = "SerpAPI monthly quota exhausted — no searches possible this month."
        print(f"[Fatal] {err_msg}")
        send_telegram(f"🛑 <b>Job Scout stopped</b>\n{err_msg}")
        sys.exit(1)

    # Deduplicate
    seen = set()
    unique = []
    for r in results:
        u = r["href"]
        if u and u not in seen:
            seen.add(u)
            unique.append(r)
    return unique


# ----------------------------- NVIDIA NIM Analysis ----------------------------
def analyze_post_verbose(post, max_retries=3):
    """
    Returns (parsed_dict_or_None, verdict_str).
    verdict is one of: 'ok', 'irrelevant', 'no_email', 'quota_error', 'error'
    """
    if not client:
        return None, "no_client"

    title, snippet, url = post["title"], post["body"], post["href"]
    # Extract contacts (emails, LinkedIn URLs, phone numbers) from the post content
    contacts = extract_contacts(snippet + " " + title)
    found_emails = contacts.get("emails", [])
    found_linkedin = contacts.get("linkedin", [])
    found_phones = contacts.get("phones", [])

    prompt = f"""You are an expert job matcher and email writer for candidate {CANDIDATE_NAME}.

    Candidate: Data Analyst | SQL, Python, Power BI, Tableau, Excel, research/reporting, ETL.
    ~1 year experience as Research & Strategy Associate (Founding Team Member) at ProSca Business Solutions + internships at IBM/UptoSkills/Vodafone.
    Wants: Data/Research/BI/Business/MIS/Reporting Analyst roles in Delhi/NCR/Noida/Gurgaon/Gurugram/Remote/Hybrid. Day shift, full-time, 6.5-7 LPA.
    Portfolio: {CANDIDATE_PORTFOLIO}
    LinkedIn: {CANDIDATE_LINKEDIN}
    GitHub: https://github.com/shukla009
    Email: work.shuklashivank@gmail.com | Phone: +91 9013584218

    Post:
    Title: {title}
    Snippet: {snippet}
    URL: {url}
    Emails found in text: {found_emails or 'none'}

    Rules (ALL MUST PASS):
    - Accept ONLY if it is a genuine recruiter/hiring post matching the candidate's roles AND locations.
    - Reject job boards, blogs, resume templates, ads, awareness/advice posts (e.g. job scam warnings, "how to get hired", tips), candidate self-promotion.
    - Reject senior/lead/manager roles unless explicitly junior/fresher level.
    - Reject if the post is clearly old (posted/apply-by dates in the past, "last month", "weeks ago") - we only email active, recent openings.
    - Reject if there is NO real recruiter email visible in the post text (we do NOT send LinkedIn DMs). A "DM me"/"comment" CTA with no email = reject.
    - NEVER invent an email. If none is in the post, set recruiter_email to null.

    If accepted, write a personalized cold email that follows the format and tone of the example below.

    Example of the desired email format (follow this structure and tone, but tailor the content to the specific post):

    Hi [Company Name] Team,

    I came across your [Job Title] opening in [Location], and the role immediately stood out because it mirrors the work I enjoy most: building reliable reporting, turning raw business data into clear insights, and partnering with teams to make better decisions.

    I currently work as a Research & Strategy Associate at ProSca Business Solutions, where I analyze qualitative and quantitative data across 5+ industry verticals and have contributed to 12+ strategic intelligence reports. I also helped reduce research turnaround time by 20% through standardized analytical frameworks and reporting templates.

    My hands-on toolkit includes SQL, Power BI, Tableau, Excel, Python, ETL, data cleaning, KPI development, and dashboarding. Relevant work includes improving data-collection efficiency by 40% across 1,000+ institutional records, analyzing 10,000+ healthcare records, and building a Power BI/DAX dashboard for [Relevant Project/Company] sales and inventory analysis. [Their Company Name]'s cross-functional analytics environment across operations, revenue, marketing, and sales is exactly the kind of business-facing role I am looking for.

    Portfolio: https://shivank-shukla-portfolio.vercel.app/
    LinkedIn: https://www.linkedin.com/in/shivank-shukla09
    Notice period: 1 month

    I have attached my updated resume and would welcome the opportunity to discuss how I could contribute to your analytics team.

    Best regards,
    Shivank Shukla
    +91-9013584218

    Rules for the email:
    - Use PLAIN URLs only (no @url:... or markdown wrappers)
    - Each paragraph must be separated by exactly ONE blank line
    - Do NOT add extra spaces or formatting
    - Use the EXACT structure above - 6 paragraphs total
    - If company/location specifics aren't clear from post, use generic but professional language
    - NEVER use banned phrases: "I came across your post", "I believe I would be a great/strong fit", "I am writing to express my interest", "as a Data Analyst with...", "as a seasoned data analyst...", "2+ years of experience", "senior-level", "extensive experience", "deep expertise", "your company's emphasis/values", "resonates with my experience", "caught my attention", "I noticed that you're/are looking", "I am confident that I can/will", "I can bring/add value to", "I am particularly drawn to", "I am excited about the opportunity/to apply", "I believe my background aligns", "my background aligns perfectly", "I have/ve built several", "I have/ve worked on various"
    - TONE: Professional, confident, concise. Show you READ their post. Be specific about THEIR requirements when possible.

    Return ONLY JSON:
    {{
      "is_relevant": true|false,
      "recruiter_email": "best_email_or_null",
      "job_title": "...",
      "company": "...",
      "email_subject": "Application for [Job Title] - {CANDIDATE_NAME}",
      "email_body": "...",
      "reason": "one line why accepted/rejected"
    }}"""

    for attempt in range(max_retries):
        try:
            resp = client.chat.completions.create(
                model=NVIDIA_MODEL,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0.1,
                max_tokens=2048,
            )
            content = resp.choices[0].message.content
            if not content or not content.strip():
                raise ValueError("Empty response from LLM")
            result = json.loads(content)

            # Prefer regex-extracted email over model guess
            if found_emails:
                result["recruiter_email"] = found_emails[0]

            if not result.get("is_relevant"):
                print(f"  -> rejected: {result.get('reason', 'irrelevant')}")
                return None, "irrelevant"

            if not _valid_recruiter_email(result.get("recruiter_email")):
                return None, "no_email"

            return result, "ok"

        except (json.JSONDecodeError, ValueError) as e:
            err = str(e)
            if attempt < max_retries - 1:
                print(f"  [NVIDIA JSON Error] {err} — retry {attempt+2}/{max_retries}")
                time.sleep(3)
                continue
            print(f"  [NVIDIA JSON Error] {err} — giving up")
            return None, f"json_error: {err[:100]}"
        except Exception as e:
            err = str(e)
            # Check for authentication errors
            if "401" in err or "Unauthorized" in err or "authentication failed" in err.lower():
                error_msg = f"NVIDIA API authentication failed for model '{NVIDIA_MODEL}'. Please check your NVIDIA_API_KEY environment variable."
                print(f"  [NVIDIA Auth Error] {error_msg}")
                return None, "auth_error"
            # Check for rate limit errors
            elif "429" in err or "rate limit" in err.lower() or "RPM" in err:
                wait = 30 * (2 ** attempt)
                print(f"  [NVIDIA 429] retry {attempt+1}/{max_retries} in {wait}s")
                time.sleep(wait)
                if attempt == max_retries - 1:
                    return None, "quota_error"
            else:
                print(f"  [NVIDIA Error] {err[:150]}")
                return None, f"llm_error: {err[:100]}"

    return None, "error"


# ----------------------------- Follow‑up Scheduler ---------------------------

def load_follow_up():
    """Load pending follow‑up entries from follow_up.json (or create empty list)."""
    if os.path.exists('follow_up.json'):
        try:
            with open('follow_up.json', 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"[Follow‑up] Error loading follow_up.json: {e}")
            return []
    return []

def save_follow_up(data):
    """Write follow‑up list back to follow_up.json."""
    try:
        with open('follow_up.json', 'w') as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"[Follow‑up] Error saving follow_up.json: {e}")

def schedule_follow_up(email, subject, body_text):
    """Create three follow‑up entries (2,5,9 days) for a newly sent email.
    Each entry stores: email, subject, body_text, step (1‑3), next_send (epoch timestamp).
    """
    now = datetime.utcnow()
    offsets = [2, 5, 9]  # days from now
    entries = []
    for i, d in enumerate(offsets, start=1):
        entry = {
            "email": email,
            "subject": subject,
            "body": body_text,
            "step": i,
            "next_send": (now + timedelta(days=d)).timestamp()
        }
        entries.append(entry)
    # Append to file
    follow_ups = load_follow_up()
    follow_ups.extend(entries)
    save_follow_up(follow_ups)

def process_pending_follow_ups():
    """Send any follow‑up emails whose scheduled time has arrived.
    After sending step 1 or 2, re‑schedule the next step (if any). Step 3 is final.
    """
    follow_ups = load_follow_up()
    if not follow_ups:
        return
    now_ts = datetime.utcnow().timestamp()
    updated = []
    for fu in follow_ups:
        if now_ts >= fu.get('next_send', 0):
            # Send follow‑up
            follow_subject = f"Re: {fu['subject']}"
            follow_body = fu['body'] + "\n\n" + f"(Follow‑up {fu['step']} – just checking in)"
            ok = send_email(fu['email'], follow_subject, follow_body)
            if ok:
                print(f"[Follow‑up] Sent step {fu['step']} to {fu['email']}")
                # If there is a next step, schedule it
                if fu['step'] < 3:
                    # compute next offset based on original schedule (2→5→9 days)
                    next_offset = [5, 9][fu['step'] - 1] if fu['step'] == 1 else 9
                    fu['step'] += 1
                    fu['next_send'] = (datetime.utcnow() + timedelta(days=next_offset)).timestamp()
                    updated.append(fu)
                else:
                    print(f"[Follow‑up] Completed all steps for {fu['email']}")
            else:
                print(f"[Follow‑up] Failed to send step {fu['step']} to {fu['email']}")
                # Keep entry for retry later
                updated.append(fu)
        else:
            updated.append(fu)
    save_follow_up(updated)

# ----------------------------- Main ----------------------------------------
def main():
    print("=== Job Scout Agent Starting ===")

    if not all([NVIDIA_API_KEY, SERPAPI_KEY, GMAIL_SENDER, GMAIL_PASSWORD,
                TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID]):
        print("[Fatal] One or more required env vars are missing.")
        send_telegram("❗ Job Scout stopped: missing API keys/secrets.")
        sys.exit(1)

    # Process any pending follow‑ups first
    process_pending_follow_ups()

    start_msg = (
        f"🤖 <b>Job Scout Started</b>\n\n"
        f"👤 {CANDIDATE_NAME}\n"
        f"🕐 {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"🎯 Roles: Data / Research / BI / Business Analyst\n"
        f"📍 Delhi-NCR / Remote / Hybrid | ₹6.5-7 LPA\n"
        f"🧠 Model: {NVIDIA_MODEL}"
    )
    send_telegram(start_msg)

    history = load_history()
    applied = {e.get("email", "").lower() for e in history if isinstance(e, dict)}

    posts = search_recruiter_posts()
    total = len(posts)
    print(f"[Search] {total} candidate posts after filtering")
    send_telegram(f"🔍 Search complete: <b>{total}</b> posts to analyse (1-by-1)…")

    sent = 0
    rejected_irrelevant = 0
    rejected_no_email = 0
    rejected_duplicate = 0
    errors = 0
    error_details = []

    for i, post in enumerate(posts, 1):
        url = post["href"]
        print(f"\n[{i}/{total}] {url}")

        parsed, verdict = analyze_post_verbose(post)
        if parsed is None:
            if verdict == "irrelevant":
                rejected_irrelevant += 1
            elif verdict == "no_email":
                rejected_no_email += 1
            elif verdict == "auth_error":
                # Fatal error: stop the run
                errors += 1
                error_details.append(verdict[:80])
                send_telegram(f"🛑 <b>Job Scout stopped</b>\nNVIDIA API authentication failed. Please check your NVIDIA_API_KEY environment variable.")
                break  # break out of the for loop
            else:
                errors += 1
                error_details.append(verdict[:80])
            continue

        email = parsed["recruiter_email"].lower()
        if email in applied:
            print("  -> already contacted, skipping")
            rejected_duplicate += 1
            continue

        job_title = parsed.get("job_title", "Data Analyst")
        subject = parsed.get("email_subject",
                             f"Application for {job_title} – {CANDIDATE_NAME}")
        body = parsed.get("email_body", "")

        print(f"  ✅ MATCH: {email} | {job_title}")
        ok = send_email(email, subject, body)
        if ok:
            entry = {"email": email, "job_title": job_title, "url": url,
                     "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")}
            history.append(entry)
            applied.add(email)
            save_history(history)
            schedule_follow_up(email, subject, body)
            sent += 1
            send_telegram(
                f"🚀 <b>Email sent</b>\n"
                f"📧 <code>{email}</code>\n"
                f"💼 {html.escape(str(job_title))}\n"
                f"🔗 <a href='{url}'>post</a>"
            )
        else:
            errors += 1
            error_details.append(f"email send failed → {email}")

        # Stay well under NVIDIA NIM free tier (40 RPM)
        time.sleep(2)

    err_preview = ""
    if error_details:
        shown = "\n".join(f"   • {e}" for e in error_details[:5])
        more = f"\n   …and {len(error_details) - 5} more" if len(error_details) > 5 else ""
        err_preview = f"\n⚠️ <b>Error details:</b>\n{shown}{more}\n"

    summary = (
        f"✅ <b>Run Complete — {time.strftime('%d %b %Y, %H:%M')}</b>\n\n"
        f"🔍 Posts analysed: <b>{total}</b>\n"
        f"🚫 Irrelevant/wrong role: {rejected_irrelevant}\n"
        f"📭 No recruiter email visible: {rejected_no_email}\n"
        f"🔄 Already contacted: {rejected_duplicate}\n"
        f"⚠️ Errors: {errors}\n"
        f"{err_preview}\n"
        f"📧 <b>Emails sent this run: {sent}</b>\n"
        f"💡 Total unique recruiters in history: {len(applied)}"
    )
    send_telegram(summary)
    print("=== Done ===")


if __name__ == "__main__":
    main()