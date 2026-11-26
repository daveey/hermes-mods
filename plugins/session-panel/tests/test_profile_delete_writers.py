"""session-panel background writers never recreate a deleted/tombstoned named profile."""
import importlib.util
import shutil
import sys
import threading
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
