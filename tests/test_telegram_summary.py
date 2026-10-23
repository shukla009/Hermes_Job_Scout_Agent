from scout_runtime import telegram_summary


def report(**changes):
    result = {"status": "healthy", "results_discovered": 24,
              "funnel": {"verified_fresh": 6, "suitable": 0, "reachable": 0},
              "emails_accepted_by_smtp": 0,
              "named_contacts": {"backend": "free_quota_exhausted", "direct_pages": "available",
                                 "cloud_backend_verified": True, "validation_mode": True, "shadow_runs_completed": 1}}
    result.update(changes)
    return result


def test_zero_result_message_explains_results_without_technical_noise():
    message = telegram_summary(report())
    assert "Jobs checked: 24" in message and "Suitable jobs: 0" in message
    assert "no jobs passed all your requirements" in message
    assert "Company-page checks still work" in message and "safety checks 1/3" in message
    assert "Your action: None" in message and "/status details" in message
    for term in ("SMTP", "Exa", "shadow", "adapters", "mandatory_requirement", "free_quota_exhausted"):
        assert term not in message
    assert len(message) < 800


def test_email_acceptance_is_not_described_as_confirmed_delivery():
    message = telegram_summary(report(emails_accepted_by_smtp=2, introductions_accepted_by_smtp=1))
    assert "2 accepted by Gmail" in message and "Recruiter introductions: 1" in message
    assert "does not confirm delivery" in message
    assert "no jobs passed" not in message


def test_safety_stops_and_gmail_failure_need_action():
    assert "needs attention" in telegram_summary(report(status="state_safety_stop"))
    message = telegram_summary(report(outreach={"smtp": "failed"}))
    assert "Gmail connection failed" in message and "Check the saved Gmail" in message
    assert "Your action: None" not in message


def test_optional_actions_and_incomplete_search_are_explicit():
    message = telegram_summary(report(manual_actions={"sent": 2}, discovery_health={"failing_sources": 1}))
    assert "Optional jobs to apply to yourself: /actions" in message
    assert "results may be incomplete" in message


def test_forms_and_followups_are_not_confused_with_new_application_emails():
    message = telegram_summary(report(applications={"confirmed": 1}))
    assert "website confirmed submission" in message and "Email acceptance" not in message
    message = telegram_summary(report(follow_ups={"sent": 1}))
    assert "Follow-up emails: 1" in message and "No application email sent" in message
