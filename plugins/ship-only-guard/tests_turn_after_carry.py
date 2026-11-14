"""A carried, approved, drifted PR records its land turn (t_01a1da82d9dfdebd).

State PR #2944 (2026-11-14, ~10 cycles across 4 PRs) livelocked through update-branch -> CI -> drift: every
merge attempt of a PR that update-branch had refreshed carried its approval through the patch-identical check,
the carry and the drift compare left 2-4 s of the 18 s budget, and the acquisition needs more than _TURN_BUDGET
(10 s) left, so `refs/land-turn/pr-<N>` was never written and the next state commit drifted the PR again.

The hook keeps its 18 s budget and never weakens a refusal. When the in-hook acquisition cannot run, the drift
refusal names a bounded follow-up command (`guard.py --land-turn ...`) that re-runs the same preflight under its
own budget, records the turn and never merges. Driven here under the worker gh shim's measured latency
(tests_drift_deadline_latency.FakeShim).
"""
import importlib.util
import json
from pathlib import Path
import shlex
import threading
import time

import pytest

_spec = importlib.util.spec_from_file_location(
    "drift_latency_fixture", Path(__file__).with_name("tests_drift_deadline_latency.py"))
lat = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lat)

R, HEAD, NUMBER = lat.R, lat.HEAD, 2842
TURN_REF = f"refs/land-turn/pr-{NUMBER}"
WORDS = ["pr", "merge", str(NUMBER), "-R", R, "--rebase", "--match-head-commit", HEAD]


class TurnShim(lat.FakeShim):
    """FakeShim plus the land-turn endpoints: a ref store, commit writes and the GraphQL record read."""

    def __init__(self, g, overlap):
        super().__init__(g, overlap)
        self.refs, self.objects, self.writes = {}, {}, []
        self.local = threading.local()          # calls run concurrently: the stdin payload is per thread

    def run(self, argv, *args, **kwargs):
        self.local.input = kwargs.get("input")
        return super().run(argv, *args, **kwargs)

    def answer(self, argv):
        words = [w for w in argv[2:] if not w.startswith("-") and not w.startswith("Accept:")]
        path = words[0] if words else ""
        stdin = getattr(self.local, "input", None)
        payload = json.loads(stdin) if stdin else None
        if argv[1] == "api" and path == f"repos/{R}/git/matching-refs/land-turn/pr-":
            return json.dumps([[{"ref": ref, "object": {"sha": sha}} for ref, sha in self.refs.items()]]), 0
        if argv[1] == "api" and path == "graphql":
            got = {}
            for i, sha in enumerate(payload["query"].split('object(oid: "')[1:]):
                got[f"t{i}"] = self.objects.get(sha[:40])
            return json.dumps({"data": {"repository": got}}), 0
        if path == f"repos/{R}/git/commits/{HEAD}":
            return json.dumps({"sha": HEAD, "tree": {"sha": "7" * 40}}), 0
        if path == f"repos/{R}/git/commits" and payload is not None:
            sha = "%040x" % (0x5eed0000 + len(self.objects))
            self.objects[sha] = {"message": payload["message"]}
            self.writes.append(("commit", sha))
            return json.dumps({"sha": sha}), 0
        if path == f"repos/{R}/git/refs" and payload is not None:
            self.refs[payload["ref"]] = payload["sha"]
            self.writes.append(("ref", payload["ref"]))
            return json.dumps({"ref": payload["ref"]}), 0
        if path.startswith(f"repos/{R}/git/ref/"):
            return "", 1
        return super().answer(argv)


def load(monkeypatch, overlap=True):
    g, _ = lat.load(monkeypatch, overlap)
    shim = TurnShim(g, overlap)
    monkeypatch.setattr(g.subprocess, "run", shim.run)
    monkeypatch.setattr(g, "LAND_TURN_TIMEOUT", 60 * lat.SCALE, raising=False)
    # Every budget in the same scaled seconds as the shim, so the in-hook turn is judged as it is live.
    monkeypatch.setattr(g, "_TURN_BUDGET", g._TURN_BUDGET * lat.SCALE)
    monkeypatch.setattr(g, "_WRITE_RESERVE", g._WRITE_RESERVE * lat.SCALE)
    monkeypatch.setenv("SHIP_ONLY_GUARD_REPOS", json.dumps([R]))
    return g, shim


def timeline(shim):
    return "\n".join(f"{s / lat.SCALE:6.2f} {e / lat.SCALE:6.2f} [{st}] {w}" for s, e, st, w in sorted(shim.calls))


def record(g, shim):
    sha = shim.refs.get(TURN_REF)
    return g.parse_turn(shim.objects[sha]["message"]) if sha else None


def test_carried_drifted_pr_records_its_land_turn(monkeypatch):
    g, shim = load(monkeypatch)
    start = time.monotonic()
    result = g.bounded_preflight(g._state_merge, WORDS, R, ".", {})
    took = (time.monotonic() - start) / lat.SCALE
    assert isinstance(result, str) and "state commit(s) landed since then" in result, f"{result}\n{timeline(shim)}"
    assert "deadline exceeded" not in result and took < 18, f"{took:.1f}\n{result}\n{timeline(shim)}"
    # The carry is still running when the in-hook turn would have to start (the livelock), so the refusal
    # must name the follow-up that records it, before the update-branch it recommends.
    assert TURN_REF not in shim.refs, "fixture no longer reproduces the unreachable in-hook turn"
    line = next((ln for ln in result.splitlines() if "--land-turn" in ln), "")
    assert line and result.index("--land-turn") < result.index("update-branch"), result
    cmd = shlex.split(line[line.index("`") + 1:line.index("`", line.index("`") + 1)])
    assert cmd[0] == "python3" and Path(cmd[1]).resolve() == Path(g.__file__).resolve(), cmd
    assert cmd[2:] == ["--land-turn", str(NUMBER), "--repo", R, "--match-head-commit", HEAD], cmd

    # The named follow-up, under the same shim latency, writes the turn and never reports an allow.
    shim.calls.clear()
    rc = g.main(cmd[2:])
    assert rc == 0, timeline(shim)
    rec = record(g, shim)
    assert rec and rec["pr"] == NUMBER and rec["head"] == HEAD and rec["files"] == [lat.F], (rec, shim.writes)

    # The next hook attempt sees the PR's own live turn: drift still refuses (update-branch), and it reports the
    # recorded turn instead of asking for the follow-up again.
    shim.calls.clear()
    again = g.bounded_preflight(g._state_merge, WORDS, R, ".", {})
    assert "state commit(s) landed since then" in again and "--land-turn" not in again, again
    assert f"holds the state land turn" in again, again


def test_land_turn_command_never_allows_a_merge(monkeypatch, capsys):
    # No drift: the command records nothing and says so; it is not a second merge preflight that admits.
    g, shim = load(monkeypatch, overlap=False)
    rc = g.main(["--land-turn", str(NUMBER), "--repo", R, "--match-head-commit", HEAD])
    out = capsys.readouterr()
    assert rc == 1 and TURN_REF not in shim.refs and not shim.writes
    assert "no land turn" in (out.out + out.err).lower(), out


def test_land_turn_command_keeps_every_refusal_predicate(monkeypatch, capsys):
    # The longer budget buys time, not admission: without an approval no turn is recorded.
    g, shim = load(monkeypatch)
    unapproved = {**lat.pr_data(), "reviews": []}
    monkeypatch.setattr(lat, "pr_data", lambda: unapproved)
    rc = g.main(["--land-turn", str(NUMBER), "--repo", R, "--match-head-commit", HEAD])
    err = capsys.readouterr().err
    assert rc == 1 and TURN_REF not in shim.refs and not shim.writes, err
    assert "No land turn recorded" in err and "approv" in err.lower(), err


@pytest.mark.parametrize("argv", [
    ["--land-turn", "2842", "--repo", R],                                   # no head fence
    ["--land-turn", "2842", "--repo", R, "--match-head-commit", "abc"],     # short head
    ["--land-turn", "x", "--repo", R, "--match-head-commit", HEAD],
    ["--land-turn", "2842", "--repo", "example/other", "--match-head-commit", HEAD],   # not protected
])
def test_land_turn_command_refuses_malformed_or_unprotected(monkeypatch, argv):
    g, shim = load(monkeypatch)
    assert g.main(argv) == 2 and not shim.calls and not shim.writes


def test_hook_does_not_treat_the_land_turn_command_as_a_merge(monkeypatch, tmp_path):
    g, shim = load(monkeypatch)
    cmd = f"python3 {shlex.quote(g.__file__)} --land-turn {NUMBER} --repo {R} --match-head-commit {HEAD}"
    assert g.check_shell(cmd, cwd=str(tmp_path), repos={R}) is None and not shim.calls


class ListFailsAfterWriteShim(TurnShim):
    """The land-turn listing is readable for the pre-write hold check, then fails once a turn was written."""

    def answer(self, argv):
        words = [w for w in argv[2:] if not w.startswith("-") and not w.startswith("Accept:")]
        if self.writes and words and words[0] == f"repos/{R}/git/matching-refs/land-turn/pr-":
            return "", 1
        return super().answer(argv)


def test_land_turn_command_exits_1_when_acquisition_refuses(monkeypatch, capsys):
    # Acquisition wrote the ref but could not re-read the ordering snapshot: `_turn_acquire` returns a
    # `[ship-only-guard]` refusal, not an expiry. The command must not report it as a held turn (exit 0).
    g, _ = load(monkeypatch)
    shim = ListFailsAfterWriteShim(g, True)
    monkeypatch.setattr(g.subprocess, "run", shim.run)
    rc = g.main(["--land-turn", str(NUMBER), "--repo", R, "--match-head-commit", HEAD])
    out = capsys.readouterr()
    text = out.out + out.err
    assert ("ref", TURN_REF) in shim.writes, (shim.writes, text)     # the refusal comes after the write
    assert rc == 1, text
    assert "holds the state land turn" not in text, text
    assert "No land turn recorded" in text and "unreadable after writing" in text, text
