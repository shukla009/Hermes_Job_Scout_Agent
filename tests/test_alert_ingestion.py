"""Bounded read-only alert intake; all messages are synthetic fixtures."""
from email.message import EmailMessage
from types import SimpleNamespace

import pytest

import scout_mail


AGENT = SimpleNamespace(GMAIL_SENDER="candidate@gmail.com", GMAIL_PASSWORD="fixture", IMAP_CHECK=True)


def alert(authentication="mx.google.com; dkim=pass header.d=linkedin.com", body=None):
    value = EmailMessage()
    value["From"] = "Jobs <jobs@linkedin.com>"
    value["Message-ID"] = "<alert-1@linkedin.com>"
    value["Date"] = "Mon, 19 Oct 2026 08:00:00 +0530"
    if authentication:
        value["Authentication-Results"] = authentication
    value.set_content(body or "https://www.linkedin.com/comm/jobs/view/123")
    return value


class AlertMailbox:
    messages = {}
    calls = []
    validity = b"1234"
    broken = False
    def __init__(self, *args, **kwargs):
        pass
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def login(self, *args):
        pass
    def select(self, label, readonly=False):
        assert label == '"HermesJobAlerts"' and readonly
        return "OK", []
    def response(self, name):
        return name, [self.validity]
    def uid(self, action, *args):
        self.calls.append((action, args))
        if action == "search":
            values = sorted(self.messages)
            if "UID" in args:
                minimum = int(args[-1].split(":")[0])
                values = [value for value in values if value >= minimum]
            return "OK", [b" ".join(str(value).encode() for value in values)]
        uid, query = int(args[0]), args[1]
        if self.broken:
            return "NO", []
        message = self.messages[uid]
        if "HEADER.FIELDS" in query:
            return "OK", [(b"", message.as_bytes().split(b"\n\n", 1)[0] + b"\n\n")]
        path = query.split("[", 1)[1].split("]", 1)[0].replace(".MIME", "")
        part = message
        try:
            for index in path.split("."):
                if part.is_multipart():
                    part = part.get_payload()[int(index) - 1]
                elif index != "1":
                    return "NO", []
        except IndexError:
            return "NO", []
        headers, body = part.as_bytes().split(b"\n\n", 1)
        return "OK", [(b"", headers + b"\n\n" if ".MIME]" in query else body[:32768])]


@pytest.fixture
def mailbox(monkeypatch):
    AlertMailbox.messages = {}
    AlertMailbox.calls = []
    AlertMailbox.validity = b"1234"
    AlertMailbox.broken = False
    monkeypatch.setattr(scout_mail.imaplib, "IMAP4_SSL", AlertMailbox)
    return AlertMailbox


def test_authenticates_before_body_and_retains_tracker_hints_without_posted_date(mailbox):
    value = alert(body="https://email.linkedin.com/e/abc-123")
    mailbox.messages = {10: value}
    result = scout_mail.ingest_alert_batch(AGENT)
    assert result["status"] == "ok" and result["authenticated"] == 1
    hint = result["hints"][0]
    assert hint["url"] == "https://email.linkedin.com/e/abc-123"
    assert hint["alert_received_at"].startswith("2026-10-19")
    assert "date_posted" not in hint and "body" not in hint
    assert result["cursor"] == {"uidvalidity": "1234", "last_uid": 10}
    queries = [args[1] for action, args in mailbox.calls if action == "fetch"]
    assert "HEADER.FIELDS" in queries[0]
    assert all("BODY.PEEK[]" not in query for query in queries)


@pytest.mark.parametrize("authentication", [None, "evil.example; dkim=pass header.d=linkedin.com", "mx.google.com; dkim=fail header.d=linkedin.com", "mx.google.com; spf=pass smtp.mailfrom=attacker.example", "mx.google.com; dkim=pass header.d=evil-linkedin.com"])
def test_spoofed_or_unaligned_senders_never_have_bodies_read(mailbox, authentication):
    mailbox.messages = {10: alert(authentication)}
    result = scout_mail.ingest_alert_batch(AGENT)
    assert result["status"] == "authentication_rejected"
    assert result["auth_rejected"] == 1 and not result["hints"]
    assert len([args for action, args in mailbox.calls if action == "fetch"]) == 1


def test_first_untrusted_authentication_result_cannot_be_rescued_by_later_header(mailbox):
    value = alert("attacker.example; dkim=pass header.d=linkedin.com")
    value["Authentication-Results"] = "mx.google.com; dkim=pass header.d=linkedin.com"
    mailbox.messages = {10: value}
    assert scout_mail.ingest_alert_batch(AGENT)["status"] == "authentication_rejected"


def test_cursor_reads_oldest_unseen_and_invalidates_on_uidvalidity_change(mailbox):
    mailbox.messages = {10: alert(), 11: alert(), 12: alert()}
    cursor = {"uidvalidity": "1234", "last_uid": 10}
    result = scout_mail.ingest_alert_batch(AGENT, max_messages=1, cursor=cursor)
    assert result["checked"] == 1 and result["cursor"]["last_uid"] == 11
    result = scout_mail.ingest_alert_batch(AGENT, cursor=result["cursor"])
    assert result["checked"] == 1 and result["cursor"]["last_uid"] == 12
    result = scout_mail.ingest_alert_batch(AGENT, cursor=result["cursor"])
    assert result["status"] == "empty" and result["checked"] == 0
    mailbox.validity = b"9999"
    result = scout_mail.ingest_alert_batch(AGENT, max_messages=1, cursor=cursor)
    assert result["cursor"] == {"uidvalidity": "9999", "last_uid": 12}


def test_failure_keeps_cursor_and_distinguishes_unavailable(mailbox):
    mailbox.messages = {11: alert()}
    mailbox.broken = True
    cursor = {"uidvalidity": "1234", "last_uid": 10}
    result = scout_mail.ingest_alert_batch(AGENT, cursor=cursor)
    assert result["status"] == "unavailable" and result["cursor"] == cursor
    assert not result["hints"]


def test_multipart_html_links_and_attachment_never_downloaded(mailbox):
    value = alert(body="Weekly vacancies")
    value.add_alternative('<a href="https://email.linkedin.com/e/r">Research Analyst</a>', subtype="html")
    value.add_attachment(b"private fixture attachment", maintype="application", subtype="pdf", filename="resume.pdf")
    mailbox.messages = {10: value}
    result = scout_mail.ingest_alert_batch(AGENT)
    assert result["hints"][0]["title"] == "Research Analyst"
    queries = [args[1] for action, args in mailbox.calls if action == "fetch"]
    assert not any("[2]<" in query for query in queries)


def test_zero_limit_and_invalid_label_do_not_connect(mailbox):
    assert scout_mail.ingest_alert_batch(AGENT, max_messages=0)["status"] == "disabled"
    assert scout_mail.ingest_alert_batch(AGENT, label='bad"label')["status"] == "invalid_config"
    assert mailbox.calls == []


def test_tracker_unsubscribe_and_settings_links_are_not_returned(mailbox):
    value = alert(body="Weekly jobs")
    value.add_alternative('<a href="https://email.linkedin.com/e/opaque1">Unsubscribe</a>'
                          '<a href="https://click.linkedin.com/r?url=https%3A%2F%2Fwww.linkedin.com%2Fsettings">Manage settings</a>'
                          '<a href="https://email.linkedin.com/e/opaque2">Research Analyst</a>', subtype="html")
    mailbox.messages = {10: value}
    result = scout_mail.ingest_alert_batch(AGENT)
    assert [hint["url"] for hint in result["hints"]] == ["https://email.linkedin.com/e/opaque2"]


def test_verified_employer_sender_can_be_explicitly_allowed_via_environment(mailbox, monkeypatch):
    value = alert("mx.google.com; dkim=pass header.d=example.com", body="https://careers.example.com/jobs/123")
    value.replace_header("From", "jobs@careers.example.com")
    mailbox.messages = {10: value}
    monkeypatch.setenv("SCOUT_ALERT_SENDER_DOMAINS", "example.com,invalid domain,www.naukri.com")
    result = scout_mail.ingest_alert_batch(AGENT)
    assert result["authenticated"] == 1 and len(result["hints"]) == 1


def test_prohibited_sender_domain_cannot_be_enabled_by_environment(mailbox, monkeypatch):
    value = alert("mx.google.com; dkim=pass header.d=naukri.com")
    value.replace_header("From", "jobs@naukri.com")
    mailbox.messages = {10: value}
    monkeypatch.setenv("SCOUT_ALERT_SENDER_DOMAINS", "naukri.com")
    assert scout_mail.ingest_alert_batch(AGENT)["status"] == "authentication_rejected"
