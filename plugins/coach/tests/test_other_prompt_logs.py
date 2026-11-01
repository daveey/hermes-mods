"""Synthetic late-action evidence at every remaining coach prompt boundary."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(f"prompt_test_{name}", ROOT / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


s1, so, fl = (load(name) for name in ("system_one", "second_opinion", "fix_loop"))


@pytest.fixture(params=["reply_screen", "diff_review", "fix_judge", "fix_screen"])
def render(request):
    if request.param == "reply_screen":
        return lambda log: s1.reply_state("ask", log, "reply")["tool_log"], 5000
    if request.param == "fix_screen":
        return lambda log: s1.fix_state("reply", log, "still broken")["agent_tool_calls_that_turn"], 3000
    if request.param == "diff_review":
        return lambda log: so.review_prompt("ask", "reply", log, {}).split(
            "TOOL LOG (truncated):\n", 1)[1].split("\n\nGIT DIFF", 1)[0], 5000
    return lambda log: fl.judge_prompt("reply", log, "still broken").split(
        "AGENT'S TOOL CALLS IN THAT TURN (truncated):\n", 1)[1].split("\n\nUSER'S", 1)[0], 3000


def test_late_actions_survive_long_earlier_read(render):
    section, budget = render
    create = '-> kanban_create({"title": "Synthetic repair"}) => {"id": "synthetic-task"}'
    comment = '-> kanban_comment({"task_id": "synthetic-task", "body": "Started"}) => {"ok": true}'
    log = ["-> read_file(synthetic.txt) => " + "diagnostic " * 1500, create, comment]
    result = section(log)
    assert create in result
    assert comment in result
    assert result.index(create) < result.index(comment)
    assert len(result) <= budget
    assert "omitted" in result


def test_many_reads_retain_newest_actions_within_budget(render):
    section, budget = render
    log = [f"-> read_file(synthetic-{i}.txt) => " + "x" * 850 for i in range(45)]
    log += ["-> kanban_create(synthetic repair)", "-> kanban_comment(synthetic progress)"]
    result = section(log)
    assert result.endswith("\n".join(log[-2:]))
    assert result.startswith("[older tool calls omitted]")
    assert len(result) <= budget
