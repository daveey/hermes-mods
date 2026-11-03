"""Steady-interval reuse (IDLE-READS.md "Unchanged ticks"): a pass's union card read and the remote gate's
validation are reused across passes only while their content keys are unchanged; time-judged coverage and clock
verdicts are evaluated on every pass; failed reads are never kept.

Native replay V3: two ``read_cards`` per 30 s tick (wake + watchdog) and three gate validations (wake, owner wake,
watchdog) were most of the remaining closeout CPU for unchanged boards and an unchanged hub replica."""
import inspect
import json
import sqlite3

import pytest
from test_closeout import core, plugin
from test_light_read import _board
from test_remote_gate import gate, _norm, _passes  # noqa: F401 -- fixture

REM = 'owner/work/build/t_rem'
GONE = 'owner/work/build/t_gone'
IDS = ['t_done', 't_run', 'default/t_blk', REM, GONE]


def _row(**kw):
    return {'id': 't_rem', 'gateway': 'owner', 'profile': 'work', 'board': 'build', 'remote': True,
            'status': 'running', 'stale': False, 'via': 'origin', **kw}


@pytest.fixture
def reuse(tmp_path, monkeypatch):
    core._structural_reads.clear()
    getattr(plugin, '_read_memo', {}).clear()
    board = tmp_path / 'kanban.db'
    _board(board)
    root = tmp_path / 'home'
    shadow = root / 'kanban' / 'sync-shadow' / 'build.db'
    shadow.parent.mkdir(parents=True)
    with sqlite3.connect(shadow) as db:
        db.execute('CREATE TABLE tasks (id TEXT)')
        db.execute('CREATE TABLE kanban_sync_state (board_id TEXT, pulled_at REAL)')
        db.execute("INSERT INTO kanban_sync_state VALUES ('build', 1000)")
    clock = [1000.0]
    monkeypatch.setattr(plugin.time, 'time', lambda: clock[0])
    monkeypatch.setattr(plugin, '_kanban_root', lambda: root)
    monkeypatch.setattr(plugin, 'board_paths', lambda: [board])
    monkeypatch.setattr(plugin, 'hub_db_path', lambda: None)
    state = {'rows': [_row()], 'raise': 0}

    def inventory():
        if state['raise']:
            raise sqlite3.OperationalError('replica locked')
        return state['rows']
    reads = []
    real = core.read_cards

    def counted(*args, **kwargs):
        reads.append(1)
        return real(*args, **kwargs)
    monkeypatch.setattr(core, 'read_cards', counted)
    world = {'board': board, 'shadow': shadow, 'clock': clock, 'state': state, 'inventory': inventory,
             'reads': reads, 'tmp': tmp_path}
    yield world
    getattr(plugin, '_read_memo', {}).clear()
    core._structural_reads.clear()


def _view(cards):
    return json.dumps([dict(cards), sorted(cards.uncertain), sorted(cards.absence_veto),
                       getattr(cards, 'readable', True)], sort_keys=True, default=str)


def _read(world, light=False, reuse=True):
    kw = {'reuse': reuse} if 'reuse' in inspect.signature(plugin.read_cards).parameters else {}
    return plugin.read_cards(IDS, remote_inventory=world['inventory'], light=light, **kw)


def _reference(world, light=False):
    """What a read with no reuse returns now (its own core read is not counted)."""
    n = len(world['reads'])
    out = _read(world, light, reuse=False)
    del world['reads'][n:]
    return _view(out)


@pytest.mark.parametrize('light', [False, True])
def test_unchanged_union_is_read_once_over_k_passes(reuse, light):
    results = []
    for i in range(6):
        reuse['clock'][0] = 1000.0 + 10 * i
        results.append(_read(reuse, light))
        assert _view(results[-1]) == _reference(reuse, light)
    assert len(reuse['reads']) == 1
    assert results[0]['t_done']['status'] == 'done' and results[0][REM]['status'] == 'running'
    # Fresh dictionaries on every pass: a caller's mutation never leaks into a later pass.
    assert results[1]['t_done'] is not results[2]['t_done']
    results[1]['t_done']['status'] = 'mutated'
    assert _read(reuse, light)['t_done']['status'] == 'done'


@pytest.mark.parametrize('light', [False, True])
def test_board_commit_is_seen_next_pass(reuse, light):
    _read(reuse, light)
    _read(reuse, light)
    with sqlite3.connect(reuse['board']) as db:
        db.execute("UPDATE tasks SET status='done', completed_at=20 WHERE id='t_run'")
    cards = _read(reuse, light)
    assert cards['t_run']['status'] == 'done'
    assert _view(cards) == _reference(reuse, light)
    assert len(reuse['reads']) == 2


def test_inventory_change_is_seen_next_pass(reuse):
    _read(reuse)
    reuse['state']['rows'] = [_row(status='done', completed_at=30.0)]
    cards = _read(reuse)
    assert cards[REM]['status'] == 'done'
    assert _view(cards) == _reference(reuse)
    # Equal content in new objects is not the gate's unchanged projection: read again (never assume).
    reuse['state']['rows'] = [dict(r) for r in reuse['state']['rows']]
    _read(reuse)
    assert len(reuse['reads']) == 3


def test_absence_coverage_is_judged_every_pass(reuse):
    assert GONE not in _read(reuse).absence_veto  # the fresh shadow covers build and lacks t_gone
    reuse['clock'][0] = 1121.0  # same files; the shadow is now older than 120 s
    cards = _read(reuse)
    assert GONE in cards.absence_veto and GONE in cards.uncertain
    assert _view(cards) == _reference(reuse)
    assert len(reuse['reads']) == 1  # the structural read was reused; only coverage was re-judged
    reuse['shadow'].unlink()
    reuse['clock'][0] = 1000.0
    assert GONE in _read(reuse).absence_veto


def test_failed_board_read_is_retried_next_pass(reuse, monkeypatch):
    real = core._board_rows
    fails = [1]

    def flaky(path, left, light):
        if fails:
            fails.pop()
            return [], sqlite3.OperationalError('database is locked')
        return real(path, left, light)
    monkeypatch.setattr(core, '_board_rows', flaky)
    broken = _read(reuse)
    assert 't_done' not in broken and 't_done' in broken.uncertain
    cards = _read(reuse)
    assert cards['t_done']['status'] == 'done'
    assert len(reuse['reads']) == 2  # nothing was kept from the failed read
    _read(reuse)
    assert len(reuse['reads']) == 2


def test_failed_inventory_read_is_retried_next_pass(reuse):
    _read(reuse)
    reuse['state']['rows'] = [_row(status='done', completed_at=30.0)]
    reuse['state']['raise'] = 1
    broken = _read(reuse)
    assert broken.readable is False and REM in broken.uncertain
    reuse['state']['raise'] = 0
    cards = _read(reuse)
    assert cards[REM]['status'] == 'done' and getattr(cards, 'readable', True)
    assert len(reuse['reads']) == 3
    _read(reuse)
    assert len(reuse['reads']) == 3


def test_unobservable_board_takes_no_shortcut(reuse, monkeypatch):
    monkeypatch.setattr(core, 'revision_token', lambda *a, **k: None)
    for _ in range(3):
        _read(reuse)
    assert len(reuse['reads']) == 3


def test_replica_completion_read_failure_is_retried(reuse, monkeypatch):
    hub = reuse['tmp'] / 'hub.db'
    hub.write_text('not a database')
    monkeypatch.setattr(plugin, 'hub_db_path', lambda: hub)
    reuse['state']['rows'] = [_row(via='shepherd', status='done', completed_at=None)]
    for _ in range(2):
        assert _read(reuse)[REM].get('completed_at') is None
    assert len(reuse['reads']) == 2  # the unreadable replica made each read unkeepable
    hub.unlink()
    with sqlite3.connect(hub) as db:
        db.execute('CREATE TABLE replica (gw TEXT, coll TEXT, key TEXT, data TEXT)')
        db.execute("INSERT INTO replica VALUES ('owner', 'tasks', 'work/build/t_rem', ?)",
                   (json.dumps({'id': 't_rem', 'status': 'done', 'completed_at': 40.0}),))
    assert _read(reuse)[REM]['completed_at'] == 40.0
    assert _read(reuse)[REM]['completed_at'] == 40.0
    assert len(reuse['reads']) == 3
    with sqlite3.connect(hub) as db:
        db.execute("UPDATE replica SET data=?", (json.dumps({'id': 't_rem', 'status': 'done', 'completed_at': 41.0}),))
    assert _read(reuse)[REM]['completed_at'] == 41.0
    assert len(reuse['reads']) == 4


def test_pass_readers_share_one_read_until_a_commit(reuse, monkeypatch):
    """The steady interval: wake and watchdog each build a pass reader every tick."""
    led = core.Ledger(reuse['tmp'] / 'ledger.db')
    led.write('chat', batch=IDS, open=[])
    monkeypatch.setattr(plugin, 'ledger', lambda: led)
    monkeypatch.setattr(plugin, 'remote_cards', reuse['inventory'])
    views = {_view(plugin._pass_reader()(IDS)) for _ in range(8)}
    assert len(views) == 1 and len(reuse['reads']) == 1
    with sqlite3.connect(reuse['board']) as db:
        db.execute("UPDATE tasks SET status='blocked' WHERE id='t_run'")
    assert plugin._pass_reader()(IDS)['t_run']['status'] == 'blocked'
    assert len(reuse['reads']) == 2
    # A pass whose ledger holds other ids is a different read (never another pass's subset).
    led.write('chat', batch=IDS[:2], open=[])
    assert set(plugin._pass_reader()(IDS[:2])) == {'t_done', 't_run'}
    assert len(reuse['reads']) == 3


# ---- the remote gate's input-key shortcut (real fork core projection) ------------------------------------------
@pytest.fixture
def hits(gate, monkeypatch):
    calls = []
    real = plugin._gate_hit

    def counted(*args, **kwargs):
        calls.append(gate['clock'].now)
        return real(*args, **kwargs)
    monkeypatch.setattr(plugin, '_gate_hit', counted)
    return calls


def test_gate_validates_once_over_k_unchanged_passes(gate, hits):
    cards = _passes(gate, 6, step=10.0)
    assert len(gate['projections']) == 1
    assert len(hits) == 1  # the first pass (no memo yet); five passes reuse on unchanged input keys
    assert all(_norm(c) == _norm(cards[0]) for c in cards)
    assert _norm(cards[-1]) == _norm(gate['fresh']())


def _change(gate, what):
    if what == 'replica_task':
        gate['publish']('done', 4)
    elif what == 'pull':
        gate['pull']()
    elif what == 'ledger':
        with sqlite3.connect(gate['ledger_path']) as db:
            db.execute("UPDATE origin_obligations SET resolved_session = 'chat2'")
    elif what == 'board':
        with sqlite3.connect(gate['local']) as db:
            db.execute("INSERT INTO tasks VALUES ('t_new', 'x', 'todo', 'chat')")
    elif what == 'offline':
        with sqlite3.connect(gate['replica']) as db:
            db.execute("INSERT OR REPLACE INTO meta VALUES ('offline_since', ?)", (str(gate['clock'].now),))


@pytest.mark.parametrize('what', ['replica_task', 'pull', 'ledger', 'board', 'offline'])
def test_gate_shortcut_sees_each_input_change_next_pass(gate, hits, what):
    _passes(gate, 3, step=5.0)
    validated = len(hits)
    gate['clock'].now += 5.0
    _change(gate, what)
    cards = _passes(gate, 1, step=5.0)[0]
    assert len(hits) == validated + 1  # the changed key sent this pass through the full gate
    assert _norm(cards) == _norm(gate['fresh']())


def test_gate_shortcut_judges_the_replica_clock_every_pass(gate, hits):
    _passes(gate, 3, step=5.0)
    validated = len(hits)
    gate['clock'].now += 200.0  # no file changed; last_ok is now past the 180 s freshness window
    cards = plugin.remote_cards()
    assert len(hits) == validated + 1
    assert [c['stale'] for c in cards if c.get('via') == 'origin'] == [True]
    assert _norm(cards) == _norm(gate['fresh']())


def test_gate_shortcut_never_crosses_an_owner_window_cutoff(gate, hits):
    _passes(gate, 1, step=5.0)
    with plugin._gate_lock:
        memo = plugin._gate['memo']
    cut = [c for c in memo['cutoffs'] if c > memo['t0']]
    assert cut, 'fixture must carry an owner observation window'
    gate['clock'].now = cut[0] - 1.0
    plugin.remote_cards()
    validated = len(hits)
    gate['clock'].now = cut[0]  # nothing changed on disk; the window ends now
    cards = plugin.remote_cards()
    assert len(hits) == validated + 1
    assert _norm(cards) == _norm(gate['fresh']())


@pytest.mark.parametrize('broken', ['revision', 'meta'])
def test_gate_shortcut_needs_readable_keys(gate, hits, monkeypatch, broken):
    if broken == 'revision':
        real = core.revision_token
        # Boards unobservable (the full gate keys them through core's own content keys and still reuses).
        monkeypatch.setattr(core, 'revision_token', lambda path, board=False: None if board else real(path))
    else:
        monkeypatch.setattr(plugin, '_replica_meta', lambda ksa: None)
    _passes(gate, 4, step=5.0)
    assert len(hits) == 4  # every pass validated in full; nothing remembered from unreadable keys
    # An unreadable replica meta also fails the full gate (it judges the same meta): nothing is reused at all.
    assert len(gate['projections']) == (1 if broken == 'revision' else 4)


def test_state_db_change_revalidates_only_when_tips_matter(gate, hits, monkeypatch):
    state_db = gate['ledger_path'].parent.parent / 'state.db'
    __import__('hermes_state').SessionDB(db_path=state_db).close()

    def add(sid):
        with sqlite3.connect(state_db) as db:
            db.execute("INSERT OR IGNORE INTO sessions (id, source, started_at) VALUES (?, 'cli', 0)", (sid,))
    _passes(gate, 2, step=5.0)
    with plugin._gate_lock:
        assert not plugin._gate['memo']['sessions']
    validated = len(hits)
    add('other')
    _passes(gate, 1, step=5.0)
    assert len(hits) == validated  # no alias the projection could resolve: state.db is not an input

    # A projection that resolves an alias tip (core's ``superseded``; _alias_sessions is tested against core in
    # test_remote_gate): state.db is then an input of the projection.
    add('old-chat')
    monkeypatch.setattr(plugin, '_alias_sessions', lambda legacy, rows: frozenset({'old-chat'}))
    gate['ksa']._reset_cache_for_tests()
    plugin._gate_reset()
    _passes(gate, 2, step=5.0)
    with plugin._gate_lock:
        assert plugin._gate['memo']['sessions'], 'fixture must make the projection resolve an alias'
    validated = len(hits)
    _passes(gate, 2, step=5.0)
    assert len(hits) == validated
    add('another')
    _passes(gate, 1, step=5.0)
    assert len(hits) == validated + 1  # state.db moved and the projection resolves tips from it


def test_gate_shortcut_recovers_after_a_transiently_unreadable_key(gate, hits, monkeypatch):
    real = core.revision_token
    down = [True]
    monkeypatch.setattr(core, 'revision_token', lambda path, board=False: None if down[0] else real(path, board))
    _passes(gate, 2, step=5.0)
    assert len(hits) == 2
    down[0] = False
    cards = _passes(gate, 3, step=5.0)
    assert len(hits) == 3  # one full validation once keys read again, then the shortcut
    assert _norm(cards[-1]) == _norm(gate['fresh']())


@pytest.mark.parametrize('what', ['ledger', 'board'])
def test_gate_shortcut_keys_on_committed_revisions_not_only_file_stats(gate, hits, monkeypatch, what):
    """A commit that leaves a file's stat key unchanged (same size and mtime granularity) is still seen."""
    _passes(gate, 2, step=5.0)
    validated = len(hits)
    monkeypatch.setattr(plugin, '_stat_key', lambda path: ('pinned', str(path)))
    _passes(gate, 1, step=5.0)  # re-validated once under the pinned stats (the key shape changed)
    validated = len(hits)
    _passes(gate, 2, step=5.0)
    assert len(hits) == validated
    _change(gate, what)
    cards = _passes(gate, 1, step=5.0)[0]
    assert len(hits) == validated + 1
    assert _norm(cards) == _norm(gate['fresh']())
