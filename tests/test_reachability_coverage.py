"""Offline contact coverage, fairness and route-specific action regressions."""
from datetime import timedelta

from scout_actions import manual_draft
from scout_contact_search import NamedDiscovery, named_evidence
from test_named_contacts import EMPLOYER, NOW, POST, person, response, state


def test_former_employee_on_another_card_does_not_hide_current_recruiter():
    content = (person() + '<section><h3>Ravi Kumar</h3>'
               '<p>Former Research Director ravi@fixture.org</p></section>')
    rows, _ = named_evidence(content, 'https://fixture.org/team', EMPLOYER, {'fixture.org'}, POST, NOW)
    assert [row['email'] for row in rows] == ['asha@fixture.org']
    blocked, reason = named_evidence(content + '<p>No unsolicited email</p>',
        'https://fixture.org/team', EMPLOYER, {'fixture.org'}, POST, NOW)
    assert blocked == [] and reason == 'contact_instructions_prohibit'


def test_shared_general_contact_avoids_repeated_pages_and_counts_once(tmp_path):
    calls = []
    def request(url, **kwargs):
        calls.append(url)
        return response(person() if url.endswith('/careers') else '<p>' + 'Public vacancy information. ' * 8 + '</p>', url=url)
    finder = NamedDiscovery(request, state(tmp_path), registry=[EMPLOYER], now=NOW)
    assert finder.find(dict(POST))
    first_calls = len(calls)
    assert finder.find(dict(POST, href='https://fixture.org/jobs/43'))
    assert len(calls) == first_calls
    assert finder.stats['contacts_discovered'] == 1
    assert finder.stats['hiring_relevance_confirmed'] == 1
    # A new execution explicitly records a cache hit, not a new discovery.
    next_run = NamedDiscovery(request, finder.store, registry=[EMPLOYER], now=NOW + timedelta(hours=1))
    assert next_run.find(dict(POST, href='https://fixture.org/jobs/44'))
    assert next_run.stats['contacts_cached'] == 1
    assert next_run.stats['contacts_discovered'] == 0
    revalidated = NamedDiscovery(request, finder.store, registry=[EMPLOYER], now=NOW + timedelta(hours=25))
    assert revalidated.find(dict(POST))
    assert revalidated.stats['contacts_revalidated'] == 1
    assert revalidated.stats['contacts_new'] == 0


def test_role_specific_manager_not_reused_across_vacancies(tmp_path):
    calls = []
    def request(url, **kwargs):
        calls.append(url)
        return response(person(jobTitle='Hiring Manager for Research Associate REQ42'), url=url)
    finder = NamedDiscovery(request, state(tmp_path), registry=[EMPLOYER], now=NOW)
    assert finder.find(dict(POST))
    count = len(calls)
    assert finder.find(dict(POST, href='https://fixture.org/jobs/other', job_title='Operations Associate', vacancy_reference='OTHER')) is None
    assert len(calls) > count


def test_fair_budget_and_incremental_navigation(tmp_path):
    other = dict(EMPLOYER, name='Other Research', official_domains=['other.org'], careers_url='https://other.org/careers')
    other_post = dict(POST, company='Other Research', href='https://other.org/jobs/42')
    calls = []
    def request(url, **kwargs):
        calls.append(url)
        if url.endswith('/people'):
            return response(person(), url=url)
        return response('<p>' + 'Public recruitment information. ' * 8 + '</p><a href="/team">Team</a><a href="/people">People</a>', url=url)
    store = state(tmp_path)
    finder = NamedDiscovery(request, store, registry=[EMPLOYER, other], now=NOW)
    batch = [(dict(company=EMPLOYER['name'], email_safe=True), dict(POST), ''),
             (dict(company=other['name'], email_safe=True), other_post, '')]
    for _, post, _ in finder.prioritize_candidates(batch):
        finder.find(post)
    assert finder.fetches == 4
    assert finder.employer_fetches == {'fixtureresearch': 2, 'otherresearch': 2}
    assert any(row.get('pending_urls') for row in finder.cache.values())
    # The next run continues saved navigation rather than starting over at a
    # vacancy page that repeatedly consumes the entire per-employer allowance.
    second = NamedDiscovery(request, store, registry=[EMPLOYER], now=NOW + timedelta(hours=1))
    assert second.find(dict(POST))
    assert any(url.endswith('/people') for url in calls)


def test_unvisited_employer_rotates_ahead_next_run(tmp_path):
    store = state(tmp_path)
    finder = NamedDiscovery(lambda *a, **kw: None, store, registry=[EMPLOYER], now=NOW)
    finder.cache['poll:fixtureresearch'] = {'checked_at': NOW.isoformat()}
    batch = [(dict(company='Fixture Research', email_safe=True), {}, ''),
             (dict(company='Unvisited Employer', email_safe=True), {}, '')]
    assert finder.prioritize_candidates(batch)[0][0]['company'] == 'Unvisited Employer'


def test_manual_application_copy_is_not_a_referral_request():
    analysis = {'job_title': 'Research Associate', 'company': 'Fixture Research', 'evidence': ['Research reporting experience']}
    application = manual_draft(analysis)
    assert 'referring' not in application and 'I am applying' in application
    assert 'referring' in manual_draft(analysis, route='referral')


def test_python_optional_card_labels_and_original_based_expiry(monkeypatch):
    import scout_agent
    messages = []
    monkeypatch.setenv('TELEGRAM_TRACKING_READY', 'false')
    monkeypatch.setattr(scout_agent, 'send_telegram', lambda message: messages.append(message) or True)
    analysis = dict(job_id='a' * 24, manual_optional=True, manual_draft='Fixture draft',
                    date_posted='2026-10-23T03:00:00Z', manual_expires_at='2099-01-01T00:00:00Z')
    assert scout_agent._send_apply_alert(analysis)
    assert 'Application draft:' in messages[-1]
    assert 'knows your work' not in messages[-1]
    assert '25 Oct 2026, 08:30 IST' in messages[-1]
    assert '2099' not in messages[-1]
    assert scout_agent._send_apply_alert(dict(analysis, manual_route='referral'))
    assert 'Referral draft:' in messages[-1] and 'knows your work' in messages[-1]
