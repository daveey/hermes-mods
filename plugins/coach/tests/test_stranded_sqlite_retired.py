"""Retired profile handles need cache cleanup, not a restart/corruption incident."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location('retired_watch', Path(__file__).resolve().parents[1] / 'stranded_sqlite_watch.py')
assert spec is not None and spec.loader is not None
sw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sw)


@pytest.mark.parametrize('state', ['missing', 'tombstoned', 'live', 'live_missing_db'])
def test_profile_lifecycle_classification(tmp_path, capsys, state):
    home = tmp_path / 'home'
    profile = home / 'profiles' / 'fixture'
    home.mkdir()
    if state.startswith('live'):
        profile.mkdir(parents=True)
        if state == 'live':
            (profile / 'state.db').touch()
    elif state == 'tombstoned':
        tomb = home / 'profiles' / '.deleted' / 'fixture'
        tomb.parent.mkdir(parents=True)
        tomb.touch()
    proc = tmp_path / 'proc'
    fds = proc / '987654' / 'fd'
    fds.mkdir(parents=True)
    (fds.parent / 'cmdline').write_bytes(b'python\x00watch\x00')
    (fds.parent / 'stat').write_text('987654 (watch) S ' + ' '.join(['0'] * 18 + ['100']))
    for i, side in enumerate(['wal', 'shm']):
        (fds / str(i)).symlink_to(str(profile / ('state.db-' + side)) + ' (deleted)')
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout='{}', stderr='')

    assert sw.main(['--home', str(home), '--json'], runner=run, proc=str(proc)) == 0
    result = json.loads(capsys.readouterr().out)
    if state.startswith('live'):
        assert len(result['findings']) == 1 and len(calls) == 1
        assert len(result['findings'][0]['sidecars']) == 2
        body = calls[0][1]['input']
        assert 'does not establish' in body
    else:
        assert result['findings'] == [] and calls == []


def _fake_proc(tmp_path, db_dir, db='kanban.db'):
    proc = tmp_path / 'proc'
    fds = proc / '987655' / 'fd'
    fds.mkdir(parents=True)
    (fds.parent / 'cmdline').write_bytes(b'hermes\x00gateway\x00')
    (fds.parent / 'stat').write_text('987655 (hermes) S ' + ' '.join(['0'] * 18 + ['100']))
    for i, side in enumerate(['wal', 'shm']):
        (fds / str(i)).symlink_to(str(db_dir / (db + '-' + side)) + ' (deleted)')
    return proc


@pytest.mark.parametrize('under_profile', [False, True])
@pytest.mark.parametrize('state', ['absent', 'present', 'present_missing_db', 'stat_error'])
def test_board_dir_lifecycle_classification(tmp_path, capsys, monkeypatch, state, under_profile):
    """A deleted board dir (kanban/boards/<slug>/, also under a live profile home) is retired-reader cleanup;
    a still-existing board dir, or one whose state cannot be read, keeps the finding."""
    home = tmp_path / 'home'
    base = home / 'profiles' / 'fixture' if under_profile else home
    (base / 'kanban' / 'boards').mkdir(parents=True)  # profile home exists: only the board dir decides
    board = base / 'kanban' / 'boards' / 'probe-board'
    if state.startswith('present'):
        board.mkdir()
        if state == 'present':
            (board / 'kanban.db').touch()
    if state == 'stat_error':
        real_stat = sw.Path.stat

        def stat(self, *a, **k):
            if self.name == 'probe-board':
                raise PermissionError(13, 'denied', str(self))
            return real_stat(self, *a, **k)
        monkeypatch.setattr(sw.Path, 'stat', stat)
    proc = _fake_proc(tmp_path, board)
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout='{}', stderr='')

    assert sw.main(['--home', str(home), '--json'], runner=run, proc=str(proc)) == 0
    result = json.loads(capsys.readouterr().out)
    if state == 'absent':
        assert result['findings'] == [] and calls == []
    else:
        assert len(result['findings']) == 1 and len(calls) == 1
        assert result['findings'][0]['db'].endswith('/kanban/boards/probe-board/kanban.db')
        assert len(result['findings'][0]['sidecars']) == 2


def test_absent_profile_home_with_board_path_skipped(tmp_path):
    home = tmp_path / 'home'
    home.mkdir()
    proc = _fake_proc(tmp_path, home / 'profiles' / 'gone' / 'kanban' / 'boards' / 'b')
    assert sw.scan(str(home), str(proc)) == []


@pytest.mark.parametrize('rel', [('kanban', 'other'), ('kanban',), ('profiles', 'fixture', 'kanban', 'other')])
def test_non_board_paths_not_suppressed(tmp_path, rel):
    """Only <base>/kanban/boards/<slug>/ is a board dir: an absent sibling dir must not hide a finding."""
    home = tmp_path / 'home'
    (home / 'profiles' / 'fixture' / 'kanban').mkdir(parents=True)
    proc = _fake_proc(tmp_path, home.joinpath(*rel))
    assert len(sw.scan(str(home), str(proc))) == 1
