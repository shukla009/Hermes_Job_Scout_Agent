import argparse
import hashlib
import html
import imaplib
import json
import os
import re
import smtplib
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from email import encoders
from email import message_from_bytes
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
SUPPRESSION_FILE = "suppressed_emails.json"
EMPLOYER_REGISTRY_FILE = os.getenv("EMPLOYER_REGISTRY_FILE", "config/employers.json")
RESUME_PATH = "Shivank_Shukla_Data_Analyst.pdf"
NVIDIA_MODEL = os.getenv("NVIDIA_MODEL") or "nvidia/nemotron-3-super-120b-a12b"
MAX_POST_AGE_DAYS = int(os.getenv("MAX_POST_AGE_DAYS", "4"))
MAX_POST_AGE_HOURS = int(os.getenv("MAX_POST_AGE_HOURS", "48"))
MAX_DIRECT_APPLY_AGE_DAYS = int(os.getenv("MAX_DIRECT_APPLY_AGE_DAYS", "21"))
MAX_SOURCE_CHARS = int(os.getenv("MAX_SOURCE_CHARS", "12000"))
MAX_APPLY_ALERTS = int(os.getenv("MAX_APPLY_ALERTS", "5"))
MAX_EMAILS_PER_RUN = int(os.getenv("MAX_EMAILS_PER_RUN", "3"))
MAX_EMAILS_PER_DAY = int(os.getenv("MAX_EMAILS_PER_DAY", "6"))
MAX_FOLLOWUPS_PER_RUN = int(os.getenv("MAX_FOLLOWUPS_PER_RUN", "2"))
RECIPIENT_COOLDOWN_DAYS = int(os.getenv("RECIPIENT_COOLDOWN_DAYS", "30"))
MIN_APPLY_SCORE = int(os.getenv("MIN_APPLY_SCORE", "54"))
MIN_EMAIL_SCORE = int(os.getenv("MIN_EMAIL_SCORE", "58"))
MAX_CANDIDATES_PER_RUN = int(os.getenv("MAX_CANDIDATES_PER_RUN", "50"))
HTTP_RETRIES = max(1, int(os.getenv("HTTP_RETRIES", "2")))
SEND_EMAILS = os.getenv("SEND_EMAILS", "true").strip().lower() in {"1", "true", "yes", "on"}
SMTP_PREFLIGHT = os.getenv("SMTP_PREFLIGHT", "true").strip().lower() in {"1", "true", "yes", "on"}
USE_LLM_REVIEW = os.getenv("USE_LLM_REVIEW", "false").strip().lower() in {"1", "true", "yes", "on"}
IMAP_CHECK = os.getenv("IMAP_CHECK", "true").strip().lower() in {"1", "true", "yes", "on"}


def _parse_follow_up_offsets(raw):
    try:
        values = sorted({int(value.strip()) for value in raw.split(",") if int(value.strip()) > 0})
        return values or [4, 9]
    except (TypeError, ValueError):
        return [4, 9]


FOLLOW_UP_OFFSETS_DAYS = _parse_follow_up_offsets(os.getenv("FOLLOW_UP_OFFSETS_DAYS", "4,9"))
client = OpenAI(base_url="https://integrate.api.nvidia.com/v1", api_key=NVIDIA_API_KEY) if NVIDIA_API_KEY else None
ACTIVE_BUDGET = None
ACTIVE_STORE = None
ACTIVE_MESSAGE_ID = None
ACTIVE_REFERENCES = None
OUTBOUND_READY = False
LAST_SEARCH_STATS = {"queries_attempted": 0, "queries_successful": 0, "query_errors": [], "results_discovered": 0, "source_stats": {}}

ROLE_TERMS = (
    "data analyst", "business analyst", "bi analyst", "business intelligence analyst",
    "research analyst", "market research analyst", "reporting analyst", "mis analyst",
    "insights analyst", "dashboard analyst", "data operations analyst", "data quality analyst",
    "commercial analyst", "operations analyst", "product analyst", "strategy analyst",
    "analytics analyst", "business analytics", "data analytics", "risk analyst", "marketing analyst",
    "reporting executive", "research associate", "junior etl",
)
TARGET_SKILLS = ("sql", "power bi", "tableau", "excel", "python", "etl", "dashboard", "reporting", "data cleaning", "market research")
TARGET_LOCATIONS = ("delhi", "new delhi", "noida", "gurgaon", "gurugram", "ncr", "remote")


def _target_location(location):
    value = str(location or "").lower()
    return not value or any(term in value for term in TARGET_LOCATIONS)


def _load_json_list(path, label):
    if not os.path.exists(path):
        return []
    try:
        from scout_storage import load_json
        data = load_json(path)
        if not isinstance(data, list):
            raise ValueError("expected a JSON list")
        return data
    except Exception as exc:
        print(f"[{label} Warning] {path} unreadable ({exc}) - starting with empty state")
        return []


def _save_json(path, data):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
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


def load_suppressions():
    return _load_json_list(SUPPRESSION_FILE, "Suppression")


def _suppress_email(email, reason):
    email = str(email or "").lower().strip()
    if not email:
        return
    records = [item for item in load_suppressions() if item.get("email", "").lower() != email]
    records.append({"email": email, "reason": reason, "timestamp": datetime.now(timezone.utc).isoformat()})
    _save_json(SUPPRESSION_FILE, records)


def _is_suppressed(email):
    value = str(email or "").lower().strip()
    return any(item.get("email", "").lower() == value for item in load_suppressions())


def send_telegram(message, reply_markup=None):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[Telegram] Missing token or chat ID.")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML", "disable_web_page_preview": True}
    if reply_markup:
        data["reply_markup"] = reply_markup
    payload = json.dumps(data).encode("utf-8")
    request = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(request, timeout=15)
        print("[Telegram] Alert sent successfully.")
        return True
    except Exception as exc:
        print(f"[Telegram Error] {type(exc).__name__}")
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
        print(f"[SMTP Error] Authentication failed: {type(exc).__name__}")
        return False


def send_email(to_email, subject, body_text, *, attach_resume=True):
    if not GMAIL_SENDER or not GMAIL_PASSWORD:
        print("[Email Error] Missing Gmail credentials.")
        return False
    if not _valid_recruiter_email(to_email):
        print("[Email Error] Refusing invalid recipient")
        return False
    subject = " ".join(str(subject).split())
    message = MIMEMultipart()
    message["From"] = f"{CANDIDATE_NAME} <{GMAIL_SENDER}>"
    message["To"] = to_email
    message["Subject"] = subject
    if ACTIVE_MESSAGE_ID:
        message["Message-ID"] = ACTIVE_MESSAGE_ID
    if ACTIVE_REFERENCES:
        message["In-Reply-To"] = ACTIVE_REFERENCES
        message["References"] = ACTIVE_REFERENCES
    message.attach(MIMEText(body_text, "plain", "utf-8"))
    if attach_resume and not os.path.exists(RESUME_PATH):
        print(f"[Email Error] Resume not found at {RESUME_PATH} - aborting send")
        return False
    try:
        if attach_resume:
            with open(RESUME_PATH, "rb") as file:
                attachment = MIMEBase("application", "pdf")
                attachment.set_payload(file.read())
            encoders.encode_base64(attachment)
            attachment.add_header("Content-Disposition", f'attachment; filename="{os.path.basename(RESUME_PATH)}"')
            message.attach(attachment)
    except Exception as exc:
        print(f"[Email Error] Could not attach resume: {type(exc).__name__}")
        return False
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
            server.login(GMAIL_SENDER, GMAIL_PASSWORD.replace(" ", ""))
            server.send_message(message)
        print("[Email Success] SMTP accepted application message")
        return True
    except Exception as exc:
        print(f"[Email Error] Send outcome uncertain: {type(exc).__name__}")
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
    return hashlib.sha256((canonical_url or "|".join((title, company))).encode("utf-8")).hexdigest()[:24]


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
    return age is None or age * 24 > MAX_POST_AGE_HOURS


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


def _request_with_retries(url, *, params=None, timeout=25, max_retries=None, method="GET", **kwargs):
    if ACTIVE_BUDGET is not None:
        return ACTIVE_BUDGET.request(url, params=params, timeout=timeout, max_retries=max_retries, method=method, **kwargs)
    from scout_persistence import RuntimeBudget
    return RuntimeBudget().request(url, params=params, timeout=timeout, max_retries=max_retries, method=method, **kwargs)


def enrich_post(post):
    enriched = dict(post)
    if enriched.get("source_type") == "direct_apply" and enriched.get("page_fetched") and not enriched.get("needs_reverification"):
        return enriched
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
        if enriched.get("needs_reverification"):
            # Only structured vacancy evidence can refresh a cached listing.
            enriched["active_listing"] = bool(structured)
            enriched["reverification_failed"] = not bool(structured)
            enriched.pop("needs_reverification", None)
        if structured.get("application_url"):
            enriched["application_url"] = urllib.parse.urljoin(url, structured["application_url"])
        if structured:
            enriched["source_type"] = "direct_apply"
    except Exception as exc:
        enriched["active_listing"] = False
        enriched["reverification_failed"] = True
        enriched["fetch_error"] = str(exc)[:160]
        print(f"[Fetch Warning] {urllib.parse.urlsplit(url).hostname}: {type(exc).__name__}")
    return enriched


def _structured_freshness(post):
    now = datetime.now(timezone.utc)
    valid_through = _parse_iso_datetime(post.get("valid_through"))
    if valid_through and valid_through < now:
        return False, "application deadline has passed"
    originals = [_parse_iso_datetime(post.get(key)) for key in ("first_published", "date_posted", "posted_at", "published_at")]
    posted = min((stamp for stamp in originals if stamp), default=None)
    if posted:
        age = (now - posted).total_seconds() / 86400
        return 0 <= age * 24 <= MAX_POST_AGE_HOURS, f"original posting age is {age * 24:.1f} hours"
    age = _parse_serpapi_date_days(post.get("date", ""))
    if age is None:
        age = _linkedin_post_age_days(post.get("href", ""))
    if age is not None:
        return 0 <= age * 24 <= MAX_POST_AGE_HOURS, f"source posting age is {age:.1f} days"
    return False, "publication date could not be verified"


def _deterministic_rejection(post):
    title = str(post.get("job_title") or post.get("title") or "").lower()
    source = str(post.get("source_text") or post.get("body") or "").lower()
    combined = f"{title} {source[:6000]}"
    if any(word in title for word in ("senior", "sr.", "lead ", "manager", "principal", "director", "head ", "trainer")):
        return "senior/lead/manager/trainer title"
    if re.search(r"\b(intern|internship)\b", title):
        return "internship rather than full-time analyst role"
    employment = str(post.get("employment_type") or "").lower()
    if any(term in employment for term in ("part time", "part-time", "contract", "temporary", "intern")):
        return "not a permanent full-time role"
    if re.search(r"\b(night shift|rotational shifts?|graveyard shift|us shift)\b", combined):
        return "explicit night or rotational shift"
    if not any(term in combined for term in ROLE_TERMS):
        return "no target analyst role found"
    title_range = re.search(r"\b(\d{1,2})\s*(?:[-–]|to)\s*\d{1,2}\s*(?:\+\s*)?(?:years?|yrs?)\b", title)
    if title_range and int(title_range.group(1)) > 2:
        return "requires more than two years of experience"
    experience_values = [int(value) for value in re.findall(r"\b(\d{1,2})\s*(?:\+\s*)?(?:years?|yrs?)\b", combined)]
    if experience_values and min(experience_values) > 2 and not re.search(r"\b(0\s*[-–]\s*3|1\s*[-–]\s*3|fresher|entry.level|junior)\b", combined):
        return "requires more than two years of experience"
    location = str(post.get("location") or "").lower()
    if not _target_location(location):
        return "location is outside Delhi-NCR/India-remote targets"
    salary_lpa = [float(value) for value in re.findall(r"(?:₹|inr)?\s*(\d+(?:\.\d+)?)\s*(?:lpa|lakhs?)", combined)]
    if salary_lpa and max(salary_lpa) < 6.5:
        return "explicit salary is below 6.5 LPA"
    fresh, reason = _structured_freshness(post)
    return None if fresh else reason


def _fit_score(post):
    """Transparent 0-100 fit score. Hard gates always run before this score."""
    title = str(post.get("job_title") or post.get("title") or "").lower()
    source = str(post.get("source_text") or post.get("body") or "").lower()
    location = str(post.get("location") or "").lower()
    score = 0
    if any(term in title for term in ROLE_TERMS):
        score += 38
    elif any(term in source[:2000] for term in ROLE_TERMS):
        score += 28
    skill_count = sum(1 for skill in TARGET_SKILLS if skill in source)
    score += min(25, skill_count * 5)
    if location and _target_location(location):
        score += 15
    elif not location:
        score += 7
    if re.search(r"\b(junior|entry.level|fresher|0\s*[-–]\s*2|1\s*[-–]\s*2)\b", f"{title} {source[:4000]}"):
        score += 10
    else:
        score += 5
    score += 7 if post.get("source_type") == "direct_apply" else 4
    score += 5 if post.get("source_emails") else 0
    return min(100, score)


def _serpapi_search(query):
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=2)
    custom_range = f"cdr:1,cd_min:{start.strftime('%m/%d/%Y')},cd_max:{today.strftime('%m/%d/%Y')}"
    params = {"engine": "google", "q": query, "api_key": SERPAPI_KEY, "num": 10, "hl": "en", "gl": "in", "tbs": custom_range}
    data = _request_with_retries("https://serpapi.com/search", params=params, timeout=30).json()
    if data.get("error"):
        if "hasn't returned any results" in str(data["error"]).lower():
            return []
        raise RuntimeError(str(data["error"]))
    return data.get("organic_results", [])


def _load_employer_registry():
    try:
        with open(EMPLOYER_REGISTRY_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
        return data if isinstance(data, list) else []
    except Exception as exc:
        print(f"[Registry Warning] {exc}")
        return []


def _looks_like_target_title(title):
    value = str(title or "").lower()
    return any(term in value for term in ROLE_TERMS) and not re.search(r"\b(intern|senior|sr\.?|lead|manager|principal|director|head)\b", value)


def _ats_post(title, company, location, description, url, date_posted="", employment_type=""):
    source = _html_to_text(description)
    emails = [value for value in extract_contacts(source)["emails"] if _valid_recruiter_email(value) and value != (CANDIDATE_EMAIL or "").lower()]
    return {
        "title": title, "job_title": title, "company": company, "location": location,
        "body": source[:1500], "source_text": source[:MAX_SOURCE_CHARS], "source_emails": emails,
        "href": _normalize_url(url), "application_url": _normalize_url(url), "date_posted": date_posted,
        "employment_type": employment_type, "source_type": "direct_apply", "page_fetched": True,
        "active_listing": True,
    }


def _greenhouse_jobs(employer):
    slug = employer["slug"]
    data = _request_with_retries(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", params={"content": "true"}, timeout=30).json()
    jobs = []
    for item in data.get("jobs", []):
        if not _looks_like_target_title(item.get("title")):
            continue
        jobs.append(_ats_post(item.get("title", ""), employer.get("name", slug), (item.get("location") or {}).get("name", ""), item.get("content", ""), item.get("absolute_url", ""), item.get("updated_at", "")))
    return jobs


def _lever_jobs(employer):
    slug = employer["slug"]
    data = _request_with_retries(f"https://api.lever.co/v0/postings/{slug}", params={"mode": "json"}, timeout=30).json()
    jobs = []
    for item in data if isinstance(data, list) else []:
        if not _looks_like_target_title(item.get("text")):
            continue
        categories = item.get("categories") or {}
        list_content = "\n".join(str(value.get("content") or "") for value in item.get("lists", []) if isinstance(value, dict))
        description = "\n".join(str(item.get(key) or "") for key in ("description", "descriptionPlain", "additional", "additionalPlain", "requirements")) + "\n" + list_content
        locations = categories.get("allLocations") or [categories.get("location", "")]
        location = ", ".join(str(value) for value in locations if value)
        if str(item.get("workplaceType") or "").lower() == "remote":
            location = f"Remote; {location}"
        jobs.append(_ats_post(item.get("text", ""), employer.get("name", slug), location, description, item.get("hostedUrl") or item.get("applyUrl", ""), employment_type=categories.get("commitment", "")))
    return jobs


def _smartrecruiters_jobs(employer):
    slug = employer["slug"]
    listing = _request_with_retries(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings", params={"limit": 100, "q": "analyst", "country": "in"}, timeout=30).json()
    jobs = []
    for item in listing.get("content", []):
        if not _looks_like_target_title(item.get("name")):
            continue
        location_data = item.get("location") or {}
        location = location_data.get("fullLocation") or location_data.get("city") or ""
        if not _target_location(location):
            continue
        detail = _request_with_retries(item.get("ref"), timeout=25).json()
        sections = ((detail.get("jobAd") or {}).get("sections") or {})
        description = "\n".join(str(section.get("text") or "") for section in sections.values() if isinstance(section, dict))
        jobs.append(_ats_post(item.get("name", ""), (item.get("company") or {}).get("name", employer.get("name", slug)), location, description, detail.get("applyUrl", ""), item.get("releasedDate", ""), (item.get("typeOfEmployment") or {}).get("label", "")))
    return jobs


def discover_ats_jobs():
    from scout_sources import discover_sources, prioritize_registry
    from scout_runtime import read_state
    previous = read_state("state/sources.json", dict, {})
    registry = prioritize_registry(_load_employer_registry(), ACTIVE_STORE.data.get("jobs", {}) if ACTIVE_STORE else {})
    jobs, stats, updated = discover_sources(registry, _request_with_retries, previous)
    _save_json("state/sources.json", updated)
    return jobs, stats


def _rotating_linkedin_query():
    role_groups = [
        '("Data Analyst" OR "BI Analyst" OR "Reporting Analyst")',
        '("Business Analyst" OR "Operations Analyst" OR "Product Analyst")',
        '("Research Analyst" OR "Market Research Analyst" OR "Insights Analyst")',
        '("MIS Analyst" OR "Data Operations Analyst" OR "Data Quality Analyst")',
    ]
    roles = role_groups[(datetime.now(timezone.utc).timetuple().tm_yday * 3 + datetime.now(timezone.utc).hour // 6) % len(role_groups)]
    return f'site:linkedin.com/posts {roles} (Delhi OR Noida OR Gurgaon OR Gurugram OR Remote) (hiring OR opening) (email OR "send resume" OR "share CV") -intern -senior -manager -course'


def search_recruiter_posts():
    global LAST_SEARCH_STATS
    LAST_SEARCH_STATS = {"queries_attempted": 0, "queries_successful": 0, "query_errors": [], "results_discovered": 0, "source_stats": {}}
    results, source_stats = discover_ats_jobs()
    LAST_SEARCH_STATS["source_stats"].update(source_stats)
    if os.getenv("ENABLE_JOB_ALERT_EMAILS", "false").lower() == "true":
        from scout_mail import ingest_alert_batch
        from scout_discovery import resolve_alert_hints
        cursor = ACTIVE_STORE.data.get("mailbox_alerts", {}) if ACTIVE_STORE else {}
        batch = ingest_alert_batch(__import__(__name__), max_messages=20, cursor=cursor)
        backlog = ACTIVE_STORE.data.get("alert_hint_queue", []) if ACTIVE_STORE else []
        hints = list({hint["url"]: hint for hint in [*backlog, *batch.get("hints", [])] if hint.get("url")}.values())[:100]
        alert_jobs, alert_stats = resolve_alert_hints(hints, _request_with_retries)
        pending = alert_stats.pop("pending_hints", [])
        if ACTIVE_STORE:
            ACTIVE_STORE.data["alert_hint_queue"] = pending
        if ACTIVE_STORE and batch.get("status") in {"ok", "empty", "authentication_rejected"}:
            ACTIVE_STORE.data["mailbox_alerts"] = batch.get("cursor", cursor)
        if batch.get("status") in {"unavailable", "invalid_config"}:
            alert_stats["status"] = "error"
        if batch.get("status") == "disabled":
            alert_stats["status"] = "disabled"
        LAST_SEARCH_STATS["source_stats"]["gmail_alert_label"] = {
            **alert_stats, "mailbox_status": batch.get("status"),
            "checked": batch.get("checked", 0), "auth_rejected": batch.get("auth_rejected", 0)}
        # Interleave the alert channel, rather than dropping it behind large feeds.
        results = [row for pair in zip(results, alert_jobs) for row in pair] + results[len(alert_jobs):] + alert_jobs[len(results):]
    if SERPAPI_KEY and os.getenv("ENABLE_PAID_SEARCH", "false").lower() == "true":
        query = _rotating_linkedin_query()
        LAST_SEARCH_STATS["queries_attempted"] += 1
        print(f"[Search] {query[:150]}...")
        try:
            items = _serpapi_search(query)
            LAST_SEARCH_STATS["queries_successful"] += 1
            LAST_SEARCH_STATS["source_stats"]["linkedin_public_search"] = {"status": "ok", "leads": len(items)}
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
            LAST_SEARCH_STATS["source_stats"]["linkedin_public_search"] = {"status": "error", "leads": 0, "error": message}
            print(f"[Search Error] {message}")
    else:
        LAST_SEARCH_STATS["source_stats"]["linkedin_public_search"] = {"status": "disabled", "leads": 0}
    unique, seen = [], set()
    for result in results:
        if result["href"] not in seen:
            seen.add(result["href"])
            unique.append(result)
    LAST_SEARCH_STATS["results_discovered"] = len(unique)
    return unique[:MAX_CANDIDATES_PER_RUN]


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
    from scout_matching import evaluate_job
    from scout_mail import recruitment_contacts
    checked = evaluate_job(post)
    fresh, freshness_reason = _structured_freshness(post)
    if not checked["eligible"] or not fresh:
        post["rejection_reason"] = checked["reason"] if not checked["eligible"] else freshness_reason
        return None, "irrelevant"
    contacts = recruitment_contacts(post, CANDIDATE_EMAIL) if checked["email_safe"] else []
    if "unavailable" in freshness_reason:
        checked["warnings"].append("Original posting date unavailable; not verified latest")
        checked["email_safe"] = False
        contacts = []
    source_emails = [x for x in contacts if _valid_recruiter_email(x)]
    review_suffix = (
        "; Review required: " + ", ".join(checked.get("review_reasons", [])[:2])
        if checked.get("review_required") else ""
    )
    result = {
        "is_relevant": True, "job_title": post.get("job_title") or post.get("title", ""),
        "company": post.get("company", ""), "location": post.get("location", ""),
        "application_url": post.get("application_url") or post.get("href", ""),
        "recruiter_email": source_emails[0] if source_emails else None,
        "reason": checked["reason"] + "; " + freshness_reason + review_suffix,
        "fit_score": checked["score"], "family": checked["family"],
        "tier": checked["tier"], "warnings": checked["warnings"],
        "evidence": checked["evidence"], "email_safe": checked["email_safe"],
        "source_id": post.get("source_id", ""),
        "source_name": post.get("source_name", ""),
        "matched_skills": checked["matched_skills"],
        "requirements": checked.get("requirements", {}),
        "review_required": checked.get("review_required", False),
        "review_reasons": checked.get("review_reasons", []),
        "risk_assessment": checked.get("risk_assessment", {}),
        "risk_flags": checked.get("risk_flags", []),
        "score_breakdown": checked.get("score_breakdown", {}),
        "score_kind": checked.get("score_kind", "deterministic_fit_points_not_probability"),
        "date_posted": post.get("date_posted") or post.get("posted_at") or post.get("published_at"),
        "vacancy_reference": str(post.get("vacancy_reference") or post.get("source_id") or "")[:100],
        "junior_experience_exception": checked.get("junior_experience_exception", False),
    }
    result["job_id"] = _job_fingerprint({"href": result["application_url"], "job_title": result["job_title"], "company": result["company"]})
    return result, "ok" if result["recruiter_email"] else "apply"


def _email_footer():
    phone = re.sub(r"[\s-]", "", CANDIDATE_PHONE)
    if re.fullmatch(r"\+91\d{10}", phone):
        phone = "+91-" + phone[3:]
    return (f"Portfolio: {CANDIDATE_PORTFOLIO}\nLinkedIn: {CANDIDATE_LINKEDIN}\n"
            f"Email: {CANDIDATE_EMAIL}\nPhone: {phone}\n\nBest regards,\n{CANDIDATE_NAME}")


def _fallback_email(analysis, post=None):
    from scout_matching import load_profile
    from scout_personalization import personalize_email
    title = " ".join(str(analysis.get("job_title") or "Analyst").split())[:140]
    company = " ".join(str(analysis.get("company") or "your team").split())[:120]
    profile = load_profile()
    family = analysis.get("family", "data")
    post = post or {}
    # Metadata/snippets or failed fetches cannot establish a responsibility.
    verified_page = (post.get("page_fetched") is True and not any(post.get(key) for key in
                     ("page_fetch_failed", "access_blocked", "reverification_failed")))
    text = str(post.get("description") or post.get("source_text") or "")[:30000] if verified_page else ""
    tailored = personalize_email(text, profile, family)
    points = tailored["highlights"]
    current = next((row for row in profile.get("experience", []) if row.get("kind") == "full_time" and not row.get("end")), None)
    graduate = any(row.get("completed") and row.get("degree") == "BSc Data Analytics" for row in profile.get("education", []))
    current_background = ("I'm a BSc Data Analytics graduate" if graduate else "My background combines research and analytical reporting")
    current_background += (f" currently working in research and strategy at {current['employer']}.\n\n" if current else ".\n\n")
    skill_line = ("Relevant skills: " + ", ".join(tailored["skills"]) + ".\n\n") if tailored["skills"] else ""
    alignment = tailored["alignment"] + "\n\n" if tailored["alignment"] else ""
    example = ("Portfolio example: " + tailored["portfolio_example"] + " (see Portfolio below).\n\n") if tailored.get("portfolio_example") else ""
    reference = re.sub(r"[\r\n]+", " ", str(analysis.get("vacancy_reference") or ""))[:100]
    reference_label = (" (reference " + reference + ")") if reference and not reference.startswith("https://") else ""
    vacancy_line = ("Vacancy link: " + str(analysis["application_url"]) + "\n\n") if analysis.get("application_url") else ""
    body = (
        f"Hello Hiring Team,\n\nI'm applying for the {title} position{reference_label} at {company}. "
        + current_background + alignment + skill_line
        + "Experience highlights:\n"
        + "\n".join("- " + str(point) for point in points[:3])
        + "\n\n" + example + "My resume is attached. Would you consider my profile for this opening?\n\n"
        + vacancy_line
        + _email_footer()
    )
    return {"email_subject": f"Application: {title} - {CANDIDATE_NAME}", "email_body": body}


def compose_email(post, analysis, max_retries=3):
    # The zero-cost production path never invokes a metered inference service.
    return _fallback_email(analysis, post), "fallback"


def schedule_follow_up(email, subject, body_text, job_id=None):
    """Store one evolving follow-up record, preventing duplicate chains."""
    created = datetime.now(timezone.utc)
    record = {"email": email, "subject": subject, "body": body_text, "job_id": job_id or "", "step": 1, "offsets_days": FOLLOW_UP_OFFSETS_DAYS, "created_at": created.timestamp(), "next_send": (created + timedelta(days=FOLLOW_UP_OFFSETS_DAYS[0])).timestamp()}
    record["message_id"] = ACTIVE_MESSAGE_ID or ""
    record["original_message_id"] = ACTIVE_MESSAGE_ID or ""
    pending = [item for item in load_follow_up() if not (item.get("email", "").lower() == email.lower() and item.get("job_id", "") == record["job_id"])]
    pending.append(record)
    save_follow_up(pending)


def _mailbox_status(record):
    from scout_mail import mailbox_status
    import sys
    return mailbox_status(sys.modules[__name__], record)


def process_pending_follow_ups(max_sends=None):
    global ACTIVE_MESSAGE_ID, ACTIVE_REFERENCES
    pending = load_follow_up()
    if not pending:
        return {"sent": 0, "failed": 0, "pending": 0, "sent_records": []}
    max_sends = max(0, min(2, MAX_FOLLOWUPS_PER_RUN, MAX_FOLLOWUPS_PER_RUN if max_sends is None else int(max_sends)))
    now, updated, sent, failed, sent_records = datetime.now(timezone.utc).timestamp(), [], 0, 0, []
    checked = 0
    for record_index, record in enumerate(pending):
        try:
            due = now >= float(record.get("next_send", now + 86400))
            step = int(record.get("step", 1))
        except (TypeError, ValueError):
            updated.append(record)
            continue
        if not due or checked >= max_sends or not SEND_EMAILS or not ACTIVE_STORE:
            updated.append(record)
            continue
        original = record.get("original_message_id") or record.get("message_id")
        if not original or not re.fullmatch(r"<[^<>\s]+>", original):
            updated.append(record)
            continue
        job = ACTIVE_STORE.data.get("jobs", {}).get(record.get("job_id", ""), {}) if hasattr(ACTIVE_STORE, "data") else {}
        if (job.get("outcome") in {"notfit", "rejected", "reply", "interview"}
                or job.get("submitted") or job.get("delivery_status") == "bounced"
                or job.get("active_listing") is False
                or str(job.get("exclusion_reason", "")).lower() in {"vacancy closed", "application deadline expired"}):
            continue
        checked += 1
        mailbox_status = _mailbox_status(record)
        if mailbox_status == "reply":
            continue
        if mailbox_status == "bounce":
            _suppress_email(record.get("email", ""), "bounce")
            continue
        if mailbox_status != "none" or _is_suppressed(record.get("email", "")):
            updated.append(record)
            continue
        if hasattr(ACTIVE_STORE, 'data'):
            vacancy = job.get("apply_url") or job.get("application_url") or job.get("url")
            if not vacancy:
                updated.append(record)
                continue
            try:
                page = enrich_post({"href": vacancy, "source_type": "direct_apply", "needs_reverification": True})
                expiry = _parse_iso_datetime(page.get("valid_through"))
                if page.get("reverification_failed"):
                    updated.append(record)
                    continue
                if page.get("active_listing") is False or (expiry and expiry <= datetime.now(timezone.utc)):
                    continue
            except Exception:
                updated.append(record)
                continue
        intent = ACTIVE_STORE.reserve_send(record.get("job_id", ""), record.get("email", ""), step=f"followup-{step}")
        if not intent:
            updated.append(record)
            continue
        ACTIVE_MESSAGE_ID = intent["message_id"]
        ACTIVE_REFERENCES = original
        body = "Hello,\n\nI wanted to follow up on my application below. I remain interested and would welcome an opportunity to discuss the role.\n\n" + _email_footer()
        subject = record.get("subject", "")
        if not subject.lower().startswith("re:"):
            subject = "Re: " + subject
        try:
            ok = send_email(record.get("email", ""), subject, body, attach_resume=False)
        except Exception:
            ok = False
        persisted = ACTIVE_STORE.finish_send(intent["id"], "accepted" if ok else "uncertain")
        ACTIVE_MESSAGE_ID = ACTIVE_REFERENCES = None
        if not ok:
            failed += 1
            updated.append(record)
            if not persisted:
                updated.extend(pending[record_index+1:])
                break
            continue
        sent += 1
        sent_records.append({"email": record.get("email", ""), "job_id": record.get("job_id", ""), "step": step, "message_id": intent["message_id"]})
        record["last_message_id"] = intent["message_id"]
        offsets = record.get("offsets_days") or FOLLOW_UP_OFFSETS_DAYS
        if step < len(offsets):
            record["step"] = step + 1
            record["next_send"] = float(record.get("created_at", now)) + (int(offsets[step]) * 86400)
            updated.append(record)
        if not persisted:
            # Accepted-but-unpersisted is quarantined in the send ledger; stop
            # processing other threads rather than risk an additional send.
            updated.extend(pending[record_index+1:])
            break
    save_follow_up(updated)
    return {"sent": sent, "failed": failed, "pending": len(updated), "sent_records": sent_records}


def _parse_history_time(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        try:
            return datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return None


def _emails_sent_today(history):
    today = datetime.now(timezone.utc).date()
    return sum(1 for item in history if item.get("route") in {"email", "follow_up"} and (_parse_history_time(item.get("timestamp")) or datetime.min.replace(tzinfo=timezone.utc)).date() == today)


def _recipient_in_cooldown(history, email):
    cutoff = datetime.now(timezone.utc) - timedelta(days=RECIPIENT_COOLDOWN_DAYS)
    for item in history:
        if item.get("route") == "email" and item.get("email", "").lower() == str(email).lower():
            sent_at = _parse_history_time(item.get("timestamp"))
            if sent_at and sent_at >= cutoff:
                return True
    return False


def _record_history(history, analysis, post, route, email=""):
    history.append({"job_id": analysis["job_id"], "route": route, "email": email, "job_title": analysis.get("job_title", ""), "company": analysis.get("company", ""), "url": analysis.get("application_url") or post.get("href", ""), "fit_score": analysis.get("fit_score"), "timestamp": datetime.now(timezone.utc).isoformat()})
    save_history(history)


def _send_apply_alert(analysis):
    from scout_state import callback_data
    warnings = "; ".join(analysis.get("warnings", [])[:3])
    evidence = "\n".join("• " + html.escape(str(x)) for x in analysis.get("evidence", [])[:2])
    message = (
        "🎯 <b>" + ("Stretch opportunity" if analysis.get("tier") == "stretch" else "Relevant job") + "</b>\n"
        + html.escape(str(analysis.get("job_title", "Analyst"))) + "\n"
        + html.escape(str(analysis.get("company", ""))) + " — " + html.escape(str(analysis.get("location", ""))) + "\n"
        + f"Fit: {analysis.get('fit_score', 0)}/100\n" + evidence + "\n"
        + ("⚠️ " + html.escape(warnings) + "\n" if warnings else "")
        + "<a href='" + html.escape(str(analysis.get("application_url", "")), quote=True) + "'>Apply on official site</a>"
    )
    if analysis.get("manual_optional"):
        message = ("<b>Optional phone action — not an approval</b>\n" + message +
                   "\nAutomatic applications continue independently. Only contact someone who knows your work.\n" +
                   "<b>Referral draft:</b>\n" + html.escape(str(analysis.get("manual_draft", ""))[:850]))
    if os.getenv("TELEGRAM_TRACKING_READY", "false").lower() != "true":
        return send_telegram(message)
    pairs = (
                    (("Applied", "applied"), ("Skipped", "skipped"), ("Referral requested", "referral")),
                    (("Reply", "reply"), ("Interview", "interview"), ("Rejected", "rejected"))) if analysis.get("manual_optional") else (
                    (("Applied", "applied"), ("Not suitable", "notfit"), ("Snooze", "snooze")),
                    (("Reply", "reply"), ("Interview", "interview"), ("Rejected", "rejected")))
    buttons = [[{"text": label, "callback_data": callback_data(analysis["job_id"], action, TELEGRAM_BOT_TOKEN)}
                for label, action in pair] for pair in pairs]
    return send_telegram(message, {"inline_keyboard": buttons})


def _write_run_report(report):
    _save_json(RUN_REPORT_FILE, report)
    print("RUN_REPORT=" + json.dumps(report, sort_keys=True))


def main(*, dry_run=False, notify=True):
    import sys
    from scout_runtime import run
    return run(sys.modules[__name__], dry_run=dry_run, notify=notify)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Zero-cost public-careers job scout")
    parser.add_argument("--dry-run", action="store_true", help="Discover and rank without sending email or job alerts")
    parser.add_argument("--no-notify", action="store_true", help="Do not send Telegram messages")
    args = parser.parse_args()
    main(dry_run=args.dry_run, notify=not args.no_notify)


