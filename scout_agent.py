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
from duckduckgo_search import DDGS
from google import genai
from google.genai import types

# Load environment variables
load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GMAIL_SENDER = os.getenv("GMAIL_SENDER_EMAIL")
GMAIL_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
CANDIDATE_NAME = os.getenv("CANDIDATE_NAME", "Shivank Shukla")
CANDIDATE_PORTFOLIO = os.getenv("CANDIDATE_PORTFOLIO", "https://shivank-shukla-portfolio.vercel.app/")
CANDIDATE_LINKEDIN = os.getenv("CANDIDATE_LINKEDIN", "https://www.linkedin.com/in/shivank-shukla09")

HISTORY_FILE = "applied_history.json"
RESUME_PATH = "resume.pdf"

# Initialize Gemini Client
if GEMINI_API_KEY and GEMINI_API_KEY != "YOUR_GEMINI_API_KEY_HERE":
    client = genai.Client(api_key=GEMINI_API_KEY)
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
    queries = [
        'site:linkedin.com/posts "hiring" "Data Analyst" ("Delhi - NCR" OR "Noida" OR "Gurugram" OR "Gurgaon" OR "Remote") ("@gmail.com" OR "@" OR "send resume")',
        'site:linkedin.com/posts "looking for" ("BI Analyst" OR "Business Analyst" OR "Data Analyst") ("Delhi - NCR" OR "Noida" OR "Gurugram" OR "Gurgaon" OR "Remote") "@"',
        'site:linkedin.com/posts "hiring" ("Data Analyst" OR "Product Analyst") ("Delhi - NCR" OR "Noida" OR "Gurugram" OR "Gurgaon" OR "Remote" OR "Hybrid") "reach out"'
    ]

    results = []
    ddgs = DDGS()
    for q in queries:
        print(f"[Search] Executing query: {q}")
        try:
            res = ddgs.text(q, max_results=10)
            if res:
                results.extend(res)
        except Exception as e:
            print(f"[Search Error] {e}")
        time.sleep(1)
    
    # Deduplicate by URL
    seen = set()
    unique_results = []
    for r in results:
        url = r.get("href")
        if url and url not in seen:
            seen.add(url)
            unique_results.append(r)
    return unique_results

def analyze_and_process_post(post):
    if not client:
        print("[Gemini Error] GEMINI_API_KEY is not configured in .env.")
        return None

    title = post.get("title", "")
    snippet = post.get("body", "")
    url = post.get("href", "")

    prompt = f"""
You are an expert AI recruiter assistant for candidate {CANDIDATE_NAME}.

Candidate Background & Preferences:
- Preferred Roles: Data Analyst, BI Analyst, Business Analyst, Product Analyst, Data Engineering Associate
- Locations: Delhi-NCR, Noida, Gurgaon / Gurugram, Remote, Hybrid (Full-time Day Shift)
- Target Salary: 6.5 - 7 LPA
- Portfolio: {CANDIDATE_PORTFOLIO}
- LinkedIn: {CANDIDATE_LINKEDIN}

Recruiter Post Snippet:
Title: {title}
Body: {snippet}
URL: {url}

Task:
1. Determine if this post is a genuine job opening matching the candidate's preferred roles & locations.
2. Search and extract the direct recruiter email address from the text snippet if present (e.g., recruiter@company.com or company HR email).
3. If valid and email is found, draft a concise, compelling cold email (120-150 words) from Shivank Shukla. Highlight key skills relevant to the post (Python, SQL, PowerBI, Data Visualization, EDA), reference their portfolio, and mention the attached resume.

Return ONLY a JSON object with this exact structure:
{{
  "is_relevant": true/false,
  "recruiter_email": "extracted_email_or_null",
  "job_title": "extracted_job_title",
  "company_name": "company_or_recruiter_name",
  "email_subject": "Application for [Job Title] - Shivank Shukla",
  "email_body": "Cold email body here..."
}}
"""

    try:
        response = client.models.generate_content(
            model='gemini-1.5-flash-latest',
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.2
            )
        )

        data = json.loads(response.text)
        return data
    except Exception as e:
        print(f"[Gemini Parsing Error] {e}")
        return None

def main():
    print("=== Starting Autonomous Job Scout Agent ===")
    history = load_history()
    applied_emails = {entry.get("email") for entry in history if isinstance(entry, dict) and "email" in entry}

    posts = search_linkedin_posts()
    print(f"[Search] Found {len(posts)} potential post(s).")

    new_applications = 0

    for post in posts:
        url = post.get("href")
        print(f"\n[Processing] {url}")
        
        parsed = analyze_and_process_post(post)
        if not parsed:
            continue

        if not parsed.get("is_relevant"):
            print("[Skip] Post not relevant or location/role mismatch.")
            continue

        email = parsed.get("recruiter_email")
        if not email or email == "extracted_email_or_null" or "@" not in email:
            print("[Skip] No direct recruiter email found in snippet.")
            continue

        if email.lower() in applied_emails:
            print(f"[Skip] Already emailed recruiter at {email}.")
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

            # Telegram Notification
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

    print(f"\n=== Run Complete. Total new applications sent: {new_applications} ===")

if __name__ == "__main__":
    main()
