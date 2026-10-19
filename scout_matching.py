"""Deterministic evidence-aware matching, with no model/network dependency."""
import json
import re
from datetime import date, datetime, timezone, timedelta
import math
from pathlib import Path
from urllib.parse import urlparse


_REQUIREMENT_TERMS = {
    "excel": ("Excel", r"\b(?:microsoft\s+)?excel\b"),
    "power bi": ("Power BI", r"\bpower\s*bi\b"),
    "tableau": ("Tableau", r"\btableau\b"),
    "sql": ("SQL", r"\bsql\b"),
    "python": ("Python", r"\bpython\b"),
    "r": ("R", r"(?:\br\s+programming\b|\bprogramming\s+in\s+r\b)"),
    "sas": ("SAS", r"\bsas\b"),
    "power query": ("Power Query", r"\bpower\s+query\b"),
    "dax": ("DAX", r"\bdax\b"),
    "pandas": ("Pandas", r"\bpandas\b"),
    "numpy": ("NumPy", r"\bnumpy\b"),
    "etl": ("ETL", r"\betl\b"),
    "mysql": ("MySQL", r"\bmysql\b"),
    "oracle": ("Oracle", r"\boracle\b"),
    "snowflake": ("Snowflake", r"\bsnowflake\b"),
    "databricks": ("Databricks", r"\bdatabricks\b"),
    "spark": ("Spark", r"\b(?:apache\s+)?spark\b"),
    "dbt": ("dbt", r"\bdbt\b"),
    "azure": ("Azure", r"\bazure\b"),
    "aws": ("AWS", r"\b(?:aws|amazon\s+web\s+services)\b"),
    "gcp": ("GCP", r"\b(?:gcp|google\s+cloud)\b"),
    "qlik": ("Qlik", r"\bqlik(?:view|sense)?\b"),
    "looker": ("Looker", r"\blooker\b"),
    "machine learning": ("Machine learning", r"\bmachine\s+learning\b"),
    "financial modelling": ("Financial modelling", r"\bfinancial\s+model(?:l)?ing\b"),
    "market research": ("Market research", r"\bmarket\s+research\b"),
}

_DOMAIN_TERMS = {
    "healthcare": ("Healthcare domain", r"\bhealthcare\b"),
    "banking": ("Banking domain", r"\bbanking\b"),
    "insurance": ("Insurance domain", r"\binsurance\b"),
    "fintech": ("Fintech domain", r"\bfintech\b"),
    "fmcg": ("FMCG domain", r"\bfmcg\b"),
    "pharma": ("Pharma domain", r"\bpharma(?:ceutical)?\b"),
    "telecom": ("Telecom domain", r"\btelecom(?:munications)?\b"),
    "e-commerce": ("E-commerce domain", r"\be[ -]?commerce\b"),
}

_TRUSTED_ATS_HOSTS = (
    "greenhouse.io", "lever.co", "smartrecruiters.com", "ashbyhq.com",
    "workable.com", "myworkdayjobs.com", "oraclecloud.com", "applytojob.com",
)
_SHORTENER_HOSTS = {
    "bit.ly", "tinyurl.com", "t.co", "goo.gl", "ow.ly", "buff.ly",
    "cutt.ly", "rb.gy", "shorturl.at", "is.gd", "rebrand.ly",
}
_FREE_MAIL_HOSTS = {
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.in", "outlook.com",
    "hotmail.com", "live.com", "rediffmail.com", "proton.me", "protonmail.com",
}


def _sentences(text):
    return [part.strip() for part in re.split(r"\n|;|(?<!\d)\.(?!\d)", text) if part.strip()]


def _requirement_clauses(text):
    """Yield clauses plus bounded context inherited from requirement list headings."""
    section = None
    section_items = 0
    section_chars = 0
    heading = re.compile(
        r"^\s*(required(?: skills?| qualifications?)?|requirements?|mandatory(?: skills?| qualifications?)?|"
        r"must[- ]have(?: skills?)?|minimum qualifications?|essential qualifications?|qualifications?|"
        r"preferred(?: skills?| qualifications?)?|nice to have|good to have|desirable(?: skills?| qualifications?)?)\s*(?::\s*(.*))?$",
        re.I,
    )
    new_section = re.compile(r"^\s*[a-z][^:\n]{1,80}:\s*$", re.I)
    bullet = re.compile(r"^\s*(?:[-*\u2022]|\d+[.)]|[a-z][.)])\s+(.+)$", re.I)
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = heading.match(line)
        if match:
            label, remainder = match.groups()
            section = "preferred" if re.search(r"preferred|nice to have|good to have|desirable", label, re.I) else "mandatory"
            section_items = 0
            section_chars = 0
            if (remainder or "").strip():
                for clause in _sentences(remainder):
                    yield clause, section
            continue
        if new_section.match(line):
            section = None
            section_items = 0
            section_chars = 0
            continue
        item = bullet.match(line)
        if section and item and section_items < 12 and section_chars + len(line) <= 1200:
            clause = item.group(1).strip()
            section_items += 1
            section_chars += len(line)
            if clause:
                yield clause, section
            continue
        section_chars += len(line)
        if section_chars > 1200:
            section = None
        for clause in _sentences(line):
            yield clause, None


def _has_requirement_marker(sentence, preferred=False):
    if preferred:
        return bool(re.search(r"\b(?:preferred|nice to have|good to have|desirable|a plus|bonus)\b", sentence))
    return bool(re.search(
        r"\b(?:must have|mandatory|essential qualification|required skills?|required experience|"
        r"minimum qualification|minimum experience|need to have|proficiency (?:in|with).{0,60}required|"
        r"experience (?:in|with).{0,60}required|(?:is|are) (?:mandatory|required|essential))\b|"
        r"\b(?:mandatory|required|essential)\s*(?::|$)", sentence
    ))


def _profile_evidence_text(profile):
    evidence = " ".join(
        str(point) for points in profile.get("evidence", {}).values() for point in points
    ).lower()
    skills = {str(skill).lower() for skill in profile.get("skills", [])}
    listed_only = {str(skill).lower() for skill in profile.get("listed_only_skills", [])}
    return evidence, skills, listed_only


def _assess_requirements(text, profile):
    """Classify only explicit requirement clauses; never infer missing experience."""
    assessment = {
        "mandatory": {"met": [], "unmet": [], "unknown": []},
        "preferred": {"met": [], "unmet": [], "unknown": []},
    }
    evidence, skills, listed_only = _profile_evidence_text(profile)
    found = set()
    for sentence, inherited in _requirement_clauses(text):
        explicit_preferred = _has_requirement_marker(sentence, preferred=True)
        explicit_mandatory = not explicit_preferred and _has_requirement_marker(sentence)
        group = "preferred" if explicit_preferred else "mandatory" if explicit_mandatory else inherited
        if not group:
            continue
        matched = False
        for key, (label, pattern) in {**_REQUIREMENT_TERMS, **_DOMAIN_TERMS}.items():
            if not re.search(pattern, sentence):
                continue
            identity = (group, key)
            if identity in found:
                continue
            found.add(identity); matched = True
            if key in _DOMAIN_TERMS:
                status = "met" if re.search(pattern, evidence) else "unmet"
            elif key == "market research" and ({"research", "market intelligence"} & skills):
                status = "met"
            elif key in listed_only:
                status = "unknown"
                label += " (listed, implementation evidence unavailable)"
            else:
                status = "met" if key in skills or re.search(pattern, evidence) else "unmet"
            assessment[group][status].append(label)
        # Surface an explicit but unrecognised qualification instead of silently
        # treating it as satisfied. Keep the excerpt bounded for state/Telegram.
        if not matched and re.search(r"\b(?:technical|experience|proficiency|knowledge|certification|degree|domain|qualification)\b", sentence):
            assessment[group]["unknown"].append(re.sub(r"\s+", " ", sentence)[:180])
    return assessment


def _hostname(value):
    try:
        return (urlparse(str(value or "")).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def _same_site(host, official):
    return bool(host and official and (host == official or host.endswith("." + official) or official.endswith("." + host)))


def _official_corroboration(post, application_host):
    if post.get("official_corroborated") is True:
        return True
    official_host = _hostname(post.get("official_url") or post.get("company_official_url"))
    if _same_site(application_host, official_host):
        return True
    return post.get("source_type") == "direct_apply" and any(
        application_host == host or application_host.endswith("." + host) for host in _TRUSTED_ATS_HOSTS
    )


def _edit_distance(left, right):
    previous = list(range(len(right) + 1))
    for i, a in enumerate(left, 1):
        current = [i]
        for j, b in enumerate(right, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (a != b)))
        previous = current
    return previous[-1]


def _site_label(host):
    parts = host.split(".")
    if len(parts) >= 3 and parts[-2] in {"co", "com", "org", "net"}:
        return parts[-3]
    return parts[-2] if len(parts) >= 2 else parts[0]


def _brand_label(host):
    label = re.sub(r"[^a-z0-9]", "", _site_label(host))
    for suffix in ("careers", "career", "jobs", "job", "hiring", "recruitment"):
        if label.endswith(suffix) and len(label) > len(suffix) + 2:
            label = label[:-len(suffix)]
    return label


def _assess_risk(post, text):
    """Return explainable signals; signals are not a fraud determination."""
    flags = []
    def add(code, severity, detail):
        if not any(flag["code"] == code for flag in flags):
            flags.append({"code": code, "severity": severity, "detail": detail})

    def affirmative(pattern):
        """Ignore a risk phrase only when its own nearby clause negates it."""
        for match in re.finditer(pattern, text):
            prefix = text[max(0, match.start() - 70):match.start()]
            if re.search(r"\b(?:never|do not|does not|won't|will not|no candidate (?:is|will be))\b[^.;]{0,60}$", prefix):
                continue
            return True
        return False

    if affirmative(
        r"\b(?:pay|submit|send|provide|make)\b.{0,45}\b(?:registration|application|processing|security|refundable)\s+(?:fee|deposit|payment)\b|"
        r"\b(?:fee|deposit|payment)\b.{0,35}\b(?:required|mandatory|before (?:joining|interview))\b"
    ):
        add("fee_or_deposit_request", "critical", "The listing asks the candidate for a fee, deposit, or payment.")
    if affirmative(
        r"\b(?:buy|purchase|pay for|send money for)\b.{0,55}\b(?:equipment|laptop|computer|software|training|course|certification)\b|"
        r"\b(?:training|course|certification)\s+fee\b"
    ):
        add("equipment_or_training_payment", "critical", "The listing asks the candidate to pay for equipment or training.")
    if re.search(
        r"\b(?:send|share|provide|submit|enter)\b.{0,45}\b(?:password|one[ -]?time password|otp|(?:bank|atm|security) pin|bank (?:login|account details)|credit card|debit card|cvv)\b|"
        r"\b(?:password|otp|cvv)\b.{0,25}\b(?:required|mandatory)\b", text
    ):
        add("credential_or_financial_secret_request", "critical", "The listing requests credentials or financial secrets.")

    application_url = post.get("application_url") or post.get("href") or post.get("url") or ""
    host = _hostname(application_url)
    official_host = _hostname(post.get("official_url") or post.get("company_official_url"))
    if host in _SHORTENER_HOSTS:
        add("shortened_application_url", "high", "The application URL hides its final destination behind a shortener.")
    if host.startswith("xn--") or ".xn--" in host:
        add("internationalized_application_domain", "high", "The application domain uses punycode and needs manual verification.")
    trusted_ats = any(host == item or host.endswith("." + item) for item in _TRUSTED_ATS_HOSTS)
    if host and official_host and not trusted_ats and not _same_site(host, official_host):
        left, right = _brand_label(host), _brand_label(official_host)
        if left and right and (_edit_distance(left, right) <= 2 or left.startswith(right) or right.startswith(left)):
            add("lookalike_application_domain", "high", "The application domain resembles, but is not, the supplied official domain.")

    emails = post.get("source_emails") or []
    if isinstance(emails, str):
        emails = [emails]
    if any(str(email).rsplit("@", 1)[-1].lower() in _FREE_MAIL_HOSTS for email in emails if "@" in str(email)):
        add("free_mail_contact", "review", "A public free-mail address is a risk signal, not proof of fraud.")

    agency = bool(re.search(r"\b(?:on behalf of (?:our|a) client|confidential client|staffing agency|recruitment agency|recruiting agency)\b", text))
    client = str(post.get("client_company") or post.get("hiring_company") or "").strip()
    if agency and not client:
        add("agency_client_identity_unverified", "high", "The intermediary does not identify the hiring employer.")
    elif agency:
        add("agency_intermediary", "review", f"The vacancy is represented by an agency for {client[:80]}.")

    official = _official_corroboration(post, host)
    severity = "critical" if any(f["severity"] == "critical" for f in flags) else "high" if any(f["severity"] == "high" for f in flags) else "review" if flags else "low"
    return {
        "level": severity,
        "flags": flags,
        "official_corroboration": official,
        "requires_review": bool(flags),
        "resume_autosend_allowed": not any(f["severity"] == "critical" for f in flags) and not (
            any(f["severity"] in {"high", "review"} for f in flags) and not official
        ),
        "note": "Risk signals are deterministic review cues, not a fraud probability or accusation.",
    }


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
        candidate_context = bool(re.search(
            r"\b(?:candidates?|applicants?|you (?:have|bring|possess|need)|must have|required|requires|"
            r"minimum|at least|looking for|seeking|should have|need(?:s|ed)?(?: to have)?)\b", sentence
        ))
        employer_context = bool(re.search(
            r"\b(?:we|our (?:team|company|firm|organisation|organization)|the (?:company|team|firm)|"
            r"this (?:company|team|firm))\b.{0,50}\b(?:bring|have|has|offer|combine|boast|possess|with)\b",
            sentence,
        ))
        if employer_context and not candidate_context:
            continue
        if re.search(r"company|founded|established|history", sentence) and not candidate_context:
            continue
        range_pattern = r"(\d+(?:\.\d+)?)\s*(?:-|–|to)\s*\d+(?:\.\d+)?\s*(?:years?|yrs?)"
        values.extend(float(v) for v in re.findall(range_pattern, sentence))
        sentence = re.sub(range_pattern, "", sentence)
        for pattern in (r"(\d+(?:\.\d+)?)\s*(?:-|–|to)\s*\d+(?:\.\d+)?\s*(?:years?|yrs?)", r"(?:minimum|at least|requires?|experience(?: of|:)?)\s*(\d+(?:\.\d+)?)\s*\+?\s*(?:years?|yrs?)", r"(\d+(?:\.\d+)?)\s*\+?\s*(?:years?|yrs?)\s*(?:of\s+)?(?:relevant|professional|work|industry|experience)"):
            values.extend(float(v) for v in re.findall(pattern, sentence))
        if candidate_context:
            values.extend(float(v) for v in re.findall(
                r"(\d+(?:\.\d+)?)\s*\+?\s*(?:years?|yrs?)\s+(?:of\s+)?[a-z /+-]{1,35}\s+experience",
                sentence,
            ))
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
    requirements = _assess_requirements(text, profile)
    risk = _assess_risk(post, text)
    result = {
        "eligible": False, "reason": "", "family": family, "tier": "core", "score": 0,
        "score_kind": "deterministic_fit_points_not_probability", "score_breakdown": {},
        "evidence": proof_points(family, profile), "warnings": [], "email_safe": False,
        "resume_autosend_allowed": False, "matched_skills": [], "experience_min": None,
        "requirements": requirements, "review_required": False, "review_reasons": [],
        "risk_assessment": risk, "risk_flags": risk["flags"],
    }
    def reject(reason):
        result["reason"] = reason
        result["score_breakdown"] = {"hard_gate": 0}
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
        requirements["mandatory"]["unmet"].append("Administration or team-management experience")
        return reject("Mandatory administration or management experience not evidenced")
    if requirements["mandatory"]["unmet"]:
        return reject("Mandatory requirement not evidenced: " + ", ".join(requirements["mandatory"]["unmet"][:3]))
    if requirements["mandatory"]["unknown"]:
        result["review_required"] = True
        result["review_reasons"].append("Mandatory requirement evidence is unverified")
        result["warnings"].append(
            "Mandatory requirement unverified: " + ", ".join(requirements["mandatory"]["unknown"][:3])
        )
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
            requirements["mandatory"]["unmet"].append("Specified mandatory degree")
            return reject("Mandatory degree not evidenced")
    if re.search(r"(?:mandatory|required|must have).{0,60}(?:fabric certification|production sql pipelines|azure certification)|(?:fabric certification|production sql pipelines|azure certification).{0,30}(?:mandatory|required)", text):
        requirements["mandatory"]["unmet"].append("Specialised certification or production implementation")
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
    entry_level = bool(re.search(r"entry.level|freshers?|no (?:prior )?experience|graduate opportunity", text))
    if minimum is not None and minimum > 3:
        requirements["mandatory"]["unmet"].append(f"Minimum {minimum:g} years experience")
        return reject("Experience exceeds stretch limit")
    years = experience_years(profile, now, bool(re.search(r"full.time experience|post.qualification|professional experience", text)))
    if minimum is not None and minimum > years:
        requirements["mandatory"]["unmet"].append(f"Minimum {minimum:g} years experience")
        result["tier"] = "stretch"
        result["warnings"].append("Experience stretch; internships are not full-time employment")
        result["review_required"] = True
        result["review_reasons"].append("Mandatory experience requirement exceeds evidenced tenure")
    elif minimum is not None:
        requirements["mandatory"]["met"].append(f"Minimum {minimum:g} years experience")
    if minimum is None and not entry_level:
        requirements["mandatory"]["unknown"].append("Minimum experience not stated")
        result["warnings"].append("Experience requirement undisclosed")
        result["review_required"] = True
        result["review_reasons"].append("Minimum experience requirement is unknown")
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
    preferred_misses = len(requirements["preferred"]["unmet"]) + len(requirements["preferred"]["unknown"])
    preferred_penalty = min(10, preferred_misses * 2)
    if requirements["preferred"]["unmet"]:
        result["warnings"].append(
            "Preferred requirements not evidenced: " + ", ".join(requirements["preferred"]["unmet"][:3])
        )
    duty_score = min(35, 20 + len(set(duties))*5)
    result["score_breakdown"] = {
        "responsibility_alignment": duty_score,
        "role_family_evidence": 20 if family in ("research", "process") else 15,
        "evidenced_skills": min(5, len(evidenced)),
        "experience_fit": 20 if result["tier"] == "core" else 10,
        "location_fit": 10 if location_ok else 0,
        "freshness": freshness,
        "preferred_requirement_penalty": -preferred_penalty,
    }
    result["score"] = max(0, min(100, sum(result["score_breakdown"].values())))
    result["eligible"] = result["score"] >= (70 if result["tier"] == "core" else 60)
    result["reason"] = "Relevant evidence-backed match" if result["eligible"] else "Insufficient evidence-backed fit"
    mandatory_known = not requirements["mandatory"]["unknown"] and not requirements["mandatory"]["unmet"]
    result["email_safe"] = (
        result["eligible"] and result["tier"] == "core" and location_ok and
        (minimum is not None or entry_level) and mandatory_known and
        "Experience value unverified" not in result["warnings"] and
        "Full-time employment not confirmed" not in result["warnings"] and
        risk["resume_autosend_allowed"]
    )
    if risk["requires_review"]:
        result["review_required"] = True
        result["review_reasons"].append("Recruitment safety signals require review")
        details = [flag.get("detail", "") for flag in risk["flags"] if flag.get("detail")]
        if details:
            result["warnings"].append("Recruitment safety review: " + "; ".join(details[:2]))
    result["resume_autosend_allowed"] = result["email_safe"]
    return result
