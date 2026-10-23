"""Bounded encrypted decision evidence and honest discovery funnel measurements.

This module never sends an application or calls a model/search service. Public
reports contain counters only; vacancy text belongs inside encrypted scout state.
"""
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from functools import lru_cache

from scout_state import IST

MAX_REVIEWS = 80
MAX_REVIEW_BYTES = 700000
MAX_DAILY_IDENTITIES = 2000
COUNT_KEYS = {"new_to_scout", "rechecked", "introduction_rechecks", "duplicates_in_batch", "unique_checked",
              "fresh_checks", "suitable_checks", "reachable_checks", "application_emails_accepted",
              "portal_confirmed", "introductions_accepted"}
STAGE_KEYS = {"checked", "new_to_scout", "rechecked", "introduction_rechecks", "fresh", "suitable",
              "held", "rejected", "reachable", "introductions_accepted", "application_emails_accepted", "portal_confirmed"}


@lru_cache(maxsize=1)
def _source_names():
    """Never promote arbitrary vacancy text or personal names into public keys."""
    allowed = {"unknown", "other", "email_alert", "authenticated_alert", "gmail_alert", "manual_recheck"}
    for filename in ("employers.json", "recruitment_employers.json"):
        rows = json.loads((Path(__file__).parent / "config" / filename).read_text(encoding="utf-8"))
        for row in rows:
            name = row.get("name", "")
            allowed.update((name, row.get("source_name", ""), f"{row.get('ats', 'html')}:{name}"))
    return allowed - {""}


def revision():
    root = Path(__file__).parent
    return hashlib.sha256((root / "scout_matching.py").read_bytes() +
                          (root / "config/candidate_profile.json").read_bytes()).hexdigest()[:24]


def source_key(post):
    value = str(post.get("source_name") or "unknown")[:100]
    return value if value in _source_names() else "other"


def stage(counters, post, name, amount=1):
    if name not in STAGE_KEYS or type(amount) is not int or amount < 0:
        return
    key = source_key(post)
    row = counters.setdefault(key, {})
    row[name] = row.get(name, 0) + amount


def classify_batch(data, posts, normalize, now=None):
    now = now or datetime.now(timezone.utc)
    seen = data.setdefault("vacancy_seen", {})
    cutoff = (now - timedelta(days=60)).isoformat()
    for key, row in list(seen.items()):
        if not isinstance(row, dict) or str(row.get("last_seen", "")) < cutoff:
            del seen[key]
    counts = {"new_to_scout": 0, "rechecked": 0, "introduction_rechecks": 0, "duplicates_in_batch": 0}
    unique, batch, sources = [], set(), {}
    for post in posts:
        url = normalize(post.get("application_url") or post.get("href") or post.get("url") or "")
        if not url:
            url = "|".join(str(post.get(k, "")) for k in ("company", "job_title", "title", "source_id"))
        key = hashlib.sha256(url.encode()).hexdigest()[:24]
        if key in batch:
            counts["duplicates_in_batch"] += 1
            continue
        batch.add(key)
        intro = post.get("outreach_recheck") is True
        historical = key in seen or any(normalize(r.get("apply_url") or r.get("application_url") or r.get("url") or "") == url
                                       for r in data.get("jobs", {}).values())
        kind = "introduction_rechecks" if intro else "rechecked" if historical else "new_to_scout"
        counts[kind] += 1
        seen[key] = {"first_seen": seen.get(key, {}).get("first_seen", now.isoformat()), "last_seen": now.isoformat()}
        post["audit_id"] = key
        stage(sources, post, "checked")
        stage(sources, post, kind)
        unique.append(post)
    for key in sorted(seen, key=lambda k: seen[k]["last_seen"])[:-2000]:
        del seen[key]
    counts["unique_checked"] = len(unique)
    return unique, counts, sources


def record_review(data, post, checked, fresh, freshness_reason, *, decision_revision=None, now=None):
    now = now or datetime.now(timezone.utc)
    ident = post.get("audit_id")
    if not ident:
        return
    reviews = data.setdefault("vacancy_reviews", {})
    previous = reviews.get(ident, {})
    fields = ("href", "url", "application_url", "company", "job_title", "title", "location", "job_location", "employment_type", "employmenttype", "source_type",
              "source_name", "source_id", "date_posted", "posted_at", "published_at", "first_published",
              "valid_through", "validThrough", "application_deadline", "active_listing", "salary_max_lpa",
              "experience_min", "minYears", "minimum_years_experience", "official_corroborated", "official_url",
              "company_official_url", "date_provenance", "page_fetched", "page_fetch_failed", "access_blocked", "reverification_failed")
    snapshot = {key: post[key] for key in fields if key in post and isinstance(post[key], (str, bool, int, float))}
    truncated = any(isinstance(value, str) and len(value) > 2000 for value in snapshot.values())
    for key, value in list(snapshot.items()):
        if isinstance(value, str):
            snapshot[key] = value[:2000]
    remaining = 12000
    for field in ("description", "source_text", "body", "snippet", "requirements"):
        if post.get(field):
            content = str(post[field])
            snapshot[field] = content[:remaining]
            remaining -= len(snapshot[field])
            truncated = truncated or len(content) > len(snapshot[field])
    if isinstance(post.get("source_emails"), (str, list)):
        emails = post["source_emails"] if isinstance(post["source_emails"], list) else [post["source_emails"]]
        snapshot["source_emails"] = [str(value)[:200] for value in emails[:10]]
        truncated = truncated or len(emails) > 10 or any(len(str(value)) > 200 for value in emails)
    requirements = checked.get("requirements", {})
    bounded = {group: {status: [str(v)[:240] for v in requirements.get(group, {}).get(status, [])[:12]]
                       for status in ("met", "unmet", "unknown")} for group in ("mandatory", "preferred")}
    clauses = requirements.get("clauses", [])
    if isinstance(clauses, list):
        bounded["clauses"] = [{str(k)[:30]: str(v)[:300] for k, v in row.items()}
                              for row in clauses[:16] if isinstance(row, dict)]
    tenure = []
    for row in checked.get("experience_assessment", [])[:16]:
        if isinstance(row, dict):
            tenure.append({"minimum": row.get("minimum"), "evidenced_years": row.get("evidenced_years"),
                           "professional": row.get("professional") is True,
                           "scope": [str(scope)[:60] for scope in row.get("scope", [])[:8]],
                           "clause": str(row.get("clause", ""))[:300]})
    reviews[ident] = {"id": ident, "first_seen": previous.get("first_seen", now.isoformat()), "checked_at": now.isoformat(),
        "revision": decision_revision or revision(), "post": snapshot, "source_truncated": truncated,
        "decision": "qualified" if checked.get("email_safe") and fresh else "held" if checked.get("eligible") and fresh else "rejected",
        "potentially_relevant": bool(checked.get("eligible")), "fully_eligible": bool(checked.get("email_safe") and fresh),
        "reason": str(checked.get("reason") or post.get("rejection_reason") or "Unknown")[:800],
        "fresh": bool(fresh), "freshness_reason": str(freshness_reason)[:200],
        "family": str(checked.get("family", "unknown"))[:40], "requirements": bounded, "experience_assessment": tenure}
    cutoff = (now - timedelta(days=30)).isoformat()
    for key, row in list(reviews.items()):
        if not isinstance(row, dict) or str(row.get("checked_at", "")) < cutoff:
            del reviews[key]
    oldest = sorted(reviews, key=lambda k: reviews[k].get("checked_at", ""))
    while oldest and (len(reviews) > MAX_REVIEWS or len(json.dumps(reviews, ensure_ascii=False).encode()) > MAX_REVIEW_BYTES):
        del reviews[oldest.pop(0)]


def record_daily(data, report, identity, now=None):
    now = now or datetime.now(timezone.utc)
    days = data.setdefault("daily_funnel", {})
    day = now.astimezone(IST).date().isoformat()
    row = days.setdefault(day, {"runs": 0, "identities": [], "counts": {}, "sources": {}})
    identity = hashlib.sha256(str(identity).encode()).hexdigest()[:24] if identity else ""
    if identity and identity in row["identities"]:
        return
    # Do not evict same-day idempotency keys and later count an old run twice.
    if len(row["identities"]) >= MAX_DAILY_IDENTITIES:
        return
    row["runs"] += 1
    if identity:
        row["identities"].append(identity)
    counts = {**report.get("vacancy_counts", {}),
              "fresh_checks": report.get("funnel", {}).get("verified_fresh", 0),
              "suitable_checks": report.get("funnel", {}).get("suitable", 0),
              "reachable_checks": report.get("funnel", {}).get("reachable", 0),
              "application_emails_accepted": report.get("emails_accepted_by_smtp", 0),
              "portal_confirmed": report.get("applications", {}).get("confirmed", 0),
              "introductions_accepted": report.get("introductions_accepted_by_smtp", 0)}
    for key, value in counts.items():
        if key in COUNT_KEYS and type(value) is int and value >= 0:
            row["counts"][key] = row["counts"].get(key, 0) + value
    for source, values in report.get("source_funnel", {}).items():
        target = row["sources"].setdefault(source_key({"source_name": source}), {})
        for key, value in values.items():
            if key in STAGE_KEYS and type(value) is int and value >= 0:
                target[key] = target.get(key, 0) + value
    cutoff = (now.astimezone(IST).date() - timedelta(days=29)).isoformat()
    for old in list(days):
        if old < cutoff:
            del days[old]
    for old in sorted(days)[:-30]:
        del days[old]


def replay_reviews(data, profile=None):
    """Matching-only comparison, not freshness renewal or permission to email."""
    from scout_matching import evaluate_job
    result = {"reviewed": 0, "now_matches": 0, "still_blocked": 0, "truncated_not_replayed": 0,
              "recovered_matches": 0, "newly_blocked": 0, "invalid_not_replayed": 0}
    for row in list(data.get("vacancy_reviews", {}).values())[:MAX_REVIEWS]:
        if not isinstance(row, dict) or not isinstance(row.get("post"), dict):
            result["invalid_not_replayed"] += 1
            continue
        if row.get("source_truncated"):
            result["truncated_not_replayed"] += 1
            continue
        try:
            original_time = datetime.fromisoformat(row["checked_at"].replace("Z", "+00:00"))
            if original_time.tzinfo is None:
                raise ValueError("Unverified decision time")
        except (KeyError, AttributeError, ValueError, TypeError):
            result["invalid_not_replayed"] += 1
            continue
        checked = evaluate_job(dict(row["post"]), profile=profile, now=original_time)
        safe = bool(checked.get("email_safe"))
        before = bool(row.get("fully_eligible"))
        result["reviewed"] += 1
        result["now_matches" if safe else "still_blocked"] += 1
        result["recovered_matches"] += int(safe and not before and row.get("fresh") is True)
        result["newly_blocked"] += int(not safe and before)
    return result


def main(argv=None):
    """Read-only local replay; never instantiate a sender or refresh a vacancy."""
    import argparse
    import sys
    from scout_storage import decode_state
    parser = argparse.ArgumentParser(description="Offline matching replay. Counts are not permission to apply.")
    parser.add_argument("--state", default="state/scout.json", help="Existing encrypted or plaintext scout-state snapshot")
    args = parser.parse_args(argv)
    try:
        path = Path(args.state)
        if path.stat().st_size > 8_000_000:
            raise ValueError("Oversized snapshot")
        data = decode_state(json.loads(path.read_text(encoding="utf-8")), "state/scout.json")
        if not isinstance(data, dict) or not isinstance(data.get("vacancy_reviews", {}), dict):
            raise ValueError("Invalid snapshot")
        print(json.dumps({"mode": "offline_matching_only", "original_decision_time": True,
                          "freshness_renewed": False, "sending_enabled": False, "counts": replay_reviews(data)}, sort_keys=True))
        return 0
    except (ValueError, OSError, TypeError):
        print(json.dumps({"error": "Snapshot unavailable, invalid, or decryption key missing"}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
