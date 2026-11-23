"""Source-to-publication freshness must not depend on unrelated readers."""
import threading
import time

from test_feed import Readers, feedmod


def test_changed_chat_publishes_while_other_chat_reader_is_blocked():
    r = Readers()
    entered, release, published = threading.Event(), threading.Event(), threading.Event()
    pushes = []
    f = feedmod.Feed(r, emit=lambda p: (pushes.append(p), published.set()
                        if 'S2' in p['bundles'] else None))
    f.changes(['S1', 'S2'])
    published.clear()
    original = r.linked

    def slow(ids):
        if 'S1' in ids:
            entered.set()
            assert release.wait(5)
        return original(ids)

    r.linked = slow
    r.events = [('t_aaaaaa1', 'S1')]
    r.stamp += 1
    sweep = threading.Thread(target=f.tick)
    sweep.start()
    try:
        assert entered.wait(2)
        r.links['S2'] = [{**r.links['S2'][0], 'status': 'scheduled'}]
        r.events = [('t_dddddd4', 'S2')]
        r.stamp += 1
        # The production source lane must stay independently admissible while tick
        # is inside a reader. Baseline's only board lane is the blocked tick.
        if hasattr(feedmod, 'BoardLane'):
            lane = feedmod.BoardLane(f, lambda: r)
            lane.step()
        else:
            lane = None
        assert published.wait(1), 'chat B status is withheld by chat A/full-pass hydration'
        assert f.bundles['S2']['bundle']['tasks'][0]['status'] == 'scheduled'
    finally:
        release.set()
        sweep.join(5)
        if lane is not None:
            lane.close()


def test_new_blocked_human_card_appears_while_other_chat_reader_blocks():
    from test_feed import detail
    from test_model import human
    r = Readers()
    f = feedmod.Feed(r, emit=lambda p: None)
    f.changes(['S1', 'S2'])
    entered, release = threading.Event(), threading.Event()
    original = r.linked
    def slow(ids):
        if 'S1' in ids:
            entered.set()
            assert release.wait(5)
        return original(ids)
    r.linked = slow
    r.events = [('t_aaaaaa1', 'S1')]
    r.stamp += 1
    sweep = threading.Thread(target=f.tick)
    sweep.start()
    lane = feedmod.BoardLane(f, lambda: r)
    try:
        assert entered.wait(1)
        # New membership, not merely a changed status on an existing card.
        card = {'id': 't_ffffff6', 'session_id': 'S2', 'board': 'default',
                'status': 'blocked', 'title': 'Choose the bounded test',
                'human_request': human(), 'wait': {'wait_state': 'human'}}
        r.links['S2'].append(card)
        r.cards[card['id']] = detail(card['id'], 'blocked', session='S2')
        r.cards[card['id']]['detail']['task'].update(card)
        r.events = [(card['id'], 'S2')]
        r.stamp += 1
        started = time.monotonic()
        lane.step()
        deadline = started + 1
        rows = []
        while time.monotonic() < deadline:
            rows = f.bundles['S2']['bundle']['model']['sections']['needs_you']
            if any(row['id'] == card['id'] for row in rows):
                break
            time.sleep(.01)
        assert any(row['id'] == card['id'] for row in rows), 'new human ask withheld behind unrelated reader'
        assert time.monotonic() - started < 1
        assert not release.is_set()
    finally:
        release.set()
        sweep.join(5)
        lane.close()


def test_terminal_change_after_sweep_read_removes_waiting_and_survives_old_publication():
    from test_feed import detail
    for terminal in ('archived', 'done'):
        r = Readers()
        r.links['S2'] = [{**r.links['S2'][0], 'status': 'scheduled'}]
        r.cards['t_dddddd4'] = detail('t_dddddd4', 'scheduled', session='S2')
        f = feedmod.Feed(r, emit=lambda p: None)
        f.changes(['S2'])
        assert f.bundles['S2']['bundle']['model']['sections']['waiting']
        entered, release = threading.Event(), threading.Event()
        original = r.linked
        def captured_before_change(ids):
            rows = original(ids)
            if threading.current_thread().name == 'stale-terminal-sweep':
                entered.set()
                assert release.wait(5)
            return rows
        r.linked = captured_before_change
        r.events = [('t_dddddd4', 'S2')]
        r.stamp += 1
        sweep = threading.Thread(target=f.tick, name='stale-terminal-sweep')
        sweep.start()
        lane = feedmod.BoardLane(f, lambda: r)
        try:
            assert entered.wait(1)
            # The archive/complete commits just after the older sweep read.
            r.links['S2'] = []
            r.finished['S2'] = [{'id': 't_dddddd4', 'status': terminal, 'board': 'default'}]
            r.cards['t_dddddd4'] = detail('t_dddddd4', terminal, session='S2')
            r.events = [('t_dddddd4', 'S2')]
            r.stamp += 1
            started = time.monotonic()
            settle(lane)
            model = f.bundles['S2']['bundle']['model']
            assert model['sections']['waiting'] == [], 'terminal card remains in Waiting'
            assert time.monotonic() - started < 1
            assert not release.is_set()
            release.set()
            sweep.join(5)
            assert not sweep.is_alive()
            assert f.bundles['S2']['bundle']['model']['sections']['waiting'] == [], \
                'older pre-terminal sweep resurrected Waiting'
        finally:
            release.set()
            sweep.join(5)
            lane.close()


def test_reconciliation_blocks_neither_board_capture_nor_publication():
    r = Readers()
    f = feedmod.Feed(r, emit=lambda p: None)
    f.changes(['S1', 'S2'])
    entered, release = threading.Event(), threading.Event()
    r.reconciliation_stamp = lambda: r.stamp

    def reconcile(sessions):
        entered.set()
        assert release.wait(5)

    r.reconcile_sources = reconcile
    sweep = threading.Thread(target=f.tick)
    sweep.start()
    try:
        assert entered.wait(2)
        r.links['S2'] = [{**r.links['S2'][0], 'status': 'ready'}]
        r.events = [('t_dddddd4', 'S2')]
        r.stamp += 1
        lane = feedmod.BoardLane(f, lambda: r)
        lane.step()
        end = time.monotonic() + 1
        while time.monotonic() < end:
            if f.bundles['S2']['bundle']['tasks'][0]['status'] == 'ready':
                break
            f._board.wait(.01)
        assert f.bundles['S2']['bundle']['tasks'][0]['status'] == 'ready'
    finally:
        release.set()
        sweep.join(5)
        if 'lane' in locals():
            lane.close()


def test_external_source_change_publishes_during_blocked_full_sweep(monkeypatch):
    monkeypatch.setenv('HERMES_PANEL_BOARD_COALESCE', '0')  # legacy subsecond budget
    r = Readers()
    f = feedmod.Feed(r, emit=lambda p: None)
    f.changes(['S1', 'S2'])
    entered, release = threading.Event(), threading.Event()
    external = [0]
    r.external_stamps = lambda: external[0]
    r.external_capture = lambda held: {s: external[0] if s == 'S2' else 0 for s in held}
    lane = feedmod.BoardLane(f, lambda: r)
    settle(lane)
    r.reconciliation_stamp = lambda: r.stamp
    def reconcile(sessions):
        entered.set()
        assert release.wait(5)
    r.reconcile_sources = reconcile
    sweep = threading.Thread(target=f.tick)
    sweep.start()
    try:
        assert entered.wait(1)
        r.links['S2'] = [{**r.links['S2'][0], 'status': 'ready'}]
        external[0] += 1  # ledger/hub write; no local-board stamp or revision moves
        settle(lane)
        assert f.bundles['S2']['bundle']['tasks'][0]['status'] == 'ready'
        assert not release.is_set()
    finally:
        release.set()
        sweep.join(5)
        lane.close()


def test_older_changed_chat_is_not_starved_by_continually_newer_changes():
    r = Readers()
    painted = threading.Event()
    f = feedmod.Feed(r, emit=lambda p: painted.set() if 'S1' in p['bundles'] else None)
    f.changes(['S1', 'S2'])
    painted.clear()
    lane = feedmod.BoardLane(f, lambda: r, capacity=0)
    lane.step()
    r.links['S1'] = [{**r.links['S1'][0], 'status': 'ready'}]
    r.events = [('t_aaaaaa1', 'S1')]
    r.stamp += 1
    lane.step()  # pool occupied: retain this older admission
    lane.capacity = 1
    try:
        for i in range(8):
            r.links['S2'] = [{**r.links['S2'][0], 'status': 'ready' if i % 2 else 'scheduled'}]
            r.events = [('t_dddddd4', 'S2')]
            r.stamp += 1
            lane.step()
            for worker in list(lane.active.values()):
                worker.join(1)
            if painted.is_set():
                break
        assert painted.wait(.1), 'newer arrivals starved the already admitted changed chat'
    finally:
        settle(lane)
        lane.close()


def test_admitted_chats_refill_capacity_while_next_external_capture_blocks():
    r = Readers()
    f = feedmod.Feed(r, emit=lambda p: None)
    f.changes(['S1', 'S2'])
    lane = feedmod.BoardLane(f, lambda: r, capacity=0)
    r.events = [('t_aaaaaa1', 'S1'), ('t_dddddd4', 'S2')]
    lane.step()  # admitted, but no free worker slots yet
    assert lane.pending
    entered, release = threading.Event(), threading.Event()
    r.external_stamps = lambda: 99
    def blocked_capture(held):
        entered.set()
        assert release.wait(5)
        return {s: 0 for s in held}
    r.external_capture = blocked_capture
    r.links['S1'] = [{**r.links['S1'][0], 'status': 'ready'}]
    r.links['S2'] = [{**r.links['S2'][0], 'status': 'ready'}]
    lane.capacity = 1
    if hasattr(lane, 'start_service'):
        lane.start_service()
    capture = threading.Thread(target=lane.step)
    capture.start()
    try:
        assert entered.wait(1)
        end = time.monotonic() + 1
        while time.monotonic() < end:
            if all(f.bundles[s]['bundle']['tasks'][0]['status'] == 'ready' for s in ('S1', 'S2')):
                break
            time.sleep(.01)
        assert all(f.bundles[s]['bundle']['tasks'][0]['status'] == 'ready' for s in ('S1', 'S2')), \
            'slow recapture holds admitted work and prevents completion-driven slot refill'
        assert not release.is_set()
    finally:
        release.set()
        capture.join(5)
        lane.close()


def test_new_local_change_is_admitted_before_blocked_external_capture(monkeypatch):
    monkeypatch.setenv('HERMES_PANEL_BOARD_COALESCE', '0')  # legacy subsecond budget
    """Unlike the refill case, there is NO admitted work before recapture starts."""
    r = Readers()
    emitted = threading.Event()
    f = feedmod.Feed(r, emit=lambda p: emitted.set() if 'S2' in p['bundles'] else None)
    f.changes(['S2'])
    r.external_stamps = lambda: 1
    r.external_capture = lambda held: {s: 0 for s in held}
    lane = feedmod.BoardLane(f, lambda: r)
    settle(lane)
    emitted.clear()
    entered, release = threading.Event(), threading.Event()
    def capture(held):
        entered.set()
        assert release.wait(5)
        return {s: 1 for s in held}
    r.external_capture = capture
    r.links['S2'] = [{**r.links['S2'][0], 'status': 'ready'}]
    r.events = [('t_dddddd4', 'S2')]
    r.stamp += 1
    lane.start_service()
    thread = threading.Thread(target=lane.step)
    thread.start()
    try:
        assert entered.wait(1)
        assert emitted.wait(1), 'known local delta withheld until strict external inventory completes'
        assert f.bundles['S2']['bundle']['tasks'][0]['status'] == 'ready'
        assert not release.is_set()
        # This later external snapshot cannot be treated as covered by the first
        # local read; it must queue a successor even though that chat just emitted.
        r.links['S2'] = [{**r.links['S2'][0], 'status': 'scheduled'}]
        release.set()
        thread.join(5)
        settle(lane)
        assert f.bundles['S2']['bundle']['tasks'][0]['status'] == 'scheduled'
    finally:
        release.set()
        thread.join(5)
        lane.close()


def test_local_admission_keeps_external_failure_retry_and_later_successor(monkeypatch):
    monkeypatch.setenv('HERMES_PANEL_BOARD_COALESCE', '0')  # legacy subsecond budget
    r = Readers()
    now = [1000.0]
    f = feedmod.Feed(r, emit=lambda p: None, clock=lambda: now[0])
    f.changes(['S2'])
    external = [0]
    r.external_stamps = lambda: external[0]
    r.external_capture = lambda held: {s: external[0] for s in held}
    lane = feedmod.BoardLane(f, lambda: r)
    settle(lane)
    cursor, stamp, captures = lane.cursor, lane.stamp, dict(lane.external_captures)
    def fail(held):
        raise RuntimeError('fixture inventory unavailable')
    r.external_capture = fail
    r.links['S2'] = [{**r.links['S2'][0], 'status': 'ready'}]
    r.events = [('t_dddddd4', 'S2')]
    r.stamp += 1
    try:
        lane.step()
        for worker in list(lane.active.values()):
            worker.join(1)
        lane.service()
        assert f.bundles['S2']['bundle']['tasks'][0]['status'] == 'ready'
        assert (lane.cursor, lane.stamp, lane.external_captures) == (cursor, stamp, captures)
        assert lane.capture_retry_at is not None
        assert f.timing['board_captures'][-1]['failed'] is True
        # Recover the SAME source stamp by finite retry, not another write.
        now[0] = lane.capture_retry_at
        r.external_capture = lambda held: {s: external[0] for s in held}
        settle(lane)
        assert lane.capture_failures == 0 and lane.capture_retry_at is None
        assert lane.stamp != stamp
        capture = f.timing['board_captures'][-1]
        assert capture['local_admitted_at'] <= capture['external_admitted_at'] <= capture['finished_at']
        assert capture['external_s'] >= 0
        # A later external-only change must still hydrate the already-admitted chat.
        r.links['S2'] = [{**r.links['S2'][0], 'status': 'scheduled'}]
        external[0] += 1
        settle(lane)
        assert f.bundles['S2']['bundle']['tasks'][0]['status'] == 'scheduled'
    finally:
        lane.close()


def test_failed_capture_cannot_be_vouched_by_earlier_local_read(monkeypatch):
    monkeypatch.setenv('HERMES_PANEL_BOARD_COALESCE', '0')  # legacy subsecond budget
    r = Readers()
    now = [1000.0]
    pushes = []
    f = feedmod.Feed(r, emit=pushes.append, clock=lambda: now[0])
    f.changes(['S1', 'S2'])
    r.external_stamps = lambda: 0
    r.external_capture = lambda held: {s: 0 for s in held}
    lane = feedmod.BoardLane(f, lambda: r)
    settle(lane)
    entered, release = threading.Event(), threading.Event()
    original = r.linked
    def paused(ids):
        rows = original(ids)
        if 'S2' in ids:
            entered.set()
            assert release.wait(5)
        return rows
    def unavailable(held):
        assert entered.wait(1)
        raise RuntimeError('fixture external source unavailable')
    r.linked = paused
    r.external_capture = unavailable
    r.links['S2'] = [{**r.links['S2'][0], 'status': 'ready'}]
    r.events = [('t_dddddd4', 'S2')]
    r.stamp += 1
    try:
        lane.step()
        assert lane.capture_failures == 1
        release.set()
        for worker in list(lane.active.values()):
            worker.join(1)
        lane.service()
        assert f.bundles['S2']['bundle']['tasks'][0]['status'] == 'ready'
        now[0] += f.heartbeat_s + 1
        f._publish({}, heartbeat=True)
        assert 'S2' not in pushes[-1]['fresh'], 'early hydration erased a later capture failure'
        assert 'S1' not in pushes[-1]['fresh'], 'unaffected held chat vouched during capture outage'
        f._publish(f._compute_all(['S1', 'S2']))
        now[0] += f.heartbeat_s + 1
        f._publish({}, heartbeat=True)
        assert not {'S1', 'S2'} & set(pushes[-1]['fresh']), 'full sweep cleared capture custody'
        # A successful same-source recapture restores only the capture fence;
        # no extra source write is required and no board clocks are fabricated.
        r.linked = original
        r.external_capture = lambda held: {s: 0 for s in held}
        before = f.bundles['S1']['bundle']['computed_at']
        lane.step()
        assert lane.capture_failures == 0
        now[0] += f.heartbeat_s + 1
        f._publish({}, heartbeat=True)
        assert {'S1', 'S2'} <= set(pushes[-1]['fresh'])
        assert f.bundles['S1']['bundle']['computed_at'] == before
    finally:
        release.set()
        lane.close()


def test_capture_failure_custody_survives_resume_and_ends_at_forget():
    r = Readers()
    now = [1000.0]
    pushes = []
    old = feedmod.Feed(r, emit=pushes.append, clock=lambda: now[0])
    old.changes(['S2'])
    old._board_capture_failures.add('S2')
    old.stopped.set()
    f = feedmod.Feed(r, emit=pushes.append, clock=lambda: now[0]).resume(old)
    f.tick()
    now[0] += f.heartbeat_s + 1
    f._publish({}, heartbeat=True)
    assert 'S2' not in pushes[-1]['fresh']
    assert f._board_capture_failures == {'S2'}
    with f.lock:
        f._forget('S2')
    assert not f._board_capture_failures
    f.stopped.set()


def test_emit_timing_names_exact_model_and_omits_failed_transport():
    pushed = []
    r = Readers()
    f = feedmod.Feed(r, emit=pushed.append)
    f.changes(['S2'])
    record = f.timing['recent'][-1]
    wire = pushed[-1]
    assert record['rev'] == wire['rev']
    assert record['models']['S2'] == {
        'revision': wire['bundles']['S2']['model']['revision'],
        'computed_seq': f.bundles['S2']['bundle']['computed_seq'],
        'computed_at': f.bundles['S2']['bundle']['computed_at']}
    previous = list(f.timing['recent'])
    def offline(payload):
        raise RuntimeError('fixture transport offline')
    f.emit = offline
    r.links['S2'] = [{**r.links['S2'][0], 'status': 'ready'}]
    f._publish({'S2': f.compute('S2', base=f.bundles['S2']['bundle'])})
    assert f.timing['recent'] == previous


def test_capacity_service_registration_respects_stop_and_single_owner():
    r = Readers()
    f = feedmod.Feed(r, emit=lambda p: None)
    f._board_service_required = True
    f.stopped.set()
    lane = feedmod.BoardLane(f, lambda: r)
    lane.start_service()
    assert lane.service_worker is None
    lane.close()

    f = feedmod.Feed(r, emit=lambda p: None)
    lane = feedmod.BoardLane(f, lambda: r)
    lane.close()
    lane.start_service()
    assert lane.service_worker is None

    lane = feedmod.BoardLane(f, lambda: r)
    try:
        lane.start_service()
        worker = lane.service_worker
        lane.start_service()
        assert lane.service_worker is worker and worker.is_alive()
    finally:
        lane.close()
        assert not worker.is_alive()


def settle(lane, timeout=2):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        lane.step()
        if not lane.active and not lane.pending:
            return
        lane.feed._board.wait(.01)
    raise AssertionError('board workers did not settle')


def test_slow_lane_worker_does_not_block_other_changed_chat(monkeypatch):
    monkeypatch.setenv('HERMES_PANEL_BOARD_COALESCE', '0')  # legacy parallel capacity
    r = Readers()
    f = feedmod.Feed(r, emit=lambda p: None)
    f.changes(['S1', 'S2'])
    entered, release = threading.Event(), threading.Event()
    original = r.linked
    def slow(ids):
        if 'S1' in ids:
            entered.set()
            assert release.wait(5)
        return original(ids)
    r.linked = slow
    r.events = [('t_aaaaaa1', 'S1')]
    lane = feedmod.BoardLane(f, lambda: r)
    try:
        lane.step()
        assert entered.wait(1)
        r.links['S2'] = [{**r.links['S2'][0], 'status': 'ready'}]
        r.events = [('t_dddddd4', 'S2')]
        r.stamp += 1
        lane.step()
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline and f.bundles['S2']['bundle']['tasks'][0]['status'] != 'ready':
            f._board.wait(.01)
        assert f.bundles['S2']['bundle']['tasks'][0]['status'] == 'ready'
        assert not release.is_set()
    finally:
        release.set()
        settle(lane)
        lane.close()


def test_board_lane_carries_canonical_component_and_rejects_old_sweep():
    r = Readers()
    f = feedmod.Feed(r, emit=lambda p: None)
    f.changes(['S2'])
    old = f.bundles['S2']['bundle']
    current = {**old, 'canonical_seq': next(f._seq), 'requests': [], 'outcome_json': {'marker': 'current'}}
    f._publish({'S2': current}, canonical_only=True)
    # Any canonical read or source reconciliation here is a bug, not a cheap fallback.
    def forbidden(*args):
        raise AssertionError('board-only lane read canonical inputs')
    r.panel_inputs = forbidden
    r.reconcile_sources = forbidden
    r.links['S2'] = [{**r.links['S2'][0], 'status': 'ready'}]
    r.events = [('t_dddddd4', 'S2')]
    lane = feedmod.BoardLane(f, lambda: r)
    try:
        settle(lane)
        b = f.bundles['S2']['bundle']
        assert b['canonical_seq'] == current['canonical_seq']
        assert b['outcome_json'] == current['outcome_json']
        assert b['tasks'][0]['status'] == 'ready'
        f._publish({'S2': old})
        assert f.bundles['S2']['bundle']['tasks'][0]['status'] == 'ready'
    finally:
        lane.close()


def test_forget_rewatch_and_stop_fence_inflight_publication():
    r = Readers()
    f = feedmod.Feed(r, emit=lambda p: None)
    f.changes(['S2'])
    base = f.bundles['S2']['bundle']
    token = f._watch_tokens['S2']
    candidate = f.compute('S2', base=base)
    with f.lock:
        f._forget('S2')
    f.changes(['S2'])
    assert f._publish({'S2': candidate}, watch_tokens={'S2': token}) == {}
    token = f._watch_tokens['S2']
    f.stopped.set()
    assert f._board.is_set()
    assert f._publish({'S2': candidate}, watch_tokens={'S2': token}) == {}


def test_event_during_worker_is_coalesced_into_one_followup_and_idle_does_no_db_capture(monkeypatch):
    monkeypatch.setenv('HERMES_PANEL_BOARD_COALESCE', '0')  # legacy subsecond budget
    r = Readers()
    f = feedmod.Feed(r, emit=lambda p: None)
    f.changes(['S2'])
    entered, release = threading.Event(), threading.Event()
    original = r.linked
    reads = []
    def slow(ids):
        rows = original(ids)
        reads.append(1)
        if len(reads) == 1:
            entered.set()
            assert release.wait(5)
        return rows
    r.linked = slow
    r.events = [('t_dddddd4', 'S2')]
    lane = feedmod.BoardLane(f, lambda: r)
    try:
        lane.step()
        assert entered.wait(1)
        for status in ('scheduled', 'blocked', 'ready'):
            r.links['S2'] = [{**r.links['S2'][0], 'status': status}]
            r.events = [('t_dddddd4', 'S2')]
            r.stamp += 1
            lane.step()
        assert len(reads) == 1
        release.set()
        settle(lane)
        assert len(reads) == 2
        assert f.bundles['S2']['bundle']['tasks'][0]['status'] == 'ready'
        rev = f.rev
        r.changed_cards = lambda cursor: (_ for _ in ()).throw(AssertionError('idle source capture'))
        lane.step()
        assert f.rev == rev
    finally:
        release.set()
        lane.close()


def test_capture_failure_has_finite_retry_and_new_source_recovers():
    r = Readers()
    now = [1000.]
    f = feedmod.Feed(r, emit=lambda p: None, clock=lambda: now[0])
    f.changes(['S2'])
    calls = []
    original = r.changed_cards
    def fail(cursor):
        calls.append(cursor)
        raise RuntimeError('source unavailable')
    r.changed_cards = fail
    lane = feedmod.BoardLane(f, lambda: r)
    try:
        lane.step()
        for _ in range(3):
            now[0] = lane.retry_at()
            lane.step()
        assert len(calls) == 4 and lane.retry_at() is None
        assert lane.cursor is None
        assert 'S2' in f._board_capture_failures
        for _ in range(10):
            now[0] += 100
            lane.step()
        assert len(calls) == 4, 'time/worker completion cannot renew an exhausted source retry'
        r.changed_cards = original
        r.stamp += 1
        r.links['S2'] = [{**r.links['S2'][0], 'status': 'ready'}]
        r.events = [('t_dddddd4', 'S2')]
        settle(lane)
        assert lane.cursor is not None
        assert 'S2' not in f._board_failures
        assert 'S2' not in f._board_capture_failures
        assert f.bundles['S2']['bundle']['tasks'][0]['status'] == 'ready'
    finally:
        lane.close()


def test_actual_board_loop_wakes_on_source_event_and_stops(monkeypatch):
    import importlib.util
    import sys
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / 'dashboard' / 'plugin_api.py'
    spec = importlib.util.spec_from_file_location('_board_loop_api', path)
    api = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = api
    spec.loader.exec_module(api)
    r = Readers()
    f = feedmod.Feed(r, emit=lambda p: None)
    f.changes(['S2'])
    monkeypatch.setattr(api, 'CoreReaders', lambda: r)
    monkeypatch.setattr(api, 'write_timing', lambda f: True)
    thread = threading.Thread(target=api._run_board, args=(f, feedmod.BoardLane))
    thread.start()
    try:
        r.links['S2'] = [{**r.links['S2'][0], 'status': 'ready'}]
        r.events = [('t_dddddd4', 'S2')]
        r.stamp += 1
        f._board.set()
        # The default-on mitigation admits a trailing source pass at >=3s.
        # Keep this real-thread wake test valid regardless of the startup race.
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and f.bundles['S2']['bundle']['tasks'][0]['status'] != 'ready':
            f._board.wait(.01)
        assert f.bundles['S2']['bundle']['tasks'][0]['status'] == 'ready'
    finally:
        f.stopped.set()
        thread.join(3)
        assert not thread.is_alive()
