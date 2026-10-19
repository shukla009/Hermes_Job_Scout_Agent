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
