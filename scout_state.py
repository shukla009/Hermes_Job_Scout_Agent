"""Versioned durable scout state. Alerts are never treated as applications."""
import hashlib
import hmac
import time
import json
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))


def callback_data(ident, action, token, now=None):
    if not re.fullmatch(r"[0-9a-f]{24}", ident) or action not in ("applied", "notfit", "snooze", "reply", "interview", "rejected"):
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
                    if not isinstance(json.loads(Path(legacy_path).read_text(encoding="utf-8")), (dict, list)):
                        raise ValueError("Invalid legacy state")
                except (ValueError, OSError) as exc:
                    raise StateCorruptError("Ancillary history cannot be safely loaded") from exc

    def load(self):
        if self.path.exists():
            try:
                value = json.loads(self.path.read_text(encoding="utf-8"))
                if value.get("version") != 1 or not all(isinstance(value.get(k), dict) for k in ("jobs", "sends", "alerts")):
                    raise ValueError("unsupported state shape")
                if not isinstance(value.get("processed_feedback"), list):
                    raise ValueError("invalid feedback ledger")
                self.data = value
                if not value.get("legacy_dates_repaired") and self.history_path.exists():
                    legacy = json.loads(self.history_path.read_text(encoding="utf-8"))
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
                history = json.loads(self.history_path.read_text(encoding="utf-8"))
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
        now = datetime.now(timezone.utc).isoformat()
        record = self.data["jobs"].setdefault(ident, {"id": ident, "first_seen": now, "submitted": False, "emailed": False})
        if record.get("archived"):
            record["last_seen"] = now
            return record
        for key in ("job_title", "title", "company", "location", "application_url", "apply_url", "url", "score", "fit_score", "fit_reason", "stretch", "email", "warnings", "family", "tier", "reason", "evidence", "source_id"):
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

    def pending_jobs(self):
        return [record for record in self.data["jobs"].values() if not record.get("archived") and not record.get("submitted") and not record.get("outcome")]

    def weekly_metrics(self):
        jobs = list(self.data["jobs"].values())
        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        return {"discovered": sum(j.get("first_seen", "") >= cutoff for j in jobs),
                "alerted": sum(j.get("alerted_at", "") >= cutoff for j in jobs),
                "submitted": sum(j.get("applied_at", "") >= cutoff for j in jobs),
                "emailed": sum(j.get("emailed_at", "") >= cutoff for j in jobs),
                "replies": sum(j.get("outcome") == "reply" and j.get("outcome_at", "") >= cutoff for j in jobs),
                "interviews": sum(j.get("outcome") == "interview" and j.get("outcome_at", "") >= cutoff for j in jobs)}

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
                event = json.loads(path.read_text(encoding="utf-8"))
                ident, action = event["job_id"], event["action"]
                record = self.data["jobs"].get(ident)
                if not record or action not in ("applied", "notfit", "snooze", "reply", "interview", "rejected"):
                    continue
                previous = int(record.get("feedback_update_id", -1))
                if int(path.stem) > previous:
                    if action == "applied":
                        record["submitted"] = True
                        record["submission_unconfirmed"] = False
                        record["applied_at"] = event.get("created_at") or datetime.now(timezone.utc).isoformat()
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

    def reserve_send(self, ident, recipient, step="initial", message_id=None):
        recipient = recipient.strip().lower()
        intent_id = hashlib.sha256(f"{ident}|{recipient}|{step}".encode()).hexdigest()[:32]
        record = self.data["jobs"].get(ident, {})
        if not record or record.get("outcome") in ("notfit", "rejected", "reply", "interview") or record.get("submitted"):
            return None
        if intent_id in self.data["sends"] or (step == "initial" and self.known_emailed(ident)):
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
                  "message_id": message_id or f"<hermes-{intent_id}@job-scout.local>", "status": "pending", "created_at": now.isoformat()}
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
                record["emailed"] = True
                if intent.get("step") == "initial":
                    record["email"] = intent["recipient"]
                record.setdefault("emailed_at", datetime.now(timezone.utc).isoformat())
        return self.save()
