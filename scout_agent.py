import os
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
from google import genai
from google.genai import types

# Load environment variables
load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GMAIL_SENDER = os.getenv("GMAIL_SENDER_EMAIL")
GMAIL_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
SERPAPI_KEY = os.getenv("SERPAPI_KEY")
CANDIDATE_NAME = os.getenv("CANDIDATE_NAME", "Shivank Shukla")
CANDIDATE_PORTFOLIO = os.getenv("CANDIDATE_PORTFOLIO", "https://shivank-shukla-portfolio.vercel.app/")
CANDIDATE_LINKEDIN = os.getenv("CANDIDATE_LINKEDIN", "https://www.linkedin.com/in/shivank-shukla09")

HISTORY_FILE = "applied_history.json"
RESUME_PATH = "resume.pdf"

# Initialize Gemini Client
if GEMINI_API_KEY and GEMINI_API_KEY != "YOUR_GEMINI_API_KEY_HERE":
    client = genai.Client(api_key=GEMINI_API_KEY, http_options={"api_version": "v1"})
else:
    client = None

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
        "disable_web_page_preview": True
    }).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req)
        print("[Telegram] Alert sent successfully.")
    except Exception as e:
        print(f"[Telegram Error] {e}")

def send_email(to_email, subject, body_text):
    if not GMAIL_SENDER or not GMAIL_PASSWORD:
        print("[Email Error] Missing Gmail credentials.")
        return False

    msg = MIMEMultipart()
    msg['From'] = f"{CANDIDATE_NAME} <{GMAIL_SENDER}>"
    msg['To'] = to_email
    msg['Subject'] = subject

    msg.attach(MIMEText(body_text, 'plain'))

    # Attach resume if available
    resume_source_path = "Shivank_Shukla_Data_Analyst.pdf"
    if not os.path.exists(resume_source_path):
        resume_source_path = "resume.pdf" # Fallback

    if os.path.exists(resume_source_path):
        try:
            with open(resume_source_path, "rb") as f:
                part = MIMEBase('application', 'octet-stream')
                part.set_payload(f.read())
                encoders.encode_base64(part)
                part.add_header('Content-Disposition', f'attachment; filename="Shivank_Shukla_Data_Analyst.pdf"')
                msg.attach(part)
                print(f"[Email] Attached {resume_source_path} as Shivank_Shukla_Data_Analyst.pdf")
        except Exception as e:
            print(f"[Email Warning] Could not attach resume: {e}")
    else:
        print(f"[Email Warning] Resume PDF not found. Email will be sent without attachment.")

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_SENDER, GMAIL_PASSWORD.replace(" ", ""))
            server.send_message(msg)
        print(f"[Email Success] Sent to {to_email}")
        return True
    except Exception as e:
        print(f"[Email Error] Failed to send to {to_email}: {e}")
        return False

def search_linkedin_posts():
    """Search for recruiter posts with contact info using SerpAPI."""
    if not SERPAPI_KEY:
        print("[Search] SERPAPI_KEY not configured. Falling back to empty results.")
        return []
    
    # All 23 target roles from career profile
    target_roles = [
        # Highest priority
        "Data Analyst", "Research Analyst", "Business Intelligence Analyst", 
        "BI Analyst", "Business Analyst", "Market Research Analyst", "Reporting Analyst",
        # Additional suitable
        "Power BI Analyst", "SQL Analyst", "MIS Executive", "MIS Analyst",
        "Analytics Associate", "Data and Reporting Analyst", "Research Associate",
        "Insights Analyst", "Strategy and Analytics Associate", "Market Intelligence Analyst",
        "Competitive Intelligence Analyst", "Business Research Analyst", "Data Operations Analyst",
        "Junior Data Analyst", "Junior Business Analyst", "Dashboard Analyst",
        "Visualization Analyst", "Healthcare Data Analyst", "Revenue Operations Analyst"
    ]
    
    # Build role query parts (grouped for API efficiency)
    role_groups = [
        '("Data Analyst" OR "Research Analyst" OR "Business Intelligence Analyst" OR "BI Analyst" OR "Business Analyst" OR "Market Research Analyst" OR "Reporting Analyst")',
        '("Power BI Analyst" OR "SQL Analyst" OR "MIS Executive" OR "MIS Analyst" OR "Analytics Associate" OR "Data and Reporting Analyst" OR "Research Associate")',
        '("Insights Analyst" OR "Strategy and Analytics Associate" OR "Market Intelligence Analyst" OR "Competitive Intelligence Analyst" OR "Business Research Analyst" OR "Data Operations Analyst")',
        '("Junior Data Analyst" OR "Junior Business Analyst" OR "Dashboard Analyst" OR "Visualization Analyst" OR "Healthcare Data Analyst" OR "Revenue Operations Analyst")'
    ]
    
    # Target locations (properly grouped)
    locations = '("Delhi" OR "Delhi NCR" OR "Noida" OR "Gurgaon" OR "Gurugram" OR "Remote" OR "Hybrid")'
    
    # Contact/intent signals (properly grouped)
    contact_signals = '("email" OR "@" OR "reach out" OR "contact" OR "dm" OR "resume" OR "cv" OR "apply" OR "referral" OR "hiring")'
    
    queries = []
    for role_group in role_groups:
        queries.append(f'{role_group} {locations} {contact_signals}')
    
    # Additional broad searches for recruiter posts on LinkedIn/Twitter
    queries.extend([
        f'site:linkedin.com/posts "Data Analyst" {locations} {contact_signals}',
        f'site:linkedin.com/posts "Business Analyst" {locations} {contact_signals}',
        f'site:twitter.com "Data Analyst" {locations} {contact_signals}',
        f'"Data Analyst" "referral" {locations} "@"',
        f'"Business Analyst" "referral" {locations} "@"',
    ])

    results = []
    for q in queries:
        print(f"[Search] Executing query: {q}")
        try:
            params = {
                "engine": "google",
                "q": q,
                "api_key": SERPAPI_KEY,
                "num": 10,
                "hl": "en",
                "gl": "in"
            }
            response = requests.get("https://serpapi.com/search", params=params, timeout=30)
            data = response.json()
            
            organic_results = data.get("organic_results", [])
            for r in organic_results:
                url = r.get("link", "")
                # Filter out job boards and aggregators - we want recruiter posts with contact info
                if not _is_job_board_url(url):
                    results.append({
                        "title": r.get("title", ""),
                        "body": r.get("snippet", ""),
                        "href": url
                    })
        except Exception as e:
            print(f"[Search Error] {e}")
        time.sleep(1)  # Respect rate limits
    
    # Deduplicate by URL
    seen = set()
    unique_results = []
    for r in results:
        url = r.get("href")
        if url and url not in seen:
            seen.add(url)
            unique_results.append(r)
    return unique_results


def _is_job_board_url(url: str) -> bool:
    """Filter out job board/aggregator URLs that won't have recruiter contact info."""
    job_board_domains = [
        "indeed.com", "naukri.com", "linkedin.com/jobs", "glassdoor.com",
        "shine.com", "monster.com", "timesjobs.com", "placementindia.com",
        "jobhai.com", "freshersworld.com", "cutshort.io", "instahyre.com",
        "wellfound.com", "angel.co", "internshala.com", "upwork.com",
        "freelancer.com", "toptal.com", "remoteok.com", "weworkremotely.com",
        "flexjobs.com", "remoterocketship.com", "jobsearch.com",
        "careerbuilder.com", "dice.com", "simplyhired.com", "ziprecruiter.com",
        "jobvite.com", "lever.co", "greenhouse.io", "smartrecruiters.com",
        "workday.com", "bamboohr.com", "icims.com", "taleo.net",
        "myamcat.com", "capstone.com", "michaelpage.com", "roberthalf.com",
        "robertwalters.com", "hays.com", "randstad.com", "manpower.com",
        "kellyservices.com", "adecco.com", "manpowergroup.com",
        "remotive.com", "remote.co", "workingnomads.com", "jobspresso.com",
        "himalayas.app", "remoteleaf.com", "jobgether.com", "arc.dev",
        "capitalonecareers.com", "careers.", "jobs.", "job-", "recruit",
        "orange.jobs", "getmereferred.com", "interviewchacha.com",
        "ambitionbox.com", "fishbowlapp.com", "capitalplacement.in",
        "jobnexusindia.com", "hicareer.com", "traveltechessentialist.com",
        "n-ix.com", "unitedhealthgroup.com", "n-ix.com"
    ]
    url_lower = url.lower()
    for domain in job_board_domains:
        if domain in url_lower:
            return True
    return False

def analyze_and_process_post(post):
    if not client:
        print("[Gemini Error] GEMINI_API_KEY is not configured in .env.")
        return None

    title = post.get("title", "")
    snippet = post.get("body", "")
    url = post.get("href", "")

    # Load the candidate context for better matching
    candidate_context = ""
    try:
        with open("Resume/Shivank_Shukla_Career_Context.md", "r", encoding="utf-8") as f:
            candidate_context = f.read()
    except:
        candidate_context = "Data Analyst with experience in SQL, Python, Power BI, and data visualization."

    # Create chat session for better AFC compliance
    chat = client.chats.create(model='gemini-3.5-flash')
    
    prompt = f"""
You are an expert job matcher. Analyze the LinkedIn/post below and extract ONLY if:
1. It's from a recruiter/hiring manager (not a job board or candidate post)
2. Contains contact information (email or clear indication to DM/contact for the role)
3. Is for a Data Analyst, Research Analyst, BI Analyst, Business Analyst, or similar analytics role in Delhi/NCR/Noida/Gurgaon/Gurugram/Remote/Hybrid
4. Offers 6.5-7 LPA salary (or doesn't contradict this range)
5. Is day shift, full-time
6. Matches candidate profile below

CRITICAL RULES:
- If unsure about recruiter/contact info, RETURN NULL
- Must have EITHER explicit email OR clear call-to-action like "DM me", "contact", "reach out", "referral", "apply"
- Reject if: "post your resume", "apply via link", "visit careers page", "send CV to HR" (no direct contact)
- Reject senior/lead/manager roles unless explicitly junior/fresher
- Location must match: Delhi, NCR, Noida, Gurgaon, Gurugram, Remote, Hybrid
- Salary: Must NOT contradict 6.5-7 LPA range (if mentioned, must be within or close)
- Employment: Must be full-time, day shift

CANDIDATE PROFILE:
{candidate_context[:2000]}

POST TO ANALYZE:
Title: {title}
Content: {snippet}
URL: {url}

RETURN ONLY VALID JSON (no extra text):
{{
  "is_recruiter_post": true/false,
  "role_title": "exact role title from post",
  "company": "company name if mentioned",
  "location": "location from post",
  "salary_mentioned": true/false,
  "salary_range": "if mentioned, else null",
  "has_contact_info": true/false,
  "contact_method": "email/dm/referral/contact",
  "contact_value": "email address or null",
  "is_relevant": true/false,
  "confidence": "high/medium/low",
  "reasoning": "brief explanation"
}}
"""
    
    try:
        response = chat.send_message(
            message=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.2
            )
        )
        result = json.loads(response.text)
        
        # Additional validation
        if result.get("is_recruiter_post") and result.get("has_contact_info") and result.get("is_relevant"):
            # Extract email if present
            email = result.get("contact_value")
            if email and "@" in str(email):
                return {
                    "email": email.strip(),
                    "role": result.get("role_title", ""),
                    "company": result.get("company", ""),
                    "location": result.get("location", ""),
                    "url": url,
                    "confidence": result.get("confidence", "medium")
                }
        
        return None
    except Exception as e:
        print(f"[Gemini Error] Failed to parse response: {e}")
        return None

def main():
    print("=== Starting Autonomous Job Scout Agent ===")
    
    # Start notification
    start_msg = (
        f"🤖 <b>Job Scout Agent Started</b>\n\n"
        f"👤 <b>Candidate:</b> {CANDIDATE_NAME}\n"
        f"🕐 <b>Time:</b> {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"🎯 <b>Searching for roles:</b> 23 target roles across 3 priority tiers\n"
        f"📍 <b>Locations:</b> Delhi, NCR, Noida, Gurgaon, Remote, Hybrid\n"
        f"💰 <b>Salary:</b> ₹6.5-7 LPA | Day Shift | Full-time only\n"
        f"⏰ <b>Schedule:</b> 2 runs/day (08:30 AM & 08:30 PM IST)"
    )
    send_telegram(start_msg)
    
    history = load_history()
    applied_emails = {entry.get("email") for entry in history if isinstance(entry, dict) and "email" in entry}

    posts = search_linkedin_posts()
    total_posts = len(posts)
    print(f"[Search] Found {total_posts} potential post(s).")
    
    # Search summary notification
    search_msg = (
        f"🔍 <b>Search Complete</b>\n\n"
        f"📊 <b>Total posts found:</b> {total_posts}\n"
        f"🔎 <b>Processing each post for relevance...</b>"
    )
    send_telegram(search_msg)

    new_applications = 0
    skipped_relevant = 0
    skipped_no_email = 0
    skipped_duplicate = 0
    skipped_irrelevant = 0
    errors = 0

    for post in posts:
        url = post.get("href")
        print(f"\n[Processing] {url}")
        
        parsed = analyze_and_process_post(post)
        if not parsed:
            errors += 1
            continue

        if not parsed.get("is_relevant"):
            print("[Skip] Post not relevant or location/role mismatch.")
            skipped_irrelevant += 1
            continue

        email = parsed.get("recruiter_email")
        if not email or email == "extracted_email_or_null" or "@" not in email:
            print("[Skip] No direct recruiter email found in snippet.")
            skipped_no_email += 1
            continue

        if email.lower() in applied_emails:
            print(f"[Skip] Already emailed recruiter at {email}.")
            skipped_duplicate += 1
            continue

        job_title = parsed.get("job_title", "Data Analyst")
        subject = parsed.get("email_subject", f"Application for {job_title} - {CANDIDATE_NAME}")
        body = parsed.get("email_body", "")

        print(f"🎯 [MATCH FOUND] Recruiter: {email} | Role: {job_title}")
        print(f"Subject: {subject}")

        # Send Email
        success = send_email(email, subject, body)
        if success:
            print(f"✅ [EMAIL SENT] {email} | {job_title}")
            entry = {
                "email": email.lower(),
                "job_title": job_title,
                "url": url,
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
            }
            history.append(entry)
            applied_emails.add(email.lower())
            save_history(history)
            new_applications += 1

            # Telegram Notification for each email
            tg_msg = (
                f"🚀 <b>New Cold Email Sent!</b>\n\n"
                f"👤 <b>Candidate:</b> {CANDIDATE_NAME}\n"
                f"📧 <b>Recruiter:</b> <code>{email}</code>\n"
                f"💼 <b>Role:</b> {job_title}\n"
                f"🔗 <b>Post:</b> <a href='{url}'>LinkedIn Post</a>\n"
                f"📎 <b>Resume:</b> Attached PDF\n\n"
                f"✨ <b>Portfolio:</b> {CANDIDATE_PORTFOLIO}"
            )
            send_telegram(tg_msg)
        else:
            errors += 1

    # Final Summary Notification
    summary_msg = (
        f"✅ <b>Job Scout Run Complete</b>\n\n"
        f"👤 <b>Candidate:</b> {CANDIDATE_NAME}\n"
        f"🕐 <b>Completed:</b> {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        f"📊 <b>Search Results:</b>\n"
        f"   🔍 Posts found: <b>{total_posts}</b>\n"
        f"   ❌ Irrelevant: {skipped_irrelevant}\n"
        f"   📭 No email: {skipped_no_email}\n"
        f"   🔄 Duplicate: {skipped_duplicate}\n"
        f"   ⚠️ Errors: {errors}\n\n"
        f"📧 <b>Emails Sent:</b> <b>{new_applications}</b>\n\n"
        f"💡 <b>Total unique recruiters contacted:</b> {len(applied_emails)}"
    )
    send_telegram(summary_msg)

    print(f"\n=== Run Complete. Total new applications sent: {new_applications} ===")

if __name__ == "__main__":
    main()
