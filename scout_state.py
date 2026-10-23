"""Versioned durable scout state. Alerts are never treated as applications."""
import hashlib
import hmac
import time
import json
import os
import re
import tempfile
from statistics import median
from datetime import datetime, timedelta, timezone
from pathlib import Path
from scout_storage import load_json

IST = timezone(timedelta(hours=5, minutes=30))


def callback_data(ident, action, token, now=None):
    if not re.fullmatch(r"[0-9a-f]{24}", ident) or action not in ("applied", "notfit", "snooze", "reply", "interview", "rejected", "skipped", "referral"):
        raise ValueError("Invalid callback target")
    expiry = int(time.time() if now is None else now) + 7 * 86400
    payload = f"{action}:{ident}:{expiry}"
    signature = hmac.new(token.encode(), payload.encode(), hashlib.sha256).hexdigest()[:12]
    return f"{payload}:{signature}"


class StateCorruptError(RuntimeError):
    pass


def job_id(analysis, post=None):
    post = post or {}
    existing = analysis.get("job_id") or analysis.get("id")
    if isinstance(existing, str) and re.fullmatch(r"[0-9a-f]{24}", existing):
        return existing
    key = analysis.get("application_url") or analysis.get("apply_url") or analysis.get("url") or post.get("url") or post.get("link") or post.get("href")
    if not key:
        key = "|".join(str(analysis.get(k, "")) for k in ("company", "job_title", "location"))
    return hashlib.sha256(str(key).strip().encode()).hexdigest()[:24]


class StateStore:
    def __init__(self, path="state/scout.json", history_path="applied_history.json", commit_callback=None,
                 followups_path=None, suppressions_path=None, **kwargs):
        self.path = Path(path)
        self.history_path = Path(history_path)
        self.commit_callback = commit_callback
        self.data = None
        self.load()
        for legacy_path in (followups_path, suppressions_path):
            if legacy_path and Path(legacy_path).exists():
                try:
                    if not isinstance(load_json(legacy_path), (dict, list)):
                        raise ValueError("Invalid legacy state")
                except (ValueError, OSError) as exc:
                    raise StateCorruptError("Ancillary history cannot be safely loaded") from exc

    def load(self):
        if self.path.exists():
            try:
                value = load_json(self.path)
                if value.get("version") != 1 or not all(isinstance(value.get(k), dict) for k in ("jobs", "sends", "alerts")):
                    raise ValueError("unsupported state shape")
                if not isinstance(value.get("processed_feedback"), list):
                    raise ValueError("invalid feedback ledger")
                for key in ("employer_contacts", "contacts_rollout", "manual_alert_days", "named_contacts", "named_contacts_rollout", "contact_search_days", "named_backend_verification", "contact_search_health", "vacancy_reviews", "vacancy_seen", "daily_funnel"):
                    if key in value and not isinstance(value[key], dict):
                        raise ValueError("invalid recruiter-reach state")
                self.data = value
                if not value.get("legacy_dates_repaired") and self.history_path.exists():
                    legacy = load_json(self.history_path)
                    if not isinstance(legacy, list):
                        raise ValueError("Invalid legacy history")
                    for entry in legacy:
                        if not isinstance(entry, dict):
                            raise ValueError("Invalid legacy history entry")
                        record = value["jobs"].get(job_id(entry, entry))
                        if record and not record.get("tier"):
                            record["first_seen"] = entry.get("timestamp") or "1970-01-01T00:00:00+00:00"
                            record["legacy_unverified"] = True
                    value["legacy_dates_repaired"] = True
            except (ValueError, OSError, TypeError, AttributeError) as exc:
                raise StateCorruptError("Scout state cannot be safely loaded") from exc
            return self
        self.data = {"version": 1, "jobs": {}, "sends": {}, "alerts": {}, "processed_feedback": [], "budget": {}}
        if self.history_path.exists():
            try:
                history = load_json(self.history_path)
                if not isinstance(history, list):
                    raise ValueError("history must be a list")
                for entry in history:
                    if not isinstance(entry, dict):
                        raise ValueError("invalid history entry")
                    record = self.upsert_job(entry, entry)
                    record["first_seen"] = entry.get("timestamp") or "1970-01-01T00:00:00+00:00"
                    record["legacy_unverified"] = True
                    if entry.get("route") in ("apply", "alert"):
                        record["alerted"] = True
                        record["submission_unconfirmed"] = True
                    elif entry.get("route") == "recruiter_intro":
                        record.update(intro_emailed=True, intro_emailed_at=entry.get("timestamp"), submitted=True,
                                      submission_unconfirmed=True, intro_recipient=entry.get("email"))
                    elif entry.get("email"):
                        record["emailed"] = True
                        record["email"] = entry["email"]
                        record["emailed_at"] = entry.get("timestamp")
            except (ValueError, OSError, TypeError) as exc:
                raise StateCorruptError("Legacy history cannot be safely migrated") from exc
        return self

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".scout-", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self.data, handle, indent=2, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, self.path)
            return self.commit_callback(self) is not False if self.commit_callback else True
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def upsert_job(self, analysis, post=None):
        ident = job_id(analysis, post)
        # Preserve historical identity if a prior release included title/company
        # in its hash. Tracking aliases must not recreate an old application.
        from scout_agent import _normalize_url
        target = _normalize_url(analysis.get("application_url") or (post or {}).get("href") or "")
        if target:
            existing = next((row for row in self.data["jobs"].values()
                             if _normalize_url(row.get("apply_url") or row.get("application_url") or row.get("url") or "") == target), None)
            if existing:
                ident = existing["id"]
        now = datetime.now(timezone.utc).isoformat()
        record = self.data["jobs"].setdefault(ident, {"id": ident, "first_seen": now, "submitted": False, "emailed": False})
        if record.get("archived"):
            record["last_seen"] = now
            return record
        for key in ("job_title", "title", "company", "location", "application_url", "apply_url", "url", "score", "fit_score", "fit_reason", "stretch", "email", "warnings", "family", "tier", "reason", "evidence", "source_id", "source_name", "requirements", "review_required", "review_reasons", "risk_assessment", "risk_flags", "score_breakdown", "score_kind", "date_posted", "vacancy_reference", "contact_route", "email_safe", "junior_experience_exception"):
            if key in analysis:
                record[key] = analysis[key]
        if post:
            record.setdefault("url", post.get("url") or post.get("link") or post.get("href"))
        if analysis.get("application_url"):
            record["apply_url"] = analysis["application_url"]
        record["last_seen"] = now
        if sum(not r.get("archived") for r in self.data["jobs"].values()) > 500:
            candidates = sorted((r for r in self.data["jobs"].values() if r["id"] != ident and not r.get("archived")), key=lambda r: r.get("last_seen", ""))
            old = candidates[0]
            # Retain dedup/outcome evidence; archive only bulky descriptive metadata.
            for key in ("warnings", "fit_reason", "location", "title", "job_title", "company"):
                old.pop(key, None)
            old["archived"] = True
        return record

    def known_submitted(self, ident):
        return bool(self.data["jobs"].get(ident, {}).get("submitted"))

    def known_emailed(self, ident):
        return bool(self.data["jobs"].get(ident, {}).get("emailed"))

    def introduction_eligible(self, ident):
        """Only a confirmed form receipt or explicit Applied feedback is evidence.

        A pending/ambiguous request dominates any later user flag. An email (even
        ambiguous) precludes changing recipients and calling it an introduction.
        """
        row = self.data["jobs"].get(ident, {})
        if (not row.get("submitted") or row.get("submission_unconfirmed") or row.get("emailed")
                or row.get("intro_emailed") or row.get("outcome") or row.get("archived")
                or row.get("delivery_status") == "bounced"
                or row.get("manual_action_status") in {"skipped", "referral_requested"}):
            return False
        forms = [v for v in self.data.get("applications", {}).values() if v.get("job_id") == ident]
        if any(v.get("status") in {"pending", "ambiguous"} for v in forms):
            return False
        if any(v.get("job_id") == ident and v.get("step", "initial") == "initial"
               and v.get("status") in {"pending", "uncertain", "accepted"} for v in self.data["sends"].values()):
            return False
        return bool((row.get("application_route") == "public_form" and any(v.get("status") == "confirmed" for v in forms))
                    or (row.get("application_route") == "user_reported" and row.get("manual_action_status") == "applied"
                        and row.get("feedback_update_id") is not None))

    def introduction_followup_allowed(self, ident, recipient):
        row = self.data["jobs"].get(ident, {})
        return bool(row.get("intro_emailed") and not row.get("outcome") and
                    any(v.get("job_id") == ident and v.get("recipient") == recipient
                        and v.get("outreach_purpose") == "recruiter_intro" and v.get("step") == "initial"
                        and v.get("status") == "accepted" for v in self.data["sends"].values()))

    def pending_jobs(self):
        return [record for record in self.data["jobs"].values() if not record.get("archived") and not record.get("submitted") and not record.get("outcome")]

    def weekly_metrics(self):
        jobs = list(self.data["jobs"].values())
        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        delays = [j["minutes_to_application"] for j in jobs if j.get("first_seen", "") >= cutoff and isinstance(j.get("minutes_to_application"), (int, float))]
        return {"discovered": sum(j.get("first_seen", "") >= cutoff for j in jobs),
                "alerted": sum(j.get("alerted_at", "") >= cutoff for j in jobs),
                "submitted": sum(j.get("applied_at", "") >= cutoff for j in jobs),
                "emailed": sum(j.get("emailed_at", "") >= cutoff for j in jobs),
                "recruiter_introductions": sum(j.get("intro_emailed_at", "") >= cutoff for j in jobs),
                "replies": sum(j.get("outcome") == "reply" and j.get("outcome_at", "") >= cutoff for j in jobs),
                "interviews": sum(j.get("outcome") == "interview" and j.get("outcome_at", "") >= cutoff for j in jobs),
                "median_minutes_to_application": round(median(delays), 1) if delays else "unavailable"}

    def conversion_cohorts(self, days=30):
        """Return explainable source/family funnel counts for recently found jobs.

        This intentionally reports counts, not misleading conversion probabilities.
        A job belongs to the cohort selected by its first-seen timestamp; later
        outcomes remain attached to that job so the funnel can mature over time.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        stages = ("discovered", "alerted", "emailed", "submitted", "replies", "interviews", "rejected")

        def empty():
            return {stage: 0 for stage in stages}

        cohorts = {"sources": {}, "families": {}}
        for job in self.data["jobs"].values():
            if job.get("first_seen", "") < cutoff:
                continue
            source = str(job.get("source_name") or "unknown")[:80]
            family = str(job.get("family") or "unknown")[:80]
            for bucket, key in (("sources", source), ("families", family)):
                counts = cohorts[bucket].setdefault(key, empty())
                counts["discovered"] += 1
                counts["alerted"] += int(bool(job.get("alerted")))
                counts["emailed"] += int(bool(job.get("emailed")))
                counts["submitted"] += int(bool(job.get("submitted")))
                counts["replies"] += int(job.get("outcome") == "reply")
                counts["interviews"] += int(job.get("outcome") == "interview")
                counts["rejected"] += int(job.get("outcome") in ("rejected", "notfit"))
        return cohorts

    def can_alert(self, ident, stretch=False):
        record = self.data["jobs"].get(ident, {})
        if record.get("alerted") or record.get("submitted") or record.get("outcome") in ("notfit", "rejected", "interview", "reply"):
            return False
        until = record.get("snoozed_until")
        if until and until > datetime.now(timezone.utc).isoformat():
            return False
        today = self.data["alerts"].get(datetime.now(IST).date().isoformat(), {})
        return today.get("count", 0) < 5 and (not stretch or today.get("stretch", 0) < 1)

    def record_alert(self, ident, stretch=False):
        if not self.can_alert(ident, stretch):
            return False
        today = self.data["alerts"].setdefault(datetime.now(IST).date().isoformat(), {"count": 0, "stretch": 0})
        today["count"] += 1
        today["stretch"] += int(stretch)
        self.data["jobs"][ident]["alerted"] = True
        self.data["jobs"][ident]["alerted_at"] = datetime.now(timezone.utc).isoformat()
        return self.save()

    def ingest_feedback(self, directory="state/feedback"):
        count = 0
        files = sorted(Path(directory).glob("*.json"), key=lambda p: int(p.stem) if p.stem.isdigit() else -1)
        for path in files:
            if not path.stem.isdigit() or path.stem in self.data["processed_feedback"]:
                continue
            try:
                event = load_json(path)
                ident, action = event["job_id"], event["action"]
                record = self.data["jobs"].get(ident)
                if not record or action not in ("applied", "notfit", "snooze", "reply", "interview", "rejected", "skipped", "referral"):
                    continue
                previous = int(record.get("feedback_update_id", -1))
                if int(path.stem) > previous:
                    if action == "applied":
                        record["submitted"] = True
                        record["submission_unconfirmed"] = False
                        record["applied_at"] = event.get("created_at") or datetime.now(timezone.utc).isoformat()
                        record["application_route"] = "user_reported"
                        record["manual_action_status"] = "applied"
                    elif action in ("skipped", "referral"):
                        record["manual_action_status"] = "skipped" if action == "skipped" else "referral_requested"
                        record["manual_action_at"] = event.get("created_at") or datetime.now(timezone.utc).isoformat()
                    elif action == "snooze" and not record.get("outcome"):
                        record["snoozed_until"] = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
                    if action in ("reply", "interview", "rejected", "notfit") and not (record.get("outcome") in ("interview", "rejected") and action in ("reply", "notfit")):
                        record["outcome"] = action
                        record["outcome_at"] = event.get("created_at") or datetime.now(timezone.utc).isoformat()
                    record["feedback_update_id"] = int(path.stem)
                self.data["processed_feedback"].append(path.stem)
                count += 1
            except (ValueError, KeyError, TypeError, OSError):
                raise StateCorruptError("Invalid feedback event; refusing unsafe state update")
        if count:
            self.save()
        return count

    def reserve_send(self, ident, recipient, step="initial", message_id=None, outreach_purpose="application"):
        if outreach_purpose not in {"application", "recruiter_intro"}:
            return None
        recipient = recipient.strip().lower()
        intent_id = hashlib.sha256(f"{ident}|{recipient}|{step}".encode()).hexdigest()[:32]
        record = self.data["jobs"].get(ident, {})
        if recipient in self.data.get("suppressed_recipients", {}):
            return None
        intro = outreach_purpose == "recruiter_intro"
        if intro and not (self.introduction_eligible(ident) if step == "initial" else self.introduction_followup_allowed(ident, recipient)):
            return None
        if (not record or record.get("outcome") in ("notfit", "rejected", "reply", "interview")
                or record.get("manual_action_status") in {"skipped", "referral_requested"}
                or (not intro and (record.get("submitted") or record.get("manual_action_status") == "applied" or self.application_reserved(ident)))):
            return None
        if step == "initial" and self.company_in_cooldown(ident):
            return None
        if intent_id in self.data["sends"] or (step == "initial" and self.known_emailed(ident)):
            return None
        if step == "initial" and any(send.get("job_id") == ident and send.get("step") == "initial"
                and send.get("status") in {"pending", "uncertain", "accepted"} for send in self.data["sends"].values()):
            return None
        now = datetime.now(timezone.utc)
        for send in self.data["sends"].values():
            if send.get("recipient") == recipient and send.get("job_id") != ident:
                try:
                    if now - datetime.fromisoformat(send["created_at"]) < timedelta(days=30):
                        return None
                except (ValueError, KeyError):
                    return None
        for record in self.data["jobs"].values():
            if record.get("email", "").lower() == recipient and record.get("emailed") and record.get("id") != ident:
                try:
                    stamp = datetime.fromisoformat(record["emailed_at"])
                    if stamp.tzinfo is None:
                        stamp = stamp.replace(tzinfo=IST)
                    if now - stamp < timedelta(days=30):
                        return None
                except (ValueError, KeyError, TypeError):
                    return None
        intent = {"id": intent_id, "job_id": ident, "recipient": recipient, "step": step,
                  "outreach_purpose": outreach_purpose,
                  "message_id": message_id or f"<hermes-{intent_id}@job-scout.local>", "status": "pending", "created_at": now.isoformat()}
        if step == "initial":
            intent["contact_route"] = self.data["jobs"][ident].get("contact_route", "vacancy_email")
            if intro:
                applied = self.data["jobs"][ident]
                intent["application_evidence_kind"] = "portal_confirmed" if applied.get("application_route") == "public_form" else "user_reported"
                intent["applied_at"] = applied.get("applied_at")
        self.data["sends"][intent_id] = intent
        if not self.save():
            return None
        return intent

    def finish_send(self, intent_id, status="accepted"):
        if status not in ("accepted", "uncertain", "not_sent"):
            raise ValueError("Invalid send status")
        intent = self.data["sends"][intent_id]
        intent["status"] = status
        if status == "accepted":
            record = self.data["jobs"].get(intent["job_id"])
            if record:
                if intent.get("outreach_purpose") == "recruiter_intro":
                    record["intro_emailed"] = True
                    record.setdefault("intro_emailed_at", datetime.now(timezone.utc).isoformat())
                    record["intro_recipient"] = intent["recipient"]
                    return self.save()
                record["emailed"] = True
                if intent.get("step") == "initial":
                    record["email"] = intent["recipient"]
                record.setdefault("emailed_at", datetime.now(timezone.utc).isoformat())
                if intent.get("step") == "initial":
                    self._record_application_delay(record)
        return self.save()

    @staticmethod
    def _record_application_delay(record):
        try:
            published = datetime.fromisoformat(str(record.get("date_posted", "")).replace("Z", "+00:00"))
            if published.tzinfo is None:
                published = published.replace(tzinfo=timezone.utc)
            record.setdefault("minutes_to_application", round(max(0, (datetime.now(timezone.utc) - published).total_seconds() / 60), 1))
        except (ValueError, TypeError):
            pass

    def company_in_cooldown(self, ident):
        record = self.data["jobs"].get(ident, {})
        company = re.sub(r"\W+", "", str(record.get("company", "")).lower())
        if not company:
            return False
        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        for row in self.data["jobs"].values():
            if row.get("id") == ident or re.sub(r"\W+", "", str(row.get("company", "")).lower()) != company:
                continue
            if row.get("applied_at", "") >= cutoff or row.get("emailed_at", "") >= cutoff:
                return True
            for ledger in (self.data.get("applications", {}), self.data["sends"]):
                if any(item.get("job_id") == row.get("id") and item.get("step", "initial") == "initial"
                       and item.get("status") in {"pending", "uncertain", "accepted", "confirmed", "ambiguous"}
                       and item.get("created_at", "") >= cutoff for item in ledger.values()):
                    return True
        return False

    def application_reserved(self, ident):
        return any(row.get("job_id") == ident and row.get("status") in {"pending", "ambiguous", "confirmed"}
                   for row in self.data.get("applications", {}).values())

    def reserve_application(self, ident, adapter_id):
        record = self.data["jobs"].get(ident, {})
        if not record or record.get("submitted") or record.get("emailed") or record.get("outcome") or record.get("manual_action_status") in {"skipped", "referral_requested", "applied"}:
            return None
        if self.application_reserved(ident) or self.company_in_cooldown(ident):
            return None
        if any(send.get("job_id") == ident and send.get("status") in {"pending", "uncertain", "accepted"}
               for send in self.data["sends"].values()):
            return None
        identity = hashlib.sha256((ident + "|public_form").encode()).hexdigest()[:32]
        if identity in self.data.setdefault("applications", {}):
            return None
        intent = {"id": identity, "job_id": ident, "adapter_id": adapter_id, "status": "pending",
                  "created_at": datetime.now(timezone.utc).isoformat()}
        self.data["applications"][identity] = intent
        return intent if self.save() else None

    def finish_application(self, identity, outcome):
        status = outcome.get("status")
        if status not in {"confirmed", "rejected", "ambiguous", "blocked"}:
            raise ValueError("Invalid application result")
        intent = self.data["applications"][identity]
        intent["status"] = status
        for key in ("application_id", "evidence_kind", "reason"):
            if outcome.get(key):
                intent[key] = str(outcome[key])[:200]
        if status == "confirmed":
            record = self.data["jobs"][intent["job_id"]]
            record.update(submitted=True, submission_unconfirmed=False, application_route="public_form",
                          applied_at=datetime.now(timezone.utc).isoformat())
            self._record_application_delay(record)
        return self.save()
