"""Public classifier consumer contracts; analyze strings, never run payloads."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


guard = load('public_guard', ROOT / 'guard.py')
runner = load('public_tailnet', ROOT.parent / 'tailnet' / 'runner.py')


@pytest.mark.parametrize('command', [
    'echo $(cargo build)', 'x=$(cargo build --release)', '"$(swift build)"',
    'echo `cargo build`', 'case $(cargo build) in *) true;; esac',
    'for x in $(xcodebuild build); do true; done', 'echo $(rsync -a a b)',
    'bash -c \'echo $(cargo build)\'',
    'ssh -o ProxyCommand="echo $(cargo build)" host true',
])
def test_f5_real_tailnet_consumer(command):
    assert runner.workload_reason(command)


def test_f5_public_commands_are_argv():
    commands = list(guard.commands('echo $(cargo build)'))
    assert any(argv == ['cargo', 'build'] for argv, _ in commands)
    assert all(type(argv) is list for argv, _ in commands)


def test_f5_public_limit_fails_closed():
    command = 'cargo build'
    for _ in range(40):
        command = 'echo $(' + command + ')'
    with pytest.raises(ValueError, match='inspection'):
        list(guard.commands(command))
    with pytest.raises(ValueError, match='inspection'):
        runner.workload_reason(command)


@pytest.mark.parametrize('command', [
    'echo $(case x in x) pytest;; esac)',
    'echo "$(case x in x) pytest;; esac)"',
    'echo $(case x in (x) pytest;; esac)',
    'echo $(case x in x) echo light;; esac)',
    'echo $(if true; then case x in x) pytest;; esac; fi)',
    'echo $({ case x in x) pytest;; esac; })',
    'cat <<EOF\n$(case x in x) pytest;; esac)\nEOF',
])
def test_f6_case_substitution_conservative_hold(command, monkeypatch):
    monkeypatch.delenv('MAC_HEAVY_OK', raising=False)
    assert guard.check_shell(command)
    monkeypatch.setenv('MAC_HEAVY_OK', '1')
    assert guard.check_shell(command) is None


@pytest.mark.parametrize('command', [
    "echo $('case' x)", "echo $(echo case)", "echo $(echo 'case x in x) pytest;; esac')",
])
def test_f6_quoted_case_data_allowed(command):
    assert guard.check_shell(command) is None


@pytest.mark.parametrize('prefix', [
    'time', 'time -p', 'time !', 'time -p !', 'coproc', 'coproc worker',
    'function f', 'time coproc worker',
])
def test_f7_compound_prefix_case_hold(prefix, monkeypatch):
    monkeypatch.delenv('MAC_HEAVY_OK', raising=False)
    command = 'echo $(' + prefix + ' case x in x) pytest;; esac)'
    assert guard.check_shell(command)
    with pytest.raises(ValueError, match='inspection'):
        runner.workload_reason(command)


@pytest.mark.parametrize('command', [
    'echo $(echo time case)', 'echo $(echo coproc worker case)',
    'echo $(echo function f case)',
])
def test_f7_prefix_words_as_data(command):
    assert guard.check_shell(command) is None
    assert runner.workload_reason(command) is None


@pytest.mark.parametrize('prefix', [
    'time --', 'time -p --', 'time -p -- !',
    *[kind + ' ' + name for kind in ('function', 'coproc')
      for name in ('a-b', 'a.b', 'a:b', 'a/b', 'a+b', '1f',
                   "'a-b'", '"a b"', r'a\-b', "a' 'b")],
    'time -- coproc a-b', '! time -p -- function 1f',
])
def test_f8_shell_word_names_and_time_terminator(prefix, monkeypatch):
    monkeypatch.delenv('MAC_HEAVY_OK', raising=False)
    command = 'echo $(' + prefix + ' case x in x) pytest;; esac)'
    assert guard.check_shell(command)
    with pytest.raises(ValueError, match='inspection'):
        runner.workload_reason(command)
    monkeypatch.setenv('MAC_HEAVY_OK', '1')
    assert guard.check_shell(command) is None


@pytest.mark.parametrize('command', [
    'echo $(echo time -- case)', 'echo $(echo function a-b case)',
    'echo $(echo coproc a.b case)', 'echo $(time -- date)',
    'echo $(time -p -- date)', 'echo $(coproc a-b date)',
    'echo $(function a-b { date; }; a-b)',
])
def test_f8_shell_word_data_controls(command):
    assert guard.check_shell(command) is None
    assert runner.workload_reason(command) is None


@pytest.mark.parametrize('prefix', [
    'coproc a$(:)', 'coproc a`:`', 'coproc a"$(:)"', 'time -- coproc a$(:)', '! coproc a`:`',
    'coproc a$(echo $(echo b))', 'function a$(:)', 'coproc a"x$(:)y"',
])
def test_f9_substitution_in_shell_word_name(prefix, monkeypatch):
    monkeypatch.delenv('MAC_HEAVY_OK', raising=False)
    command = 'echo $(' + prefix + ' case x in x) pytest;; esac)'
    assert guard.check_shell(command)
    with pytest.raises(ValueError, match='inspection'):
        runner.workload_reason(command)
    monkeypatch.setenv('MAC_HEAVY_OK', '1')
    assert guard.check_shell(command) is None


@pytest.mark.parametrize('command', [
    'echo $(echo coproc a$(:) case)', 'echo $(coproc a$(:) date)', 'echo $(echo $(:) case)',
])
def test_f9_data_controls(command):
    assert guard.check_shell(command) is None
    assert runner.workload_reason(command) is None


def test_f9_arithmetic_name_still_refused(monkeypatch):
    monkeypatch.delenv('MAC_HEAVY_OK', raising=False)
    command = 'echo $(coproc a$((1)) case x in x) pytest;; esac)'
    assert guard.check_shell(command)
    try:
        assert runner.workload_reason(command)
    except ValueError:
        pass
