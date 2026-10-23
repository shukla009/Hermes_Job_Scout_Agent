"""Read-only mailbox discovery and strict recruitment-contact evidence.

Functions accept the scout module (or an object with GMAIL_SENDER,
GMAIL_PASSWORD and IMAP_CHECK). No credentials or message bodies are logged.
Alert results are unverified discovery hints, never application instructions.
"""
import imaplib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from email import message_from_bytes
from email.utils import parseaddr, parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import urlsplit

EMAIL = re.compile(r"[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)

_LOCAL_CONTACT_EXCLUSIONS = re.compile(
    r"privacy|cookie|accommodation|accessibility|vendor|campus|support|copyright|legal|footer",
    re.I,
)
_AUTOMATIC_REPLY = re.compile(
    r"automatic(?: reply| response)?|auto[- ]?reply|we (?:have )?received your (?:email|application)|"
    r"application (?:was )?received|thank you for (?:applying|your application)|acknowledg(?:e|ement)",
    re.I,
)
_OUT_OF_OFFICE = re.compile(
    r"out of (?:the )?office|away from (?:the )?office|on (?:annual )?leave|vacation reply|"
    r"limited access to (?:my )?email|return(?:ing)? on",
    re.I,
)


def _contact_context(text, match):
    """Return bounded exact and normalized context centred on one address.

    Career pages frequently flatten a whole ATS description into one line.  A
    centred window therefore works where line/paragraph prefixes do not, while
    the smaller exclusion window prevents a distant hiring headline from
    legitimising a privacy or support address in the footer.
    """
    start = max(0, match.start() - 520)
    end = min(len(text), match.end() + 320)
    exact = text[start:end].strip()
    before = re.sub(r"\s+", " ", text[start:match.start()]).strip()[-420:]
    after = re.sub(r"\s+", " ", text[match.end():end]).strip()[:240]
    local_start = max(0, match.start() - 150)
    local_end = min(len(text), match.end() + 150)
    local = re.sub(r"\s+", " ", text[local_start:local_end])
    return exact, before, after, local


def _has_recruitment_cta(address, before, after):
    address_pattern = re.escape(address)
    joined = f"{before} {address} {after}"
    patterns = (
        rf"(?:send|share|email|submit|forward)\b.{{0,100}}?(?:resume|résumé|cv|application)\b.{{0,100}}?{address_pattern}",
        rf"(?:resume|résumé|cv|application)\b.{{0,100}}?(?:send|share|email|submit|forward)\b.{{0,100}}?{address_pattern}",
        rf"\bapply\b.{{0,120}}?(?:email|at|to|via|through)\b.{{0,80}}?{address_pattern}",
        rf"{address_pattern}.{{0,100}}?(?:resume|résumé|cv|application)\b",
        rf"\brecruit(?:er|ment)\b.{{0,100}}?(?:contact|email)\b.{{0,80}}?{address_pattern}",
    )
    return any(re.search(pattern, joined, re.I) for pattern in patterns)


def email_applications_forbidden(post):
    text = re.sub(r"\s+", " ", str(post.get("source_text") or post.get("description") or post.get("body") or ""))
    return bool(re.search(r"do not (?:send|email)|no (?:email|e-mail) applications|applications? (?:must|should) (?:only )?be (?:submitted|made).{0,80}(?:portal|website)|apply (?:only|exclusively) (?:via|through|on)|(?:portal|website)[- ]only applications", text, re.I))


def recruitment_contacts(post, candidate_email=""):
    """Only emails next to hiring CTAs in a fetched, accessible vacancy page."""
    if post.get("page_fetched") is not True or post.get("page_fetch_failed") or post.get("access_blocked"):
        return []
    text = str(post.get("source_text") or post.get("description") or post.get("body") or "")
    if not text or email_applications_forbidden(post):
        return []
    output, evidence = [], []
    for match in EMAIL.finditer(text):
        address = match.group().lower().rstrip(".")
        local = address.split("@", 1)[0]
        if address == candidate_email.lower() or re.search(r"support|privacy|accommod|accessibility|noreply|no.reply|vendor|campus|postmaster|mailer.daemon", local) or "naukri" in address.rsplit("@",1)[-1]:
            continue
        exact, before, after, local_context = _contact_context(text, match)
        if _LOCAL_CONTACT_EXCLUSIONS.search(local_context):
            continue
        if not _has_recruitment_cta(address, before, after):
            continue
        if address not in output:
            output.append(address)
            evidence.append({
                "email": address,
                "excerpt": exact,
                "source_url": str(post.get("application_url") or post.get("href") or post.get("url") or "")[:2000],
                "verified_at": datetime.now(timezone.utc).isoformat(),
            })
    if evidence:
        post["recruitment_contact_evidence"] = evidence
    else:
        post.pop("recruitment_contact_evidence", None)
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


def _fetch_uid_section(mailbox, uid, query, limit):
    status, data = mailbox.uid("fetch", uid, query)
    if status != "OK":
        raise RuntimeError("Mailbox UID read failed")
    chunks = []
    for item in data or []:
        if isinstance(item, tuple) and isinstance(item[1], bytes):
            chunks.append(item[1])
            if sum(len(chunk) for chunk in chunks) > limit:
                raise RuntimeError("Mailbox section exceeded bounded size")
    if not chunks:
        raise RuntimeError("Mailbox returned no UID section")
    return chunks


def _fetch_uid_headers(mailbox, uid):
    fields = ("FROM TO SUBJECT MESSAGE-ID ORIGINAL-MESSAGE-ID IN-REPLY-TO REFERENCES "
              "AUTO-SUBMITTED PRECEDENCE X-AUTOREPLY X-AUTORESPOND X-AUTO-RESPONSE-SUPPRESS CONTENT-TYPE")
    chunks = _fetch_uid_section(mailbox, uid, f"(BODY.PEEK[HEADER.FIELDS ({fields})])", 32768)
    return message_from_bytes(b"\r\n".join(chunks))


def _fetch_uid_preview(mailbox, uid, part="1", limit=8192):
    """Fetch one bounded MIME part, never the full message or attachments."""
    safe_part = part if part in {"1", "2"} else "1"
    mime_chunks = _fetch_uid_section(mailbox, uid, f"(BODY.PEEK[{safe_part}.MIME])", 8192)
    mime_raw = b"\r\n".join(mime_chunks)
    metadata = message_from_bytes(mime_raw)
    content_type = metadata.get_content_type().lower()
    disposition = str(metadata.get("Content-Disposition", "")).lower()
    allowed = content_type in {"text/plain", "text/html"}
    if safe_part == "2":
        allowed = content_type == "message/delivery-status"
    if not allowed or "attachment" in disposition:
        return ""
    chunks = _fetch_uid_section(mailbox, uid, f"(BODY.PEEK[{safe_part}]<0.{limit}>)", limit)
    body_raw = b"\r\n".join(chunks)
    if safe_part == "2":
        # message/delivery-status is structured header-like text rather than a
        # normal text MIME payload. Preserve only this bounded section so the
        # exact recipient and original message id can be verified.
        return body_raw.decode("utf-8", errors="replace")[:limit]
    raw = mime_raw + b"\r\n" + body_raw
    parsed = message_from_bytes(raw)
    decoded = " ".join(content for _, content in _decoded_parts(parsed))
    return (decoded or raw.decode("utf-8", errors="replace"))[:limit]


def _thread_ids(message):
    value = " ".join(str(message.get(h, "")) for h in ("Message-ID", "Original-Message-ID", "In-Reply-To", "References"))
    return set(re.findall(r"<[^<>\s]+>", value))


def _record_thread_key(record):
    recipient = parseaddr(str(record.get("email", "")))[1].lower()
    ids = set()
    for key in ("message_id", "original_message_id", "last_message_id", "references"):
        ids.update(re.findall(r"<[^<>\s]+>", str(record.get(key, ""))))
    return recipient, ids


def _bounce_matches(message, recipient, ids):
    sender = parseaddr(str(message.get("From", "")))[1].lower()
    if not re.match(r"(?:mailer-daemon|postmaster)@", sender):
        return False
    delivery_recipient = False
    related = bool(ids & _thread_ids(message))
    for part in message.walk():
        related = related or bool(ids & _thread_ids(part))
        final = str(part.get("Final-Recipient", "") or part.get("Original-Recipient", ""))
        delivery_recipient = delivery_recipient or final.split(";")[-1].strip().lower() == recipient
    return delivery_recipient and related


def mailbox_status(agent, record):
    """Exact sender AND thread identity required; uncertainty defers follow-up."""
    credentials = _credentials(agent)
    if not credentials:
        return "unavailable"
    recipient, ids = _record_thread_key(record)
    if not recipient or not ids:
        return "unavailable"
    cache = getattr(agent, "_MAILBOX_MESSAGE_STATUS", {})
    cached = [cache.get((recipient, ident)) for ident in ids]
    if cached and all(value is not None for value in cached):
        if "bounce" in cached:
            return "bounce"
        if "reply" in cached:
            return "reply"
        return "none"
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
                if _bounce_matches(message, recipient, ids):
                    return "bounce"
            return "reply" if reply else "none"
    except Exception:
        return "unavailable"


def _message_preview(message, limit=6000):
    chunks = []
    for _, content in _decoded_parts(message):
        chunks.append(re.sub(r"<[^>]{0,500}>", " ", content))
        if sum(len(chunk) for chunk in chunks) >= limit:
            break
    return re.sub(r"\s+", " ", " ".join(chunks))[:limit]


def _automatic_kind(message, preview=""):
    subject = str(message.get("Subject", ""))[:500]
    if not preview:
        preview = _message_preview(message)
    searchable = subject + " " + preview
    if _OUT_OF_OFFICE.search(searchable):
        return "out_of_office"
    auto_submitted = str(message.get("Auto-Submitted", "")).strip().lower()
    precedence = str(message.get("Precedence", "")).strip().lower()
    auto_header = bool(message.get("X-Autoreply") or message.get("X-Autorespond") or message.get("X-Auto-Response-Suppress"))
    if ((auto_submitted and auto_submitted != "no") or precedence in {"bulk", "junk", "list"}
            or auto_header or _AUTOMATIC_REPLY.search(searchable)):
        return "auto_ack"
    return "human"


def _uidvalidity(mailbox):
    try:
        _, values = mailbox.response("UIDVALIDITY")
        raw = values[0] if values else b""
        return str(raw.decode() if isinstance(raw, bytes) else raw)
    except Exception:
        return ""


def _monitored_threads(store):
    grouped = {}
    jobs = store.data.get("jobs", {})
    for send in store.data.get("sends", {}).values():
        if send.get("status") not in {"accepted", "uncertain"}:
            continue
        recipient = parseaddr(str(send.get("recipient", "")))[1].lower()
        message_id = str(send.get("message_id", ""))
        job_id = str(send.get("job_id", ""))
        if not recipient or not re.fullmatch(r"<[^<>\s\"\r\n]+>", message_id) or job_id not in jobs:
            continue
        key = (job_id, recipient)
        row = grouped.setdefault(key, {"job_id": job_id, "recipient": recipient, "ids": set(), "created_at": []})
        row["ids"].add(message_id)
        row["created_at"].append(str(send.get("created_at", "")))
    return list(grouped.values())


def _bounce_preview_matches(message, preview, recipient, ids):
    sender = parseaddr(str(message.get("From", "")))[1].lower()
    if not re.match(r"(?:mailer-daemon|postmaster)@", sender):
        return False
    related = bool(ids & _thread_ids(message)) or any(ident in preview for ident in ids)
    recipients = re.findall(r"(?:Final|Original)-Recipient\s*:\s*(?:rfc822\s*;\s*)?([^\s;<>]+@[^\s;<>]+)", preview, re.I)
    return related and recipient in {value.lower().rstrip(".,") for value in recipients}


def _record_bounce(agent, store, thread):
    now = datetime.now(timezone.utc).isoformat()
    recipient = thread["recipient"]
    store.data.setdefault("suppressed_recipients", {})[recipient] = {
        "reason": "bounce", "detected_at": now, "proof": "imap_exact_delivery_status"
    }
    record = store.data.get("jobs", {}).get(thread["job_id"])
    if record is not None:
        record["delivery_status"] = "bounced"
        record["bounced_at"] = now
    for send in store.data.get("sends", {}).values():
        if send.get("job_id") == thread["job_id"] and send.get("recipient", "").lower() == recipient:
            send["delivery_status"] = "bounced"
            send["bounced_at"] = now
    suppress = getattr(agent, "_suppress_email", None)
    if callable(suppress):
        try:
            suppress(recipient, "bounce")
        except Exception:
            # Durable scout state is authoritative. A legacy suppression-file
            # write failure must not prevent the exact bounce from being saved.
            pass


def monitor_replies(agent, store, max_messages=100):
    """Scan new INBOX UIDs once and persist only exact-thread human replies.

    The durable cursor advances only after a complete bounded scan.  Automatic
    replies, out-of-office notices and bounces are classified but never become
    a positive recruiter outcome.  A different sender on the same thread is
    deliberately ambiguous and requires manual review.
    """
    result = {"status": "not_checked", "checked": 0, "new_human_replies": 0,
              "auto_replies": 0, "out_of_office": 0, "bounces": 0, "ambiguous": 0}
    # Never let a prior/failed scan authorise a follow-up through stale "none"
    # results.  The cache is published only after a complete successful scan.
    agent._MAILBOX_MESSAGE_STATUS = {}
    credentials = _credentials(agent)
    threads = _monitored_threads(store)
    if not credentials:
        result["status"] = "unavailable"
        return result
    if not threads:
        result["status"] = "no_threads"
        return result
    try:
        limit = min(100, max(1, int(max_messages)))
    except (TypeError, ValueError):
        limit = 100
    cache = {}
    for thread in threads:
        for ident in thread["ids"]:
            cache[(thread["recipient"], ident)] = "none"
    cursor = store.data.setdefault("mailbox_monitor", {})
    original_cursor = dict(cursor)
    try:
        with imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=30) as mailbox:
            mailbox.login(*credentials)
            if mailbox.select("INBOX", readonly=True)[0] != "OK":
                result["status"] = "unavailable"
                return result
            validity = _uidvalidity(mailbox)
            last_uid = int(cursor.get("last_uid", 0) or 0) if cursor.get("uidvalidity", "") == validity else 0
            if last_uid:
                status, data = mailbox.uid("search", None, "UID", f"{last_uid + 1}:*")
            else:
                timestamps = []
                for thread in threads:
                    for value in thread["created_at"]:
                        try:
                            stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
                            timestamps.append(stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc))
                        except (TypeError, ValueError):
                            pass
                floor = datetime.now(timezone.utc) - timedelta(days=90)
                since = max(min(timestamps) if timestamps else floor, floor).strftime("%d-%b-%Y")
                status, data = mailbox.uid("search", None, "SINCE", since)
            if status != "OK":
                result["status"] = "unavailable"
                return result
            values = []
            for raw in (data[0] or b"").split():
                try:
                    value = int(raw)
                    if value > last_uid:
                        values.append(value)
                except (TypeError, ValueError):
                    continue
            values = sorted(set(values))[:limit]
            for value in values:
                uid = str(value).encode()
                message = _fetch_uid_headers(mailbox, uid)
                result["checked"] += 1
                message_ids = _thread_ids(message)
                matches = [thread for thread in threads if thread["ids"] & message_ids]
                sender = parseaddr(str(message.get("From", "")))[1].lower()
                if re.match(r"(?:mailer-daemon|postmaster)@", sender):
                    # Delivery-status fields live in the second MIME part. Fetch
                    # only that bounded part and require exact recipient/thread.
                    preview = _fetch_uid_preview(mailbox, uid, part="2")
                    bounce_matches = [thread for thread in threads
                                      if _bounce_preview_matches(message, preview, thread["recipient"], thread["ids"])]
                    if len(bounce_matches) == 1:
                        thread = bounce_matches[0]
                        result["bounces"] += 1
                        _record_bounce(agent, store, thread)
                        for ident in thread["ids"]:
                            cache[(thread["recipient"], ident)] = "bounce"
                    elif bounce_matches or matches:
                        result["ambiguous"] += 1
                    continue
                if len(matches) != 1:
                    if matches:
                        result["ambiguous"] += 1
                    continue
                thread = matches[0]
                recipient, ids = thread["recipient"], thread["ids"]
                if sender != recipient:
                    result["ambiguous"] += 1
                    continue
                # Only a single exact sender+thread match earns a bounded text
                # preview. Unrelated messages never have their bodies fetched.
                preview = _fetch_uid_preview(mailbox, uid, part="1")
                kind = _automatic_kind(message, preview)
                if kind == "out_of_office":
                    result["out_of_office"] += 1
                    continue
                if kind == "auto_ack":
                    result["auto_replies"] += 1
                    continue
                record = store.data["jobs"].get(thread["job_id"], {})
                if record.get("outcome") not in {"interview", "rejected", "notfit", "reply"}:
                    record["outcome"] = "reply"
                    record["outcome_at"] = datetime.now(timezone.utc).isoformat()
                    record["outcome_source"] = "imap_exact_thread"
                    result["new_human_replies"] += 1
                for ident in ids:
                    cache[(recipient, ident)] = "reply"
            if values:
                cursor["last_uid"] = values[-1]
            cursor["uidvalidity"] = validity
            result["status"] = "ok"
            if cursor != original_cursor and not store.save():
                result["status"] = "state_error"
            if result["status"] == "ok":
                agent._MAILBOX_MESSAGE_STATUS = cache
            return result
    except Exception:
        result["status"] = "unavailable"
        return result


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
        self.visible = []
        self.hidden = 0
    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        if tag == "a":
            self.href = dict(attrs).get("href")
            self.caption = []
    def handle_data(self, data):
        if self.hidden:
            return
        self.visible.append(data)
        if self.href:
            self.caption.append(data)
    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        if tag == "a" and self.href:
            self.links.append((self.href, " ".join(self.caption)))
            self.href = None


def alert_destinations(kind, content):
    """Extract explicit destinations before trackers; never import email dates as proof."""
    from scout_discovery import _unwrap, _url_block_reason
    links = []
    if kind == "text/html":
        parser = _Links()
        parser.feed(content)
        links.extend(parser.links)
        # HTML alerts sometimes display an employer's full vacancy URL while
        # the anchor href remains an opaque platform tracker. Treat that exact
        # visible URL as a public-discovery hint, never an ownership/date proof.
        visible = " ".join(parser.visible)[:32768]
        for candidate in re.findall(r"https://[^\s<>\"]+", visible)[:100]:
            candidate = candidate.rstrip(".,);]")
            if not _url_block_reason(candidate) and re.search(r"/(?:jobs?|careers?|positions?|requisitions?|apply)(?:/|[?#])", urlsplit(candidate).path, re.I):
                links.insert(0, (candidate, "Employer vacancy link"))
        # Some providers embed JobPosting data in an email. It supplies a URL
        # hint only: the employer vacancy still needs independent verification.
        from scout_sources import Page
        page = Page()
        page.feed(content)
        pending = []
        for script in page.scripts[:10]:
            try:
                pending.append(json.loads(script))
            except ValueError:
                continue
        inspected = 0
        while pending and inspected < 100:
            node = pending.pop()
            inspected += 1
            if isinstance(node, list):
                pending.extend(node[:20])
            elif isinstance(node, dict):
                types = node.get("@type") or []
                if "JobPosting" in ([types] if isinstance(types, str) else types):
                    url = node.get("url")
                    if isinstance(url, str):
                        links.insert(0, (url, str(node.get("title") or "Job opening")[:200]))
                pending.extend(v for v in node.values() if isinstance(v, (dict, list)))
    else:
        links.extend((url.rstrip(".,);"), "") for url in re.findall(r"https://[^\s<>\"]+", content))
    resolved, seen = [], set()
    for url, caption in links[:100]:
        try:
            target = _unwrap(url)
        except (ValueError, TypeError):
            continue
        # Keep original platform links for diagnostics, but do not unwrap to an
        # unsafe/excluded destination and then fetch its outer tracker instead.
        if target != url and _url_block_reason(target):
            continue
        if target not in seen:
            resolved.append((target, caption))
            seen.add(target)
    def rank(row):
        host = urlsplit(row[0]).hostname or ""
        return int(bool(re.search(r"linkedin|indeed|foundit|cutshort|instahyre|wellfound|hirist|iimjobs|internshala|unstop|track|click|email", host, re.I)))
    return sorted(resolved, key=rank)


def _decoded_parts(message):
    for part in message.walk():
        if part.get_content_type() in ("text/plain", "text/html") and part.get_content_disposition() != "attachment":
            payload = part.get_payload(decode=True) or b""
            try:
                yield part.get_content_type(), payload.decode(part.get_content_charset() or "utf-8", errors="replace")[:250000]
            except LookupError:
                yield part.get_content_type(), payload.decode("utf-8", errors="replace")[:250000]


def _authenticated_alert_sender(message, allowed):
    address = parseaddr(str(message.get("From", "")))[1].lower()
    domain = address.rsplit("@", 1)[-1]
    roots = [root for root in allowed if domain == root or domain.endswith("." + root)]
    if not roots:
        return None
    # Gmail prepends its own Authentication-Results. Never accept a result
    # supplied by another authserv-id, or a later forged duplicate header.
    results = message.get_all("Authentication-Results", [])
    if not results:
        return None
    result = re.sub(r"\s+", " ", str(results[0])).lower()
    if not re.match(r"^\s*mx\.google\.com\s*;", result):
        return None
    for mechanism, clause in re.findall(r"(?:^|;)\s*(dkim|spf)\s*=\s*pass\b([^;]*)", result):
        identity = re.search(r"\b(?:header\.d|header\.i|smtp\.mailfrom)\s*=\s*([^\s;]+)", clause)
        if not identity:
            continue
        authenticated_domain = identity.group(1).strip('<>"').rsplit("@", 1)[-1].rstrip(".")
        if any(authenticated_domain == root or authenticated_domain.endswith("." + root) for root in roots):
            return {"domain": domain, "mechanism": mechanism, "authserv_id": "mx.google.com"}
    return None


def _alert_text_parts(mailbox, uid, root_type):
    """Read at most two bounded text parts; inspect MIME before reading payload."""
    pending = ["1", "2"] if root_type.startswith("multipart/") else ["1"]
    inspected = 0
    yielded = 0
    read_metadata = False
    while pending and inspected < 8 and yielded < 2:
        part = pending.pop(0)
        inspected += 1
        try:
            mime = b"\r\n".join(_fetch_uid_section(mailbox, uid, f"(BODY.PEEK[{part}.MIME])", 8192))
        except RuntimeError:
            # A MIME part absent in this message is not an inbox failure.
            continue
        metadata = message_from_bytes(mime)
        read_metadata = True
        kind = metadata.get_content_type().lower()
        if "attachment" in str(metadata.get("Content-Disposition", "")).lower() or metadata.get_filename():
            continue
        if kind.startswith("multipart/") and part.count(".") < 2:
            pending[0:0] = [part + ".1", part + ".2"]
            continue
        if kind not in {"text/plain", "text/html"}:
            continue
        raw = b"\r\n".join(_fetch_uid_section(mailbox, uid, f"(BODY.PEEK[{part}]<0.32768>)", 32768))
        parsed = message_from_bytes(mime + b"\r\n" + raw)
        for content_kind, content in _decoded_parts(parsed):
            yielded += 1
            yield content_kind, content[:32768]
    if not read_metadata:
        raise RuntimeError("Alert MIME metadata unavailable")


def ingest_alert_batch(agent, label="HermesJobAlerts", max_messages=30, allowed_sender_domains=None, cursor=None):
    """Read an opted-in label with authenticated senders and a durable UID cursor.

    Returned links remain discovery hints. Received time is never a vacancy's
    publication date, and bodies/attachments are never persisted in results.
    """
    original_cursor = dict(cursor or {})
    report = {"status": "unavailable", "hints": [], "checked": 0, "authenticated": 0,
              "auth_rejected": 0, "cursor": original_cursor, "label": label}
    credentials = _credentials(agent)
    if not credentials:
        report["status"] = "disabled"
        return report
    if not isinstance(label, str) or not re.fullmatch(r"[A-Za-z0-9 _/-]{1,80}", label):
        report["status"] = "invalid_config"
        return report
    try:
        limit = min(30, max(0, int(max_messages)))
    except (ValueError, TypeError):
        report["status"] = "invalid_config"
        return report
    if not limit:
        report["status"] = "disabled"
        return report
    allowed = {"cutshort.io", "instahyre.com", "linkedin.com", "indeed.com", "indeedemail.com", "foundit.in",
               "hirist.com", "hirist.tech", "iimjobs.com", "wellfound.com", "internshala.com", "unstop.com"}
    allowed.update(value.strip().lower() for value in os.getenv("SCOUT_ALERT_SENDER_DOMAINS", "").split(","))
    allowed.update(str(d).lower().strip() for d in (allowed_sender_domains or []))
    allowed = {d for d in allowed if re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", d) and "naukri" not in d}
    hints, seen = [], set()
    try:
        with imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=30) as mailbox:
            mailbox.login(*credentials)
            if mailbox.select('"' + label + '"', readonly=True)[0] != "OK":
                return report
            validity_response = mailbox.response("UIDVALIDITY")[1]
            validity = (validity_response[0] or b"").decode("ascii", errors="ignore")
            if not validity.isdigit():
                return report
            last_uid = int(original_cursor.get("last_uid", 0)) if str(original_cursor.get("uidvalidity", "")) == validity else 0
            status, data = mailbox.uid("search", None, "UID", f"{last_uid + 1}:*") if last_uid else mailbox.uid("search", None, "ALL")
            if status != "OK":
                return report
            values = sorted(int(value) for value in (data[0] or b"").split() if value.isdigit() and int(value) > last_uid)
            # Bootstrap only the latest bounded batch; later runs consume oldest
            # unseen UIDs first so a busy label cannot permanently starve alerts.
            values = values[:limit] if last_uid else values[-limit:]
            for uid in values:
                fields = "FROM MESSAGE-ID DATE AUTHENTICATION-RESULTS CONTENT-TYPE SUBJECT"
                raw_headers = b"\r\n".join(_fetch_uid_section(mailbox, str(uid), f"(BODY.PEEK[HEADER.FIELDS ({fields})])", 32768))
                message = message_from_bytes(raw_headers)
                report["checked"] += 1
                proof = _authenticated_alert_sender(message, allowed)
                if not proof:
                    report["auth_rejected"] += 1
                    continue
                report["authenticated"] += 1
                links = []
                for kind, content in _alert_text_parts(mailbox, str(uid), message.get_content_type()):
                    links.extend(alert_destinations(kind, content))
                for url, caption in links:
                    parsed = urlsplit(url)
                    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or "naukri" in parsed.hostname.lower() or url in seen:
                        continue
                    path = parsed.path.lower()
                    if re.search(r"unsubscribe|privacy|preferences|settings|login|signin", path + " " + parsed.query.lower() + " " + caption.lower()):
                        continue
                    # Preserve platform tracking URLs; the caller resolves them
                    # through its bounded public-URL fetcher before qualification.
                    context = path + " " + parsed.query.lower() + " " + caption.lower()
                    if not re.search(r"job|career|position|vacan|opening|analyst|research|reporting|intelligence|dashboard", context) and not re.search(r"track|click|redirect|url|link|email", parsed.hostname.lower() + path):
                        continue
                    seen.add(url)
                    received = ""
                    try:
                        received = parsedate_to_datetime(str(message.get("Date", ""))).isoformat()
                    except (ValueError, TypeError, OverflowError):
                        pass
                    hints.append({"url":url,"link":url,"title":caption[:200] or "Job-alert discovery link","source_text":caption[:500],"source_type":"job_alert","page_fetched":False,"alert_sender_domain":proof["domain"],"alert_authentication":proof,"alert_message_id":str(message.get("Message-ID", ""))[:200],"alert_received_at":received,"unverified":True})
                    if len(hints) >= 100:
                        break
                if len(hints) >= 100:
                    break
            processed_uid = uid if values else last_uid
            next_cursor = {"uidvalidity": validity, "last_uid": processed_uid}
        report["cursor"] = next_cursor
        report["hints"] = hints
        report["status"] = "ok" if report["authenticated"] else "authentication_rejected" if report["auth_rejected"] else "empty"
        return report
    except Exception:
        return report


def ingest_alert_emails(agent, label="HermesJobAlerts", max_messages=30, allowed_sender_domains=None):
    """Compatibility wrapper; use ingest_alert_batch for diagnostics/cursors."""
    return ingest_alert_batch(agent, label, max_messages, allowed_sender_domains)["hints"]
