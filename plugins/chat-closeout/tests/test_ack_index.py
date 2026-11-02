"""Per-pass cost of the ack proof: one transcript read per lineage (not one per finished card), and the 30 s waker
tick shares one pass's state.db connections. The index proves exactly what the per-card scan proved."""
import random
import sqlite3

import pytest
from test_closeout import plugin, core

SCHEMA = ("CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT);"
          "CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT, content TEXT, "
          "timestamp REAL, display_kind TEXT, finish_reason TEXT, tool_calls TEXT, active INTEGER DEFAULT 1)")


def _reference_matches(text, tid):
    """chat-closeout's per-card notice match before the index (verbatim)."""
    import re
    match = re.match(r"^\s*✔\s*\[([^\]]+)\]\s+@\S+\s+Kanban\s+(\S+)\s+(done|archived)(?:\s|$)", text)
    if not match:
        return False
    board, named, _ = match.groups()
    parts = str(tid).split('/')
    if len(parts) == 4:
        return named == tid and board == parts[2]
    if len(parts) == 2:
        return board == parts[0] and named == parts[1]
    return len(parts) == 1 and board == 'default' and named == tid


def _reference_ack(path, ids, tid, since):
    """chat-closeout's per-card ack proof before the index (verbatim scan, one query per card)."""
    ids = [i for i in dict.fromkeys(ids) if i]
    if not ids or not tid:
        return False
    conn = core.ro_connect(path)
    try:
        rows = conn.execute(f"SELECT role, content, timestamp, display_kind, finish_reason, tool_calls "
                            f"FROM messages WHERE session_id IN ({','.join('?' for _ in ids)}) AND active=1 "
                            f"AND timestamp >= ? ORDER BY timestamp", (*ids, float(since))).fetchall()
    finally:
        conn.close()
    notice_at = None
    for r in rows:
        text = str(r["content"] or "")
        if r['role'] == 'user':
            notice_at = float(r['timestamp']) if _reference_matches(text, tid) else None
        elif (notice_at is not None and r['role'] == 'assistant' and text.strip()
              and float(r['timestamp']) > notice_at
              and r['display_kind'] in (None, '', 'final')
              and r['finish_reason'] in ('stop', 'end_turn')
              and r['tool_calls'] in (None, '', '[]', 'null')):
            return True
    return False


TIDS = ['t_1', 't_2', 'default/t_1', 'build/t_2', 'gw/p/build/t_3', 'gw/p/other/t_3', 'x/y/t_1', 't_9']
USER_TEXT = ['✔ [default] @default Kanban t_1 done — ok', '✔ [build] @default Kanban t_2 archived',
             '✔ [build] @hub Kanban gw/p/build/t_3 done', '✔ [default] @default Kanban t_2 done',
             '  ✔ [default] @x Kanban t_9 done\nmore', '✔ [default] @x Kanban t_1 running', 'thanks, next?',
             '[chat-closeout] brief', '']


def _random_transcript(conn, rng):
    stamps = rng.sample(range(1, 200), 60)  # unique: tie order is SQLite's, not chat-closeout's
    for at in sorted(stamps, key=lambda _: rng.random()):
        sid = rng.choice(['a', 'a', 'b', 'noise'])
        role = rng.choice(['user', 'user', 'assistant', 'assistant', 'tool', 'system'])
        content = rng.choice(USER_TEXT) if role == 'user' else rng.choice(['Done.', 'Done.', 'ok', '', '   ', None])
        conn.execute("INSERT INTO messages (session_id, role, content, timestamp, display_kind, finish_reason, "
                     "tool_calls, active) VALUES (?,?,?,?,?,?,?,?)",
                     (sid, role, content, float(at), rng.choice([None, None, '', 'final', 'commentary']),
                      rng.choice(['stop', 'stop', 'end_turn', 'length', None]),
                      rng.choice([None, None, '', '[]', 'null', '[{}]']), rng.choice([1, 1, 1, 1, 1, 0])))


@pytest.mark.parametrize('seed', range(150))
def test_the_lineage_index_proves_what_the_per_card_scan_proved(tmp_path, seed):
    rng = random.Random(seed)
    path = tmp_path / 'state.db'
    with sqlite3.connect(path) as conn:
        conn.executescript(SCHEMA)
        _random_transcript(conn, rng)
    ids = rng.choice([['a'], ['a', 'b'], ['b', 'a', 'a']])
    pool = core.ReadPool()
    idx = core.AckIndex(path, [i for i in dict.fromkeys(ids)])
    try:
        for _ in range(25):  # one index answers a pass's cards in any order of their completion times
            tid, since = rng.choice(TIDS), float(rng.randint(0, 210))
            want = _reference_ack(path, ids, tid, since)
            assert idx.proves(tid, since, pool) is want, (seed, tid, since)
            assert core.ack_proven([path], ids, tid, since) is want, (seed, tid, since)
    finally:
        pool.close()


def test_the_random_transcripts_prove_both_ways(tmp_path):
    """The equivalence above is not vacuous: a good share of its questions are proven."""
    answers = {True: 0, False: 0}
    for seed in range(150):
        rng = random.Random(seed)
        path = tmp_path / f'{seed}.db'
        with sqlite3.connect(path) as conn:
            conn.executescript(SCHEMA)
            _random_transcript(conn, rng)
        ids = rng.choice([['a'], ['a', 'b'], ['b', 'a', 'a']])
        for _ in range(25):
            tid, since = rng.choice(TIDS), float(rng.randint(0, 210))
            answers[_reference_ack(path, ids, tid, since)] += 1
    assert answers[True] >= 150 and answers[False] >= 1500, answers


def _queries(monkeypatch):
    """Count transcript reads (``SELECT ... FROM messages``) on every connection core opens."""
    seen = []
    real = core.ro_connect

    def connect(path, *a, **kw):
        conn = real(path, *a, **kw)
        conn.set_trace_callback(lambda sql: seen.append(sql) if 'FROM messages' in sql else None)
        return conn

    monkeypatch.setattr(core, 'ro_connect', connect)
    return seen


@pytest.fixture
def chat(tmp_path, monkeypatch):
    path = tmp_path / 'state.db'
    with sqlite3.connect(path) as conn:
        conn.executescript(SCHEMA)
        conn.execute("INSERT INTO sessions VALUES ('chat', 'desktop')")
        for sid, role, text, at, finish in (
                ('chat', 'user', '✔ [default] @default Kanban t_1 done', 101, None),
                ('chat', 'assistant', 'Checked.', 102, 'stop'),
                ('chat', 'tool', 'x' * 5000, 103, None),
                ('chat', 'user', '✔ [default] @default Kanban t_2 done', 201, None),
                ('chat', 'assistant', 'Checked.', 202, 'stop')):
            conn.execute("INSERT INTO messages (session_id, role, content, timestamp, finish_reason) "
                         "VALUES (?,?,?,?,?)", (sid, role, text, at, finish))
    monkeypatch.setattr(plugin, 'state_dbs', lambda: [path])
    monkeypatch.setattr(plugin, '_compression_tip', lambda sid: None)
    return path


def test_a_pass_reads_a_lineage_transcript_once_per_lower_since(chat, monkeypatch):
    seen = _queries(monkeypatch)
    proofs = [('t_2', 200), ('t_1', 100), ('t_1', 150), ('t_3', 120), ('t_2', 100), ('t_1', 50)]
    alone = [plugin.session_proof('chat', tid, {'completed_at': at}) for tid, at in proofs]
    assert alone == [True, True, False, False, True, True]
    assert len(seen) == len(proofs)  # outside a pass: one read per proof, as before
    seen.clear()
    with plugin._proof_pass():
        assert [plugin.session_proof('chat', tid, {'completed_at': at}) for tid, at in proofs] == alone
    assert len(seen) == 3  # since 200, then the older slices from 100 and from 50; never a row twice
    assert all('role IN' in sql for sql in seen)  # tool output (the bulk of a transcript) is never read


def test_the_waker_tick_shares_one_pass(chat, tmp_path, monkeypatch):
    """Every row's ack proofs, session source and last-user read reuse one connection per state.db per tick."""
    led = core.Ledger(tmp_path / 'ledger.db')
    for n in range(3):
        led.write(f'chat{n}' if n else 'chat', batch=[f'default/t_{n}', 'default/t_open'], open=[])
    board = tmp_path / 'kanban.db'
    with sqlite3.connect(board) as db:
        db.execute('CREATE TABLE tasks (id TEXT, title TEXT, status TEXT, completed_at REAL)')
        db.executemany('INSERT INTO tasks VALUES (?,?,?,?)',
                       [(f't_{n}', f'card {n}', 'done', 100.0) for n in range(3)] + [('t_open', 'w', 'running', None)])
    opens = []
    real = core.ro_connect
    monkeypatch.setattr(core, 'ro_connect', lambda p, *a, **k: opens.append(str(p)) or real(p, *a, **k))
    monkeypatch.setattr(plugin, 'ledger', lambda: led)
    monkeypatch.setattr(plugin, 'board_paths', lambda: [board])
    monkeypatch.setattr(plugin, 'remote_cards', lambda: [])
    in_pass = []
    real_proof = plugin.session_proof
    monkeypatch.setattr(plugin, 'session_proof',
                        lambda *a: in_pass.append(plugin._pass_local.res is not None) or real_proof(*a))
    assert plugin.wake_tick(now=10_000, wake=lambda *a, **k: 'busy', unseen={}) == []
    assert in_pass == [True, True, True]
    assert opens.count(str(chat)) == 1
    assert plugin._pass_local.res is None
    rows = {r['session_id']: r['batch'] for r in led.pending()}
    assert rows == {'chat': ['default/t_0', 'default/t_open'], 'chat1': ['default/t_1', 'default/t_open'],
                    'chat2': ['default/t_2', 'default/t_open']}  # nothing acked: no notice for these cards
