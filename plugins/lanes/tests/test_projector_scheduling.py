"""Regression: opposite presentation variants must not serialize two source jobs."""
import threading
import time
from types import SimpleNamespace

from test_projector import module, actual_board
import pytest


def test_fallback_variants_read_snapshot_once(actual_board, monkeypatch):
    api, config = actual_board
    cli = api.core()[2]
    snapshot = cli.snapshot
    calls = []
    from functools import wraps
    @wraps(snapshot)
    def counted(*args, **kwargs):
        calls.append(1)
        return snapshot(*args, **kwargs)
    monkeypatch.setattr(cli, 'snapshot', counted)
    views = api._lane_views(6., '6h')
    assert len(calls) == 1
    assert views['collapsed']['totals'] == views['expanded']['totals']
    assert 'flow' not in views['collapsed']
    assert views['expanded']['flow']['graphs']['project']['total'] == 1


def test_completed_group_expires_and_cursor_digest_is_part_of_identity(monkeypatch):
    supervisor, calls, entered, release = setup_supervisor(monkeypatch, gap=.05)
    try:
        release.set()
        supervisor.submit((6., '6h', False), 1, [1]).result(2)
        supervisor.submit((6., '6h', True), 1, [2]).result(2)
        assert len(calls) == 2  # same generation, different cursor
        time.sleep(.07)
        supervisor.submit((6., '6h', False), 1, [2]).result(2)
        assert len(calls) == 3  # same revision, expired result
    finally:
        release.set(); supervisor.close()


def test_completed_group_rechecks_loaded_identity(monkeypatch):
    supervisor, calls, entered, release = setup_supervisor(monkeypatch, gap=.3)
    try:
        release.set()
        supervisor.submit((6., '6h', False), 1, [1]).result(2)
        # Change the exact helper used by this isolated supervisor module.
        globals_ = supervisor._run.__globals__
        monkeypatch.setattr(globals_['identity'], 'matches', lambda _: False)
        with pytest.raises(globals_['Unavailable'], match='unavailable'):
            supervisor.submit((6., '6h', True), 1, [1]).result(2)
    finally:
        release.set(); supervisor.close()


def setup_supervisor(monkeypatch, gap=.12):
    client = module('projector_client')
    calls = []
    entered, release = threading.Event(), threading.Event()
    class Child:
        session = 'test'
        closed = False
        def close(self):
            self.closed = True
        def request(self, cmd, timeout):
            calls.append((cmd, time.monotonic()))
            entered.set()
            assert release.wait(2)
            now = time.time()
            views = {'collapsed': {'configured': True, 'variant': False},
                     'expanded': {'configured': True, 'variant': True}}
            return {'view': views['expanded' if cmd['params'][2] else 'collapsed'],
                    'views': views, 'revision': cmd['revision'], 'params': cmd['params'],
                    'timing': {'project_start': now, 'project_end': now}, 'projector_pid': 123}
    supervisor = client.Supervisor(SimpleNamespace(MIN_PROJECT_GAP_S=gap), {})
    child = Child()
    monkeypatch.setattr(supervisor, '_configuration', lambda: {'identity': supervisor.manifest, 'local': 'fixture', 'fleet': None, 'mode': 'off'})
    monkeypatch.setattr(supervisor, '_spawn', lambda _: child)
    return supervisor, calls, entered, release


@pytest.mark.parametrize('duration', [.16, .4])
def test_midflight_change_uses_start_gap_not_completion_gap(monkeypatch, duration):
    gap = .3
    supervisor, calls, entered, release = setup_supervisor(monkeypatch, gap=gap)
    try:
        first = supervisor.submit((6., '6h', False), 1, [1])
        assert entered.wait(2)
        second = supervisor.submit((6., '6h', True), 2, [2])
        time.sleep(duration)
        ended = time.monotonic()
        release.set()
        first.result(2)
        second.result(2)
        expected = max(calls[0][1] + gap, ended)
        assert expected - .01 <= calls[1][1] < expected + .12, calls
    finally:
        release.set(); supervisor.close()


def test_burst_starts_at_most_once_per_gap_window(monkeypatch):
    gap = .12
    supervisor, calls, entered, release = setup_supervisor(monkeypatch, gap=gap)
    try:
        release.set()
        for revision in range(5):
            supervisor.submit((6., '6h', True), revision, [revision]).result(2)
        assert len(calls) == 5
        assert all(b[1] - a[1] >= gap - .005 for a, b in zip(calls, calls[1:])), calls
    finally:
        release.set(); supervisor.close()


def test_opposite_expansion_viewers_share_active_job(monkeypatch):
    supervisor, calls, entered, release = setup_supervisor(monkeypatch)
    try:
        collapsed = supervisor.submit((6., '6h', False), 1, [1])
        assert entered.wait(2)
        expanded = supervisor.submit((6., '6h', True), 1, [1])
        release.set()
        assert collapsed.result(2)['view']['variant'] is False
        assert expanded.result(2)['view']['variant'] is True
        assert len(calls) == 1
        assert collapsed.result()['params'] == [6., '6h', False]
        assert expanded.result()['params'] == [6., '6h', True]
    finally:
        release.set(); supervisor.close()


def test_late_variant_same_revision_reuses_bounded_completed_job(monkeypatch):
    supervisor, calls, entered, release = setup_supervisor(monkeypatch, gap=.3)
    try:
        release.set()
        supervisor.submit((6., '6h', False), 1, [1]).result(2)
        assert supervisor.submit((6., '6h', True), 1, [1]).result(2)['view']['variant'] is True
        assert len(calls) == 1
        # Different revision must run fresh and retain the global burst gap.
        supervisor.submit((6., '6h', True), 2, [2]).result(2)
        assert len(calls) == 2
        assert calls[1][1] - calls[0][1] >= .29
    finally:
        release.set(); supervisor.close()
