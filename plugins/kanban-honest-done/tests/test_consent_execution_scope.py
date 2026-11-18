"""Only terminal command text proven wholly inert can exempt consent matches."""
import pytest

from test_consent_raw_parity import consent, core, MAIN_WRITE_RE

TARGET = 't_12345678'
CALLS = [
    f"hermes chat -q 'hermes kanban comment {TARGET} done'",
    f"hermes -z 'hermes kanban comment {TARGET} done'",
    f"hermes kanban block {TARGET} 'will comment after review'",
    f"hermes kanban complete {TARGET} --summary 'notify owner'",
    f"echo 'hermes kanban comment {TARGET} done'; hermes chat -q hi",
    f"hermes chat <<'EOF'\nhermes kanban comment {TARGET} done\nEOF",
]
DATA = f"echo 'hermes kanban comment {TARGET} done'"


@pytest.mark.parametrize('command', CALLS)
@pytest.mark.parametrize('tool', ['terminal', 'run_in_user_terminal'])
def test_hermes_command_keeps_raw_main_detector(command, tool, tmp_path):
    assert core._shell_commands().within_inert_grammar(command)
    assert MAIN_WRITE_RE.search(command)
    assert consent.write_texts(tool, {'command': command}) == [command]
    env = {'HERMES_HOME': str(tmp_path)}
    consent.record('kanban_comment', {'task_id': TARGET},
                   'BLOCKED: User denied', 'scope-session', env=env, now=100)
    assert consent.check_relay(tool, {'command': command}, ['scope-session'], env=env, now=101)


@pytest.mark.parametrize('tool', ['terminal', 'run_in_user_terminal', 'process', 'process_manage'])
@pytest.mark.parametrize('field', ['input', 'data'])
def test_input_fields_keep_raw_main_detector(tool, field, tmp_path):
    args = {'action': 'submit', field: DATA}
    assert MAIN_WRITE_RE.search(DATA)
    assert consent.write_texts(tool, args) == [DATA]
    env = {'HERMES_HOME': str(tmp_path)}
    consent.record('kanban_comment', {'task_id': TARGET},
                   'BLOCKED: User denied', 'scope-session', env=env, now=100)
    assert consent.check_relay(tool, args, ['scope-session'], env=env, now=101)


@pytest.mark.parametrize('tool', ['process', 'process_manage'])
def test_process_command_is_not_a_fresh_shell(tool):
    assert consent.write_texts(tool, {'command': DATA}) == [DATA]


@pytest.mark.parametrize('tool', ['terminal', 'run_in_user_terminal'])
def test_only_terminal_command_is_exempt(tool):
    assert consent.write_texts(tool, {'command': DATA}) == []
    args = {'command': DATA, 'data': DATA}
    assert consent.write_texts(tool, args) == [DATA + ' ; ' + DATA]
