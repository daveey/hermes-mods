"""Shell substitution boundaries must distinguish executable code from quoted data."""
import importlib.util
import time
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "coach_substitution_rules", Path(__file__).resolve().parents[1] / "agent_rules.py"
)
assert spec is not None and spec.loader is not None
rules = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rules)


@pytest.mark.parametrize(("suffix", "flagged"), [
    ('git rev-parse HEAD; echo "x: $(sqlite3 db "select 1 where a >= 2")"', False),
    ('echo "$(date > out.txt)"', True),
    ('git rev-parse HEAD; sqlite3 db "select 1 where a >= 2"', False),
    ('sqlite3 db "select strftime(\'%H\', t) where a >= 2"', False),
    ('echo hi > f.txt', True),
    ('echo "`date > out.txt`"', True),
    ('echo "$((a >= 2))"', False),
    ('echo $((a >= 2))', False),
    ('echo $(date > out.txt)', True),
    ('echo "$(echo "$(date > out.txt)")"', True),
    ('echo "$(sqlite3 db \'select 1 where a >= 2\')"', False),
    ('echo \'$(date > out.txt)\'', False),
    ('echo "\\$(date > out.txt)"', False),
    ('echo "\\`date > out.txt\\`"', False),
    ('echo "$( (date > out.txt) )"', True),
    ('echo "$(date)" > out.txt', True),
    ('echo "$(echo $((a >= 2)))" > out.txt', True),
    ('echo "$(echo $(((a >= 2))))" > out.txt', True),
    ("git status # don't care\ngit pull", True),
    (r"echo $'it\'s' && git pull", True),
    ('echo $(( 1 + $(date > out.txt) ))', True),
])
def test_live_checkout_shell_contexts(suffix, flagged):
    live = Path.home() / ".hermes/hermes-agent"
    command = "cd ~/.hermes/hermes-agent && " + suffix
    assert bool(rules.touches_live_checkout("terminal", {"command": command}, live)) is flagged
    assert len(rules._mask_quoted(command)) == len(command)


@pytest.mark.parametrize("command", [
    'echo "unfinished > out.txt',
    'echo $(date > out.txt',
    'echo "$(date > out.txt',
    "echo 'unfinished > out.txt",
])
def test_unterminated_input_is_masked_without_raising(command):
    masked = rules._mask_quoted(command)
    assert len(masked) == len(command)
    assert ">" not in masked


def test_single_quotes_do_not_treat_backslash_as_an_escape():
    command = "echo 'literal\\' ; date > out.txt"
    assert rules._mask_quoted(command).endswith("; date > out.txt")


def test_mask_preserves_offsets_for_later_directory_and_mutation(tmp_path):
    live = tmp_path / "live"
    command = f'echo "some quoted text"; cd "{live}" && git pull'
    masked = rules._mask_quoted(command)
    assert len(masked) == len(command)
    assert masked.index("git pull") == command.index("git pull")
    assert rules.touches_live_checkout("terminal", {"command": command}, live) == str(live)


@pytest.mark.parametrize("prefix", ["$(" * 500, '"$(' * 350, '`"' * 500])
def test_deep_unbalanced_substitutions_do_not_raise(prefix):
    command = prefix + "date > out.txt"
    masked = rules._mask_quoted(command)
    assert len(masked) == len(command)
    assert ">" not in masked


def test_mask_quoted_many_nested_quotes_is_not_quadratic():
    """SHELL_RUNNER_RE used to scan the whole prefix on every quote (~13s at 5000 reps)."""
    command = 'echo "$(a "b" c)" ' * 5000
    started = time.perf_counter()
    masked = rules._mask_quoted(command)
    elapsed = time.perf_counter() - started
    assert len(masked) == len(command)
    assert elapsed < 1.0, f"_mask_quoted took {elapsed:.2f}s on a {len(command)}-byte command"


def test_long_prefix_still_scans_shell_runner_scripts_and_masks_quoted_data():
    filler = 'echo "$(a "b" c)" ' * 200
    quoted = filler + 'echo "select 1 where a >= 2"'
    masked = rules._mask_quoted(quoted)
    assert len(masked) == len(quoted)
    assert ">=" not in masked[len(filler):]
    script = filler + "bash -c 'date > out.txt'"
    scanned = rules._mask_quoted(script)
    assert "date > out.txt" in scanned


def test_bounded_runner_tail_keeps_a_separator_at_the_window_edge():
    """A 64-char window that starts on the separator before the runner still counts."""
    token = "zsh" + " -" + ("c" * 58)
    assert len(token) == 63
    command = "echo " + token + " 'date > out.txt'"
    assert "date > out.txt" in rules._mask_quoted(command)


def test_bounded_runner_tail_does_not_split_a_longer_word():
    """A tail that starts on 'bash' must not treat a longer token as a runner."""
    token = "bash" + " -" + ("c" * 58)
    assert len(token) == 64
    command = "not" + token + " 'date > out.txt'"
    masked = rules._mask_quoted(command)
    assert ">" not in masked
    real = "echo;" + token + " 'date > out.txt'"
    assert "date > out.txt" in rules._mask_quoted(real)
