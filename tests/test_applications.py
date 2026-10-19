"""Public form contracts use local fixtures only; never upload to an employer."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import scout_applications as applications

NOW = datetime(2026, 10, 19, 9, tzinfo=timezone.utc)
URL = 'https://careers.example.org/jobs/analyst-17/apply'
ACTION = 'https://careers.example.org/applications'
HTML = '''<form id="job-application" action="/applications" method="post" enctype="multipart/form-data">
<input type="hidden" name="csrf" value="rotating-token">
<input name="full_name" required><input type="email" name="email" required>
<input type="file" name="resume" accept="application/pdf" required>
<input type="checkbox" name="privacy" value="accepted" required>
<input type="checkbox" name="marketing" value="yes">
<input type="submit" value="Apply"></form>'''


def response(content, code=200, *, url=None, headers=None):
    value = SimpleNamespace(text=content, status_code=code, headers=headers or {})
    if url is not None:
        value.url = url
    return value


@pytest.fixture
def fixture(monkeypatch, tmp_path):
    monkeypatch.setattr(applications, '_utcnow', lambda: NOW)
    resume = tmp_path / 'resume.pdf'
    resume.write_bytes(b'%PDF-1.7\nfixture resume')
    adapter = {
        'id': 'fixture-only', 'company': 'Fixture Employer', 'enabled': True,
        'allowed_hosts': ['careers.example.org'],
        'page_url_pattern': r'https://careers\.example\.org/jobs/analyst-17/apply',
        'form_id': 'job-application', 'action_url': ACTION,
        'contract_sha256': applications.contract_fingerprint(HTML, URL, 'job-application'),
        'csrf_fields': ['csrf'],
        'fields': {'full_name': {'answer': 'full_name'}, 'email': {'answer': 'email'},
                   'resume': {'resume': True}, 'privacy': {'consent': 'application_privacy'},
                   'marketing': {'consent': 'marketing_opt_in'}},
        'confirmation_pattern': r'Application received',
        'application_id_pattern': r'Application reference: ([A-Z0-9-]+)',
        'confirmation_url_pattern': r'https://careers\.example\.org/confirmed/[A-Z0-9-]+',
        'rejection_patterns': ['application could not be accepted'],
        'verification': {'public_form': True, 'no_authentication': True,
                         'no_challenge': True, 'confirmation_verified': True,
                         'checked_at': NOW.isoformat(), 'evidence_url': URL},
    }
    post = {'company': 'Fixture Employer', 'application_url': URL,
            'date_posted': (NOW - timedelta(hours=2)).isoformat()}
    analysis = {'is_relevant': True, 'email_safe': True, 'company': 'Fixture Employer',
                'application_url': URL, 'job_id': 'job-17'}
    answers = {'verified': True, 'verified_at': NOW.isoformat(),
               'values': {'full_name': 'Example Candidate', 'email': 'candidate@example.org'},
               'consents': {'application_privacy': True}}
    return post, analysis, adapter, answers, resume


def prepare(fixture, *, html=HTML, **overrides):
    post, analysis, adapter, answers, resume = fixture
    calls = []

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return response(html, url=url)

    result = applications.prepare_application(post, analysis, request,
                                              answers=answers, adapters=[adapter],
                                              resume_path=resume, **overrides)
    return result, calls


def test_prepare_is_get_only_and_suppresses_unapproved_marketing(fixture):
    plan, calls = prepare(fixture)
    assert plan['status'] == 'ready'
    assert len(calls) == 1 and calls[0][0] == 'GET'
    assert calls[0][2]['allow_redirects'] is False
    assert 'marketing' not in plan['_data']
    assert plan['_data']['privacy'] == 'accepted'
    assert plan['_data']['csrf'] == 'rotating-token'


def test_submit_confirms_only_reference_and_never_returns_private_payload(fixture):
    plan, _ = prepare(fixture)
    calls = []

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return response('Application received. Application reference: APP-123')

    outcome = applications.submit_application(plan, request)
    assert outcome['status'] == 'confirmed'
    assert outcome['application_id'] == 'APP-123'
    assert len(calls) == 1 and calls[0][0] == 'POST'
    assert calls[0][2]['allow_redirects'] is False
    assert calls[0][2]['files']['resume'][1].startswith(b'%PDF-')
    assert 'candidate@example.org' not in str(outcome)
    assert '_data' not in outcome
    assert applications.submit_application(plan, request)['status'] == 'blocked'
    assert len(calls) == 1


@pytest.mark.parametrize('html', [
    HTML.replace('name="email" required', 'name="email" required maxlength="2"'),
    HTML.replace('action="/applications"', 'action="https://evil.example.org/collect"'),
    HTML.replace('name="full_name" required', 'name="full_name" required><input name="salary" required'),
    HTML + '<script src="https://www.google.com/recaptcha/api.js"></script>',
    HTML.replace('type="email"', 'type="password"'),
])
def test_changed_contract_and_challenge_block_before_post(fixture, html):
    result, calls = prepare(fixture, html=html)
    assert result['status'] == 'blocked'
    assert all(method == 'GET' for method, _, _ in calls)


@pytest.mark.parametrize('change,reason', [
    ('unknown_answer', 'required_answer_missing'),
    ('unapproved_consent', 'required_consent_not_approved'),
    ('unverified_bank', 'verified_answer_bank_missing'),
    ('stale', 'original_publication_outside_48_hours_or_unknown'),
    ('undated', 'original_publication_outside_48_hours_or_unknown'),
    ('expired', 'vacancy_closed'),
    ('review', 'job_not_cleared_for_automatic_application'),
])
def test_ineligible_or_unverified_inputs_cannot_submit(fixture, change, reason):
    post, analysis, _, answers, _ = fixture
    if change == 'unknown_answer':
        answers['values'].pop('email')
    elif change == 'unapproved_consent':
        answers['consents'] = {}
    elif change == 'unverified_bank':
        answers['verified'] = False
    elif change == 'stale':
        post['date_posted'] = (NOW - timedelta(hours=48, seconds=1)).isoformat()
    elif change == 'undated':
        post.pop('date_posted')
    elif change == 'expired':
        post['valid_through'] = (NOW - timedelta(seconds=1)).isoformat()
    elif change == 'review':
        analysis['review_required'] = True
    result, calls = prepare(fixture)
    assert result['status'] == 'blocked' and result['reason'] == reason
    assert all(method == 'GET' for method, _, _ in calls)


def test_no_adapter_and_disabled_registry_never_make_requests(fixture):
    fixture[2]['enabled'] = False
    result, calls = prepare(fixture)
    assert result['status'] == 'unsupported' and not calls
    readiness = applications.adapter_readiness()
    assert readiness['enabled'] == 0
    assert readiness['audited'] >= 4


def test_resume_changed_between_prepare_and_submit_blocks(fixture):
    plan, _ = prepare(fixture)
    fixture[4].write_bytes(b'%PDF-1.7 changed document')
    assert applications.submit_application(plan, lambda *a, **k: pytest.fail('must not request'))['reason'] == 'resume_changed_since_preparation'


def test_expired_plan_never_posts(fixture):
    plan, _ = prepare(fixture)
    plan['prepared_at'] = (NOW - timedelta(seconds=301)).isoformat()
    assert applications.submit_application(plan, lambda *a, **k: pytest.fail('must not request'))['reason'] == 'application_plan_expired'


@pytest.mark.parametrize('content,status,outcome', [
    ('Application received', 200, 'ambiguous'),
    ('Application received. Application reference: APP-123', 500, 'ambiguous'),
    ('Application could not be accepted', 200, 'rejected'),
    ('Invalid candidate email', 422, 'rejected'),
    ('Too many requests', 429, 'blocked'),
    ('<div class="cf-turnstile">', 200, 'blocked'),
])
def test_submission_outcomes_are_not_assumed_success(fixture, content, status, outcome):
    plan, _ = prepare(fixture)
    result = applications.submit_application(plan, lambda *a, **k: response(content, status))
    assert result['status'] == outcome


def test_timeout_is_ambiguous_consumed_and_never_retried(fixture):
    plan, _ = prepare(fixture)
    calls = []

    def request(*args, **kwargs):
        calls.append(args)
        raise TimeoutError('candidate@example.org must not escape to logs')

    result = applications.submit_application(plan, request)
    assert result['status'] == 'ambiguous'
    assert 'candidate@example.org' not in str(result)
    assert applications.submit_application(plan, request)['status'] == 'blocked'
    assert len(calls) == 1


def test_confirmed_redirect_is_get_only_and_references_required(fixture):
    plan, _ = prepare(fixture)
    calls = []

    def request(method, url, **kwargs):
        calls.append((method, url))
        if method == 'POST':
            return response('', 303, headers={'Location': '/confirmed/APP-123'})
        return response('Application received. Application reference: APP-123')

    result = applications.submit_application(plan, request)
    assert result['status'] == 'confirmed'
    assert [row[0] for row in calls] == ['POST', 'GET']


def test_unexpected_redirect_never_forwards_candidate_data(fixture):
    plan, _ = prepare(fixture)
    calls = []

    def request(method, url, **kwargs):
        calls.append((method, url))
        return response('', 307, headers={'Location': 'https://evil.example.org/collect'})

    assert applications.submit_application(plan, request)['status'] == 'ambiguous'
    assert len(calls) == 1


def test_select_values_and_unmapped_fields_are_fail_closed(fixture):
    html = HTML.replace('<input type="submit"', '<select name="notice" required><option value="30">30 days</option></select><input type="submit"')
    adapter = fixture[2]
    adapter['contract_sha256'] = applications.contract_fingerprint(html, URL, adapter['form_id'])
    result, _ = prepare(fixture, html=html)
    assert result['reason'] == 'unmapped_form_field'
    adapter['fields']['notice'] = {'answer': 'notice_period_days'}
    fixture[3]['values']['notice_period_days'] = 0
    result, _ = prepare(fixture, html=html)
    assert result['reason'] == 'answer_not_in_verified_options'
    fixture[3]['values']['notice_period_days'] = 30
    assert prepare(fixture, html=html)[0]['status'] == 'ready'


def test_unverified_or_wrong_company_adapter_is_not_used(fixture):
    fixture[2]['company'] = 'Other company'
    assert prepare(fixture)[0]['status'] == 'unsupported'
    fixture[2]['company'] = 'Fixture Employer'
    fixture[2]['verification']['confirmation_verified'] = False
    assert prepare(fixture)[0]['reason'] == 'adapter_verification_incomplete'


def test_token_value_rotation_does_not_change_schema_but_is_fresh(fixture):
    result, _ = prepare(fixture, html=HTML.replace('rotating-token', 'new-token'))
    assert result['status'] == 'ready' and result['_data']['csrf'] == 'new-token'


def test_plan_uses_first_publication_not_recent_update(fixture):
    fixture[0]['first_published'] = (NOW - timedelta(days=10)).isoformat()
    fixture[0]['updated_at'] = NOW.isoformat()
    assert prepare(fixture)[0]['reason'] == 'original_publication_outside_48_hours_or_unknown'


def test_load_answers_never_trusts_unverified_json(monkeypatch):
    monkeypatch.setenv('SCOUT_APPLICATION_ANSWERS', '{"values":{"notice_period_days":0}}')
    assert applications.load_application_answers() == {}
    monkeypatch.setenv('SCOUT_APPLICATION_ANSWERS', 'bad json')
    assert applications.load_application_answers() == {}


def test_contract_fingerprint_rejects_duplicate_form_identity():
    assert applications.contract_fingerprint(HTML + HTML, URL, 'job-application') == ''
