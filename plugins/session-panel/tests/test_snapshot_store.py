"""Internal snapshot contract; no feed consumer is activated by this suite."""
import importlib.util
import sys
from pathlib import Path

import pytest


def load_snapshot():
    path = Path(__file__).resolve().parents[1] / 'dashboard' / 'board_snapshot.py'
    assert path.is_file(), 'shared immutable source store is not implemented'
    name = '_panel_snapshot_contract'
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return sys.modules[name]


def test_scope_coverage_survives_partial_capture_without_certifying_new_watch():
    m = load_snapshot()
    store = m.SnapshotStore()
    source = m.SourceKey('gateway', 'default', 'default', 'local', '/example/board.db')
    first = m.ChatKey('gateway', 'default', 'first')
    second = m.ChatKey('gateway', 'default', 'second')
    store.add_source(source)
    store.register([m.Watch(first, '', ('first',), 0)])
    frozen = store.demands(source)
    prior = store.version(source)
    store.register([m.Watch(second, '', ('second',), 0)])
    receipt = m.Receipt(source, m.SourceVersion(store.epoch, 0, 1), None, None, None,
                        m.Coverage('capture', frozen, frozenset(frozen), frozenset()),
                        1, 1, 1, 2, store.owner_token(source))
    part = m.SourcePart(receipt)
    snapshot, admissions = store.accept_local(part, prior)
    assert snapshot.schema == 'board-snapshot/1'
    assert not store.pin(first)[1].missing_scopes
    assert store.pin(second)[1].missing_scopes
    assert store.pending(source) == frozenset({store.demand(source, second).id})
    assert {a.chat for a in admissions} == {first}
    with pytest.raises(TypeError):
        snapshot.parts[source] = part
    assert store.pin(second)[1].first_admitted_mono_ns > 0


def test_partial_capture_readmits_unqueried_unchanged_scope():
    m = load_snapshot()
    store = m.SnapshotStore()
    source = m.SourceKey('gateway', 'default', 'default', 'local', '/example/board.db')
    chats = [m.ChatKey('gateway', 'default', name) for name in ('first', 'second')]
    keys = [m.CardKey('gateway', 'default', 'default', name) for name in ('one', 'two')]
    store.add_source(source)
    store.register([m.Watch(c, '', (c.session_id,), 0) for c in chats])
    def capture(demands, rows, scope_keys):
        prior = store.version(source)
        receipt = m.Receipt(source, m.SourceVersion(store.epoch, 0, prior.source_revision + 1),
                            None, None, None, m.Coverage('capture', demands, frozenset(demands), frozenset()),
                            1, 1, 1, 2, store.owner_token(source))
        return m.SourcePart(receipt, rows=rows, scope_keys=scope_keys), prior
    all_demands = store.demands(source)
    part, prior = capture(all_demands, {keys[0]: {'title': 'one'}, keys[1]: {'title': 'two'}},
                          {d.id: frozenset({keys[i]}) for i, d in enumerate(all_demands.values())})
    store.accept_local(part, prior)
    first = store.demand(source, chats[0])
    part, prior = capture({first.id: first}, {keys[0]: {'title': 'changed'}}, {first.id: frozenset({keys[0]})})
    store.accept_local(part, prior)
    snapshot, second = store.pin(chats[1])
    assert second.missing_scopes, 'an unread scope cannot inherit a certificate'
    assert store.demand(source, chats[1]).id not in snapshot.parts[source].scope_receipts
    assert keys[1] in snapshot.parts[source].rows  # last-known payload, not coverage
