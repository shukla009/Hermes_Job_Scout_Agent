"""Adversarial regression scenarios for cross-route/state integration."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import scout_agent
from scout_state import StateStore


def test_uncertain_initial_application_blocks_another_recipient_for_same_job(tmp_path):
    store = StateStore(tmp_path / "scout.json", history_path=tmp_path / "history.json")
    job = store.upsert_job({"job_title": "Research Analyst", "company": "Example",
                            "application_url": "https://careers.example/jobs/1"})
    first = store.reserve_send(job["id"], "recruiter-a@example.com")
    assert first
    assert store.finish_send(first["id"], "uncertain")
    assert store.reserve_send(job["id"], "recruiter-b@example.com") is None


def test_refreshed_posting_date_cannot_hide_earlier_original_publication():
    now = datetime.now(timezone.utc)
    old = (now - timedelta(days=8)).isoformat()
    recent = (now - timedelta(hours=1)).isoformat()
    fresh, _ = scout_agent._structured_freshness({"first_published": old, "date_posted": recent})
    assert not fresh


def test_known_closed_vacancy_does_not_receive_followup(monkeypatch):
    now = datetime.now(timezone.utc).timestamp()
    job_id = "a" * 24
    record = {"job_id": job_id, "email": "recruiter@example.com", "subject": "Application",
              "message_id": "<original@scout>", "created_at": now - 4 * 86400,
              "next_send": now - 10, "step": 1, "offsets_days": [4, 9]}
    sent = []
    store = SimpleNamespace(
        data={"jobs": {job_id: {"id": job_id, "archived": True, "exclusion_reason": "Vacancy closed"}}},
        reserve_send=lambda *args, **kwargs: {"id": "fixture", "message_id": "<followup@scout>"},
        finish_send=lambda *args: True,
    )
    monkeypatch.setattr(scout_agent, "ACTIVE_STORE", store)
    monkeypatch.setattr(scout_agent, "SEND_EMAILS", True)
    monkeypatch.setattr(scout_agent, "load_follow_up", lambda: [dict(record)])
    monkeypatch.setattr(scout_agent, "save_follow_up", lambda records: None)
    monkeypatch.setattr(scout_agent, "_mailbox_status", lambda record: "none")
    monkeypatch.setattr(scout_agent, "_is_suppressed", lambda email: False)
    monkeypatch.setattr(scout_agent, "send_email", lambda *args, **kwargs: sent.append(args) or True)
    scout_agent.process_pending_follow_ups()
    assert sent == []
