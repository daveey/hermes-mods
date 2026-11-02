"""Remote shepherd rows (core ``_read_remote_links`` via='shepherd'): fresh, owner-attested finished rows settle; stale,
unfinished and unbound origin rows never do. Table-driven over every key shape the ledger holds. All databases are
temporary SQLite fixtures; no Hermes home, board or hub is touched."""
import json
import sqlite3

import pytest
from test_closeout import plugin, core

SK = 'hub/default/build/t_shep'


def shepherd(status='done', stale=False, unfinished=False, **kw):
    return {'id': 't_shep', 'key': SK, 'gateway': 'hub', 'profile': 'default', 'board': 'build', 'remote': True,
            'status': status, 'stale': stale, 'unfinished': unfinished, 'via': 'shepherd',
            'released': not unfinished and status in core.DONE_STATUSES, **kw}


def origin(status='done', stale=False, unfinished=False, binding='binding-0001', **kw):
    card = {**shepherd(status, stale, unfinished), 'via': 'origin', **kw}
    if binding:
        card['binding_id'] = binding
    return card


# (shape, card, settled, finished): finished = done/archived, so a close-out removes it from the batch.
SHAPES = [
    *[(f'local-{s}', {'status': s}, False, False) for s in ('todo', 'ready', 'running', 'scheduled')],
    ('local-review', {'status': 'review'}, True, False),
    ('local-done', {'status': 'done'}, True, True),
    ('local-archived', {'status': 'archived'}, True, True),
    ('local-blocked-human', {'status': 'blocked', 'block_kind': None}, True, False),
    ('local-blocked-needs-input', {'status': 'blocked', 'block_kind': 'needs_input'}, True, False),
    ('local-blocked-transient', {'status': 'blocked', 'block_kind': 'transient'}, False, False),
    ('local-triage-capability', {'status': 'triage', 'block_kind': 'capability'}, True, False),
    ('local-triage-plain', {'status': 'triage', 'block_kind': None}, False, False),
    ('origin-bound-done', origin(), True, True),
    ('origin-bound-archived', origin('archived'), True, True),
    ('origin-unbound-done', origin(binding=None), False, False),
    ('origin-stale-done', origin(stale=True), False, False),
    ('origin-unfinished-done', origin(unfinished=True), False, False),
    ('origin-review', origin('review', unfinished=True), False, False),
    ('shepherd-fresh-done', shepherd(), True, True),
    ('shepherd-fresh-archived', shepherd('archived'), True, True),
    ('shepherd-stale-done', shepherd(stale=True), False, False),
    ('shepherd-stale-unknown', shepherd(stale=None), False, False),
    ('shepherd-unfinished-done', shepherd(unfinished=True), False, False),
    ('shepherd-running', shepherd('running', unfinished=True), False, False),
    ('shepherd-review', shepherd('review', unfinished=True), False, False),
    ('shepherd-blocked-needs-input', shepherd('blocked', unfinished=True, block_kind='needs_input'), True, False),
    ('missing', None, False, False),
]


@pytest.mark.parametrize('shape,card,settled,finished', SHAPES, ids=[s[0] for s in SHAPES])
def test_settlement_table(shape, card, settled, finished):
    assert core.is_settled(card) is settled


def _upgraded(tmp_path, monkeypatch):
    """A ledger whose one-time upgrade already ran (as on every host after the first tick)."""
    led = core.Ledger(tmp_path / 'ledger.db')
    monkeypatch.setattr(plugin, 'ledger', lambda: led)
    monkeypatch.setattr(plugin, '_lineage', lambda sid: [sid])
    monkeypatch.setattr(plugin, '_session_source', lambda sid: 'desktop')
    assert plugin.ensure_upgraded(lambda ids: {}) is True
    return led


def _tick(cards, now, calls, proof=lambda *a: False):
    def wake(sid, prompt, **kw):
        calls.append((sid, prompt, kw))
        return 'started'
    return plugin.wake_tick(now=now, wake=wake, unseen={}, last_user=lambda _: 0,
                            read=lambda ids: {k: v for k, v in cards.items() if k in ids and v is not None},
                            proof=proof)


@pytest.mark.parametrize('shape,card,settled,finished', SHAPES, ids=[s[0] for s in SHAPES])
def test_wake_and_close_once_per_shape(tmp_path, monkeypatch, shape, card, settled, finished):
    led = _upgraded(tmp_path, monkeypatch)
    key = SK if card is not None and card.get('remote') else 'default/t_local'
    led.write('chat', batch=[key], open=[key])
    cards = {key: card}
    calls = []
    _tick(cards, 1000, calls)
    assert len(calls) == (1 if settled else 0)
    core.record_turn_end(led, 'chat', [], lambda ids: {k: v for k, v in cards.items() if v is not None}, now=1010)
    row = led.get('chat')
    if finished:
        assert row['batch'] == [] and row['covered'] == []      # the row closes
    elif settled:
        assert row['batch'] == [key] and row['covered'] == [key]  # human-parked: covered, stays until it moves
    else:
        assert row['batch'] == [key] and row['covered'] == []
    _tick(cards, 5000, calls)
    assert len(calls) == (1 if settled else 0)  # never a second close-out for the same state


def test_shepherd_reopened_is_uncovered_and_closes_again(tmp_path, monkeypatch):
    led = _upgraded(tmp_path, monkeypatch)
    led.write('chat', batch=[SK], open=[], covered=[SK], done_sig='old', claim_sig='old')
    cards = {SK: shepherd('running', unfinished=True)}
    calls = []
    _tick(cards, 1000, calls)
    row = led.get('chat')
    assert row['covered'] == [] and row['batch'] == [SK] and not row['done_sig'] and not calls
    cards[SK] = shepherd()
    _tick(cards, 2000, calls)
    assert len(calls) == 1 and calls[0][2]['display_metadata']['task_ids'] == [SK]


def test_origin_without_binding_never_wakes(tmp_path, monkeypatch):
    led = _upgraded(tmp_path, monkeypatch)
    led.write('chat', batch=[SK], open=[SK])
    calls = []
    _tick({SK: origin(binding=None)}, 1000, calls)
    assert not calls and led.get('chat')['batch'] == [SK]


# ---- transcript acknowledgement: completed_at carried from the projection or the hub replica -----------------------
def _state_db(path, sid, notice_at, reply_at):
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE messages (session_id TEXT, role TEXT, content TEXT, timestamp REAL, '
                   'display_kind TEXT, finish_reason TEXT, tool_calls TEXT, active INTEGER)')
        db.execute('INSERT INTO messages VALUES (?,?,?,?,NULL,NULL,NULL,1)',
                   (sid, 'user', f'✔ [build] @hub Kanban {SK} done — shipped', notice_at))
        db.execute('INSERT INTO messages VALUES (?,?,?,?,NULL,?,NULL,1)',
                   (sid, 'assistant', 'Checked the result.', reply_at, 'stop'))
    return path


def _hub(path, completed_at, key='default/build/t_shep'):
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE replica (gw TEXT, coll TEXT, key TEXT, version INTEGER, data TEXT)')
        db.execute('INSERT INTO replica VALUES (?,?,?,?,?)', ('hub', 'tasks', key, 1, json.dumps(
            {'id': 't_shep', 'status': 'done', 'completed_at': completed_at})))
    return path


@pytest.fixture
def ack_world(tmp_path, monkeypatch):
    led = _upgraded(tmp_path, monkeypatch)
    led.write('chat', batch=[SK], open=[])
    monkeypatch.setattr(plugin, 'board_paths', lambda: [])
    monkeypatch.setattr(plugin, 'state_dbs', lambda: [tmp_path / 'state.db'])
    monkeypatch.setattr(plugin, 'hub_db_path', lambda: None)
    _state_db(tmp_path / 'state.db', 'chat', notice_at=600, reply_at=601)
    return led


def _ack_tick(calls):
    return plugin.wake_tick(now=10_000, wake=lambda sid, p, **k: calls.append(sid) or 'started', unseen={},
                            last_user=lambda _: 0, read=plugin._pass_reader())


def test_shepherd_ack_uses_completed_at_from_projection(ack_world, monkeypatch):
    rows = [shepherd(), {**origin(), 'completed_at': 590}]  # same owner key; the shepherd row is the one kept
    monkeypatch.setattr(plugin, 'remote_cards', lambda: [dict(r) for r in rows])
    assert plugin.read_cards([SK])[SK]['completed_at'] == 590
    calls = []
    _ack_tick(calls)
    assert not calls                                   # answered notice: no close-out wake
    assert ack_world.get('chat')['batch'] == []


def test_shepherd_ack_uses_completed_at_from_replica(ack_world, monkeypatch, tmp_path):
    monkeypatch.setattr(plugin, 'remote_cards', lambda: [shepherd()])
    hub = _hub(tmp_path / 'hub.db', 595)
    monkeypatch.setattr(plugin, 'hub_db_path', lambda: hub)
    assert plugin.read_cards([SK])[SK]['completed_at'] == 595
    calls = []
    _ack_tick(calls)
    assert not calls and ack_world.get('chat')['batch'] == []


def test_shepherd_without_completion_time_still_closes_out(ack_world, monkeypatch):
    monkeypatch.setattr(plugin, 'remote_cards', lambda: [shepherd()])
    assert plugin.read_cards([SK])[SK].get('completed_at') is None
    calls = []
    _ack_tick(calls)
    assert calls == ['chat']                            # no proof: the handoff gets its close-out instead


def test_shepherd_notice_after_completion_only(ack_world, monkeypatch, tmp_path):
    """A notice older than the completion is not its acknowledgement."""
    monkeypatch.setattr(plugin, 'remote_cards', lambda: [shepherd()])
    hub = _hub(tmp_path / 'hub.db', 700)
    monkeypatch.setattr(plugin, 'hub_db_path', lambda: hub)
    calls = []
    _ack_tick(calls)
    assert calls == ['chat']
