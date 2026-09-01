import os
import re
import json
import time
import smtplib
import urllib.request
import urllib.parse
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from dotenv import load_dotenv
import requests
from openai import OpenAI

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
NVIDIA_MODEL = "nvidia/nemotron-3-super-120b-a12b"  # Fast, accurate model for analysis

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
        except Exception:
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

    msg = MIMEMultipart()
    msg["From"] = f"{CANDIDATE_NAME} <{GMAIL_SENDER}>"
    msg["To"] = to_email
    msg["Subject"] = subject
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


def extract_emails(text):
    """Cheap regex pass — catches most recruiter emails without using LLM."""
    if not text:
        return []
    pattern = r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"
    return list(dict.fromkeys(re.findall(pattern, text)))


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
    contact = '("email" OR "@" OR "reach out" OR "dm" OR "resume" OR "cv" OR "referral" OR "hiring")'

    queries = [
        # 1) LinkedIn posts with roles + location + contact
        f'site:linkedin.com/posts {roles} {locations} {contact}',
        # 2) Wider Google search limited to recent (past-week implied by 'hiring' intent)
        f'{roles} {locations} {contact} -jobs -careers -apply',
    ]

    results = []
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
                print(f"[SerpAPI Error] {data['error']}")
                continue
            for item in data.get("organic_results", []):
                url = item.get("link", "")
                if _is_job_board_url(url):
                    continue
                results.append({
                    "title": item.get("title", ""),
                    "body": item.get("snippet", ""),
                    "href": url,
                })
        except Exception as e:
            print(f"[Search Error] {e}")
        time.sleep(2)

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
    found_emails = extract_emails(snippet + " " + title)

    prompt = f"""You are a strict job-filter for candidate {CANDIDATE_NAME}.

Candidate: Data Analyst | SQL, Python, Power BI, Tableau, Excel, research/reporting.
Wants: Data/Research/BI/Business Analyst roles in Delhi/NCR/Noida/Gurgaon/Remote/Hybrid. Day shift, full-time, 6-7 LPA (negotiable for right role).

Post:
Title: {title}
Snippet: {snippet}
URL: {url}
Emails found in text: {found_emails or 'none'}

Rules:
- Relevant ONLY if it is a recruiter/hiring post for the candidate's roles AND location.
- Reject job boards, blogs, resume templates, ads, generic pages.
- Reject if no visible recruiter email (we do NOT send LinkedIn DMs).
- If relevant, write a short personalized cold email (100-130 words) referencing the role, mentioning portfolio {CANDIDATE_PORTFOLIO} and LinkedIn {CANDIDATE_LINKEDIN}.
- Email format: the portfolio link on its own line, then the LinkedIn link on the next line.

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
            result = json.loads(resp.choices[0].message.content)

            # Prefer regex-extracted email over model guess
            if found_emails:
                result["recruiter_email"] = found_emails[0]

            if not result.get("is_relevant"):
                print(f"  -> rejected: {result.get('reason', 'irrelevant')}")
                return None, "irrelevant"

            if not result.get("recruiter_email") or "@" not in result["recruiter_email"]:
                print("  -> rejected: no recruiter email visible")
                return None, "no_email"

            return result, "ok"

        except Exception as e:
            err = str(e)
            if "429" in err or "rate limit" in err.lower() or "RPM" in err:
                wait = 30 * (2 ** attempt)
                print(f"  [NVIDIA 429] retry {attempt+1}/{max_retries} in {wait}s")
                time.sleep(wait)
                if attempt == max_retries - 1:
                    return None, "quota_error"
            else:
                print(f"  [NVIDIA Error] {err[:150]}")
                return None, "error"

    return None, "error"


# ----------------------------- Main ----------------------------------------
def main():
    print("=== Job Scout Agent Starting ===")

    if not all([NVIDIA_API_KEY, SERPAPI_KEY, GMAIL_SENDER, GMAIL_PASSWORD,
                TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID]):
        print("[Fatal] One or more required env vars are missing.")
        send_telegram("❗ Job Scout stopped: missing API keys/secrets.")
        return

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
            sent += 1
            send_telegram(
                f"🚀 <b>Email sent</b>\n"
                f"📧 <code>{email}</code>\n"
                f"💼 {job_title}\n"
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