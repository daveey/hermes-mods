"""Unsupported ANSI-C syntax must hold, not escape to the hook's allow fallback."""
import importlib.util
import io
import json
import runpy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


guard = load('ansi_hold_guard', ROOT / 'guard.py')


def test_heredoc_ansi_refusal_is_a_hold(monkeypatch):
    monkeypatch.delenv('MAC_HEAVY_OK', raising=False)
    command = "echo $'\\cA'; cat <<EOF\nx\nEOF\npytest"
    try:
        result = guard.check_shell(command)
    except Exception as exc:
        raise AssertionError('inspection refusal escaped instead of producing a hold') from exc
    assert result and 'inspection' in result


CASES = [
    "echo $'\\cA'; cat <<EOF\nx\nEOF\npytest",
    "echo $'\\x80'; cat <<EOF\nx\nEOF\ncargo build --release",
    "x=\"$(echo 'a\"b')\"; echo $'\\cA'; pytest",
    "x=\"$(echo 'a\"b')\"; echo $'\\u00e9'; cargo build --release",
]


@pytest.mark.parametrize('command', CASES)
def test_all_refusals_reach_check_and_evaluate(command, monkeypatch):
    monkeypatch.delenv('MAC_HEAVY_OK', raising=False)
    try:
        assert guard.check_shell(command)
        block, note = guard.evaluate(command)
    except Exception as exc:
        raise AssertionError('inspection hold escaped the classifier') from exc
    assert block and 'inspection' in block
    assert note is None


@pytest.mark.parametrize('command', CASES)
def test_registered_hook_blocks_unsupported_ansi(command, monkeypatch):
    plugin = load('ansi_hold_plugin', ROOT / '__init__.py')
    monkeypatch.setattr(plugin.guard, 'enabled', lambda: True)
    monkeypatch.delenv('MAC_HEAVY_OK', raising=False)
    hooks = {}
    class Context:
        def register_hook(self, name, callback):
            hooks[name] = callback
    plugin.register(Context())
    result = hooks['pre_tool_call'](tool_name='terminal', args={'command': command, 'workdir': '/scratch'})
    assert result and result['action'] == 'block'
    assert 'inspection' in result['message']


@pytest.mark.parametrize('command', CASES)
def test_claude_hook_returns_blocking_exit(command, monkeypatch, capsys):
    monkeypatch.setattr(sys, 'platform', 'darwin')
    monkeypatch.delenv('MAC_HEAVY_OK', raising=False)
    monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps({'tool_input': {'command': command}})))
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(str(ROOT / 'claude_hook.py'), run_name='__main__')
    assert exc.value.code == 2
    assert 'inspection' in capsys.readouterr().err


@pytest.mark.parametrize('command', CASES)
def test_public_commands_raise_documented_valueerror(command):
    with pytest.raises(ValueError, match='inspection'):
        list(guard.commands(command))


@pytest.mark.parametrize('command', CASES)
def test_note_cannot_leak_unsupported_syntax(command, monkeypatch):
    monkeypatch.delenv('MAC_HEAVY_OK', raising=False)
    assert guard.note(command) is None


@pytest.mark.parametrize('command', [
    "echo $'ok'; cat <<EOF\nx\nEOF\necho light",
    "x=\"$(echo 'a\"b')\"; echo $'ok'; echo light",
    "cat <<'EOF'\necho $'\\cA'; pytest\nEOF\necho light",
])
def test_supported_and_inert_controls_are_allowed(command, monkeypatch):
    monkeypatch.delenv('MAC_HEAVY_OK', raising=False)
    assert guard.check_shell(command) is None


@pytest.mark.parametrize('channel', ['local', 'tailnet'])
def test_differential_does_not_count_unexpected_exceptions_as_holds(channel, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / 'tests'))
    import diff_corpus
    from types import SimpleNamespace
    def crash(command):
        raise RuntimeError('injected classification crash')
    fake = SimpleNamespace(check_shell=crash if channel == 'local' else lambda c: None,
                           workload_reason=crash if channel == 'tailnet' else lambda c: None)
    monkeypatch.setattr(diff_corpus, 'load', lambda *args: fake)
    with pytest.raises(RuntimeError, match='injected classification crash'):
        diff_corpus.verdicts(ROOT, ['echo control'])


