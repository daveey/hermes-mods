"""Self-heal judges keep a long turn's newest tool calls inside their existing prompt budgets."""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(f"selfheal_log_test_{name}", ROOT / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sh = load("self_heal")
CREATE = "kanban_create(title='late follow-up') -> {\"ok\": true, \"task_id\": \"t_late\"}"
COMMENT = "kanban_comment(task_id='t_late', body='evidence') -> {\"ok\": true}"


def long_turn(reads):
    """`reads` verbose reads (each over the 200-char per-call cap), then the turn's final two actions."""
    return [f"read_file(path='notes/{i}.md') -> " + "x" * 300 for i in range(reads)] + [CREATE, COMMENT]


def section(prompt, start, end):
    return prompt.split(start, 1)[1].split(end, 1)[0]


# entry path -> (prompt builder, section start, section end, call window, character budget)
PATHS = {
    "complaint": (lambda log: sh.complaint_prompt("previous reply", "still broken", log),
                  "TOOL LOG OF THE AGENT'S PREVIOUS TURN:\n", "\n\nUSER'S NEW MESSAGE", 25, 2500),
    "admission": (lambda log: sh.admission_prompt("I could not finish it", log, "fix the build"),
                  "TOOL LOG THIS TURN:\n", "\n\nAGENT'S FINAL MESSAGE", 30, 3500),
}


@pytest.mark.parametrize("path", sorted(PATHS))
def test_late_actions_survive_a_long_turn(path):
    build, start, end, window, budget = PATHS[path]
    text = section(build(long_turn(window)), start, end)
    assert CREATE in text and COMMENT in text
    assert text.endswith(COMMENT)
    assert text.startswith("[older tool calls omitted]")
    assert len(text) <= budget
    assert all(len(line) <= 200 for line in text.splitlines())


@pytest.mark.parametrize("path", sorted(PATHS))
def test_a_log_within_budget_renders_as_before(path):
    build, start, end, window, budget = PATHS[path]
    log = ["terminal(command='ls') -> ok", "read_file(path='a.md') -> " + "y" * 400, CREATE]
    assert section(build(log), start, end) == "\n".join(str(x)[:200] for x in log[-window:])[:budget]


@pytest.mark.parametrize("path", sorted(PATHS))
def test_an_empty_log_keeps_its_placeholder(path):
    build, start, end, _, _ = PATHS[path]
    assert section(build([]), start, end) == "(no tool calls)"
    assert section(build(None), start, end) == "(no tool calls)"


def judge(seen):
    def llm(system, prompt):
        seen.append(prompt)
        return json.dumps({"p_defect": 0.0, "kind": "none", "component": "other", "symptom": "none"})
    return llm


def test_complaint_verdict_sends_the_late_actions_to_its_judge():
    seen = []
    sh.complaint_verdict("previous reply", "still broken", judge(seen), tool_log=long_turn(25))
    assert seen and all(CREATE in p and COMMENT in p for p in seen)


def test_audit_verdict_sends_the_late_actions_to_its_judge():
    seen = []
    sh.audit_verdict("I could not finish it", judge(seen), tool_log=long_turn(30), ask="fix the build")
    assert seen and all(CREATE in p and COMMENT in p for p in seen)
