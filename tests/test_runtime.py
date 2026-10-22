from types import SimpleNamespace

import pytest

from scout_runtime import read_state, reconcile_pending
from scout_state import StateCorruptError
from datetime import datetime
from scout_state import IST
from scout_runtime import email_window, repository_mode


def test_corrupt_state_fails_closed(tmp_path):
    path = tmp_path / 'state.json'
    path.write_text('{broken', encoding='utf-8')
    with pytest.raises(StateCorruptError):
        read_state(path, dict, {})


def test_pending_reconciliation_is_bounded(monkeypatch):
    calls = []
    monkeypatch.setattr('scout_mail.sent_status', lambda agent, mid: calls.append(mid) or None)
    store = SimpleNamespace(data={'sends': {
        str(i): {'id':str(i), 'message_id':f'<{i}@scout>', 'status':'pending'}
        for i in range(8)}}, finish_send=lambda *args: True)
    assert reconcile_pending(SimpleNamespace(), store) is False
    assert len(calls) == 5


def test_missing_sent_evidence_quarantines(monkeypatch):
    statuses = []
    monkeypatch.setattr('scout_mail.sent_status', lambda agent, mid: False)
    store = SimpleNamespace(data={'sends': {'a': {
        'id':'a', 'message_id':'<a@scout>', 'status':'pending'}}},
        finish_send=lambda ident, status: statuses.append(status) or True)
    assert reconcile_pending(SimpleNamespace(), store)
    assert statuses == ['uncertain']


@pytest.mark.parametrize('day,hour,minute,allowed', [(19,8,29,False),(19,8,30,True),(19,18,29,True),(19,18,30,False),(18,12,0,False)])
def test_ist_business_window(day,hour,minute,allowed):
    assert email_window(datetime(2026,10,day,hour,minute,tzinfo=IST)) is allowed


def test_unknown_visibility_keeps_conservative_free_guard():
    def fail(*args):
        raise OSError('not available')
    assert repository_mode(SimpleNamespace(_call=fail)) == 'private_allowance'
    assert repository_mode(SimpleNamespace(_call=lambda *args:{'private':False})) == 'standard_public'


def test_discovery_diagnostics_count_partial_resolver_failures_without_urls():
    from scout_runtime import discovery_health, _discovery_summary
    stats = {'healthy': {'status': 'ok'}, 'bad': {'status': 'error'},
             'gmail_alert_label': {'status': 'ok', 'errors': 2,
                                  'failure_categories': {'timeout': 1, 'http_access_denied': 1,
                                                         'https://tracker.example/private?token=secret': 3}}}
    health = discovery_health(stats)
    assert health['failing_sources'] == 2
    assert health['alert_failure_categories'] == {'timeout': 1, 'http_access_denied': 1, 'unexpected_error': 3}
    summary = _discovery_summary(health)
    assert 'http_access_denied: 1' in summary
    assert 'tracker' not in summary and 'secret' not in summary


def test_expected_alert_exclusions_are_diagnostics_not_source_outages():
    from scout_runtime import discovery_health
    health = discovery_health({'gmail_alert_label': {'status': 'ok', 'errors': 0,
                                                    'failure_categories': {'excluded_platform': 3}}})
    assert health == {'failing_sources': 0, 'alert_failure_categories': {'excluded_platform': 3}}
