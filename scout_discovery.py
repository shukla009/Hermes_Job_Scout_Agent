"""Turn authenticated alert links into public vacancy evidence, never freshen them."""
import re
from urllib.parse import urlsplit, parse_qs, unquote


def _unwrap(url):
    parsed = urlsplit(url)
    for key, values in parse_qs(parsed.query).items():
        if key.lower() in {"url", "redirect", "redirecturl", "redirect_url", "destination", "target", "dest"}:
            candidate = unquote(values[0])
            if candidate.startswith("https://"):
                return candidate
    return url


def resolve_alert_hints(hints, request_fn, max_links=10):
    from scout_agent import _extract_job_json_ld, _html_to_text, _is_job_board_url, _normalize_url
    jobs, seen = [], set()
    stats = {"status": "ok", "hints": len(hints), "resolved": 0, "leads": 0,
             "blocked": 0, "errors": 0, "unverified_dates": 0,
             "pending_hints": list(hints[max_links:100])}
    for hint in hints[:max_links]:
        try:
            url = _unwrap(str(hint.get("url", "")))
            parsed = urlsplit(url)
            host = (parsed.hostname or "").lower()
            if (parsed.scheme != "https" or not host or parsed.username or parsed.password
                    or any(term in host for term in ("naukri", "linkedin.com"))):
                stats["blocked"] += 1
                continue
            canonical = _normalize_url(url)
            if canonical in seen:
                continue
            seen.add(canonical)
            # The request function enforces public DNS, HTTPS, response limits and
            # validates every redirect; platform pages are discovery only.
            response = request_fn(url, timeout=15)
            final_url = getattr(response, "url", None) or url
            structured = _extract_job_json_ld(response.text[:1_000_000])
            stats["resolved"] += 1
            if not structured:
                stats["unverified_dates"] += 1
                continue
            if not structured.get("date_posted"):
                stats["unverified_dates"] += 1
                continue
            target = structured.get("application_url") or final_url
            if _is_job_board_url(target) or "linkedin.com" in (urlsplit(target).hostname or ""):
                stats["blocked"] += 1
                continue
            jobs.append({**structured, "href": final_url, "url": final_url,
                         "application_url": target, "source_type": "direct_apply",
                         "source_name": "gmail_alert:" + str(hint.get("alert_sender_domain", "unknown")),
                         "source_id": canonical, "page_fetched": True, "active_listing": True,
                         "source_text": (structured.get("description", "") + "\n" + _html_to_text(response.text))[:12000],
                         "alert_message_id": hint.get("alert_message_id", ""),
                         "alert_received_at": hint.get("alert_received_at", "")})
        except Exception:
            stats["errors"] += 1
            retries = int(hint.get("resolve_attempts", 0)) + 1
            if retries <= 2:
                stats["pending_hints"].append({**hint, "resolve_attempts": retries})
    stats["leads"] = len(jobs)
    if stats["errors"] and not stats["resolved"]:
        stats["status"] = "error"
    return jobs, stats
