"""A feed pass shares card reads across its chunks. [daveey]

Hub 2026-11-26 (71 watched chats, 16 per chunk): 738 card reads for 439 distinct cards. Nothing here outlives
the pass.
"""
import contextlib

from test_core_readers import api, board  # noqa: F401  (fixtures)
from test_core_readers import kanban_db
from test_reader_batch import graph, stable  # noqa: F401  (fixture)


def _clockless(value):
    """`stable` plus execution-observation age and validity, which move with the wall clock between the two reads."""
    value = stable(value)
    if isinstance(value, dict):
        return {k: _clockless(v) for k, v in value.items() if k not in ('age_seconds', 'valid_until')}
    if isinstance(value, list):
        return [_clockless(v) for v in value]
    return value


def _diff(a, b, key=''):
    if isinstance(a, dict) and isinstance(b, dict):
        return [d for k in a.keys() | b.keys() for d in _diff(a.get(k), b.get(k), f'{key}/{k}')]
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        return [d for i, (x, y) in enumerate(zip(a, b)) for d in _diff(x, y, f'{key}/{i}')]
    return [] if a == b else [(key, a, b)]


def _pass(r):
    """The reader's pass scope; old readers have none (their chunks then read alone, as before)."""
    return getattr(r, 'pass_scope', contextlib.nullcontext)()


def _count_card_reads(r, monkeypatch):
    calls = []
    original = r._card
    monkeypatch.setattr(r, '_card', lambda *a: calls.append(a) or original(*a))
    return calls


def test_a_card_held_by_chats_in_two_chunks_is_read_once_a_pass(graph, monkeypatch):
    r, local, _, _ = graph
    calls = _count_card_reads(r, monkeypatch)
    with _pass(r):
        with r.batch(['S0', 'S1']):
            first = r.card(local, 'default')
        with r.batch(['S2', 'S3']):
            second = r.card(local, 'default')
    assert first == second and first is not second
    assert len(calls) == 1, calls


def test_the_next_pass_reads_again(graph, monkeypatch):
    r, local, _, _ = graph
    calls = _count_card_reads(r, monkeypatch)
    for _ in range(2):
        with _pass(r):
            with r.batch(['S0', 'S1']):
                r.card(local, 'default')
    assert len(calls) == 2


def test_shared_records_are_copies_and_misses_are_not_shared(graph, monkeypatch):
    r, local, _, _ = graph
    calls = []
    original = r._card
    def flaky(*a):
        calls.append(a)
        return None if len(calls) == 1 else original(*a)
    monkeypatch.setattr(r, '_card', flaky)
    with _pass(r):
        with r.batch(['S0', 'S1']):
            assert r.card(local, 'default') is None
        with r.batch(['S2', 'S3']):
            rec = r.card(local, 'default')
            rec['detail']['task']['title'] = 'mutated by a reader'
        with r.batch(['S4', 'S5']):
            assert r.card(local, 'default')['detail']['task']['title'] != 'mutated by a reader'
    assert len(calls) == 2


def test_a_feed_tick_reads_a_shared_card_once_across_its_chunks(graph, api, monkeypatch):
    r, local, _, _ = graph
    core = api._load_feed()
    monkeypatch.setattr(core, 'CHUNK', 2)
    sessions = [f'S{i}' for i in range(6)]
    f = core.Feed(r, emit=lambda _: None)
    f.watch(sessions)
    calls = _count_card_reads(r, monkeypatch)
    f.tick()
    assert {s for s in sessions if s in f.bundles} == set(sessions), f.failures
    reads = [a for a in calls if a[0] == local]
    assert len(reads) == 1, len(reads)
    # The same bundles as chats read one chunk at a time with no shared pass.
    alone = core.Feed(r, emit=lambda _: None)
    for s in sessions:
        with r.batch([s]):
            mine = alone.compute(s)
        held = f.bundles[s]['bundle']
        assert _diff(_clockless(mine['tasks']), _clockless(held['tasks'])) == []
        assert _diff(_clockless(mine['cards']), _clockless(held['cards'])) == []


def test_an_in_place_write_between_chunks_is_refreshed_by_the_next_tick(graph, board, api, monkeypatch):
    """The pass_scope contract: a content write during a pass (same file, same inode) may leave later chunks of THAT
    pass on the card read earlier in it, and the next tick re-dirties and refreshes every chat holding the card."""
    r, local, _, _ = graph
    path, c = board
    core = api._load_feed()
    monkeypatch.setattr(core, 'CHUNK', 2)
    sessions = [f'S{i}' for i in range(6)]
    f = core.Feed(r, emit=lambda _: None)
    f.watch(sessions)
    inode = path.stat().st_ino
    original = f._compute_all
    chunks = []

    def compute_all(chunk, **kwargs):
        out = original(chunk, **kwargs)
        chunks.append(list(chunk))
        if len(chunks) == 1:
            c.execute('UPDATE tasks SET title=? WHERE id=?', ('Retitled mid-pass', local))
            c.commit()
        return out
    monkeypatch.setattr(f, '_compute_all', compute_all)
    f.tick()
    assert len(chunks) >= 2 and path.stat().st_ino == inode

    def title(s):
        return f.bundles[s]['bundle']['cards'][local]['detail']['task']['title']
    assert {title(s) for s in sessions} == {'Shared local ready card'}  # one read for the whole pass
    chunks.clear()
    f.tick()
    assert sorted(s for chunk in chunks for s in chunk) == sessions  # every holder re-dirtied
    assert {title(s) for s in sessions} == {'Retitled mid-pass'}
    assert kanban_db.get_task(c, local).title == 'Retitled mid-pass'
