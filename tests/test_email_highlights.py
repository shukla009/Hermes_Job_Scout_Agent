"""Resume-grounded email content must not inherit candidate claims from a job."""
import pytest

import scout_agent
from scout_matching import load_profile


@pytest.mark.parametrize("family,metrics", [
    ("research", ("12+", "5+", "20%")),
    ("process", ("20%", "12+")),
    ("reporting", ("15+", "30%", "10,000+", "30 Karnataka districts")),
    ("data", ("1,000+", "40%", "15+", "30%", "10,000+", "30 Karnataka districts")),
])
def test_three_work_bullets_preserve_resume_metrics_and_linkedin(family, metrics):
    written, _ = scout_agent.compose_email({}, {
        "family": family, "job_title": "Analyst", "company": "Example",
        "application_url": "https://careers.example/jobs/1",
    })
    body = written["email_body"]
    bullets = [line[2:] for line in body.splitlines() if line.startswith("- ")]
    assert bullets == load_profile()["email_experience_highlights"][family]
    assert len(bullets) == 3 and "Experience highlights:\n- " in body
    for metric in metrics:
        assert metric in " ".join(bullets)
    assert "LinkedIn: https://www.linkedin.com/in/shivank-shukla09" in body
    assert "https://careers.example/jobs/1" in body
    assert "full-time since August 2026" in body
    assert "attached my resume" in body
    assert "lpa" not in body.lower()


def test_untrusted_analysis_cannot_replace_resume_highlights():
    written, _ = scout_agent.compose_email({}, {
        "family": "data", "job_title": "Analyst", "company": "Example",
        "evidence": ["Managed 500 people and improved revenue by 999%."],
    })
    assert "999%" not in written["email_body"]
    assert "1,000+ institutions" in written["email_body"]


@pytest.mark.parametrize("family", ["data", "reporting"])
def test_internship_metrics_are_not_presented_as_current_job_achievements(family):
    written, _ = scout_agent.compose_email({}, {"family": family})
    bullets = [line for line in written["email_body"].splitlines() if line.startswith("- ")]
    assert all("internship:" in line for line in bullets)
    assert "MAPE" not in written["email_body"]
    assert "98.1%" not in written["email_body"]


def test_unknown_family_uses_verified_work_highlights_not_project_metrics():
    written, _ = scout_agent.compose_email({}, {"family": "unknown"})
    assert "40%" in written["email_body"]
    assert "MAPE" not in written["email_body"]
