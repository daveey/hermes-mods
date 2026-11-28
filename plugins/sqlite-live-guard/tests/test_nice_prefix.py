"""NICE-1: exact middleware contract, not a general shell preamble exemption."""
import importlib.util
import json
from pathlib import Path
import sys

import pytest

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parent
HOME = '/home/sqlite-followup-fixture'
PREFIX = ('# kanban-worker-nice terminal floor\n'
          'if [ "$(nice)" -lt 10 ]; then renice -n 10 -p "${BASHPID:-$$}" >/dev/null || exit $?; fi\n')


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


guard = load('nice_contract_guard', PLUGIN / 'guard.py')


@pytest.fixture(autouse=True)
def lexical_home(monkeypatch):
    monkeypatch.setenv('HOME', HOME)
    monkeypatch.setenv('HERMES_HOME', HOME + '/.hermes')


@pytest.fixture
def real_prefix(monkeypatch):
    nice = load('nice_contract_producer', PLUGIN.parent / 'kanban-worker-nice/__init__.py')
    monkeypatch.setattr(nice.sys, 'platform', 'linux')
    monkeypatch.setattr(nice.socket, 'gethostname', lambda: 'protected-fixture')
    monkeypatch.setenv('HERMES_TERMINAL_NICE_HOSTS', 'protected-fixture')
    monkeypatch.setenv('HERMES_KANBAN_WORKER_NICE', '10')
    for name in ('HERMES_KANBAN_TASK', 'HERMES_KANBAN_WORKER_TASK', 'HUB_NICE_OK'):
        monkeypatch.delenv(name, raising=False)
    command = 'echo contract'
    actual = nice.terminal_request('terminal', {'command': command})['args']['command']
    assert actual == PREFIX + command  # fail if the producer's bytes change
    return actual[:-len(command)]


ALLOWS = [
    'hermes kanban show t_x | head -5',
    'ls | wc -l > /tmp/n.txt',
    "gh pr view 1 --json state --jq '{state}' | cat >/dev/null",
]


@pytest.mark.parametrize('command', ALLOWS)
def test_synthetic_live_regression(real_prefix, command):
    assert guard.check_shell(real_prefix + command, HOME) is None


def test_existing_set_plus_real_redirect_refusal_is_preserved(real_prefix):
    # Prefix transparency is not authorization to relax an unprefixed refusal.
    command = 'set -euo pipefail; ls | wc -l > /tmp/n.txt'
    assert guard.check_shell(command, HOME)
    assert guard.check_shell(real_prefix + command, HOME)


def test_protected_mutation_still_refused(real_prefix):
    assert guard.check_shell(real_prefix + 'cd ~/.hermes; rm -rf state.db', HOME)


def corpus_commands(session):
    """All collected parametrizations plus the generated and saved historic corpora.

    Include every string leaf, even data/control parameters: a conservative superset
    avoids silently missing command parameters with different names or tuple shapes.
    """
    commands = set()
    def add(value):
        if isinstance(value, str):
            commands.add(value)
        elif isinstance(value, dict):
            for v in value.values():
                add(v)
        elif isinstance(value, (list, tuple, set)):
            for v in value:
                add(v)
    for item in session.items:
        if hasattr(item, 'callspec'):
            add(item.callspec.params)
    sys.path.insert(0, str(HERE))
    import diff_corpus
    import followup_corpus
    add(diff_corpus.corpus())
    add(followup_corpus.shell_cases())
    for path in (HERE / 'fixtures').glob('*.json'):
        add(json.loads(path.read_text()))
    return sorted(commands)


@pytest.mark.parametrize('target', [1, 10, 19])
def test_real_producer_contract_over_every_corpus_command(real_prefix, request, target):
    real_prefix = real_prefix.replace('-lt 10', f'-lt {target}').replace('-n 10', f'-n {target}')
    commands = corpus_commands(request.session)
    assert len(commands) > 4000
    for command in commands:
        assert bool(guard.check_shell(real_prefix + command, HOME)) == bool(guard.check_shell(command, HOME)), command


@pytest.mark.parametrize('target', [1, 10, 19])
@pytest.mark.parametrize('marker', [True, False])
def test_exact_prefix_range(target, marker):
    prefix = PREFIX.replace('-lt 10', f'-lt {target}').replace('-n 10', f'-n {target}')
    if not marker:
        prefix = prefix.split('\n', 1)[1]
    for command in ALLOWS:
        assert guard.check_shell(prefix + command, HOME) is None


@pytest.mark.parametrize('prefix', [
    PREFIX.replace('-n 10', '-n 11'),
    PREFIX.replace('then renice', 'then  renice'),
    'echo before;\n' + PREFIX,
    PREFIX.replace('>/dev/null', '>state.db'),
    PREFIX.replace('then renice', 'then cd ~/.hermes; renice'),
    PREFIX.rstrip('\n'),
    PREFIX.replace('-lt 10', '-lt 0').replace('-n 10', '-n 0'),
    PREFIX.replace('-lt 10', '-lt 20').replace('-n 10', '-n 20'),
    PREFIX.replace('-lt 10', '-lt 01').replace('-n 10', '-n 01'),
])
def test_near_misses_are_not_stripped(prefix):
    assert guard.check_shell(prefix + ALLOWS[1], HOME)


def test_strip_once_only():
    assert guard.check_shell(PREFIX + PREFIX + ALLOWS[1], HOME)


@pytest.mark.parametrize('target', ['/dev/null', "'/dev/null'", '"/dev/null"'])
@pytest.mark.parametrize('operator', ['>', '>>', '>|', '&>', '&>>', '<>'])
def test_literal_null_is_not_file_mutation(target, operator):
    assert guard.check_shell(f'set -e; echo ok {operator}{target}', HOME) is None
    # Same construct with an actual file target must still fail closed.
    assert guard.check_shell(f'set -e; echo ok {operator}state.db', HOME)


@pytest.mark.parametrize('target', ['$NULL', '${NULL}', '/dev/"null"', '/dev/null/../null', '"$(echo /dev/null)"'])
def test_nonliteral_null_is_unchanged(target):
    assert guard.check_shell(f'set -e; echo ok >{target}', HOME)


def test_refusal_explains_supported_alternatives():
    why = guard.check_shell('set -e; echo ok >/tmp/example.txt', HOME)
    for text in ('unsupported shell state', 'backticks', 'braces', 'pipe', '$PWD', '$OLDPWD', '~+', '~-',
                 'set', 'read', 'mapfile', 'eval', 'source', 'printf -v', 'pushd', 'popd',
                 'functions', 'write_file', 'literal absolute paths', 'swap_db.py'):
        assert text in why


def test_prefix_preserves_550_blocking_repros(real_prefix):
    sys.path.insert(0, str(HERE))
    from test_review_550 import SHELL_CASES
    from followup_corpus import shell_cases
    commands = SHELL_CASES + [r['command'] for r in shell_cases()]
    for command in commands:
        assert guard.check_shell(real_prefix + command, HOME), command
