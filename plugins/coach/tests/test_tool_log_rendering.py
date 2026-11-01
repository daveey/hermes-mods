"""Synthetic prompt-boundary regressions; never use captured conversations here."""
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "tool_log_reply_check", Path(__file__).resolve().parents[1] / "reply_check.py")
rc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rc)


@pytest.fixture(params=[(rc.judge_prompt, 5000), (rc.confirm_prompt, 7000)],
                ids=["screen", "confirm"])
def render(request):
    prompt, budget = request.param

    def tool_section(log):
        text = prompt("Investigate and repair the synthetic issue.", log, "Repair assigned.")
        return text.split("TOOL LOG THIS TURN (", 1)[1].split("):\n", 1)[1].split(
            "\n\nAGENT'S FINAL MESSAGE:", 1)[0]

    return tool_section, budget


def test_late_actions_survive_long_earlier_read(render):
    section, budget = render
    create = '-> kanban_create({"title": "Repair synthetic parser"}) => {"id": "synthetic-task"}'
    comment = '-> kanban_comment({"task_id": "synthetic-task", "body": "Repair started"}) => {"ok": true}'
    log = ['-> read_file({"path": "diagnostics.txt"}) => ' + 'diagnostic ' * 1500, create, comment]
    result = section(log)
    assert create in result
    assert comment in result
    assert result.index(create) < result.index(comment)
    assert len(result) <= budget
    assert "omitted" in result


def test_oversized_single_call_keeps_identity_and_result(render):
    section, budget = render
    start = '-> read_file({"path": "synthetic.txt"}) => '
    end = 'END_OF_SYNTHETIC_RESULT'
    result = section([start + 'read-only data\n' * 2000 + end])
    assert result.startswith(start)
    assert result.endswith(end)
    assert '[tool call middle omitted]' in result
    assert len(result) <= 1000 <= budget
    assert 'kanban_create' not in result
    assert 'kanban_comment' not in result


@pytest.mark.parametrize("log", [[], ['-> read_file(short.txt)'],
                                     ['-> read_file(first.txt)', '-> search_files(second)']])
def test_empty_and_short_read_only_logs_unchanged(render, log):
    section, budget = render
    assert section(log) == ('\n'.join(log) or '(no tool calls this turn)')
    assert len(section(log)) <= budget


def test_more_than_forty_calls_keeps_only_last_forty_in_order(render):
    section, budget = render
    log = [f'-> read_file(synthetic-{i:03d}.txt)' for i in range(45)]
    result = section(log)
    assert result.splitlines() == ['[older tool calls omitted]', *log[-40:]]
    assert len(result) <= budget


def test_total_budget_drops_whole_old_calls_not_recent_actions(render):
    section, budget = render
    log = [f'-> read_file(synthetic-{i:03d}.txt) => ' + 'x' * 850 for i in range(20)]
    log += ['-> kanban_create(synthetic repair)', '-> kanban_comment(synthetic progress)']
    result = section(log)
    lines = result.splitlines()
    assert lines[0] == '[older tool calls omitted]'
    retained = lines[1:]
    assert retained == log[-len(retained):]
    assert retained[-2:] == log[-2:]
    assert len(result) <= budget
    assert len(retained) < len(log)


@pytest.mark.parametrize("size", [999, 1000, 1001])
def test_per_entry_cap_boundary(render, size):
    section, _ = render
    call = '-> read_file(' + 'x' * (size - len('-> read_file()')) + ')'
    result = section([call])
    if size <= 1000:
        assert result == call
    else:
        assert len(result) == 1000
        assert '[tool call middle omitted]' in result
        assert result.startswith('-> read_file(') and result.endswith(')')


def test_real_rich_line_producer_retains_late_create_and_comment(render):
    section, _ = render
    spec = importlib.util.spec_from_file_location('tool_log_agent_rules',
                                                Path(__file__).resolve().parents[1] / 'agent_rules.py')
    rules = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rules)
    log = [rules.rich_tool_line('terminal', {'command': 'inspect ' + 'x' * 300}) for _ in range(38)]
    log += [rules.rich_tool_line('kanban_create', {'title': 'Synthetic repair', 'body': 'Fix parser',
                                                'assignee': 'worker'}),
            rules.rich_tool_line('kanban_comment', {'task_id': 'synthetic-task', 'body': 'Started'})]
    result = section(log)
    assert result.endswith('\n'.join(log[-2:]))
    assert '[older tool calls omitted]' in result
