"""Fixture-only authenticated state encryption and Python/Worker compatibility."""
import base64
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from scout_persistence import GitHubWriter
from scout_runtime import read_state
from scout_state import StateCorruptError, StateStore
import scout_storage as storage


@pytest.fixture(autouse=True)
def fixture_key(monkeypatch):
    monkeypatch.setenv('SCOUT_STATE_KEY', 'fixture-only-state-key-do-not-use')
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'fixture-only-bot-token')


def test_encryption_roundtrip_nonce_and_context():
    fixture = {'version': 1, 'name': 'विनय', 'email': 'fixture@example.org', 'list': [1, True]}
    encrypted = storage.encode_state(fixture, 'state/scout.json')
    again = storage.encode_state(fixture, 'state/scout.json')
    assert encrypted['format'] == storage.FORMAT
    assert encrypted['nonce'] != again['nonce']
    assert 'fixture@example.org' not in json.dumps(encrypted)
    assert storage.decode_state(encrypted, 'state/scout.json') == fixture
    with pytest.raises(ValueError):
        storage.decode_state(encrypted, 'state/sources.json')


def test_ciphertext_and_key_tampering_rejected(monkeypatch):
    encrypted = storage.encode_state({'fixture': 'private'}, 'state/scout.json')
    altered = deepcopy(encrypted)
    raw = bytearray(base64.b64decode(altered['ciphertext']))
    raw[0] ^= 1
    altered['ciphertext'] = base64.b64encode(raw).decode()
    with pytest.raises(ValueError):
        storage.decode_state(altered, 'state/scout.json')
    monkeypatch.setenv('SCOUT_STATE_KEY', 'a-different-fixture-key')
    with pytest.raises(ValueError):
        storage.decode_state(encrypted, 'state/scout.json')


def test_missing_key_does_not_encrypt_plaintext(monkeypatch):
    monkeypatch.delenv('SCOUT_STATE_KEY')
    monkeypatch.delenv('TELEGRAM_BOT_TOKEN')
    with pytest.raises(ValueError):
        storage.encode_state({'fixture': 'private'}, 'state/scout.json')


def test_plaintext_rolling_migration_still_readable():
    fixture = {'version': 1, 'jobs': {}, 'sends': {}}
    assert storage.decode_state(fixture, 'state/scout.json') is fixture


def _worker_crypto(payload):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is unavailable for Worker compatibility check')
    worker = Path(__file__).parents[1] / 'workers' / 'telegram_trigger_worker.js'
    code = r'''
import {readFileSync} from 'node:fs';
import {webcrypto} from 'node:crypto';
if (!globalThis.crypto) Object.defineProperty(globalThis, 'crypto', {value:webcrypto});
const input=JSON.parse(readFileSync(0,'utf8'));
const source=readFileSync(input.worker,'utf8');
const {encodeState,decodeState}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
const env={SCOUT_STATE_KEY:'fixture-only-state-key-do-not-use',TELEGRAM_BOT_TOKEN:'fixture-only-bot-token'};
const decoded=await decodeState(input.encrypted,env,input.context);
const encrypted=await encodeState(input.value,env,input.context);
process.stdout.write(JSON.stringify({decoded,encrypted}));
'''
    result = subprocess.run([node, '--input-type=module', '-e', code],
                            input=json.dumps(dict(payload, worker=str(worker))),
                            text=True, encoding='utf-8', capture_output=True, check=True, timeout=20)
    return json.loads(result.stdout)


def test_worker_python_bidirectional_encryption():
    fixture = {'version': 1, 'jobs': {'fixture': {'company': 'संस्था'}}, 'sends': {}}
    context = 'state/scout.json'
    encrypted = storage.encode_state(fixture, context)
    result = _worker_crypto({'encrypted': encrypted, 'context': context, 'value': fixture})
    assert result['decoded'] == fixture
    assert storage.decode_state(result['encrypted'], context) == fixture


def test_worker_feedback_context_is_compatible():
    fixture = {'update_id': 123, 'job_id': 'a' * 24, 'action': 'reply', 'sender_id': 'fixture'}
    context = 'state/feedback/123.json'
    result = _worker_crypto({'encrypted': storage.encode_state(fixture, context),
                             'context': context, 'value': fixture})
    assert result['decoded'] == fixture
    assert storage.decode_state(result['encrypted'], context) == fixture
    with pytest.raises(ValueError):
        storage.decode_state(result['encrypted'], 'state/feedback/124.json')


def test_persistence_encrypts_public_repository_blob(monkeypatch, tmp_path):
    monkeypatch.setenv('SCOUT_ENCRYPT_STATE', 'true')
    path = tmp_path / 'state' / 'scout.json'
    path.parent.mkdir()
    fixture = {'version': 1, 'jobs': {}, 'sends': {}, 'fixture': 'private-fixture-marker'}
    path.write_text(json.dumps(fixture), encoding='utf-8')
    writer = GitHubWriter(token='fixture-github-token', repo='fixture/repo',
                          root=tmp_path, paths=['state/scout.json'])
    blobs = []

    def api(method, endpoint, body=None):
        if method == 'GET' and endpoint.startswith('/git/ref/'):
            return {'object': {'sha': 'head'}}
        if method == 'GET' and endpoint == '/git/commits/head':
            return {'tree': {'sha': 'tree'}}
        if endpoint == '/git/blobs':
            blobs.append(base64.b64decode(body['content']))
        return {'sha': 'fixture-sha'}

    writer._call = api
    assert writer()
    assert len(blobs) == 1
    assert b'private-fixture-marker' not in blobs[0]
    assert storage.decode_state(json.loads(blobs[0]), 'state/scout.json') == fixture


def test_corrupt_encrypted_read_state_becomes_state_safety_stop(tmp_path, monkeypatch):
    path = tmp_path / 'state.json'
    encrypted = storage.encode_state({'version': 1}, path.as_posix())
    path.write_text(json.dumps(encrypted), encoding='utf-8')
    monkeypatch.setenv('SCOUT_STATE_KEY', 'incorrect-fixture-key')
    with pytest.raises(StateCorruptError):
        read_state(path, dict, {})


def test_corrupt_encrypted_store_becomes_state_safety_stop(tmp_path, monkeypatch):
    path = tmp_path / 'state.json'
    value = {'version': 1, 'jobs': {}, 'sends': {}, 'alerts': {}, 'processed_feedback': []}
    path.write_text(json.dumps(storage.encode_state(value, path.as_posix())), encoding='utf-8')
    monkeypatch.setenv('SCOUT_STATE_KEY', 'incorrect-fixture-key')
    with pytest.raises(StateCorruptError):
        StateStore(path, history_path=tmp_path / 'missing-history.json')
