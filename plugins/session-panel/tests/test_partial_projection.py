"""Opt-in B2a composition only. Production scheduling is deliberately unchanged."""
import threading
from dataclasses import replace


import pytest

from test_feed import feedmod, Readers
from test_snapshot_store import load_snapshot


def setup(chats=('S1',), external=True):
    m = load_snapshot()
    store = m.SnapshotStore()
    local = m.SourceKey('gateway', 'default', 'default', 'local', '/example/board.db')
    remote = m.SourceKey('remote', 'default', 'default', 'shadow', '/example/shadow.db')
    store.add_source(local)
    if external:
        store.add_source(remote)
    keys = [m.ChatKey('gateway', 'default', s) for s in chats]
    store.register([m.Watch(c, '', (c.session_id,), 0) for c in keys])
    f = feedmod.Feed(Readers(), emit=lambda _: None, clock=lambda: 1000.)
    f.watch(chats)
    return m, store, local, remote, keys, f


def capture(m, store, source, states, *, demands=None):
    ds = store.demands(source) if demands is None else demands
    prior = store.version(source)
    rows, details, scoped, memberships = {}, {}, {}, {}
    for d in ds.values():
        cid = 't_aaaaaa1' if d.id.chat.session_id == 'S1' else 't_bbbbbb2'
        key = m.CardKey(source.gateway, source.profile, source.board, cid)
        status = states.get(d.id.chat.session_id)
        row = {'id': cid, 'title': d.id.chat.session_id, 'session_id': d.id.chat.session_id,
               'status': status, 'gateway': source.gateway, 'profile': source.profile, 'board': source.board}
        selected = [row] if status else []
        linked = [] if status in ('done', 'archived') else selected
        scoped[d.id] = {'linked': linked, 'done': selected}
        memberships[d.id] = (m.Membership(key, d.id.chat.session_id, 'created'),) if status else ()
        if status:
            rows[key] = row
            details[key] = {'task': row, 'links': {'parents': []}, 'events': []}
    receipt = m.Receipt(source, m.SourceVersion(store.epoch, prior.source_generation, prior.source_revision + 1),
        None, None, None, m.Coverage(str(prior.source_revision + 1), ds, frozenset(ds), frozenset()),
        990_000_000_000, 1, 990_000_000_000, 990_000_000_001, store.owner_token(source))
    part = m.SourcePart(receipt, rows=rows, details=details, scope_rows=scoped,
        scope_keys={d.id: frozenset(k for k in rows if rows[k]['session_id'] == d.id.chat.session_id) for d in ds.values()},
        scope_memberships=memberships)
    (store.accept_local if source.producer == 'local' else store.accept_external)(part, prior)
    return part


def base(chat):
    return {'session': chat.session_id, 'lineage': [chat.session_id], 'requests': [], 'outcome_json': {},
            'canonical_seq': 1, 'computed_at': 900., 'tasks': [], 'done': [], 'cards': {}}


def project(f, store, chat, *, pin=None, canonical=None, seq=None):
    assert callable(getattr(f, 'project_board', None)), 'pure partial/source-vector consumer seam missing'
    return f.project_board(store, chat, canonical_base=canonical or base(chat), pin=pin, seq=seq)


def test_partial_local_progress_during_held_external_capture():
    m, store, local, remote, (chat,), f = setup()
    capture(m, store, local, {'S1': 'ready'})
    entered, release = threading.Event(), threading.Event()
    def strict():
        entered.set()
        assert release.wait(5)
    worker = threading.Thread(target=strict)
    worker.start()
    try:
        assert entered.wait(2)
        capture(m, store, local, {'S1': 'scheduled'})  # NEW write after strict entry
        p = project(f, store, chat)
        result = f.publish_projections(store, {chat.session_id: p})[chat.session_id]
        assert result.disposition == 'stored' and result.emit_succeeded
        b = f.bundles[chat.session_id]['bundle']
        assert b['tasks'][0]['status'] == 'scheduled'
        assert p.missing_scopes == {remote: store.demand(remote, chat)}
        assert b['model']['diagnostics'] and f.bundles[chat.session_id]['source_stale']
        assert not release.is_set()
        with pytest.raises(m.CoverageUnavailable):
            m.SnapshotReaders(*store.pin(chat)[:1], chat).linked([chat.session_id])
    finally:
        release.set(); worker.join(5)


@pytest.mark.parametrize('terminal', ['done', 'archived'])
def test_later_computed_sequence_cannot_resurrect_old_pinned_board(terminal):
    m, store, local, _, (chat,), f = setup(external=False)
    capture(m, store, local, {'S1': 'scheduled'})
    old_pin = store.pin(chat)
    capture(m, store, local, {'S1': terminal})
    latest = project(f, store, chat, seq=10)
    assert f.publish_projections(store, {'S1': latest})['S1'].disposition == 'stored'
    older = project(f, store, chat, pin=old_pin, seq=100)
    result = f.publish_projections(store, {'S1': older})['S1']
    assert result.disposition == 'superseded'
    assert f.bundles['S1']['bundle']['model']['sections']['waiting'] == []
    assert f.bundles['S1']['bundle']['done'][0]['status'] == terminal


def test_missing_scope_empty_is_unknown_and_never_heartbeat_fresh():
    m, store, local, _, (chat,), f = setup()
    capture(m, store, local, {})
    pushes = []
    f.emit = pushes.append
    p = project(f, store, chat)
    f.publish_projections(store, {'S1': p})
    assert pushes[-1]['fresh'] == []
    assert f.bundles['S1']['source_stale']
    assert p.missing_scopes and p.bundle['model']['header']['state'] == 'unknown'


@pytest.mark.parametrize('change', ['refs', 'lineage', 'rewatch', 'reset', 'failure', 'source', 'close'])
def test_exact_demand_generation_failure_and_epoch_fence(change):
    m, store, local, _, (chat,), f = setup(external=False)
    capture(m, store, local, {'S1': 'ready'})
    p = project(f, store, chat)
    first_age = p.admission.first_admitted_mono_ns
    if change == 'refs':
        store.require(chat, [m.CardKey('remote', 'default', 'other', 't_cccccc3')])
    elif change == 'lineage':
        store.register([m.Watch(chat, '', ('S1', 'ancestor'), 0)])
    elif change == 'rewatch':
        assert store.forget(chat, p.admission.watch_token)
        store.register([m.Watch(chat, '', ('S1',), 0)])
    elif change == 'reset':
        store.reset(local)
    elif change == 'failure':
        store.fail(local, 0, 'fixture outage', store.demands(local))
    elif change == 'source':
        store.add_source(m.SourceKey('other', 'default', 'default', 'shadow', '/example/new.db'))
    else:
        store.close(store.epoch)
    assert f.publish_projections(store, {'S1': p})['S1'].disposition == 'superseded'
    assert not f.bundles
    if change not in ('close', 'rewatch'):
        assert store.pin(chat)[1].first_admitted_mono_ns == first_age


@pytest.mark.parametrize('change', ['forget', 'stop', 'resume', 'restore'])
def test_feed_lifetime_rejects_held_projection(change):
    m, store, local, _, (chat,), f = setup(external=False)
    capture(m, store, local, {'S1': 'ready'})
    p = project(f, store, chat)
    f.publish_projections(store, {'S1': p})
    if change == 'forget':
        with f.lock:
            f._forget('S1')
        f.watch(['S1'])
    elif change == 'stop':
        f.stopped.set()
    elif change == 'resume':
        f.stopped.set()
        f = feedmod.Feed(Readers(), emit=lambda _: None, clock=lambda: 1000.).resume(f)
    else:
        f = feedmod.Feed(Readers(), emit=lambda _: None, clock=lambda: 1000.).restore(f.snapshot())
    result = f.publish_projections(store, {'S1': p})['S1']
    assert result.disposition in ('stopped', 'superseded')


def test_unrelated_covered_scope_progress_does_not_supersede_pin():
    m, store, local, _, chats, f = setup(('S1', 'S2'), external=False)
    capture(m, store, local, {'S1': 'ready', 'S2': 'ready'})
    p = project(f, store, chats[0])
    capture(m, store, local, {'S1': 'ready', 'S2': 'scheduled'})
    assert store.version(local) != p.input_versions[local]
    assert store.current(p.admission), 'same scoped receipt survives unrelated captured changes'
    assert f.publish_projections(store, {'S1': p})['S1'].disposition == 'stored'


@pytest.mark.parametrize('other_board', [False, True])
def test_barrier_held_projection_does_not_block_peer_or_steal_new_source(other_board):
    m, store, local, _, chats, f = setup(('S1', 'S2'), external=False)
    capture(m, store, local, {'S1': 'ready', 'S2': 'ready'})
    second = replace(local, board='other', canonical_path='/example/other.db') if other_board else local
    if other_board:
        store.add_source(second)
        capture(m, store, second, {})
    entered, release = threading.Event(), threading.Event()
    results = []
    pin = store.pin(chats[0])
    def slow():
        entered.set()
        assert release.wait(5)
        p = project(f, store, chats[0], pin=pin, seq=1000)
        results.append(f.publish_projections(store, {'S1': p})['S1'])
    worker = threading.Thread(target=slow)
    worker.start()
    try:
        assert entered.wait(2)
        capture(m, store, second, {'S1': 'ready', 'S2': 'scheduled'})
        p = project(f, store, chats[1])
        assert f.publish_projections(store, {'S2': p})['S2'].disposition == 'stored'
        assert any(r['status'] == 'scheduled' for r in f.bundles['S2']['bundle']['tasks'])
        assert not release.is_set()
    finally:
        release.set(); worker.join(5)
    assert results and results[0].disposition in ('stored', 'superseded')


def test_unknown_source_retains_last_known_rows_without_reopening_human_controls():
    m, store, local, _, (chat,), f = setup(external=False)
    capture(m, store, local, {'S1': 'blocked'})
    store.fail(local, 0, 'fixture failure', store.demands(local))
    p = project(f, store, chat)
    assert p.bundle['tasks'][0]['stale'] is True
    assert not p.bundle['model']['sections']['needs_you']
    assert p.bundle['model']['sections']['waiting'][0]['detail'].startswith('status unknown')
    assert store.pending(local)


def test_newest_canonical_errors_and_requests_merge_even_when_old_full_starts_later():
    m, store, local, _, (chat,), f = setup(external=False)
    capture(m, store, local, {'S1': 'scheduled'})
    first = project(f, store, chat)
    f.publish_projections(store, {'S1': first})
    held = f.bundles['S1']['bundle']
    canonical = {**held, 'canonical_seq': 50, 'requests': [], 'input_errors': ['canonical source unavailable']}
    f._publish({'S1': canonical}, canonical_only=True)
    f._decision_failures.add('S1')
    capture(m, store, local, {'S1': 'done'})
    p = project(f, store, chat, canonical=base(chat), seq=100)
    f.publish_projections(store, {'S1': p})
    b = f.bundles['S1']['bundle']
    assert b['canonical_seq'] == 50 and b['input_errors'] == ['canonical source unavailable']
    assert 'S1' in f._decision_failures
    # A legacy full read has a later computation sequence but no vector proof.
    old_full = {**held, 'computed_seq': 10000, 'canonical_seq': 49}
    f._publish({'S1': old_full})
    assert f.bundles['S1']['bundle']['tasks'] == []
    assert f.bundles['S1']['bundle']['input_errors'] == ['canonical source unavailable']


def test_emit_failure_is_not_success_and_never_holds_source_lock():
    m, store, local, _, (chat,), f = setup(external=False)
    capture(m, store, local, {'S1': 'ready'})
    entered = threading.Event()
    def emit(_):
        # A different thread can acquire the store while transport executes.
        worker = threading.Thread(target=lambda: (store.pin(chat), entered.set()))
        worker.start(); worker.join(2)
        assert entered.is_set(), 'transport held the snapshot lock'
        raise RuntimeError('fixture transport unavailable')
    f.emit = emit
    p = project(f, store, chat)
    result = f.publish_projections(store, {'S1': p})['S1']
    assert result.disposition == 'stored' and not result.emit_succeeded
    assert 'S1' in f.bundles and store.current(p.admission)


def test_owner_collision_cannot_borrow_detail_through_id_keyed_bundle():
    m, store, local, remote, (chat,), f = setup()
    capture(m, store, local, {'S1': 'ready'})
    capture(m, store, remote, {'S1': 'blocked'})
    p = project(f, store, chat)
    assert not p.bundle['cards']
    assert len(p.bundle['tasks']) == 2
    assert p.bundle['board_errors']
    owners = {row['gateway'] for row in p.bundle['tasks']}
    assert owners == {'gateway', 'remote'}


def test_wrong_lineage_canonical_base_is_refused_and_projection_is_immutable():
    m, store, local, _, (chat,), f = setup(external=False)
    capture(m, store, local, {'S1': 'ready'})
    with pytest.raises(ValueError, match='exact current lineage'):
        project(f, store, chat, canonical={**base(chat), 'lineage': ['someone-else']})
    p = project(f, store, chat)
    with pytest.raises(TypeError):
        p.bundle['tasks'][0]['status'] = 'done'


def test_covered_recovery_clears_only_projection_unknown_and_repeated_input_does_not_churn():
    m, store, local, remote, (chat,), f = setup()
    pushes = []
    f.emit = pushes.append
    capture(m, store, local, {'S1': 'ready'})
    p = project(f, store, chat)
    f.publish_projections(store, {'S1': p})
    assert pushes[-1]['fresh'] == [] and store.pending(remote)
    capture(m, store, remote, {})
    p = project(f, store, chat)
    result = f.publish_projections(store, {'S1': p})['S1']
    assert result.changed and not f.bundles['S1']['source_stale']
    first = f.bundles['S1']['bundle']['computed_at']
    repeated = project(f, store, chat)
    assert repeated.bundle['computed_at'] == first
    assert f.publish_projections(store, {'S1': repeated})['S1'].disposition == 'no_change'
    f._decision_failures.add('S1')
    f.last_emit = 0
    f._publish({}, heartbeat=True)
    assert not pushes[-1]['fresh']
