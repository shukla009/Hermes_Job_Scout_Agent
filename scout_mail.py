"""Read-only mailbox discovery and strict recruitment-contact evidence.

Functions accept the scout module (or an object with GMAIL_SENDER,
GMAIL_PASSWORD and IMAP_CHECK). No credentials or message bodies are logged.
Alert results are unverified discovery hints, never application instructions.
"""
import imaplib
import re
from datetime import datetime, timezone
from email import message_from_bytes
from email.utils import parseaddr
from html.parser import HTMLParser
from urllib.parse import urlsplit

EMAIL = re.compile(r"[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)


def recruitment_contacts(post, candidate_email=""):
    """Only emails next to hiring CTAs in a fetched, accessible vacancy page."""
    if post.get("page_fetched") is not True or post.get("page_fetch_failed") or post.get("access_blocked"):
        return []
    text = str(post.get("source_text") or post.get("description") or post.get("body") or "")
    if not text or re.search(r"do not (?:send|email)|applications? (?:must|should) (?:only )?be (?:submitted|made).{0,40}(?:portal|website)|apply (?:only|exclusively) (?:via|through|on)", text, re.I):
        return []
    output = []
    for match in EMAIL.finditer(text):
        address = match.group().lower().rstrip(".")
        local = address.split("@", 1)[0]
        if address == candidate_email.lower() or re.search(r"support|privacy|accommod|accessibility|noreply|no.reply|vendor|campus|postmaster|mailer.daemon", local) or "naukri" in address.rsplit("@",1)[-1]:
            continue
        # Evaluate only the paragraph containing the address; a hiring headline
        # elsewhere on the page cannot legitimize a footer contact.
        start = max(text.rfind("\n\n", 0, match.start()), text.rfind("\n", 0, match.start())) + 1
        end = text.find("\n", match.end())
        paragraph = text[start:end if end >= 0 else len(text)]
        # Recruiters commonly put the application CTA immediately above the
        # address. Read adjacent lines only, never borrow a distant headline.
        previous_end = max(0, start - 1)
        previous_start = text.rfind("\n", 0, previous_end) + 1
        previous = text[previous_start:previous_end][-250:]
        if previous.strip() and not re.search(r"privacy|cookie|accommodation|accessibility|vendor|campus|support|copyright|legal|footer", previous, re.I):
            paragraph = previous + " " + paragraph
            start = previous_start
        nearby = paragraph[:750]
        if re.search(r"privacy|cookie|accommodation|accessibility|vendor|campus|support|copyright|legal|footer", nearby, re.I):
            continue
        if not re.search(r"(?:send|share|email|submit|forward).{0,65}(?:resume|résumé|cv|application)|(?:resume|résumé|cv|application).{0,65}(?:send|share|email|submit|forward)|apply.{0,30}(?:email|at)|recruit(?:er|ment).{0,30}(?:contact|email)", nearby, re.I):
            continue
        if address not in output:
            output.append(address)
    return output


def _credentials(agent):
    if not getattr(agent, "IMAP_CHECK", True):
        return None
    username = getattr(agent, "GMAIL_SENDER", "")
    password = getattr(agent, "GMAIL_PASSWORD", "")
    return (username, password.replace(" ", "")) if username and password else None


def _fetch(mailbox, uid):
    status, data = mailbox.fetch(uid, "(BODY.PEEK[])")
    if status != "OK":
        raise RuntimeError("Mailbox read failed")
    for item in data or []:
        if isinstance(item, tuple) and isinstance(item[1], bytes):
            return message_from_bytes(item[1])
    raise RuntimeError("Mailbox returned no message")


def _thread_ids(message):
    value = " ".join(str(message.get(h, "")) for h in ("Message-ID", "Original-Message-ID", "In-Reply-To", "References"))
    return set(re.findall(r"<[^<>\s]+>", value))


def mailbox_status(agent, record):
    """Exact sender AND thread identity required; uncertainty defers follow-up."""
    credentials = _credentials(agent)
    if not credentials:
        return "unavailable"
    recipient = parseaddr(str(record.get("email", "")))[1].lower()
    ids = set()
    for key in ("message_id", "original_message_id", "last_message_id", "references"):
        ids.update(re.findall(r"<[^<>\s]+>", str(record.get(key, ""))))
    if not recipient or not ids:
        return "unavailable"
    try:
        since = datetime.fromtimestamp(float(record.get("created_at", 0)), timezone.utc).strftime("%d-%b-%Y")
        with imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=30) as mailbox:
            mailbox.login(*credentials)
            if mailbox.select("INBOX", readonly=True)[0] != "OK":
                return "unavailable"
            status, data = mailbox.search(None, "SINCE", since)
            if status != "OK":
                return "unavailable"
            reply = False
            for uid in (data[0] or b"").split()[-100:]:
                message = _fetch(mailbox, uid)
                sender = parseaddr(str(message.get("From", "")))[1].lower()
                thread = bool(ids & _thread_ids(message))
                if sender == recipient and thread:
                    reply = True
                if re.match(r"(?:mailer-daemon|postmaster)@", sender):
                    delivery_recipient = False
                    related = thread
                    for part in message.walk():
                        related = related or bool(ids & _thread_ids(part))
                        final = str(part.get("Final-Recipient", "") or part.get("Original-Recipient", ""))
                        delivery_recipient = delivery_recipient or final.split(";")[-1].strip().lower() == recipient
                    if delivery_recipient and related:
                        return "bounce"
            return "reply" if reply else "none"
    except Exception:
        return "unavailable"


def sent_status(agent, message_id):
    """True confirmed sent, False absent, None mailbox/search uncertainty."""
    credentials = _credentials(agent)
    if not credentials or not re.fullmatch(r"<[^<>\s\"\r\n]+>", str(message_id)):
        return None
    try:
        with imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=30) as mailbox:
            mailbox.login(*credentials)
            if mailbox.select('"[Gmail]/Sent Mail"', readonly=True)[0] != "OK":
                return None
            status, data = mailbox.search(None, "HEADER", "Message-ID", '"' + message_id + '"')
            if status != "OK":
                return None
            found = (data[0] or b"").split()
            if len(found) > 100:
                return None
            for uid in found:
                if _fetch(mailbox, uid).get("Message-ID", "").strip() == message_id:
                    return True
            return False
    except Exception:
        return None


class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.href = None
        self.caption = []
    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.href = dict(attrs).get("href")
            self.caption = []
    def handle_data(self, data):
        if self.href:
            self.caption.append(data)
    def handle_endtag(self, tag):
        if tag == "a" and self.href:
            self.links.append((self.href, " ".join(self.caption)))
            self.href = None


def _decoded_parts(message):
    for part in message.walk():
        if part.get_content_type() in ("text/plain", "text/html") and part.get_content_disposition() != "attachment":
            payload = part.get_payload(decode=True) or b""
            try:
                yield part.get_content_type(), payload.decode(part.get_content_charset() or "utf-8", errors="replace")[:250000]
            except LookupError:
                yield part.get_content_type(), payload.decode("utf-8", errors="replace")[:250000]


def ingest_alert_emails(agent, label="HermesJobAlerts", max_messages=30, allowed_sender_domains=None):
    """Read ONLY an opted-in Gmail label; return unverified link hints.

    Operational failures return an empty list; callers must not equate this
    with a successful inbox check or use it to authorize follow-ups.
    """
    credentials = _credentials(agent)
    if not credentials or not re.fullmatch(r"[A-Za-z0-9 _/-]{1,80}", label):
        return []
    try:
        limit = min(30, max(0, int(max_messages)))
    except (ValueError, TypeError):
        return []
    if not limit:
        return []
    allowed = {"cutshort.io", "instahyre.com", "linkedin.com"}
    allowed.update(str(d).lower().strip() for d in (allowed_sender_domains or []))
    allowed = {d for d in allowed if re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", d) and "naukri" not in d}
    hints, seen = [], set()
    try:
        with imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=30) as mailbox:
            mailbox.login(*credentials)
            if mailbox.select('"' + label + '"', readonly=True)[0] != "OK":
                return []
            status, data = mailbox.search(None, "ALL")
            if status != "OK":
                return []
            for uid in (data[0] or b"").split()[-limit:]:
                message = _fetch(mailbox, uid)
                address = parseaddr(str(message.get("From", "")))[1].lower()
                domain = address.rsplit("@", 1)[-1]
                if not any(domain == d or domain.endswith("." + d) for d in allowed):
                    continue
                links = []
                for kind, content in _decoded_parts(message):
                    if kind == "text/html":
                        parser = _Links()
                        parser.feed(content)
                        links.extend(parser.links)
                    else:
                        links.extend((url.rstrip(".,);"), "") for url in re.findall(r"https://[^\s<>\"]+", content))
                for url, caption in links:
                    parsed = urlsplit(url)
                    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or "naukri" in parsed.hostname.lower() or url in seen:
                        continue
                    path = parsed.path.lower()
                    if re.search(r"unsubscribe|privacy|preferences|settings|login|signin", path):
                        continue
                    if not re.search(r"job|career|position|vacan|opening", path + " " + caption.lower()):
                        continue
                    seen.add(url)
                    hints.append({"url":url,"link":url,"title":caption[:200] or "Job-alert discovery link","source_text":caption[:500],"source_type":"job_alert","page_fetched":False,"alert_sender_domain":domain,"alert_message_id":str(message.get("Message-ID", ""))[:200],"unverified":True})
                    if len(hints) >= 100:
                        return hints
        return hints
    except Exception:
        return []
