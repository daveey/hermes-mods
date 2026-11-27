"""Monotone floor: the head can only add refusals relative to origin/main.

`main_floor/` holds byte-identical copies of origin/main's guard.py and shell_context.py (pinned
below). guard.check_shell refuses whenever main's logic refuses OR the head analysis refuses.
"""
import hashlib
import importlib.util
import random
import shlex
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parent
sys.path.insert(0, str(HERE))
import diff_corpus  # noqa: E402

spec = importlib.util.spec_from_file_location('floor_guard_under_test', PLUGIN / 'guard.py')
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)

MAIN_COMMIT = 'a34b09d2e16d120743c72c685c2ec4d0a77e9f6c'
MAIN_SHA256 = {
    'guard.py': '4881561ab2a2c49f85b94724690dc48b06897ee5a6deb203854533ddf0335917',
    'shell_context.py': 'ddcb685a39a6c8b036ce8e086ed8887c2c77e89f35462dd1a4cc34865eb70bab',
}
SEED = 531
RANDOM_CASES = 3000


@pytest.fixture(autouse=True)
def synthetic_home(monkeypatch):
    monkeypatch.setenv('HOME', diff_corpus.HOME_PARENT)
    monkeypatch.setenv('HERMES_HOME', diff_corpus.HOME_PARENT + '/.hermes')


def test_floor_files_are_byte_identical_to_main():
    for name, digest in MAIN_SHA256.items():
        assert hashlib.sha256((PLUGIN / 'main_floor' / name).read_bytes()).hexdigest() == digest, (
            f'main_floor/{name} must stay byte-identical to {name} at origin/main {MAIN_COMMIT}')


_WRAPPERS = [
    lambda c: c,
    lambda c: 'bash -c ' + shlex.quote(c),
    lambda c: 'sh -c ' + shlex.quote(c),
    lambda c: 'zsh -c ' + shlex.quote(c),
    lambda c: 'bash -c ' + shlex.quote('shopt -s lastpipe; ' + c),
    lambda c: 'bash -O lastpipe -c ' + shlex.quote(c),
    lambda c: 'ssh example.com ' + shlex.quote(c),
    lambda c: 'ssh example.com ' + shlex.quote('zsh -c ' + shlex.quote(c)),
    lambda c: 'ssh example.com ' + shlex.quote('shopt -s lastpipe; ' + c),
]


def _splice(rng, command):
    words = [w for w in ('rm', 'cd', 'state.db', 'mv', 'echo') if w in command]
    if not words:
        return command
    word = rng.choice(words)
    cut = rng.randrange(1, len(word)) if len(word) > 1 else 0
    return command.replace(word, word[:cut] + rng.choice(["''", '""']) + word[cut:], 1)


def _backslash_newline(rng, command):
    index = rng.randrange(1, len(command))
    return command[:index] + '\\\n' + command[index:]


def generated_commands():
    rng = random.Random(SEED)
    base = diff_corpus.corpus()
    out, seen = [], set()
    while len(out) < RANDOM_CASES:
        command = rng.choice(base)
        for _ in range(rng.choice((0, 1, 1, 2))):
            command = rng.choice((_splice, _backslash_newline))(rng, command)
        command = rng.choice(_WRAPPERS)(command)
        if command not in seen:
            seen.add(command)
            out.append(command)
    return out


def _main_refuses(command):
    return bool(guard._main_floor().check_shell(command, diff_corpus.HOME_PARENT))


def violations(check, commands):
    """Commands main refuses that `check` allows."""
    return [c for c in commands if _main_refuses(c) and not check(c, diff_corpus.HOME_PARENT)]


def test_head_refuses_everything_main_refuses_on_random_compositions():
    commands = generated_commands()
    refused = [c for c in commands if _main_refuses(c)]
    assert len(refused) > 300, 'generator must exercise main refusals'
    assert violations(guard.check_shell, commands) == []


def test_head_refuses_everything_main_refuses_on_corpus_with_wrappers():
    commands = [w(c) for c in diff_corpus.corpus() for w in _WRAPPERS]
    assert violations(guard.check_shell, commands) == []


@pytest.mark.parametrize('command', diff_corpus.REVIEW_REPROS)
def test_every_review_repro_is_refused(command):
    assert guard.check_shell(command, diff_corpus.HOME_PARENT)


def test_violation_detector_flags_a_permissive_checker():
    commands = generated_commands()
    assert violations(lambda command, cwd='': None, commands), 'detector must notice an allow-all checker'


def test_floor_is_what_makes_the_property_hold(monkeypatch):
    """Mutant: delete the floor call. The head must then be the only thing refusing."""
    commands = generated_commands()
    real_floor = guard._main_floor()

    class _NoFloor:
        @staticmethod
        def check_shell(command, cwd=''):
            return None

    monkeypatch.setattr(guard, '_main_floor', lambda: _NoFloor)
    leaked = [c for c in commands if real_floor.check_shell(c, diff_corpus.HOME_PARENT)
              and not guard.check_shell(c, diff_corpus.HOME_PARENT)]
    print(f'floor deleted: {len(leaked)} of {len(commands)} generated commands leak past the head alone')
    # With the floor restored nothing may leak, whatever the head alone does.
    monkeypatch.undo()
    assert violations(guard.check_shell, commands) == []


def test_floor_unavailable_fails_closed(monkeypatch):
    def broken():
        raise ImportError('boom')

    monkeypatch.setattr(guard, '_main_floor', broken)
    assert 'floor unavailable' in guard.check_shell('echo hi', diff_corpus.HOME_PARENT)
