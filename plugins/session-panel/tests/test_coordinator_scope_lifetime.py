"""Composed unchanged-stamp scope races and disk restore forget/rewatch.

Original B2b-ii gaps only. Fixtures and production stay untouched.
"""
import sqlite3
import threading
import uuid
from contextlib import closing
from types import SimpleNamespace

import pytest

# Public Python 3.11 CI deliberately has no Hermes core. Match the shared
# real-reader fixtures before importing their core-backed helpers.
pytest.importorskip('hermes_cli.kanban_db')

from hermes_cli.kanban_db_connect import connect
from hermes_cli import kanban_chat_links as links

from test_core_readers import kanban_db
from test_feed import feedmod
from test_partial_projection_core import action_core
from test_reader_batch import graph
from test_source_coordinator_core import (
    api, board, compose, drain, readers_for, strict_empty,
)


EXCLUSIVE = 'Only reachable after in-memory demand expansion'


def _sessions(r, names):
    with closing(sqlite3.connect(r.state_db())) as state:
        state.executemany('INSERT OR IGNORE INTO sessions(id, parent_session_id) VALUES (?, NULL)',
                          [(name,) for name in names])
        state.commit()


def _exclusive(r, domain, session):
    if domain == 'local':
        with closing(connect(r.boards()[0][1])) as conn:
            task = kanban_db.create_task(conn, title=EXCLUSIVE, session_id=session, initial_status='running')
            conn.execute("UPDATE tasks SET status='ready' WHERE id=?", (task,))
            conn.commit()
            return task
    replica = connect(r.ksc.shadow_path('default'))
    try:
        task = kanban_db.create_task(replica, title=EXCLUSIVE, session_id='elsewhere',
                                     initial_status='running')
        replica.execute('UPDATE tasks SET created_on=? WHERE id=?', ('remote-test', task))
        replica.execute(
            'INSERT INTO task_chat_links(uid,task_id,chat_gateway,chat_profile,chat_session,kind,released,created_at) '
            'VALUES (?,?,?,?,?,?,0,1)',
            (links.link_uid(task, 'local-test', 'default', session, 'shepherd'), task,
             'local-test', 'default', session, 'shepherd'))
        replica.commit()
        return task
    finally:
        replica.close()


def _source(lane, domain):
    return lane.sources[0] if domain == 'local' else lane.external


def _ids(bundle):
    return {row['id'] for row in bundle.get('tasks', ()) + bundle.get('done', ())}


def _seen(bundle):
    return _ids(bundle) | set(bundle.get('cards', {}))


def _hold(original, entered, release, calls):
    def held(*args, **kwargs):
        n = len(calls)
        calls.append(1)
        if n < len(entered):
            entered[n].set()
            assert release[n].wait(5), 'capture barrier was not released'
        return original(*args, **kwargs)
    return held


def _freeze_capture_clock(lane, monkeypatch):
    import time
    frozen = SimpleNamespace(**vars(time))
    frozen.monotonic = lambda: 100.
    monkeypatch.setattr(lane.capture, 'time', frozen)


def _arm_capture(r, monkeypatch, domain, entered, release, calls):
    if domain == 'local':
        monkeypatch.setattr(r, 'capture_local', _hold(r.capture_local, entered, release, calls))
    else:
        monkeypatch.setattr(r, 'capture_external', _hold(r.capture_external, entered, release, calls))


@pytest.mark.parametrize('domain', ['local', 'external'])
@pytest.mark.parametrize('change', ['register', 'lineage', 'refs'])
def test_unchanged_stamp_scope_registration_and_expansion_during_capture(
        graph, monkeypatch, tmp_path, domain, change):
    r, covered, shared_remote, ksa = graph
    monkeypatch.setenv('HERMES_HUB_STATE', str(tmp_path / 'hub-state'))
    _sessions(r, ('NEW', 'ANC', 'HELD'))
    target = 'NEW' if change == 'register' else 'S1'
    seed_session = {'register': 'NEW', 'lineage': 'ANC', 'refs': 'HELD'}[change]
    exclusive = _exclusive(r, domain, seed_session)
    local_stamp, external_stamp = r.board_stamps(), r.external_stamps()
    published, armed = threading.Event(), [False]
    pushes = []

    def emit(payload):
        pushes.append(payload)
        if armed[0] and _ids(payload.get('bundles', {}).get('S0', {})):
            published.set()

    f = feedmod.Feed(r, emit=emit, clock=lambda: 1800000000.)
    watched = ['S0'] if change == 'register' else ['S0', 'S1']
    f.watch(watched)
    lane = compose(f, r)
    entered = [threading.Event(), threading.Event()]
    release = [threading.Event(), threading.Event()]
    calls = []
    _arm_capture(r, monkeypatch, domain, entered, release, calls)
    _freeze_capture_clock(lane, monkeypatch)
    m = lane.module
    source = None
    try:
        lane.step()
        if domain == 'external':
            for worker in list(lane.acquiring.values()):
                worker.join(5)
            lane.step()
        assert entered[0].wait(3), 'frozen capture never entered'
        source = _source(lane, domain)
        frozen = dict(lane.store.demands(source))
        frozen_tokens = {sid.opaque_watch_token for sid in frozen}
        chat = m.ChatKey(lane.gateway, lane.profile, target)
        if change == 'register':
            f.watch(['NEW'])
            assert lane.store.register([m.Watch(chat, '', ('NEW',), 0)])
        elif change == 'lineage':
            original_lineage = r.lineage
            def expanded(session):
                ids = list(original_lineage(session) or [session])
                if session == 'S1' and 'ANC' not in ids:
                    ids.append('ANC')
                return ids
            monkeypatch.setattr(r, 'lineage', expanded)
            assert lane.store.register([m.Watch(chat, '', tuple(expanded('S1')), 0)])
        else:
            owner = lane.gateway if domain == 'local' else 'remote-test'
            assert lane.require('S1', [m.CardKey(owner, 'default', 'default', exclusive)])
        current = dict(lane.store.demands(source))
        new_demand = lane.store.demand(source, chat)
        assert new_demand.id not in frozen
        assert frozen_tokens <= {sid.opaque_watch_token for sid in current} | frozen_tokens
        assert current[new_demand.id] == new_demand
        version = lane.store.version(source)
        assert lane.store.fail(
            source, version.source_generation, 'expanded scope fixture failure',
            {new_demand.id: new_demand}, prior_version=version)
        armed[0] = True
        release[0].set()
        for worker in list(lane.acquiring.values()):
            worker.join(5)
        lane.step()
        assert entered[1].wait(3), 'successor capture did not start at unchanged stamps'
        lane.service()
        for worker in list(lane.active.values()):
            worker.join(5)
        lane.service()
        assert published.wait(3) or 'S0' in f.bundles, 'covered chat did not publish while successor blocked'
        assert not release[1].is_set()
        covered_pin = lane.store.pin(lane.chats['S0'])[1]
        missing_pin = lane.store.pin(chat)[1]
        live = lane.store.demand(source, chat)
        assert not covered_pin.missing_scopes.get(source)
        assert missing_pin.missing_scopes.get(source) == live
        assert live.id in lane.store.pending(source)
        assert missing_pin.required_failures or live.id not in frozen
        assert exclusive not in _seen(f.bundles.get('S0', {}).get('bundle', {}))
        if target in f.bundles:
            assert exclusive not in _seen(f.bundles[target]['bundle'])
        assert covered_pin.watch_token != missing_pin.watch_token or change != 'register'
        assert r.board_stamps() == local_stamp and r.external_stamps() == external_stamp
        assert covered_pin.required_scopes[source].id in frozen
        assert missing_pin.required_scopes[source].id == live.id
        if change == 'lineage':
            assert 'ANC' in live.lineage
        elif change == 'refs':
            assert live.refs
        release[1].set()
        drain(lane)
        snapshot = lane.store.pin(chat)[0]
        part = snapshot.parts[source]
        # Membership rows and dependency details are different contracts. An
        # exclusive ref is not linked to this chat and must NOT be promoted to a
        # listed membership row merely because its closure was requested.
        captured_ids = {k.card_id for k in (part.details if change == 'refs' else part.rows)}
        bundle = f.bundles[target]['bundle']
        got = _seen(bundle)
        assert exclusive in captured_ids, (
            'successor capture omitted the preseeded expanded row/detail', exclusive, captured_ids)
        if change == 'refs':
            key = next(k for k in live.refs if k.card_id == exclusive)
            assert key in part.scope_keys[live.id]
            assert part.details[key]['task']['title'] == EXCLUSIVE
            assert exclusive not in {k.card_id for k in part.rows}, 'refs cannot invent membership'
        if change != 'refs':
            assert exclusive in got, 'successor did not publish the preseeded expanded membership row'
        lineage = ['NEW'] if change == 'register' else (['S1', 'ANC'] if change == 'lineage' else ['S1'])
        canonical = {row['id'] for row in r.linked(lineage) + r.done(lineage)}
        if change == 'refs':
            canonical.add(exclusive)
        else:
            assert exclusive in canonical
        assert not lane.store.pin(chat)[1].missing_scopes.get(source)
    finally:
        for event in release:
            event.set()
        f.stopped.set()
        lane.close()


@pytest.mark.parametrize('old_result', ['success', 'failure'])
def test_disk_restore_forget_rewatch_fences_old_success_and_failure(
        api, board, monkeypatch, tmp_path, old_result):
    from hermes_state import SessionDB
    path, conn = board
    r = readers_for(api, path, monkeypatch)
    monkeypatch.setattr(r, 'external_capture', strict_empty)
    now = [1800000000.]
    task = kanban_db.create_task(conn, title='Restored local row', session_id='S')
    db = SessionDB(db_path=tmp_path / 'state.db')
    db.create_session('S', 'desktop')
    core = action_core()
    owner, rid = core.server_owner('S'), str(uuid.uuid4())
    actions = core.ActionStore()
    actions.create(owner, dict(schemaVersion=2, owner=dict(owner), requestId=rid, revision=1,
        kind='approval', title='Authorize the precise fixture scope',
        scope=dict(markdown='Only change example.py.', complete=True, sourceRefs=[]),
        limits=[dict(key='cost', value='0', permissionRequired=True)], exclusions=['No network changes'],
        outcomes=dict(approve='Authorize only this scope', revise='Keep blocked', decline='Cancel'),
        choices=[dict(id=e, label=e.title(), effect=e) for e in ('approve', 'revise', 'decline')],
        aliases=[], state='open'))
    snap_path = tmp_path / 'session-panel' / 'feed-restore.json'
    snap_path.parent.mkdir(parents=True)
    old = feedmod.Feed(r, emit=lambda _: None, clock=lambda: now[0])
    old.watch(['S'])
    previous = compose(old, r)
    restored = lane = None
    entered, release = threading.Event(), threading.Event()
    try:
        old.changes(['S']); drain(previous)
        ask = next(row for row in old.bundles['S']['bundle']['model']['sections']['needs_you']
                   if row.get('request_id') == rid)
        assert ask['options']
        assert api.save_snapshot(old, snap_path, claim=True)
        assert snap_path.is_file() and '\n' in snap_path.read_text()
        core.withdraw_request(actions, None, owner, rid, by='agent', reason='fixture request is moot')
        assert actions.get(owner, rid)['state'] == 'withdrawn'
        old.stopped.set(); previous.close()
        loaded = api.load_snapshot(snap_path, now=now[0])
        assert loaded and loaded['epoch'] == old.epoch
        restored = feedmod.Feed(r, emit=lambda _: None, clock=lambda: now[0]).restore(loaded)
        assert 'S' in restored._carried and not restored._bundle_fresh('S', now[0])
        stale = restored.bundles['S']['bundle']
        assert any(row.get('request_id') == rid for row in stale['model']['sections']['needs_you'])
        lane = compose(restored, r)
        assert not lane.chats, 'disk restore must not revive snapshot-store coverage'
        restored.refresh_decisions()
        current = restored.bundles['S']['bundle']
        assert not current['model']['sections']['needs_you'], 'disk restore revived withdrawn consent'
        assert actions.get(owner, rid)['state'] == 'withdrawn'
        assert not current['model']['sections']['running']
        original = r.capture_local

        def held(*args, **kwargs):
            entered.set()
            assert release.wait(5)
            if old_result == 'failure':
                raise RuntimeError('old in-flight failure after rewatch')
            return original(*args, **kwargs)

        monkeypatch.setattr(r, 'capture_local', held)
        _freeze_capture_clock(lane, monkeypatch)
        restored.watch(['S'])
        lane.step()
        assert entered.wait(3)
        chat = lane.chats['S']
        source = lane.sources[0]
        old_pin = lane.store.pin(chat)[1]
        old_token = old_pin.watch_token
        frozen = dict(lane.store.demands(source))
        feed_token = restored._watch_tokens['S']
        with restored.lock:
            restored._forget('S')
        restored.watch(['S'])
        new_feed = restored._watch_tokens['S']
        assert new_feed is not feed_token
        lane.store.forget(chat, old_token)
        lane.store.register([lane.module.Watch(chat, '', ('S',), 0)])
        lane.tokens['S'] = new_feed
        new_pin = lane.store.pin(chat)[1]
        assert new_pin.watch_token != old_token
        assert new_pin.missing_scopes
        assert new_pin.required_scopes[source].id not in frozen
        release.set()
        for worker in list(lane.acquiring.values()):
            worker.join(5)
        lane.service()
        pin = lane.store.pin(chat)[1]
        assert pin.watch_token == new_pin.watch_token
        assert pin.missing_scopes, 'old in-flight result certified the rewatch token'
        assert pin.required_scopes[source].id not in frozen
        if old_result == 'success':
            assert not lane.store.fail(
                source, lane.store.version(source).source_generation, 'old forgotten failure',
                frozen, prior_version=lane.store.version(source)) or pin.missing_scopes
        assert 'S' not in lane.pending or lane.store.pending(source)
        watches = lane.store.pin(chat)[0].watches
        assert set(watches) == {chat}
        assert watches[chat].opaque_watch_token == pin.watch_token
        assert actions.get(owner, rid)['state'] == 'withdrawn'
        assert not restored._bundle_fresh('S', now[0])
        monkeypatch.setattr(r, 'capture_local', original)
        drain(lane)
        assert not lane.store.pin(chat)[1].missing_scopes.get(source)
        assert actions.get(owner, rid)['state'] == 'withdrawn'
        assert not restored.bundles['S']['bundle']['model']['sections']['needs_you']
        assert task in _ids(restored.bundles['S']['bundle'])
    finally:
        release.set()
        if restored is not None:
            restored.stopped.set()
        if lane is not None:
            lane.close()
        old.stopped.set()
        previous.close()
        r.release(); conn.close(); db.close()
