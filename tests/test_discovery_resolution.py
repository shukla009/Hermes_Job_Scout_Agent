from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
import json
from urllib.parse import quote

import pytest
import requests

from scout_discovery import resolve_alert_hints


def page(url, posted=None):
    job = {"@type":"JobPosting", "title":"Research Analyst", "datePosted": posted,
           "hiringOrganization":{"name":"Example"}, "description":"Market research and intelligence reports",
           "jobLocation":{"address":{"addressLocality":"Noida", "addressCountry":"India"}}, "employmentType":"FULL_TIME"}
    return SimpleNamespace(url=url, text='<script type="application/ld+json">'+json.dumps(job)+'</script>')


def test_ignored_links_remain_queued_and_failure_has_bounded_retry():
    hints = [{"url":f"https://careers.example/jobs/{n}"} for n in range(12)]
    def fail(*args, **kwargs):
        raise OSError("temporary")
    jobs, stats = resolve_alert_hints(hints, fail)
    assert not jobs and len(stats["pending_hints"]) == 12
    retry = {"url":"https://careers.example/jobs/1", "resolve_attempts":2}
    assert resolve_alert_hints([retry], fail)[1]["pending_hints"] == []


def test_wrapped_job_url_resolves_before_filtering_without_refreshing_original_date():
    original = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    jobs, stats = resolve_alert_hints([{"url":"https://tracking.example/click?url=https%3A%2F%2Fcareers.example%2Fjobs%2F1",
        "alert_received_at":datetime.now(timezone.utc).isoformat()}], lambda url, **kw:page(url, original))
    assert len(jobs) == 1 and jobs[0]["href"] == "https://careers.example/jobs/1"
    assert jobs[0]["date_posted"] == original
    from scout_agent import _structured_freshness
    assert not _structured_freshness(jobs[0])[0]


def test_unknown_original_date_is_discovery_gap_not_new_job():
    jobs, stats = resolve_alert_hints([{"url":"https://careers.example/jobs/1"}], lambda url, **kw:page(url))
    assert jobs == [] and stats["unverified_dates"] == 1


def test_excluded_login_platform_not_fetched():
    calls=[]
    jobs, stats=resolve_alert_hints([{"url":"https://www.linkedin.com/jobs/view/1"}], lambda *a,**kw:calls.append(a))
    assert not calls and not jobs and stats["blocked"] == 1


@pytest.mark.parametrize("status,category,retry", [
    (401,"http_access_denied",False), (403,"http_access_denied",False),
    (404,"http_not_found",False), (410,"http_not_found",False),
    (429,"http_rate_limited",True), (503,"http_server_error",True),
    (422,"http_client_error",False),
])
def test_http_failure_categories_do_not_leak_tracker_tokens_or_repeat_permanent_failures(status, category, retry):
    token = "private-tracker-token"
    hint = {"url":"https://tracking.example/click?token="+token}
    def fail(url, **kwargs):
        assert kwargs["max_retries"] == 0
        response = requests.Response()
        response.status_code = status
        raise requests.HTTPError(f"{status} for {url}", response=response)
    jobs, stats = resolve_alert_hints([hint], fail)
    assert jobs == [] and stats["failure_categories"] == {category:1}
    pending = stats.pop("pending_hints")
    assert bool(pending) == retry
    assert stats["retry_queued"] == int(retry)
    assert token not in json.dumps(stats) and "tracking.example" not in json.dumps(stats)


@pytest.mark.parametrize("error,category,retry", [
    (requests.Timeout("private url"), "timeout", True),
    (requests.ConnectionError("private url"), "connection_error", True),
    (requests.exceptions.SSLError("private url"), "tls_error", False),
    (ValueError("unsafe_public_url"), "unsafe_url", False),
    (ValueError("redirect_limit"), "redirect_limit", False),
])
def test_transport_failure_retry_policy(error, category, retry):
    def fail(*args, **kwargs):
        raise error
    _, stats = resolve_alert_hints([{"url":"https://careers.example/jobs/1"}], fail)
    assert stats["failure_categories"] == {category:1}
    assert bool(stats["pending_hints"]) == retry


def test_nested_explicit_destination_skips_trackers_and_deduplicates():
    destination = "https://careers.example/jobs/1"
    nested = "https://tracking.example/click?destination=" + quote(destination, safe="")
    wrapped = "https://mail.example/click?url=" + quote(nested, safe="")
    calls = []
    def fetch(url, **kwargs):
        calls.append(url)
        return page(url, "2026-10-22")
    jobs, stats = resolve_alert_hints([{"url":wrapped}, {"url":destination}], fetch)
    assert calls == [destination] and len(jobs) == 1 and stats["duplicates"] == 1


def test_public_board_explicit_employer_destination_is_fetched_with_its_own_date():
    employer = "https://careers.example/jobs/123"
    board = "https://www.indeed.com/viewjob?jk=123"
    calls = []
    def fetch(url, **kwargs):
        calls.append(url)
        result = page(url, "2026-10-22" if url == board else "2026-09-01")
        if url == board:
            result.text = result.text.replace('"datePosted"', '"url":"'+employer+'", "datePosted"')
        return result
    jobs, stats = resolve_alert_hints([{"url":board}], fetch)
    assert calls == [board, employer]
    assert len(jobs) == 1 and jobs[0]["href"] == employer
    assert jobs[0]["date_posted"] == "2026-09-01"


def test_board_without_explicit_employer_destination_is_not_retried():
    _, stats = resolve_alert_hints([{"url":"https://www.indeed.com/viewjob?jk=123"}], lambda url, **kw:page(url, "2026-10-22"))
    assert stats["failure_categories"] == {"job_board_only":1}
    assert stats["pending_hints"] == [] and stats["blocked"] == 1


def test_old_board_vacancy_cannot_be_freshened_by_employer_republication_date():
    board = "https://www.indeed.com/viewjob?jk=123"
    employer = "https://careers.example/jobs/123"
    def fetch(url, **kwargs):
        result = page(url, "2026-09-01" if url == board else datetime.now(timezone.utc).isoformat())
        if url == board:
            result.text = result.text.replace('"datePosted"', '"url":"'+employer+'", "datePosted"')
        return result
    jobs, _ = resolve_alert_hints([{"url":board}], fetch)
    assert len(jobs) == 1 and jobs[0]["first_published"] == "2026-09-01"
    from scout_agent import _structured_freshness
    assert not _structured_freshness(jobs[0])[0]


def test_excluded_structured_destination_never_requested():
    calls = []
    def fetch(url, **kwargs):
        calls.append(url)
        result = page(url, "2026-10-22")
        result.text = result.text.replace('"datePosted"', '"url":"https://www.naukri.com/jobs/1", "datePosted"')
        return result
    jobs, stats = resolve_alert_hints([{"url":"https://www.indeed.com/viewjob?jk=123"}], fetch)
    assert len(calls) == 1 and jobs == []
    assert stats["failure_categories"] == {"excluded_platform":1}
    stats.pop("pending_hints")
    assert "naukri" not in json.dumps(stats).lower()


def test_pending_queue_is_bounded_and_duplicate_failures_not_requeued_twice():
    hints = [{"url":f"https://careers.example/jobs/{n}"} for n in range(250)]
    hints.insert(1, dict(hints[0]))
    def fail(*args, **kwargs):
        raise requests.Timeout("temporary")
    _, stats = resolve_alert_hints(hints, fail, max_links=5)
    assert len(stats["pending_hints"]) == 100
    assert len({hint["url"] for hint in stats["pending_hints"]}) == 100
    assert stats["duplicates"] == 1


def test_invalid_original_date_is_not_a_resolved_vacancy():
    jobs, stats = resolve_alert_hints([{"url":"https://careers.example/jobs/1"}], lambda url, **kw:page(url, "new today"))
    assert jobs == [] and stats["failure_categories"] == {"invalid_original_date":1}


def test_global_budget_defers_unattempted_links_without_consuming_retry_allowance():
    from scout_persistence import BudgetExhausted
    hints = [{"url":f"https://careers.example/jobs/{n}", "resolve_attempts":2} for n in range(12)]
    calls = []
    def fail(url, **kwargs):
        calls.append(url)
        raise BudgetExhausted("public_http_deadline")
    jobs, stats = resolve_alert_hints(hints, fail)
    assert len(calls) == 1 and jobs == []
    assert len(stats["pending_hints"]) == 12 and stats["deferred_budget"] == 10
    assert all(hint["resolve_attempts"] == 2 for hint in stats["pending_hints"])
    assert stats["retry_exhausted"] == 0
