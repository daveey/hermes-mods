"""second_opinion: fix loop -> one Opus review card; unverified + repo diff -> one Opus diff review."""
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]


def _mod(name):
    spec = importlib.util.spec_from_file_location(name, HERE / "__init__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("COACH_LESSONS", "0")
    monkeypatch.delenv("COACH_REPLY_RULES", raising=False)
    monkeypatch.delenv("COACH_FIXLOOP_REVIEW_MODEL", raising=False)
    return monkeypatch


# ───────────────────────────── 1. fix loop -> review card ─────────────────────────────
def _fixloop_hooks(tmp_path, env, on=True):
    mod = _mod("coach_init_so_fl")
    core, flm, so = mod._load("core"), mod._load("fix_loop"), mod._load("second_opinion")
    env.setattr(flm, "aux_judge", lambda s, p: json.dumps({"p_failed_fix": 0.92, "issue": "login 500"}))
    env.setenv("COACH_FIXLOOP_REVIEW", "1" if on else "0")
    board: dict = {}
    calls: list = []

    def fake_create(args):  # kanban idempotency: same key -> same task
        calls.append(args)
        tid = board.setdefault(args["idempotency_key"], f"t_{len(board) + 1:04d}")
        return {"ok": True, "task_id": tid}
    env.setattr(so, "create_card", fake_create)
    store = core.Store(root=tmp_path / "coach")
    coach, hooks = mod.build(store=store, checkout=None)
    return coach, hooks, store, calls, board


def _fix_turn(hooks, user, path, reply, sid="s1"):
    out = hooks["pre_llm_call"](session_id=sid, user_message=user)
    hooks["post_tool_call"](tool_name="patch", args={"path": path}, result="{}", session_id=sid)
    hooks["post_llm_call"](session_id=sid, assistant_response=reply)
    return (out or {}).get("context", "")


def test_second_failed_fix_creates_one_opus_card_idempotent(tmp_path, env):
    coach, hooks, store, calls, board = _fixloop_hooks(tmp_path, env)
    _fix_turn(hooks, "fix the login 500", "/repo/auth.py", "Fixed the login 500; it was a null session.")
    h1 = _fix_turn(hooks, "still getting the 500", "/repo/session.py", "Fixed for real: session cookie path.")
    assert "reproduce" in h1 and not calls  # 1st failure: advice only
    h2 = hooks["pre_llm_call"](session_id="s1", user_message="nope, still 500")["context"]
    assert len(calls) == 1
    a = calls[0]
    assert a["model"] == "claude-opus-5-5" and a["assignee"] == "default" and a["session_id"] == "s1"
    assert "do not edit" in a["body"].lower()
    assert "## Original ask\nfix the login 500" in a["body"]
    for s in ("still getting the 500", "nope, still 500", "null session", "session cookie path",
              "/repo/auth.py", "/repo/session.py"):
        assert s in a["body"], s
    assert "Stop iterating" in h2 and "t_0001" in h2 and 'kanban_show(task_id="t_0001")' in h2
    # 3rd failure in the same window: no new card, same id in the hint
    hooks["post_llm_call"](session_id="s1", assistant_response="Fixed again.")
    h3 = hooks["pre_llm_call"](session_id="s1", user_message="still broken")["context"]
    assert len(calls) == 1 and "t_0001" in h3
    logged = [json.loads(l) for l in (tmp_path / "coach" / "log.jsonl").read_text().splitlines()]
    assert [r["task_id"] for r in logged if r["event"] == "fixloop_card"] == ["t_0001"]


def test_card_idempotency_key_survives_restart(tmp_path, env):
    so = _mod("coach_init_so_k")._load("second_opinion")
    st = {"last_reply": "Fixed.", "last_log": ["-> patch(/r/a.py)"], "fails": [2]}
    so.record_fail(st, 2, "still broken", "x")
    st["fails"] = [2, 3]
    so.record_fail(st, 3, "still broken again", "x")
    k1 = so.card_args("s1", st["fail_recs"])["idempotency_key"]
    st2 = json.loads(json.dumps(st))  # same window re-derived after a backend restart
    assert so.card_args("s1", st2["fail_recs"])["idempotency_key"] == k1
    assert k1.startswith("coach-fixloop:s1:")
    assert so.card_args("s2", st["fail_recs"])["idempotency_key"] != k1


def test_fix_loop_window_survives_new_processes(tmp_path, env):
    """`hermes chat -q --resume` runs each turn in a new process: the loop state is re-seeded from the session file."""
    mod = _mod("coach_init_so_proc")
    core, flm, so = mod._load("core"), mod._load("fix_loop"), mod._load("second_opinion")
    env.setattr(flm, "aux_judge", lambda s, p: json.dumps({"p_failed_fix": 0.92, "issue": "login 500"}))
    env.setenv("COACH_FIXLOOP_REVIEW", "1")
    calls = []
    env.setattr(so, "create_card", lambda a: calls.append(a) or {"task_id": "t_proc"})
    root = tmp_path / "coach"
    hooks = lambda: mod.build(store=core.Store(root=root), checkout=None)[1]  # noqa: E731 - one per "process"
    _fix_turn(hooks(), "fix the login 500", "/repo/auth.py", "Fixed the login 500.")
    assert "reproduce" in _fix_turn(hooks(), "still 500", "/repo/auth.py", "Fixed for real.")
    h = hooks()["pre_llm_call"](session_id="s1", user_message="nope, still 500")["context"]
    assert len(calls) == 1 and "t_proc" in h and "/repo/auth.py" in calls[0]["body"]


def test_fixloop_review_off_keeps_old_behaviour(tmp_path, env):
    coach, hooks, store, calls, board = _fixloop_hooks(tmp_path, env, on=False)
    _fix_turn(hooks, "fix it", "/repo/a.py", "Fixed.")
    _fix_turn(hooks, "still broken", "/repo/a.py", "Fixed again.")
    h = hooks["pre_llm_call"](session_id="s1", user_message="still broken")["context"]
    assert "Stop iterating" in h and not calls and "second-opinion card" not in h


# ───────────────────────────── 2. unverified + repo -> diff review ─────────────────────────────
@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    g = lambda *a: subprocess.run(["git", "-C", str(r), *a], check=True, capture_output=True)
    g("init", "-q")
    g("config", "user.email", "t@t")
    g("config", "user.name", "t")
    (r / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    g("add", ".")
    g("commit", "-qm", "init")
    return r


def _reply_hooks(tmp_path, env, unverified=0.9, on=True):
    mod = _mod("coach_init_so_rc")
    core, rcl, so = mod._load("core"), mod._load("reply_check"), mod._load("second_opinion")
    judged, reviewed = [], []

    def judge(s, p):
        judged.append(p)
        return json.dumps({"handoff": 0.0, "confirm": 0.0, "unverified": unverified, "unwatched": 0.0,
                           "evidence": "no test run"})

    def review(system, prompt, **kw):
        reviewed.append(prompt)
        return json.dumps({"supported": False, "findings": ["calc.py:2 subtracts instead of adding"],
                           "check": "python -c 'import calc; print(calc.add(2,2))'"})
    env.setattr(rcl, "aux_judge", judge)
    env.setattr(so, "aux_review", review)
    env.setenv("COACH_DIFF_REVIEW", "1" if on else "0")
    store = core.Store(root=tmp_path / "coach")
    coach, hooks = mod.build(store=store, checkout=None)
    return hooks, judged, reviewed, store


REPLY = "Done: add() is fixed."


def test_unverified_with_repo_change_runs_one_diff_review(tmp_path, env, repo):
    hooks, judged, reviewed, store = _reply_hooks(tmp_path, env)
    hooks["pre_llm_call"](session_id="s1", user_message="fix add()")
    (repo / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    hooks["post_tool_call"](tool_name="patch", args={"path": str(repo / "calc.py")}, result="{}", session_id="s1")
    out = hooks["pre_verify"](session_id="s1", attempt=0, final_response=REPLY, changed_paths=[str(repo / "calc.py")])
    assert out["action"] == "continue"
    assert "calc.py:2 subtracts instead of adding" in out["message"] and "independent review" in out["message"]
    assert len(reviewed) == 1 and "-    return a + b" in reviewed[0] and "+    return a - b" in reviewed[0]
    # max one per turn: a second pre_verify in the same turn doesn't review again
    assert hooks["pre_verify"](session_id="s1", attempt=0, final_response=REPLY,
                               changed_paths=[str(repo / "calc.py")]) is None
    assert len(reviewed) == 1
    logged = [json.loads(l) for l in (tmp_path / "coach" / "log.jsonl").read_text().splitlines()]
    assert any(r["event"] == "diff_review" and r["ok"] and r["findings"] == 1 for r in logged)


def test_committed_change_this_turn_is_reviewed(tmp_path, env, repo):
    hooks, judged, reviewed, store = _reply_hooks(tmp_path, env)
    hooks["pre_llm_call"](session_id="s1", user_message="fix add() and commit")
    (repo / "calc.py").write_text("def add(a, b):\n    return a * b\n")
    cmd = f"cd {repo} && git commit -qam 'fix add'"
    subprocess.run(cmd, shell=True, check=True)
    hooks["post_tool_call"](tool_name="terminal", args={"command": cmd}, result="{}", session_id="s1")
    out = hooks["pre_verify"](session_id="s1", attempt=0, final_response=REPLY, changed_paths=[])
    assert out and len(reviewed) == 1 and "+    return a * b" in reviewed[0]


def test_no_review_without_repo_change(tmp_path, env):
    hooks, judged, reviewed, store = _reply_hooks(tmp_path, env)
    outside = tmp_path / "notes.txt"  # not in a git repo
    hooks["pre_llm_call"](session_id="s1", user_message="fix it")
    outside.write_text("x")
    hooks["post_tool_call"](tool_name="write_file", args={"path": str(outside)}, result="{}", session_id="s1")
    assert hooks["pre_verify"](session_id="s1", attempt=0, final_response=REPLY, changed_paths=[str(outside)]) is None
    assert not reviewed and not judged  # unverified stays off by default -> no judge call either


def test_no_review_when_judge_does_not_flag_unverified(tmp_path, env, repo):
    hooks, judged, reviewed, store = _reply_hooks(tmp_path, env, unverified=0.2)
    hooks["pre_llm_call"](session_id="s1", user_message="fix add()")
    (repo / "calc.py").write_text("def add(a, b):\n    return b + a\n")
    hooks["post_tool_call"](tool_name="patch", args={"path": str(repo / "calc.py")}, result="{}", session_id="s1")
    assert hooks["pre_verify"](session_id="s1", attempt=0, final_response=REPLY,
                               changed_paths=[str(repo / "calc.py")]) is None
    assert len(judged) == 1 and not reviewed


def test_diff_review_off_is_old_behaviour(tmp_path, env, repo):
    hooks, judged, reviewed, store = _reply_hooks(tmp_path, env, on=False)
    hooks["pre_llm_call"](session_id="s1", user_message="fix add()")
    (repo / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    hooks["post_tool_call"](tool_name="patch", args={"path": str(repo / "calc.py")}, result="{}", session_id="s1")
    assert hooks["pre_verify"](session_id="s1", attempt=0, final_response=REPLY,
                               changed_paths=[str(repo / "calc.py")]) is None
    assert not judged and not reviewed


def test_diff_cap_and_settings(tmp_path, env, repo):
    so = _mod("coach_init_so_cap")._load("second_opinion")
    (repo / "calc.py").write_text("x = 1\n" * 5000)
    d = so.repo_diff(str(repo), [str(repo / "calc.py")], None, max_chars=2000)
    assert len(d) < 2100 and "truncated at 2000" in d
    env.setenv("COACH_DIFF_REVIEW_TIMEOUT_S", "30")
    assert so.setting("diff_review_timeout_s") == 30
    assert so.setting("diff_review", {"diff_review": True}) is False  # env (conftest "0") wins over config
    env.delenv("COACH_DIFF_REVIEW")
    assert so.setting("diff_review", {"diff_review": True}) is True
    assert so.setting("diff_review", {}) is False
    assert so.commit_dirs(["-> terminal(cd /a/b && git add -A && git commit -m x)"]) == ["/a/b"]
    assert so.commit_dirs(["-> terminal(git -C /c commit -am y)"]) == ["/c"]
    assert so.files_touched(["-> patch(/a.py)", "-> write_file(/b.md)", "-> terminal(ls)", "-> patch(/a.py)"]) == \
        ["/a.py", "/b.md"]
