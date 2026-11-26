"""session-panel background writers never recreate a deleted/tombstoned named profile."""
import importlib.util
import queue
import shutil
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

DASH = Path(__file__).resolve().parents[1] / 'dashboard'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def receipts():
    return load('_panel_source_receipts_profile_delete', DASH / 'source_receipts.py')


def identity():
    return dict(boot_id='fixture-boot', pid=123, process_birth_ticks=321, runtime_sha='1' * 40,
        mods_sha='2' * 40, core_sha='3' * 40, api_generation='fixture-api', serving_gateway='fixture-gateway', profile='default')


def _deleted_profile(tmp_path, name='gone'):
    constants = pytest.importorskip('hermes_constants')
    if not hasattr(constants, 'mkdir_under_hermes_home'):
        pytest.skip('core predates the named-profile mkdir fence')
    home = tmp_path / '.hermes' / 'profiles' / name
    home.mkdir(parents=True)
    (home / 'config.yaml').write_text('x: 1\n')
    constants.mark_named_profile_deleted(home)
    shutil.rmtree(home)
    return home


def _feed():
    return SimpleNamespace(lock=threading.RLock(), rev=1, interest=set(), timing={})


def test_write_timing_and_locked_do_not_recreate_deleted_profile(tmp_path):
    api = load('_spf_profile_delete_api', DASH / 'plugin_api.py')
    home = _deleted_profile(tmp_path)
    assert api.write_timing(_feed(), home / 'session-panel' / 'timing.json') is False
    with pytest.raises(FileNotFoundError):
        with api._locked(home / 'session-panel' / 'feed.json'):
            pass
    assert not home.exists()


def test_final_snapshot_save_does_not_recreate_deleted_profile(tmp_path):
    api = load('_spf_profile_delete_api2', DASH / 'plugin_api.py')
    home = _deleted_profile(tmp_path)
    f = SimpleNamespace(snapshot=lambda timeout=None: {'version': 1, 'epoch': 'e', 'rev': 1, 'saved_at': 0.0})
    assert api.save_snapshot(f, home / 'session-panel' / 'feed.json') is False
    assert not home.exists()


def test_live_profile_still_writes(tmp_path):
    api = load('_spf_profile_delete_api3', DASH / 'plugin_api.py')
    home = tmp_path / '.hermes' / 'profiles' / 'live'
    home.mkdir(parents=True)
    (home / 'config.yaml').write_text('x: 1\n')
    target = home / 'session-panel' / 'timing.json'
    assert api.write_timing(_feed(), target) is True
    assert target.is_file()


def test_receipt_log_does_not_recreate_deleted_profile(tmp_path):
    mod = receipts()
    home = _deleted_profile(tmp_path)
    log = mod.ReceiptLog(home / 'logs' / 'deep' / 'receipts.jsonl', identity=identity())
    log.close()
    assert not home.exists()
    assert log.valid is False  # the refused open is a write error, never a silent recreate


@pytest.mark.parametrize('mode', ['legacy', 'facts'])
def test_running_producer_and_quarantine_poll_preserve_deleted_home(tmp_path, monkeypatch, mode):
    """Delete between real producer passes, including its finally snapshot write."""
    constants = pytest.importorskip('hermes_constants')
    dbfile = pytest.importorskip('hermes_state_dbfile')
    if not hasattr(constants, 'mkdir_under_hermes_home'):
        pytest.skip('core predates the named-profile mkdir fence')
    api = load('_spf_running_delete_' + mode, DASH / 'plugin_api.py')
    home = tmp_path / '.hermes' / 'profiles' / 'running'
    home.mkdir(parents=True)
    (home / 'config.yaml').write_text('{}')
    monkeypatch.setattr(api, '_snapshot_path', lambda: home / 'session-panel' / 'feed.json')
    monkeypatch.setattr(api, 'SNAPSHOT_EVERY_S', 0)
    monkeypatch.setattr(api, '_event_driven', lambda f: True)
    monkeypatch.setattr(api, '_settle', lambda f, last: None)
    # Holding an admitted pass across deletion is synchronization, not producer
    # work. Do not turn scheduler delay into unrelated duty-rest deferrals/saves.
    monkeypatch.setattr(api, 'DUTY_REST_RATIO', 0)
    writes, passes, permits, errors = (queue.Queue() for _ in range(4))
    released, adapter_closed = threading.Event(), threading.Event()
    f = _feed()
    f.stopped, f._wake, f._cold = (threading.Event() for _ in range(3))
    f.heartbeat_s, f.clock, f._retry = 60.0, time.monotonic, False
    f.readers = SimpleNamespace(release=released.set)
    f.snapshot = lambda timeout=None: dict(version=1, epoch='e', rev=f.rev, saved_at=0.0)
    f.beat = lambda: None

    def step():
        passes.put(True)
        permits.get(timeout=10)
        f._wake.clear()
        f._cold.clear()
        f.rev += 1

    f.tick = step
    adapter = SimpleNamespace(step=step, deadlines={}, close=adapter_closed.set)
    save = api.save_snapshot

    def observed_save(*args, **kwargs):
        result = save(*args, **kwargs)
        writes.put(result)
        return result

    monkeypatch.setattr(api, 'save_snapshot', observed_save)

    def produce():
        try:
            api._run(f) if mode == 'legacy' else api._run_facts(f, adapter)
        except BaseException as exc:
            errors.put(exc)

    poll_stop, poll_commands, poll_results = threading.Event(), queue.Queue(), queue.Queue()

    def quarantine_poller():
        while not poll_stop.is_set():
            try:
                command = poll_commands.get(timeout=0.1)
            except queue.Empty:
                continue
            if command == 'stop':
                return
            try:
                with dbfile.quarantine_cross_process_lock(home / 'state.db') as acquired:
                    poll_results.put(acquired)
            except FileNotFoundError:
                poll_results.put(False)
            except BaseException as exc:
                errors.put(exc)
                return

    producer = threading.Thread(target=produce, daemon=True)
    poller = threading.Thread(target=quarantine_poller, daemon=True)
    producer.start()
    poller.start()
    try:
        assert passes.get(timeout=10)
        permits.put(True)
        assert writes.get(timeout=10) is True
        poll_commands.put('poll')
        assert poll_results.get(timeout=10) is True
        # Admit a pass before deletion, then hold it across removal.
        f._wake.set()
        assert passes.get(timeout=10)
        constants.mark_named_profile_deleted(home)
        shutil.rmtree(home)
        for index in range(3):
            permits.put(True)
            assert writes.get(timeout=10) is False
            poll_commands.put('poll')
            assert poll_results.get(timeout=10) is False
            assert not home.exists()
            if index < 2:
                f._wake.set()
                assert passes.get(timeout=10)
        f.stopped.set()
        f._wake.set()
        producer.join(timeout=10)
        assert not producer.is_alive()
        assert writes.get(timeout=10) is False  # real finally save
        assert not home.exists()
        assert released.is_set() if mode == 'legacy' else adapter_closed.is_set()
        assert errors.empty(), list(errors.queue)
    finally:
        f.stopped.set()
        f._wake.set()
        permits.put(True)
        poll_stop.set()
        poll_commands.put('stop')
        producer.join(timeout=10)
        poller.join(timeout=10)
    assert not producer.is_alive() and not poller.is_alive()
