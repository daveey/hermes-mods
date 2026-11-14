"""A 3-merge carry at landing-host latency decides with time left for the drift check (t_01a1dadbf711c31c).

State PR #2987 (2026-11-14) had an approval two to three update-branch merges back, and each merge content-merged
a file both sides changed (`hermes-sync` on state). Its head-pinned merge was refused five times with "preflight
deadline exceeded during drift check" or "... during carry blob reads". No refusal ever reached the drift
refusal and its land-turn follow-up, so the PR kept drifting. A read-only replay of
`_carry_one(13a06e5c4f, 4d3a775963)` on the landing host (load 55-65, cc3b484c) took 24.8 s:
- a lone gh call took 1.8 s; with ~8 concurrent, each took 4.3-8.4 s;
- the first `compare H0...head` (173 commits listed) took 4.28 s on its own;
- one `git fetch --depth=128` took 9.0-9.2 s.

3a9573ef (one fetch, local trees and blobs) removed the gh tree/blob waves. The critical path was still serial:
ancestry compare, then the fetch and the gh wave (membership compares, patch diffs, 3 merge-base compares), then
local reads, then the drift compare. That is 1.8 + 4.3 + 9 + 4.3 s of an 18 s budget.

Model below (Shim, scaled by SCALE): a gh call costs LATENCY in flight plus METER shim-seconds of host work
shared with every concurrent call (lone: 1.8 s; 8 at once: ~4.6 s). The ancestry compare H0...head lists
every commit merged in (173 commits, 729 KB on the real chain) and adds COMPARE_EXTRA, so a lone one takes
4.3 s as measured. The authoritative fetch holds a host share for FETCH = 9 s.

On the turn-write reserve: the authoritative fetch is part of the decision, so no in-hook carry can finish
before the fetch does. At FETCH = 9 s, 18 - 9 = 9 s is the most that could be left, below _WRITE_RESERVE (10 s),
even if the fetch started at hook t=0. This test therefore pins the bound the hook can meet: the carry
overlaps its fetch with the ancestry walk, and it leaves the drift check (and the refusal that names the
`--land-turn` follow-up, e9f2ae5c) enough time. A turn that needs more than this is recorded by that follow-up
under its own budget, never by a late hook.
"""
import hashlib
import importlib.util
import json
from pathlib import Path
import time

import pytest

_spec = importlib.util.spec_from_file_location(
    "drift_latency_fixture_3m", Path(__file__).with_name("tests_drift_deadline_latency.py"))
lat = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lat)

SCALE = lat.SCALE
LATENCY, METER, COMPARE_EXTRA, FETCH = 1.4, 0.4, 2.5, 9.0
R = "example/state-repo"
F = "home/bin/hermes-sync"
NUMBER = 2987


def sha(c):
    return c * 40


# approved head, merge base, 3 state tips, 3 update-branch merges (HEAD is the last)
H0, MB, B1, B2, B3, M1, M2, HEAD = (sha(c) for c in "a0123bcd")


def blob_id(data):
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def text(*edits):
    lines = [f"line {i}\n" for i in range(16)]
    for at, new in edits:
        lines[at] = new
    return "".join(lines).encode()


PR, S1, S2, S3 = (0, "pr edit\n"), (15, "state edit 1\n"), (11, "state edit 2\n"), (7, "state edit 3\n")
CONTENT = {MB: text(), H0: text(PR), B1: text(S1), B2: text(S1, S2), B3: text(S1, S2, S3),
           M1: text(PR, S1), M2: text(PR, S1, S2), HEAD: text(PR, S1, S2, S3)}
STATE_FILES = {B1: ["x/state1.py"], B2: ["x/state1.py", "y/state2.py"], B3: ["x/state1.py", "y/state2.py",
                                                                             "z/state3.py"]}
STATE_FILES.update({MB: [], H0: [], M1: STATE_FILES[B1], M2: STATE_FILES[B2], HEAD: STATE_FILES[B3]})
FILLER = {f"pkg{i}/mod{j}.py": ("100644", sha("0")[:-6] + f"{i:03d}{j:03d}") for i in range(40) for j in range(10)}
TREES = {c: {**FILLER, F: ("100755", blob_id(CONTENT[c])), **{p: ("100644", sha("e")[:-1] + str(i))
                                                               for i, p in enumerate(STATE_FILES[c])}}
         for c in CONTENT}
BLOBS = {blob_id(b): b for b in CONTENT.values()}
MERGES = [(M1, H0, B1, MB), (M2, M1, B2, B1), (HEAD, M2, B3, B2)]       # merge, ours, theirs, merge base
DIFF = (f"diff --git a/{F} b/{F}\nindex 1111111..2222222 100755\n--- a/{F}\n+++ b/{F}\n"
        "@@ -1,3 +1,3 @@\n-line 0\n+pr edit\n line 1\n line 2\n")


def pr_data():
    data = lat.pr_data()
    data.update(number=NUMBER, url=f"https://github.com/{R}/pull/{NUMBER}", baseRefOid=B3, headRefOid=HEAD,
                files=data["files"] + [{"path": F}])
    data["reviews"][0].update(body=f"VERDICT: APPROVE\n\nIndependent review of exact head {H0}.",
                              commit={"oid": H0})
    return data


class Shim(lat.FakeShim):
    """The landing host's gh shim: LATENCY + shared METER per call, COMPARE_EXTRA for the ancestry compare."""

    def answer(self, argv):
        words = [w for w in argv[2:] if not w.startswith("-") and not w.startswith("Accept:")]
        path = words[0] if words else ""
        if argv[1] == "pr":
            return json.dumps(pr_data()), 0
        if path.startswith(f"repos/{R}/git/matching-refs/"):
            return "[[]]", 0
        if path == f"repos/{R}/compare/{H0}...{HEAD}":
            return json.dumps({"status": "ahead", "behind_by": 0, "commits": [
                {"sha": m, "parents": [{"sha": o}, {"sha": t}]} for m, o, t, _ in MERGES]}), 0
        if path.startswith(f"repos/{R}/compare/state..."):
            return DIFF, 0
        if path == f"repos/{R}/compare/{B3}...state":          # carry membership and the drift check alike
            return json.dumps(lat.drift(self.overlap)), 0
        if path in (f"repos/{R}/compare/{B1}...state", f"repos/{R}/compare/{B2}...state"):
            return json.dumps({"status": "ahead", "behind_by": 0}), 0
        for m, o, t, base in MERGES:
            if path == f"repos/{R}/compare/{o}...{t}":
                return json.dumps({"merge_base_commit": {"sha": base}}), 0
        if path.startswith(f"repos/{R}/commits?"):
            return json.dumps([]), 0
        return "", 1                                           # gh tree/blob reads: the store must answer

    def run(self, argv, *args, **kwargs):
        argv = list(argv)
        if argv[0] != "gh":
            return self.real_run(argv, *args, **kwargs)        # local git patch-id / merge-file
        start = time.monotonic()
        self.busy(work=METER)                                  # the shim's metering shares the host
        time.sleep((LATENCY + (COMPARE_EXTRA if self.heavy(argv) else 0)) * SCALE)
        timeout = kwargs.get("timeout")
        if timeout is not None and time.monotonic() - start > timeout:
            raise lat.subprocess.TimeoutExpired(argv, timeout)
        out, rc = self.answer(argv)
        with self.lock:
            self.calls.append((start - self.t0, time.monotonic() - self.t0, self.g._PREFLIGHT_STAGE.get(),
                               " ".join(argv[:4])))
        return lat.subprocess.CompletedProcess(argv, rc, out, "")

    @staticmethod
    def heavy(argv):
        return argv[-1] == f"repos/{R}/compare/{H0}...{HEAD}"


def load(monkeypatch, overlap):
    g, _ = lat.load(monkeypatch, overlap)
    shim = Shim(g, overlap)
    monkeypatch.setattr(g.subprocess, "run", shim.run)
    fetches = []

    def fetch(objects):
        fetches.append((time.monotonic() - shim.t0, list(objects.shas)))
        g._remaining()
        shim.busy(seconds=FETCH)                               # one fetch; index-pack shares the host
        g._remaining()
        objects.path = "fetched"
        return ""

    def local_git(objects, *args, binary=False):
        # The fetched store answers what GitHub's trees/blobs/compare APIs do (content addressing).
        if args[:4] == ("ls-tree", "-r", "-l", "-z"):
            sizes = {b: len(d) for b, d in BLOBS.items()}
            return b"".join(f"{m} blob {s} {sizes.get(s, 10):>7}\t{p}\0".encode()
                            for p, (m, s) in TREES[args[4]].items())
        if args[:2] == ("cat-file", "blob"):
            return BLOBS[args[2]]
        if args[:2] == ("merge-base", "--all"):
            return next(base for _, o, t, base in MERGES if (o, t) == args[2:4])
        raise AssertionError(args)

    monkeypatch.setattr(g._CarryObjects, "_fetch", fetch)
    monkeypatch.setattr(g._CarryObjects, "git", local_git)
    monkeypatch.setattr(g._CarryObjects, "verify", lambda *a: "")
    left = []
    carry = g._carry_one

    def timed_carry(*args, **kwargs):
        out = carry(*args, **kwargs)
        left.append((g._PREFLIGHT_DEADLINE.get() - time.monotonic()) / SCALE)
        return out
    monkeypatch.setattr(g, "_carry_one", timed_carry)
    return g, shim, fetches, left


@pytest.mark.parametrize("overlap", [False, True])
def test_three_merge_carry_decides_with_the_drift_check_in_budget(monkeypatch, overlap):
    g, shim, fetches, left = load(monkeypatch, overlap)
    assert g._CARRY_MERGES == 3 and len(MERGES) == g._CARRY_MERGES      # the longest admitted chain
    words = ["pr", "merge", str(NUMBER), "-R", R, "--rebase", "--match-head-commit", HEAD]
    start = time.monotonic()
    result = g.bounded_preflight(g._state_merge, words, R, ".", {})
    took = (time.monotonic() - start) / SCALE
    timeline = "\n".join(f"{s / SCALE:6.2f} {e / SCALE:6.2f} [{st}] {w}" for s, e, st, w in sorted(shim.calls))
    timeline += "\nfetches: " + ", ".join(f"{t / SCALE:.2f}" for t, _ in fetches)
    timeline += f"\nleft after carry: {left}"
    assert "deadline exceeded" not in str(result), f"{result} after {took:.1f} shim-seconds\n{timeline}"
    # Time left after the carry: pr view (1.8 s) + max(walk + membership compares, fetch) is ~11 s of 18, so
    # ~7 s remain for the drift compare and its refusal. origin/main (walk, then fetch) left ~2 s: the drift
    # check ended at ~17.8 s, within a scheduling hiccup of "deadline exceeded during drift check".
    assert len(left) == 1 and left[0] >= 6, timeline
    assert took < 16, f"preflight took {took:.1f} shim-seconds of 18\n{timeline}"
    drift_reads = [c for c in shim.calls if c[2] == "drift check"]
    assert drift_reads and min(c[0] for c in drift_reads) >= max(
        c[1] for c in shim.calls if c[2].startswith("carry")), timeline
    if overlap:
        assert "state commit(s) landed since then" in result and "worker_parity.py" in result, result
        assert "--land-turn" in result, result     # the refusal names the out-of-hook turn record
    else:
        assert result is True, result
    # One authoritative fetch serves the chain; no immutable read goes through gh trees/blobs.
    assert len(fetches) == 1, timeline
    assert not [w for _, _, _, w in shim.calls if "/git/trees/" in w or "/git/blobs/" in w], timeline
    # The fetch starts with the ancestry walk, not after it: the carry costs about one fetch, not walk + fetch.
    walk = next(s for s, _, _, w in shim.calls if w.endswith(f"compare/{H0}...{HEAD}"))
    assert fetches[0][0] - walk < 1.0 * SCALE, timeline
    # The prefilter takes each merge base from the fetched store: no gh merge-base compare joins the wave.
    assert not [w for _, _, _, w in shim.calls if any(w.endswith(f"compare/{o}...{t}") for _, o, t, _ in MERGES)]
    print(f"\n{took:.2f} shim-seconds, overlap={overlap}\n{timeline}")