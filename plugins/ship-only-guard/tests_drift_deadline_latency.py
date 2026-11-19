"""PR #2842 (t_01a1d96e71233fe1): the carry left the drift check no time under the worker gh shim.

Live on a pool worker (2026-11-14 05:36-05:38Z, with 6a21b258 installed) `gh pr merge 2842 --rebase
--match-head-commit 9a389c2c63` was refused three times with "preflight deadline exceeded during drift check"
(`time`: 19.3 s), then merged on a blind retry. Its approval was on e1b0255c29 and the head had since merged
state twice (e1b0255c29 -> eca013a9f3 -> 9a389c2c63), so the patch-identical carry ran first. A replay of that
preflight with per-call timings (read-only, real gh) spent 15.8 s of the 18 s in the carry and
started the drift compare at 15.76 s: compare -> 8 recursive tree reads (two of them duplicates) -> blob reads
ran as three serial waves while every concurrent gh call slowed the others.

The original fake reproduced measured shim latency with scaled wall time. Runner scheduling noise was
amplified tenfold and could fail healthy code. This test instead guards the expensive work and its order:
one shared fetch for both merges, no remote tree/blob reads, and drift reads only after carry completes.
The guard's clock is frozen locally (not the process-wide time module), so host delays cannot consume
its deadline. Real thread-pool execution, content checks and local merge-file/patch-id stay exercised.

t_01a1da82db1127a5: after that fix the carry wave still held ~13 concurrent gh calls (6 of them tree reads),
then a serial merge-base tree read and a serial blob wave; the drift compare started at 14.3 s of 18. The
prefilter now reads trees and blobs from the chain's one authoritative fetch (faked below as a store that
answers what GitHub's trees/blobs API does). Avoiding that redundant work leaves the drift check its time;
this is a work/order regression contract, not a benchmark or a test of deadline expiration.
"""
import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import threading
import time
from types import SimpleNamespace

import pytest

# Compatibility model for the separate three-merge/turn latency benchmarks. The regression below
# uses FakeShim and causal ordinals exclusively; those consumers explicitly opt into WallClockShim.
SCALE = 0.1
TICK = 0.002
LATENCY, METER, TREE_EXTRA, FETCH = 1.3, 0.33, 0.15, 6.5

R = "example/state-repo"
F = "hosts/workers/worker_disk_reclaim.py"


def sha(c):
    return c * 40


H0, B1, M1, B2, HEAD, MB1 = (sha(c) for c in "abcdef")   # approved head, state, merge, state(=base), head, base


def blob_id(data):
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


LINES = [f"line {i}\n".encode() for i in range(12)]
BASE_F = b"".join(LINES)
PR_F = b"pr edit\n" + b"".join(LINES[1:])                   # the PR's own patch, top of the file
STATE_F = b"".join(LINES[:-1]) + b"state edit\n"           # a state commit on the same file, bottom
MERGED_F = b"pr edit\n" + b"".join(LINES[1:-1]) + b"state edit\n"
FILLER = {f"pkg{i}/mod{j}.py": ("100644", sha("0")[:-6] + f"{i:03d}{j:03d}") for i in range(40) for j in range(10)}


def tree(**over):
    files = dict(FILLER)
    files.update(over)
    return files


TREES = {
    MB1: tree(**{F: ("100644", blob_id(BASE_F))}),
    H0: tree(**{F: ("100644", blob_id(PR_F))}),
    B1: tree(**{F: ("100644", blob_id(STATE_F)), "x/state1.py": ("100644", sha("1"))}),
    M1: tree(**{F: ("100644", blob_id(MERGED_F)), "x/state1.py": ("100644", sha("1"))}),
    B2: tree(**{F: ("100644", blob_id(STATE_F)), "x/state1.py": ("100644", sha("1")), "y/state2.py": ("100644", sha("2"))}),
    HEAD: tree(**{F: ("100644", blob_id(MERGED_F)), "x/state1.py": ("100644", sha("1")),
                  "y/state2.py": ("100644", sha("2"))}),
}
BLOBS = {blob_id(b): b for b in (BASE_F, PR_F, STATE_F)}
DIFF = (f"diff --git a/{F} b/{F}\nindex 1111111..2222222 100644\n--- a/{F}\n+++ b/{F}\n"
        "@@ -1,3 +1,3 @@\n-line 0\n+pr edit\n line 1\n line 2\n")


def drift(overlap):
    # 11 state commits since the PR's tested base (live #2842: 8 behind / 15 ahead of state).
    commits = [{"sha": sha("%x" % (i + 1))[:-1] + "9", "commit": {"message": f"fix: state work {i}"}}
               for i in range(11)]
    files = [{"filename": f"z/elsewhere{i}.py"} for i in range(11)]
    if overlap:
        files.append({"filename": "hosts/workers/worker_parity.py"})
    return {"status": "ahead", "behind_by": 0, "total_commits": 11, "commits": commits, "files": files}


def pr_data():
    return {
        "number": 2842, "url": f"https://github.com/{R}/pull/2842", "state": "OPEN", "baseRefName": "state",
        "baseRefOid": B2, "headRefOid": HEAD, "files": [{"path": F}],
        "statusCheckRollup": [{"__typename": "CheckRun", "name": f"check {i}", "status": "COMPLETED",
                               "conclusion": "SUCCESS"} for i in range(13)],
        "reviews": [{"author": {"login": "reviewer"}, "authorAssociation": "OWNER", "state": "COMMENTED",
                     "body": f"VERDICT: APPROVE\n\nIndependent review of exact head {H0}.",
                     "submittedAt": "2026-11-14T05:11:33Z", "commit": {"oid": H0}}],
        "comments": [],
    }


class FakeShim:
    """Record causal start/end ordinals, independent of host speed and thread scheduling."""

    def __init__(self, g, overlap):
        self.g, self.overlap = g, overlap
        self.lock, self.ordinal = threading.Lock(), 0
        self.calls, self.fetches, self.local_reads, self.verified = [], [], [], []
        self.carry_done = []
        self.real_run = subprocess.run

    def tick(self):
        with self.lock:
            self.ordinal += 1
            return self.ordinal

    def answer(self, argv):
        words = [w for w in argv[2:] if not w.startswith("-") and not w.startswith("Accept:")]
        path = words[0] if words else ""
        if argv[1] == "pr":
            return json.dumps(pr_data()), 0
        if path.startswith(f"repos/{R}/git/matching-refs/"):
            return "[[]]", 0
        if path == f"repos/{R}/compare/{H0}...{HEAD}":
            return json.dumps({"status": "ahead", "behind_by": 0, "commits": [
                {"sha": M1, "parents": [{"sha": H0}, {"sha": B1}]},
                {"sha": HEAD, "parents": [{"sha": M1}, {"sha": B2}]}]}), 0
        if path.startswith(f"repos/{R}/compare/state..."):
            return DIFF, 0
        if path == f"repos/{R}/compare/{B2}...state":       # carry membership and the drift check alike
            return json.dumps(drift(self.overlap)), 0
        if path == f"repos/{R}/compare/{B1}...state":
            return json.dumps({"status": "ahead", "behind_by": 0}), 0
        if path == f"repos/{R}/compare/{H0}...{B1}":
            return json.dumps({"merge_base_commit": {"sha": MB1}}), 0
        if path == f"repos/{R}/compare/{M1}...{B2}":
            return json.dumps({"merge_base_commit": {"sha": B1}}), 0
        if path.startswith(f"repos/{R}/git/trees/"):
            got = TREES[path.split("/")[-1].split("?")[0]]
            return json.dumps({"truncated": False, "tree": [
                {"path": p, "mode": m, "type": "blob", "sha": s, "size": 10} for p, (m, s) in got.items()]}), 0
        if path.startswith(f"repos/{R}/git/blobs/"):
            data = BLOBS[path.split("/")[-1]]
            return json.dumps({"encoding": "base64", "content": base64.b64encode(data).decode()}), 0
        if path.startswith(f"repos/{R}/commits?"):
            return json.dumps([]), 0
        return "", 1

    def run(self, argv, *args, **kwargs):
        argv = list(argv)
        if argv[0] != "gh":
            return self.real_run(argv, *args, **kwargs)      # local git patch-id / merge-file
        start = self.tick()
        out, rc = self.answer(argv)
        end = self.tick()
        with self.lock:
            self.calls.append((start, end, self.g._PREFLIGHT_STAGE.get(),
                               " ".join(argv[:4])))
        return subprocess.CompletedProcess(argv, rc, out, "")


class WallClockShim(FakeShim):
    """Legacy measured-latency model, retained only for explicitly opted-in sibling tests."""

    def __init__(self, g, overlap):
        super().__init__(g, overlap)
        self.active, self.t0 = 0, time.monotonic()

    def busy(self, seconds=None, work=0.0):
        with self.lock:
            self.active += 1
        try:
            if seconds is not None:
                time.sleep(seconds * SCALE)
            # Meter the elapsed time, not the requested TICK: a host whose short sleeps overshoot (macOS
            # timer slack, ~0.5 ms of 2 ms) would otherwise stretch every METER by a quarter (t_01a1e13f02725e97).
            done, last = 0.0, time.monotonic()
            while done < work:
                time.sleep(TICK)
                with self.lock:
                    now = time.monotonic()
                    done += (now - last) / SCALE / self.active
                    last = now
        finally:
            with self.lock:
                self.active -= 1

    def run(self, argv, *args, **kwargs):
        argv = list(argv)
        if argv[0] != "gh":
            return self.real_run(argv, *args, **kwargs)
        start = time.monotonic()
        timeout = kwargs.get("timeout")
        self.busy(work=METER)
        extra = TREE_EXTRA if any("/git/trees/" in w for w in argv) else 0
        time.sleep((LATENCY + extra) * SCALE)
        if timeout is not None and time.monotonic() - start > timeout:
            raise subprocess.TimeoutExpired(argv, timeout)
        out, rc = self.answer(argv)
        with self.lock:
            self.calls.append((start - self.t0, time.monotonic() - self.t0, self.g._PREFLIGHT_STAGE.get(),
                               " ".join(argv[:4])))
        return subprocess.CompletedProcess(argv, rc, out, "")


def load(monkeypatch, overlap, *, wall_clock=False):
    spec = importlib.util.spec_from_file_location("drift_latency_guard", Path(__file__).with_name("guard.py"))
    g = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(g)
    if wall_clock:
        monkeypatch.setattr(g, "PREFLIGHT_TIMEOUT", 18 * SCALE)
    else:
        monkeypatch.setattr(g, "time", SimpleNamespace(monotonic=lambda: 1000.0))
    shim = (WallClockShim if wall_clock else FakeShim)(g, overlap)
    monkeypatch.setattr(g, "subprocess", SimpleNamespace(**{**vars(subprocess), "run": shim.run}))

    def fetch(objects):
        g._remaining()
        with shim.lock:
            shim.fetches.append(tuple(objects.shas))
        if wall_clock:
            shim.busy(seconds=FETCH)
        g._remaining()
        objects.path = "fetched"
        return ""

    def local_git(objects, *args, binary=False):
        # The fetched object store answers what GitHub's trees/blobs API does (content addressing).
        with shim.lock:
            shim.local_reads.append(args)
        if args[:4] == ("ls-tree", "-r", "-l", "-z"):
            sizes = {blob_id(b): len(b) for b in BLOBS.values()}
            return b"".join(f"{m} blob {s} {sizes.get(s, 10):>7}\t{p}\0".encode()
                            for p, (m, s) in TREES[args[4]].items())
        if args[:2] == ("cat-file", "blob"):
            return BLOBS[args[2]]
        if args[:2] == ("merge-base", "--all"):               # as the merge-base compares answer (t_01a1dadbf711c31c)
            return {(H0, B1): MB1, (M1, B2): B1}[args[2:4]]
        raise AssertionError(args)
    monkeypatch.setattr(g._CarryObjects, "_fetch", fetch)
    monkeypatch.setattr(g._CarryObjects, "git", local_git)

    def verify(objects, merge, ours, theirs):
        with shim.lock:
            shim.verified.append((merge, ours, theirs))
        return ""

    monkeypatch.setattr(g._CarryObjects, "verify", verify)
    carry = g._carry_one

    def recorded_carry(*args, **kwargs):
        result = carry(*args, **kwargs)
        shim.carry_done.append(shim.tick())
        return result

    monkeypatch.setattr(g, "_carry_one", recorded_carry)
    return g, shim


@pytest.mark.parametrize("overlap", [False, True])
def test_two_merge_carry_leaves_the_drift_check_its_time(monkeypatch, overlap):
    g, shim = load(monkeypatch, overlap)
    words = ["pr", "merge", "2842", "-R", R, "--rebase", "--match-head-commit", HEAD]
    result = g.bounded_preflight(g._state_merge, words, R, ".", {})
    timeline = "\n".join(f"{s:3d} {e:3d} [{st}] {w}" for s, e, st, w in sorted(shim.calls))
    assert "deadline exceeded" not in str(result), f"{result}\n{timeline}"
    if overlap:
        assert "state commit(s) landed since then" in result and "worker_parity.py" in result
    else:
        assert result is True
    assert len(shim.fetches) == 1, shim.fetches
    assert shim.fetches == [(HEAD,)]  # one bounded ancestor fetch serves the entire chain
    assert sorted(shim.verified) == sorted([(M1, H0, B1), (HEAD, M1, B2)])

    # Every immutable carry read is made once, and the drift compare is still the last read of state.
    trees = [w for _, _, _, w in shim.calls if "/git/trees/" in w]
    assert len(trees) == len(set(trees)), timeline
    drift_reads = [c for c in shim.calls if c[2] == "drift check"]
    assert drift_reads and min(c[0] for c in drift_reads) >= max(
        c[1] for c in shim.calls if c[2].startswith("carry")), timeline
    assert len(shim.carry_done) == 1, shim.carry_done
    assert min(c[0] for c in drift_reads) > shim.carry_done[0], timeline
    # t_01a1da82db1127a5: the prefilter reads trees and blobs from the authoritative fetch, so no gh tree or
    # blob read joins the carry wave. This rejects the pre-3a9573ef expensive API-prefilter behaviour.
    assert not [w for _, _, _, w in shim.calls if "/git/trees/" in w or "/git/blobs/" in w], timeline
    assert any(args[0] == "ls-tree" for args in shim.local_reads)
    assert any(args[0] == "cat-file" for args in shim.local_reads)
