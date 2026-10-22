"""Turn authenticated alert links into public vacancy evidence, never freshen them."""
import ipaddress
from collections import Counter
from urllib.parse import urlsplit, parse_qs, unquote, urljoin

import requests


def _unwrap(url):
    # Only explicit URL-valued destinations, not encoded platform identifiers or
    # guessed employer paths. Nested mail trackers are common; keep this bounded.
    visited = set()
    for _ in range(5):
        if url in visited:
            raise ValueError("redirect_limit")
        visited.add(url)
        destination = None
        for key, values in parse_qs(urlsplit(url).query).items():
            if key.lower() in {"url", "redirect", "redirecturl", "redirect_url", "destination", "target", "dest"}:
                candidate = unquote(values[0])
                if candidate.startswith("https://"):
                    destination = candidate
                    break
        if not destination:
            return url
        url = destination
    return url


def _url_block_reason(url):
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        if "naukri" in host or host == "linkedin.com" or host.endswith(".linkedin.com"):
            return "excluded_platform"
        if (parsed.scheme != "https" or not host or parsed.username or parsed.password
                or parsed.port not in (None, 443) or "." not in host
                or host.endswith((".local", ".internal", ".localhost"))):
            return "unsafe_url"
        try:
            if not ipaddress.ip_address(host).is_global:
                return "unsafe_url"
        except ValueError:
            pass
    except (ValueError, TypeError):
        return "unsafe_url"
    return ""


def _failure_category(exc):
    """Return fixed, non-sensitive labels; never include URL/exception text."""
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if isinstance(status, int):
        if status in (401, 403):
            return "http_access_denied", False
        if status == 429:
            return "http_rate_limited", True
        if status in (404, 410):
            return "http_not_found", False
        if 500 <= status <= 599:
            return "http_server_error", True
        if 400 <= status <= 499:
            return "http_client_error", False
    if isinstance(exc, (requests.Timeout, TimeoutError)):
        return "timeout", True
    if isinstance(exc, requests.exceptions.SSLError):
        return "tls_error", False
    if isinstance(exc, (requests.ConnectionError, OSError)):
        return "connection_error", True
    if exc.__class__.__name__ == "BudgetExhausted":
        return "request_budget_exhausted", True
    if isinstance(exc, ValueError):
        if str(exc) == "public_response_too_large":
            return "response_too_large", False
        if str(exc) == "redirect_limit":
            return "redirect_limit", False
        return "unsafe_url", False
    return "unexpected_error", False


def resolve_alert_hints(hints, request_fn, max_links=10):
    from scout_agent import _extract_job_json_ld, _html_to_text, _is_job_board_url, _normalize_url, _parse_iso_datetime
    jobs, seen, categories = [], set(), Counter()
    max_links = min(100, max(0, int(max_links)))
    stats = {"status": "ok", "hints": len(hints), "resolved": 0, "leads": 0,
             "blocked": 0, "errors": 0, "unverified_dates": 0,
             "retry_queued": 0, "retry_exhausted": 0, "duplicates": 0, "queue_dropped": 0,
             "deferred_budget": 0,
             "pending_hints": []}
    pending_seen = set()

    def enqueue(hint):
        # The caller stores this private queue encrypted and removes it from
        # diagnostics. Keep the queue bounded even for malformed/duplicate input.
        key = str(hint.get("url", ""))
        if key and key not in pending_seen and len(stats["pending_hints"]) < 100:
            pending_seen.add(key)
            stats["pending_hints"].append(hint)
            return True
        if key and key not in pending_seen:
            stats["queue_dropped"] += 1
        return False

    def is_board(url):
        # The shared legacy helper scans its whole input; inspecting only the
        # host avoids treating an employer URL containing a tracking query as a
        # job-board page.
        return _is_job_board_url(urlsplit(url).hostname or "")

    for hint in hints[max_links:]:
        enqueue(hint)
    for index, hint in enumerate(hints[:max_links]):
        try:
            url = _unwrap(str(hint.get("url", "")))
            reason = _url_block_reason(url)
            if reason:
                categories[reason] += 1
                stats["blocked"] += 1
                continue
            canonical = _normalize_url(url)
            if canonical in seen:
                stats["duplicates"] += 1
                continue
            seen.add(canonical)
            # The request function enforces public DNS, HTTPS, response limits and
            # validates every redirect; platform pages are discovery only.
            # Retry only on a later run after classification; do not burn extra
            # requests immediately on an access-denied tracker.
            response = request_fn(url, timeout=15, max_retries=0)
            final_url = getattr(response, "url", None) or url
            reason = _url_block_reason(final_url)
            if reason:
                categories[reason] += 1
                stats["blocked"] += 1
                continue
            structured = _extract_job_json_ld(response.text[:1_000_000])
            observed_original = structured.get("date_posted")
            stats["resolved"] += 1
            target = urljoin(final_url, structured.get("application_url") or final_url)
            reason = _url_block_reason(target)
            if reason:
                categories[reason] += 1
                stats["blocked"] += 1
                continue
            # A public board may explicitly identify an employer vacancy in its
            # JobPosting data. Verify that exact destination independently; never
            # transfer a board's publication date to the employer's page.
            if structured and is_board(final_url) and target != final_url and not is_board(target):
                response = request_fn(target, timeout=15, max_retries=0)
                final_url = getattr(response, "url", None) or target
                reason = _url_block_reason(final_url)
                if reason:
                    categories[reason] += 1
                    stats["blocked"] += 1
                    continue
                structured = _extract_job_json_ld(response.text[:1_000_000])
                target = urljoin(final_url, structured.get("application_url") or final_url)
            if is_board(final_url) or is_board(target):
                categories["job_board_only"] += 1
                stats["blocked"] += 1
                continue
            if not structured:
                categories["no_structured_vacancy"] += 1
                stats["unverified_dates"] += 1
                continue
            if not structured.get("date_posted"):
                categories["missing_original_date"] += 1
                stats["unverified_dates"] += 1
                continue
            if not _parse_iso_datetime(structured["date_posted"]):
                categories["invalid_original_date"] += 1
                stats["unverified_dates"] += 1
                continue
            earlier = _parse_iso_datetime(observed_original)
            if earlier and earlier < _parse_iso_datetime(structured["date_posted"]):
                # An explicit same-vacancy source may make the job older, never
                # younger. Employer evidence still needs its own valid date.
                structured["first_published"] = observed_original
            reason = _url_block_reason(target)
            if reason:
                categories[reason] += 1
                stats["blocked"] += 1
                continue
            jobs.append({**structured, "href": final_url, "url": final_url,
                         "application_url": target, "source_type": "direct_apply",
                         "source_name": "gmail_alert:" + str(hint.get("alert_sender_domain", "unknown")),
                         "source_id": canonical, "page_fetched": True, "active_listing": True,
                         "source_text": (structured.get("description", "") + "\n" + _html_to_text(response.text))[:12000],
                         "alert_message_id": hint.get("alert_message_id", ""),
                         "alert_received_at": hint.get("alert_received_at", "")})
        except Exception as exc:
            stats["errors"] += 1
            category, retryable = _failure_category(exc)
            categories[category] += 1
            if category == "request_budget_exhausted":
                # These links were not attempted; a shared deadline is not a
                # vacancy failure and must not consume their retry allowance.
                for deferred in hints[index:max_links]:
                    stats["deferred_budget"] += int(enqueue(deferred))
                break
            if retryable:
                try:
                    retries = max(0, int(hint.get("resolve_attempts", 0))) + 1
                except (ValueError, TypeError):
                    retries = 1
                if retries <= 2:
                    stats["retry_queued"] += int(enqueue({**hint, "resolve_attempts": retries}))
                else:
                    stats["retry_exhausted"] += 1
    stats["leads"] = len(jobs)
    stats["failure_categories"] = dict(sorted(categories.items()))
    if stats["errors"] and not stats["resolved"]:
        stats["status"] = "error"
    return jobs, stats
