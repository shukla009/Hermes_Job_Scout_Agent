"""Allowlisted public HTML application forms; never a generic ATS write API.

``prepare_application`` performs only a GET and returns a private, short-lived
plan. Do not persist or log that plan: it contains candidate answers and CSRF
tokens. ``submit_application`` performs one POST, without retries, and returns
only bounded operational evidence. The caller owns application reservation,
duplicate suppression, request/response limits and network/DNS protection.

An enabled adapter is an individually audited contract, not a hostname rule.
The shipped registry has no enabled routes until a real form's fields and
confirmation behaviour have been verified. Existing recruitment email remains
the working automatic submission route.
"""
from datetime import datetime, timezone
from hashlib import sha256
from html.parser import HTMLParser
import json
import math
import os
from pathlib import Path
import re
from urllib.parse import urljoin, urlparse

from scout_sources import safe_url

REGISTRY_PATH = Path(__file__).parent / 'config' / 'application_adapters.json'
RESUME_PATH = Path(__file__).parent / 'Shivank_Shukla_Data_Analyst.pdf'
MAX_FORM_BYTES = 500_000
MAX_RESUME_BYTES = 5 * 1024 * 1024
PLAN_TTL_SECONDS = 300
CHALLENGE = re.compile(r'(?:g-recaptcha|h-captcha|cf-turnstile|recaptcha/api|hcaptcha\.com|challenges\.cloudflare\.com|captcha-response)', re.I)
FORBIDDEN_KEYS = re.compile(r'password|\botp\b|passport|aadhaar|aadhar|pan_number|bank_account|credit_card|religion|caste', re.I)


def _utcnow():
    return datetime.now(timezone.utc)


def _iso(value):
    try:
        result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return result if result.tzinfo else result.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def load_adapters():
    try:
        data = json.loads(REGISTRY_PATH.read_text(encoding='utf-8'))
        return data.get('adapters', []) if isinstance(data, dict) else []
    except (OSError, ValueError, TypeError):
        return []


def load_application_answers():
    """Read only the explicitly verified secret answer bank; never infer facts."""
    try:
        value = json.loads(os.getenv('SCOUT_APPLICATION_ANSWERS', '{}'))
        return value if isinstance(value, dict) and value.get('verified') is True else {}
    except (ValueError, TypeError):
        return {}


def load_answers():
    """Runtime-facing alias; missing personal facts remain missing."""
    return load_application_answers()


def adapter_readiness(adapters=None):
    """Safe diagnostics suitable for status/Telegram; no answers or form tokens."""
    entries = load_adapters() if adapters is None else adapters
    enabled = sum(1 for item in entries if item.get('enabled') is True)
    bank = load_answers()
    return {
        'enabled_adapters': enabled,
        'disabled_adapters': len(entries) - enabled,
        'answer_bank_ready': bool(bank.get('verified') is True and _iso(bank.get('verified_at'))),
        'enabled': enabled,
        'audited': len(entries),
        'adapters': [{'id': str(item.get('id', ''))[:80],
                      'enabled': item.get('enabled') is True,
                      'reason': str(item.get('disabled_reason') or 'enabled; exact contract required')[:240]}
                     for item in entries],
    }


class _Forms(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.forms = []
        self.current = None
        self.select = None

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag == 'form':
            if self.current is not None:
                self.current['invalid'] = True
                return
            self.current = {'attrs': attrs, 'controls': [], 'invalid': False}
            self.forms.append(self.current)
        elif self.current is not None and tag in ('input', 'textarea', 'select'):
            item = dict(attrs)
            item['type'] = (attrs.get('type', 'text') if tag == 'input' else tag).lower()
            item['options'] = []
            self.current['controls'].append(item)
            if tag == 'select':
                self.select = item
        elif tag == 'option' and self.select is not None:
            # Only explicit option values can be submitted by this adapter.
            self.select['options'].append(attrs.get('value'))

    def handle_endtag(self, tag):
        if tag == 'form':
            self.current = None
            self.select = None
        elif tag == 'select':
            self.select = None


def _schema(form, base_url):
    attrs = form['attrs']
    controls = []
    for item in form['controls']:
        if item.get('type') in ('submit', 'button', 'reset'):
            continue
        controls.append({key: item.get(key) for key in
                         ('name', 'type', 'aria-label', 'placeholder', 'accept',
                          'pattern', 'min', 'max', 'minlength', 'maxlength')}
                        | {key: key in item for key in ('required', 'disabled', 'multiple')}
                        | {'options': item['options']})
    return {'action': urljoin(base_url, attrs.get('action', '')),
            'method': attrs.get('method', 'get').lower(),
            'enctype': attrs.get('enctype', '').lower(), 'controls': controls}


def contract_fingerprint(html, page_url, form_id):
    """Return schema fingerprint for an audited form; never submits anything."""
    parser = _Forms()
    parser.feed(html[:MAX_FORM_BYTES])
    forms = [form for form in parser.forms if form['attrs'].get('id') == form_id]
    if len(forms) != 1 or forms[0]['invalid']:
        return ''
    return sha256(json.dumps(_schema(forms[0], page_url), sort_keys=True,
                             separators=(',', ':')).encode()).hexdigest()


def _url_allowed(url, adapter):
    return safe_url(url) and urlparse(url).hostname in adapter.get('allowed_hosts', [])


def _matches(pattern, value):
    try:
        return bool(pattern and re.fullmatch(pattern, value))
    except (re.error, TypeError):
        return False


def _valid_control_answer(control, answer):
    """Mirror only the simple browser constraints this adapter can prove."""
    try:
        if control.get('maxlength') and len(answer) > int(control['maxlength']):
            return False
        if control.get('minlength') and len(answer) < int(control['minlength']):
            return False
        if control.get('pattern') and not _matches(control['pattern'], answer):
            return False
        if control['type'] == 'number':
            numeric = float(answer)
            if not math.isfinite(numeric):
                return False
            if control.get('min') is not None and numeric < float(control['min']):
                return False
            if control.get('max') is not None and numeric > float(control['max']):
                return False
        return True
    except (ValueError, TypeError, OverflowError):
        return False


def _blocked(reason, status='blocked', **kwargs):
    return {'status': status, 'reason': reason, **kwargs}


def _verification_valid(adapter):
    verification = adapter.get('verification') or {}
    return (verification.get('public_form') is True and
            verification.get('no_authentication') is True and
            verification.get('no_challenge') is True and
            verification.get('confirmation_verified') is True and
            bool(_iso(verification.get('checked_at'))) and
            _url_allowed(verification.get('evidence_url', ''), adapter))


def _fetch_form(url, adapter, request_fn):
    response = request_fn('GET', url, timeout=20, allow_redirects=False)
    if response.status_code != 200 or getattr(response, 'url', url) != url:
        raise ValueError('form_unavailable_or_redirected')
    content = response.text
    if len(content.encode('utf-8')) > MAX_FORM_BYTES:
        raise ValueError('form_response_too_large')
    if CHALLENGE.search(content):
        raise ValueError('challenge_detected')
    parser = _Forms()
    parser.feed(content)
    forms = [form for form in parser.forms if form['attrs'].get('id') == adapter.get('form_id')]
    if len(forms) != 1 or forms[0]['invalid']:
        raise ValueError('form_identity_changed')
    form = forms[0]
    schema = _schema(form, url)
    if schema['method'] != 'post' or schema['enctype'] != 'multipart/form-data':
        raise ValueError('unsupported_form_transport')
    if not form['attrs'].get('action') or schema['action'] != adapter.get('action_url') or not _url_allowed(schema['action'], adapter):
        raise ValueError('form_action_changed')
    if not adapter.get('contract_sha256') or contract_fingerprint(content, url, adapter['form_id']) != adapter['contract_sha256']:
        raise ValueError('form_contract_changed')
    return form


def prepare_application(post, analysis, request_fn, answers=None, adapters=None, *, resume_path=None):
    """Inspect one exact allowlisted form and construct a private ephemeral plan.

    Answers: {'verified': True, 'verified_at': ISO, 'values': {full_name, email,
    phone, ...}, 'consents': {exact_adapter_consent_key: True}}. The adapter
    defines exact answer keys; there are no guessed answers or checkbox defaults.
    """
    url = str(analysis.get('application_url') or post.get('application_url') or post.get('href') or '')
    entries = load_adapters() if adapters is None else adapters
    matches = [entry for entry in entries if entry.get('enabled') is True and
               _url_allowed(url, entry) and _matches(entry.get('page_url_pattern'), url) and
               str(entry.get('company', '')).casefold() == str(analysis.get('company') or post.get('company') or '').casefold()]
    if not matches:
        return _blocked('no_verified_public_form_adapter', status='unsupported')
    if len(matches) != 1:
        return _blocked('ambiguous_adapter_identity')
    adapter = matches[0]
    if not _verification_valid(adapter):
        return _blocked('adapter_verification_incomplete')
    if analysis.get('is_relevant', analysis.get('eligible')) is not True or analysis.get('review_required') or analysis.get('email_safe') is not True:
        return _blocked('job_not_cleared_for_automatic_application')
    now = _utcnow()
    published = next((_iso(post.get(key)) for key in ('first_published', 'date_posted', 'posted_at') if _iso(post.get(key))), None)
    if not published or not -300 <= (now - published).total_seconds() <= 48 * 3600:
        return _blocked('original_publication_outside_48_hours_or_unknown')
    expiry = _iso(post.get('valid_through') or post.get('application_deadline'))
    if expiry and expiry <= now:
        return _blocked('vacancy_closed')
    bank = load_application_answers() if answers is None else answers
    if not isinstance(bank, dict) or bank.get('verified') is not True or not _iso(bank.get('verified_at')):
        return _blocked('verified_answer_bank_missing')
    values = bank.get('values') or {}
    consents = bank.get('consents') or {}
    if not isinstance(values, dict) or not isinstance(consents, dict):
        return _blocked('invalid_answer_bank')
    path = Path(resume_path or RESUME_PATH)
    try:
        resume = path.read_bytes()
        if not resume.startswith(b'%PDF-') or not 5 <= len(resume) <= MAX_RESUME_BYTES:
            return _blocked('resume_not_supported_pdf')
    except OSError:
        return _blocked('resume_unavailable')
    try:
        form = _fetch_form(url, adapter, request_fn)
    except Exception as exc:
        return _blocked(str(exc) if isinstance(exc, ValueError) else 'form_read_failed')
    fields = adapter.get('fields') or {}
    tokens = set(adapter.get('csrf_fields') or [])
    fixed = adapter.get('fixed_fields') or {}
    data = {}
    files = []
    controls = [item for item in form['controls'] if item['type'] not in ('submit', 'button', 'reset') and 'disabled' not in item]
    names = [item.get('name') for item in controls]
    if None in names or '' in names or len(names) != len(set(names)):
        return _blocked('unnamed_or_duplicate_form_controls')
    for control in controls:
        name = control['name']
        kind = control['type']
        if FORBIDDEN_KEYS.search(name) or kind == 'password' or 'multiple' in control:
            return _blocked('unsupported_sensitive_or_multiple_field')
        if name in tokens:
            if kind != 'hidden' or not control.get('value') or len(control['value']) > 2048:
                return _blocked('csrf_contract_changed')
            data[name] = control['value']
            continue
        if name in fixed:
            if str(control.get('value', '')) != str(fixed[name]):
                return _blocked('fixed_form_field_changed')
            data[name] = str(fixed[name])
            continue
        spec = fields.get(name)
        if not isinstance(spec, dict):
            return _blocked('unmapped_form_field')
        if spec.get('resume') is True:
            if kind != 'file' or files:
                return _blocked('unsupported_file_field')
            files.append(name)
            continue
        if kind == 'file' or kind == 'hidden':
            return _blocked('unapproved_file_or_hidden_field')
        if kind == 'checkbox':
            key = spec.get('consent')
            if not key or consents.get(key) is not True:
                if 'required' in control:
                    return _blocked('required_consent_not_approved')
                continue
            data[name] = control.get('value', 'on')
            continue
        if kind == 'radio':
            return _blocked('radio_group_requires_specialised_adapter')
        key = spec.get('answer')
        if not key or FORBIDDEN_KEYS.search(key):
            return _blocked('unapproved_answer_binding')
        answer = values.get(key)
        if answer is None or answer == '':
            if 'required' in control:
                return _blocked('required_answer_missing', missing_answer=key)
            continue
        if isinstance(answer, (dict, list, bool)) or len(str(answer)) > 4000:
            return _blocked('invalid_answer_value')
        answer = str(answer)
        if kind == 'select' and answer not in control['options']:
            return _blocked('answer_not_in_verified_options', missing_answer=key)
        if kind == 'email' and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', answer):
            return _blocked('invalid_email_answer')
        if kind == 'url' and not safe_url(answer):
            return _blocked('invalid_url_answer')
        if not _valid_control_answer(control, answer):
            return _blocked('answer_does_not_match_form_constraints')
        data[name] = answer
    if len(files) != 1:
        return _blocked('single_resume_upload_required')
    if not adapter.get('confirmation_pattern') or not adapter.get('application_id_pattern'):
        return _blocked('confirmation_contract_missing')
    return {'status': 'ready', 'reason': 'verified_public_form_prepared',
            'adapter_id': adapter['id'], 'application_url': url,
            'job_id': analysis.get('job_id', ''), 'prepared_at': now.isoformat(),
            '_adapter': adapter, '_data': data, '_file_field': files[0],
            '_resume_path': str(path), '_resume_sha256': sha256(resume).hexdigest()}


def submit_application(plan, request_fn):
    """Submit once; any uncertain response is ambiguous and MUST NOT be retried."""
    if plan.get('status') != 'ready' or plan.get('_consumed'):
        return _blocked('application_plan_not_ready_or_already_consumed')
    prepared = _iso(plan.get('prepared_at'))
    if not prepared or not 0 <= (_utcnow() - prepared).total_seconds() <= PLAN_TTL_SECONDS:
        return _blocked('application_plan_expired')
    adapter = plan.get('_adapter') or {}
    if not _verification_valid(adapter) or not _url_allowed(adapter.get('action_url', ''), adapter):
        return _blocked('adapter_verification_incomplete')
    try:
        resume = Path(plan['_resume_path']).read_bytes()
        if sha256(resume).hexdigest() != plan['_resume_sha256']:
            return _blocked('resume_changed_since_preparation')
    except (OSError, KeyError):
        return _blocked('resume_unavailable')
    # Mark before crossing the network boundary. Even timeout may mean accepted.
    plan['_consumed'] = True
    try:
        response = request_fn('POST', adapter['action_url'], data=plan['_data'],
                              files={plan['_file_field']: ('Shivank_Shukla_Data_Analyst.pdf', resume, 'application/pdf')},
                              timeout=30, allow_redirects=False)
        confirmation_url = adapter['action_url']
        if response.status_code in (301, 302, 303, 307, 308):
            target = urljoin(adapter['action_url'], response.headers.get('Location', ''))
            if response.status_code not in (302, 303) or not _url_allowed(target, adapter) or not _matches(adapter.get('confirmation_url_pattern'), target):
                return _blocked('unexpected_submission_redirect', status='ambiguous')
            response = request_fn('GET', target, timeout=20, allow_redirects=False)
            confirmation_url = target
        content = response.text
        if len(content.encode('utf-8')) > MAX_FORM_BYTES:
            return _blocked('submission_response_too_large', status='ambiguous')
        if response.status_code in (401, 403, 429) or CHALLENGE.search(content):
            return _blocked('submission_access_or_challenge_blocked')
        if any(re.search(pattern, content, re.I) for pattern in adapter.get('rejection_patterns', [])):
            return _blocked('explicit_application_rejection', status='rejected')
        if response.status_code in (400, 413, 422):
            return _blocked('application_validation_rejected', status='rejected')
        match = re.search(adapter.get('application_id_pattern', r'(?!)'), content)
        if (response.status_code in (200, 201) and re.search(adapter.get('confirmation_pattern', r'(?!)'), content, re.I)
                and match and match.lastindex and re.fullmatch(r'[A-Za-z0-9._-]{3,128}', match.group(1))):
            return {'status': 'confirmed', 'reason': 'explicit_application_confirmation',
                    'adapter_id': adapter['id'], 'application_id': match.group(1),
                    'confirmation_url': confirmation_url, 'evidence_kind': 'confirmation_with_reference'}
        return _blocked('no_explicit_application_confirmation', status='ambiguous')
    except Exception:
        # Do not return exception strings: transport errors can contain private
        # request payloads, and no response does not establish non-submission.
        return _blocked('submission_transport_uncertain', status='ambiguous')
