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
from openai import OpenAI

# Load environment variables
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
RESUME_PATH = "resume.pdf"

# Model to use on NVIDIA NIM
NVIDIA_MODEL = "nvidia/nemotron-3-ultra-550b-a55b"
# Fast model for post analysis (Llama 3.1 8B - very fast)
NVIDIA_ANALYSIS_MODEL = "meta/llama-3.1-8b-instruct"

# Initialize NVIDIA NIM Client (OpenAI-compatible)
if NVIDIA_API_KEY and NVIDIA_API_KEY != "YOUR_NVIDIA_API_KEY_HERE":
    client = OpenAI(
        base_url="https://integrate.api.nvidia.com/v1",
        api_key=NVIDIA_API_KEY
    )
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

def send_email(to_email, subject, body_text, body_html=None):
    if not GMAIL_SENDER or not GMAIL_PASSWORD:
        print("[Email Error] Missing Gmail credentials.")
        return False

    msg = MIMEMultipart('alternative')
    msg['From'] = f"{CANDIDATE_NAME} <{GMAIL_SENDER}>"
    msg['To'] = to_email
    msg['Subject'] = subject

    # Plain text version
    msg.attach(MIMEText(body_text, 'plain'))

    # HTML version (if provided, otherwise generate from plain text)
    if body_html:
        msg.attach(MIMEText(body_html, 'html'))
    else:
        # Convert plain text to basic HTML
        html_body = body_text.replace('\n\n', '</p><p>').replace('\n', '<br>')
        html_body = f"<html><body><p>{html_body}</p></body></html>"
        msg.attach(MIMEText(html_body, 'html'))

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

def generate_search_queries_ai(previous_results_summary: str = "") -> list:
    """Use AI to generate optimized Google dorking queries for recruiter posts."""
    if not client:
        print("[AI Query Gen] No NVIDIA client, using fallback queries")
        return get_fallback_queries()
    
    candidate_context = ""
    try:
        with open("Resume/Shivank_Shukla_Career_Context.md", "r", encoding="utf-8") as f:
            candidate_context = f.read(1500)
    except:
        candidate_context = "Data Analyst with 2+ years experience in SQL, Python, Power BI, data visualization. Seeking Data Analyst/BI Analyst/Research Analyst/Business Analyst roles in Delhi NCR/Noida/Gurgaon/Remote. 6.5-7 LPA, day shift, full-time."
    
    prompt = f"""You are an expert at Google dorking for finding RECRUITER LinkedIn posts with direct contact info (email, phone, DM request).

CANDIDATE PROFILE:
{candidate_context}

TARGET:
- Recruiter/hiring manager posts (NOT job boards)
- With DIRECT contact info: email, @handle, "DM me", "reach out", "contact", "referral"
- Roles: Data Analyst, Business Analyst, BI Analyst, Research Analyst, Market Research Analyst, SQL Analyst, MIS Analyst
- Locations: Delhi, Delhi NCR, Noida, Gurgaon, Gurugram, Remote, Hybrid
- Must be full-time, day shift, 6.5-7 LPA range

{previous_results_summary if previous_results_summary else "FIRST RUN - generate diverse initial queries"}

Generate 5 OPTIMIZED Google search queries that will find recruiter posts with contact info.
Each query must use site:linkedin.com/posts and include hiring signals + contact signals + location + role.

Return ONLY valid JSON array of strings:
["query1", "query2", "query3", "query4", "query5"]"""

    try:
        response = client.chat.completions.create(
            model=NVIDIA_MODEL,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.3,
            max_tokens=1024
        )
        result = json.loads(response.choices[0].message.content)
        # Handle both array and object with "queries" key
        if isinstance(result, list):
            return result
        elif isinstance(result, dict) and "queries" in result:
            return result["queries"]
        else:
            print("[AI Query Gen] Unexpected format, using fallback")
            return get_fallback_queries()
    except Exception as e:
        print(f"[AI Query Gen Error] {e}")
        return get_fallback_queries()

def get_fallback_queries() -> list:
    """Fallback hardcoded queries if AI fails."""
    return [
        'site:linkedin.com/posts "hiring" "Data Analyst" ("email" OR "@" OR "DM" OR "reach out" OR "contact") "Noida" OR "Delhi" OR "Gurgaon" OR "Gurugram" OR "Remote"',
        'site:linkedin.com/posts "hiring" "Business Analyst" ("email" OR "@" OR "DM" OR "reach out" OR "contact") "Noida" OR "Delhi" OR "Gurgaon" OR "Gurugram" OR "Remote"',
        'site:linkedin.com/posts "hiring" ("BI Analyst" OR "Business Intelligence" OR "Research Analyst" OR "Market Research") ("email" OR "@" OR "DM" OR "reach out" OR "contact") "Noida" OR "Delhi" OR "Gurgaon" OR "Remote"',
        'site:linkedin.com/posts ("referral" OR "DM me" OR "reach out" OR "send resume") "Data Analyst" "Noida" OR "Delhi" OR "Gurgaon" OR "Remote"',
        'site:linkedin.com/posts "we are hiring" "Data Analyst" "Noida" OR "Delhi" OR "Gurgaon" OR "Remote"',
    ]

def analyze_search_results_ai(results: list) -> str:
    """Use AI to analyze search results and provide feedback for query refinement."""
    # DISABLED: NVIDIA API calls too slow for SerpAPI rate limits
    # Simple heuristic-based analysis instead
    if not results:
        return ""
    
    # Count results with contact-like patterns
    contact_count = 0
    for r in results[:10]:
        body = r.get('body', '').lower()
        if any(kw in body for kw in ['@', 'email', 'dm me', 'reach out', 'contact', 'referral', 'phone', 'mobile']):
            contact_count += 1
    
    # Simple heuristic suggestions
    if contact_count >= 3:
        return json.dumps({
            "analysis": f"Good results: {contact_count}/10 have contact info. Queries working well.",
            "worked_patterns": ["site:linkedin.com/posts + hiring + role + location + contact signals"],
            "failed_patterns": [],
            "refined_queries": [
                'site:linkedin.com/posts "hiring" "Data Analyst" ("email" OR "@" OR "DM" OR "contact") "Noida" OR "Gurgaon" OR "Delhi" OR "Remote"',
                'site:linkedin.com/posts "referral" "Data Analyst" "Noida" OR "Gurgaon" OR "Delhi"',
                'site:linkedin.com/posts "we are hiring" "Business Analyst" "Noida" OR "Gurgaon" OR "Remote"'
            ]
        }, indent=2)
    else:
        return json.dumps({
            "analysis": f"Low contact info rate: {contact_count}/10. Need more specific contact signals.",
            "worked_patterns": ["site:linkedin.com/posts + hiring + role + location"],
            "failed_patterns": ["generic hiring posts without contact info"],
            "refined_queries": [
                'site:linkedin.com/posts "hiring" "Data Analyst" "email" "Noida" OR "Gurgaon"',
                'site:linkedin.com/posts "DM me" "Data Analyst" "referral" "Delhi" OR "Noida"',
                'site:linkedin.com/posts "contact" "hiring" "Business Analyst" "Gurgaon" OR "Remote"'
            ]
        }, indent=2)

def search_linkedin_posts():
    """Search for recruiter posts with contact info using AI-optimized queries."""
    if not SERPAPI_KEY:
        print("[Search] SERPAPI_KEY not configured. Falling back to empty results.")
        return []

    # AI-driven iterative search (max 2 iterations to stay within SerpAPI limits)
    all_results = []
    previous_summary = ""
    max_iterations = 2  # Reduced from 3 to stay within rate limits
    
    for iteration in range(max_iterations):
        print(f"\n[Search] === Iteration {iteration + 1}/{max_iterations} ===")
        
        # Generate queries (AI-powered after first iteration)
        if iteration == 0:
            queries = get_fallback_queries()  # Start with proven queries (5 queries)
        else:
            queries = generate_search_queries_ai(previous_summary)[:3]  # Max 3 refined queries
        
        print(f"[Search] Generated {len(queries)} queries for iteration {iteration + 1}")
        
        iteration_results = []
        for q in queries:
            print(f"[Search] Executing query...")
            try:
                params = {
                    "engine": "google",
                    "q": q,
                    "api_key": SERPAPI_KEY,
                    "num": 10,
                    "hl": "en",
                    "gl": "in",
                    "tbs": "qdr:d4"  # Past 4 days only
                }
                response = requests.get("https://serpapi.com/search", params=params, timeout=30)
                data = response.json()
                
                organic_results = data.get("organic_results", [])
                for r in organic_results:
                    url = r.get("link", "")
                    if not _is_job_board_url(url):
                        iteration_results.append({
                            "title": r.get("title", ""),
                            "body": r.get("snippet", ""),
                            "href": url
                        })
            except Exception as e:
                print(f"[Search Error] {e}")
            time.sleep(2)  # Rate limit safety
        
        # Deduplicate within iteration
        seen = set()
        unique_iteration = []
        for r in iteration_results:
            url = r.get("href")
            if url and url not in seen:
                seen.add(url)
                unique_iteration.append(r)
        
        print(f"[Search] Iteration {iteration + 1}: Found {len(unique_iteration)} unique recruiter posts")
        
        # Add to overall results (deduplicate across iterations)
        for r in unique_iteration:
            url = r.get("href")
            if url not in [x.get("href") for x in all_results]:
                all_results.append(r)
        
        # AI analysis for next iteration
        if iteration < max_iterations - 1 and unique_iteration:
            print(f"[Search] Analyzing results with AI for query refinement...")
            previous_summary = analyze_search_results_ai(unique_iteration)
            if previous_summary:
                print(f"[Search] AI Analysis: {previous_summary[:200]}...")
        else:
            break  # No results or last iteration
    
    print(f"\n[Search] === COMPLETE: {len(all_results)} total unique recruiter post(s) ===")
    return all_results


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
        "n-ix.com", "unitedhealthgroup.com", "n-ix.com",
        # Additional job boards and aggregators observed in the run
        "michaelpage.co.in", "glassdoor.co.in", "instagram.com", "twitter.com",
        "mobile.twitter.com", "wikipedia.org", "enhancv.com", "geeksforgeeks.org",
        "cloud.google.com", "study.com", "bloomfire.com", "merriam-webster.com",
        "diligent-actor-263.notion.site", "reddit.com", "ambitionbox.com",
        "fishbowlapp.com", "getmereferred.com", "orange.jobs", "careers.n-ix.com",
        "careers.unitedhealthgroup.com", "naukri.com", "indeed.com", "shine.com",
        "friendsjob.in", "rojgargroup.com", "placementindia.com", "naukri.com",
        "smartrecruiters.com", "myamcat.com", "capitalplacement.in",
        "traveltechessentialist.com", "shine.com", "indeed.com", "cryptomize.com",
        "glassdoor.co.in", "shine.com", "indeed.com", "reddit.com",
        "interviewchacha.com", "glassdoor.co.in", "orange.jobs", "instagram.com"
    ]
    url_lower = url.lower()
    for domain in job_board_domains:
        if domain in url_lower:
            return True
    return False

def analyze_and_process_post(post, max_retries=5):
    if not client:
        print("[NVIDIA NIM Error] NVIDIA_API_KEY is not configured in .env.")
        return None

    title = post.get("title", "")
    snippet = post.get("body", "")
    url = post.get("href", "")

    # Load a short candidate context for better matching
    candidate_context = ""
    try:
        with open("Resume/Shivank_Shukla_Career_Context.md", "r", encoding="utf-8") as f:
            # Read first 1000 characters to keep prompt short
            candidate_context = f.read(1000)
    except:
        candidate_context = "Research and Strategy Associate at ProSca Business Solutions (Founding Team Member). ~1 year combined early-career experience across data analysis, business research, market intelligence, and dashboard development. Delivered 12+ strategic reports across 5 verticals. Analyzed 10,000+ healthcare records. Built LSTM stock forecasting model with 1.9% MAPE. Reduced research turnaround 20%, improved data collection efficiency 40%. Skilled in SQL, Python, Power BI, Tableau, Advanced Excel, DAX."

    prompt = f"""
You are an expert job matcher and email writer. Analyze the LinkedIn/post below and extract ONLY if:
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

CANDIDATE PROFILE (summary):
{candidate_context}

CANDIDATE CONTACT INFO (use these EXACT details in the email signature - do NOT make up any contact info):
- Name: {CANDIDATE_NAME}
- Phone: +91 9013584218
- Email: work.shuklashivank@gmail.com
- Portfolio: {CANDIDATE_PORTFOLIO}
- LinkedIn: {CANDIDATE_LINKEDIN}
- GitHub: https://github.com/shukla009

POST TO ANALYZE:
Title: {title}
Content: {snippet}
URL: {url}

RETURN ONLY VALID JSON (no extra text):
{{
  "is_relevant": true/false,
  "recruiter_email": "extracted_email_or_null",
  "job_title": "extracted_job_title",
  "company_name": "company_or_recruiter_name",
  "email_subject": "Application for [Job Title] - Shivank Shukla",
  "email_body": "Write a PERSONALIZED, professional cold email (120-180 words) that:

    STRUCTURE:
    1. PERSONALIZED OPENING: Address the recruiter by name if visible in the post. Reference their SPECIFIC post title/role/company.
    2. WHY THIS ROLE: Connect 2-3 SPECIFIC requirements from their post (tech stack, domain, location, salary, experience level) to your relevant experience. Quote details from their snippet.
    3. CONCRETE EVIDENCE: Reference ONE specific portfolio project that matches THIS role (pick from: LSTM stock forecasting with 1.9% MAPE, 12+ strategic intelligence reports across 5 verticals, 10,000+ healthcare records analysis, 40% data collection efficiency improvement across 1,000+ institutions, 20% research turnaround reduction, Power BI/Tableau dashboards). Say WHY it's relevant to their needs.
    4. AVAILABILITY & FIT: Mention Delhi NCR base, immediate joining, day shift preference, 6.5-7 LPA alignment. Note: ~1 year early-career experience (ProSca Business Solutions, Founding Team Member + internships), not senior-level experience.
    5. PROFESSIONAL CLOSE: Clear call to action (e.g., 'I'd welcome a brief call to discuss how my SQL/Python/Power BI experience aligns with your team's needs').
    6. SIGNATURE: Use EXACT contact info provided above (no placeholders).

    BANNED PHRASES (do NOT use - automatic rejection if found):
    - 'I came across your post'
    - 'I believe I would be a great/strong fit'
    - 'I am writing to express my interest'
    - 'As a Data Analyst with...'
    - 'As a seasoned data analyst...'
    - '2+ years of experience' / '2+ years experience'
    - 'senior-level' / 'extensive experience' / 'deep expertise'
    - 'Your company's emphasis' / 'Your company values' (recruiter is a PERSON, not a company)
    - 'resonates with my experience' (generic filler)
    - Generic templates

    STRICT: If any banned phrase appears, the email is INVALID. Do NOT use them.

    TONE: Professional, confident, concise. Show you READ their post. Be specific about THEIR requirements.

    MUST USE EXACT CONTACT INFO: Phone +91 9013584218, Email work.shuklashivank@gmail.com, Portfolio {CANDIDATE_PORTFOLIO}, LinkedIn {CANDIDATE_LINKEDIN}, GitHub https://github.com/shukla009

    URL FORMAT: Use plain URLs only (https://...). NO @url:... or markdown wrappers.

    OUTPUT FORMAT: Return JSON with TWO fields:
    - \"email_body\": plain text version
    - \"email_body_html\": HTML version with proper formatting (paragraphs, line breaks, signature block)

    Both versions must contain the same content, just different formats.
    "
}}
"""

    # Retry logic for rate limits (NVIDIA NIM has 40 RPM, much higher than Gemini)
    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model=NVIDIA_ANALYSIS_MODEL,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0.2,
                max_tokens=2048
            )
            result = json.loads(response.choices[0].message.content)

            # Validate we have the required fields for sending email
            if result.get("is_relevant") and result.get("recruiter_email") and "@" in str(result.get("recruiter_email", "")):
                # Post-generation validation for banned phrases
                banned_phrases = [
                    # Original banned phrases
                    "i came across your post",
                    "i believe i would be a great fit",
                    "i believe i would be a strong fit",
                    "i am writing to express my interest",
                    "as a data analyst with",
                    "as a seasoned data analyst",
                    "2+ years of experience",
                    "2+ years experience",
                    "senior-level",
                    "extensive experience",
                    "deep expertise",
                    "your company's emphasis",
                    "your company values",
                    "resonates with my experience",
                    # Additional patterns to catch unprofessional language
                    "your post about the",
                    "your post for the",
                    "your post regarding the",
                    "caught my attention",
                    "i noticed that you're looking",
                    "i noticed that you are looking",
                    "i am confident that i can",
                    "i am confident that i will",
                    "i can bring value to",
                    "i can add value to",
                    "i am particularly drawn to",
                    "i am excited about the opportunity",
                    "i am excited to apply",
                    "i believe my background aligns",
                    "my background aligns perfectly",
                    "i have built several",
                    "i've built several",
                    "i have worked on various",
                    "i've worked on various",
                ]
                
                email_text = result.get("email_body", "").lower()
                email_html = result.get("email_body_html", "").lower()
                
                for phrase in banned_phrases:
                    if phrase in email_text or phrase in email_html:
                        print(f"[Validation Failed] Banned phrase detected: '{phrase}' - retrying...")
                        raise ValueError(f"Banned phrase: {phrase}")
                
                return result

            return None
        except Exception as e:
            error_str = str(e)
            if "429" in error_str or "RATE_LIMIT" in error_str or "rate limit" in error_str.lower():
                delay = 10 * (2 ** attempt)  # 10s, 20s, 40s (more conservative)
                print(f"[Rate Limit] Attempt {attempt + 1}/{max_retries} failed. Waiting {delay}s before retry...")
                time.sleep(delay)
            else:
                print(f"[NVIDIA NIM Error] Failed to parse response: {e}")
                return None

    print(f"[NVIDIA NIM Error] All {max_retries} retries exhausted for rate limit")
    return None

def main():
    print("=== Starting Autonomous Job Scout Agent ===")

    # Start notification
    start_msg = (
        f"🤖 <b>Job Scout Agent Started</b>\n\n"
        f"👤 <b>Candidate:</b> {CANDIDATE_NAME}\n"
        f"🕐 <b>Time:</b> {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"🎯 <b>Searching for roles:</b> 23 target roles\n"
        f"📍 <b>Locations:</b> Delhi, NCR, Noida, Gurgaon, Remote, Hybrid\n"
        f"💰 <b>Salary:</b> ₹6.5-7 LPA | Day Shift | Full-time only\n"
        f"⏰ <b>Schedule:</b> 1 run/day (08:30 AM IST)"
    )
    send_telegram(start_msg)

    history = load_history()
    applied_emails = {entry.get("email") for entry in history if isinstance(entry, dict) and "email" in entry}

    posts = search_linkedin_posts()
    total_posts = len(posts)
    print(f"[Search] Found {total_posts} potential post(s).")

    # Limit posts to process to avoid rate limits (40 RPM = ~40 calls/min, need ~3 calls/post = ~13 posts/min)
    max_posts_per_run = 15
    if len(posts) > max_posts_per_run:
        print(f"[Search] Limiting to top {max_posts_per_run} posts to stay within rate limits")
        posts = posts[:max_posts_per_run]

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

    for i, post in enumerate(posts):
        url = post.get("href")
        # Clean up any @url: prefix that might come from search results
        if url.startswith("@url:"):
            url = url[5:].strip("`")
        print(f"\n[Processing] {url}")

        if client is None:
            errors += 1
            continue

        # Add delay between LLM calls to avoid rate limits (NVIDIA NIM: 40 RPM = 1.5s/call)
        if i > 0:
            time.sleep(2)

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
        body_text = parsed.get("email_body", "")
        body_html = parsed.get("email_body_html", "")

        print(f"🎯 [MATCH FOUND] Recruiter: {email} | Role: {job_title}")
        print(f"Subject: {subject}")

        # Send Email
        success = send_email(email, subject, body_text, body_html)
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

        # Small delay between posts to be polite to API
        time.sleep(1)

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
