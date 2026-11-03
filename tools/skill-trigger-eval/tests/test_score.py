from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import router  # noqa: E402
import score as sc  # noqa: E402


def row(skill, kind, aid, run, loaded, instead=None):
    return {"skill": skill, "kind": kind, "ask_id": aid, "run": run, "loaded": loaded, "instead": instead}


def test_recall_near_miss_and_false_loads():
    rows = [
        row("a", "should", "a#s0", 0, ["a"]), row("a", "should", "a#s0", 1, ["a", "b"]), row("a", "should", "a#s0", 2, []),
        row("a", "near_miss", "a#n0", 0, ["a"]), row("a", "near_miss", "a#n0", 1, ["c"], instead="c"),
        row("b", "should", "b#s0", 0, ["b"]),
    ]
    m = sc.score(rows)
    assert m["recall"] == {"a": 2 / 3, "b": 1.0}
    assert m["near_miss_ok"] == {"a": 0.5}
    # a: loaded on its own near-miss once; b: loaded on a's ask once; c was the named winner -> not false.
    assert m["false_loads"] == {"a": 1, "b": 1}
    assert m["loads"] == {"a": 3, "b": 2, "c": 1}
    assert abs(m["false_load_rate"]["a"] - 1 / 3) < 1e-9 and m["false_load_rate"]["c"] == 0
    assert abs(m["mean_recall"] - (2 / 3 + 1) / 2) < 1e-9
    assert abs(m["micro_recall"] - 3 / 4) < 1e-9


def test_collisions_need_alternation_not_co_loading():
    rows = [row("a", "should", "a#s0", 0, ["a", "b"]), row("a", "should", "a#s0", 1, ["a", "b"]),  # co-load only
            row("c", "should", "c#s0", 0, ["c"]), row("c", "should", "c#s0", 1, ["d"]), row("c", "should", "c#s0", 2, ["c"])]
    m = sc.score(rows)
    assert m["collisions"] == {"c|d": ["c#s0"]}
    assert sc.collision_skills(m) == {"c", "d"}


def test_steal_needs_two_thirds_of_runs():
    rows = [row("a", "should", "a#s0", i, ["b"]) for i in range(2)] + [row("a", "should", "a#s0", 2, ["a"])]
    rows += [row("x", "should", "x#s0", 0, ["y"]), row("x", "should", "x#s0", 1, ["x"]), row("x", "should", "x#s0", 2, ["x"])]
    m = sc.score(rows)
    assert m["steals"] == {"a>b": ["a#s0"]}


def test_errors_are_not_scored():
    rows = [row("a", "should", "a#s0", 0, ["a"]), dict(row("a", "should", "a#s0", 1, []), error="boom")]
    m = sc.score(rows)
    assert m["recall"] == {"a": 1.0} and m["errors"] == 1


def test_weakest_orders_by_recall_then_false_loads():
    rows = [row("a", "should", "a#s0", 0, []), row("b", "should", "b#s0", 0, ["b"]),
            row("c", "should", "c#s0", 0, ["a"]), row("d", "should", "d#s0", 0, [])]
    m = sc.score(rows)
    assert sc.weakest(m, 3) == ["a", "c", "d"]  # all recall 0; a has a false load, then name order
    assert sc.weakest(m, 2, among={"b", "d"}) == ["d", "b"]


def test_report_renders_tables():
    m = sc.score([row("a", "should", "a#s0", 0, ["a"]), row("a", "near_miss", "a#n0", 0, ["a"])])
    md = sc.render_report("T", m, owned={"a"})
    assert "| a | y | 1.00 | 0.00 | 1 | 0.50 |" in md


def test_parse_reply_records_skill_view_order_and_ignores_reference_loads():
    reply = {"content": [
        {"type": "text", "text": "let me check"},
        {"type": "tool_use", "name": "skill_view", "input": {"name": "b"}},
        {"type": "tool_use", "name": "skill_view", "input": {"name": "a", "file_path": "references/x.md"}},
        {"type": "tool_use", "name": "terminal", "input": {"command": "ls"}},
        {"type": "tool_use", "name": "skill_view", "input": {"name": "a"}},
        {"type": "tool_use", "name": "skill_view", "input": {"name": "b"}},
    ]}
    out = router.parse_reply(reply)
    assert out["loaded"] == ["b", "a"]
    assert out["tools"] == ["skill_view", "skill_view", "terminal", "skill_view", "skill_view"]


def test_build_body_offers_skill_view_among_other_tools():
    body = router.build_body("SYS", "hi", "m")
    names = [t["name"] for t in body["tools"]]
    assert names[0] == "skill_view" and "terminal" in names and len(names) >= 5
    assert body["system"][0]["text"] == "SYS" and body["messages"] == [{"role": "user", "content": "hi"}]
