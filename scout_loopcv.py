"""Export eligible core-scored jobs to Loopcv free-tier import format.

The file produced here is intended for manual import into Loopcv's free tier.
It contains only jobs the scout already scored with its deterministic,
no-model core matcher -- no inference output, no application side-effects.
"""

import json
import os
from datetime import datetime, timezone


def export_to_loopcv(candidates, path="state/loopcv_export.json"):
    """Write eligible core-scored jobs as JSON for manual Loopcv import.

    candidates: iterable of (analysis, post, verdict) tuples as built by
    scout_runtime.run.  Every row is eligible (passed matching + freshness)
    and core-scored; guessed or unverified contacts are never included.
    """
    rows = []
    for entry in candidates or []:
        if not isinstance(entry, (list, tuple)) or len(entry) < 2:
            continue
        analysis, post = entry[0], entry[1]
        if not isinstance(analysis, dict) or not analysis.get("is_relevant"):
            continue
        application_url = (
            analysis.get("application_url")
            or (post or {}).get("application_url")
            or (post or {}).get("href")
            or ""
        )
        rows.append({
            "job_id": analysis.get("job_id", ""),
            "job_title": analysis.get("job_title") or analysis.get("title", ""),
            "company": analysis.get("company", ""),
            "location": analysis.get("location", ""),
            "application_url": application_url,
            "fit_score": analysis.get("fit_score"),
            "tier": analysis.get("tier", ""),
            "family": analysis.get("family", ""),
            "source_name": analysis.get("source_name", ""),
            "date_posted": (post or {}).get("date_posted", ""),
            "matched_skills": analysis.get("matched_skills", []),
            "exported_at": datetime.now(timezone.utc).isoformat(),
        })
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    payload = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "count": len(rows),
        "note": "Manual import into Loopcv free tier; reviewed before use.",
        "jobs": rows,
    }
    temporary = f"{path}.tmp"
    with open(temporary, "w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2, ensure_ascii=False)
    os.replace(temporary, path)
    print(f"[Loopcv] Exported {len(rows)} eligible jobs to {path}")
    return path
