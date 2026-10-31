import json
from datetime import date
from pathlib import Path

import pytest

from scout_matching import evaluate_job


NOW = date(2026, 10, 11)
CASES = json.loads(
    (Path(__file__).parent / "fixtures" / "matching_safety_cases.json").read_text(encoding="utf-8")
)


def base_job():
    return {
        "title": "Research Analyst",
        "description": "Full-time day shift. Research and competitive analysis. 0-2 years experience.",
        "location": "Gurugram, India",
        "date_posted": "2026-10-10",
    }


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_matching_safety_fixtures(case):
    vacancy = base_job()
    vacancy.update(case["changes"])
    result = evaluate_job(vacancy, now=NOW)
    expected = case["expected"]

    assert result["eligible"] is expected["eligible"]
    assert result["email_safe"] is expected["email_safe"]
    assert result["resume_autosend_allowed"] is expected["email_safe"]
    assert result["score_kind"] == "deterministic_fit_points_not_probability"
    assert result["score"] == max(0, min(100, sum(result["score_breakdown"].values()))) if result["score_breakdown"] else result["score"] == 0

    if "reason_contains" in expected:
        assert expected["reason_contains"] in result["reason"]
    if "mandatory_unmet" in expected:
        assert result["requirements"]["mandatory"]["unmet"] == expected["mandatory_unmet"]
    if "mandatory_unmet_contains" in expected:
        assert expected["mandatory_unmet_contains"] in result["requirements"]["mandatory"]["unmet"]
    if "mandatory_met_contains" in expected:
        assert expected["mandatory_met_contains"] in result["requirements"]["mandatory"]["met"]
    if "mandatory_unknown_contains" in expected:
        assert any(expected["mandatory_unknown_contains"] in item for item in result["requirements"]["mandatory"]["unknown"])
    if "preferred_unmet" in expected:
        assert result["requirements"]["preferred"]["unmet"] == expected["preferred_unmet"]
    if "preferred_penalty" in expected:
        assert result["score_breakdown"]["preferred_requirement_penalty"] == expected["preferred_penalty"]
    if "review_required" in expected:
        assert result["review_required"] is expected["review_required"]
    if "risk_level" in expected:
        assert result["risk_assessment"]["level"] == expected["risk_level"]
        assert {flag["code"] for flag in result["risk_flags"]} == set(expected["risk_codes"])
        assert "not a fraud probability" in result["risk_assessment"]["note"]
    if "official_corroboration" in expected:
        assert result["risk_assessment"]["official_corroboration"] is expected["official_corroboration"]


def test_preferred_requirement_is_a_small_penalty_not_a_hard_gate():
    baseline = evaluate_job(base_job(), now=NOW)
    preferred = base_job()
    preferred["description"] += " SAS preferred."
    with_preference = evaluate_job(preferred, now=NOW)
    assert with_preference["score"] == baseline["score"] - 2
    assert with_preference["eligible"]


def test_agency_identity_and_credentials_route_to_review_without_fraud_claim():
    vacancy = base_job()
    vacancy["description"] += " We are a recruitment agency acting on behalf of a confidential client. Share your bank account details to apply."
    result = evaluate_job(vacancy, now=NOW)
    codes = {flag["code"] for flag in result["risk_flags"]}
    assert {"agency_client_identity_unverified", "credential_or_financial_secret_request"} <= codes
    assert result["eligible"] and not result["email_safe"]
    assert result["risk_assessment"]["level"] == "critical"
    assert any("Recruitment safety review" in warning for warning in result["warnings"])
    assert "not a fraud probability" in result["risk_assessment"]["note"]


def test_generic_scam_warning_does_not_hide_a_separate_payment_request():
    vacancy = base_job()
    vacancy["description"] += " We do not charge an application fee. Purchase equipment from our nominated vendor before joining."
    result = evaluate_job(vacancy, now=NOW)
    assert "equipment_or_training_payment" in {flag["code"] for flag in result["risk_flags"]}
    assert not result["email_safe"]


def test_company_domain_experience_is_not_candidate_tenure():
    vacancy = base_job()
    vacancy["description"] = "Full-time day shift research. The company has 20 years of healthcare industry experience. Entry-level opportunity."
    result = evaluate_job(vacancy, now=NOW)
    assert result["experience_min"] is None
    assert result["eligible"] and result["email_safe"]


def test_employer_marketing_tenure_is_not_candidate_experience():
    vacancy = base_job()
    vacancy["description"] = "Full-time day shift research and competitive analysis. We bring 15 years of market research experience. Entry-level opportunity."
    result = evaluate_job(vacancy, now=NOW)
    assert result["experience_min"] is None
    assert result["eligible"] and result["email_safe"]


def test_candidate_wording_still_extracts_specialised_experience():
    vacancy = base_job()
    vacancy["description"] = "Full-time day shift research and competitive analysis. Candidates must have 2 years of market research experience."
    result = evaluate_job(vacancy, now=NOW)
    assert result["experience_min"] == 2
    assert result["tier"] == "stretch"
    assert not result["email_safe"]


def test_requirement_section_ends_at_new_heading():
    vacancy = base_job()
    vacancy["description"] += "\nRequired skills:\n- Excel\nResponsibilities:\n- Use SAS datasets supplied by the client"
    result = evaluate_job(vacancy, now=NOW)
    assert "SAS" not in result["requirements"]["mandatory"]["unmet"]
    assert result["eligible"] and result["email_safe"]


@pytest.mark.parametrize("requirement", ["SQL is required.", "Knowledge of SQL is required.", "Required skills: SQL"])
def test_listed_verified_skill_satisfies_knowledge_requirement(requirement):
    vacancy = base_job()
    vacancy["description"] += " " + requirement
    result = evaluate_job(vacancy, now=NOW)
    assert result["email_safe"]
    assert "SQL" in result["requirements"]["mandatory"]["met"]


@pytest.mark.parametrize("requirement", ["Professional SQL implementation is required.", "Hands-on SQL experience is mandatory.", "Production SQL is required."])
def test_knowledge_does_not_establish_professional_implementation(requirement):
    vacancy = base_job()
    vacancy["description"] += " " + requirement
    result = evaluate_job(vacancy, now=NOW)
    assert result["eligible"] and not result["email_safe"]
    assert any("SQL" in value for value in result["requirements"]["mandatory"]["unknown"])


def test_internship_dashboard_evidence_does_not_establish_professional_requirement():
    vacancy = base_job()
    vacancy["description"] += " Professional Power BI experience is mandatory."
    result = evaluate_job(vacancy, now=NOW)
    assert not result["email_safe"]
    assert any("Power BI" in value for value in result["requirements"]["mandatory"]["unknown"])


def test_internship_implementation_can_satisfy_handson_without_professional_tenure_claim():
    vacancy = base_job()
    vacancy["description"] += " Hands-on Power BI experience is mandatory."
    result = evaluate_job(vacancy, now=NOW)
    assert result["email_safe"]
    assert "Power BI" in result["requirements"]["mandatory"]["met"]


@pytest.mark.parametrize("requirement", ["Excel or SAS is required.", "SAS / Excel is required.", "Required skills: SAS, Excel or SQL"])
def test_one_evidenced_alternative_satisfies_or_requirement(requirement):
    vacancy = base_job()
    vacancy["description"] += " " + requirement
    result = evaluate_job(vacancy, now=NOW)
    assert result["email_safe"]
    assert result["requirements"]["mandatory"]["unmet"] == []


def test_each_and_requirement_must_be_met():
    vacancy = base_job()
    vacancy["description"] += " Excel and SAS are required."
    result = evaluate_job(vacancy, now=NOW)
    assert not result["eligible"]
    assert "SAS" in result["requirements"]["mandatory"]["unmet"]


def test_ambiguous_mixed_alternatives_fail_closed_for_autosend():
    vacancy = base_job()
    vacancy["description"] += " Excel and SAS or Python are required."
    result = evaluate_job(vacancy, now=NOW)
    assert not result["email_safe"]
    assert result["requirements"]["mandatory"]["unknown"]


@pytest.mark.parametrize("requirement", ["Bachelor's degree is required.", "An undergraduate degree is required.", "BCA is mandatory.", "BSc degree required.", "Bachelor's degree in any discipline is required.", "BSc in Data Analytics is required."])
def test_verified_completed_undergraduate_qualifications_pass(requirement):
    vacancy = base_job()
    vacancy["description"] += " " + requirement
    result = evaluate_job(vacancy, now=NOW)
    assert result["email_safe"], result["requirements"]
    assert not result["requirements"]["mandatory"]["unknown"]


@pytest.mark.parametrize("requirement", ["MBA degree is mandatory.", "BTech degree required.", "Master's degree required.", "PhD required."])
def test_unattested_mandatory_degrees_stay_rejected(requirement):
    vacancy = base_job()
    vacancy["description"] += " " + requirement
    result = evaluate_job(vacancy, now=NOW)
    assert not result["eligible"] and not result["email_safe"]


@pytest.mark.parametrize("requirement", ["Bachelor's degree in Computer Science required.", "BSc in Physics is required.", "Bachelor's degree in STEM is mandatory.", "A relevant degree is required."])
def test_degree_discipline_and_equivalence_are_not_invented(requirement):
    vacancy = base_job()
    vacancy["description"] += " " + requirement
    result = evaluate_job(vacancy, now=NOW)
    assert not result["email_safe"]
    assert result["requirements"]["mandatory"]["unknown"]


def test_uncompleted_education_cannot_satisfy_degree_requirement():
    from scout_matching import load_profile
    profile = load_profile()
    profile["education"] = [{"degree": "BCA", "completed": False}]
    vacancy = base_job()
    vacancy["description"] += " Bachelor's degree is required."
    assert not evaluate_job(vacancy, profile=profile, now=NOW)["email_safe"]
