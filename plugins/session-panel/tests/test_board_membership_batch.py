"""A board-lane pass reads local card membership once per board and status set, not once per chat.

On the hub a 16-chat pass called core's membership selector 340 times (~4,000 statements, 18% of the serve's
sampled thread time sat in `_session_tasks`). The pass now reads every chat's (session, card) pairs once and derives
each lineage's answer; these tests hold that answer to the per-lineage selector exactly (order and `via`)."""
import itertools
import sqlite3

import pytest

from test_core_readers import api, board, kanban_db  # noqa: F401  (fixtures)
from test_reader_batch import graph, stable  # noqa: F401  (fixture)


SESSIONS = [f'S{i}' for i in range(6)]


def unclocked(value):
    """`stable` minus the evidence expiry, which core stamps from the read's own clock."""
    if isinstance(value, dict):
        return {k: unclocked(v) for k, v in stable(value).items() if k not in ('valid_until', 'age_seconds')}
    if isinstance(value, (list, tuple)):
        return [unclocked(v) for v in value]
    return value


@pytest.fixture
def mixed(graph, board, monkeypatch):
    """The graph board plus cards created by, link-created for and subscribed by different chats, in every listed and
    terminal status, and a two-chat lineage (S5's parent is S4)."""
    from hermes_cli import kanban_origin, profiles
    r, local, remote, ksa = graph
    path, c = board
    monkeypatch.setattr(kanban_origin, 'gateway_names', lambda *a, **k: ('local-test',))
    monkeypatch.setattr(profiles, 'get_active_profile_name', lambda *a, **k: 'default')
    statuses = ('ready', 'running', 'todo', 'blocked', 'triage', 'scheduled', 'done', 'archived')
    cards = []
    for i in range(24):
        creator = SESSIONS[i % 5] if i % 7 else None
        cid = kanban_db.create_task(c, title=f'Mixed {i}', session_id=creator, initial_status='running')
        c.execute('UPDATE tasks SET status=? WHERE id=?', (statuses[i % len(statuses)], cid))
        cards.append(cid)
    links = c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_chat_links'").fetchone()
    for i, cid in enumerate(cards):
        # Subscriptions from other chats (and some from the creator itself), out of creation order.
        for j in (i * 5 % 6, (i + 3) % 6):
            c.execute("INSERT INTO kanban_notify_subs (task_id, platform, chat_id, created_at) VALUES (?, 'tui', ?, ?)",
                      (cid, SESSIONS[j], 100 - i))
        if links and i % 3 == 0:
            c.execute('INSERT INTO task_chat_links (uid, task_id, chat_gateway, chat_profile, chat_session, kind, '
                      "released, created_at) VALUES (?, ?, 'local-test', 'default', ?, 'created', 0, 1)",
                      (f'u{i}', cid, SESSIONS[(i + 1) % 6]))
    c.commit()
    with sqlite3.connect(path.parent / 'state.db') as state:
        state.executemany('INSERT OR IGNORE INTO sessions VALUES (?, NULL)', [(s,) for s in SESSIONS])
        state.execute("UPDATE sessions SET parent_session_id='S4' WHERE id='S5'")
    ksa._reset_cache_for_tests()
    return r, cards, ksa, path, c


def _statuses(ksa):
    listed = tuple(ksa.LISTED_STATUSES)
    return listed, listed + ('done', 'archived')


def test_lineage_members_equal_the_per_lineage_selector_in_order_and_via(mixed, api):
    from hermes_cli.kanban_session_outcome_tasks import select_listed_local_tasks, select_listed_local_task_set
    r, cards, ksa, path, c = mixed
    c.row_factory = sqlite3.Row
    lineages = [list(x) for n in (1, 2, 3) for x in itertools.combinations(SESSIONS + ['absent'], n)]
    union = list(dict.fromkeys(i for lineage in lineages for i in lineage))
    compared = 0
    for statuses in _statuses(ksa):
        pairs = select_listed_local_task_set(c, union, statuses, strict=False)[1]
        assert {via for via in pairs.values()} == {'created', 'subscribed'}
        for lineage in lineages:
            expected = [(tid, m['via']) for tid, m in select_listed_local_tasks(c, lineage, statuses).items()]
            actual = [(tid, m['via']) for tid, m in api._lineage_members(pairs, lineage).items()]
            assert actual == expected, lineage
            compared += bool(expected)
    assert compared > 50


def test_pass_reads_membership_once_per_status_set_with_unchanged_answers(mixed, monkeypatch):
    import hermes_cli.kanban_session_outcome_tasks as kot
    r, cards, ksa, path, c = mixed
    chats = ['S0', 'S1', 'S2', 'S3', 'S5'] + [f'S{i}' for i in range(6, 23)]
    lineage = {s: r.lineage(s) for s in chats}
    assert lineage['S5'] == ['S5', 'S4']
    baseline = {s: (r.linked(lineage[s]), r.done(lineage[s])) for s in chats}
    assert sum(len(linked) + len(done) for linked, done in baseline.values()) > 40
    calls, statements = [], []
    original = kot.select_listed_local_task_set

    def counted(conn, ids, *a, **k):
        calls.append(list(ids))
        conn.set_trace_callback(statements.append)
        try:
            return original(conn, ids, *a, **k)
        finally:
            conn.set_trace_callback(None)

    monkeypatch.setattr(kot, 'select_listed_local_task_set', counted)
    with r.batch(chats):
        union = list(r._local.batch['ids'])
        actual = {s: (r.linked(lineage[s]), r.done(lineage[s])) for s in chats}
    assert unclocked(actual) == unclocked(baseline)
    assert [t['via'] for t in actual['S0'][0]] == [t['via'] for t in baseline['S0'][0]]
    # Per status set, core's union hydration selects once and the pass's membership read selects once, both over
    # the pass's ids: 4 calls for 22 chats, where the per-chat loop added one call per chat and status set (46).
    assert calls == [union] * 4, [len(ids) for ids in calls]
    # ~11 statements per selector call: ~44 for the pass (the per-chat loop issued ~500).
    assert len(statements) <= 4 * 12, len(statements)
