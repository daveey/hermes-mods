"""Offline synthetic gold harness check; no live data or model-quality claims."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("kind", ["reply", "fix"])
def test_bakeoff_uses_bounded_production_state_and_scores_gold(tmp_path, capsys, kind):
    bake = load("synthetic_bakeoff", ROOT / "eval/system_one_bakeoff.py")
    screen = bake._load("synthetic_screen", ROOT / "system_one.py")
    log = ["-> read_file(synthetic.txt) => " + "x" * 9000,
           "-> kanban_create(synthetic)", "-> kanban_comment(synthetic)"]
    sample = {"positive": {"id": "positive", "log": repr(log)}}
    seen = []

    class Client:
        def decide(self, name, state, questions, timeout):
            seen.append(state)
            return {key: {"noul": 0.9} for key in questions}

    if kind == "reply":
        state_fn = lambda s: screen.reply_state("ask", bake.as_list(s["log"]), "reply")
        questions, field, budget, rule, jkey = screen.REPLY_Q, "tool_log", 5000, "confirm", "confirm"
    else:
        state_fn = lambda s: screen.fix_state("reply", bake.as_list(s["log"]), "still broken")
        questions, field, budget, rule, jkey = screen.FIX_Q, "agent_tool_calls_that_turn", 3000, "p_failed_fix", "failed_fix"
    answers = bake.run(kind, sample, state_fn, questions, Client(), tmp_path)
    assert seen[0][field].endswith("\n".join(log[-2:]))
    assert len(seen[0][field]) <= budget
    assert bake.run(kind, sample, state_fn, questions, Client(), tmp_path) == answers
    assert len(seen) == 1  # persisted rows resume without a second call
    gold = {"positive": {rule: 1.0}}
    detected = {"positive": {rule: 0.95}}
    bake.report(kind, gold, detected, answers, rule, jkey, 0.5)
    output = capsys.readouterr().out
    assert "gold+=1" in output
    assert output.count("correct catches lost 0/1") == 3
