"""A source write reaches the published panel after its own work, never after the ceiling sweep.

Live, hub serve 2026-11-22 (acceptance of the change-driven producer): the source files never went quiet, so the
60 s per-chat ceiling made ~40 of 48 watched chats due on every pass; one pass took 196 s (~5 s a chat) and a
comment on a watched chat's card reached the panel 106-186 s after it was written (target: 1 s). The ceiling
work now runs AFTER the pass's event-admitted chats are computed and pushed, a slice at a time, and a new source
write (or a waiting reader) ends the pass after a slice."""
import threading
import time

import pytest

from test_feed import feedmod
from test_selective_producer import WATCHED, load_api, setup, write


def due(f, r, source, calls):
    """Every watched chat is past the ceiling and reconciled once (as after a long pass)."""
    now = {'value': 1000.0}
    f.clock = lambda: now['value']
    f.watch(WATCHED)
    f.tick()
    calls.clear()
    now['value'] += feedmod.PRODUCER_FULL_EVERY_S + 1
    return now


def test_a_write_is_reconciled_computed_and_pushed_before_any_ceiling_chat():
    f, r, source, calls = setup()
    due(f, r, source, calls)
    seen = []

    def produce(sessions):
        calls.append(list(sessions))
        # what the panel already holds at the moment this batch starts
        seen.append(('C7' in f.bundles and f.bundles['C7']['rev'], list(sessions)))
        return set()

    r.reconcile_sources = produce
    before = f.bundles['C7']['rev']
    r.cards['t_C7']['detail']['task']['title'] = 'changed by the write'
    write(r, source, 't_C7', 'C7')
    f.tick()
    assert calls[0] == ['C7'], 'the written chat is reconciled alone, first'
    ceiling = [c for batch in calls[1:] for c in batch]
    assert 'C7' not in ceiling and all(len(batch) <= feedmod.CEILING_CHUNK for batch in calls[1:])
    assert sorted(ceiling) == sorted(set(WATCHED) - {'C7'}), 'with no further write the whole ceiling is served'
    assert seen[1][0] > before, 'its recomputed bundle was pushed before the first ceiling slice began'


def test_ceiling_slices_are_bounded_and_a_new_source_write_ends_the_pass():
    f, r, source, calls = setup()
    now = due(f, r, source, calls)

    def produce(sessions):
        calls.append(list(sessions))
        write(r, source)  # the hub never goes quiet: a write lands during every slice
        return set()

    r.reconcile_sources = produce
    write(r, source)
    f.tick()
    assert [len(c) for c in calls] == [feedmod.CEILING_CHUNK], calls
    assert len(f._ceiling_left) == len(WATCHED) - feedmod.CEILING_CHUNK


def test_no_chat_is_starved_by_the_ceiling_under_continuous_writes():
    f, r, source, calls = setup()
    now = due(f, r, source, calls)

    def produce(sessions):
        calls.append(list(sessions))
        write(r, source)
        return set()

    r.reconcile_sources = produce
    write(r, source)
    for _ in range(len(WATCHED)):
        now['value'] += 0.1  # passes are quick: nobody else becomes due meanwhile
        f.tick()
    done = [c for batch in calls for c in batch]
    assert sorted(done) == sorted(WATCHED), 'every ceiling-due chat is reconciled, once, oldest first'
    assert len(done) == len(set(done))
    assert not f._ceiling_left


def test_a_waiting_reader_ends_the_ceiling_pass_after_one_slice():
    f, r, source, calls = setup()
    due(f, r, source, calls)

    def produce(sessions):
        calls.append(list(sessions))
        f._cold.set()  # a click on a chat with no bundle
        return set()

    r.reconcile_sources = produce
    write(r, source)
    f.tick()
    assert len(calls) == 1 and len(calls[0]) == feedmod.CEILING_CHUNK


def test_left_chats_are_admitted_by_the_next_pass_without_another_write():
    f, r, source, calls = setup()
    due(f, r, source, calls)
    state = {'writes': 1}

    def produce(sessions):
        calls.append(list(sessions))
        if state['writes']:
            state['writes'] -= 1
            write(r, source)
        return set()

    r.reconcile_sources = produce
    write(r, source)
    f.tick()
    first = [c for batch in calls for c in batch]
    assert len(first) == feedmod.CEILING_CHUNK
    f.tick()  # no source write since: the stamp did not move, the chats left are still owed
    assert len([c for batch in calls for c in batch]) > len(first)


def test_wall_time_alone_still_never_sweeps_the_ceiling():
    f, r, source, calls = setup()
    now = due(f, r, source, calls)
    now['value'] += 600
    f.tick()
    assert calls == [], 'the ceiling needs a source write; nothing is an idle poll'


# ── the real loop ──────────────────────────────────────────────────────────────────────────────────────────

def test_real_loop_publishes_a_write_within_a_slice_while_sources_never_go_quiet(monkeypatch):
    """`plugin_api._run` (settle, MIN_GAP, wake, tick, beat) with a producer that costs 40 ms a chat and a
    source written 20 times a second. Before the fix every pass reconciled all 48 chats (~2 s) and the written
    chat waited for the pass in flight plus the next pass's whole producer."""
    api = load_api()
    core = api._load_feed()
    monkeypatch.setattr(core, 'PRODUCER_FULL_EVERY_S', 0.3)
    monkeypatch.setattr(api, 'write_timing', lambda f, path=None: True)
    monkeypatch.setattr(api, 'save_snapshot', lambda *a, **k: True)
    chats = [f'C{i}' for i in range(48)]
    from test_feed import Readers
    r = Readers()
    source = {'version': 1}
    calls = []
    r.reconcile_scoped = True
    r.reconciliation_stamp = lambda: source['version']
    r.panel_files_stamp = lambda: r.stamp
    r.panel_stamps = lambda ids: {s: 0 for s in ids}

    def produce(sessions):
        calls.append((time.monotonic(), list(sessions)))
        time.sleep(0.04 * len(sessions))
        return set()

    r.reconcile_sources = produce
    for s in chats:
        r.links[s] = [{'id': f't_{s}', 'status': 'running', 'board': 'default', 'session_id': s}]
        r.cards[f't_{s}'] = {'detail': {'task': {'id': f't_{s}', 'title': 'v0', 'status': 'running', 'session_id': s},
                                        'links': {'parents': [], 'children': []}, 'link_tasks': [], 'events': []},
                             'board': 'default'}
    f = core.Feed(r, emit=lambda *_: None)
    f.watch(chats)
    stop = threading.Event()

    def writes():
        while not stop.wait(0.05):
            source['version'] += 1
            r.stamp += 1
            f._wake.set()

    thread = threading.Thread(target=api._run, args=(f,), daemon=True)
    writer = threading.Thread(target=writes, daemon=True)
    try:
        thread.start()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and len(f.bundles) < len(chats):
            time.sleep(0.05)
        assert len(f.bundles) == len(chats)
        writer.start()
        steady = time.monotonic()
        time.sleep(1.0)  # the ceiling sweep is under way: every chat is permanently past 0.3 s
        worst = 0.0
        for n in range(4):
            target = chats[7 + 9 * n]
            before = f.bundles[target]['rev']
            r.cards[f't_{target}']['detail']['task']['title'] = f'v{n + 1}'
            started = time.monotonic()
            with f.lock:
                pass
            r.events.append((f't_{target}', target))
            r.stamp += 1
            source['version'] += 1
            f._wake.set()
            while f.bundles[target]['rev'] == before and time.monotonic() - started < 10:
                time.sleep(0.01)
            worst = max(worst, time.monotonic() - started)
            time.sleep(0.2)
        biggest = max(len(sessions) for at, sessions in calls if at > steady)
        assert worst < 1.0, f'a write took {worst:.2f} s to reach the published panel'
        assert biggest <= max(core.CEILING_CHUNK, 4), f'a producer batch of {biggest} chats'
    finally:
        stop.set()
        f.stopped.set()
        thread.join(5)
