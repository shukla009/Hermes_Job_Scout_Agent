"""Synthetic fixture-only decision audit and offline replay checks."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json

import pytest

import scout_evaluation as evaluation
from scout_matching import evaluate_job
from scout_storage import encode_state


NOW = datetime(2026, 10, 23, 6, tzinfo=timezone.utc)


def post(**changes):
    result = {"audit_id": "a" * 24, "title": "Junior Research Analyst", "location": "Gurugram, India",
              "description": "Full-time day shift. Market research and competitive analysis. 0-2 years experience.",
              "source_name": "html:Moody's", "href": "https://careers.example.org/jobs/123"}
    result.update(changes)
    return result


def test_review_is_bounded_and_idempotent():
    data = {}
    vacancy = post()
    evaluation.record_review(data, vacancy, evaluate_job(vacancy, now=NOW), True, "verified", now=NOW)
    evaluation.record_review(data, vacancy, evaluate_job(vacancy, now=NOW), True, "verified", now=NOW + timedelta(hours=1))
    assert len(data["vacancy_reviews"]) == 1
    assert data["vacancy_reviews"]["a" * 24]["first_seen"] == NOW.isoformat()
    for index in range(90):
        vacancy = post(audit_id=f"{index:024x}", description="research" * 4000)
        evaluation.record_review(data, vacancy, {}, True, "verified", now=NOW + timedelta(hours=2))
    assert len(data["vacancy_reviews"]) <= evaluation.MAX_REVIEWS
    assert len(json.dumps(data["vacancy_reviews"], ensure_ascii=False).encode()) <= evaluation.MAX_REVIEW_BYTES
    assert all(row["source_truncated"] for row in data["vacancy_reviews"].values())


def test_text_fields_and_structured_restrictions_survive_snapshot():
    data = {}
    vacancy = post(source_text="Additional research duties", requirements="Excel required", salary_max_lpa=4,
                   experience_min=3, official_url="https://employer.example.org", active_listing=False)
    evaluation.record_review(data, vacancy, {}, True, "verified", now=NOW)
    saved = data["vacancy_reviews"]["a" * 24]["post"]
    for key in ("description", "source_text", "requirements", "salary_max_lpa", "experience_min", "official_url", "active_listing"):
        assert saved[key] == vacancy[key]
    assert not evaluate_job(saved, now=NOW)["email_safe"]


def test_30_day_retention_removes_old_reviews():
    data = {}
    evaluation.record_review(data, post(), {}, True, "verified", now=NOW - timedelta(days=31))
    evaluation.record_review(data, post(audit_id="b" * 24), {}, True, "verified", now=NOW)
    assert list(data["vacancy_reviews"]) == ["b" * 24]


def test_daily_counters_are_private_bounded_and_idempotent():
    data = {}
    report = {"vacancy_counts": {"new_to_scout": 2, "private@example.org": 100, "rechecked": -2},
              "source_funnel": {"Recruiter Personal Name": {"checked": 2, "private@example.org": 1}}}
    evaluation.record_daily(data, report, "private@example.org", now=NOW)
    evaluation.record_daily(data, report, "private@example.org", now=NOW)
    row = next(iter(data["daily_funnel"].values()))
    assert row["runs"] == 1 and row["counts"]["new_to_scout"] == 2
    assert row["sources"] == {"other": {"checked": 2}}
    assert "private@example.org" not in json.dumps(data)
    assert "Recruiter Personal Name" not in json.dumps(data)
    assert "rechecked" not in row["counts"]


def test_same_day_idempotency_does_not_evict_early_runs(monkeypatch):
    monkeypatch.setattr(evaluation, "MAX_DAILY_IDENTITIES", 2)
    data = {}
    for identity in ("run1", "run2", "run3", "run1"):
        evaluation.record_daily(data, {}, identity, now=NOW)
    assert next(iter(data["daily_funnel"].values()))["runs"] == 2


def test_sparse_daily_history_retains_only_30_calendar_days():
    data = {}
    evaluation.record_daily(data, {}, "old", now=NOW - timedelta(days=30))
    evaluation.record_daily(data, {}, "boundary", now=NOW - timedelta(days=29))
    evaluation.record_daily(data, {}, "today", now=NOW)
    assert len(data["daily_funnel"]) == 2
    assert (NOW - timedelta(days=30)).date().isoformat() not in data["daily_funnel"]


def test_confirmed_portal_submission_is_a_counted_source_stage():
    counters = {}
    evaluation.stage(counters, post(), "portal_confirmed")
    assert counters["html:Moody's"]["portal_confirmed"] == 1
    data = {}
    evaluation.record_daily(data, {"source_funnel": counters}, "run", now=NOW)
    assert next(iter(data["daily_funnel"].values()))["sources"]["html:Moody's"]["portal_confirmed"] == 1


def test_classification_distinguishes_duplicates_and_rechecks():
    data = {}
    vacancies = [post(), post(), post(href="https://careers.example.org/jobs/456", outreach_recheck=True)]
    unique, counts, sources = evaluation.classify_batch(data, vacancies, lambda value: value, now=NOW)
    assert counts == {"new_to_scout": 1, "rechecked": 0, "introduction_rechecks": 1, "duplicates_in_batch": 1, "unique_checked": 2}
    _, again, _ = evaluation.classify_batch(data, [post()], lambda value: value, now=NOW)
    assert again["rechecked"] == 1
    assert sources["html:Moody's"]["checked"] == 2
    assert len(unique) == 2


def test_replay_freezes_decision_time_and_never_changes_state(monkeypatch):
    data = {}
    vacancy = post(valid_through="2026-10-24")
    evaluation.record_review(data, vacancy, {"eligible": True, "email_safe": False}, True, "original fresh", now=NOW)
    before = deepcopy(data)
    import scout_matching
    original = scout_matching.evaluate_job
    observed = []
    def inspect(value, **kwargs):
        observed.append(kwargs["now"])
        return original(value, **kwargs)
    monkeypatch.setattr(scout_matching, "evaluate_job", inspect)
    counts = evaluation.replay_reviews(data)
    assert counts["recovered_matches"] == 1
    assert observed == [NOW]
    assert data == before
    assert all(type(value) is int for value in counts.values())


def test_partial_or_invalid_snapshots_are_not_replayed():
    data = {"vacancy_reviews": {"1": {"source_truncated": True, "post": {}}, "2": {"post": {}}, "3": None}}
    counts = evaluation.replay_reviews(data)
    assert counts["reviewed"] == 0
    assert counts["truncated_not_replayed"] == 1
    assert counts["invalid_not_replayed"] == 2


@pytest.mark.parametrize("encrypted", [False, True])
def test_cli_offline_replay_does_not_write_or_leak(tmp_path, monkeypatch, capsys, encrypted):
    monkeypatch.setenv("SCOUT_STATE_KEY", "fixture-only-never-production")
    data = {}
    evaluation.record_review(data, post(), {}, True, "verified", now=NOW)
    snapshot = encode_state(data, "state/scout.json") if encrypted else data
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps(snapshot), encoding="utf-8")
    before = path.read_bytes()
    assert evaluation.main(["--state", str(path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["mode"] == "offline_matching_only" and result["sending_enabled"] is False
    assert result["freshness_renewed"] is False
    assert path.read_bytes() == before
    assert "careers.example.org" not in json.dumps(result)


def test_cli_invalid_key_is_redacted(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SCOUT_STATE_KEY", "fixture-key-one")
    path = tmp_path / "secret-personal-path.json"
    path.write_text(json.dumps(encode_state({}, "state/scout.json")), encoding="utf-8")
    monkeypatch.setenv("SCOUT_STATE_KEY", "fixture-key-two")
    assert evaluation.main(["--state", str(path)]) == 1
    output = capsys.readouterr().err
    assert "fixture-key" not in output and "secret-personal-path" not in output
