"""Every `self_heal.py event ...` command the investigation and fix briefs print runs VERBATIM from a Kanban
worker's terminal / execute_code shell.

Hermes scrubs ``HERMES_KANBAN_TASK`` from a worker's tool children on purpose (``scrub_kanban_env``: a nested
``hermes`` must never act as the worker) and exports ``HERMES_KANBAN_WORKER_TASK`` there instead. A brief that
said ``--card $HERMES_KANBAN_TASK`` expanded to a bare ``--card`` in that shell and argparse exited 2
(``argument --card: expected one argument``), so the card never recorded its verdict.
"""
import importlib.util
import json
import re
import shlex
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


sh = load("coach_self_heal_brief_cmds_test", ROOT / "self_heal.py")
core = load("coach_core_brief_cmds_test", ROOT / "core.py")

NOW = 1792660000.0
INV, FIX, OWNER = "t_0000aaaa", "t_0000bbbb", "t_0000cccc"
CMD = re.compile(r"`(python3 \S*self_heal\.py event [^`]+)`")

# what a worker's tool-child shell holds: the scrubbed env (no HERMES_KANBAN_TASK)
TOOL_CHILD_ENV = {"HERMES_KANBAN_WORKSPACE": "/tmp/ws", "HERMES_KANBAN_BOARD": "default"}


def defect():
    d = {"component": "desktop", "symptom": "sidebar sessions reshuffle", "source": "complaint",
         "kind": "hermes_bug", "p_defect": 0.9, "session_id": "20261022_090000_abcdef", "quote": "q",
         "host": "hermes-hub", "at": NOW - 60}
    d["fingerprint"] = sh.fingerprint(d["component"], d["symptom"])
    return d


def commands(brief):
    out = CMD.findall(brief)
    assert out, "brief prints no event command"
    return out


def shell_argv(cmd, env, tmp_path):
    """Expand `cmd` the way bash in the worker's terminal does, fill the <placeholders>, drop `python3 <path>`."""
    for var in set(re.findall(r"\$\{?(\w+)\}?", cmd)):
        cmd = re.sub(r"\$\{?" + var + r"\}?", env.get(var) or "", cmd)
    cmd = (cmd.replace("<owner-card>", OWNER).replace("<evidence>", "e").replace("<why>", "w")
           .replace("<commit sha>", "abc123").replace("<sha>", "abc123"))
    argv = shlex.split(cmd)[2:]
    if "--result" in argv:
        res = tmp_path / "result.md"
        res.write_text("done")
        argv[argv.index("--result") + 1] = str(res)
    return argv


@pytest.fixture
def lane(tmp_path, monkeypatch):
    store = core.Store(tmp_path / "coach")
    monkeypatch.setattr(sh, "_store", lambda store_=None: store)
    for k in ("HERMES_KANBAN_TASK", "HERMES_KANBAN_WORKER_TASK"):
        monkeypatch.delenv(k, raising=False)
    d = defect()
    sh.LaneState(store.root).update(d["fingerprint"], status="filed", card=INV, component="desktop")
    return store, d


def _log(store, event):
    return [r for r in (json.loads(x) for x in (Path(store.root) / "log.jsonl").read_text().splitlines())
            if r.get("event") == event]


def _run(argv, capsys):
    rc = sh.main(argv)
    assert rc in (None, 0)
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def _fill(cmd):
    return (cmd.replace("<owner-card>", OWNER).replace("<evidence>", "e").replace("<why>", "w")
            .replace("<commit sha>", "abc123").replace("<sha>", "abc123"))


def test_terminal_entry_real_bash_runs_each_investigation_command_verbatim(tmp_path):
    """terminal path: a real `bash -c` with the scrubbed tool-child env (no HERMES_KANBAN_TASK) runs the line."""
    import os
    import subprocess
    home = tmp_path / "home"
    store = core.Store(home / "coach")
    d = defect()
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(tmp_path), "HERMES_HOME": str(home),
           **TOOL_CHILD_ENV, "HERMES_KANBAN_WORKER_TASK": INV}
    for cmd in commands(sh.investigation_brief(d)):
        sh.LaneState(store.root).update(d["fingerprint"], status="filed", card=INV)
        line = _fill(cmd).replace("python3 " + sh.SELF, f"{shlex.quote(sys.executable)} "
                                                        f"{shlex.quote(str(ROOT / 'self_heal.py'))}")
        p = subprocess.run(["bash", "-c", line], env=env, capture_output=True, text=True, timeout=60)
        assert p.returncode == 0, (cmd, p.stderr)
    (dup,) = _log(store, "selfheal_adopted_owner")
    assert dup["duplicate_card"] == INV and dup["card"] == OWNER
    assert _log(store, "selfheal_proposed")[0]["card"] == INV


def test_execute_code_entry_scrubbed_os_environ_runs_each_investigation_command(lane, tmp_path, monkeypatch,
                                                                                capsys):
    """execute_code path: the sandbox script's os.environ is the scrubbed env; it calls the CLI with the argv."""
    store, d = lane
    env = dict(TOOL_CHILD_ENV, HERMES_KANBAN_WORKER_TASK=INV)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    for cmd in commands(sh.investigation_brief(d)):
        sh.LaneState(store.root).update(d["fingerprint"], status="filed", card=INV)
        out = _run(shell_argv(cmd, env, tmp_path), capsys)
        assert out["fingerprint"] == d["fingerprint"], cmd
    (dup,) = _log(store, "selfheal_adopted_owner")
    assert dup["duplicate_card"] == INV and dup["card"] == OWNER
    assert _log(store, "selfheal_proposed")[0]["card"] == INV


def test_fix_brief_commands_run_verbatim_and_attribute_the_fix_card(lane, tmp_path, monkeypatch, capsys):
    store, d = lane
    fp = d["fingerprint"]
    env = dict(TOOL_CHILD_ENV, HERMES_KANBAN_WORKER_TASK=FIX)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    for cmd in commands(sh.fix_brief(fp, {"card": INV, "component": "desktop"})):
        sh.LaneState(store.root).update(fp, status="approved", fix_card=FIX)
        _run(shell_argv(cmd, env, tmp_path), capsys)
    assert sh.LaneState(store.root).get(fp)["fix_card"] == FIX
    assert _log(store, "selfheal_fixed")[0]["card"] == FIX


def test_card_falls_back_to_the_lane_state_with_no_env_at_all(lane, capsys):
    # a shell with neither variable (a launcher that dropped HERMES_*): the fingerprint's recorded card is used
    store, d = lane
    fp = d["fingerprint"]
    _run(["event", "proposed", fp], capsys)
    assert _log(store, "selfheal_proposed")[0]["card"] == INV
    sh.LaneState(store.root).update(fp, status="approved", fix_card=FIX)
    _run(["event", "not_defect", fp, "--note", "already fixed"], capsys)
    assert _log(store, "selfheal_not_defect")[-1]["card"] == FIX


def test_explicit_card_wins_then_worker_env_then_lane_state(lane, monkeypatch):
    store, d = lane
    st = sh.LaneState(store.root).get(d["fingerprint"])
    assert sh.resolve_event_card("t_0000dddd", st) == "t_0000dddd"
    assert sh.resolve_event_card("", st) == INV
    monkeypatch.setenv("HERMES_KANBAN_WORKER_TASK", FIX)
    assert sh.resolve_event_card("", st) == FIX
    monkeypatch.setenv("HERMES_KANBAN_TASK", OWNER)  # the worker process itself (in-process callers)
    assert sh.resolve_event_card("", st) == OWNER


def test_briefs_never_reference_the_scrubbed_variable():
    d = defect()
    for brief in (sh.investigation_brief(d), sh.fix_brief(d["fingerprint"], {"card": INV})):
        assert "$HERMES_KANBAN_TASK" not in brief and "${HERMES_KANBAN_TASK}" not in brief
