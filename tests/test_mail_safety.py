from email.message import EmailMessage
from types import SimpleNamespace
from datetime import datetime, timezone
import scout_mail

AGENT = SimpleNamespace(GMAIL_SENDER="candidate@gmail.com", GMAIL_PASSWORD="secret", IMAP_CHECK=True)


class Mailbox:
    messages = []
    selected = []
    queries = []
    def __init__(self, *args, **kwargs):
        pass
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def login(self, *args):
        pass
    def select(self, folder, readonly=False):
        assert readonly
        self.selected.append(folder)
        return "OK", []
    def search(self, *args):
        self.queries.append(args)
        return "OK", [b" ".join(str(i).encode() for i in range(len(self.messages)))]
    def fetch(self, uid, query):
        assert query == "(BODY.PEEK[])"
        return "OK", [(b"", self.messages[int(uid)].as_bytes())]


def message(sender="recruiter@example.com", references="<job@scout>", mid="<reply@example>", body="hello"):
    value = EmailMessage()
    value["From"] = sender
    value["Message-ID"] = mid
    if references:
        value["References"] = references
    value.set_content(body)
    return value


def setup(monkeypatch, messages):
    Mailbox.messages = messages
    Mailbox.selected = []
    Mailbox.queries = []
    monkeypatch.setattr(scout_mail.imaplib, "IMAP4_SSL", Mailbox)


def test_contacts_require_page_and_local_hiring_context():
    good = {"page_fetched":True,"source_text":"Hiring Research Analyst. Send your CV to recruiter@example.com"}
    assert scout_mail.recruitment_contacts(good) == ["recruiter@example.com"]
    assert scout_mail.recruitment_contacts(dict(good, page_fetched=False)) == []
    assert scout_mail.recruitment_contacts({"page_fetched":True,"source_text":"Hiring analysts\nPrivacy: privacy@example.com\nRecruitment support support@example.com"}) == []
    assert scout_mail.recruitment_contacts(dict(good, source_text=good["source_text"] + " Apply only through website")) == []
    assert scout_mail.recruitment_contacts(good, "recruiter@example.com") == []


def test_contacts_use_email_centered_context_and_record_exact_evidence():
    prefix = "Role requirements and responsibilities. " * 80
    post = {"page_fetched": True, "application_url": "https://careers.example/job/1",
            "source_text": prefix + "Interested candidates can share their CV "
                           "for consideration at talent@example.com today."}
    assert scout_mail.recruitment_contacts(post) == ["talent@example.com"]
    proof = post["recruitment_contact_evidence"][0]
    assert proof["email"] == "talent@example.com"
    assert proof["source_url"] == "https://careers.example/job/1"
    assert "share their CV" in proof["excerpt"] and "talent@example.com" in proof["excerpt"]
    assert len(proof["excerpt"]) <= 900


def test_contacts_handle_split_text_but_reject_footer_and_portal_only():
    split = {"page_fetched": True, "source_text":
             "Interested candidates\ncan share\ntheir resume\nat\njobs@example.com"}
    assert scout_mail.recruitment_contacts(split) == ["jobs@example.com"]
    footer = {"page_fetched": True, "source_text":
              "Send your CV for the analyst opening. Privacy contact: legal@example.com"}
    assert scout_mail.recruitment_contacts(footer) == []
    portal = dict(split, source_text=split["source_text"] + " Applications must only be submitted through our portal")
    assert scout_mail.recruitment_contacts(portal) == []


def test_reply_exact_sender_and_thread(monkeypatch):
    record = {"email":"recruiter@example.com","message_id":"<job@scout>","created_at":1}
    setup(monkeypatch, [message('"recruiter@example.com" <attacker@example.com>'), message(references="<different@job>")])
    assert scout_mail.mailbox_status(AGENT, record) == "none"
    setup(monkeypatch, [message()])
    assert scout_mail.mailbox_status(AGENT, record) == "reply"
    assert len(Mailbox.queries) == 1 and Mailbox.selected == ["INBOX"]
    assert scout_mail.mailbox_status(AGENT, {"email":"recruiter@example.com"}) == "unavailable"


def test_mailbox_unavailable_fail_closed(monkeypatch):
    monkeypatch.setattr(scout_mail.imaplib, "IMAP4_SSL", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("secret")))
    assert scout_mail.mailbox_status(AGENT, {"email":"r@example.com","message_id":"<job@scout>"}) == "unavailable"
    assert scout_mail.sent_status(AGENT, "<job@scout>") is None


def test_sent_exact_identity(monkeypatch):
    setup(monkeypatch, [message(mid="<job@scout>")])
    assert scout_mail.sent_status(AGENT, "<job@scout>") is True
    assert Mailbox.selected == ['"[Gmail]/Sent Mail"']
    assert scout_mail.sent_status(AGENT, "<other@scout>") is False
    assert scout_mail.sent_status(AGENT, '<evil"@scout>') is None


def test_alert_label_sender_whitelist_and_hints(monkeypatch):
    value = message(sender="jobs@instahyre.com", references="", body="https://instahyre.com/job/123 https://www.naukri.com/job/456 https://instahyre.com/unsubscribe")
    spoof = message(sender='"jobs@instahyre.com" <attacker@example.com>', body="https://evil.example/job/123")
    setup(monkeypatch, [value, spoof])
    hints = scout_mail.ingest_alert_emails(AGENT)
    assert len(hints) == 1 and hints[0]["url"] == "https://instahyre.com/job/123"
    assert not hints[0]["page_fetched"] and hints[0]["unverified"]
    assert Mailbox.selected == ['"HermesJobAlerts"']
    assert scout_mail.ingest_alert_emails(AGENT, label='bad"label') == []


def test_alert_html_and_configured_employer(monkeypatch):
    value = message(sender="talent@careers.example.com")
    value.add_alternative('<a href="https://careers.example.com/positions/123">Research Analyst job</a>', subtype="html")
    setup(monkeypatch, [value])
    assert scout_mail.ingest_alert_emails(AGENT) == []
    hints = scout_mail.ingest_alert_emails(AGENT, allowed_sender_domains=["example.com"])
    assert len(hints) == 1 and hints[0]["title"] == "Research Analyst job"


def test_zero_limit_does_not_read_label(monkeypatch):
    setup(monkeypatch, [message()])
    assert scout_mail.ingest_alert_emails(AGENT, max_messages=0) == []
    assert Mailbox.selected == []


def test_bounce_requires_structured_recipient_and_thread(monkeypatch):
    from email.mime.multipart import MIMEMultipart
    from email.message import Message
    bounced = MIMEMultipart("report", report_type="delivery-status")
    bounced["From"] = "mailer-daemon@gmail.com"
    delivery = Message()
    delivery.set_type("message/delivery-status")
    fields = Message()
    fields["Final-Recipient"] = "rfc822; recruiter@example.com"
    fields["Original-Message-ID"] = "<job@scout>"
    delivery.set_payload([fields])
    bounced.attach(delivery)
    # Header references on delivery reports or attached originals must identify
    # the exact outgoing message; recipient text alone is insufficient.
    bounced["References"] = "<job@scout>"
    setup(monkeypatch, [bounced])
    record = {"email":"recruiter@example.com","message_id":"<job@scout>","created_at":1}
    assert scout_mail.mailbox_status(AGENT, record) == "bounce"
    del bounced["References"]
    setup(monkeypatch, [bounced])
    assert scout_mail.mailbox_status(AGENT, record) == "bounce"
    del fields["Original-Message-ID"]
    setup(monkeypatch, [bounced])
    assert scout_mail.mailbox_status(AGENT, record) == "none"


class UidMailbox:
    messages = {}
    searches = []
    fetches = []
    validity = b"44"
    def __init__(self, *args, **kwargs):
        pass
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def login(self, *args):
        pass
    def select(self, folder, readonly=False):
        assert folder == "INBOX" and readonly
        return "OK", []
    def response(self, name):
        assert name == "UIDVALIDITY"
        return "UIDVALIDITY", [self.validity]
    def uid(self, action, *args):
        if action == "search":
            self.searches.append(args)
            values = sorted(self.messages)
            if "UID" in args:
                floor = int(args[-1].split(":", 1)[0])
                values = [value for value in values if value >= floor]
            return "OK", [b" ".join(str(value).encode() for value in values)]
        if action == "fetch":
            uid = int(args[0])
            query = args[1]
            self.fetches.append((uid, query))
            raw = self.messages[uid].as_bytes()
            if "HEADER.FIELDS" in query:
                separator = b"\r\n\r\n" if b"\r\n\r\n" in raw else b"\n\n"
                return "OK", [(b"", raw.split(separator, 1)[0] + separator)]
            if ".MIME]" in query:
                content_type = "message/delivery-status" if "[2." in query else "text/plain; charset=utf-8"
                return "OK", [(b"", f"Content-Type: {content_type}\r\n\r\n".encode())]
            if "[2" in query:
                lines = []
                for part in self.messages[uid].walk():
                    for name in ("Final-Recipient", "Original-Recipient", "Original-Message-ID"):
                        if part.get(name):
                            lines.append(f"{name}: {part.get(name)}")
                return "OK", [(b"", "\n".join(lines).encode())]
            body = self.messages[uid].get_body(preferencelist=("plain", "html"))
            content = body.get_content() if body else ""
            return "OK", [(b"", str(content).encode()[:8192])]
        raise AssertionError(action)


class ReplyStore:
    def __init__(self):
        created = datetime.now(timezone.utc).isoformat()
        self.data = {"jobs": {name: {"id": name} for name in ("human", "ooo", "auto", "ambiguous")},
                     "sends": {name: {"job_id": name, "recipient": "recruiter@example.com",
                                             "message_id": f"<{name}@scout>", "status": "accepted",
                                             "created_at": created}
                               for name in ("human", "ooo", "auto", "ambiguous")}}
        self.saved = 0
    def save(self):
        self.saved += 1
        return True


def test_reply_monitor_persists_only_exact_thread_human_reply(monkeypatch):
    human = message(references="<human@scout>", mid="<human-reply@example>")
    ooo = message(references="<ooo@scout>", mid="<ooo-reply@example>", body="I am out of office until Monday")
    auto = message(references="<auto@scout>", mid="<auto-reply@example>", body="We have received your application")
    auto["Auto-Submitted"] = "auto-replied"
    ambiguous = message(sender="colleague@example.com", references="<ambiguous@scout>", mid="<ambiguous-reply@example>")
    UidMailbox.messages = {101: human, 102: ooo, 103: auto, 104: ambiguous}
    UidMailbox.searches = []
    UidMailbox.fetches = []
    monkeypatch.setattr(scout_mail.imaplib, "IMAP4_SSL", UidMailbox)
    store = ReplyStore()

    result = scout_mail.monitor_replies(AGENT, store)
    assert result == {"status": "ok", "checked": 4, "new_human_replies": 1,
                      "auto_replies": 1, "out_of_office": 1, "bounces": 0, "ambiguous": 1}
    assert store.data["jobs"]["human"]["outcome"] == "reply"
    assert store.data["jobs"]["human"]["outcome_source"] == "imap_exact_thread"
    assert all("outcome" not in store.data["jobs"][name] for name in ("ooo", "auto", "ambiguous"))
    assert store.data["mailbox_monitor"]["last_uid"] == 104 and store.saved == 1
    header_uids = [uid for uid, query in UidMailbox.fetches if "HEADER.FIELDS" in query]
    preview_uids = [uid for uid, query in UidMailbox.fetches
                    if "HEADER.FIELDS" not in query and ".MIME]" not in query]
    assert header_uids == [101, 102, 103, 104]
    assert preview_uids == [101, 102, 103]
    assert all("BODY.PEEK[]" not in query for _, query in UidMailbox.fetches)
    assert all("<0.8192>" in query for _, query in UidMailbox.fetches
               if "HEADER.FIELDS" not in query and ".MIME]" not in query)

    again = scout_mail.monitor_replies(AGENT, store)
    assert again["checked"] == 0 and again["new_human_replies"] == 0
    assert "UID" in UidMailbox.searches[-1]


def test_reply_monitor_failure_does_not_advance_cursor(monkeypatch):
    class BrokenUidMailbox(UidMailbox):
        def uid(self, action, *args):
            if action == "fetch":
                return "NO", []
            return super().uid(action, *args)
    UidMailbox.messages = {201: message(references="<human@scout>")}
    monkeypatch.setattr(scout_mail.imaplib, "IMAP4_SSL", BrokenUidMailbox)
    store = ReplyStore()
    AGENT._MAILBOX_MESSAGE_STATUS = {("recruiter@example.com", "<human@scout>"): "none"}
    result = scout_mail.monitor_replies(AGENT, store)
    assert result["status"] == "unavailable"
    assert store.data["mailbox_monitor"].get("last_uid") is None
    assert AGENT._MAILBOX_MESSAGE_STATUS == {}


def test_reply_monitor_classifies_exact_bounce_without_positive_outcome(monkeypatch):
    from email.mime.multipart import MIMEMultipart
    from email.message import Message
    bounced = MIMEMultipart("report", report_type="delivery-status")
    bounced["From"] = "mailer-daemon@gmail.com"
    bounced["References"] = "<bounce@scout>"
    delivery = Message()
    delivery.set_type("message/delivery-status")
    fields = Message()
    fields["Final-Recipient"] = "rfc822; recruiter@example.com"
    fields["Original-Message-ID"] = "<bounce@scout>"
    delivery.set_payload([fields])
    bounced.attach(delivery)
    UidMailbox.messages = {301: bounced}
    UidMailbox.fetches = []
    monkeypatch.setattr(scout_mail.imaplib, "IMAP4_SSL", UidMailbox)
    suppressed = []
    monkeypatch.setattr(AGENT, "_suppress_email", lambda email, reason: suppressed.append((email, reason)), raising=False)
    store = ReplyStore()
    created = datetime.now(timezone.utc).isoformat()
    store.data["jobs"]["bounce"] = {"id": "bounce"}
    store.data["sends"]["bounce"] = {"job_id": "bounce", "recipient": "recruiter@example.com",
                                             "message_id": "<bounce@scout>", "status": "accepted",
                                             "created_at": created}
    result = scout_mail.monitor_replies(AGENT, store)
    assert result["bounces"] == 1 and result["new_human_replies"] == 0
    assert "outcome" not in store.data["jobs"]["bounce"]
    assert store.data["jobs"]["bounce"]["delivery_status"] == "bounced"
    assert store.data["suppressed_recipients"]["recruiter@example.com"]["reason"] == "bounce"
    assert store.data["sends"]["bounce"]["delivery_status"] == "bounced"
    assert suppressed == [("recruiter@example.com", "bounce")]
    assert [query for _, query in UidMailbox.fetches] == [
        "(BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT MESSAGE-ID ORIGINAL-MESSAGE-ID IN-REPLY-TO REFERENCES AUTO-SUBMITTED PRECEDENCE X-AUTOREPLY X-AUTORESPOND X-AUTO-RESPONSE-SUPPRESS CONTENT-TYPE)])",
        "(BODY.PEEK[2.MIME])",
        "(BODY.PEEK[2]<0.8192>)",
    ]
