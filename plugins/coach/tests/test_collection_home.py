"""Collection home lifecycle, including an actual killed creator."""
import os
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import patch
import collection_home as c


def old(path):
    for root, dirs, files in os.walk(path):
        for name in dirs + files:
            os.utime(Path(root, name), (1, 1), follow_symlinks=False)
    os.utime(path, (1, 1))


def test_dead_sibling_reaped_live_and_legacy_kept(tmp_path):
    dead = tmp_path / 'coach-collection-p99999999-abcdefgh'
    live = tmp_path / f'coach-collection-p{os.getpid()}-abcdefgh'
    legacy = tmp_path / 'coach-collection-abcdefgh'
    for p in (dead, live, legacy):
        p.mkdir()
        old(p)
    with patch.object(c, 'held_paths', return_value=set()):
        assert c.sweep(tmp_path) == [dead]
    assert live.exists() and legacy.exists()


def test_busy_or_unknown_process_scan_keeps(tmp_path):
    p = tmp_path / 'coach-collection-p99999999-abcdefgh'
    p.mkdir()
    old(p)
    for held in (None, {str(p / 'child.db')}):
        with patch.object(c, 'held_paths', return_value=held):
            assert c.sweep(tmp_path) == []
        assert p.exists()


def test_deep_activity_and_symlink_kept(tmp_path):
    p = tmp_path / 'coach-collection-p99999999-abcdefgh'
    (p / 'a/b/c/d').mkdir(parents=True)
    old(p)
    (p / 'a/b/c/d/new').write_text('recent')
    link = tmp_path / 'coach-collection-p99999998-abcdefgh'
    link.symlink_to(p, target_is_directory=True)
    with patch.object(c, 'held_paths', return_value=set()):
        assert c.sweep(tmp_path) == []
    assert link.is_symlink() and p.exists()


def test_create_has_pid_and_normal_cleanup(tmp_path):
    with patch.object(c.tempfile, 'gettempdir', return_value=str(tmp_path)):
        home = c.create()
    p = Path(home.name)
    assert c.NAME.fullmatch(p.name)[1] == str(os.getpid())
    home.cleanup()
    assert not p.exists()


def test_killed_creator_recovered(tmp_path):
    env = {**os.environ, 'TMPDIR': str(tmp_path)}
    code = 'import collection_home as c,time; h=c.create(); print(h.name,flush=True); time.sleep(60)'
    child = subprocess.Popen([sys.executable, '-c', code], env=env,
                             cwd=Path(c.__file__).parent, stdout=subprocess.PIPE, text=True)
    try:
        p = Path(child.stdout.readline().strip())
        assert p.exists()
        old(p)
        with patch.object(c, 'held_paths', return_value=set()):
            assert c.sweep(tmp_path) == []  # even an old live creator is protected
        child.kill()
        child.wait(timeout=10)
        assert p.exists()  # SIGKILL really bypassed TemporaryDirectory finalization
        with patch.object(c, 'held_paths', return_value=set()):
            assert c.sweep(tmp_path) == [p]
        assert not p.exists()
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)


def test_permission_error_is_not_dead():
    with patch.object(c.os, 'kill', side_effect=PermissionError):
        assert not c.dead(99999999)
