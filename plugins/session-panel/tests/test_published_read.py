"""Interactive reads consume one immutable publication, never compute or wait."""
import json
import threading
from test_feed import feedmod, Readers

Feed = feedmod.Feed


def wire(out):
    """Feed.changes() (the in-process view) as a client receives it: server-only inputs dropped."""
    return {**out, 'bundles': {s: feedmod.wire_bundle(b) for s, b in out['bundles'].items()}}


def test_published_read_is_byte_identical_without_compute_or_locks():
    f = Feed(Readers(), emit=lambda p: None)
    expected = wire(f.changes(['S1', 'S2']))
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
    assert json.loads(restored.changes_bytes(['S1'])) == wire(restored.changes(['S1']))
    resumed = Feed(Readers(), emit=lambda p: None).resume(restored)
    assert json.loads(f.changes_bytes(['S1']))['bundles']
    assert json.loads(resumed.changes_bytes(['S1'])) == wire(resumed.changes(['S1']))
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


def test_wire_omits_server_only_inputs_but_keeps_what_clients_render():
    """[daveey 2026-11-22] "switching sessions ... should be instant": `cards`/`done`/`requests`/`outcome_json` are
    the hub's inputs to `model` and no client reads them, but they were ~90% of catch-up and push bytes on the
    Desktop's tunnel, queuing transcript reads behind them. Catch-ups and pushes carry only what clients use."""
    pushed = []
    f = Feed(Readers(), emit=pushed.append)
    full = f.changes(['S1'])['bundles']['S1']
    assert full['cards'] and 'done' in full, 'the held bundle keeps the inputs the model is reduced from'
    got = json.loads(f.changes_bytes(['S1']))['bundles']['S1']
    assert not feedmod.WIRE_OMIT & set(got), got.keys()
    for k in ('session', 'lineage', 'tasks', 'model', 'computed_at'):
        assert got[k] == json.loads(json.dumps(full[k])), k
    assert pushed and pushed[-1]['bundles'], 'computing S1 pushed it'
    for b in pushed[-1]['bundles'].values():
        assert not feedmod.WIRE_OMIT & set(b) and 'model' in b and 'tasks' in b
