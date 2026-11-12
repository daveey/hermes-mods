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
ORCH = ("intro\n\n**Card bodies for this user** (he reviews the board): spec path.\nsecond line.\n\n"
        "**Card mechanics for this user**: notify-subscribe on an unlinked card.\n\nunrelated\n")
ASK = """# Asking

## Commands that need the user

- run_in_user_terminal, never paste.

## Chrome steps

- browser_focus first.

## Another Mac's GUI

- remote_approve_dialog first.

## Inbox posts

- ask={interaction, why_human, action}.

## Done means

- tick
"""
WORKER = ("# Launch\n\n## Pitfalls\n\n- **Run commands on existing workers from the hub with SSM, never SSH.** "
          "Use us-west-2 and the instance role.\n- **Other bullet** unrelated.\n")
MODS = "# mods\n\n## Git worktrees\n\n- add owns remove.\n\n## Other\n\nnoise\n"
REPO = P.parents[1]


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    home = tmp_path / "h"
    for n, body in (("token-broker", SKILL), ("kanban-orchestrator", ORCH), ("asking-the-user", ASK),
                    ("hermes-mods-repo", MODS), ("launch-hub-worker", WORKER)):
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
    assert "notify-subscribe on an unlinked card" in out


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


@pytest.mark.parametrize("tool,want,not_want", [
    ("run_in_user_terminal", "never paste", "browser_focus first"),
    ("browser_focus", "browser_focus first", "never paste"),
    ("remote_approve_dialog", "remote_approve_dialog first", "Inbox"),
    ("remote_screenshot", "remote_approve_dialog first", "Inbox"),
    ("remote_input", "remote_approve_dialog first", "Inbox"),
    ("remote_screen_hosts", "remote_approve_dialog first", "Inbox"),
    ("inbox_post", "why_human", "never paste"),
])
def test_asking_the_user_rows(tool, want, not_want):
    out = run(tool, {})
    assert out and st.MARK in out and "asking-the-user" in out and want in out and not_want not in out
    assert "tick" not in out


def test_deferred_bridge_call_matches_inner_tool():
    out = run("tool_call", {"calls": [{"name": "inbox_post", "arguments": {"title": "t"}}]})
    assert out and "why_human" in out
    # the inner call re-running the hook under its own name must not inject twice
    assert run("inbox_post", {"title": "t"}) is None


def test_bridge_call_inner_terminal_command():
    out = run("tool_call", {"calls": [{"name": "terminal", "arguments": json.dumps({"command": "token-broker scopes"})}]})
    assert out and "Never run approve" in out


def test_bridge_call_other_tool_no_match():
    assert run("tool_call", {"calls": [{"name": "web_search", "arguments": {}}]}) is None


@pytest.mark.parametrize("cmd", [
    "git worktree add --detach ../wt origin/main",
    "git -C ~/code/hermes-mods worktree add ~/code/mods-work origin/main",
    "for b in a b; do git worktree add ../$b $b; done",
])
def test_worktree_add_row(cmd):
    out = run("terminal", {"command": cmd})
    assert out and "add owns remove" in out and "noise" not in out


@pytest.mark.parametrize("cmd", ["git worktree list", "git worktree remove ../wt", "echo worktreeadd"])
def test_worktree_row_ignores_other_commands(cmd):
    assert run("terminal", {"command": cmd}) is None


def test_worktree_add_in_execute_code():
    out = run("execute_code", {"code": "terminal('git worktree add ../x origin/main')"})
    assert out and "add owns remove" in out


def test_shipped_rows_for_this_repos_skill_resolve():
    """Rows naming the public asking-the-user skill extract every section from the real SKILL.md in this repo."""
    rows = [r for r in st.load_rows(user_path=P / "nonexistent.yaml") if r["skill"] == "asking-the-user"]
    assert {r["id"] for r in rows} == {"user-terminal-handoff", "chrome-focus-handoff", "remote-mac-gui",
                                       "inbox-post-shape"}
    text = (REPO / "skills" / "productivity" / "asking-the-user" / "SKILL.md").read_text()
    for r in rows:
        for sec in r["sections"]:
            assert st.extract_section(text, sec), (r["id"], sec)
    inj = st.build_injection(rows[0], dirs=[REPO / "skills"])
    assert inj and st.MARK in inj


def test_kanban_cli_create_row():
    out = run("terminal", {"command": "hermes kanban create 'x' --max-retries 2"})
    assert out and "notify-subscribe on an unlinked card" in out


def test_kanban_cli_other_verbs_no_match():
    assert run("terminal", {"command": "hermes kanban list"}) is None


# Defect agent-cde19bf930: a hub session's ssh to the workers failed (publickey) and it gave up instead of using SSM.
@pytest.mark.parametrize("cmd,result", [
    ("ssh -o BatchMode=yes -o ConnectTimeout=10 hermes-worker-2 'systemctl --user is-active hermes-gateway'",
     "ubuntu@hermes-worker-2: Permission denied (publickey).\n"),
    ("for n in 1 2 3; do ssh hermes-worker-$n true; done; ssh ubuntu@hermes-worker-4 hostname",
     "Host key verification failed.\n"),
    ("ssh hermes-worker-5.example.ts.net uptime", "ssh: connect to host x port 22: Connection timed out\n"),
])
def test_worker_ssh_fail_row(cmd, result):
    out = run("terminal", {"command": cmd}, result)
    assert out and st.MARK in out and "launch-hub-worker" in out
    assert "from the hub with SSM" in out and "Other bullet" not in out


@pytest.mark.parametrize("cmd,result", [
    ("ssh -o BatchMode=yes hermes-worker-2 true", "active\n"),                        # ssh worked
    ("ssh hermes-hub true", "Permission denied (publickey).\n"),                      # not a worker
    ("echo hermes-worker-2 | ssh-keygen -F x", "Permission denied (publickey).\n"),   # ssh not aimed at a worker
])
def test_worker_ssh_fail_row_negatives(cmd, result):
    assert run("terminal", {"command": cmd}, result) is None


def test_worker_ssh_fail_in_execute_code():
    out = run("execute_code", {"code": "terminal('ssh hermes-worker-3 true')"}, "Permission denied (publickey).")
    assert out and "from the hub with SSM" in out


def test_bold_lead_in_list_item_extracts_one_item():
    text = ("## Pitfalls\n\n- **First rule.** wrapped\n  continuation line.\n  - nested detail\n"
            "- **Second rule.** other\n1. **Numbered lead.** n\n2. next\n")
    assert st.extract_section(text, "First rule") == "- **First rule.** wrapped\n  continuation line.\n  - nested detail"
    assert st.extract_section(text, "Numbered lead") == "1. **Numbered lead.** n"


def test_worker_ssh_fail_row_shipped():
    rows = {r["id"]: r for r in st.load_rows(user_path=P / "nonexistent.yaml")}
    r = rows["worker-ssh-fail"]
    assert r["skill"] == "launch-hub-worker" and r["sections"] == ["Run commands on existing workers from the hub with SSM"]
