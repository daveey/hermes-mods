"""Actual supported launch/admission and both mode directions; never supplies a cut."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != 'linux', reason='strict Linux process census')


def test_supported_panel_real_census_legacy_drain_exec_and_same_pid_rollback(tmp_path):
    admission = pytest.importorskip('hermes_cli.serve_producer_admission')
    pytest.importorskip('hermes_cli.serve_producer_retirement')
    core = Path(admission.__file__).parents[1]
    here = Path(__file__).parent
    home = tmp_path / 'isolated-home'
    home.mkdir(mode=0o700)
    bwrap = shutil.which('bwrap')
    assert bwrap, 'core-backed acceptance requires bubblewrap PID isolation'
    privilege = [] if os.geteuid() == 0 else ['sudo', '-n']
    command = [*privilege, bwrap, '--ro-bind', '/', '/', '--bind', str(tmp_path), str(tmp_path),
               '--unshare-pid', '--proc', '/proc', '--dev', '/dev', '--clearenv',
               '--setenv', 'PATH', '/usr/bin:/bin', '--setenv', 'LANG', 'C.UTF-8',
               '--setenv', 'HOME', str(home), '--setenv', 'HERMES_HOME', str(home),
               '--setenv', 'TMPDIR', str(home), '--setenv', 'XDG_STATE_HOME', str(home / 'state'),
               '--unsetenv', 'HERMES_SERVE_PANEL_EXTERNAL',
               '--unsetenv', 'HERMES_SERVE_PRODUCERS_EXTERNAL',
               '--unsetenv', 'HERMES_KANBAN_DB', '--unsetenv', 'HERMES_KANBAN_BOARD',
               '--unsetenv', 'HERMES_KANBAN_HOME',
               'setpriv', f'--reuid={os.getuid()}', f'--regid={os.getgid()}', '--clear-groups',
               sys.executable, '-I', str(here / 'supported_panel_process.py'), str(core), str(here.parent / 'dashboard')]
    result = subprocess.run(command, capture_output=True, text=True, timeout=180, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    # Normal core boot can redirect print() to stderr; receipt transport is not the gate.
    rows = [json.loads(line) for line in (result.stdout + '\n' + result.stderr).splitlines()
            if line.startswith('{')]
    assert len(rows) == 3, result.stdout + '\nSTDERR:\n' + result.stderr
    external, attached, off = rows
    assert external['gate'] == 'real panel OFF to external'
    assert external['status']['running'] is True
    assert external['pid'] != external['broker']
    assert external['legacy_fenced'] is True
    assert attached['gate'] == 'second supported client'
    assert attached['pid'] != external['pid']
    assert attached['broker'] == external['broker']
    assert off == {'gate': 'same PID external to OFF', 'pid': external['pid'],
                   'old_relay_retired': True, 'legacy_fenced': False}
