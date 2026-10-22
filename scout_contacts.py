"""Evidence-bound employer careers inboxes; never infer or probe an address."""
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from scout_mail import recruitment_contacts, email_applications_forbidden
from scout_sources import Page, safe_url, text

ROOT = Path(__file__).parent
CONTACT_POLICY = "official-careers-v1"


def company_key(value):
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def contact_registry():
    employers = json.loads((ROOT / "config/employers.json").read_text(encoding="utf-8"))
    extras = json.loads((ROOT / "config/recruitment_employers.json").read_text(encoding="utf-8"))
    return employers + extras


def _owned_host(host, root):
    return bool(host and root and (host == root or host.endswith("." + root)))


def vacancy_belongs_to(post, employer):
    """An employer-name assertion alone cannot authorise a cross-page contact join."""
    if company_key(post.get("company")) != company_key(employer.get("name")):
        return False
    url = str(post.get("application_url") or post.get("href") or "")
    if not safe_url(url):
        return False
    parsed = urlsplit(url)
    ats, slug = employer.get("ats"), employer.get("slug", "").lower()
    parts = [value.lower() for value in parsed.path.split("/") if value]
    if slug and ats in {"greenhouse", "lever", "smartrecruiters", "ashby", "workable"}:
        hosts = {"greenhouse": {"boards.greenhouse.io", "job-boards.greenhouse.io"},
                 "lever": {"jobs.lever.co", "jobs.eu.lever.co"},
                 "smartrecruiters": {"jobs.smartrecruiters.com"},
                 "ashby": {"jobs.ashbyhq.com"}, "workable": {"apply.workable.com"}}
        if parsed.hostname in hosts[ats] and parts and parts[0] == slug:
            return True
    careers = urlsplit(str(employer.get("careers_url") or ""))
    if ats in {"workday", "oracle"} and parsed.hostname == careers.hostname:
        return parsed.path.startswith(careers.path.rstrip("/"))
    if parsed.hostname in employer.get("detail_hosts", []):
        return True  # Explicitly audited employer-specific hosted application tenant.
    roots = employer.get("official_domains", [])
    for key in ("official_url", "careers_url"):
        root = urlsplit(str(employer.get(key) or "")).hostname
        # Hosted ATS tenant roots do not establish employer ownership of siblings.
        if root and not re.search(r"greenhouse|lever\.co|smartrecruiters|ashbyhq|workable|oraclecloud|myworkdayjobs", root):
            roots = [*roots, root.removeprefix("www.")]
    return any(_owned_host(parsed.hostname, root) for root in roots)


def _recent(value, now):
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return stamp.tzinfo is not None and timedelta(0) <= now - stamp <= timedelta(hours=24)
    except (TypeError, ValueError):
        return False


class ContactDiscovery:
    """At most six fetches per run, two per employer, one link level deep."""
    def __init__(self, request, cache, *, registry=None, now=None):
        self.request, self.cache = request, cache
        self.registry = contact_registry() if registry is None else registry
        self.now = now or datetime.now(timezone.utc)
        self.fetches = 0
        self.stats = {"employers_checked": 0, "verified_inboxes": 0, "errors": 0}

    @property
    def revision(self):
        return hashlib.sha256((CONTACT_POLICY + json.dumps(self.registry, sort_keys=True)).encode()).hexdigest()[:24]

    def find(self, post, candidate_email=""):
        if (post.get("page_fetched") is not True or any(post.get(k) for k in
                ("page_fetch_failed", "access_blocked", "reverification_failed")) or email_applications_forbidden(post)):
            return []
        employer = next((e for e in self.registry if vacancy_belongs_to(post, e)), None)
        if not employer:
            return []
        seeds = employer.get("recruitment_pages") or [employer.get("official_url") or employer.get("careers_url")]
        seeds = [u for u in seeds if safe_url(u) and re.search(r"career|recruit|join|jobs|opportunit", (urlsplit(u).hostname or "") + urlsplit(u).path, re.I)]
        if not seeds:
            return []
        key = company_key(employer["name"])
        policy = hashlib.sha256(json.dumps(employer, sort_keys=True).encode()).hexdigest()[:16]
        cached = self.cache.get(key, {})
        self.stats["employers_checked"] += 1
        if cached.get("policy") == policy and _recent(cached.get("checked_at"), self.now):
            evidence = cached.get("evidence", [])
        else:
            evidence, pending, visited = [], list(seeds[:2]), set()
            roots = {urlsplit(u).hostname for u in seeds}
            while pending and len(visited) < 2 and self.fetches < 6:
                url = pending.pop(0)
                if url in visited or not safe_url(url) or urlsplit(url).hostname not in roots:
                    continue
                visited.add(url)
                self.fetches += 1
                try:
                    response = self.request(url, max_retries=0, timeout=12)
                    final = getattr(response, "url", url) or url
                    if response.status_code != 200 or urlsplit(final).hostname not in roots or not safe_url(final):
                        self.stats["errors"] += 1
                        continue
                    content = response.text[:500000]
                    visible = text(re.sub(r"<(?:script|style|footer|nav)\b[^>]*>.*?</(?:script|style|footer|nav)>", " ", content, flags=re.S | re.I))
                    contact_post = {"page_fetched": True, "source_text": visible, "href": final}
                    addresses = recruitment_contacts(contact_post, candidate_email)
                    # Cross-page joins are only for employer-domain recruitment mailboxes.
                    domains = employer.get("official_domains") or [h.removeprefix("www.") for h in roots]
                    for proof in contact_post.get("recruitment_contact_evidence", []):
                        domain = proof["email"].rsplit("@", 1)[-1]
                        local = proof["email"].split("@", 1)[0]
                        if (proof["email"] in addresses and not re.search(r"sales|support|privacy|info|enquir|help|billing", local)
                                and any(_owned_host(domain, root) for root in domains)):
                            proof.update(employer=employer["name"], scope="employer_recruitment", route="official_careers_inbox",
                                         verified_at=self.now.isoformat())
                            evidence.append(proof)
                    if evidence:
                        break
                    parser = Page()
                    parser.feed(content)
                    if len(visited) == 1:
                        pending.extend(urljoin(final, link) for link in parser.links
                                       if re.search(r"career|recruit|join|opportunit", urlsplit(link).path, re.I))
                except Exception:
                    self.stats["errors"] += 1
            # Exhausting the shared fetch budget does not negatively cache an unvisited employer.
            if visited:
                self.cache[key] = {"policy": policy, "checked_at": self.now.isoformat(), "evidence": evidence[:3]}
        valid = [dict(row) for row in evidence if isinstance(row, dict) and _recent(row.get("verified_at"), self.now)]
        if valid:
            post["recruitment_contact_evidence"] = valid[:3]
            self.stats["verified_inboxes"] += 1
        return [row["email"] for row in valid[:3]]


def contact_is_current(post, recipient, now=None):
    return any(row.get("email") == recipient and _recent(row.get("verified_at"), now or datetime.now(timezone.utc))
               for row in post.get("recruitment_contact_evidence", [])) and not email_applications_forbidden(post)


def rollout_state(data, revision):
    rollout = data.setdefault("contacts_rollout", {})
    if rollout.get("revision") != revision:
        rollout.clear()
        rollout.update(revision=revision, shadow_runs=0, scheduled_ids=[])
    if (type(rollout.get("shadow_runs")) is not int or not 0 <= rollout["shadow_runs"] <= 3 or
            not isinstance(rollout.get("scheduled_ids"), list)):
        rollout.update(invalid=True, shadow_runs=0, scheduled_ids=[])
    return rollout


def can_send_new_contact(data, rollout, now=None):
    now = now or datetime.now(timezone.utc)
    try:
        if rollout.get("invalid"):
            return False, "careers_inbox_invalid_rollout"
        if type(rollout.get("shadow_runs")) is not int or rollout["shadow_runs"] < 3:
            return False, "careers_inbox_shadow"
        since = rollout.get("live_since")
        if since and not _recent_rollout(since, now):
            return True, "live"
        from scout_state import IST
        attempts = sum(row.get("contact_route", data.get("jobs", {}).get(row.get("job_id"), {}).get("contact_route")) == "official_careers_inbox" and
                       row.get("step", "initial") == "initial" and
                       datetime.fromisoformat(row["created_at"]).astimezone(IST).date() == now.astimezone(IST).date()
                       for row in data.get("sends", {}).values())
        return attempts < 2, "careers_inbox_canary_limit" if attempts >= 2 else "canary"
    except (TypeError, ValueError, KeyError):
        return False, "careers_inbox_invalid_rollout"


def _recent_rollout(value, now):
    since = datetime.fromisoformat(value)
    if since.tzinfo is None or since > now:
        raise ValueError("Invalid rollout timestamp")
    return now - since < timedelta(days=7)


def complete_contact_shadow(rollout, identity):
    if not rollout.get("invalid") and identity and identity not in rollout["scheduled_ids"]:
        rollout["shadow_runs"] = min(3, rollout["shadow_runs"] + 1)
        rollout["scheduled_ids"] = (rollout["scheduled_ids"] + [identity])[-3:]
