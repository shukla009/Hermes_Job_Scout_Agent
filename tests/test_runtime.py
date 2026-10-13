from types import SimpleNamespace

import pytest

from scout_runtime import read_state, reconcile_pending
from scout_state import StateCorruptError


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
