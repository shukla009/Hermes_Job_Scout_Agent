"""Discovery recall regressions: admission is never application eligibility."""
from datetime import datetime, timezone
import scout_sources as s

NOW = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)


class Response:
    def __init__(self, value=None, text=''):
        self.value = value
        self.text = text

    def json(self):
        return self.value


def test_aligned_title_aliases_are_only_discovery_candidates():
    for title in ('Business Operations Associate', 'Process Associate', 'Junior Data Associate'):
        assert s.suitable(title)
    for title in ('Senior Operations Associate', 'Sales Executive', 'Customer Support Associate'):
        assert not s.suitable(title)


def test_lever_refreshes_head_during_backlog_and_deduplicates():
    calls = []
    def get(url, **kwargs):
        offset = kwargs['params']['skip']
        calls.append(offset)
        ids = ['new', 'shared'] if offset == 0 else ['shared', 'old']
        return Response([{'id': ident, 'text': 'Research Analyst',
                          'categories': {'location': 'Noida'},
                          'hostedUrl': 'https://example.com/jobs/' + ident} for ident in ids])
    jobs, stats, cursor = s._fetch({'name': 'A', 'ats': 'lever', 'slug': 'a'}, get, NOW, cursor=100)
    assert calls == [0, 100]
    assert {job['source_id'] for job in jobs} == {'new', 'shared', 'old'}
    assert len(jobs) == 3 and stats['head_refreshed'] and cursor == 0


def test_smartrecruiters_refreshes_head_with_bounded_details():
    offsets = []
    details = []
    def get(url, **kwargs):
        if 'params' in kwargs:
            offset = kwargs['params']['offset']
            offsets.append(offset)
            return Response({'totalFound': 250, 'content': [
                {'id': str(offset), 'name': 'Research Analyst', 'location': {'city': 'Noida'},
                 'ref': 'https://api.smartrecruiters.com/jobs/' + str(offset)}]})
        details.append(url)
        return Response({'applyUrl': 'https://example.com/jobs/1', 'jobAd': {'sections': {}}})
    _, stats, _ = s._fetch({'name': 'A', 'ats': 'smartrecruiters', 'slug': 'a', 'detail_limit': 1}, get, NOW, cursor=100)
    assert offsets == [0, 100] and len(details) == 1
    assert stats['detail_truncated'] and stats['head_refreshed']


def test_backlog_timeout_does_not_discard_successful_head():
    def get(url, **kwargs):
        if kwargs['params']['skip']:
            raise TimeoutError()
        return Response([{'id': 'new', 'text': 'Research Analyst',
                          'categories': {'location': 'Noida'}, 'hostedUrl': 'https://example.com/jobs/new'}])
    jobs, stats, cursor = s._fetch({'name': 'A', 'ats': 'lever', 'slug': 'a'}, get, NOW, cursor=100)
    assert jobs[0]['source_id'] == 'new' and stats['backlog_errors'] == 1
    assert stats['incomplete'] and cursor == 100


def test_detail_priority_does_not_turn_relative_age_into_date():
    old = {'title': 'Research Analyst', 'first_published': '2026-01-01'}
    fresh = {'title': 'Research Analyst', 'first_published': '2026-10-23T09:00:00Z'}
    hint = {'title': 'Research Analyst', 'postedOn': 'Posted Today'}
    assert sorted([old, fresh], key=lambda card: s._card_priority(card, NOW))[0] == fresh
    assert s._card_priority(hint, NOW) < s._card_priority(old, NOW)
    assert not hint.get('date_posted') and not hint.get('first_published')


def test_plain_html_original_date_label_only():
    def html(label):
        return ('<h1>Research Analyst</h1><p>Location: Noida</p><p>' + label + '</p><p>'
                + 'Market research Excel insights. ' * 15 + '</p><a href="/jobs/1/apply">Apply now</a>')
    good = s._jsonld(html('Date posted: 2026-10-23'), 'https://example.com/jobs/1', {'name': 'A'}, NOW)[0][0]
    assert good['date_posted'] == '2026-10-23'
    assert good['date_provenance'] == 'official_html_original_date_label'
    for label in ('Updated: 2026-10-23', 'Posted on: today', 'Copyright 2026-10-23', 'Date posted: 2026-99-99'):
        job = s._jsonld(html(label), 'https://example.com/jobs/1', {'name': 'A'}, NOW)[0][0]
        assert not job['date_posted']


def test_html_head_and_backlog_share_five_detail_slots():
    calls = []
    def get(url, **kwargs):
        calls.append(url)
        if url.endswith('/careers'):
            return Response(text=''.join(f'<a href="/jobs/{i}">Research Analyst</a>' for i in range(20)))
        return Response(text='<h1>Unknown</h1>')
    _, stats, cursor = s._fetch({'name': 'A', 'ats': 'html', 'careers_url': 'https://example.com/careers'}, get, NOW, cursor=10)
    assert calls[1:] == ['https://example.com/jobs/' + str(i) for i in (0, 1, 2, 10, 11)]
    assert cursor == 12 and stats['incomplete']


def test_source_priority_rewards_qualified_yield_not_listing_volume():
    # Exclude the rotating explorer from the ranking assertion.
    registry = [{'name': n, 'ats': 'html'} for n in ('Many', 'Good', 'Explorer')]
    registry[NOW.toordinal() % 3], registry[2] = registry[2], registry[NOW.toordinal() % 3]
    explorer = registry[NOW.toordinal() % 3]['name']
    jobs = {str(i): {'first_seen': NOW.isoformat(), 'source_name': 'html:Many', 'emailed': i == 0} for i in range(20)}
    jobs['good'] = {'first_seen': NOW.isoformat(), 'source_name': 'html:Good', 'email_safe': True}
    ranked = s.prioritize_registry(registry, jobs, NOW)
    assert ranked[0]['name'] == explorer
    remaining = [e['name'] for e in ranked if e['name'] != explorer]
    if 'Good' in remaining and 'Many' in remaining:
        assert remaining.index('Good') < remaining.index('Many')
