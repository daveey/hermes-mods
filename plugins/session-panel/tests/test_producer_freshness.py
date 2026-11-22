"""A source write reaches the published panel after its own work, never after the ceiling sweep.

Live, hub serve 2026-11-22 (acceptance of the change-driven producer): the source files never went quiet, so the
60 s per-chat ceiling made ~40 of 48 watched chats due on every pass; one pass took 196 s (~5 s a chat) and a
comment on a watched chat's card reached the panel 106-186 s after it was written (target: 1 s).
The production loop now gives ceiling reconciliation its own event-woken worker.
These synthetic regressions do not establish live freshness or CPU acceptance.
"""
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

@pytest.mark.parametrize('producer_cost', [0.04, 0.4])
def test_real_loop_publishes_a_write_within_a_slice_while_sources_never_go_quiet(monkeypatch, producer_cost):
    """Real settle/wake/tick loop, 40ms and 400ms/chat, continuous writes.

    Four-chat synchronous slices fail at 400ms/chat; a fast fake alone conceals
    that head-of-line blocking. Neither fixture measures production CPU cost.
    """
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
        time.sleep(producer_cost * len(sessions))
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
        f._ceiling_thread.join(5)


@pytest.mark.parametrize('async_ceiling', [False, True])
def test_outcome_write_during_compute_is_owed_to_next_source_pass(async_ceiling):
    f, r, source, calls = setup()
    panel = {s: 0 for s in WATCHED}
    r.panel_stamps = lambda ids: {s: panel[s] for s in ids}
    due(f, r, source, calls)
    for i, s in enumerate(WATCHED):
        f._producer_at[s] = 900 + i
    target = WATCHED[-1]
    f._producer_at[target] = f.clock()
    armed = [True]
    compute = f._compute_all

    def computing(*args, **kwargs):
        result = compute(*args, **kwargs)
        if armed[0]:
            armed[0] = False
            panel[target] += 1
            write(r, source)
        return result

    f._compute_all = computing
    if async_ceiling:
        f.start_ceiling()
    try:
        write(r, source, 't_C0', 'C0')
        f.tick()
        calls.clear()
        f.tick()
        assert [target] in calls, f'outcome lost producer admission: {calls}'
    finally:
        f.stopped.set()
        if async_ceiling:
            f._ceiling_thread.join(5)


@pytest.mark.parametrize('target_kind', ['unrelated', 'same', 'ancestor'])
def test_real_loop_write_during_blocked_ceiling_does_not_wait_or_overlap(monkeypatch, target_kind):
    api = load_api()
    monkeypatch.setattr(api, 'write_timing', lambda *a, **k: True)
    monkeypatch.setattr(api, 'save_snapshot', lambda *a, **k: True)
    f, r, source, calls = setup()
    due(f, r, source, calls)
    entered, release = threading.Event(), threading.Event()
    reconciled = threading.Event()
    blocked, other = WATCHED[:2]
    target = other if target_kind != 'same' else blocked
    lineage = r.lineage
    if target_kind == 'ancestor':
        r.lineage = lambda s: [s, 'shared-parent'] if s in (blocked, other) else lineage(s)
        f._producer_lineage.update({s: tuple(r.lineage(s)) for s in (blocked, other)})
    attempts = []

    def produce(sessions):
        ceiling = threading.current_thread().name == 'session-panel-ceiling'
        attempts.append((ceiling, list(sessions)))
        if ceiling and blocked in sessions and not entered.is_set():
            entered.set()
            assert release.wait(8), 'test must release the blocked producer'
        elif target in sessions:
            # No foreground writer may overlap the same owner/ancestor.
            if target_kind != 'unrelated':
                assert release.is_set()
            reconciled.set()
        return set()

    r.reconcile_sources = produce
    f.start_ceiling()
    with f.lock:
        f._ceiling_queue[blocked] = tuple(r.lineage(blocked))
    f._ceiling_wake.set()
    thread = threading.Thread(target=api._run, args=(f,), daemon=True)
    try:
        assert entered.wait(2)
        before = f.bundles[target]['rev']
        r.cards[f't_{target}']['detail']['task']['title'] = 'written while ceiling blocked'
        write(r, source, f't_{target}', target)
        started = time.monotonic()
        f._wake.set()
        thread.start()
        deadline = started + 1
        while f.bundles[target]['rev'] == before and time.monotonic() < deadline:
            time.sleep(0.005)
        assert f.bundles[target]['rev'] > before, 'blocked ceiling delayed board publication'
        assert not release.is_set(), 'freshness must be observed while the producer is still blocked'
        if target_kind != 'unrelated':
            assert target in f._producer_blocked
            assert not reconciled.is_set()
        release.set()
        assert reconciled.wait(3), 'same-owner new work must resume without another source write'
        assert not f._producer_claims[1].intersection({'shared-parent'}) or target_kind != 'ancestor'
    finally:
        release.set()
        f.stopped.set()
        if thread.ident:
            thread.join(5)
        f._ceiling_thread.join(5)


def test_ceiling_worker_does_not_poll_or_publish_and_discards_forgotten_completion():
    f, r, source, calls = setup()
    due(f, r, source, calls)
    entered, release = threading.Event(), threading.Event()
    s = WATCHED[0]

    def produce(sessions):
        calls.append(list(sessions))
        entered.set()
        assert release.wait(3)
        return set()

    r.reconcile_sources = produce
    f.start_ceiling()
    try:
        assert not entered.wait(0.1), 'starting the worker is not source admission'
        with f.lock:
            f._ceiling_queue[s] = tuple(r.lineage(s))
        f._ceiling_wake.set()
        assert entered.wait(2)
        before = (f.rev, dict(f._reconcile_stamps), f._producer_seen)
        with f.lock:
            f._forget(s)
        f.watch([s])
        release.set()
        f.stopped.set()
        f._ceiling_thread.join(3)
        assert f.rev == before[0]
        assert f._producer_seen == before[2]
        assert s not in f._ceiling_at
        assert s not in f._reconcile_stamps
        assert f._ceiling_results.empty()
        assert len(calls) == 1
        assert not f._producer_claims[1]
    finally:
        release.set()
        f.stopped.set()
        f._ceiling_thread.join(3)


def test_failed_ceiling_retries_are_finite_and_stay_off_the_feed_thread():
    f, r, source, calls = setup()
    due(f, r, source, calls)
    target = WATCHED[0]
    attempts = []

    def produce(sessions):
        attempts.append((threading.current_thread().name, list(sessions)))
        return {target}

    r.reconcile_sources = produce
    f.start_ceiling()
    try:
        with f.lock:
            f._ceiling_queue[target] = tuple(r.lineage(target))
        f._ceiling_wake.set()
        deadline = time.monotonic() + 3
        while target not in f._ceiling_failed and time.monotonic() < deadline:
            time.sleep(0.01)
        assert target in f._ceiling_failed
        assert len(attempts) == feedmod.RETRY_STEPS + 1
        f.tick()  # drains completion, but must not re-run a failed slow producer here
        assert len(attempts) == feedmod.RETRY_STEPS + 1
        assert all(name == 'session-panel-ceiling' for name, _ in attempts)
        assert not f._producer_blocked
        assert f._reconcile_retry_at is None
    finally:
        f.stopped.set()
        f._ceiling_thread.join(3)


def test_completion_between_tick_snapshot_and_wake_clear_is_not_lost():
    f, r, source, calls = setup()
    due(f, r, source, calls)
    original = f._tick

    def racing_tick():
        s = WATCHED[0]
        f._ceiling_results.put((s, f._interest_generation.get(s, 0), False))
        f._wake.set()
        return original()

    f._tick = racing_tick
    f.tick()
    assert f._ceiling_results.empty() or f._wake.is_set(), 'completion needs consumption or a surviving wake'


def test_hot_resume_keeps_inflight_owner_claim_and_discards_old_completion():
    f, r, source, calls = setup()
    due(f, r, source, calls)
    entered, release = threading.Event(), threading.Event()
    s = WATCHED[0]
    foreground = []

    def produce(sessions):
        if threading.current_thread().name == 'session-panel-ceiling':
            entered.set()
            assert release.wait(3)
        else:
            foreground.extend(sessions)
        return set()

    r.reconcile_sources = produce
    f.start_ceiling()
    with f.lock:
        f._ceiling_queue[s] = tuple(r.lineage(s))
    f._ceiling_wake.set()
    successor = None
    try:
        assert entered.wait(2)
        successor = feedmod.Feed(r, emit=lambda *_: None, clock=f.clock).resume(f)
        successor.tick()
        assert s not in foreground
        assert s in successor._producer_blocked
        release.set()
        f._ceiling_thread.join(3)
        assert not f._ceiling_thread.is_alive()
        assert s not in f._ceiling_at
        assert f._ceiling_results.empty()
        successor.tick()  # no new write: release must preserve owed work
        assert s in foreground
        assert not successor._producer_blocked
        assert not successor._producer_claims[1]
    finally:
        release.set()
        f.stopped.set()
        if successor:
            successor.stopped.set()
        f._ceiling_thread.join(3)
