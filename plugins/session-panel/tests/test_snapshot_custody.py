"""Scope-local replacement, immutable payload and publication-vector regressions."""
import importlib.util
import sys
from dataclasses import dataclass, make_dataclass

import pytest

from test_snapshot_store import load_snapshot


@pytest.fixture
def owned():
    m = load_snapshot()
    store = m.SnapshotStore()
    source = m.SourceKey('gateway', 'default', 'default', 'local', '/example/board.db')
    chats = tuple(m.ChatKey('gateway', 'default', x) for x in ('A', 'B'))
    key = m.CardKey('gateway', 'default', 'default', 'one')
    store.add_source(source)
    store.register([m.Watch(c, '', (c.session_id,), 0) for c in chats])
    return m, store, source, chats, key


def capture(owned, chats=None, *, rows=None, details=None, edges=None, members=None, unknown=False):
    m, store, source, all_chats, key = owned
    chats = all_chats if chats is None else chats
    demands = {store.demand(source, c).id: store.demand(source, c) for c in chats}
    prior = store.version(source)
    receipt = m.Receipt(source, m.SourceVersion(store.epoch, prior.source_generation, prior.source_revision + 1),
                        None, None, None, m.Coverage(str(prior), demands,
                        frozenset() if unknown else frozenset(demands),
                        frozenset(demands) if unknown else frozenset()),
                        1, 1, 1, 2, store.owner_token(source))
    part = m.SourcePart(receipt, rows={key: {'title': 'one'}} if rows is None else rows,
                        details={} if details is None else details, edges={} if edges is None else edges,
                        memberships=tuple(m.Membership(key, c.session_id, 'subscribed') for c in chats)
                        if members is None else members,
                        scope_keys={sid: frozenset({key}) if rows != {} else frozenset() for sid in demands})
    return part, prior


def accept(owned, *args, **kwargs):
    _, store, _, _, _ = owned
    part, prior = capture(owned, *args, **kwargs)
    return store.accept_local(part, prior)


def test_reset_invalidates_an_already_missing_projection(owned):
    _, store, source, chats, _ = owned
    _, missing = store.pin(chats[0])
    store.reset(source)
    assert not store.current(missing)


def test_missing_projection_loses_currency_after_success(owned):
    _, store, source, chats, _ = owned
    _, missing = store.pin(chats[0])
    accept(owned)
    assert not store.current(missing)
    assert store.pin(chats[0])[1].first_admitted_mono_ns == missing.first_admitted_mono_ns


def test_missing_projection_is_fenced_by_new_failure(owned):
    _, store, source, chats, _ = owned
    _, missing = store.pin(chats[0])
    version = store.version(source)
    store.fail(source, version.source_generation, 'read unavailable', store.demands(source), prior_version=version)
    assert not store.current(missing)


def test_exact_publication_ack_retires_only_fulfilled_custody(owned):
    _, store, _, chats, _ = owned
    accept(owned)
    _, old = store.pin(chats[0])
    accept(owned, rows={owned[-1]: {'title': 'changed'}})
    assert not store.acknowledge(old)
    _, served = store.pin(chats[0])
    assert served.id == old.id
    assert served.first_admitted_mono_ns == old.first_admitted_mono_ns
    assert store.acknowledge(served)
    assert not store.current(served)
    _, fresh = store.pin(chats[0])
    assert fresh.id != served.id
    assert fresh.receipt_ids == ()


def test_repeated_publication_has_finite_receipt_custody(owned):
    _, store, _, chats, _ = owned
    for n in range(40):
        accept(owned, rows={owned[-1]: {'title': str(n)}})
        _, admission = store.pin(chats[0])
        assert len(admission.receipt_ids) == 1
        assert store.acknowledge(admission)


def test_added_source_invalidates_projection(owned):
    m, store, _, chats, _ = owned
    accept(owned)
    _, pinned = store.pin(chats[0])
    store.add_source(m.SourceKey('gateway', 'default', 'default', 'shadow', '/example/shadow.db'))
    assert not store.current(pinned)


def test_receipt_capacity_refuses_before_advancing_source(owned):
    m, store, source, chats, key = owned
    store._limits = (400, 64, 65536, 2)
    accept(owned)
    accept(owned, rows={key: {'title': 'second'}})
    part, prior = capture(owned)
    with pytest.raises(m.SnapshotCapacity):
        store.accept_local(part, prior)
    assert store.version(source) == prior
    assert len(store.pin(chats[0])[1].receipt_ids) == 2


def test_watch_capacity_is_atomic_and_forget_prunes_scoped_payload(owned):
    m, store, source, chats, key = owned
    store._limits = (2, 64, 65536, 4096)
    accept(owned)
    extra = m.ChatKey('gateway', 'default', 'C')
    with pytest.raises(m.SnapshotCapacity):
        store.register([m.Watch(extra, '', ('C',), 0)])
    for chat in chats:
        assert store.forget(chat, store.pin(chat)[1].watch_token)
    part = store.export_resume().parts[source]
    assert not part.rows and not part.details and not part.memberships and not part.scope_rows


@pytest.mark.parametrize('kind', ['details', 'edges'])
def test_shared_payload_change_revokes_carried_scope(owned, kind):
    _, store, source, chats, key = owned
    accept(owned)
    _, old = store.pin(chats[1])
    accept(owned, [chats[0]], **{kind: {key: {'new': 'value'} if kind == 'details' else ()}})
    assert store.pin(chats[1])[1].missing_scopes
    assert not store.current(old)


def test_authoritative_membership_removal_keeps_other_chat(owned):
    _, store, source, chats, key = owned
    accept(owned)
    snapshot, _ = accept(owned, [chats[0]], members=())
    assert {m.session for m in snapshot.parts[source].memberships} == {'B'}


def test_old_scope_membership_cannot_reappear(owned):
    m, store, source, chats, key = owned
    part, prior = capture(owned)
    store.register([m.Watch(chats[0], '', ('replacement',), 0)])
    snapshot, _ = store.accept_local(part, prior)
    assert {member.session for member in snapshot.parts[source].memberships} == {'B'}
    assert store.pin(chats[0])[1].missing_scopes
    assert not store.pin(chats[1])[1].missing_scopes


def test_unknown_disposition_revokes_old_coverage(owned):
    _, store, _, chats, _ = owned
    accept(owned)
    _, old = store.pin(chats[0])
    accept(owned, [chats[0]], unknown=True)
    assert store.pin(chats[0])[1].missing_scopes
    assert not store.current(old)
    assert store.pin(chats[1])[1].missing_scopes  # omitted scopes are never certified


@pytest.mark.parametrize('expansion', ['refs', 'lineage'])
def test_scope_expansion_keeps_last_known_payload_until_replacement(owned, expansion):
    m, store, source, chats, key = owned
    store.forget(chats[1], store.pin(chats[1])[1].watch_token)
    accept(owned, [chats[0]])
    if expansion == 'refs':
        store.require(chats[0], [key])
    else:
        store.register([m.Watch(chats[0], '', ('A', 'ancestor'), 0)])
    snapshot, admission = store.pin(chats[0])
    assert admission.missing_scopes
    assert key in snapshot.parts[source].rows
    snapshot, _ = accept(owned, [chats[0]], rows={}, members=())
    assert not snapshot.parts[source].rows


def test_mutable_frozen_dataclass_is_not_detached():
    m = load_snapshot()
    @dataclass(frozen=True)
    class Unsafe:
        payload: list
    with pytest.raises(TypeError):
        m.freeze(Unsafe([]))


def test_proxy_inside_frozen_dataclass_cannot_smuggle_a_live_alias():
    from types import MappingProxyType
    m = load_snapshot()
    @dataclass(frozen=True)
    class Unsafe:
        payload: object
    with pytest.raises(TypeError):
        m.freeze(Unsafe(MappingProxyType({'value': 'initial'})))


def test_stale_failure_cannot_poison_newer_success(owned):
    _, store, source, chats, _ = owned
    prior = store.version(source)
    demands = store.demands(source)
    accept(owned)
    assert not store.fail(source, prior.source_generation, 'old failure', demands, prior_version=prior)
    assert not store.pin(chats[0])[1].missing_scopes


def test_forget_rewatch_and_reset_fence_old_results(owned):
    m, store, source, chats, _ = owned
    part, prior = capture(owned)
    token = store.pin(chats[0])[1].watch_token
    store.forget(chats[0], token)
    store.register([m.Watch(chats[0], '', ('A',), 0)])
    store.accept_local(part, prior)
    assert store.pin(chats[0])[1].missing_scopes
    assert not store.pin(chats[1])[1].missing_scopes
    part, prior = capture(owned)
    store.reset(source)
    with pytest.raises(m.StaleCapture):
        store.accept_local(part, prior)
    assert not store.fail(source, prior.source_generation, 'old', part.receipt.coverage.captured_demands)


def test_transfer_rebinds_content_qualified_module_keys_without_new_coverage(owned, monkeypatch):
    old, store, source, chats, key = owned
    accept(owned)
    _, admission = store.pin(chats[0])
    spec = importlib.util.spec_from_file_location('_snapshot_transfer_successor', old.__file__)
    assert spec is not None and spec.loader is not None
    new = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, new)
    spec.loader.exec_module(new)
    assert old.SourceKey is not new.SourceKey
    store.close(store.epoch)
    resumed = new.SnapshotStore.transfer(store)
    chat = new.ChatKey(*chats[0].__dict__.values())
    current_source = new.SourceKey(*source.__dict__.values())
    snapshot, current = resumed.pin(chat)
    assert all(type(s) is new.SourceKey for s in snapshot.parts)
    assert all(type(c) is new.ChatKey for c in snapshot.watches)
    assert all(type(d) is new.ScopeDemand for group in snapshot.demands.values() for d in group.values())
    assert current.id == admission.id and current.watch_token == admission.watch_token
    assert current.first_admitted_mono_ns == admission.first_admitted_mono_ns
    assert current.receipt_ids == admission.receipt_ids
    assert not current.missing_scopes
    assert resumed.current(current)
    assert new.SnapshotReaders(snapshot, chat).linked(['A']) == [{'title': 'one', 'via': 'subscribed'}]
    assert snapshot.parts[current_source].receipt.read_finished_ns == 2
    assert resumed.owner_token(current_source) != store.owner_token(source)
    assert resumed.version(current_source).source_revision == store.version(source).source_revision


def test_transfer_rejects_changed_dataclass_schema_without_consuming_owner(owned):
    m, store, source, chats, _ = owned
    store.close(store.epoch)
    incompatible = make_dataclass('SourceKey', [('unexpected', str)], frozen=True)('wrong schema')
    store._versions[incompatible] = store._versions[source]
    with pytest.raises(m.StaleCapture, match='incompatible transfer field layout'):
        m.SnapshotStore.transfer(store)
    assert not getattr(store, '_transferred', False)
    store._versions.pop(incompatible)
    assert m.SnapshotStore.transfer(store).pin(chats[0])[1].watch_token


def test_compatible_transfer_requires_stopped_owner_and_fences_captures(owned):
    m, store, source, chats, key = owned
    accept(owned)
    _, admission = store.pin(chats[0])
    part, prior = capture(owned)
    with pytest.raises(m.StaleCapture):
        m.SnapshotStore.transfer(store)
    assert store.close(store.epoch)
    resumed = m.SnapshotStore.transfer(store)
    assert resumed.epoch == store.epoch
    assert resumed.version(source) == prior
    assert resumed.pin(chats[0])[1].first_admitted_mono_ns == admission.first_admitted_mono_ns
    assert resumed.pin(chats[0])[1].watch_token == admission.watch_token
    with pytest.raises(m.StaleCapture):
        resumed.accept_local(part, prior)
    with pytest.raises(m.StaleCapture):
        store.accept_local(part, prior)
    with pytest.raises(m.StaleCapture):
        m.SnapshotStore.transfer(store)
