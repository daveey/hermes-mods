"""Updater stdin policy: real harmless shell oracle plus the registered hook."""
import importlib.util
import os
from pathlib import Path
import shlex
import subprocess

import pytest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('pipeline_plugin', HERE / '__init__.py')
assert spec is not None and spec.loader is not None
plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin)


@pytest.fixture
def hook(monkeypatch, tmp_path):
    monkeypatch.setenv('LIVE_CHECKOUT_GUARD', '1')
    monkeypatch.setenv('LIVE_CHECKOUT_ROOT', str(tmp_path / 'live'))
    monkeypatch.setenv('LIVE_CORE_CHECKOUT', str(tmp_path / 'core'))
    registered = {}

    class Context:
        def register_hook(self, name, fn, **kwargs):
            registered[name] = fn

    plugin.register(Context())
    return registered['pre_tool_call']


@pytest.mark.parametrize('command,executes', [
    ("cat <<'EOF' | bash\nhermes update --yes\nEOF", True),
    ("cat <<'EOF' | cat | bash -s\nhermes update --yes\nEOF", True),
    ("cat <<-'EOF' | env bash\n\thermes update --yes\nEOF", True),
    ("bash <<'EOF'\nhermes update --yes\nEOF", True),
    ('hermes update --yes', True),
    ("cat <<'EOF'\nhermes update --yes\nEOF", False),
    ("cat <<'EOF' | cat\nhermes update --yes\nEOF", False),
    ("cat <<'EOF' | bash /dev/null\nhermes update --yes\nEOF", False),
    ("cat <<'EOF' | bash -n\nhermes update --yes\nEOF", False),
    ("cat <<'EOF' | bash < /dev/null\nhermes update --yes\nEOF", False),
    ("cat <<'EOF' > /dev/null | bash\nhermes update --yes\nEOF", False),
    ("custom-reader <<'EOF'\nhermes update --yes\nEOF", False),
    ("cat <<'EOF' | custom-reader\nhermes update --yes\nEOF", False),
    ("cat > s.sh <<'EOF'\nhermes update --yes\nEOF\nbash -n s.sh", False),
    ("cat <<'EOF' | bash -c cat\nhermes update --yes\nEOF", False),
    ("sed -n p <<'EOF'\nhermes update --yes\nEOF", False),
    ("awk '{print}' <<'EOF'\nhermes update --yes\nEOF", False),
    ("while read -r line; do echo \"$line\"; done <<'EOF'\nhermes update --yes\nEOF", False),
    ("hermes kanban comment --body-file - <<'EOF'\nhermes update --yes\n`hermes update --yes`\nEOF", False),
    ("printf '%s\\n' 'hermes update --yes' | bash", True),
    ("printf 'hermes update --yes\\n' | cat | env bash -s", True),
    ("echo hermes update --yes | sh", True),
    ("printf '%s\\n' 'hermes update --yes' | cat", False),
    ("echo hermes update --yes", False),
    ("printf '%s' 'echo ' 'hermes update --yes' | bash", False),
    ("printf '%s\\n' 'hermes update --yes' | bash /dev/null", False),
    ("printf '%s\\n' 'hermes update --yes' | bash -n", False),
    ("printf '%s\\n' 'hermes update --yes' | bash < /dev/null", False),
    ("printf '%s\\n' 'hermes update --yes' > /dev/null | bash", False),
    ("bash -c \"printf '%s\\\\n' 'hermes update --yes' | bash\"", True),
])
def test_registered_hook_heredoc_updater_matches_real_shell(hook, tmp_path, command, executes):
    bindir = tmp_path / 'bin'
    bindir.mkdir()
    marker = tmp_path / 'called'
    fake = bindir / 'hermes'
    fake.write_text('#!/bin/sh\nif [ "$1" = update ]; then\nprintf "%s\\n" "$*" > "$AUDIT_MARKER"\nelse\n/bin/cat\nfi\n')
    fake.chmod(0o700)
    reader = bindir / 'custom-reader'
    reader.write_text('#!/bin/sh\n/bin/cat\n')
    reader.chmod(0o700)
    # Explicit isolated environment; no inherited BASH_ENV or shell functions.
    result = subprocess.run(['/bin/bash', '--noprofile', '--norc'], input=command,
                            text=True, capture_output=True, timeout=5, cwd=tmp_path,
                            env={'PATH': str(bindir) + ':/usr/bin:/bin', 'HOME': str(tmp_path),
                                 'AUDIT_MARKER': str(marker)})
    assert result.returncode == 0, result.stderr
    assert marker.exists() is executes
    if executes:
        assert marker.read_text() == 'update --yes\n'
    verdict = hook('terminal', {'command': command, 'workdir': str(tmp_path)})
    if executes:
        assert verdict and verdict['action'] == 'block'
        assert 'unfenced hermes update' in plugin.guard.check_shell(command, str(tmp_path))
    else:
        assert verdict is None


@pytest.mark.parametrize('command', [
    r"printf '%s\n' 'hermes update --yes'",
    r"printf 'echo ok\n%s\n' 'hermes update --yes'",
    r"printf '%s%s\n' 'hermes ' 'update --yes'",
    r"printf '%s\n' 'one' 'two'",
    r"printf '100%%\t%s\n' 'data'",
    r"printf '%s' 'echo ' 'hermes update --yes'",
    'echo hermes update --yes',
])
def test_literal_printer_output_matches_bash(command, tmp_path):
    actual = subprocess.run(['/bin/bash', '--noprofile', '--norc'], input=command,
                            capture_output=True, text=True, timeout=5, cwd=tmp_path,
                            env={'PATH': '/usr/bin:/bin', 'HOME': str(tmp_path)})
    assert actual.returncode == 0, actual.stderr
    expected = plugin.guard._update_printed_script(shlex.split(command))
    # echo's omitted trailing newline is immaterial to parsing; printf's is not.
    assert expected == (actual.stdout.rstrip('\n') if command.startswith('echo ') else actual.stdout)
