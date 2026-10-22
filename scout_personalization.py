"""Bounded, zero-cost email personalization from vacancy duties and resume facts.

Job text selects trusted phrases; it is never copied into candidate claims or
executed as instructions. This module does not change application eligibility.
"""
import re
from html.parser import HTMLParser


class _DescriptionParser(HTMLParser):
    """Preserve duty-list structure without splitting inline emphasis."""
    def __init__(self):
        super().__init__()
        self.parts = []
        self.ignored = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript", "svg"}:
            self.ignored += 1
        if not self.ignored and tag in {"p", "div", "section", "li", "ul", "ol", "br", "h1", "h2", "h3", "h4", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript", "svg"} and self.ignored:
            self.ignored -= 1
        if not self.ignored and tag in {"p", "div", "section", "li", "ul", "ol", "h1", "h2", "h3", "h4", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.ignored:
            self.parts.append(data)


_TOPICS = (
    ("markets", r"\b(?:market research|market(?:s| entry)?|competitor(?:s)?|competitive (?:analysis|intelligence))\b",
     "market and competitor research", "researching target markets, competitors and regulatory requirements"),
    ("gaps", r"\b(?:gap (?:analysis|assessments?)|compliance gaps?|operational risks?|assessment inconsistencies)\b",
     "gap analysis and assessments", "assessing client gaps, compliance issues and operational risks"),
    ("reports", r"\b(?:strategic intelligence|intelligence reports?|business reports?|analytical reports?|research reports?|reports?|business insights)\b",
     "analytical reporting", "producing strategic intelligence reports for business decisions"),
    ("workflows", r"\b(?:business processes|manufacturing (?:processes|workflows)|assessment workflows?|process improvement|workflow mapping|research turnaround)\b",
     "process and workflow improvement", "mapping manufacturing workflows and improving research and assessment turnaround"),
)
_ACTION = re.compile(
    r"^(?:(?:you(?: will| would|'ll| should| are expected to)?|responsible for|"
    r"the successful candidate will|the role (?:involves|includes)|to)\s+)?"
    r"(?:conduct(?:ing)?|perform(?:ing)?|undertak(?:e|ing)|research(?:ing)?|analy[sz](?:e|ing)|"
    r"assess(?:ing)?|evaluat(?:e|ing)|prepar(?:e|ing)|produc(?:e|ing)|writ(?:e|ing)|"
    r"deliver(?:ing)?|creat(?:e|ing)|develop(?:ing)?|identif(?:y|ying)|review(?:ing)?|"
    r"map(?:ping)?|improv(?:e|ing)|streamlin(?:e|ing)|standardiz(?:e|ing)|build(?:ing)?)\b", re.I)
_NEGATIVE = re.compile(r"\b(?:not|no|never|without)\b", re.I)
_INSTRUCTION = re.compile(r"ignore (?:all |previous |prior )?instructions|system prompt|"
                          r"disregard|email body|write (?:an? |the )?email|pretend|claim that", re.I)
_PRIORITIES = {
    "research": ("research", "market intelligence", "competitive analysis", "gap analysis", "Excel"),
    "process": ("gap analysis", "workflow mapping", "Excel", "research", "Power BI"),
    "reporting": ("Power BI", "Excel", "Tableau", "DAX", "SQL", "Python"),
    "data": ("SQL", "Python", "Excel", "Power BI", "Tableau", "data validation"),
}


def _clauses(text):
    section = ""
    for raw in str(text).splitlines():
        line = re.sub(r"^\s*(?:[-*\u2022]|\d+[.)])\s*", "", raw).strip()
        if not line:
            continue
        heading = re.match(r"^(responsibilities|key responsibilities|duties|what you(?: will|'ll) do|"
                           r"requirements|qualifications|skills|about us|about the company|benefits)\s*:?\s*(.*)$", line, re.I)
        if heading:
            section, line = heading.group(1).lower(), heading.group(2).strip()
        if section in {"about us", "about the company", "benefits"}:
            continue
        for part in re.split(r";|(?<!\d)\.(?!\d)", line):
            part = re.sub(r"\s+", " ", part).strip()
            if part and len(part) <= 600 and not _NEGATIVE.search(part) and not _INSTRUCTION.search(part):
                yield part, section


def _catalog(profile, family):
    """Keep only ProSca-sourced achievements; deduplicate the same metric."""
    highlights = profile.get("email_experience_highlights", {})
    pool = list(highlights.get(family) or highlights.get("data") or [])
    pool += [point for points in highlights.values() for point in points]
    facts, seen = [], set()
    for value in pool:
        if not isinstance(value, str) or not value.startswith("ProSca:"):
            continue
        point = re.sub(r"\s+", " ", value.removeprefix("ProSca:")).strip()
        topic = next((key for key, marker in (("reports", "12+"), ("markets", "5+"),
                      ("workflows", "20%"), ("gaps", "gap analysis")) if marker in point.lower()), None)
        if topic and topic not in seen:
            facts.append((topic, point))
            seen.add(topic)
    return facts


def personalize_email(text, profile, family):
    parser = _DescriptionParser()
    parser.feed(str(text)[:30000])
    clauses = list(_clauses("".join(parser.parts)[:24000]))
    facts = _catalog(profile, family)
    known_topics = {topic for topic, _ in facts}
    matches = []
    duty_sections = {"responsibilities", "key responsibilities", "duties", "what you will do", "what you'll do"}
    for clause, section in clauses:
        if section in {"requirements", "qualifications", "skills"}:
            continue
        if not _ACTION.search(clause) and section not in duty_sections:
            continue
        for topic, pattern, label, experience in _TOPICS:
            if topic in known_topics and re.search(pattern, clause, re.I) and topic not in [row[0] for row in matches]:
                matches.append((topic, label, experience))
    topics = [row[0] for row in matches]
    ranked = sorted(enumerate(facts), key=lambda item: (
        topics.index(item[1][0]) if item[1][0] in topics else len(topics), item[0]))
    points = [fact[1] for _, fact in ranked[:3]]
    alignment = (f"The role's focus on {matches[0][1]} aligns with my experience in {matches[0][2]}."
                 if matches else "")

    # Mention only actual resume skills found positively in the vacancy. A
    # knowledge-only entry never becomes a claim of professional implementation.
    priorities = [value.lower() for value in _PRIORITIES.get(family, _PRIORITIES["data"])]
    listed = {str(value).lower() for value in profile.get("listed_only_skills", [])}
    skills = []
    for value in profile.get("skills", []):
        if not isinstance(value, str):
            continue
        pattern = r"(?<!\w)" + re.escape(value).replace(r"\ ", r"\s+") + r"(?!\w)"
        if any(re.search(pattern, clause, re.I) for clause, _ in clauses):
            skills.append(value)
    skills.sort(key=lambda value: priorities.index(value.lower()) if value.lower() in priorities else len(priorities))
    skills = [value[:1].upper() + value[1:] + (" (knowledge)" if value.lower() in listed else "") for value in skills[:4]]
    # These case studies were checked on the existing portfolio. Names only:
    # links stay grouped in the user's footer, and project work never becomes
    # ProSca employment or unverified commercial impact.
    positive = " ".join(clause for clause, _ in clauses).lower()
    example = ""
    if family in {"reporting", "data"}:
        if re.search(r"healthcare|mental health|public health", positive) and re.search(r"python|tableau|data analysis", positive):
            example = "Mental Health Data Insights — a Python and Tableau case study"
        elif re.search(r"power\s*bi", positive) and re.search(r"dashboard|sales|inventory|retail|reporting", positive):
            example = "Blinkit Sales Performance Analysis — a Power BI, Excel and DAX case study"
        elif re.search(r"forecasting|time.series", positive) and re.search(r"python|lstm", positive):
            example = "Stock Price Prediction using LSTM — a forecasting project, not professional ML employment"
    return {"alignment": alignment, "skills": skills, "highlights": points, "portfolio_example": example,
            "matched_topics": topics, "method": "verified_rules" if alignment or skills else "neutral_fallback"}
