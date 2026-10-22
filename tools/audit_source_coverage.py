"""Read-only, bounded registry audit. Prints metadata, never candidate contacts."""
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scout_persistence import RuntimeBudget
from scout_sources import _fetch, _source_classification


def audit():
    root = Path(__file__).resolve().parents[1]
    entries = json.loads((root / "config/employers.json").read_text(encoding="utf-8"))
    extras = json.loads((root / "config/recruitment_employers.json").read_text(encoding="utf-8"))
    entries += [dict(e, ats="html", detail_limit=1) for e in extras]
    budget = RuntimeBudget(max_requests=120, deadline_seconds=110)

    def check(entry):
        try:
            sample = dict(entry, detail_limit=1, max_pages=1, search_keywords=["research"])
            jobs, status, _ = _fetch(sample, budget.request, datetime.now(timezone.utc))
            return {"name": entry["name"], "ats": entry.get("ats", "html"),
                    "classification": _source_classification(status), "status": status["status"],
                    "sample_vacancies": len(jobs), "original_dates": sum(bool(j.get("date_posted")) for j in jobs),
                    "bounded_sample": True}
        except Exception as exc:
            response = getattr(exc, "response", None)
            return {"name": entry["name"], "status": "error", "error": type(exc).__name__,
                    "http_status": getattr(response, "status_code", None), "bounded_sample": True}

    with ThreadPoolExecutor(max_workers=6) as pool:
        rows = list(pool.map(check, entries))
    print(json.dumps({"checked_at": datetime.now(timezone.utc).isoformat(),
                      "http_requests": budget.attempts, "sources": rows}, indent=2))


if __name__ == "__main__":
    audit()
