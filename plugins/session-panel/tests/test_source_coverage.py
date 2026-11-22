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
    try:
        w.set_paths([], directories=[tmp_path])
        board = tmp_path / 'new-board'
        board.mkdir()
        assert w.wait(0.2) and w.stale
        w.set_paths([board / 'kanban.db'], directories=[tmp_path, board])
        (board / 'kanban.db').write_bytes(b'new board source')
        assert w.wait(0.2)
        w.set_paths([board / 'kanban.db'], directories=[tmp_path, board])
        # Drain creation, then prove an existing unrelated file's contents do
        # not keep either producer busy through inventory wildcard coverage.
        log = tmp_path / 'unrelated.log'
        log.write_bytes(b'created')
        assert w.wait(0.2)
        w.set_paths([board / 'kanban.db'], directories=[tmp_path, board])
        while w.wait(0.01):
            pass
        log.write_bytes(b'changed only log contents')
        assert not w.wait(0.05)
    finally:
        w.close()


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
