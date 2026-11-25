"""Optional-core behavior of canonical_age_reassessment: runs with or without the reassessment modules."""
import copy
import sys
import types

import pytest
pytest.importorskip('hermes_cli.kanban_db')
from sr_helpers import Env, fixtures
F = fixtures()
URL = 'https://github.com/example/project/actions/runs/123'
SHA = 'a' * 40
CORE, REPLICA = 'hermes_cli.kanban_owner_reassessment', 'hermes_cli.kanban_owner_reassessment_replica'


@pytest.fixture
def world(tmp_path, monkeypatch):
    env = Env(tmp_path, monkeypatch)
    env.now = int(env.now)
    tid = F.long_wait(env.conn, env.now, waited_s=40000)
    env.conn.execute("UPDATE task_events SET payload=? WHERE task_id=? AND kind='scheduled'",
                     ('{"reason":"producer","awaiting":{"kind":"ci","ref":"%s"},"wake_at":%d}'
                      % (URL, env.now + 86400), tid))
    env.conn.commit()
    live = {'state': {'url': URL, 'headSha': SHA, 'status': 'in_progress'}, 'happened': False}
    monkeypatch.setattr(env.scan_mod.Sources, 'producer_state', lambda *a: copy.deepcopy(live))
    return env, tid


def block(monkeypatch, *names):
    for name in names:
        monkeypatch.setitem(sys.modules, name, None)  # import raises ModuleNotFoundError(name=name)


def run(env, tid):
    found = env.findings(rule='BS-long-wait', card=tid)
    return found, [str(e) for e in env.last_sources.errors]


def test_absent_reassessment_core_is_silent_non_authority(world, monkeypatch):
    env, tid = world
    block(monkeypatch, CORE, REPLICA)
    found, errors = run(env, tid)
    assert found and found[0]['variant'] == 'too_long'
    assert errors == []


def test_absent_replica_module_is_silent_non_authority(world, monkeypatch):
    env, tid = world
    monkeypatch.setitem(sys.modules, CORE, types.ModuleType(CORE))
    sys.modules[CORE].producer = lambda *a: {}
    block(monkeypatch, REPLICA)
    found, errors = run(env, tid)
    assert found and found[0]['variant'] == 'too_long'
    assert errors == []


@pytest.mark.parametrize(('missing_name', 'message'), [
    pytest.param('some_runtime_dep', "No module named 'some_runtime_dep'", id='external-dependency'),
    pytest.param('hermes_cli.unrelated', "No module named 'hermes_cli.unrelated'", id='core-sibling'),
    pytest.param(None, 'missing dependency without a module name', id='unknown-name'),
    pytest.param('some_runtime_dep', "No module named 'hermes_cli.kanban_owner_reassessment'",
                 id='misleading-message'),
])
def test_missing_unrelated_dependency_is_still_an_error(world, monkeypatch, missing_name, message):
    env, tid = world
    core, replica = types.ModuleType(CORE), types.ModuleType(REPLICA)
    core.producer = lambda *a: {}
    def missing(*a, **kw):
        raise ModuleNotFoundError(message, name=missing_name)
    replica.verified_for_event = missing
    monkeypatch.setitem(sys.modules, CORE, core)
    monkeypatch.setitem(sys.modules, REPLICA, replica)
    found, errors = run(env, tid)
    assert found and found[0]['variant'] == 'too_long'
    assert any('owner reassessment' in e and message in e for e in errors)


def test_runtime_failure_on_capable_core_is_still_an_error(world, monkeypatch):
    env, tid = world
    core, replica = types.ModuleType(CORE), types.ModuleType(REPLICA)
    core.producer = lambda *a: {}
    def boom(*a, **kw):
        raise RuntimeError('unreadable evidence')
    replica.verified_for_event = boom
    monkeypatch.setitem(sys.modules, CORE, core)
    monkeypatch.setitem(sys.modules, REPLICA, replica)
    found, errors = run(env, tid)
    assert found and found[0]['variant'] == 'too_long'
    assert any('unreadable evidence' in e for e in errors)
