"""Real ActionStore refusal and route-selection regressions."""
import pytest
from test_local_authority_core import local_world, real
from test_withdrawal import core


def test_reviewer_archive_stamped_offer(real, tmp_path, monkeypatch):
    original = core.ActionStore.decide
    def archive(self, owner, data):
        return original(self, owner, dict(data, choiceId='card-1', revisionText='Archive'))
    with monkeypatch.context() as m:
        m.setattr(core.ActionStore, 'decide', archive)
        world = local_world.__wrapped__(real, tmp_path, m)
        kb, connect, path, store, mirror, op, sx, adapter, owner = world
        receipt = mirror.deliver(op)
        assert receipt['value'] == 'archive'
        with connect(path) as c:
            assert kb.get_task(c, sx.TARGET.owner_task).status == 'archived'
            assert adapter.recorded(c, 'synthetic-offer') is None
            assert not sx._own(c, 'unblocked')


def test_reviewer_real_adapter_refusal(local_world, monkeypatch):
    kb, connect, path, store, mirror, op, sx, adapter, owner = local_world
    now = adapter.time.time()
    monkeypatch.setattr(adapter.time, 'time', lambda: now + 7200)
    with pytest.raises(core.ActionError, match='Local authority delivery refused: local_offer_expired_or_spent'):
        mirror.deliver(op)
    with connect(path) as c:
        assert kb.get_task(c, sx.TARGET.owner_task).status == 'blocked'
        assert not sx._own(c, 'unblocked')
        assert not sx._own(c, 'commented')


def test_reviewer_remote_precedes_local_adapter(local_world, monkeypatch):
    import json
    kb, connect, path, store, mirror, op, sx, adapter, owner = local_world
    binding = dict(mirror._bound(op))
    snapshot = json.loads(binding['snapshot'])
    snapshot['remote'] = {'gateway': 'remote-fixture'}
    binding['snapshot'] = json.dumps(snapshot)
    monkeypatch.setattr(mirror, '_bound', lambda value: binding)
    calls = []
    def remote(value, bound):
        calls.append((value, bound))
        return {'remote': True}
    def no_local(_):
        pytest.fail('remote route called local adapter')
    monkeypatch.setattr(mirror.remote, 'deliver', remote)
    monkeypatch.setattr(adapter, 'deliver', no_local)
    assert mirror.deliver(op) == {'remote': True}
    assert calls == [(op, binding)]
