"""The asks store stays readable while another process writes, and a failed reconcile never drops extraction.

Regression for self-heal defect plugin-dca5d02c20: in rollback-journal mode a writer committing (EXCLUSIVE/PENDING
lock) shut every reader out for the full 30 s busy timeout, so `transform_llm_output` failed with "database is
locked" and, because reconcile and process_reply shared one try block, that reply's asks were never recorded.
"""
import importlib.util
import json
import sqlite3
import time
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('concurrency_core', ROOT / 'core.py')
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)
OWNER = {'gatewayId': 'test-gateway', 'profile': 'default', 'storedSessionId': 'session-a'}


def _request():
    return dict(schemaVersion=2, owner=OWNER.copy(), requestId=str(uuid.uuid4()), revision=1,
                kind='approval', title='Synthetic change',
                scope=dict(markdown='Change example.py', complete=True, sourceRefs=[]),
                limits=[], exclusions=[], outcomes=dict(approve='Authorize', revise='Revise', decline='Cancel'),
                choices=[dict(id=e, label=e.title(), effect=e) for e in ['approve', 'revise', 'decline']],
                aliases=[], state='open')


def test_store_uses_wal(tmp_path):
    store = core.ActionStore(tmp_path / 'actions.sqlite3')
    c = sqlite3.connect(store.path)
    try:
        assert c.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
    finally:
        c.close()


def test_existing_rollback_journal_store_is_converted(tmp_path):
    path = tmp_path / 'actions.sqlite3'
    c = sqlite3.connect(path)
    c.execute('PRAGMA journal_mode=DELETE'); c.execute('CREATE TABLE t(x)'); c.commit(); c.close()
    core.ActionStore(path)
    c = sqlite3.connect(path)
    try:
        assert c.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
    finally:
        c.close()


@pytest.mark.parametrize('method', ['requests', 'get'])
def test_reads_return_while_another_connection_holds_the_write_lock(tmp_path, monkeypatch, method):
    store = core.ActionStore(tmp_path / 'actions.sqlite3')
    r = store.create(OWNER, _request())
    real_connect = core.sqlite3.connect

    def bounded(*a, **kw):
        kw['timeout'] = 1  # Bound the failure; production waited the full busy timeout.
        return real_connect(*a, **kw)
    monkeypatch.setattr(core.sqlite3, 'connect', bounded)
    args = (OWNER,) if method == 'requests' else (OWNER, r['requestId'])
    expected = getattr(store, method)(*args)
    # A writer mid-commit: in rollback-journal mode this lock shuts out every reader until it ends.
    writer = sqlite3.connect(store.path, timeout=0)
    writer.execute('BEGIN EXCLUSIVE')
    writer.execute("UPDATE requests SET state=state")
    try:
        t0 = time.monotonic()
        assert getattr(store, method)(*args) == expected
        assert time.monotonic() - t0 < 1
    finally:
        writer.rollback(); writer.close()


def test_timeout_is_configurable(tmp_path):
    store = core.ActionStore(tmp_path / 'actions.sqlite3', timeout=0.25)
    c = store._connect()
    try:
        assert store.timeout == 0.25
    finally:
        c.close()


def test_reconcile_failure_does_not_skip_process_reply(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path)); monkeypatch.setenv('HERMES_GATEWAY_ID', 'test-gateway')
    monkeypatch.delenv('HERMES_KANBAN_TASK', raising=False)
    pspec = importlib.util.spec_from_file_location('concurrency_plugin', ROOT / '__init__.py')
    plugin = importlib.util.module_from_spec(pspec); pspec.loader.exec_module(plugin)
    monkeypatch.setattr(plugin.core, 'home', lambda: tmp_path)
    monkeypatch.setattr(plugin.core, 'server_owner', lambda sid: dict(OWNER, storedSessionId=sid))

    def locked(*a, **kw):
        raise sqlite3.OperationalError('database is locked')
    monkeypatch.setattr(plugin.chat, 'reconcile', locked)
    called = []
    real = plugin.core.process_reply

    def spy(*a, **kw):
        called.append(kw.get('action_owner'))
        return real(*a, **kw)
    monkeypatch.setattr(plugin.core, 'process_reply', spy)
    monkeypatch.setattr(plugin, 'aux_llm', lambda *a: json.dumps({'steps': [{
        'text': 'Approve example', 'kind': 'approve', 'verbatim_span': 'Should I change the example?',
        'options': []}], 'resolved': []}))
    plugin.transform_llm_output('Should I change the example?', 'session-a', 'desktop')
    assert called, 'process_reply never ran after reconcile raised'
    assert len(plugin.core.ActionStore().requests(OWNER)) == 1
    trace = tmp_path / 'cache' / 'user-steps' / 'trace.jsonl'
    assert trace.exists() and 'reconcile_error' in trace.read_text(), 'no reconcile_error trace'
