from datetime import date
from scout_matching import evaluate_job, experience_years, load_profile, proof_points

NOW = date(2026, 10, 11)


def job(**changes):
    value = {"title":"Research Analyst","description":"Full-time day shift. Research and competitive analysis. 0-2 years experience.","location":"Gurugram, India","date_posted":"2026-10-10"}
    value.update(changes)
    return value


def test_research_match():
    result = evaluate_job(job(), now=NOW)
    assert result["eligible"] and result["email_safe"]
    assert result["family"] == "research" and result["tier"] == "core"


def test_experience_not_company_age():
    assert evaluate_job(job(description="Full-time research. Company established 20 years ago. Candidate requires 0-2 years experience."), now=NOW)["experience_min"] == 0


def test_full_time_tenure():
    result = evaluate_job(job(description="Full-time requires 1 year of professional experience. Research."), now=NOW)
    assert result["tier"] == "stretch" and not result["email_safe"]
    assert experience_years(load_profile(), NOW, True) == 2 / 12


def test_overlap_union():
    profile = {"experience":[{"start":"2024-06","end":"2024-08","kind":"internship"},{"start":"2024-06","end":"2024-07","kind":"internship"}]}
    assert experience_years(profile, NOW) == 3 / 12


def test_remote():
    assert not evaluate_job(job(location="Remote, USA only"), now=NOW)["eligible"]
    assert evaluate_job(job(location="Remote, India"), now=NOW)["email_safe"]
    assert not evaluate_job(job(location="Remote"), now=NOW)["email_safe"]


def test_hard_gates():
    for change in [{"title":"Senior Research Analyst"},{"employment_type":"CONTRACT"},{"description":"Full-time rotational shift"},{"salary_max_lpa":6},{"description":"Full-time MBA mandatory"},{"description":"Full-time 4 years of experience"},{"location":"Mumbai"},{"active_listing":False}]:
        assert not evaluate_job(job(**change), now=NOW)["eligible"]


def test_alias_and_unknown():
    assert evaluate_job(job(title="MIS Executive", description="Full-time Excel Power BI DAX"), now=NOW)["family"] == "reporting"
    result = evaluate_job(job(location="", description="Research SQL and competitive intelligence responsibilities"), now=NOW)
    assert not result["email_safe"] and "Location unconfirmed" in result["warnings"]
    assert "Some skills listed without implementation proof" in result["warnings"]


def test_proof_context():
    assert "internship" in " ".join(proof_points("data"))
    assert all("MAPE" not in v for v in proof_points("research"))


def test_decimal_experience_and_unknown_employment():
    result = evaluate_job(job(description="Full-time research. 1.5 years of professional experience"), now=NOW)
    assert result["experience_min"] == 1.5
    assert result["tier"] == "stretch"
    result = evaluate_job(job(employment_type="OTHER", description="Research 0-2 years experience"), now=NOW)
    assert not result["email_safe"]


def test_missing_body_expiry_and_unknown_experience():
    assert not evaluate_job(job(description=""), now=NOW)["eligible"]
    assert not evaluate_job(job(valid_through="2026-10-10", active_listing=True), now=NOW)["eligible"]
    result = evaluate_job(job(description="", body="Full-time research and competitive analysis for clients."), now=NOW)
    assert result["eligible"] and not result["email_safe"]


def test_two_to_five_lower_bound_and_malformed_minimum():
    result = evaluate_job(job(description="Full-time research and reports. 2–5 years experience."), now=NOW)
    assert result["experience_min"] == 2 and result["tier"] == "stretch"
    result = evaluate_job(job(minYears="nonsense", salary_max_lpa="nonsense"), now=NOW)
    assert not result["email_safe"]


def test_currency_and_foreign_boilerplate():
    for salary in ("₹6,00,000 per annum", "INR 40,000 per month"):
        assert not evaluate_job(job(description=job()["description"] + salary), now=NOW)["eligible"]
    assert evaluate_job(job(description=job()["description"] + " Salary 6–8 LPA"), now=NOW)["eligible"]
    assert not evaluate_job(job(location="Remote, US", description=job()["description"] + "Our offices include India."), now=NOW)["eligible"]


def test_degree_context_and_evidence_ranking():
    assert not evaluate_job(job(description=job()["description"] + " PhD required."), now=NOW)["eligible"]
    assert evaluate_job(job(description=job()["description"] + "MBA preferred."), now=NOW)["eligible"]
    research = evaluate_job(job(), now=NOW)
    data = evaluate_job(job(title="Data Analyst", description="Full-time day shift. Data analysis. 0-2 years experience."), now=NOW)
    assert research["score"] > data["score"]
