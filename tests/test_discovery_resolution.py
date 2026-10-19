from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
import json

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
