"""Synthetic recall regressions, not measured real-market recall."""
from datetime import date

import pytest

from scout_matching import evaluate_job, _experience


NOW = date(2026, 10, 23)


def vacancy(**changes):
    result = {
        "title": "Junior Research Analyst",
        "location": "Gurugram, India",
        "description": "Full-time day shift. Research and competitive analysis. 0-2 years experience.",
    }
    result.update(changes)
    return result


def test_preferred_tenure_is_not_mandatory():
    row = evaluate_job(vacancy(description="Full-time day shift. Research and competitive analysis. 2 years experience preferred."), now=NOW)
    assert row["email_safe"]
    assert row["experience_min"] is None
    assert not row["requirements"]["mandatory"]["unmet"]


@pytest.mark.parametrize("phrase,minimum", [
    ("Minimum 36 months research experience", 3),
    ("Minimum one year of professional experience", 1),
    ("Minimum twenty-four months research experience", 2),
    ("Minimum one and a half years research experience", 1.5),
    ("Minimum 1 year and 6 months research experience", 1.5),
])
def test_explicit_tenure_never_uses_junior_unknown_exception(phrase, minimum):
    result = evaluate_job(vacancy(description="Full-time day shift. Research and competitive analysis. " + phrase), now=NOW)
    assert _experience(phrase.lower()) == minimum
    assert not result["email_safe"]
    assert not result["junior_experience_exception"]


def test_preferred_and_required_tenure_in_one_sentence():
    assert _experience("two years preferred but minimum one year required") == 1


def test_sql_specific_tenure_is_not_met_by_unrelated_internships():
    result = evaluate_job(vacancy(title="Junior Data Analyst", description="Full-time day shift. Data analysis and dashboards. Minimum 1 year SQL experience."), now=NOW)
    assert not result["email_safe"]
    result = evaluate_job(vacancy(title="Junior Data Analyst", description="Full-time day shift. Data analysis. Experience with SQL is required."), now=NOW)
    assert not result["email_safe"]


def test_verified_professional_research_is_available_without_inventing_tenure():
    row = evaluate_job(vacancy(description=vacancy()["description"] + " Professional market research experience is required."), now=NOW)
    assert row["email_safe"]
    row = evaluate_job(vacancy(description=vacancy()["description"] + " Minimum 1 year professional market research experience required."), now=NOW)
    assert not row["email_safe"]


@pytest.mark.parametrize("requirements", [
    "Required skills:\nSnowflake\nDatabricks",
    "<h2>Required skills</h2><ul><li>Snowflake</li><li>Databricks</li></ul>",
    "<h2>Required skills</h2><p>Rust certification</p>",
])
def test_required_list_structure_is_preserved(requirements):
    row = evaluate_job(vacancy(description=vacancy()["description"] + "\n" + requirements), now=NOW)
    assert not row["email_safe"]
    assert row["requirements"]["clauses"]


def test_negated_shift_and_reporting_line_are_not_job_requirements():
    row = evaluate_job(vacancy(title="Research Analyst reporting to Team Lead", description=vacancy()["description"] + " No night shift required."), now=NOW)
    assert row["email_safe"]
    row = evaluate_job(vacancy(description=vacancy()["description"] + " No night shift required. Rotational shifts required."), now=NOW)
    assert not row["eligible"]
    assert not evaluate_job(vacancy(title="Senior Research Analyst reporting to Manager"), now=NOW)["eligible"]
    assert evaluate_job(vacancy(description=vacancy()["description"] + " Night shifts are not required."), now=NOW)["email_safe"]


def test_mandatory_section_ends_at_html_responsibility_heading():
    row = evaluate_job(vacancy(description=vacancy()["description"] + "<h2>Required skills</h2><p>Excel</p><h2>Responsibilities</h2><p>Support research team.</p>"), now=NOW)
    assert row["email_safe"]


def test_preferred_professional_tenure_does_not_retype_generic_required_tenure():
    row = evaluate_job(vacancy(description="Full-time day shift research and competitive analysis. 1 year experience required. 2 years professional experience preferred."), now=NOW)
    assert row["email_safe"]


def test_all_met_mixed_alternatives_no_longer_hold():
    row = evaluate_job(vacancy(description=vacancy()["description"] + " Required skills: SQL and Power BI or Tableau."), now=NOW)
    assert row["email_safe"]
    row = evaluate_job(vacancy(description=vacancy()["description"] + " Required skills: SQL and Power BI or Snowflake."), now=NOW)
    assert not row["email_safe"]


@pytest.mark.parametrize("title,duties", [
    ("Market Analyst", "Research and competitive analysis"),
    ("Business Operations Executive", "Process workflow mapping and gap analysis"),
])
def test_title_aliases_require_supported_responsibilities(title, duties):
    row = evaluate_job(vacancy(title=title, description="Full-time day shift. 0-2 years experience. " + duties), now=NOW)
    assert row["email_safe"]
    row = evaluate_job(vacancy(title=title, description="Full-time day shift. 0-2 years experience. General administrative duties."), now=NOW)
    assert not row["eligible"]
