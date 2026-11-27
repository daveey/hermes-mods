"""ANSI-C quoting and bash's arithmetic/command-substitution ambiguity."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from diff_corpus import ANSI_ARITH_PAIRS, load

guard = load('ansi_guard', Path(__file__).resolve().parents[1] / 'guard.py')


@pytest.mark.parametrize('heavy,benign', ANSI_ARITH_PAIRS)
def test_local_holds_heavy_and_allows_exact_twin(heavy, benign):
    assert guard.check_shell(heavy), repr(heavy)
    assert any(guard.check_argv(argv) for argv, _ in guard.commands(heavy)), repr(heavy)
    assert guard.check_shell(benign) is None, repr(benign)


@pytest.mark.parametrize('command', [
    'echo "$((1+2))"', 'echo "$(((1+2)*3))"',
    'echo "$(( (1+2) * 3 ))"', 'echo "$(( (pytest) | (true) ))"',
    'cat <<EOF\n$(( (pytest) | (true) ))\nEOF',
])
def test_real_arithmetic_remains_allowed(command, tmp_path):
    assert guard.check_shell(command) is None
    shim = tmp_path / 'pytest'
    marker = tmp_path / 'marker'
    shim.write_text('#!/bin/sh\nprintf ran >> "$MARKER"\n')
    shim.chmod(0o700)
    env = dict(os.environ, PATH=str(tmp_path) + ':' + os.defpath, MARKER=str(marker), BASH_ENV='/dev/null')
    result = subprocess.run(['/bin/bash', '--noprofile', '--norc', '-c', command], env=env,
                            capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
    assert not marker.exists()


def test_join_before_expose_keeps_benign_continued_substitution_allowed():
    assert guard.check_shell('echo "$(ec\\\nho ok)"') is None


@pytest.mark.parametrize('heavy,benign', ANSI_ARITH_PAIRS)
def test_bash_repro_executes_only_fake_pytest(tmp_path, heavy, benign):
    shim = tmp_path / 'pytest'
    marker = tmp_path / 'marker'
    shim.write_text('#!/bin/sh\nprintf ran >> "$MARKER"\n')
    shim.chmod(0o700)
    env = dict(os.environ, PATH=str(tmp_path) + ':' + os.defpath, MARKER=str(marker), BASH_ENV='/dev/null')
    for command, expected in [(heavy, True), (benign, False)]:
        marker.unlink(missing_ok=True)
        result = subprocess.run(['/bin/bash', '--noprofile', '--norc', '-c', command], env=env,
                                capture_output=True, text=True, timeout=5)
        assert result.returncode == 0, (command, result.stderr)
        assert marker.exists() == expected, command
