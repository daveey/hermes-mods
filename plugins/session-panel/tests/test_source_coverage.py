"""Change admission must survive failed captures and late source creation."""
import importlib.util
import sys
from pathlib import Path

import pytest

from test_feed import make


def api_module():
    name = '_panel_source_coverage_api'
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[1] / 'dashboard/plugin_api.py')
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_failed_panel_capture_does_not_consume_file_change():
    f, r, _, _ = make()
    f.changes(['S1'])
    r.panel_files_stamp = lambda: 'source-revision-1'
    r.panel_stamps = lambda ids: {sid: ('first', ()) for sid in ids}
    f._panel_dirty(['S1'])
    old = dict(f._panel_stamps)
    r.panel_files_stamp = lambda: 'source-revision-2'

    def fail(ids):
        raise RuntimeError('source busy')

    r.panel_stamps = fail
    with pytest.raises(RuntimeError, match='source busy'):
        f._panel_dirty(['S1'])
    assert f._panel_files == 'source-revision-1'
    assert f._panel_stamps == old
    r.panel_stamps = lambda ids: {sid: ('second', ()) for sid in ids}
    assert f._panel_dirty(['S1']) == {'S1'}


def test_missing_parent_creation_is_an_immediate_watch_event(tmp_path):
    api = api_module()
    w = api.FileWatch()
    if not w.ok or w._libc is None:
        w.close()
        pytest.skip('inotify only')
    db = tmp_path / 'late-profile' / 'user-steps' / 'actions.sqlite3'
    try:
        w.set_paths([db])
        assert not w.wait(0.01)
        db.parent.parent.mkdir()
        assert w.wait(0.2), 'creation must not wait for the 20-second inventory timer'
        assert w.stale
        w.set_paths([db])
        db.parent.mkdir()
        assert w.wait(0.2)
        w.set_paths([db])
        db.write_bytes(b'source created')
        assert w.wait(0.2)
    finally:
        w.close()


def test_missing_sources_remain_in_watch_inventory(tmp_path):
    from types import SimpleNamespace
    import threading
    api = api_module()
    # No core dependency: exercise the real inventory method against a supplied
    # filesystem-only board discovery adapter, including missing replicas.
    r = object.__new__(api.CoreReaders)
    r._state_db = tmp_path / 'state.db'
    r._boards_lock = threading.Lock()
    r._boards = []
    r._boards_at = 0
    board = tmp_path / 'boards' / 'late' / 'kanban.db'
    shadow = tmp_path / 'replicas' / 'late.db'
    r.ksc = SimpleNamespace(board_paths=lambda: [('late', board)], shadow_path=lambda slug: shadow)
    r.external_paths = lambda: [tmp_path / 'origin-ledger.db']
    paths = r.watch_paths()
    assert board in paths and shadow in paths
    assert tmp_path / 'origin-ledger.db' in paths
    assert r.state_db() in paths
    assert tmp_path / 'user-steps' / 'actions.sqlite3' in paths


def test_inventory_directory_entry_changes_rearm_without_content_polling(tmp_path):
    api = api_module()
    w = api.FileWatch()
    if not w.ok or w._libc is None:
        w.close()
        pytest.skip('inotify only')
    # The autouse fixture puts HERMES_HOME below tmp_path. Core-backed suite
    # workers may create it asynchronously; that is a real directory-entry event,
    # not content polling. Watch only the inventory this test owns.
    inventory = tmp_path / 'inventory'
    inventory.mkdir()
    try:
        w.set_paths([], directories=[inventory])
        board = inventory / 'new-board'
        board.mkdir()
        assert w.wait(0.2) and w.stale
        w.set_paths([board / 'kanban.db'], directories=[inventory, board])
        (board / 'kanban.db').write_bytes(b'new board source')
        assert w.wait(0.2)
        w.set_paths([board / 'kanban.db'], directories=[inventory, board])
        # Drain creation, then prove an existing unrelated file's contents do
        # not keep either producer busy through inventory wildcard coverage.
        log = inventory / 'unrelated.log'
        log.write_bytes(b'created')
        assert w.wait(0.2)
        w.set_paths([board / 'kanban.db'], directories=[inventory, board])
        while w.wait(0.01):
            pass
        # Deterministically exercise sibling fixture activity as well as log
        # content changes; neither belongs to this watch's source inventory.
        (tmp_path / 'sibling-home').mkdir()
        log.write_bytes(b'changed only log contents')
        assert not w.wait(0.05), (w.changed_paths, w.source_changed, w.stale)
    finally:
        w.close()


@pytest.mark.parametrize('notification', ['noise', 'source', 'new_board'])
def test_inventory_noise_cannot_wake_producers_each_idle_minute(tmp_path, monkeypatch, notification):
    from types import SimpleNamespace
    api = api_module()
    f, r, _, _ = make()
    board = tmp_path / 'kanban.db'
    board.write_bytes(b'held source')
    r.watch_paths = lambda: sorted(tmp_path.glob('*.db'))
    r.watch_directories = lambda: [tmp_path]
    f.stopped = __import__('threading').Event()  # isolate producer wakes from Feed.stop's shutdown fanout
    f._board = __import__('threading').Event()
    wakes = []
    for name in ('_wake', '_decide', '_board'):
        monkeypatch.setattr(getattr(f, name), 'set', lambda n=name: wakes.append(n))
    monkeypatch.setattr(f.stopped, 'wait', lambda *_: False)
    monkeypatch.setattr(api, '_mitigation', lambda *_: False)
    seconds = [0.0]
    monkeypatch.setattr(api, 'time', SimpleNamespace(monotonic=lambda: seconds[0]))
    native = api.FileWatch()
    if not native.ok or native._libc is None:
        native.close()
        pytest.skip('inotify only')
    wait = native.wait
    def next_event(timeout):
        seconds[0] += 1
        if seconds[0] > 60:
            f.stopped.set()
            return False
        # Sidecar/temp lifecycle is generated by readers and our publications.
        noise = tmp_path / ('kanban.db-shm' if seconds[0] % 2 else 'own.tmp')
        noise.write_bytes(b'noise')
        noise.unlink()
        if seconds[0] == 30 and notification == 'source':
            board.write_bytes(b'genuine concurrent source change')
        if seconds[0] == 30 and notification == 'new_board':
            (tmp_path / 'new.db').write_bytes(b'discovered source')
        return wait(.1)
    monkeypatch.setattr(native, 'wait', next_event)
    api._watch(f, native, r.watch_paths)
    expected = 0 if notification == 'noise' else 1
    assert wakes.count('_wake') == expected
    assert wakes.count('_decide') == expected
    assert wakes.count('_board') == expected


@pytest.mark.parametrize('keyed', [False, True])
def test_derived_publications_sleep_until_recovery_without_losing_legacy_fallback(tmp_path, monkeypatch, keyed):
    import threading
    from types import SimpleNamespace
    api = api_module()
    f, r, _, _ = make()
    f.watch(['S1'])
    f.stopped = threading.Event()
    cache = tmp_path / 'origin-work.db'
    cache.write_bytes(b'published cache')
    r.watch_paths = lambda: [cache]
    r.publication_paths = lambda: {cache}
    r.watch_directories = lambda: [tmp_path]
    seconds = [0.0]
    class Hints:
        reader = SimpleNamespace(source_dirty=False)
        next_reconcile = 30
        path = tmp_path / 'hub.db'
        def due(self):
            if seconds[0] < self.next_reconcile:
                return False
            self.next_reconcile += 30
            return True
    monkeypatch.setattr(api, '_keyed_hints', lambda: Hints() if keyed else None)
    monkeypatch.setattr(api, 'time', SimpleNamespace(monotonic=lambda: seconds[0]))
    monkeypatch.setattr(f.stopped, 'wait', lambda *_: False)
    legacy, recovery = [], []
    monkeypatch.setattr(f._wake, 'set', lambda: legacy.append(seconds[0]))
    monkeypatch.setattr(f, 'queue_keyed', lambda keys: recovery.append((seconds[0], keys)))
    native = api.FileWatch()
    if not native.ok or native._libc is None:
        native.close()
        pytest.skip('inotify only')
    wait = native.wait
    def next_event(timeout):
        seconds[0] += 1
        if seconds[0] > 60:
            f.stopped.set()
            return False
        temp = tmp_path / 'publication.tmp'
        temp.write_bytes(b'newly saved cache')
        temp.replace(cache)
        return wait(.1)
    monkeypatch.setattr(native, 'wait', next_event)
    api._watch(f, native, r.watch_paths)
    assert len(legacy) == (0 if keyed else 60)
    assert recovery == ([(30, {'S1'}), (60, {'S1'})] if keyed else [])


def test_watch_loss_withholds_heartbeat_freshness():
    f, r, pushed, t = make()
    f.watch(['S1'])
    f.tick()
    assert 'S1' in pushed[-1]['fresh']
    f._watching = False
    t['now'] += 1
    f.last_emit = 0
    f.beat()
    assert pushed[-1]['fresh'] == []


def test_external_origin_write_cannot_be_swallowed_by_board_cursor():
    f, r, _, _ = make()
    r.external_stamps = lambda: ('origin-source', 1)
    f.watch(['S1'])
    f.tick()
    before = f.bundles['S1']['bundle']
    r.links['S1'][0]['title'] = 'Changed by origin receipt projection'
    r.events = []
    r.stamp += 1
    r.external_stamps = lambda: ('origin-source', 2)
    f.tick()
    assert f.bundles['S1']['bundle'] is not before
    assert f.bundles['S1']['bundle']['tasks'][0]['title'] == 'Changed by origin receipt projection'
