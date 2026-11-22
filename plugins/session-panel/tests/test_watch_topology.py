"""Missing source directories must become visible on events, not a timed relist."""
import sys
from pathlib import Path

import pytest
from test_routes import load

API = Path(__file__).resolve().parents[1] / 'dashboard' / 'plugin_api.py'


@pytest.mark.skipif(not sys.platform.startswith('linux'), reason='Linux inotify contract')
@pytest.mark.parametrize('mode', ['staged', 'atomic', 'replace'])
def test_missing_source_tree_is_discovered_without_timer(tmp_path, mode):
    api = load('_topology_' + mode, API)
    target = tmp_path / 'sources' / 'replica' / 'hub.db'
    if mode == 'replace':
        target.parent.mkdir(parents=True)
        target.write_bytes(b'old')
    watch = api.FileWatch()
    assert watch.ok
    try:
        watch.set_paths([target])
        assert not watch.wait(.02)
        if mode == 'replace':
            target.parent.parent.rename(tmp_path / 'retired')
        elif mode == 'atomic':
            staged = tmp_path / 'staged' / 'replica'
            staged.mkdir(parents=True)
            (staged / 'hub.db').write_bytes(b'new')
            staged.parent.rename(target.parent.parent)
        else:
            target.parent.parent.mkdir()
        assert watch.wait(.2), 'source directory topology change must wake immediately'
        assert watch.stale, 'topology events require a relist before sleeping'
        watch.set_paths([target])
        if mode != 'atomic':
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b'new')
            assert watch.wait(.2)
            assert watch.stale
            watch.set_paths([target])
        # Drain any queued topology events, then verify actual database writes.
        while watch.wait(.01):
            if watch.stale:
                watch.set_paths([target])
        target.write_bytes(b'changed')
        assert watch.wait(.2)
        assert not watch.stale, 'ordinary DB writes must not relist the source inventory'
        (target.parent / 'noise.log').write_bytes(b'noise')
        assert not watch.wait(.05), 'unrelated names do not trigger a producer pass'
    finally:
        watch.close()
