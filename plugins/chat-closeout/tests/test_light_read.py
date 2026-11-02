"""The sidebar's light board read and the inventory scan that skips non-candidate rows decide settlement exactly as
the full read and the full scan do."""
import sqlite3

from test_closeout import plugin, core


def _board(path, *, runs_summary=True, events=True, links=True):
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT, status TEXT, block_kind TEXT, '
                   'completed_at REAL, result TEXT, human_request TEXT)')
        db.executemany('INSERT INTO tasks VALUES (?,?,?,?,?,?,?)', [
            ('t_done', 'a', 'done', None, 10.0, 'r', None), ('t_blk', 'b', 'blocked', 'needs_input', None, None, '{}'),
            ('t_run', 'c', 'running', None, None, None, None), ('t_dup', 'd', 'done', None, 5.0, None, None)])
        if runs_summary:
            db.execute('CREATE TABLE task_runs (id INTEGER PRIMARY KEY, task_id TEXT, summary TEXT)')
        else:
            db.execute('CREATE TABLE task_runs (id INTEGER PRIMARY KEY, task_id TEXT)')  # the full read can't run
        if events:
            db.execute('CREATE TABLE task_events (task_id TEXT, kind TEXT, created_at REAL)')
        if links:
            db.execute('CREATE TABLE task_links (parent_id TEXT, child_id TEXT)')


IDS = ['t_done', 't_blk', 't_run', 't_dup', 'default/t_done', 'ops/t_blk', 'ops/t_dup', 't_absent', 'ops/t_absent']


def _settle_view(cards):
    return {k: {f: c.get(f) for f in core.SETTLE_FIELDS} for k, c in cards.items()}


def test_light_read_matches_the_full_read_on_settlement_fields(tmp_path):
    (tmp_path / 'boards' / 'ops').mkdir(parents=True)
    default, ops = tmp_path / 'kanban.db', tmp_path / 'boards' / 'ops' / 'kanban.db'
    _board(default)
    _board(ops, events=False, links=False)
    full = core.read_cards([default, ops], IDS)
    light = core.read_cards([default, ops], IDS, light=True)
    assert set(full) == set(light) and _settle_view(full) == light
    assert 't_dup' not in full and 'ops/t_dup' in full  # a bare id on two boards is ambiguous in both reads


def test_light_read_refuses_a_board_the_full_read_cannot_query(tmp_path):
    _board(tmp_path / 'kanban.db', runs_summary=False)
    full = core.read_cards([tmp_path / 'kanban.db'], IDS)
    assert full == {} and core.read_cards([tmp_path / 'kanban.db'], IDS, light=True) == {}


def _naive_merge(cards, remote, ids):
    """plugin.read_cards' remote merge before it skipped non-candidates (verbatim loop)."""
    want = set(ids)
    for card in remote:
        key = core.card_key(card)
        cards.pop(str(card['id']), None)
        if key in want:
            previous = cards.get(key)
            if previous is None or not core.is_settled(card):
                cards[key] = card
    return cards


def test_the_inventory_scan_skips_only_rows_that_cannot_match(tmp_path, monkeypatch):
    _board(tmp_path / 'kanban.db')
    monkeypatch.setattr(plugin, 'board_paths', lambda: [tmp_path / 'kanban.db'])
    remote = [
        {'id': 't_r1', 'gateway': 'hub', 'profile': 'p', 'board': 'b', 'remote': True, 'status': 'done',
         'stale': False, 'unfinished': False, 'via': 'origin', 'binding_id': 'x', 'key': 'ignored/key'},
        {'id': 't_r1', 'gateway': 'hub', 'profile': 'p', 'board': 'b', 'remote': True, 'status': 'running',
         'stale': False, 'unfinished': True, 'via': 'origin', 'binding_id': 'y'},
        {'id': 't_legacy', 'remote': True, 'key': 'hub/b/t_old', 'status': 'done', 'stale': False},  # stored key
        {'id': 't_done', 'gateway': 'hub', 'profile': 'p', 'board': 'b', 'remote': True, 'status': 'done'},  # bare pop
        {'id': 't_other', 'gateway': 'hub', 'profile': 'p', 'board': 'b', 'remote': True, 'status': 'done'},
        {'id': 't_nokey', 'remote': True, 'status': 'done'},
    ]
    ids = ['hub/p/b/t_r1', 'hub/b/t_old', 't_done', 'default/t_blk', 'unknown/unknown/unknown/t_nokey', 't_run']
    calls = []
    real = core.card_key
    monkeypatch.setattr(core, 'card_key', lambda c: calls.append(c['id']) or real(c))
    got = plugin.read_cards(ids, remote_inventory=lambda: remote)
    monkeypatch.setattr(core, 'card_key', real)
    assert got == _naive_merge(core.read_cards([tmp_path / 'kanban.db'], ids), remote, ids)
    assert got['hub/p/b/t_r1']['status'] == 'running' and 'hub/b/t_old' in got and 't_done' not in got
    assert 't_other' not in calls and 't_nokey' in calls  # a non-candidate never pays for its key


import pytest


@pytest.mark.parametrize('journal', ['wal', 'delete'])
@pytest.mark.parametrize('light', [True, False])
def test_an_unchanged_board_is_not_read_again_and_any_commit_is_seen(tmp_path, monkeypatch, journal, light):
    """Reuse only while the board's files are unchanged: a same-size in-place update is a new read (WAL and
    rollback journal), and the reused answer equals a fresh one."""
    board = tmp_path / 'kanban.db'
    _board(board)
    writer = sqlite3.connect(board, isolation_level=None)
    writer.execute(f'PRAGMA journal_mode={journal}')
    opens = []
    real = core.ro_connect
    monkeypatch.setattr(core, 'ro_connect', lambda p, *a, **k: opens.append(p) or real(p, *a, **k))
    first = core.read_cards([board], IDS, light=light)
    assert core.read_cards([board], IDS, light=light) == first and len(opens) == 1
    for status in ('review', 'done', 'review'):  # 'review'/'done' differ in length; 'blocked'->'running' too
        writer.execute("UPDATE tasks SET status=? WHERE id='t_run'", (status,))
        assert core.read_cards([board], IDS, light=light)['t_run']['status'] == status
    writer.execute("UPDATE tasks SET status='runnin2' WHERE id='t_run'")  # same size as 'running'... in place
    writer.execute("UPDATE tasks SET status='running' WHERE id='t_run'")
    writer.execute("UPDATE tasks SET status='runnin3' WHERE id='t_run'")
    assert core.read_cards([board], IDS, light=light)['t_run']['status'] == 'runnin3'
    monkeypatch.setattr(core, 'ro_connect', real)
    core._board_cache.clear()
    assert core.read_cards([board], IDS, light=light) == core.read_cards([board], IDS, light=light)
    assert core.read_cards([board], IDS + ['t_extra'], light=light) is not None  # another id set: its own read
    writer.close()
