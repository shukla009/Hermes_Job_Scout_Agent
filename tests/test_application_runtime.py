"""Application-route reservations survive uncertain outcomes and restarts."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from types import SimpleNamespace

import pytest
import requests

from scout_applications import submit_application
from scout_persistence import RuntimeBudget
from scout_runtime import reconcile_pending
from scout_state import StateStore


def state(tmp_path, callback=None):
    return StateStore(tmp_path / 'state.json', tmp_path / 'history.json', callback)


def job(store, suffix='1', company='Fixture Employer'):
    return store.upsert_job({'application_url': f'https://careers.example.org/jobs/{suffix}',
                              'company': company, 'job_title': 'Research Analyst'})['id']


@pytest.mark.parametrize('status', ['pending', 'ambiguous', 'confirmed'])
def test_durable_form_reservation_prevents_second_route_after_restart(tmp_path, status):
    store = state(tmp_path)
    ident = job(store)
    intent = store.reserve_application(ident, 'fixture-adapter')
    assert intent
    if status != 'pending':
        assert store.finish_application(intent['id'], {'status': status,
            'reason': 'fixture result', 'application_id': 'FIXTURE-123'})
    restarted = state(tmp_path)
    assert restarted.reserve_application(ident, 'fixture-adapter') is None
    assert restarted.reserve_send(ident, 'recruiter@example.org') is None
    assert restarted.application_reserved(ident)


@pytest.mark.parametrize('status', ['pending', 'uncertain', 'accepted'])
def test_email_reservation_prevents_duplicate_form_after_restart(tmp_path, status):
    store = state(tmp_path)
    ident = job(store)
    intent = store.reserve_send(ident, 'recruiter@example.org')
    assert intent
    if status != 'pending':
        assert store.finish_send(intent['id'], status)
    restarted = state(tmp_path)
    assert restarted.reserve_application(ident, 'fixture-adapter') is None


def test_unpersisted_form_reservation_cannot_authorise_submission(tmp_path):
    store = state(tmp_path, callback=lambda _: False)
    ident = job(store)
    assert store.reserve_application(ident, 'fixture-adapter') is None


def test_uncertain_initial_email_blocks_same_employer_other_vacancy(tmp_path):
    store = state(tmp_path)
    first, second = job(store, '1'), job(store, '2')
    intent = store.reserve_send(first, 'recruiter@example.org')
    assert store.finish_send(intent['id'], 'uncertain')
    assert store.reserve_send(second, 'other-recruiter@example.org') is None
    assert store.reserve_application(second, 'fixture-adapter') is None


def test_bounce_suppression_survives_restart_and_new_jobs(tmp_path):
    store = state(tmp_path)
    first = job(store)
    store.data.setdefault('suppressed_recipients', {})['bounce@example.org'] = {
        'reason': 'bounce', 'detected_at': datetime.now(timezone.utc).isoformat()}
    assert store.save()
    restarted = state(tmp_path)
    assert restarted.reserve_send(first, 'BOUNCE@example.org') is None
    other = job(restarted, 'other', company='Other Fixture Employer')
    assert restarted.reserve_send(other, 'bounce@example.org') is None


def test_stale_company_cooldown_does_not_block_new_unique_job(tmp_path):
    store = state(tmp_path)
    first, second = job(store, '1'), job(store, '2')
    intent = store.reserve_send(first, 'recruiter@example.org')
    assert store.finish_send(intent['id'], 'accepted')
    past = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    store.data['sends'][intent['id']]['created_at'] = past
    store.data['jobs'][first]['emailed_at'] = past
    assert store.reserve_application(second, 'fixture-adapter')


def test_pending_form_reconciliation_holds_without_reposting(tmp_path):
    store = state(tmp_path)
    ident = job(store)
    intent = store.reserve_application(ident, 'fixture-adapter')
    assert reconcile_pending(SimpleNamespace(), store)
    assert store.data['applications'][intent['id']]['status'] == 'ambiguous'
    assert store.reserve_send(ident, 'recruiter@example.org') is None


def test_form_result_cannot_persist_private_request_fields(tmp_path):
    store = state(tmp_path)
    ident = job(store)
    intent = store.reserve_application(ident, 'fixture-adapter')
    assert store.finish_application(intent['id'], {
        'status': 'confirmed', 'application_id': 'FIXTURE-123',
        'reason': 'confirmed response', 'evidence_kind': 'confirmation_with_reference',
        '_data': {'salary': 'PRIVATE-FIXTURE-SALARY'},
        'resume_bytes': b'PRIVATE-FIXTURE-CV', 'full_response': 'PRIVATE-FIXTURE-BODY'})
    saved = store.path.read_text(encoding='utf-8')
    assert 'PRIVATE-FIXTURE' not in saved
    assert store.data['jobs'][ident]['submitted'] is True


@pytest.mark.parametrize('http_status,expected', [(422, 'rejected'), (403, 'blocked'),
                                                  (429, 'blocked'), (500, 'ambiguous')])
def test_runtime_request_wrapper_preserves_form_error_status_without_retry(tmp_path, http_status, expected):
    action = 'https://careers.example.org/applications'
    resume = tmp_path / 'resume.pdf'
    pdf = b'%PDF-1.7 fixture'
    resume.write_bytes(pdf)
    adapter = {'id': 'fixture', 'allowed_hosts': ['careers.example.org'], 'action_url': action,
               'confirmation_pattern': 'Application received',
               'application_id_pattern': r'Application reference: ([A-Z0-9-]+)',
               'verification': {'public_form': True, 'no_authentication': True,
                   'no_challenge': True, 'confirmation_verified': True,
                   'checked_at': datetime.now(timezone.utc).isoformat(), 'evidence_url': action}}
    plan = {'status': 'ready', 'prepared_at': datetime.now(timezone.utc).isoformat(),
            '_adapter': adapter, '_resume_path': str(resume), '_resume_sha256': sha256(pdf).hexdigest(),
            '_data': {'name': 'Fixture Candidate'}, '_file_field': 'resume'}
    calls = []

    def transport(method, url, **kwargs):
        calls.append((method, url))
        response = requests.Response()
        response.url, response.status_code = url, http_status
        response._content, response._content_consumed = b'Fixture response', True
        return response

    budget = RuntimeBudget(dns_checker=lambda _: True)
    budget.transport = transport

    def public_request(method, url, **kwargs):
        return budget.request(url, method=method, return_http_errors=True, max_retries=0, **kwargs)

    assert submit_application(plan, public_request)['status'] == expected
    assert len(calls) == 1
    assert budget.attempts == 1
