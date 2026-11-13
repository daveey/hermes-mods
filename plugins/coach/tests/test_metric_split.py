"""metric_split (self-heal agent-05bb25439d): a handoff or reply that reports a metric number (BPB, perplexity,
accuracy, loss) with no val/test split in the same clause gets a hint to name the split. Hint only, never a refusal.

Incident: a research worker's kanban_complete summary said "the tuned arm reaches 1.787±0.009 BPB". The source README
said "1.787 ± 0.009 val BPB" and "Test BPB is 1.775". The chat read the bare "BPB" as test and put 1.787 in a "test"
table next to test-split baselines; the user saw the wrong table for 40 minutes."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


rules = load("coach_rules_metric_split_test", ROOT / "agent_rules.py")

OK = json.dumps({"ok": True})

# the incident handoff summary's shape (names generalized)
INCIDENT = ("Merged PR #12. With the per-arm LR sweep and periodic carry resets, the tuned arm reaches "
            "1.787±0.009 BPB on the books corpus over 3 seeds, still behind both baselines.")

FIRES = [
    INCIDENT,
    "Best arm: 1.69 bpb (3 seeds).",
    "The atom reaches 92.4% accuracy on sequential MNIST.",
    "Perplexity drops from 41.2 perplexity to 37.9 perplexity after the sweep.",
    "Final loss 0.412 after 6000 batches.",
    "Reported BPB = 1.775 for the matched LSTM.",
]

QUIET = [
    "1.787 ± 0.009 val BPB (3 seeds). Test BPB is 1.775.",  # the README line
    ("Correction to earlier messages: I compared the new model's validation score (1.787) with the baselines' test "
     "scores. On test BPB it's 1.775, so the gap to the best baseline is 0.177. The conclusion doesn't change."),
    "Spend so far: $2.572 on the 4090 box.",
    "The tuned arm reaches 1.787 BPB on validation and 1.775 on test.",
    "Train loss 0.31, val loss 0.44.",
    "held-out accuracy 91.3%",
    "Landed 3 commits; 14 tests pass.",
    "No data loss: 0 rows dropped.",
    "| new model | 1.787 | 1.775 |",
]


def coach():
    return rules.AgentCoach(None, signals=None)


@pytest.mark.parametrize("text", FIRES)
def test_bare_metric_is_found(text):
    assert rules.bare_metrics(text)


@pytest.mark.parametrize("text", QUIET)
def test_labelled_or_non_metric_numbers_are_quiet(text):
    assert rules.bare_metrics(text) == []


def test_incident_summary_names_the_number():
    assert any("1.787" in h for h in rules.bare_metrics(INCIDENT))


def test_kanban_complete_incident_gets_hint():
    c = coach()
    c.observe("s1", "kanban_complete", {"summary": INCIDENT, "metadata": {"pr": 419}}, OK)
    hint = c.take("s1") or ""
    assert "1.787" in hint and "split" in hint and "val" in hint and "test" in hint


@pytest.mark.parametrize("tool,args", [
    ("kanban_comment", {"task_id": "t_x", "body": INCIDENT}),
    ("kanban_create", {"title": "write up", "body": "Baseline: " + INCIDENT}),
    ("mcp__kanban_complete", {"result": INCIDENT}),
    ("tool_call", {"calls": [{"name": "kanban_comment", "arguments": {"task_id": "t_x", "body": INCIDENT}}]}),
])
def test_other_handoff_tools_get_hint(tool, args):
    c = coach()
    c.observe("s1", tool, args, OK)
    assert "1.787" in (c.take("s1") or "")


def test_labelled_handoff_and_other_tools_are_quiet():
    c = coach()
    c.observe("s1", "kanban_complete", {"summary": "1.787 ± 0.009 val BPB; test BPB 1.775."}, OK)
    c.observe("s1", "terminal", {"command": f"echo '{INCIDENT}'"}, OK)
    assert "split" not in (c.take("s1") or "")


def test_hint_lands_on_the_same_call_via_transform_and_fires_once_per_number():
    c = coach()
    c.check_metric_split("s1", "kanban_complete", {"summary": INCIDENT})
    assert "1.787" in (c.take("s1") or "")
    c.check_metric_split("s1", "kanban_comment", {"body": INCIDENT})
    c.observe("s1", "kanban_comment", {"body": INCIDENT}, OK)
    assert c.take("s1") is None


def test_final_reply_hint_rides_the_next_turn():
    c = coach()
    c.turn_start("s1", "how did the new model do?")
    c.turn_end("s1", "The tuned model reaches 1.787 ± 0.009 BPB, behind the baseline.")
    hint = c.turn_start("s1", "ok") or ""
    assert "1.787" in hint and "split" in hint
    c.turn_end("s1", "Corrected: 1.787 val BPB; on test BPB it's 1.775.")
    assert "split" not in (c.turn_start("s1", "thanks") or "")


def test_transform_hook_wires_the_rule():
    src = (ROOT / "__init__.py").read_text()
    body = src[src.index("def transform_tool_result"):]
    body = body[:body.index("\n    def ", 10)]
    assert "check_metric_split" in body


def test_readme_documents_the_rule():
    assert "`metric_split`" in (ROOT / "README.md").read_text()
