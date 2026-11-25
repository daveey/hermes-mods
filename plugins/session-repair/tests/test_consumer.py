"""Canonical creation, readback and no-mutation controls for consumer routing."""
import subprocess
from types import SimpleNamespace

import pytest

from sr_helpers import PLUGIN, load

consumer = load('_sr_consumer_test', PLUGIN / 'consumer.py')
run = load('_sr_consumer_run_test', PLUGIN / 'run.py')


class Store:
    def __init__(self, launch=None, sid='repair-routed', error=None):
        self.launch = launch if launch is not None else {'model': consumer.MODEL, 'provider': 'auto'}
        self.sid, self.error, self.calls, self.closed = sid, error, [], False
        self.connection = SimpleNamespace(close=self.close)

    def create(self, launch, **kw):
        self.calls.append(('create', launch, kw))
        if self.error:
            raise ValueError(self.error)
        return 'repair-routed'

    def call(self, method, **kw):
        self.calls.append((method, kw))
        return {'stored_session_id': self.sid, 'info': {'launch_request': self.launch}}

    def close_reader(self):
        pass

    def close(self):
        self.closed = True


def test_consumer_creates_class_policy_before_resuming():
    store = Store()
    assert consumer.prepare(store) == 'repair-routed'
    assert store.calls == [('create', {'model': 'class:standard', 'provider': 'auto'},
                            {'title': consumer.TITLE, 'record_cwd': False}),
                           ('session.resume', {'session_id': 'repair-routed'})]
    assert store.closed


@pytest.mark.parametrize('launch', [None, {}, {'model': 'legacy-exact'},
    {'model': 'class:standard', 'provider': 'foreign'},
    {'model': 'class:standard', 'provider': 'auto', 'base_url': 'https://example.com'},
    {'model': 'class:standard', 'provider': 'auto', 'api_key': 'fixture'}])
def test_no_mutation_or_send_on_missing_or_concrete_or_foreign_policy(launch):
    store = Store()
    store.launch = launch
    with pytest.raises(ValueError):
        consumer.prepare(store)
    assert store.closed and [c[0] for c in store.calls] == ['create', 'session.resume']


def test_identity_mismatch_and_creation_failure_close_without_retry():
    for store in (Store(sid='different'), Store(error='quota unavailable')):
        with pytest.raises(ValueError):
            consumer.prepare(store)
        assert store.closed
        assert sum(c[0] == 'create' for c in store.calls) == 1


def test_preparation_refusal_never_launches_consumer(tmp_path, monkeypatch):
    def refuse(sd):
        raise ValueError('quota unavailable')
    monkeypatch.setattr(run, 'prepare_consumer', refuse)
    calls = []
    ok, detail, stdout = run.start_turn('fixture prompt', tmp_path, lambda *a, **kw: calls.append(a))
    assert not ok and 'consumer preparation' in detail and stdout == ''
    assert calls == []


def test_actual_core_resume_policy_accepts_same_class_and_rejects_legacy(tmp_path):
    core = pytest.importorskip('hermes_cli.gateway_chat')
    parser = pytest.importorskip('hermes_cli._parser').build_top_level_parser()[0]
    args = parser.parse_args(run.turn_argv(tmp_path / 'prompt', 'repair-routed')[1:])
    snapshot = {'info': {'launch_request': {'model': consumer.MODEL, 'provider': 'auto', 'source': 'cli'}}}
    core.check_resume_policy(args, snapshot)
    with pytest.raises(ValueError, match='Resume retains'):
        core.check_resume_policy(args, {'info': {'launch_request': {'model': 'old', 'source': 'cli',
                                                                   'model_requested': 'old'}}})


def test_actual_gateway_store_forwards_explicit_class_not_profile_default(monkeypatch):
    module = pytest.importorskip('hermes_cli.gateway_session_store')
    calls = []
    snapshot = {'stored_session_id': 'repair-routed', 'info': {'launch_request': {
        'model': consumer.MODEL, 'provider': 'auto', 'source': 'cli'}}}
    # Exercise the real store.create policy/client-env path; only transport and
    # adoption (unused by prepare) are replaced, never the launch policy builder.
    store = module.GatewaySessionStore(connection=SimpleNamespace(close=lambda: None))
    def call(method, **kw):
        calls.append((method, kw))
        if method == 'runtime.describe':
            return {'session_create': {'parameters': []}}
        return snapshot
    monkeypatch.setattr(store, 'call', call)
    monkeypatch.setattr(store, '_adopt', lambda value: value['stored_session_id'])
    assert consumer.prepare(store) == 'repair-routed'
    create = next(kw for method, kw in calls if method == 'session.create')
    assert create['model'] == consumer.MODEL and create['provider'] == 'auto'
    assert create['title'] == consumer.TITLE and create['row_cwd'] == ''
    assert not any(method == 'session.mutate' for method, kw in calls)
