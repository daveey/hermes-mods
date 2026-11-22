"""Interactive reads consume one immutable publication, never compute or wait."""
import json
import threading
from test_feed import feedmod, Readers

Feed = feedmod.Feed


def test_published_read_is_byte_identical_without_compute_or_locks():
    f = Feed(Readers(), emit=lambda p: None)
    expected = f.changes(['S1', 'S2'])
    # Hold both locks in another thread; a read must finish before either is released.
    held, release = threading.Event(), threading.Event()
    def holder():
        with f.compute_lock, f.lock:
            held.set()
            release.wait(3)
    t = threading.Thread(target=holder)
    t.start()
    assert held.wait(1)
    try:
        f.compute = lambda *a, **k: (_ for _ in ()).throw(AssertionError('request computed'))
        f._encode = lambda *a: (_ for _ in ()).throw(AssertionError('request serialized'))
        result = []
        reader = threading.Thread(target=lambda: result.append(f.changes_bytes(['S1', 'S2'])))
        reader.start()
        reader.join(.5)
        assert not reader.is_alive(), 'interactive read waited on publication lock'
        assert result == [json.dumps(expected, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()]
    finally:
        release.set()
        t.join()


def test_cold_read_registers_only_and_tick_hydrates():
    f = Feed(Readers(), emit=lambda p: None)
    assert json.loads(f.changes_bytes(['S1']))['bundles'] == {}
    assert not f.bundles
    f.tick()
    assert 'S1' in json.loads(f.changes_bytes(['S1']))['bundles']


def test_restore_and_resume_publish_without_freshness_promotion():
    f = Feed(Readers(), emit=lambda p: None)
    f.changes(['S1'])
    restored = Feed(Readers(), emit=lambda p: None).restore(f.snapshot())
    assert json.loads(restored.changes_bytes(['S1'])) == restored.changes(['S1'])
    resumed = Feed(Readers(), emit=lambda p: None).resume(restored)
    assert json.loads(f.changes_bytes(['S1']))['bundles']
    assert json.loads(resumed.changes_bytes(['S1'])) == resumed.changes(['S1'])
    assert 'S1' in resumed._carried


def test_new_registration_after_unchanged_tick_is_computed():
    f = Feed(Readers(), emit=lambda p: None)
    f.changes_bytes(['S1'])
    f.tick()
    f.changes_bytes(['S2'])
    f.tick()  # no new source stamp and no full-refresh timer
    assert 'S2' in json.loads(f.changes_bytes(['S2']))['bundles']


def test_registration_survives_handover_before_first_tick():
    f = Feed(Readers(), emit=lambda p: None)
    f.changes_bytes(['S1'])
    successor = Feed(Readers(), emit=lambda p: None).resume(f)
    successor.tick()
    assert 'S1' in json.loads(f.changes_bytes(['S1']))['bundles']


def test_unchanged_entries_reuse_bytes_and_old_publication_remains_immutable():
    f = Feed(Readers(), emit=lambda p: None)
    f.changes(['S1'])
    old = f._wire
    f._publish({}, heartbeat=True)
    assert f._wire[2]['S1'] is old[2]['S1']
    f.changes(['S2'])
    assert 'S2' not in old[2]
    assert 'S2' in f._wire[2]
