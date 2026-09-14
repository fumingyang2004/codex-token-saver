import json
from pathlib import Path

import pytest

from codex_token_saver import rtk_spool, rtk_observation, savings
from codex_token_saver.state import Store, write_json


@pytest.fixture
def pending(tmp_path, monkeypatch):
    project = tmp_path / 'project'; project.mkdir()
    codex = tmp_path / 'codex'; codex.mkdir()
    store = Store(tmp_path / 'installed', project, codex)
    write_json(store.state_path, {'enabled': True})
    store.session_id = 'session-A'
    # Keep every test artifact inside pytest's private directory.
    monkeypatch.setattr(rtk_spool.tempfile, 'tempdir', str(tmp_path))
    nonce = rtk_spool.allocate(store, store.session_id)
    rtk_spool.attach(store, nonce)
    return store, nonce, rtk_spool.measurement_store(store)


def complete(spool):
    write_json(spool.directory / 'complete.json', {'session_id': spool.session_id})


def test_host_import_waits_for_completion_isolated_and_idempotent(pending):
    store, nonce, spool = pending
    ledger = savings.SavingsLedger(spool, store.session_id)
    ledger.record_observed('rtk', 'pair-one', 'a' * 80, 'b' * 16, 'fixture pair')
    rtk_spool.ingest(store, store.session_id)
    assert savings.SavingsLedger(store, store.session_id).events() == []
    ledger.record_invocation('rtk', 'call-one', 'completed', metadata={'observed_pair_recorded': True})
    complete(spool)
    rtk_spool.ingest(store, 'another-session')
    assert savings.SavingsLedger(store, 'another-session').events() == []
    rtk_spool.ingest(store, store.session_id)
    rtk_spool.ingest(store, store.session_id)
    summary = savings.SavingsLedger(store, store.session_id).summary()
    assert summary['components']['rtk']['saved'] == 16
    assert summary['components']['rtk']['invocations'] == 1
    assert not spool.directory.exists()
    assert not (store.directory / 'rtk-pending' / (nonce + '.json')).exists()


def test_invalid_identity_or_nonce_cannot_choose_spool(pending):
    store, nonce, spool = pending
    with pytest.raises(ValueError):
        rtk_spool.attach(store, '../escape')
    store.session_id = 'another-session'
    with pytest.raises(ValueError):
        rtk_spool.attach(store, nonce)


def test_sandbox_capture_writes_only_spool_then_host_imports(pending, monkeypatch):
    store, nonce, spool = pending
    plan = {'path': spool.directory / 'rtk-observations' / 'one' / 'pair.jsonl',
            'session_id': store.session_id, 'invocation': 'one', 'binary_sha256': 'fixture'}
    plan['path'].parent.mkdir(parents=True)
    plan['path'].write_text(json.dumps({'protocol': 'ces-rtk-pair-v1', 'pid': 42,
        'invocation_id': 'one', 'before': 'raw ' * 100, 'tracked_after': 'compact', 'source': 'fixture'}) + '\n')
    original = savings.SavingsLedger._append
    def restricted(self, *args, **kwargs):
        if self.store.directory == store.directory:
            raise PermissionError('durable state is outside the command sandbox')
        return original(self, *args, **kwargs)
    with monkeypatch.context() as sandbox:
        sandbox.setattr(savings.SavingsLedger, '_append', restricted)
        rtk_observation.finish(spool, plan, 42, 0,
            {'complete': True, 'data': b'compact\n'}, {'complete': True, 'data': b''})
        rtk_observation.cleanup(plan)
        complete(spool)
    assert not (store.directory / 'savings.sqlite3').exists()
    rtk_spool.ingest(store, store.session_id)
    assert savings.SavingsLedger(store, store.session_id).summary()['components']['rtk']['saved'] == 98


def test_skipped_observation_remains_unknown_with_reason(pending, monkeypatch):
    store, nonce, spool = pending
    def denied(*args):
        raise PermissionError('private path must not be exposed')
    monkeypatch.setattr(rtk_observation, 'prepare', denied)
    assert rtk_observation.execute(store, ['git', 'diff'], 'unused', {}) is None
    rtk_spool.ingest(store, store.session_id)
    component = savings.SavingsLedger(store, store.session_id).summary()['components']['rtk']
    assert component['saved'] is None and component['events'] == 0
    assert component['invocations'] == component['unobserved'] == 1
    assert component['observation_status'] == 'PermissionError'


def test_incomplete_or_foreign_pair_never_becomes_savings(pending):
    store, nonce, spool = pending
    plan = {'path': spool.directory / 'pair.jsonl', 'session_id': store.session_id,
            'invocation': 'expected', 'binary_sha256': 'fixture'}
    plan['path'].write_text(json.dumps({'protocol': 'ces-rtk-pair-v1', 'pid': 9,
        'invocation_id': 'foreign', 'before': 'x' * 400, 'tracked_after': 'x'}) + '\n')
    rtk_observation.finish(spool, plan, 9, 0,
        {'complete': True, 'data': b'x'}, {'complete': True, 'data': b''})
    plan['path'].unlink()
    complete(spool)
    rtk_spool.ingest(store, store.session_id)
    component = savings.SavingsLedger(store, store.session_id).summary()['components']['rtk']
    assert component['saved'] is None and component['unobserved'] == 1


def test_atomic_write_permission_failure_does_not_retry(tmp_path, monkeypatch):
    from codex_token_saver import state
    attempts = []
    def denied(*args, **kwargs):
        attempts.append(args[0])
        raise PermissionError('sandbox denied')
    monkeypatch.setattr(state.os, 'open', denied)
    with pytest.raises(PermissionError):
        state.atomic_write(tmp_path / 'counts.json', b'{}')
    assert len(attempts) == 1
    assert not (tmp_path / 'counts.json').exists()


@pytest.mark.parametrize('tracked,out,measured', [('  stat\n', b'stat\n', True), ('  stat\n', b'other\n', False)])
def test_stat_whitespace_uses_actual_emitted_bytes(pending, tracked, out, measured):
    store, nonce, spool = pending
    plan = {'path': spool.directory / 'pair.jsonl', 'session_id': store.session_id,
            'invocation': 'one', 'binary_sha256': 'fixture'}
    plan['path'].write_text(json.dumps({'protocol': 'ces-rtk-pair-v1', 'pid': 9,
        'invocation_id': 'one', 'before': tracked, 'tracked_after': tracked}) + '\n')
    rtk_observation.finish(spool, plan, 9, 0,
        {'complete': True, 'data': out}, {'complete': True, 'data': b''})
    events = savings.SavingsLedger(spool, store.session_id).summary()['events']
    assert bool(events) == measured
    if measured:
        assert events[0]['after_tokens'] == savings.count_tokens(out.decode())
