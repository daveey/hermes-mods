"""Tests for skill-size-budget. Run: python3 -m pytest -q -p no:cacheprovider plugins/skill-size-budget/tests"""

import importlib.util
import json
import os
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


plugin = _load("skill_size_budget_plugin", "__init__.py")
budget = plugin.budget
if os.environ.get("SKILL_SIZE_BUDGET_TEST_BASELINE"):  # red-before proof: no budget hook on skill_manage
    plugin._pre_tool_call = lambda **_: None
    plugin._transform_tool_result = lambda **_: None

FM = "---\nname: demo\ndescription: \"Use when testing. Demo.\"\n---\n"


def skill(n_lines, done_means=False, line="- Step text that says what to do and why it matters here."):
    body = ["# Demo", "", "## Procedure", ""] + [f"{line} ({i})" for i in range(n_lines)]
    if done_means:
        body += ["", "## Done means", "- [ ] The thing works?", "- [ ] The test passed?"]
    return FM + "\n".join(body) + "\n"


FILES = {}


def read_current(name, file_path):
    return FILES.get((name, file_path or "SKILL.md"), "" if name in {k[0] for k in FILES} else None)


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    FILES.clear()
    plugin._PENDING.clear()
    monkeypatch.setenv("SKILL_SIZE_BUDGET_LOG", str(tmp_path / "budget.jsonl"))
    for v in ("SKILL_SIZE_BUDGET", "SKILL_SIZE_BUDGET_LINES", "SKILL_SIZE_BUDGET_CHARS"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setattr(plugin, "_current_text", read_current)
    monkeypatch.setattr(plugin, "evaluate",
                        lambda args, who, mode, read_current=read_current, _e=plugin.evaluate: _e(args, who, mode,
                                                                                                    read_current))
    monkeypatch.setattr(plugin, "actor", lambda: "curator")
    yield


def pre(ops, tool_call_id="c1"):
    return plugin._pre_tool_call(tool_name="skill_manage", args={"operations": ops}, session_id="s1",
                                 tool_call_id=tool_call_id)


def post(ops, result, tool_call_id="c1"):
    return plugin._transform_tool_result(tool_name="skill_manage", args={"operations": ops}, result=result,
                                         tool_call_id=tool_call_id)


def grow_op(n=40, anchor="## Procedure\n"):
    new = anchor + "\n" + "\n".join(f"- New rule {i}: always do the careful thing before X." for i in range(n)) + "\n"
    return {"action": "patch", "name": "demo", "old_string": anchor, "new_string": new}


def logged(tmp_path):
    return [json.loads(x) for x in (tmp_path / "budget.jsonl").read_text().splitlines()]


# --- acceptance --------------------------------------------------------------------------------------------------

def test_curator_edit_over_budget_is_refused_with_guidance(tmp_path):
    FILES[("demo", "SKILL.md")] = skill(280)
    out = pre([grow_op(40)])
    assert out and out["action"] == "block"
    msg = out["message"]
    assert "skill-size-budget refused" in msg and "references/<topic>.md" in msg and "one" in msg.lower()
    assert "325 lines (budget 300)" in msg and "284 lines" in msg
    assert logged(tmp_path)[-1]["verdict"] == "block"


def test_interactive_edit_over_budget_passes_with_warning(tmp_path, monkeypatch):
    monkeypatch.setattr(plugin, "actor", lambda: "agent")
    FILES[("demo", "SKILL.md")] = skill(280)
    ops = [grow_op(40)]
    assert pre(ops) is None  # not blocked
    out = post(ops, json.dumps({"success": True, "message": "Patched SKILL.md"}))
    data = json.loads(out)
    assert data["success"] is True and "skill-size-budget warning" in data["size_budget_warning"]
    assert "references/<topic>.md" in data["size_budget_warning"]
    assert logged(tmp_path)[-1]["verdict"] == "warn"
    # the warning is attached once, not to the next unrelated call
    assert post(ops, json.dumps({"success": True})) is None


def test_under_budget_edit_passes_silently(tmp_path):
    FILES[("demo", "SKILL.md")] = skill(100)
    assert pre([grow_op(40)]) is None
    assert post([grow_op(40)], json.dumps({"success": True})) is None
    assert logged(tmp_path)[-1]["verdict"] == "pass"


# --- what stays allowed over budget ------------------------------------------------------------------------------

def test_redirect_recipe_passes_over_budget():
    """The exact edit the guidance asks for: new reference file + one pointer line."""
    FILES[("demo", "SKILL.md")] = skill(320)
    ops = [
        {"action": "write_file", "name": "demo", "file_path": "references/careful-x.md",
         "file_content": "# Careful X\n\n" + "\n".join(f"- rule {i}" for i in range(40)) + "\n"},
        {"action": "patch", "name": "demo", "old_string": "## Procedure\n",
         "new_string": "## Procedure\n\nBefore X, read `references/careful-x.md` (40 rules for the careful path).\n"},
    ]
    assert pre(ops) is None


def test_done_means_line_passes_over_budget():
    FILES[("demo", "SKILL.md")] = skill(320, done_means=True)
    op = {"action": "patch", "name": "demo", "old_string": "- [ ] The test passed?",
          "new_string": "- [ ] The test passed?\n- [ ] The log line was read, not assumed?"}
    assert pre([op]) is None


def test_checkbox_outside_done_means_is_not_a_free_pass():
    FILES[("demo", "SKILL.md")] = skill(320, done_means=True)
    op = {"action": "patch", "name": "demo", "old_string": "## Procedure\n",
          "new_string": "## Procedure\n- [ ] something\n- [ ] something else\n"}
    assert pre([op])["action"] == "block"


def test_guidance_prefers_done_means_when_skill_has_one():
    FILES[("demo", "SKILL.md")] = skill(320, done_means=True)
    msg = pre([grow_op(10)])["message"]
    assert "## Done means" in msg and "- [ ]" in msg


def test_shrinking_edit_over_budget_passes():
    FILES[("demo", "SKILL.md")] = skill(400)
    op = {"action": "patch", "name": "demo", "old_string": "- Step text that says what to do and why it matters "
          "here. (5)\n", "new_string": ""}
    assert pre([op]) is None


def test_small_reword_passes_but_many_new_lines_do_not():
    FILES[("demo", "SKILL.md")] = skill(320)
    reword = {"action": "patch", "name": "demo", "old_string": "here. (7)", "new_string": "here, on every host. (7)"}
    assert pre([reword]) is None
    assert pre([grow_op(2)])["action"] == "block"


def test_char_budget_applies_with_few_lines():
    FILES[("demo", "SKILL.md")] = skill(50, line="x" * 700)  # ~35k chars in 50 lines
    op = {"action": "patch", "name": "demo", "old_string": "## Procedure\n",
          "new_string": "## Procedure\n" + "y" * 600 + "\n"}
    out = pre([op])
    assert out["action"] == "block" and "chars (budget 30,000)" in out["message"]


def test_curator_create_over_budget_is_refused():
    op = {"action": "create", "name": "big", "content": skill(400)}
    assert pre([op])["action"] == "block"


def test_ops_apply_in_order_shrink_then_grow():
    """Moving a section out first makes room for the growing op in the same batch."""
    FILES[("demo", "SKILL.md")] = skill(290)
    moved = "".join(f"- Step text that says what to do and why it matters here. ({i})\n" for i in range(100, 200))
    ops = [{"action": "write_file", "name": "demo", "file_path": "references/old.md", "file_content": moved},
           {"action": "patch", "name": "demo", "old_string": moved, "new_string": "See `references/old.md`.\n"},
           grow_op(40)]
    assert pre(ops) is None


def test_env_overrides_budget(monkeypatch):
    monkeypatch.setenv("SKILL_SIZE_BUDGET_LINES", "500")
    FILES[("demo", "SKILL.md")] = skill(280)
    assert pre([grow_op(40)]) is None


def test_shadow_mode_never_blocks(monkeypatch, tmp_path):
    monkeypatch.setenv("SKILL_SIZE_BUDGET", "shadow")
    FILES[("demo", "SKILL.md")] = skill(280)
    assert pre([grow_op(40)]) is None
    assert logged(tmp_path)[-1]["verdict"] == "warn"


def test_off_mode_skips(monkeypatch, tmp_path):
    monkeypatch.setenv("SKILL_SIZE_BUDGET", "off")
    FILES[("demo", "SKILL.md")] = skill(280)
    assert pre([grow_op(40)]) is None
    assert not (tmp_path / "budget.jsonl").exists()


def test_other_tools_ignored():
    assert plugin._pre_tool_call(tool_name="write_file", args={"path": "x"}) is None


# --- one hop -----------------------------------------------------------------------------------------------------

def test_curator_reference_linking_another_reference_is_refused():
    FILES[("demo", "SKILL.md")] = skill(10)
    FILES[("demo", "references/a.md")] = "# A\n\n- rule one\n"
    op = {"action": "patch", "name": "demo", "file_path": "references/a.md", "old_string": "- rule one\n",
          "new_string": "- rule one\n- For the rest, follow references/b.md.\n"}
    out = pre([op])
    assert out["action"] == "block" and "one hop" in out["message"] and "references/b.md" in out["message"]


def test_markdown_link_to_sibling_reference_is_a_second_hop():
    FILES[("demo", "SKILL.md")] = skill(10)
    op = {"action": "write_file", "name": "demo", "file_path": "references/a.md",
          "file_content": "# A\n\nSee [the B rules](b.md) and [this file](a.md).\n"}
    assert budget.cross_reference_links("", op["file_content"], "references/a.md") == ["b.md"]
    assert pre([op])["action"] == "block"


def test_reference_without_links_passes_and_self_link_ok():
    FILES[("demo", "SKILL.md")] = skill(10)
    op = {"action": "write_file", "name": "demo", "file_path": "references/a.md",
          "file_content": "# A\n\nThis file is references/a.md.\n- rule\n"}
    assert pre([op]) is None


def test_existing_cross_links_are_not_counted():
    FILES[("demo", "SKILL.md")] = skill(10)
    FILES[("demo", "references/a.md")] = "# A\n\nsee references/b.md\n- rule one\n"
    op = {"action": "patch", "name": "demo", "file_path": "references/a.md", "old_string": "- rule one\n",
          "new_string": "- rule one\n- rule two\n"}
    assert pre([op]) is None


def test_interactive_second_hop_warns(monkeypatch):
    monkeypatch.setattr(plugin, "actor", lambda: "user")
    FILES[("demo", "SKILL.md")] = skill(10)
    op = {"action": "write_file", "name": "demo", "file_path": "references/a.md",
          "file_content": "see references/c.md\n"}
    assert pre([op]) is None
    data = json.loads(post([op], json.dumps({"success": True})))
    assert "one hop" in data["size_budget_warning"]


# --- pure helpers ------------------------------------------------------------------------------------------------

def test_body_excludes_frontmatter():
    assert budget.measure(FM + "a\nb\n") == (2, 4)


def test_failed_write_gets_no_warning(monkeypatch):
    monkeypatch.setattr(plugin, "actor", lambda: "agent")
    FILES[("demo", "SKILL.md")] = skill(280)
    ops = [grow_op(40)]
    pre(ops)
    assert post(ops, json.dumps({"success": False, "error": "x"})) is None


# --- against real core (scripts/test-all puts core on PYTHONPATH) -------------------------------------------------

def test_real_core_actor_and_skill_lookup(tmp_path, monkeypatch):
    """Real skill-ledger actor binding, real _find_skill file read and real fuzzy patch matcher."""
    from tools.skill_ledger import reset_ledger_actor, set_ledger_actor

    home = tmp_path / "home"
    d = home / "skills" / "demo"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(skill(280))
    monkeypatch.setenv("HERMES_HOME", str(home))
    real = _load("skill_size_budget_plugin_real", "__init__.py")  # fresh module: none of the fixture's fakes
    ops = [grow_op(40)]
    args = {"operations": ops}
    tok = set_ledger_actor("curator")
    try:
        assert real.actor() == "curator"
        out = real._pre_tool_call(tool_name="skill_manage", args=args, tool_call_id="r1")
    finally:
        reset_ledger_actor(tok)
    assert out and out["action"] == "block" and "325 lines (budget 300)" in out["message"]
    tok = set_ledger_actor("user")
    try:
        assert real._pre_tool_call(tool_name="skill_manage", args=args, tool_call_id="r2") is None
    finally:
        reset_ledger_actor(tok)
    assert "size_budget_warning" in json.loads(real._transform_tool_result(
        tool_name="skill_manage", args=args, result=json.dumps({"success": True}), tool_call_id="r2"))
