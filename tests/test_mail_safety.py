from email.message import EmailMessage
from types import SimpleNamespace
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
