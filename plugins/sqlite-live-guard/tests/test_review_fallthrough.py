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

@pytest.mark.parametrize('boundary', [';', '\n', '||', '&'])
@pytest.mark.parametrize('alias', [False, True])
@pytest.mark.parametrize('wrapper', ['', 'command ', 'bash', 'sh', 'ssh'])
def test_success_chain_failure_rejoins(home, boundary, alias, wrapper):
    command = 'cd /nonexistent && echo done ' + boundary + ' rm -rf ' + ('"$D"' if alias else '.hermes')
    if wrapper == 'command ':
        command = wrapper + command
    if alias:
        command = 'D=.hermes; ' + command
    if wrapper in {'bash', 'sh'}:
        command = wrapper + " -c '" + command + "'"
    elif wrapper == 'ssh':
        command = "ssh example.com 'cd ~ && " + command + "'"

    assert guard.check_shell(command, cwd=str(home.parent))


@pytest.fixture
def lexical_home(monkeypatch):
    # No writes at these synthetic paths. A physical fixture below the worker's
    # real ~/.hermes makes even its parent look live and masks cwd regressions.
    home = Path('/home/sqlite-guard-fixture/.hermes')
    monkeypatch.setenv('HOME', str(home.parent))
    monkeypatch.setenv('HERMES_HOME', str(home))
    return home


@pytest.mark.parametrize('boundary', [';', '\n', '&&', '&'])
@pytest.mark.parametrize('alias', [False, True])
@pytest.mark.parametrize('wrapper', ['', 'command ', 'bash', 'sh', 'ssh'])
@pytest.mark.parametrize('operand', ['kanban.db', '*'])
def test_or_success_rejoins(lexical_home, boundary, alias, wrapper, operand):
    home = lexical_home
    target = '"$D"' if alias else '~/.hermes'
    command = 'cd ' + target + ' || true ' + boundary + ' rm -rf ' + operand
    if wrapper == 'command ':
        command = wrapper + command
    if alias:
        command = 'D=~/.hermes; ' + command
    if wrapper in {'bash', 'sh'}:
        command = wrapper + " -c '" + command + "'"
    elif wrapper == 'ssh':
        command = "ssh example.com '" + command + "'"
    assert guard.check_shell(command, cwd=str(home.parent))


@pytest.mark.parametrize('boundary', [';', '\n', '&&', '&'])
@pytest.mark.parametrize('operand', ['kanban.db', '*'])
@pytest.mark.parametrize('analyzer', ['base', 'context'])
def test_or_relative_success_each_analyzer(lexical_home, boundary, operand, analyzer):
    home = lexical_home
    command = 'cd .hermes || true ' + boundary + ' rm -rf ' + operand
    if analyzer == 'base':
        result = guard._check_shell_base(command, cwd=str(home.parent))
    else:
        from types import SimpleNamespace
        guard.check_shell('true')
        result = guard._SHELL_CONTEXT.check(command, SimpleNamespace(**vars(guard)), str(home.parent))
    assert result


@pytest.mark.parametrize('prefix', ['', 'command ', 'bash -c '])
def test_or_exit_success_rejoins(lexical_home, prefix):
    home = lexical_home
    command = 'cd .hermes || exit; true; rm -f kanban.db'
    command = prefix + ("'" + command + "'" if prefix == 'bash -c ' else command)
    assert guard.check_shell(command, cwd=str(home.parent))


@pytest.mark.parametrize('operator', ['&&', '||'])
@pytest.mark.parametrize('tail', ['', ' || exit'])
@pytest.mark.parametrize('wrapper', ['', 'bash', 'sh', 'ssh'])
@pytest.mark.parametrize('alias', [False, True])
def test_cd_after_pipeline_survives(lexical_home, operator, tail, wrapper, alias):
    target = '"$D"' if alias else '~/.hermes'
    command = 'true | true ' + operator + ' cd ' + target + tail + '; rm -rf *'
    if alias:
        command = 'D=~/.hermes; ' + command
    if wrapper in {'bash', 'sh'}:
        command = wrapper + " -c '" + command + "'"
    elif wrapper == 'ssh':
        command = "ssh example.com '" + command + "'"
    assert guard.check_shell(command, cwd=str(lexical_home.parent))


def test_pipeline_cd_does_not_change_parent(lexical_home):
    assert guard.check_shell('D=.hermes; true | cd "$D"; rm -rf *', cwd=str(lexical_home.parent)) is None


def test_or_rhs_uses_old_context(home):
    assert guard.check_shell('cd ~ || rm -rf .hermes', cwd=str(home / 'cache')) is None
