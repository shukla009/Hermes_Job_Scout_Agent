"""Deterministic evidence-aware matching, with no model/network dependency."""
import json
import re
from datetime import date, datetime, timezone, timedelta
import math
from pathlib import Path


def load_profile():
    profile = json.loads((Path(__file__).parent / "config/candidate_profile.json").read_text(encoding="utf-8"))
    return profile


def proof_points(family, profile=None):
    return list((profile or load_profile()).get("evidence", {}).get(family, []))


def experience_years(profile, now, full_time=False):
    def month(s):
        y, m = map(int, s[:7].split("-"))
        return y * 12 + m - 1
    months = set()
    current = month(now.isoformat())
    for item in profile.get("experience", []):
        if full_time and item.get("kind") != "full_time":
            continue
        end = min(month(item["end"]) + 1, current) if item.get("end") else current
        months.update(range(month(item["start"]), end))
    return len(months) / 12


def _experience(text):
    values = []
    for sentence in re.split(r"\n|;|(?<!\d)\.(?!\d)", text):
        if re.search(r"company|founded|established|history", sentence) and not re.search(r"candidate|required|requires|experience", sentence):
            continue
        range_pattern = r"(\d+(?:\.\d+)?)\s*(?:-|–|to)\s*\d+(?:\.\d+)?\s*(?:years?|yrs?)"
        values.extend(float(v) for v in re.findall(range_pattern, sentence))
        sentence = re.sub(range_pattern, "", sentence)
        for pattern in (r"(\d+(?:\.\d+)?)\s*(?:-|–|to)\s*\d+(?:\.\d+)?\s*(?:years?|yrs?)", r"(?:minimum|at least|requires?|experience(?: of|:)?)\s*(\d+(?:\.\d+)?)\s*\+?\s*(?:years?|yrs?)", r"(\d+(?:\.\d+)?)\s*\+?\s*(?:years?|yrs?)\s*(?:of\s+)?(?:relevant|professional|work|industry|experience)"):
            values.extend(float(v) for v in re.findall(pattern, sentence))
    return max(values) if values else None


def evaluate_job(post, profile=None, now=None):
    profile = profile or load_profile()
    now = now or datetime.now(timezone(timedelta(hours=5, minutes=30))).date()
    if isinstance(now, datetime):
        if now.tzinfo:
            now = now.astimezone(timezone(timedelta(hours=5, minutes=30)))
        now = now.date()
    title = str(post.get("job_title") or post.get("title") or "").lower()
    text = "\n".join(str(post.get(k) or "") for k in ("description", "source_text", "body", "snippet", "requirements")).lower()
    location = str(post.get("location") or post.get("job_location") or "").lower()
    family = next((name for name, pattern in (("research", r"research|market intelligence|competitive intelligence|market insights|consumer insights|research executive"), ("reporting", r"\bmis\b|reporting|power bi|\bbi\b|business intelligence|dashboard analyst|tableau analyst|data visuali[sz]ation"), ("process", r"business analyst|business process analyst|process analyst|operations (?:analyst|insights)|business operations analyst|process improvement analyst"), ("data", r"data (?:analyst|quality|operations|validation|management)|analytics (?:analyst|associate)|insights analyst|research data analyst")) if re.search(pattern, title)), None)
    if not family and re.search(r"associate|junior|analyst i\b", title) and re.search(r"data analysis|dashboard|research|reporting", text):
        family = "data"
    result = {"eligible":False,"reason":"","family":family,"tier":"core","score":0,"evidence":proof_points(family, profile),"warnings":[],"email_safe":False,"matched_skills":[],"experience_min":None}
    def reject(reason):
        result["reason"] = reason
        return result
    for key in ("valid_through", "validThrough", "application_deadline"):
        if post.get(key):
            try:
                if date.fromisoformat(str(post[key])[:10]) < now:
                    return reject("Application deadline expired")
            except ValueError:
                result["warnings"].append("Application deadline unverified")
    if post.get("active_listing") is False:
        return reject("Vacancy closed")
    if not family:
        return reject("Outside supported role families")
    if re.search(r"field sales|on.ground user acquisition|merchant acquisition|door.to.door|telecalling|cold calling|sales targets", text) and not re.search(r"(?:analyse|analyze|analysis of).{0,30}(?:sales|acquisition) data", text):
        return reject("Sales/acquisition duties rather than analyst work")
    if re.search(r"(?:must have|required|mandatory).{0,100}(?:server installation|cloud administration|team management)|(?:server installation|cloud administration).{0,50}(?:required|mandatory)", text):
        return reject("Mandatory administration or management experience not evidenced")
    responsibility_patterns = {"research":r"research|market intelligence|competitive analysis|market.entry|intelligence reports|insights", "reporting":r"dashboard|reporting|reports|power bi|tableau|excel|\bmis\b", "process":r"gap analysis|gap assessment|workflow|process|business requirements|stakeholder|operations analysis", "data":r"data analysis|data cleaning|data validation|data quality|analyse|analyze|analytics|python|pandas|dashboard|\bsql\b"}
    duties = re.findall(responsibility_patterns[family], text)
    if len(text.strip()) < 35 or not duties:
        return reject("Usable responsibilities not available")
    if re.search(r"\b(?:senior|sr\.?|manager|director|head|principal|lead)\b", title):
        return reject("Senior or management vacancy")
    employment = str(post.get("employment_type") or post.get("employmenttype") or "").lower()
    if re.search(r"internship|\bintern\b|part.time|temporary|contract|freelance", title + " " + employment) or re.search(r"(?:employment|job|position) type\s*[:\-]?\s*(?:contract|temporary|part.time|internship)|(?:this is|we offer|position is|role is)\s*(?:an?\s+)?(?:internship|temporary|contract|part.time)", text):
        return reject("Not permanent full-time")
    if not re.search(r"full[ _-]?time|permanent", employment + " " + text):
        result["warnings"].append("Full-time employment not confirmed")
    if re.search(r"night shift|overnight|rotational shifts?|rotating shifts?|us shifts?|u\.s\. shifts?", text):
        return reject("Incompatible shift")
    if not re.search(r"day shift|daytime|general shift", text):
        result["warnings"].append("Shift undisclosed")
    ncr = bool(re.search(r"\b(?:delhi|noida|gurgaon|gurugram|ncr|faridabad|ghaziabad)\b", location))
    remote = bool(re.search(r"remote|work from home|wfh", location + " " + title))
    india = bool(re.search(r"\bindia\b|india.based|anywhere in india", location + " " + text))
    if re.search(r"(?:us|usa|united states|uk|canada|europe)\s*(?:only|based)|(?:only|based in)\s*(?:the\s+)?(?:us|usa|united states|uk|canada)", location + " " + text):
        return reject("Foreign-only location")
    if remote and re.search(r"\b(?:usa|united states|united kingdom|canada|europe|us|uk)\b", location) and not re.search(r"\bindia\b", location):
        return reject("Foreign remote location")
    location_ok = ncr or (remote and india)
    if location and not location_ok and not remote:
        return reject("Outside Delhi-NCR or India-remote")
    if not location_ok:
        result["warnings"].append("Remote country eligibility unconfirmed" if remote else "Location unconfirmed")
    salary = post.get("salary_max_lpa")
    try:
        salary = float(salary) if salary is not None else None
        if salary is not None and (not math.isfinite(salary) or salary <= 0):
            salary = None
    except (ValueError, TypeError):
        salary = None
        result["warnings"].append("Salary value unverified")
    if salary is None:
        ranges = re.findall(r"(\d+(?:\.\d+)?)\s*(?:-|–|to)\s*(\d+(?:\.\d+)?)\s*(?:lpa|lakhs? per annum)", text)
        singles = re.findall(r"(?:up to|maximum|salary[:\s]+)\s*(?:₹|rs\.?|inr)?\s*(\d+(?:\.\d+)?)\s*lpa", text)
        salary = max([float(b) for a,b in ranges] + [float(v) for v in singles], default=None)
        if salary is None:
            currency_pattern = r"(?:₹|inr|rs\.?)\s*([\d,]+(?:\.\d+)?)\s*(?:-|–|to)?\s*(?:₹|inr|rs\.?)?\s*([\d,]+(?:\.\d+)?)?\s*(per month|monthly|/month|per annum|annual|yearly|/year)"
            amounts = []
            for low, high, period in re.findall(currency_pattern, text):
                value = float((high or low).replace(",", ""))
                amounts.append(value * (12 if period in ("per month", "monthly", "/month") else 1) / 100000)
            salary = max(amounts, default=None)
    if salary is not None and float(salary) < profile.get("preferences", {}).get("minimum_salary_lpa", 6.5):
        return reject("Stated maximum salary below preference")
    if salary is None:
        result["warnings"].append("Salary undisclosed")
    for qualification in re.split(r"\n|;|(?<!\d)\.(?!\d)", text):
        if re.search(r"(?:mba|b\.?tech|m\.?tech|ph\.?d)\s*(?:degree\s*)?(?:is\s+)?(?:mandatory|required|only)|(?:must have|requires?)\s*(?:an?\s+)?(?:mba|b\.?tech|m\.?tech|ph\.?d)\b", qualification) and not re.search(r"bca|b\.?sc|equivalent|related degree|preferred|optional", qualification):
            return reject("Mandatory degree not evidenced")
    if re.search(r"(?:mandatory|required|must have).{0,60}(?:fabric certification|production sql pipelines|azure certification)|(?:fabric certification|production sql pipelines|azure certification).{0,30}(?:mandatory|required)", text):
        return reject("Mandatory specialised qualification not evidenced")
    minimum = _experience(text)
    for key in ("experience_min", "minYears", "minimum_years_experience"):
        if post.get(key) is not None:
            try:
                value = float(post[key])
                if math.isfinite(value) and value >= 0:
                    minimum = max(minimum or 0, value)
                else:
                    result["warnings"].append("Experience value unverified")
            except (ValueError, TypeError):
                result["warnings"].append("Experience value unverified")
    result["experience_min"] = minimum
    if minimum is not None and minimum > 3:
        return reject("Experience exceeds stretch limit")
    years = experience_years(profile, now, bool(re.search(r"full.time experience|post.qualification|professional experience", text)))
    if minimum is not None and minimum > years:
        result["tier"] = "stretch"
        result["warnings"].append("Experience stretch; internships are not full-time employment")
    if minimum is None:
        result["warnings"].append("Experience requirement undisclosed")
    skills = [s for s in profile.get("skills", []) if re.search(r"(?<!\w)" + re.escape(s.lower()) + r"(?!\w)", title + " " + text)]
    result["matched_skills"] = skills
    listed = set(s.lower() for s in profile.get("listed_only_skills", []))
    evidenced = [s for s in skills if s.lower() not in listed]
    if any(s.lower() in listed for s in skills):
        result["warnings"].append("Some skills listed without implementation proof")
    freshness = 5
    posted = post.get("date_posted") or post.get("published_at")
    if posted:
        try:
            age = (now - date.fromisoformat(str(posted)[:10])).days
            freshness = 10 if 0 <= age <= 7 else 7 if 0 <= age <= 30 else 3
        except ValueError:
            result["warnings"].append("Publication date unverified")
    duty_score = min(35, 20 + len(set(duties))*5)
    result["score"] = min(100,duty_score + (20 if family in ("research","process") else 15) + min(5,len(evidenced)) + (20 if result["tier"] == "core" else 10) + (10 if location_ok else 0) + freshness)
    result["eligible"] = result["score"] >= (70 if result["tier"] == "core" else 60)
    result["reason"] = "Relevant evidence-backed match" if result["eligible"] else "Insufficient evidence-backed fit"
    entry_level = bool(re.search(r"entry.level|freshers?|no (?:prior )?experience|graduate opportunity", text))
    result["email_safe"] = result["eligible"] and result["tier"] == "core" and location_ok and (minimum is not None or entry_level) and "Experience value unverified" not in result["warnings"] and "Full-time employment not confirmed" not in result["warnings"]
    return result
