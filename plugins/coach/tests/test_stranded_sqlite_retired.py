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
