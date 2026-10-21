import importlib.util
import json
import sys
from pathlib import Path

import pytest

P = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("skill_triggers_mod", P / "__init__.py")
st = importlib.util.module_from_spec(spec)
spec.loader.exec_module(st)
sys.modules.setdefault("skill_triggers_mod", st)

SKILL = """# Token Broker

## Hard rules

- Never run approve.

## A permission error is not a blocker for David

Mint a better token.

## Getting a vendor credential that only a web UI can mint

Use the minter.

## Pattern: other

noise
"""
ORCH = "intro\n\n**Card bodies for this user** (he reviews the board): spec path.\nsecond line.\n\nunrelated\n"


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    home = tmp_path / "h"
    for n, body in (("token-broker", SKILL), ("kanban-orchestrator", ORCH)):
        d = home / "skills" / "devops" / n
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(body)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(st, "_skill_dirs", lambda: [home / "skills"])
    monkeypatch.setattr(st, "_viewed_in_db", lambda s, k: False)
    st._viewed.clear(); st._fired.clear()
    return home


def run(tool, args, result="ok", sid="s1"):
    return st.transform_tool_result(tool_name=tool, args=args, result=result, session_id=sid)


def test_command_match_injects_sections():
    out = run("terminal", {"command": "token-broker scopes"})
    assert out.startswith("ok") and "Never run approve" in out and "Mint a better token" in out
    assert "noise" not in out and "Use the minter" not in out


def test_no_match():
    assert run("terminal", {"command": "ls"}) is None


def test_tool_name_and_bold_paragraph():
    out = run("kanban_create", {"title": "x"})
    assert "spec path" in out and "second line" in out and "unrelated" not in out


def test_result_regex_cloudflare():
    out = run("terminal", {"command": "curl x"}, '{"errors":[{"code": 10000, "message":"Authentication error"}]}')
    assert out is not None and st.MARK in out and "Use the minter" in out


def test_once_per_session():
    assert run("terminal", {"command": "token-broker scopes"}) is not None
    assert run("terminal", {"command": "token-broker scopes"}) is None
    assert run("terminal", {"command": "token-broker scopes"}, sid="s2") is not None


def test_skill_already_loaded_via_skill_view():
    run("skill_view", {"name": "token-broker"}, "skill text")
    assert run("terminal", {"command": "token-broker scopes"}) is None


def test_skill_already_loaded_via_history(monkeypatch):
    monkeypatch.setattr(st, "_viewed_in_db", lambda s, k: k == "token-broker")
    assert run("terminal", {"command": "token-broker scopes"}) is None
    assert run("kanban_block", {}) is not None


def test_skill_view_result_not_matched():
    assert run("skill_view", {"name": "other"}, "Authentication error docs") is None


def test_user_override(env):
    (env / "skill-triggers.yaml").write_text(
        "disabled: [token-broker-command]\ntriggers:\n  - id: x\n    skill: token-broker\n    tools: [web_search]\n    sections: [Hard rules]\n")
    assert run("terminal", {"command": "token-broker scopes"}) is None
    assert "Never run approve" in run("web_search", {})


def test_missing_skill_or_section_is_noop(env):
    (env / "skill-triggers.yaml").write_text(
        "triggers:\n  - id: y\n    skill: nope\n    tools: [web_search]\n    sections: [Hard rules]\n")
    assert run("web_search", {}) is None


def test_non_string_result():
    assert run("terminal", {"command": "token-broker x"}, {"a": 1}) is None


def test_shipped_table_matches_real_skills():
    rows = st.load_rows(user_path=P / "nonexistent.yaml")
    ids = {r["id"] for r in rows}
    assert {"token-broker-command", "kanban-card-asks", "cloudflare-auth-error"} <= ids
