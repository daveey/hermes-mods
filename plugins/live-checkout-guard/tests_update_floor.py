"""Replay wave corpora against the frozen original refusal floor (never execute)."""
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_main_floor_and_real_nice_prefix_contract(monkeypatch):
    guard = load('update_floor_head', HERE / 'guard.py')
    nice = load('update_floor_nice', HERE.parent / 'kanban-worker-nice' / '__init__.py')
    monkeypatch.setenv('LIVE_CHECKOUT_ROOT', '/fixture/live')
    monkeypatch.setenv('LIVE_CORE_CHECKOUT', '/fixture/core')
    monkeypatch.setenv('HERMES_TERMINAL_NICE_HOSTS', nice.socket.gethostname())
    monkeypatch.setenv('HERMES_KANBAN_WORKER_NICE', '10')
    for k in ['HERMES_KANBAN_TASK', 'HERMES_KANBAN_WORKER_TASK', 'HUB_NICE_OK']:
        monkeypatch.delenv(k, raising=False)
    corpus = json.loads((HERE / 'fixtures/update_main_floor.json').read_text())
    assert corpus['source'] == 'a362991f2b57e59e52d2e66c2b62ebc64e3a6ada'
    changed = []
    for row in corpus['rows']:
        command = row['command']
        plain = guard.check_shell(command, corpus['cwd'])
        assert plain or not row['refused'], command
        if plain and not row['refused']:
            changed.append(command)
        wrapped = nice.terminal_request('terminal', {'command': command})
        # Explicit opt-out commands intentionally do not get a prefix.
        actual = wrapped['args']['command'] if wrapped else command
        assert guard.check_shell(actual, corpus['cwd']) == plain, command
    assert changed == sorted([
        "bash -c \"printf '%s\\\\n' 'hermes update --yes' | bash\"",
        "cat <<'EOF' | bash\nhermes update --yes\nEOF",
        "cat <<'EOF' | cat | bash -s\nhermes update --yes\nEOF",
        "cat <<-'EOF' | env bash\n\thermes update --yes\nEOF",
        'echo hermes update --yes | sh',
        "printf '%s\\n' 'hermes update --yes' | bash",
        "printf 'hermes update --yes\\n' | cat | env bash -s",
    ])
    print('wave corpus:', len(corpus['rows']), 'new allows: 0; intended new refusals:', len(changed))
