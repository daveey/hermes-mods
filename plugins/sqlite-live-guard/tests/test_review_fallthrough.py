"""Second independent review regressions: classification only."""
import importlib.util
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location('fallthrough_guard', Path(__file__).resolve().parents[1] / 'guard.py')
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)

@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / 'user' / '.hermes'
    (home / 'cache').mkdir(parents=True)
    monkeypatch.setenv('HOME', str(home.parent))
    monkeypatch.setenv('HERMES_HOME', str(home))
    return home

@pytest.mark.parametrize('option', ['--t', '--ta', '--tar', '--targ', '--targe', '--target', '--target-', '--target-d', '--target-di', '--target-dir', '--target-dire', '--target-direc', '--target-direct', '--target-directo', '--target-director'])
@pytest.mark.parametrize('equals', [False, True])
@pytest.mark.parametrize('remote', [False, True])
def test_mv_long_prefix(home, option, equals, remote):
    command = 'mv ' + option + ('=/unrelated' if equals else ' /unrelated') + ' ~/.hermes'
    if remote:
        command = 'ssh example.com "' + command + '"'
    assert guard.check_shell(command)
    assert guard.check_shell(command.replace('~/.hermes', '~/.hermes/cache')) is None

@pytest.mark.parametrize('separator', [';', '\n', '||'])
@pytest.mark.parametrize('wrapped', [False, True])
@pytest.mark.parametrize('alias', [False, True])
def test_failed_cd_preserves_previous(home, separator, wrapped, alias):
    command = 'cd /nonexistent ' + separator + ' rm -rf ' + ('"$D"' if alias else '.hermes')
    if alias:
        command = 'D=.hermes; ' + command
    if wrapped:
        command = "bash -c '" + command + "'"
    assert guard.check_shell(command, cwd=str(home.parent))

@pytest.mark.parametrize('separator', [';', '\n', '||'])
def test_failed_cd_wildcard(home, separator):
    assert guard.check_shell('cd /nonexistent ' + separator + ' rm -rf *', cwd=str(home))

@pytest.mark.parametrize('separator', [';', '\n', '||', '&&'])
def test_cd_cache_control(home, separator):
    assert guard.check_shell('cd /unrelated ' + separator + ' rm -rf disposable', cwd=str(home / 'cache')) is None

def test_success_gated_cd_exact(home):
    assert guard.check_shell('cd /unrelated && rm -rf .hermes', cwd=str(home.parent)) is None

def test_or_rhs_uses_old_context(home):
    assert guard.check_shell('cd ~ || rm -rf .hermes', cwd=str(home / 'cache')) is None
