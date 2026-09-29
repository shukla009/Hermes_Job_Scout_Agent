import hashlib
import html
import json
import os
import re
import smtplib
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html.parser import HTMLParser

import requests
from dotenv import load_dotenv
from openai import OpenAI

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
CANDIDATE_EMAIL = os.getenv("CANDIDATE_EMAIL", "work.shuklashivank@gmail.com")
CANDIDATE_PHONE = os.getenv("CANDIDATE_PHONE", "+91 9013584218")
CANDIDATE_GITHUB = os.getenv("CANDIDATE_GITHUB", "https://github.com/shukla009")

HISTORY_FILE = "applied_history.json"
FOLLOW_UP_FILE = "follow_up.json"
RUN_REPORT_FILE = "run_report.json"
RESUME_PATH = "Shivank_Shukla_Data_Analyst.pdf"
NVIDIA_MODEL = os.getenv("NVIDIA_MODEL") or "nvidia/nemotron-3-super-120b-a12b"
MAX_POST_AGE_DAYS = int(os.getenv("MAX_POST_AGE_DAYS", "4"))
MAX_DIRECT_APPLY_AGE_DAYS = int(os.getenv("MAX_DIRECT_APPLY_AGE_DAYS", "21"))
MAX_SOURCE_CHARS = int(os.getenv("MAX_SOURCE_CHARS", "12000"))
MAX_APPLY_ALERTS = int(os.getenv("MAX_APPLY_ALERTS", "5"))
HTTP_RETRIES = max(1, int(os.getenv("HTTP_RETRIES", "2")))
SEND_EMAILS = os.getenv("SEND_EMAILS", "true").strip().lower() in {"1", "true", "yes", "on"}
SMTP_PREFLIGHT = os.getenv("SMTP_PREFLIGHT", "true").strip().lower() in {"1", "true", "yes", "on"}


def _parse_follow_up_offsets(raw):
    try:
        values = sorted({int(value.strip()) for value in raw.split(",") if int(value.strip()) > 0})
        return values or [5]
    except (TypeError, ValueError):
        return [5]


FOLLOW_UP_OFFSETS_DAYS = _parse_follow_up_offsets(os.getenv("FOLLOW_UP_OFFSETS_DAYS", "5"))
client = OpenAI(base_url="https://integrate.api.nvidia.com/v1", api_key=NVIDIA_API_KEY) if NVIDIA_API_KEY else None
LAST_SEARCH_STATS = {"queries_attempted": 0, "queries_successful": 0, "query_errors": [], "results_discovered": 0}


def _load_json_list(path, label):
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file)
        if not isinstance(data, list):
            raise ValueError("expected a JSON list")
        return data
    except Exception as exc:
        print(f"[{label} Warning] {path} unreadable ({exc}) - starting with empty state")
        return []


def _save_json(path, data):
    temporary = f"{path}.tmp"
    with open(temporary, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, ensure_ascii=False)
    os.replace(temporary, path)


def load_history():
    return _load_json_list(HISTORY_FILE, "History")


def save_history(history):
    _save_json(HISTORY_FILE, history)


def load_follow_up():
    return _load_json_list(FOLLOW_UP_FILE, "Follow-up")


def save_follow_up(data):
    _save_json(FOLLOW_UP_FILE, data)


def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[Telegram] Missing token or chat ID.")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = json.dumps({"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML", "disable_web_page_preview": True}).encode("utf-8")
    request = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(request, timeout=15)
        print("[Telegram] Alert sent successfully.")
        return True
    except Exception as exc:
        print(f"[Telegram Error] {exc}")
        return False


def smtp_preflight():
    """Authenticate with Gmail without sending a message."""
    if not GMAIL_SENDER or not GMAIL_PASSWORD:
        print("[SMTP] Missing Gmail credentials.")
        return False
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
            server.login(GMAIL_SENDER, GMAIL_PASSWORD.replace(" ", ""))
        print("[SMTP] Authentication successful.")
        return True
    except Exception as exc:
        print(f"[SMTP Error] Authentication failed: {exc}")
        return False


def send_email(to_email, subject, body_text):
    if not GMAIL_SENDER or not GMAIL_PASSWORD:
        print("[Email Error] Missing Gmail credentials.")
        return False
    if not _valid_recruiter_email(to_email):
        print(f"[Email Error] Refusing invalid recipient: {to_email}")
        return False
    subject = " ".join(str(subject).split())
    message = MIMEMultipart()
    message["From"] = f"{CANDIDATE_NAME} <{GMAIL_SENDER}>"
    message["To"] = to_email
    message["Subject"] = subject
    message.attach(MIMEText(body_text, "plain", "utf-8"))
    if not os.path.exists(RESUME_PATH):
        print(f"[Email Error] Resume not found at {RESUME_PATH} - aborting send")
        return False
    try:
        with open(RESUME_PATH, "rb") as file:
            attachment = MIMEBase("application", "octet-stream")
            attachment.set_payload(file.read())
        encoders.encode_base64(attachment)
        attachment.add_header("Content-Disposition", f'attachment; filename="{os.path.basename(RESUME_PATH)}"')
        message.attach(attachment)
    except Exception as exc:
        print(f"[Email Error] Could not attach resume: {exc}")
        return False
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
            server.login(GMAIL_SENDER, GMAIL_PASSWORD.replace(" ", ""))
            server.send_message(message)
        print(f"[Email Success] SMTP accepted message for {to_email}")
        return True
    except Exception as exc:
        print(f"[Email Error] Failed to send to {to_email}: {exc}")
        return False


class _VisibleTextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self._ignored_depth = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() in {"script", "style", "noscript", "svg"}:
            self._ignored_depth += 1

    def handle_endtag(self, tag):
        if tag.lower() in {"script", "style", "noscript", "svg"} and self._ignored_depth:
            self._ignored_depth -= 1

    def handle_data(self, data):
        if not self._ignored_depth:
            value = " ".join(data.split())
            if value:
                self.parts.append(value)

    def text(self):
        return "\n".join(self.parts)


def _html_to_text(value):
    parser = _VisibleTextParser()
    parser.feed(value or "")
    return html.unescape(parser.text())


def extract_contacts(text):
    if not text:
        return {"emails": [], "linkedin": [], "phones": []}
    emails = list(dict.fromkeys(match.lower() for match in re.findall(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", text)))
    linkedin = list(dict.fromkeys(re.findall(r"linkedin\.com/in/[\w-]+", text, flags=re.I)))
    phones = list(dict.fromkeys(re.findall(r"(\+\d{1,3}[\s-]?\d{10})", text)))
    return {"emails": emails, "linkedin": linkedin, "phones": phones}


def _valid_recruiter_email(email):
    if not isinstance(email, str) or not re.fullmatch(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", email.strip()):
        return False
    local, domain = email.lower().strip().split("@", 1)
    placeholder_domains = {"company.com", "example.com", "email.com", "yourcompany.com", "placeholder.com", "sample.com", "acme.com", "xyz.com", "abc.com", "mail.com", "none.com"}
    blocked_locals = {"noreply", "no-reply", "donotreply", "do-not-reply", "privacy", "security", "abuse", "webmaster", "support", "help", "customer.service"}
    return domain not in placeholder_domains and local not in blocked_locals


def _normalize_url(url):
    if not url:
        return ""
    parsed = urllib.parse.urlsplit(url.strip())
    kept_query = [(key, value) for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True) if not key.lower().startswith("utm_") and key.lower() not in {"gclid", "fbclid", "trk", "trackingid"}]
    return urllib.parse.urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/") or "/", urllib.parse.urlencode(kept_query), ""))


def _job_fingerprint(item):
    canonical_url = _normalize_url(item.get("href") or item.get("url") or "")
    title = " ".join(str(item.get("job_title") or item.get("title") or "").lower().split())
    company = " ".join(str(item.get("company") or "").lower().split())
    return hashlib.sha256("|".join((canonical_url, title, company)).encode("utf-8")).hexdigest()[:24]


def _is_job_board_url(url):
    """Block low-value aggregators, while allowing official ATS/company pages."""
    blocked = ["indeed.", "naukri.", "glassdoor.", "shine.", "monster.", "timesjobs.", "placementindia.", "jobhai.", "freshersworld.", "upwork.", "freelancer.", "simplyhired.", "ziprecruiter.", "ambitionbox.", "fishbowlapp.", "wikipedia.", "enhancv.", "geeksforgeeks.", "study.com", "merriam-webster.", "reddit.", "instagram.", "twitter.", "mobile.twitter.", "friendsjob.", "rojgargroup."]
    return any(domain in (url or "").lower() for domain in blocked)


def _is_direct_apply_url(url):
    markers = ("jobs.lever.co", "boards.greenhouse.io", "job-boards.greenhouse.io", "myworkdayjobs.com", "jobs.smartrecruiters.com", "jobvite.com", "bamboohr.com/careers", "icims.com/jobs", "/careers/", "/jobs/")
    return any(marker in (url or "").lower() for marker in markers)


def _parse_serpapi_date_days(date_str):
    if not date_str:
        return None
    value = str(date_str).lower().strip()
    if "yesterday" in value:
        return 1.0
    if "today" in value or "hour" in value or "minute" in value:
        return 0.0
    match = re.match(r"(\d+)\s*(day|week|month|year)s?\s+ago", value)
    if not match:
        return None
    return float(int(match.group(1))) * {"day": 1, "week": 7, "month": 30, "year": 365}[match.group(2)]


def _linkedin_post_age_days(url):
    match = re.search(r"(?:activity|ugcPost)[-_](\d{15,25})", url or "")
    if not match:
        return None
    try:
        posted = datetime.fromtimestamp((int(match.group(1)) >> 22) / 1000, tz=timezone.utc)
        return (datetime.now(timezone.utc) - posted).total_seconds() / 86400
    except (OverflowError, OSError, ValueError):
        return None


def _parse_iso_datetime(value):
    if not value:
        return None
    candidate = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
        return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    except ValueError:
        try:
            return datetime.strptime(candidate[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return None


def _is_stale_post(url, date_str):
    age = _parse_serpapi_date_days(date_str)
    if age is None:
        age = _linkedin_post_age_days(url)
    return age is None or age > MAX_POST_AGE_DAYS


def _find_job_posting(value):
    if isinstance(value, list):
        for item in value:
            found = _find_job_posting(item)
            if found:
                return found
    elif isinstance(value, dict):
        value_type = value.get("@type")
        types = value_type if isinstance(value_type, list) else [value_type]
        if any(str(item).lower() == "jobposting" for item in types):
            return value
        for child in value.values():
            found = _find_job_posting(child)
            if found:
                return found
    return None


def _extract_location(job):
    if job.get("jobLocationType") == "TELECOMMUTE":
        return "Remote"
    locations = job.get("jobLocation") or []
    locations = [locations] if isinstance(locations, dict) else locations
    parts = []
    for location in locations:
        address = location.get("address", {}) if isinstance(location, dict) else {}
        if isinstance(address, str):
            parts.append(address)
        elif isinstance(address, dict):
            text = ", ".join(str(address.get(key)) for key in ("addressLocality", "addressRegion", "addressCountry") if address.get(key))
            if text:
                parts.append(text)
    return "; ".join(parts)


def _extract_job_json_ld(page_html):
    scripts = re.findall(r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>", page_html or "", flags=re.I | re.S)
    for script in scripts:
        try:
            job = _find_job_posting(json.loads(html.unescape(script.strip())))
        except (json.JSONDecodeError, TypeError):
            continue
        if not job:
            continue
        organization = job.get("hiringOrganization") or {}
        company = organization.get("name", "") if isinstance(organization, dict) else str(organization)
        return {"job_title": str(job.get("title") or ""), "company": str(company or ""), "location": _extract_location(job), "date_posted": str(job.get("datePosted") or ""), "valid_through": str(job.get("validThrough") or ""), "employment_type": job.get("employmentType") or "", "description": _html_to_text(str(job.get("description") or "")), "application_url": str(job.get("url") or "")}
    return {}


def _request_with_retries(url, *, params=None, timeout=25, max_retries=None):
    attempts = max_retries or HTTP_RETRIES
    last_error = None
    headers = {"User-Agent": "Mozilla/5.0 (compatible; HermesJobScout/2.0)"}
    for attempt in range(attempts):
        try:
            response = requests.get(url, params=params, headers=headers, timeout=timeout)
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_error = exc
            if attempt < attempts - 1:
                time.sleep(2 ** attempt)
    raise last_error


def enrich_post(post):
    enriched = dict(post)
    url = enriched.get("href", "")
    snippet = " ".join((enriched.get("title", ""), enriched.get("body", ""))).strip()
    enriched.update({"source_type": "direct_apply" if _is_direct_apply_url(url) else "public_web", "source_text": snippet, "source_emails": extract_contacts(snippet)["emails"], "page_fetched": False, "application_url": url})
    if "linkedin.com/" in url.lower():
        enriched["source_type"] = "linkedin_post"
        return enriched
    try:
        response = _request_with_retries(url, timeout=20)
        content_type = response.headers.get("Content-Type", "").lower()
        if "html" not in content_type and content_type:
            return enriched
        page_html = response.text[:1_000_000]
        structured = _extract_job_json_ld(page_html)
        source_text = "\n".join(part for part in (snippet, structured.get("description", ""), _html_to_text(page_html)) if part)
        enriched.update(structured)
        enriched["source_text"] = source_text[:MAX_SOURCE_CHARS]
        enriched["source_emails"] = [email for email in extract_contacts(enriched["source_text"])["emails"] if _valid_recruiter_email(email) and email != (CANDIDATE_EMAIL or "").lower()]
        enriched["page_fetched"] = True
        if structured.get("application_url"):
            enriched["application_url"] = urllib.parse.urljoin(url, structured["application_url"])
        if structured:
            enriched["source_type"] = "direct_apply"
    except Exception as exc:
        enriched["fetch_error"] = str(exc)[:160]
        print(f"[Fetch Warning] {url}: {exc}")
    return enriched


def _structured_freshness(post):
    now = datetime.now(timezone.utc)
    valid_through = _parse_iso_datetime(post.get("valid_through"))
    if valid_through and valid_through < now:
        return False, "application deadline has passed"
    if valid_through and valid_through >= now:
        return True, "application deadline is still open"
    posted = _parse_iso_datetime(post.get("date_posted"))
    if posted:
        age = (now - posted).total_seconds() / 86400
        limit = MAX_DIRECT_APPLY_AGE_DAYS if post.get("source_type") == "direct_apply" else MAX_POST_AGE_DAYS
        return age <= limit, f"structured posting age is {age:.1f} days"
    age = _parse_serpapi_date_days(post.get("date", ""))
    if age is None:
        age = _linkedin_post_age_days(post.get("href", ""))
    if age is not None:
        return age <= MAX_POST_AGE_DAYS, f"source posting age is {age:.1f} days"
    if post.get("source_type") == "direct_apply" and post.get("page_fetched"):
        return True, "live official application page; date unavailable"
    return False, "publication date could not be verified"


def _deterministic_rejection(post):
    title = str(post.get("job_title") or post.get("title") or "").lower()
    source = str(post.get("source_text") or post.get("body") or "").lower()
    if any(word in title for word in ("senior", "sr.", "lead ", "manager", "principal", "director", "trainer")):
        return "senior/lead/manager/trainer title"
    if re.search(r"\b(intern|internship)\b", title):
        return "internship rather than full-time analyst role"
    role_terms = ("data analyst", "business analyst", "research analyst", "bi analyst", "bi developer", "power bi", "reporting analyst", "mis analyst", "data operations")
    if not any(term in f"{title} {source[:1500]}" for term in role_terms):
        return "no target analyst role found"
    fresh, reason = _structured_freshness(post)
    return None if fresh else reason


def _serpapi_search(query):
    params = {"engine": "google", "q": query, "api_key": SERPAPI_KEY, "num": 10, "hl": "en", "gl": "in", "tbs": f"qdr:d{MAX_POST_AGE_DAYS}"}
    data = _request_with_retries("https://serpapi.com/search", params=params, timeout=30).json()
    if data.get("error"):
        raise RuntimeError(str(data["error"]))
    return data.get("organic_results", [])


def search_recruiter_posts():
    global LAST_SEARCH_STATS
    LAST_SEARCH_STATS = {"queries_attempted": 0, "queries_successful": 0, "query_errors": [], "results_discovered": 0}
    if not SERPAPI_KEY:
        print("[Search] SERPAPI_KEY not configured.")
        return []
    core_roles = '("Data Analyst" OR "Business Analyst" OR "BI Analyst" OR "Research Analyst")'
    all_roles = '("Data Analyst" OR "Business Analyst" OR "BI Analyst" OR "Research Analyst" OR "Reporting Analyst" OR "MIS Analyst" OR "Power BI Developer" OR "Data Operations Analyst")'
    locations = '("Delhi" OR "Noida" OR "Gurgaon" OR "Gurugram" OR "India" OR "Remote")'
    exclusions = '-senior -lead -manager -trainer -course -certification -internship -"open to work"'
    queries = [
        f'site:linkedin.com/posts {core_roles} {locations} ("hiring" OR "opening") ("email" OR "send resume" OR "share CV" OR "referral") {exclusions}',
        f'(site:jobs.lever.co OR site:boards.greenhouse.io OR site:job-boards.greenhouse.io OR site:myworkdayjobs.com OR site:jobs.smartrecruiters.com) {all_roles} {locations} {exclusions}',
    ]
    results = []
    quota_errors = 0
    for query in queries:
        LAST_SEARCH_STATS["queries_attempted"] += 1
        print(f"[Search] {query[:150]}...")
        try:
            items = _serpapi_search(query)
            LAST_SEARCH_STATS["queries_successful"] += 1
            for item in items:
                url = _normalize_url(item.get("link", ""))
                if not url or _is_job_board_url(url):
                    continue
                date_value = item.get("date", "")
                if "linkedin.com/" in url.lower() and _is_stale_post(url, date_value):
                    continue
                explicit_age = _parse_serpapi_date_days(date_value)
                if explicit_age is not None and explicit_age > MAX_POST_AGE_DAYS:
                    continue
                results.append({"title": item.get("title", ""), "body": item.get("snippet", ""), "href": url, "date": date_value})
        except Exception as exc:
            message = str(exc)[:180]
            LAST_SEARCH_STATS["query_errors"].append(message)
            if "quota" in message.lower() or "run out of searches" in message.lower():
                quota_errors += 1
            print(f"[Search Error] {message}")
    if quota_errors == len(queries):
        raise RuntimeError("SerpAPI monthly quota exhausted")
    if LAST_SEARCH_STATS["queries_successful"] == 0:
        raise RuntimeError("All discovery queries failed")
    unique, seen = [], set()
    for result in results:
        if result["href"] not in seen:
            seen.add(result["href"])
            unique.append(result)
    LAST_SEARCH_STATS["results_discovered"] = len(unique)
    return unique


def _call_llm_json(prompt, *, max_tokens=900, max_retries=3):
    if not client:
        return None, "no_client"
    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(model=NVIDIA_MODEL, messages=[{"role": "user", "content": prompt}], response_format={"type": "json_object"}, temperature=0.1, max_tokens=max_tokens)
            content = response.choices[0].message.content
            if not content or not content.strip():
                raise ValueError("empty response")
            parsed = json.loads(content)
            if not isinstance(parsed, dict):
                raise ValueError("response was not a JSON object")
            return parsed, "ok"
        except (json.JSONDecodeError, ValueError) as exc:
            if attempt == max_retries - 1:
                return None, f"json_error: {str(exc)[:100]}"
            time.sleep(2 ** attempt)
        except Exception as exc:
            message, lowered = str(exc), str(exc).lower()
            if "401" in message or "unauthorized" in lowered or "authentication failed" in lowered:
                return None, "auth_error"
            retryable = any(token in lowered for token in ("429", "rate limit", "503", "502", "504", "overloaded"))
            if retryable and attempt < max_retries - 1:
                time.sleep(3 * (2 ** attempt))
                continue
            return None, f"llm_error: {message[:100]}"
    return None, "llm_error"


def analyze_post_verbose(post, max_retries=3):
    """Classify a job. Verdict is `ok`, `apply`, `irrelevant`, or an error."""
    deterministic_reason = _deterministic_rejection(post)
    if deterministic_reason:
        print(f"  -> rejected before LLM: {deterministic_reason}")
        return None, "irrelevant"
    if not client:
        return None, "no_client"
    source_emails = [email.lower() for email in post.get("source_emails", []) if _valid_recruiter_email(email) and email.lower() != (CANDIDATE_EMAIL or "").lower()]
    source = str(post.get("source_text") or post.get("body") or "")[:MAX_SOURCE_CHARS]
    prompt = f"""You are a strict job eligibility classifier.
Candidate: around 1 year of experience; SQL, Python, Power BI, Tableau, Excel, ETL and research/reporting.
Targets: junior/full-time Data, Business, Research, BI, MIS, Reporting or Data Operations Analyst.
Locations: Delhi NCR, Noida, Gurgaon/Gurugram, India-remote or clearly remote.
Reject internships and roles requiring more than 2 years unless explicitly junior/fresher friendly.

Title: {post.get('job_title') or post.get('title', '')}
Company: {post.get('company', '')}
Location: {post.get('location', '')}
Source URL: {post.get('href', '')}
Application URL: {post.get('application_url', '')}
Verified source emails: {source_emails or 'none'}
Source type: {post.get('source_type', '')}
Content:
{source}

Decide whether this is a genuine, current, full-time target role. Reject advice, self-promotion,
training, scams, unrelated roles, senior roles and location mismatches. recruiter_email must be
null or one exact value from Verified source emails. Never create or infer an email.

Return ONLY JSON:
{{
  "is_relevant": true,
  "job_title": "exact role title",
  "company": "company or empty string",
  "location": "location or empty string",
  "recruiter_email": null,
  "reason": "one concise evidence-based sentence"
}}"""
    result, status = _call_llm_json(prompt, max_tokens=700, max_retries=max_retries)
    if result is None:
        return None, status
    if result.get("is_relevant") is not True:
        print(f"  -> rejected: {result.get('reason', 'irrelevant')}")
        return None, "irrelevant"
    selected = result.get("recruiter_email")
    selected = selected.lower().strip() if isinstance(selected, str) else None
    result["recruiter_email"] = selected if selected in source_emails else None
    result["job_id"] = _job_fingerprint({"href": post.get("application_url") or post.get("href"), "job_title": result.get("job_title"), "company": result.get("company")})
    result["application_url"] = post.get("application_url") or post.get("href")
    return result, "ok" if result["recruiter_email"] else "apply"


def compose_email(post, analysis, max_retries=3):
    source = str(post.get("source_text") or post.get("body") or "")[:6000]
    prompt = f"""Write a concise personalized cold application email for {CANDIDATE_NAME}.
Candidate: Research & Strategy Associate, around 1 year; SQL, Python, Power BI, Tableau, Excel,
ETL, data cleaning and KPI reporting. Portfolio: {CANDIDATE_PORTFOLIO}. LinkedIn: {CANDIDATE_LINKEDIN}.
GitHub: {CANDIDATE_GITHUB}. Phone: {CANDIDATE_PHONE}. Notice period: 1 month.

Verified job: {analysis.get('job_title', '')} at {analysis.get('company', '')}, {analysis.get('location', '')}
Source content:
{source}

Use plain text, 5-6 short paragraphs, professional and specific. Do not exaggerate or invent facts.
Mention the attached resume. Avoid "I came across your post" and "I am writing to express my interest".
Return ONLY JSON:
{{
  "email_subject": "Application for [Job Title] - {CANDIDATE_NAME}",
  "email_body": "complete plain-text email"
}}"""
    result, status = _call_llm_json(prompt, max_tokens=1100, max_retries=max_retries)
    if result is None:
        return None, status
    subject = " ".join(str(result.get("email_subject") or "").split())
    body = str(result.get("email_body") or "").strip()
    return ({"email_subject": subject, "email_body": body}, "ok") if subject and body else (None, "writer_missing_fields")


def schedule_follow_up(email, subject, body_text, job_id=None):
    """Store one evolving follow-up record, preventing duplicate chains."""
    created = datetime.now(timezone.utc)
    record = {"email": email, "subject": subject, "body": body_text, "job_id": job_id or "", "step": 1, "offsets_days": FOLLOW_UP_OFFSETS_DAYS, "created_at": created.timestamp(), "next_send": (created + timedelta(days=FOLLOW_UP_OFFSETS_DAYS[0])).timestamp()}
    pending = [item for item in load_follow_up() if not (item.get("email", "").lower() == email.lower() and item.get("job_id", "") == record["job_id"])]
    pending.append(record)
    save_follow_up(pending)


def process_pending_follow_ups():
    pending = load_follow_up()
    if not pending:
        return {"sent": 0, "failed": 0, "pending": 0}
    now, updated, sent, failed = datetime.now(timezone.utc).timestamp(), [], 0, 0
    for record in pending:
        if now < float(record.get("next_send", 0)):
            updated.append(record)
            continue
        step = int(record.get("step", 1))
        body = record.get("body", "") + f"\n\nFollow-up {step}: I wanted to check whether my application is under consideration."
        ok = SEND_EMAILS and send_email(record.get("email", ""), f"Re: {record.get('subject', '')}", body)
        if not ok:
            failed += 1
            updated.append(record)
            continue
        sent += 1
        offsets = record.get("offsets_days") or FOLLOW_UP_OFFSETS_DAYS
        if step < len(offsets):
            record["step"] = step + 1
            record["next_send"] = float(record.get("created_at", now)) + (int(offsets[step]) * 86400)
            updated.append(record)
    save_follow_up(updated)
    return {"sent": sent, "failed": failed, "pending": len(updated)}


def _record_history(history, analysis, post, route, email=""):
    history.append({"job_id": analysis["job_id"], "route": route, "email": email, "job_title": analysis.get("job_title", ""), "company": analysis.get("company", ""), "url": analysis.get("application_url") or post.get("href", ""), "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")})
    save_history(history)


def _send_apply_alert(analysis):
    return send_telegram("🎯 <b>Relevant job — apply directly</b>\n" f"💼 {html.escape(str(analysis.get('job_title', 'Analyst role')))}\n" f"🏢 {html.escape(str(analysis.get('company') or 'Company not stated'))}\n" f"📍 {html.escape(str(analysis.get('location') or 'Check listing'))}\n" f"🔗 <a href='{html.escape(str(analysis.get('application_url', '')), quote=True)}'>Open application</a>\n" f"ℹ️ {html.escape(str(analysis.get('reason', 'Relevant match')))}")


def _write_run_report(report):
    _save_json(RUN_REPORT_FILE, report)
    print("RUN_REPORT=" + json.dumps(report, sort_keys=True))


def main():
    print("=== Job Scout Agent Starting ===")
    required = [NVIDIA_API_KEY, SERPAPI_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID]
    if SEND_EMAILS:
        required.extend([GMAIL_SENDER, GMAIL_PASSWORD])
    if not all(required):
        print("[Fatal] One or more required environment variables are missing.")
        send_telegram("❗ Job Scout stopped: missing API keys or secrets.")
        raise SystemExit(1)
    if SEND_EMAILS and SMTP_PREFLIGHT and not smtp_preflight():
        send_telegram("🛑 <b>Job Scout stopped</b>\nGmail SMTP authentication failed.")
        raise SystemExit(1)
    follow_up_stats = process_pending_follow_ups()
    send_telegram("🤖 <b>Job Scout Started</b>\n\n" f"👤 {html.escape(CANDIDATE_NAME)}\n" "🎯 Data / Research / BI / Business Analyst\n" "📍 Delhi-NCR / India Remote\n" f"🧠 {html.escape(NVIDIA_MODEL)}")
    history = load_history()
    known_job_ids = {item.get("job_id") or _job_fingerprint(item) for item in history if isinstance(item, dict)}
    report = {"status": "healthy", "queries_attempted": 0, "queries_successful": 0, "query_errors": [], "results_discovered": 0, "pages_fetched": 0, "relevant_email_leads": 0, "relevant_apply_leads": 0, "emails_attempted": 0, "emails_accepted_by_smtp": 0, "duplicates": 0, "irrelevant": 0, "model_errors": 0, "follow_ups": follow_up_stats}
    try:
        posts = search_recruiter_posts()
    except Exception as exc:
        report["status"] = "failed"
        report["query_errors"] = [str(exc)[:180]]
        _write_run_report(report)
        send_telegram(f"🛑 <b>Job Scout stopped</b>\nDiscovery failed: {html.escape(str(exc)[:300])}")
        raise SystemExit(1)
    report.update(LAST_SEARCH_STATS)
    if report["query_errors"]:
        report["status"] = "degraded"
    apply_alerts_sent, fatal_model_error = 0, None
    for index, raw_post in enumerate(posts, 1):
        print(f"\n[{index}/{len(posts)}] {raw_post.get('href', '')}")
        post = enrich_post(raw_post)
        report["pages_fetched"] += int(bool(post.get("page_fetched")))
        analysis, verdict = analyze_post_verbose(post)
        if analysis is None:
            if verdict == "irrelevant":
                report["irrelevant"] += 1
            else:
                report["model_errors"] += 1
                if verdict == "auth_error":
                    fatal_model_error = verdict
                    break
            continue
        job_id = analysis["job_id"]
        if job_id in known_job_ids:
            report["duplicates"] += 1
            continue
        if verdict == "apply":
            report["relevant_apply_leads"] += 1
            if apply_alerts_sent < MAX_APPLY_ALERTS:
                _send_apply_alert(analysis)
                apply_alerts_sent += 1
            _record_history(history, analysis, post, "apply")
            known_job_ids.add(job_id)
            continue
        email = analysis["recruiter_email"]
        report["relevant_email_leads"] += 1
        written, writer_status = compose_email(post, analysis)
        if written is None:
            report["model_errors"] += 1
            print(f"[Writer Error] {writer_status}")
            continue
        report["emails_attempted"] += 1
        if not SEND_EMAILS:
            print(f"[Dry Run] Would email verified source address {email}")
            continue
        if send_email(email, written["email_subject"], written["email_body"]):
            report["emails_accepted_by_smtp"] += 1
            _record_history(history, analysis, post, "email", email)
            known_job_ids.add(job_id)
            schedule_follow_up(email, written["email_subject"], written["email_body"], job_id)
            send_telegram("🚀 <b>Email accepted by SMTP</b>\n" f"📧 <code>{html.escape(email)}</code>\n" f"💼 {html.escape(str(analysis.get('job_title', 'Analyst role')))}\n" f"🔗 <a href='{html.escape(str(analysis.get('application_url', '')), quote=True)}'>job</a>")
    if fatal_model_error:
        report["status"] = "failed"
    elif not posts or report["relevant_apply_leads"] + report["relevant_email_leads"] == 0:
        report["status"] = "no_matches" if report["status"] == "healthy" else report["status"]
    _write_run_report(report)
    summary = (f"✅ <b>Job Scout — {html.escape(report['status'])}</b>\n\n" f"🔎 Discovered: {report['results_discovered']}\n" f"📄 Full pages fetched: {report['pages_fetched']}\n" f"🎯 Direct-apply leads: {report['relevant_apply_leads']}\n" f"📧 Verified email leads: {report['relevant_email_leads']}\n" f"✉️ SMTP accepted: {report['emails_accepted_by_smtp']}\n" f"🔄 Duplicates: {report['duplicates']}\n" f"🚫 Irrelevant: {report['irrelevant']}\n" f"⚠️ Model errors: {report['model_errors']}")
    if report["query_errors"]:
        summary += "\n\n<b>Discovery warnings</b>\n" + "\n".join(f" • {html.escape(error)}" for error in report["query_errors"][:3])
    send_telegram(summary)
    print("=== Done ===")
    if fatal_model_error:
        raise SystemExit(1)


if __name__ == "__main__":
    main()


