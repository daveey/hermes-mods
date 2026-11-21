"""Blacksmith's on-demand `Proof` SKIPPED check is the one non-green entry a state preflight ignores."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

HERE = Path(__file__).parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


g = load("proof_skip_guard", HERE / "guard.py")
plugin = load("proof_skip_plugin", HERE / "__init__.py")
st = load("proof_skip_state_fixtures", HERE / "tests_state.py")
SHA = st.SHA
REPOS = {"example/protected"}
URL = "https://dashboardbackend.blacksmith.sh/org/run/1"


@pytest.fixture(autouse=True)
def _no_land_turn(monkeypatch):
    monkeypatch.setattr(g, "_live_turns", lambda *a: [])
    monkeypatch.setattr(g, "_turn_acquire", lambda *a: "")


def proof(**kw):
    c = {"__typename": "CheckRun", "name": "Proof", "status": "COMPLETED", "conclusion": "SKIPPED",
         "workflowName": "", "detailsUrl": URL}
    c.update(kw)
    return c


def ok(name="lint"):
    return {"__typename": "CheckRun", "name": name, "status": "COMPLETED", "conclusion": "SUCCESS",
            "workflowName": "CI", "detailsUrl": "https://github.com/example/protected/actions/runs/1"}


def run_all(monkeypatch, checks):
    """Return the refusal text (or None) from each entry path for one rollup."""
    data = st.payload(statusCheckRollup=checks)
    st.probe(monkeypatch, data)
    out = {"argv": g.check_argv(st.CMD, repos=REPOS, env={"GH_REPO": "example/protected"})}
    import shlex
    out["shell"] = g.check_shell(shlex.join(st.CMD), cwd="/", repos=REPOS, env={})
    monkeypatch.setenv("SHIP_ONLY_GUARD_REPOS", json.dumps(sorted(REPOS)))
    out["cli"] = g.main(["--gh-args", *st.CMD[1:]])
    hooks = {}

    class Context:
        def register_hook(self, name, fn):
            hooks[name] = fn
    plugin.register(Context())
    out["hook"] = hooks["pre_tool_call"](tool_name="terminal", args={"command": shlex.join(st.CMD), "workdir": "/"})
    return out


def test_proof_skipped_with_green_others_passes_every_path(monkeypatch):
    got = run_all(monkeypatch, [ok(), proof()])
    assert got["argv"] is None and got["shell"] is None
    assert got["cli"] == 0
    # The hook also runs the land-turn read, which has no snapshot here; only the check stage is in scope.
    assert got["hook"] is None or not any(
        t in got["hook"]["message"] for t in ("checks not green", "no check other than", "statusCheckRollup"))


@pytest.mark.parametrize("bad", [
    proof(conclusion="FAILURE"), proof(conclusion="NEUTRAL"),
    proof(status="IN_PROGRESS", conclusion=None), proof(detailsUrl="https://blacksmith.sh@evil.com/x"),
    proof(workflowName="Proof"),
    proof(detailsUrl="https://github.com/example/protected/runs/1"),
    proof(detailsUrl="https://blacksmith.sh.evil.com/x"),
    proof(detailsUrl="https://evilblacksmith.sh/x"),
    proof(detailsUrl="http://dashboardbackend.blacksmith.sh/x"),
    proof(detailsUrl="https://evil.com/blacksmith.sh"),
    proof(detailsUrl="https://user@evil.com\\@blacksmith.sh/"),
    proof(detailsUrl=None), proof(detailsUrl=""),
    proof(name="Other"),
    proof(__typename="StatusContext"),
    proof(name="Proof", status="COMPLETED", conclusion="skipped"),
])
def test_other_proof_or_skipped_entries_refuse_every_path(monkeypatch, bad):
    got = run_all(monkeypatch, [ok(), bad])
    assert got["argv"] and "checks not green" in got["argv"]
    assert got["shell"] and "checks not green" in got["shell"]
    assert got["cli"] == 1
    assert got["hook"]["action"] == "block"


def test_only_proof_skipped_refuses_with_named_reason(monkeypatch):
    got = run_all(monkeypatch, [proof()])
    assert "no check other than the skipped on-demand Blacksmith `Proof` run" in got["argv"]
    assert got["cli"] == 1 and got["hook"]["action"] == "block"


def test_another_apps_skipped_check_refuses(monkeypatch):
    other = proof(name="Lint", workflowName="CI", detailsUrl="https://github.com/x/y/actions/runs/1")
    got = run_all(monkeypatch, [ok(), other])
    assert "checks not green: Lint=SKIPPED" in got["argv"]
    assert got["cli"] == 1


def test_proof_subdomain_and_apex_hosts_accepted():
    assert g.on_demand_proof_skip(proof(detailsUrl="https://blacksmith.sh/a"))
    assert g.on_demand_proof_skip(proof(detailsUrl="https://a.b.blacksmith.sh/a"))
    assert not g.on_demand_proof_skip(None) and not g.on_demand_proof_skip("Proof")


def test_failure_message_counts_proof_with_total(monkeypatch):
    got = run_all(monkeypatch, [ok(), proof(), {**ok("t"), "conclusion": "FAILURE"}])
    assert "1 of 3 checks not green: t=FAILURE" in got["argv"]
