import json
import hashlib
import hmac
import pytest
from scout_state import StateStore, StateCorruptError, callback_data


def store(tmp_path, history=None, callback=None):
    legacy = tmp_path / "history.json"
    if history is not None:
        legacy.write_text(json.dumps(history), encoding="utf-8")
    return StateStore(tmp_path / "state.json", legacy, callback)


def test_migration_alert_is_not_application(tmp_path):
    state = store(tmp_path, [{"url": "https://company/job/1", "route": "apply"}, {"url": "https://company/job/2", "email": "hr@example.org"}])
    first, second = state.data["jobs"].values()
    assert first["alerted"] and not first["submitted"] and first["submission_unconfirmed"]
    assert second["emailed"] and not second["submitted"]


def test_corrupt_state_and_history_fail_closed(tmp_path):
    (tmp_path / "state.json").write_text("{broken", encoding="utf-8")
    with pytest.raises(StateCorruptError):
        store(tmp_path)
    (tmp_path / "state.json").unlink()
    with pytest.raises(StateCorruptError):
        store(tmp_path, {"not": "a list"})


def test_daily_quota_and_stretch(tmp_path):
    state = store(tmp_path)
    jobs = [state.upsert_job({"url": f"https://c/{n}"})["id"] for n in range(6)]
    assert state.record_alert(jobs[0], True)
    assert not state.can_alert(jobs[1], True)
    for ident in jobs[1:5]:
        assert state.record_alert(ident)
    assert not state.can_alert(jobs[5])
    assert not state.known_submitted(jobs[0])


def test_reserve_is_durable_and_uncertain_never_retried(tmp_path):
    calls = []
    state = store(tmp_path, callback=lambda s: calls.append(json.loads(s.path.read_text())) or True)
    ident = state.upsert_job({"url": "https://c/1"})["id"]
    intent = state.reserve_send(ident, "HR@Example.org")
    assert calls[-1]["sends"][intent["id"]]["status"] == "pending"
    state.finish_send(intent["id"], "uncertain")
    assert state.reserve_send(ident, "hr@example.org") is None
    assert not state.known_emailed(ident)


def test_failed_remote_commit_stops_send(tmp_path):
    state = store(tmp_path, callback=lambda s: False)
    ident = state.upsert_job({"url": "https://c/1"})["id"]
    assert state.reserve_send(ident, "hr@example.org") is None


def test_feedback_order_idempotence_and_terminal_outcomes(tmp_path):
    state = store(tmp_path)
    ident = state.upsert_job({"url": "https://c/1"})["id"]
    directory = tmp_path / "feedback"
    directory.mkdir()
    for number, action in [(20, "interview"), (10, "applied"), (30, "snooze")]:
        (directory / f"{number}.json").write_text(json.dumps({"job_id": ident, "action": action}))
    assert state.ingest_feedback(directory) == 3
    assert state.known_submitted(ident)
    assert state.data["jobs"][ident]["outcome"] == "interview"
    assert "snoozed_until" not in state.data["jobs"][ident]
    assert state.ingest_feedback(directory) == 0


def test_callback_signature_size_and_validation():
    value = callback_data("a" * 24, "interview", "token", now=1800000000)
    assert len(value.encode()) <= 64
    payload, signature = value.rsplit(":", 1)
    assert signature == hmac.new(b"token", payload.encode(), hashlib.sha256).hexdigest()[:12]
    with pytest.raises(ValueError):
        callback_data("../../secret", "applied", "token")


def test_provided_id_application_url_and_metadata(tmp_path):
    state = store(tmp_path)
    record = state.upsert_job({"job_id": "b" * 24, "application_url": "https://c/apply", "family": "research",
                               "requirements": {"mandatory": {"unknown": ["CRM"]}},
                               "risk_flags": [{"code": "free_mail_contact", "severity": "review"}],
                               "source_text": "private raw content"})
    assert record["id"] == "b" * 24
    assert record["apply_url"] == "https://c/apply"
    assert record["family"] == "research"
    assert record["requirements"]["mandatory"]["unknown"] == ["CRM"]
    assert record["risk_flags"][0]["code"] == "free_mail_contact"
    assert "source_text" not in record


def test_legacy_recipient_cooldown(tmp_path):
    from datetime import datetime, timezone
    state = store(tmp_path, [{"url": "https://c/old", "email": "hr@example.org", "timestamp": datetime.now(timezone.utc).isoformat()}])
    ident = state.upsert_job({"url": "https://c/new"})["id"]
    assert state.reserve_send(ident, "hr@example.org") is None


def test_weekly_metrics_exclude_old_events(tmp_path):
    state = store(tmp_path)
    record = state.upsert_job({"url": "https://c/old"})
    record.update(first_seen="2000-01-01", alerted=True, alerted_at="2000-01-01", submitted=True, applied_at="2000-01-01")
    assert state.weekly_metrics()["discovered"] == 0
    assert state.weekly_metrics()["submitted"] == 0


def test_initial_accepted_populates_recipient(tmp_path):
    state = store(tmp_path)
    ident = state.upsert_job({"url": "https://c/1"})["id"]
    intent = state.reserve_send(ident, "HR@example.org")
    assert state.finish_send(intent["id"], "accepted")
    assert state.data["jobs"][ident]["email"] == "hr@example.org"


def test_durable_bounce_suppression_blocks_future_jobs(tmp_path):
    state = store(tmp_path)
    first = state.upsert_job({"url": "https://c/1"})["id"]
    second = state.upsert_job({"url": "https://c/2"})["id"]
    state.data.setdefault("suppressed_recipients", {})["hr@example.org"] = {
        "reason": "bounce", "detected_at": "2026-10-18T00:00:00+00:00"
    }
    assert state.reserve_send(first, "hr@example.org") is None
    assert state.reserve_send(second, "HR@EXAMPLE.ORG") is None


def test_conversion_cohorts_are_counts_grouped_by_source_and_family(tmp_path):
    state = store(tmp_path)
    first = state.upsert_job({"url": "https://c/1", "source_id": "job-1", "source_name": "greenhouse:Acme", "family": "research"})
    second = state.upsert_job({"url": "https://c/2", "source_id": "job-2", "source_name": "greenhouse:Acme", "family": "data"})
    first.update(alerted=True, emailed=True, outcome="reply")
    second.update(submitted=True, outcome="interview")

    cohorts = state.conversion_cohorts()
    assert cohorts["sources"]["greenhouse:Acme"] == {
        "discovered": 2, "alerted": 1, "emailed": 1, "submitted": 1,
        "replies": 1, "interviews": 1, "rejected": 0,
    }
    assert cohorts["families"]["research"]["replies"] == 1
    assert cohorts["families"]["data"]["interviews"] == 1
