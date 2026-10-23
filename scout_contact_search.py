"""Free public-web enrichment. Search is discovery, never contact evidence.

Only the unauthenticated Exa web_search_exa tool is allowed. Public employer
pages are fetched through the existing SSRF/request budget boundary. No cookies,
inbox probes, address generation, employee databases or paid fallbacks.
"""
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from scout_contacts import company_key, contact_registry, vacancy_belongs_to, _owned_host, _recent
from scout_mail import EMAIL, email_applications_forbidden
from scout_sources import safe_url, text
from scout_state import IST

ENDPOINT = "https://mcp.exa.ai/mcp"
POLICY = "named-recruitment-v1"
DENIED = re.compile(r"linkedin|naukri|rocketreach|zoominfo|apollo\.io|signalhire|contactout|facebook|instagram|twitter|x\.com$", re.I)
PRIVATE = {"gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "live.com", "proton.me", "protonmail.com", "icloud.com", "aol.com"}
RECRUITMENT = re.compile(r"\brecruit(?:er|ment|ing)?\b|\btalent acquisition\b|\bhiring\b", re.I)
NEGATIVE = re.compile(r"\bformer(?:ly)?\b|\bprevious(?:ly)?\b|\bex[- ]employee\b|\b(?:worked|used to work) at\b|\buntil 20\d\d\b|\barchived\b|\bno unsolicited\b|\bdo not (?:contact|email)\b", re.I)
PROHIBITION = re.compile(r"\bno unsolicited\b|\bdo not (?:contact|email)\b", re.I)
NAME = re.compile(r"[A-Z][A-Za-z'-]{1,30}(?: [A-Z][A-Za-z'-]{1,30}){1,3}\Z")


def labelled_contacts(value):
    """Explicit labels in public documents/Reader, not inferred name patterns."""
    from html import escape
    blocks = []
    pattern = (r"(?:^|\n)\s*(?:Contact name|Name)\s*:\s*([^\n]{3,100})\s*\n"
               r"\s*(?:Position|Designation|Job title)\s*:\s*([^\n]{3,160})\s*\n"
               r"\s*(?:Email|E-mail)\s*:\s*([^\n]{3,160})")
    for match in re.finditer(pattern, value, re.I):
        blocks.append("<section><h3>" + escape(match[1].strip()) + "</h3><p>"
                      + escape(match[2]) + " " + escape(match[3]) + "</p></section>")
    return "".join(blocks)


class BackendUnavailable(RuntimeError):
    """Fixed labels only: provider bodies may contain private contact details."""


class FreeSearch:
    def __init__(self, request):
        self.request, self.schema = request, None
        self.status = "not_checked"
        self.retry_after = 0

    def _rpc(self, method, params, identity):
        try:
            response = self.request(ENDPOINT, method="POST", max_retries=0, timeout=15,
                                    return_http_errors=True,
                                    headers={"Accept": "application/json, text/event-stream"},
                                    json={"jsonrpc": "2.0", "id": identity, "method": method, "params": params})
            if response.status_code in {401, 402, 403}:
                raise BackendUnavailable("authentication_or_payment_required")
            if response.status_code == 429:
                value = str(getattr(response, "headers", {}).get("Retry-After", ""))
                try:
                    self.retry_after = max(0, int(value))
                except ValueError:
                    from email.utils import parsedate_to_datetime
                    try:
                        self.retry_after = max(0, (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds())
                    except (TypeError, ValueError):
                        pass
                raise BackendUnavailable("free_quota_exhausted")
            if response.status_code != 200:
                raise BackendUnavailable("backend_http_error")
            raw = response.text[:1000000]
            if raw.lstrip().startswith("{"):
                messages = [json.loads(raw)]
            else:
                messages = [json.loads(line[5:].strip()) for line in raw.splitlines()
                            if line.startswith("data:") and line[5:].strip() not in {"", "[DONE]"}]
            message = next((v for v in messages if isinstance(v, dict) and v.get("id") == identity), {})
            result = message.get("result")
            if message.get("error") or not isinstance(result, dict) or result.get("isError"):
                # Examine only for classification. Never log the remote message.
                if re.search(r"quota|rate.limit|free.limit", json.dumps(message), re.I):
                    raise BackendUnavailable("free_quota_exhausted")
                raise BackendUnavailable("backend_contract_error")
            return result
        except BackendUnavailable as exc:
            self.status = str(exc)
            raise
        except Exception as exc:
            self.status = "backend_unavailable"
            raise BackendUnavailable(self.status) from exc

    def check(self):
        if self.schema is not None:
            return
        result = self._rpc("tools/list", {}, 1)
        tools = result.get("tools", [])
        if not isinstance(tools, list):
            self.status = "backend_schema_changed"
            raise BackendUnavailable(self.status)
        tool = next((v for v in tools if isinstance(v, dict) and v.get("name") == "web_search_exa"), {})
        schema = tool.get("inputSchema", {})
        if not isinstance(schema, dict):
            self.status = "backend_schema_changed"
            raise BackendUnavailable(self.status)
        props = schema.get("properties", {})
        required = schema.get("required", [])
        if (schema.get("type") != "object" or not isinstance(props, dict)
                or any(not isinstance(props.get(key, {}), dict) for key in ("query", "objective", "numResults"))
                or props.get("query", {}).get("type") != "string"
                or not isinstance(required, list) or any(not isinstance(v, str) for v in required)
                or set(required) - {"query", "objective", "numResults"}
                or ("objective" in props and props["objective"].get("type") != "string")
                or ("numResults" in props and props["numResults"].get("type") not in {"number", "integer"})):
            self.status = "backend_schema_changed"
            raise BackendUnavailable(self.status)
        self.schema = schema
        self.status = "ready"

    def search(self, query):
        self.check()
        args = {"query": query}
        props = self.schema["properties"]
        if "objective" in props:
            args["objective"] = ("Find official employer recruitment/contact pages and public professional recruitment contacts. "
                                 "Return source URLs. Do not infer email addresses. Exclude social sites and employee databases.")
        if "numResults" in props:
            args["numResults"] = 4
        result = self._rpc("tools/call", {"name": "web_search_exa", "arguments": args}, 2)
        blocks = result.get("content")
        if not isinstance(blocks, list) or not all(isinstance(v, dict) for v in blocks):
            self.status = "backend_schema_changed"
            raise BackendUnavailable(self.status)
        combined = "\n".join(v.get("text", "") for v in blocks if v.get("type") == "text" and isinstance(v.get("text"), str))
        if re.search(r"free.*rate.limit|rate.limit.*free|quota exceeded", combined, re.I):
            self.status = "free_quota_exhausted"
            raise BackendUnavailable(self.status)
        # The provider's snippets are deliberately discarded, including emails.
        urls = re.findall(r"^URL:\s*(https://[^\s<>]+)", combined, re.M)
        if not urls and not re.search(r"no (?:search )?results|nothing found|no matches", combined, re.I):
            self.status = "backend_schema_changed"
            raise BackendUnavailable(self.status)
        self.status = "ready"
        return list(dict.fromkeys(u for u in urls if safe_url(u) and not DENIED.search(urlsplit(u).hostname or "")))[:4]


class ContactPage(HTMLParser):
    """Small semantic staff/contact blocks; never join different people's cards."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.blocks, self.links, self.persons = [], [], [], []
        self.skip = 0
        self.script = None
        self.visible = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script":
            self.script = [] if attrs.get("type", "").lower() == "application/ld+json" else None
        if tag in {"script", "style", "nav", "footer"}:
            self.skip += 1
        if tag == "a" and attrs.get("href"):
            self.links.append(attrs["href"])
        if tag in {"article", "section", "li", "div", "tr", "p"}:
            self.stack.append({"tag": tag, "parts": [], "names": [], "paragraphs": [], "heading": False})
        if re.fullmatch(r"h[1-6]", tag):
            for block in self.stack:
                block["heading"] = True

    def handle_data(self, value):
        if self.script is not None:
            self.script.append(value)
        if self.skip:
            return
        self.visible.append(value)
        for block in self.stack:
            block["parts"].append(value)
            if block["heading"]:
                block["names"].append(value)

    def handle_endtag(self, tag):
        if tag == "script" and self.script is not None:
            try:
                self._people(json.loads("".join(self.script)))
            except (ValueError, TypeError):
                pass
            self.script = None
        if tag in {"script", "style", "nav", "footer"}:
            self.skip = max(0, self.skip - 1)
        if re.fullmatch(r"h[1-6]", tag):
            for block in self.stack:
                block["heading"] = False
        if self.stack and self.stack[-1]["tag"] == tag:
            block = self.stack.pop()
            value = " ".join(" ".join(block["parts"]).split())
            if tag == "p":
                for parent in self.stack:
                    parent["paragraphs"].append(value)
            if len(value) <= 900 and len(set(EMAIL.findall(value))) == 1:
                self.blocks.append((value, " ".join(block["names"]).strip(), block["paragraphs"]))

    def _people(self, value):
        if isinstance(value, list):
            for item in value:
                self._people(item)
        elif isinstance(value, dict):
            if value.get("@type") == "Person":
                self.persons.append(value)
            if "@graph" in value:
                self._people(value["@graph"])


def official_roots(employer):
    roots = set(employer.get("official_domains", []))
    for key in ("official_url", "careers_url"):
        host = urlsplit(str(employer.get(key) or "")).hostname or ""
        if host and not re.search(r"greenhouse|lever\.co|smartrecruiters|ashbyhq|workable|oraclecloud|myworkdayjobs|applytojob", host):
            roots.add(host.removeprefix("www."))
    return {v.lower() for v in roots if v and not DENIED.search(v)}


def named_evidence(content, url, employer, roots, post, now):
    """Fail closed when person/email/employer/recruitment scope cannot be joined."""
    parser = ContactPage()
    parser.feed(content[:500000])
    visible = " ".join(parser.visible)
    if PROHIBITION.search(visible) or email_applications_forbidden({"source_text": visible}):
        return [], "contact_instructions_prohibit"
    candidates = []
    for person in parser.persons:
        works = person.get("worksFor", {})
        if not isinstance(works, dict) or company_key(works.get("name")) != company_key(employer["name"]):
            continue
        title, name, email = person.get("jobTitle", ""), person.get("name", ""), person.get("email", "")
        if all(isinstance(v, str) for v in (title, name, email)):
            candidates.append((name, title, email.removeprefix("mailto:"), f"{name}; {title}; {works['name']}; {email}"))
    for block, heading, paragraphs in parser.blocks:
        # A page-level heading plus someone else's email is never enough.
        position = paragraphs[0] if paragraphs else block.removeprefix(heading).strip()
        position = EMAIL.sub("", position).strip()
        if NAME.fullmatch(heading) and RECRUITMENT.search(position):
            candidates.append((heading, position, EMAIL.findall(block)[0], block))
    accepted = []
    rejected = 0
    for name, position, email, excerpt in candidates:
        email = email.lower().strip()
        if (not NAME.fullmatch(name) or not EMAIL.fullmatch(email) or NEGATIVE.search(excerpt)
                or not RECRUITMENT.search(position) or len(excerpt) > 900
                or re.search(r"\b(?:contact|apply|resume|send|ignore|instructions|email us)\b", position, re.I)):
            rejected += 1
            continue
        domain = email.rsplit("@", 1)[-1]
        local = email.split("@", 1)[0]
        if (domain in PRIVATE or not any(_owned_host(domain, root) for root in roots)
                or re.search(r"sales|support|privacy|billing|no.?reply|enquir", local)):
            rejected += 1
            continue
        # Exact-role hiring-manager evidence must explicitly name this vacancy.
        ref = str(post.get("vacancy_reference") or "")
        title = str(post.get("job_title") or post.get("title") or "")
        exact_role = (ref and ref in excerpt) or (len(title) > 8 and title.lower() in excerpt.lower())
        manager = bool(re.search(r"hiring manager", position, re.I) and exact_role)
        if re.search(r"hiring manager", position, re.I) and not manager:
            rejected += 1
            continue
        is_talent = bool(re.search(r"recruit|talent acquisition", position, re.I))
        specialisms = re.findall(r"\b(?:software|engineering|executive|campus|internship|sales)\b", position, re.I)
        if specialisms and not any(term.lower() in title.lower() for term in specialisms):
            rejected += 1
            continue
        if not manager and not is_talent and not (re.search(r"\bHR\b|human resources", position, re.I) and RECRUITMENT.search(position)):
            rejected += 1
            continue
        relevance = "confirmed_role_hiring_manager" if manager else "recruitment_contact" if is_talent else "hr_recruitment"
        accepted.append({"email": email, "name": name, "first_name": name.split()[0], "position": position[:300],
                         "employer": employer["name"], "hiring_relevance": relevance, "source_url": url,
                         "excerpt": excerpt, "verified_at": now.isoformat(), "route": "named_recruiter",
                         "scope": "vacancy" if manager else "employer_recruitment", "rank": 2 if manager else 3 if is_talent else 4})
    unique = {row["email"]: row for row in accepted}
    return sorted(unique.values(), key=lambda row: (row["rank"], row["email"])), rejected


class NamedDiscovery:
    def __init__(self, request, store, *, registry=None, now=None):
        self.request, self.store = request, store
        self.registry = contact_registry() if registry is None else registry
        self.now = now or datetime.now(timezone.utc)
        self.client = FreeSearch(request)
        self.fetches = self.searches = 0
        self.stopped = False
        self.search_stopped = False
        self.jina_enabled = True
        self.pages = {}
        self.reader_pages = set()
        self.active_employer = None
        self.employer_fetches = {}
        self.employer_limit = 4
        self.counted_contacts = set()
        self.stats = {"contacts_discovered": 0, "hiring_relevance_confirmed": 0, "contacts_rejected": 0,
                      "contacts_new": 0, "contacts_cached": 0, "contacts_revalidated": 0,
                      "searches": 0, "evidence_fetches": 0, "backend": "not_needed", "reasons": {}}
        self.cache = store.data.setdefault("named_contacts", {})
        for key, row in list(self.cache.items()):
            try:
                stamp = datetime.fromisoformat(row.get("last_used_at") or row["checked_at"])
                if stamp.tzinfo is None or self.now - stamp > timedelta(days=30):
                    del self.cache[key]
            except (ValueError, TypeError, KeyError, AttributeError):
                del self.cache[key]

    def prioritize_candidates(self, candidates):
        """Rotate unresolved employers without changing fit order within an employer."""
        def key(item):
            return company_key(item[0].get("company"))
        employers = {key(item) for item in candidates if item[0].get("email_safe") and not item[0].get("recruiter_email")}
        self.employer_limit = 2 if len(employers) > 1 else 4
        def age(item):
            if key(item) not in employers:
                return ""
            return self.cache.get("poll:" + key(item), {}).get("checked_at", "")
        return sorted(candidates, key=age)

    @property
    def revision(self):
        return hashlib.sha256((POLICY + json.dumps(self.registry, sort_keys=True)).encode()).hexdigest()[:24]

    def reason(self, label):
        self.stats["reasons"][label] = self.stats["reasons"].get(label, 0) + 1

    def _search(self, query):
        if self.stopped or self.search_stopped or self.searches >= 2:
            self.reason("run_search_limit")
            return []
        allowed, status = search_recovery(self.store.data, self.now)
        if not allowed:
            self.stats["backend"] = status
            self.reason("search_recovery_wait")
            return []
        ledger = self.store.data.setdefault("contact_search_days", {})
        day = self.now.astimezone(IST).date().isoformat()
        count = ledger.get(day, 0)
        if type(count) is not int or count < 0 or count >= 6:
            self.reason("daily_search_limit")
            return []
        # Count failed calls too. Persist before using the free provider.
        ledger[day] = count + 1
        for old in sorted(ledger)[:-30]:
            del ledger[old]
        self.searches += 1
        self.stats["searches"] = self.searches
        if not self.store.save():
            self.stopped = True
            self.stats["backend"] = "state_safety_stop"
            return []
        try:
            urls = self.client.search(query[:600])
            self.stats["backend"] = "ready"
            record_search_health(self.store.data, "verified", self.now)
            return urls
        except BackendUnavailable as exc:
            self.search_stopped = True
            self.stats["backend"] = str(exc)
            record_search_health(self.store.data, str(exc), self.now, getattr(self.client, "retry_after", 0))
            if not self.store.save():
                self.stopped = True
                self.stats["backend"] = "state_safety_stop"
            self.reason(str(exc))
            return []

    def _page(self, url, roots):
        if url in self.pages:
            return self.pages[url]
        if (self.fetches >= 4 or self.employer_fetches.get(self.active_employer, 0) >= self.employer_limit
                or not safe_url(url) or not any(_owned_host(urlsplit(url).hostname, root) for root in roots)):
            return None
        self.fetches += 1
        self.employer_fetches[self.active_employer] = self.employer_fetches.get(self.active_employer, 0) + 1
        self.stats["evidence_fetches"] = self.fetches
        try:
            response = self.request(url, max_retries=0, timeout=10, return_http_errors=True)
            final = getattr(response, "url", url) or url
            if response.status_code != 200 or not safe_url(final) or not any(_owned_host(urlsplit(final).hostname, root) for root in roots):
                self.reason("evidence_access_unavailable")
                return None
            is_pdf = "pdf" in response.headers.get("Content-Type", "").lower() or final.lower().endswith(".pdf")
            if is_pdf:
                # Keep documents bounded; never execute embedded content.
                from io import BytesIO
                from pypdf import PdfReader
                reader = PdfReader(BytesIO(response.content))
                if reader.is_encrypted or len(reader.pages) > 10:
                    self.reason("document_unsupported")
                    return None
                # Only explicit, labelled contact paragraphs can establish a PDF person.
                paragraphs = [p.extract_text() or "" for p in reader.pages]
                raw_text = "\n".join(paragraphs)[:200000]
                content = labelled_contacts(raw_text)
                if not content:
                    self.reason("document_contact_labels_missing")
                    return None
                from html import escape
                content += "<pre>" + escape(raw_text) + "</pre>"
            else:
                content = response.text[:500000]
            if re.search(r"captcha|access denied|sign in to continue|log in to continue", text(content), re.I):
                self.reason("evidence_access_unavailable")
                return None
            # Reader is optional only AFTER a successful unrestricted original
            # fetch. Never use it for a 403, CAPTCHA or authentication barrier.
            visible = text(re.sub(r"<script\b[^>]*>.*?</script>", "", content, flags=re.I | re.S))
            if (self.jina_enabled and len(visible) < 120 and not is_pdf and "application/ld+json" not in content
                    and self.fetches < 4 and self.employer_fetches.get(self.active_employer, 0) < self.employer_limit):
                self.fetches += 1
                self.employer_fetches[self.active_employer] = self.employer_fetches.get(self.active_employer, 0) + 1
                self.stats["evidence_fetches"] = self.fetches
                try:
                    reader = self.request("https://r.jina.ai/" + final, max_retries=0, timeout=10, return_http_errors=True)
                    if reader.status_code == 200 and not re.search(r"captcha|access denied|login required|robots.txt", reader.text, re.I):
                        from html import escape
                        content += labelled_contacts(reader.text[:100000]) + "<pre>" + escape(reader.text[:100000]) + "</pre>"
                        self.reader_pages.add(final)
                    else:
                        self.reason("reader_unavailable")
                except Exception:
                    self.reason("reader_unavailable")
            self.pages[url] = (final, content)
            return self.pages[url]
        except Exception:
            self.reason("evidence_fetch_failed")
            return None

    def find(self, post):
        if self.stopped:
            return None
        try:
            return self._find(post)
        except Exception:
            # Malformed public documents/cache/provider output cannot take down
            # the existing application pipeline or become sending evidence.
            self.reason("contact_evidence_invalid")
            return None

    def _find(self, post):
        if (post.get("page_fetched") is not True or any(post.get(k) for k in ("page_fetch_failed", "access_blocked", "reverification_failed"))
                or email_applications_forbidden(post)):
            self.reason("vacancy_not_verified_or_portal_only")
            return None
        employer = next((e for e in self.registry if vacancy_belongs_to(post, e)), None)
        if not employer:
            self.reason("employer_ownership_unverified")
            return None
        self.active_employer = company_key(employer["name"])
        if self.fetches < 4 and self.employer_fetches.get(self.active_employer, 0) < self.employer_limit:
            self.cache["poll:" + self.active_employer] = {"checked_at": self.now.isoformat()}
        roots = official_roots(employer)
        if not roots:
            # Search can suggest a site, but only an employer-named official
            # page linking the exact configured ATS tenant can bind it.
            candidates = [u for u in post.get("employer_websites", []) if safe_url(u)][:2]
            if not candidates:
                candidates = self._search(f'"{employer["name"]}" official careers website')
            for url in candidates:
                host = (urlsplit(url).hostname or "").removeprefix("www.")
                page = self._page(url, {host})
                if not page:
                    continue
                final, content = page
                parser = ContactPage()
                parser.feed(content)
                heading = re.search(r"<(?:title|h1)\b[^>]*>(.*?)</(?:title|h1)>", content, re.I | re.S)
                attested = {urlsplit(u).hostname.removeprefix("www.") for u in post.get("employer_websites", []) if safe_url(u)}
                if (host in attested and heading and company_key(employer["name"]) in company_key(text(heading[1]))
                        and any(vacancy_belongs_to({"company": employer["name"], "href": urljoin(final, link)}, employer)
                                for link in parser.links if safe_url(urljoin(final, link)))):
                    roots = {host}
                    break
        if not roots:
            self.reason("official_domain_unverified")
            return None
        if employer.get("ats") in {"greenhouse", "lever", "smartrecruiters", "ashby", "workable", "workday", "oracle"}:
            # Reciprocal binding, not a company-name/search-ranking assertion.
            binding_key = "binding:" + company_key(employer["name"])
            binding = self.cache.get(binding_key, {})
            if binding.get("revision") != self.revision or not _recent(binding.get("checked_at"), self.now):
                ats_host = urlsplit(str(post.get("application_url") or post.get("href"))).hostname
                seeds = [employer.get("official_url"), employer.get("careers_url")]
                seeds += ["https://" + root + "/careers" for root in sorted(roots)]
                bound = False
                for url in dict.fromkeys(u for u in seeds if safe_url(u)):
                    page = self._page(url, roots)
                    if not page:
                        continue
                    parser = ContactPage()
                    parser.feed(page[1])
                    links = [urljoin(page[0], link) for link in parser.links]
                    if any(urlsplit(link).hostname == ats_host and vacancy_belongs_to({"company": employer["name"], "href": link}, employer)
                           for link in links if safe_url(link)):
                        self.cache[binding_key] = {"revision": self.revision, "checked_at": self.now.isoformat(), "source_url": page[0]}
                        bound = True
                        break
                if not bound:
                    self.reason("official_ats_binding_unverified")
                    return None
        # Cache is vacancy-bound: a role-specific hiring manager cannot leak to
        # another vacancy. Re-fetch exact sources after 24h, not old snippets.
        key = hashlib.sha256((self.revision + str(post.get("href")) + "|" + employer["name"]).encode()).hexdigest()[:24]
        cached = self.cache.get(key, {})
        shared_key = "employer:" + self.revision + ":" + self.active_employer
        shared = self.cache.get(shared_key, {})
        if (not cached and _recent(shared.get("checked_at"), self.now)):
            cached = dict(shared)
            self.cache[key] = cached
        evidence = cached.get("evidence", [])
        evidence = [v for v in evidence if isinstance(v, dict) and v.get("email") and v.get("source_url")
                    and v.get("route") == "named_recruiter" and v.get("rank") in {1, 2, 3, 4}]
        cache_hit = _recent(cached.get("checked_at"), self.now) and not cached.get("pending_urls")
        if cache_hit:
            valid = [row for row in evidence if _recent(row.get("verified_at"), self.now)
                     and (self.jina_enabled or row.get("evidence_backend") != "jina")]
        else:
            urls = list(cached.get("pending_urls", []))[:8]
            urls += [row["source_url"] for row in evidence if isinstance(row, dict) and row.get("source_url")]
            seeds = employer.get("recruitment_pages") or [employer.get("official_url") or employer.get("careers_url")]
            vacancy_url = post.get("application_url") or post.get("href")
            urls += [u for u in [vacancy_url, *seeds] if safe_url(u) and any(_owned_host(urlsplit(u).hostname, root) for root in roots)]
            valid = []
            searched = False
            visited = set()
            while self.fetches < 4 and self.employer_fetches.get(self.active_employer, 0) < self.employer_limit:
                if not urls:
                    if searched or self.stopped:
                        break
                    searched = True
                    role = re.sub(r"[^A-Za-z0-9 -]", " ", str(post.get("job_title") or ""))[:80]
                    urls = self._search(f'site:{sorted(roots)[0]} "{employer["name"]}" recruitment talent acquisition HR email contact {role}')
                    if not urls:
                        break
                url = urls.pop(0)
                if url in visited:
                    continue
                visited.add(url)
                page = self._page(url, roots)
                if not page:
                    continue
                final, content = page
                rows, rejected = named_evidence(content, final, employer, roots, post, self.now)
                for row in rows:
                    row["evidence_backend"] = "jina" if final in self.reader_pages else "direct"
                if final == vacancy_url:
                    for row in rows:
                        row.update(rank=1, scope="vacancy", hiring_relevance="vacancy_contact")
                if isinstance(rejected, str):
                    self.reason(rejected)
                else:
                    self.stats["contacts_rejected"] += rejected
                valid.extend(rows)
                if valid:
                    break
                # Bounded official-site navigation, independent of web search.
                parser = ContactPage()
                parser.feed(content)
                for link in parser.links:
                    target = urljoin(final, link)
                    if (safe_url(target) and target not in visited and target not in urls
                            and any(_owned_host(urlsplit(target).hostname, root) for root in roots)
                            and re.search(r"career|recruit|team|people|leadership|contact", urlsplit(target).path, re.I)):
                        urls.append(target)
                urls = urls[:8]
            if visited:
                self.cache[key] = {"checked_at": self.now.isoformat(), "evidence": valid[:3],
                                   "pending_urls": [] if valid else list(dict.fromkeys(urls))[:8]}
                # Only non-specialist employer-wide recruitment contacts can be
                # reused across vacancies. Exact-role managers never transfer.
                reusable = [row for row in valid if row.get("scope") == "employer_recruitment"
                            and row.get("rank") in {3, 4} and not re.search(
                                r"\b(?:software|engineering|executive|campus|internship|sales)\b", row.get("position", ""), re.I)]
                if reusable:
                    self.cache[shared_key] = {"checked_at": self.now.isoformat(), "evidence": reusable[:3]}
        if not valid:
            self.reason("no_verified_named_contact")
            return None
        proof = sorted(valid, key=lambda row: (row["rank"], row["email"]))[0]
        self.cache[key]["last_used_at"] = self.now.isoformat()
        old_emails = {row.get("email") for row in evidence}
        for row in valid:
            identity = (self.active_employer, row["email"])
            if identity in self.counted_contacts:
                continue
            self.counted_contacts.add(identity)
            label = "contacts_cached" if cache_hit else "contacts_revalidated" if row["email"] in old_emails else "contacts_new"
            self.stats[label] += 1
            self.stats["contacts_discovered"] += int(label == "contacts_new")
            self.stats["hiring_relevance_confirmed"] += 1
        post["recruitment_contact_evidence"] = [proof]
        return proof


def named_rollout(data, revision):
    row = data.setdefault("named_contacts_rollout", {})
    if row.get("revision") != revision:
        row.clear()
        row.update(revision=revision, shadow_runs=0, scheduled_ids=[])
    if type(row.get("shadow_runs")) is not int or not 0 <= row["shadow_runs"] <= 3 or not isinstance(row.get("scheduled_ids"), list):
        row.update(invalid=True, shadow_runs=0, scheduled_ids=[])
    return row


def named_send_allowed(data, rollout, cloud_verified, now=None):
    if not cloud_verified:
        return False, "named_backend_not_cloud_verified"
    if rollout.get("invalid") or rollout.get("shadow_runs", 0) < 3:
        return False, "named_contact_shadow"
    now = now or datetime.now(timezone.utc)
    try:
        since = datetime.fromisoformat(rollout["live_since"]) if rollout.get("live_since") else now
        if since.tzinfo is None or since > now:
            raise ValueError("Invalid canary")
        if now - since >= timedelta(days=7):
            return True, "live"
        count = sum(v.get("step") == "initial" and (v.get("contact_route") == "named_recruiter" or v.get("outreach_purpose") == "recruiter_intro")
                    and datetime.fromisoformat(v["created_at"]).astimezone(IST).date() == now.astimezone(IST).date()
                    for v in data.get("sends", {}).values())
        return count < 2, "named_canary_limit" if count >= 2 else "canary"
    except (ValueError, TypeError, KeyError):
        return False, "named_rollout_invalid"


BACKEND_LABELS = {"verified", "free_quota_exhausted", "authentication_or_payment_required",
                  "backend_http_error", "backend_contract_error", "backend_schema_changed",
                  "backend_unavailable", "unavailable", "search_contract_unverified", "daily_search_limit"}


def record_search_health(data, status, now, retry_after=0):
    previous = data.get("contact_search_health", {})
    failures = min(8, int(previous.get("failures", 0)) + 1) if status != "verified" else 0
    delay = (min(7 * 86400, 86400 * 2 ** (failures - 1)) if status == "free_quota_exhausted"
             else min(86400, 3600 * 2 ** max(0, failures - 1))) if failures else 0
    delay = max(delay, retry_after)
    data["contact_search_health"] = {"status": status, "failures": failures, "checked_at": now.isoformat(),
        "retry_at": (now + timedelta(seconds=delay)).isoformat(), "policy": POLICY,
        "manual_check_required": status in {"authentication_or_payment_required", "backend_schema_changed", "backend_contract_error"}}


def search_recovery(data, now):
    row = data.get("contact_search_health", {})
    if not row or row.get("status") == "verified":
        return True, "ready"
    if row.get("manual_check_required"):
        return False, row.get("status", "backend_unavailable")
    try:
        due = datetime.fromisoformat(row["retry_at"])
        if due.tzinfo is None:
            raise ValueError("Invalid recovery time")
        return now >= due, row.get("status", "backend_unavailable")
    except (KeyError, TypeError, ValueError):
        return False, "backend_unavailable"


def cloud_backend_verification(data, *, cloud, encrypted, legacy_revision="", bootstrap_status=""):
    """A failed cloud check is visible, not silently mistaken for 'not needed'."""
    if not cloud or not encrypted:
        return False, "encrypted_cloud_verification_required"
    row = data.get("named_backend_verification", {})
    if row.get("policy") == POLICY and row.get("origin") == "github_actions" and str(row.get("run_id", "")).isdigit():
        verified = row.get("direct") == "verified"
        return verified, "verified" if verified else "direct_pages_not_cloud_verified"
    return legacy_revision == POLICY, "verified" if legacy_revision == POLICY else "not_verified"


def probe_backends(request, *, check_exa=True):
    """Credential-free Actions contract check; no candidate or recruiter data."""
    result = {"direct": "not_checked", "exa": "not_checked", "jina": "not_checked"}
    client = FreeSearch(request)
    try:
        if check_exa:
            urls = client.search("site:example.com Example Domain")
            result["exa"] = "verified" if urls else "search_contract_unverified"
    except BackendUnavailable as exc:
        result["exa"] = str(exc)
    try:
        response = request("https://example.com/", max_retries=0, timeout=10, return_http_errors=True)
        if response.status_code == 200 and "Example Domain" in response.text:
            result["direct"] = "verified"
            response = request("https://r.jina.ai/https://example.com/", max_retries=0, timeout=15, return_http_errors=True)
            result["jina"] = "verified" if response.status_code == 200 and "Example Domain" in response.text else "unavailable"
    except Exception:
        result["jina"] = "unavailable"
    return result


def record_cloud_check(*, check_exa=False):
    """Operator-triggered, no retry after a quota response, encrypted results.

    The check queries only example.com. It neither reads Gmail nor sends a resume.
    A later successful check activates the same scheduled shadow/canary safeguards
    without requiring a local computer or somebody to toggle a sending flag.
    """
    import os
    from scout_persistence import RuntimeBudget, GitHubWriter
    from scout_state import StateStore
    if (os.getenv("GITHUB_ACTIONS") != "true" or os.getenv("ZERO_COST_VERIFIED") != "true"
            or os.getenv("SCOUT_ENCRYPT_STATE") != "true" or not os.getenv("GITHUB_TOKEN")):
        raise RuntimeError("Encrypted zero-cost Actions verification is required")
    writer = GitHubWriter(token=os.environ["GITHUB_TOKEN"], repo=os.environ["GITHUB_REPOSITORY"])
    store = StateStore(commit_callback=writer)
    now = datetime.now(timezone.utc)
    bootstrap = os.getenv("SCOUT_NAMED_BACKEND_STATUS", "")
    if not store.data.get("contact_search_health") and bootstrap in BACKEND_LABELS and bootstrap != "verified":
        record_search_health(store.data, bootstrap, now)
    day = now.astimezone(IST).date().isoformat()
    ledger = store.data.setdefault("contact_search_days", {})
    used = ledger.get(day, 0)
    allowed, status = search_recovery(store.data, now)
    if check_exa and store.data.get("contact_search_health", {}).get("manual_check_required"):
        allowed = True  # Explicit operator check, never an automatic auth/schema retry.
    attempt_exa = check_exa and allowed and type(used) is int and 0 <= used < 6
    if attempt_exa:
        ledger[day] = used + 1
        if not store.save():
            raise RuntimeError("Backend quota reservation could not be persisted")
    result = probe_backends(RuntimeBudget(max_requests=4, deadline_seconds=60).request, check_exa=attempt_exa)
    if attempt_exa:
        record_search_health(store.data, result["exa"], now)
    else:
        result["exa"] = store.data.get("contact_search_health", {}).get("status", "not_checked")
    verified = result["direct"] == "verified"
    store.data["named_backend_verification"] = {**result, "verified": verified, "policy": POLICY,
        "origin": "github_actions", "run_id": os.environ["GITHUB_RUN_ID"], "checked_at": now.isoformat()}
    if not store.save():
        raise RuntimeError("Backend verification result could not be persisted")
    return result


if __name__ == "__main__":
    import os
    import argparse
    from scout_persistence import RuntimeBudget
    parser = argparse.ArgumentParser(description="Credential-free public search/Reader contract check")
    parser.add_argument("--record-cloud-check", action="store_true")
    parser.add_argument("--check-exa", action="store_true", help="Optional Exa check; respects persistent cooldown and search quota")
    args = parser.parse_args()
    result = record_cloud_check(check_exa=args.check_exa) if args.record_cloud_check else probe_backends(RuntimeBudget(max_requests=4, deadline_seconds=60).request, check_exa=args.check_exa)
    verified = result["direct"] == "verified"
    print(json.dumps(result, sort_keys=True))
    if args.record_cloud_check and os.getenv("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as handle:
            handle.write("## Contact capability check\n\n" + "\n".join(f"- {key}: {value}" for key, value in result.items())
                         + "\n\nDirect-page success unlocks scheduled shadows only. Exa and Reader are optional; no paid fallback.\n")
        if result["exa"] not in {"verified", "not_checked"}:
            print("::warning::Optional Exa search is unavailable; direct-page discovery is independent.")
    if os.getenv("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as handle:
            handle.write(f"verified={'true' if verified else 'false'}\n")
