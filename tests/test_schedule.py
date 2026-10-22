"""Scheduler jitter must never silently disable the private free-tier scout."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from scout_schedule import reserve_execution
from scout_state import IST
import scout_runtime


def at(day, hour, minute=0, month=10):
    return datetime(2026, month, day, hour, minute, tzinfo=IST)


def reserve(data, ident, now, scheduled=True, mode="private_allowance"):
    return reserve_execution(data, mode=mode, identity=ident, scheduled=scheduled, now=now)


def test_delayed_arrivals_have_no_exact_hour_requirement():
    data = {}
    for ident, now in [("a", at(22, 9, 11)), ("b", at(22, 15, 8)), ("c", at(22, 21, 34))]:
        assert reserve(data, ident, now)["status"] == "admitted"
    assert data["budget"]["used_minutes_estimate"] == 30
    result = reserve(data, "d", at(22, 23))
    assert result["status"] == "private_cadence_skip"
    assert result["scheduled_runs_today"] == 3
    assert "Three scheduled" in result["reason"]


def test_five_hour_gap_is_rolling_and_skip_setup_is_counted():
    data = {}
    reserve(data, "a", at(22, 9, 40))
    blocked = reserve(data, "b", at(22, 14, 39))
    assert blocked["status"] == "private_cadence_skip"
    assert blocked["reserved_minutes"] == 1
    assert datetime.fromisoformat(blocked["next_due_at"]).astimezone(IST) == at(22, 14, 40)
    assert reserve(data, "c", at(22, 14, 40))["status"] == "admitted"
    assert data["budget"]["used_minutes_estimate"] == 21


def test_ist_day_rollover_does_not_reset_rolling_gap():
    data = {}
    reserve(data, "a", at(22, 23, 50))
    result = reserve(data, "b", at(23, 0, 20))
    assert result["status"] == "private_cadence_skip"
    assert result["scheduled_runs_today"] == 0
    assert reserve(data, "c", at(23, 4, 50))["status"] == "admitted"


def test_utc_month_reset_preserves_schedule_gap():
    data = {}
    reserve(data, "a", datetime(2026, 10, 31, 23, 0, tzinfo=timezone.utc))
    data["budget"]["used_minutes_estimate"] = 1200
    result = reserve(data, "b", datetime(2026, 11, 1, 0, 1, tzinfo=timezone.utc))
    assert result["status"] == "private_cadence_skip"
    assert data["budget"]["month"] == "2026-11"
    assert data["budget"]["used_minutes_estimate"] == 1


def test_manual_run_does_not_consume_scheduled_slots_but_reserves_quota():
    data = {}
    reserve(data, "a", at(22, 9))
    assert reserve(data, "manual", at(22, 9, 5), scheduled=False)["status"] == "admitted"
    assert len(data["schedule"]["reservations"]) == 1
    assert data["budget"]["used_minutes_estimate"] == 20


def test_duplicate_attempt_cannot_execute_twice_or_double_charge():
    data = {}
    reserve(data, "a", at(22, 9))
    result = reserve(data, "a", at(22, 9, 5), scheduled=False)
    assert result["status"] == "duplicate_run_skip"
    assert data["budget"]["used_minutes_estimate"] == 10


@pytest.mark.parametrize("used,scheduled", [(1191, True), (1200, False)])
def test_private_monthly_budget_stops_before_work(used, scheduled):
    data = {"budget": {"month": "2026-10", "used_minutes_estimate": used, "runs": []}}
    result = reserve(data, "a", at(22, 9), scheduled=scheduled)
    assert result["status"] == "free_quota_pause"
    assert data["budget"]["paused"] is True
    assert data["schedule"]["reservations"] == []
    assert datetime.fromisoformat(result["next_due_at"]) == datetime(2026, 11, 1, tzinfo=timezone.utc)


def test_public_runs_do_not_get_private_cadence_or_private_allowance():
    data = {}
    for idx in range(5):
        result = reserve(data, str(idx), at(22, 9, idx), mode="standard_public")
        assert result["status"] == "admitted"
        assert result["reserved_minutes"] == 0
    assert data["budget"]["monthly_ceiling"] is None


def test_invalid_future_ledger_fails_closed():
    data = {"schedule": {"reservations": [{"identity": "future", "at": at(23, 9).isoformat()}]}}
    with pytest.raises(ValueError):
        reserve(data, "a", at(22, 9))


@pytest.mark.parametrize("data", [{"budget": []}, {"schedule": "broken"},
                                  *[{"budget": {"month": "2026-10", "used_minutes_estimate": value, "runs": []}}
                                    for value in (True, float("nan"), float("inf"))]])
def test_invalid_budget_or_schedule_shape_fails_closed(data):
    with pytest.raises(ValueError):
        reserve(data, "a", at(22, 9))


def run_fixture(monkeypatch, *, save_ok=True, data=None):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    monkeypatch.setenv("ZERO_COST_VERIFIED", "true")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "schedule")
    monkeypatch.setenv("GITHUB_RUN_ID", "999")
    monkeypatch.setenv("SCOUT_VERIFY_SCHEDULE", "false")
    reports, notices = [], []
    agent = SimpleNamespace(HISTORY_FILE="missing-history", FOLLOW_UP_FILE="missing-followups",
                            SUPPRESSION_FILE="missing-suppression", OUTBOUND_READY=True,
                            _write_run_report=lambda report: reports.append(dict(report)),
                            send_telegram=lambda text: notices.append(text) or True)
    store = SimpleNamespace(data=data or {}, ingest_feedback=lambda: None, save=lambda: save_ok)
    monkeypatch.setattr(scout_runtime, "StateStore", lambda *a, **k: store)
    monkeypatch.setattr(scout_runtime, "read_state", lambda *a, **k: [])
    monkeypatch.setattr("scout_persistence.GitHubWriter", lambda **k: object())
    monkeypatch.setattr(scout_runtime, "repository_mode", lambda writer: "private_allowance")
    return agent, store, reports, notices


def test_reservation_save_failure_stops_before_smtp_or_discovery(monkeypatch):
    agent, store, reports, notices = run_fixture(monkeypatch, save_ok=False)
    result = scout_runtime.run(agent)
    assert result["status"] == "state_safety_stop"
    assert reports[-1]["status"] == "state_safety_stop"
    assert agent.OUTBOUND_READY is False
    assert result["outreach"]["smtp"] == "not_checked"
    assert result["results_discovered"] == 0
    assert notices


def test_skip_save_failure_is_reported_as_safety_stop(monkeypatch):
    data = {"schedule": {"reservations": [{"identity": "prior", "at": datetime.now(timezone.utc).isoformat()}]}}
    agent, store, reports, notices = run_fixture(monkeypatch, save_ok=False, data=data)
    result = scout_runtime.run(agent)
    assert result["status"] == "state_safety_stop"
    assert reports[-1]["status"] == "state_safety_stop"
    assert "durably saved" in result["reason"]
    assert agent.OUTBOUND_READY is False
    assert notices


def test_skip_emits_report_and_attempt_without_overwriting_real_run(monkeypatch):
    now = datetime.now(timezone.utc)
    old_run = {"at": "old", "status": "partial", "results_discovered": 16}
    data = {"last_run": old_run.copy(), "schedule": {"reservations": [{"identity": "earlier", "at": now.isoformat()}]}}
    agent, store, reports, notices = run_fixture(monkeypatch, data=data)
    result = scout_runtime.run(agent)
    assert result["status"] == "private_cadence_skip"
    assert reports[-1]["next_due_at"]
    assert store.data["last_attempt"]["status"] == "private_cadence_skip"
    assert store.data["last_attempt"]["reason"]
    assert store.data["last_run"] == old_run
    assert len(notices) == 1
    # A second distinct cron arrival on the same day is visible in artifacts,
    # but does not repeat an unchanged Telegram cadence warning.
    monkeypatch.setenv("GITHUB_RUN_ID", "1000")
    scout_runtime.run(agent)
    assert len(notices) == 1
    assert len(reports) == 2


def test_manual_schedule_validation_applies_stricter_policy(monkeypatch):
    agent, store, reports, notices = run_fixture(monkeypatch, data={"schedule": {"reservations": [{"identity": "prior", "at": datetime.now(timezone.utc).isoformat()}]}})
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    monkeypatch.setenv("SCOUT_VERIFY_SCHEDULE", "true")
    result = scout_runtime.run(agent)
    assert result["status"] == "private_cadence_skip"
    assert result["trigger"] == "workflow_dispatch"
    assert result["schedule_validation"] is True
    assert store.data["last_attempt"]["scheduled"] is True


def test_quota_skip_is_visible_and_persisted(monkeypatch):
    data = {"budget": {"month": datetime.now(timezone.utc).strftime("%Y-%m"),
                       "used_minutes_estimate": 1200, "runs": []}}
    agent, store, reports, notices = run_fixture(monkeypatch, data=data)
    result = scout_runtime.run(agent)
    assert result["status"] == "free_quota_pause"
    assert reports[-1]["reason"]
    assert store.data["last_attempt"]["status"] == "free_quota_pause"
    assert store.data["budget"]["paused"] is True
    assert notices


def test_missing_cloud_precondition_still_writes_run_report(monkeypatch):
    agent, store, reports, notices = run_fixture(monkeypatch)
    monkeypatch.delenv("GITHUB_TOKEN")
    result = scout_runtime.run(agent)
    assert result["status"] == "state_safety_stop"
    assert reports[-1]["reason"] == "Durable GitHub persistence is required"
