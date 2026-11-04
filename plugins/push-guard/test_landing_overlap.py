"""Landing-overlap gate: a green exact-head check is not enough when main has since changed the PR's files.

Replays real daveey/hermes landings (slug renamed acme/core): PR #475 (head c058c7e3d3a, tested base 41b7bad881c3,
landed on main 92e72365f86 = cf4ec3f1a15^1, 250 commits later; two of its files changed meanwhile) and PR #681
(head e1e70ed04, landed 4 commits later on 881b67a0215 with none of its files touched). The GitHub API answers in
fixtures/landing_overlap_pr475_pr681.json were recorded from the live API (trimmed to the fields the guard reads),
so the 300-file truncation of the 250-commit compare is the real one.
Run: python3 -m pytest -q -p no:cacheprovider test_landing_overlap.py
"""
import json
import time
from pathlib import Path

import pytest

from tests_guard import CHECK, block, core, guard, run  # noqa: F401  (core is a fixture)

REC = json.loads((Path(__file__).resolve().parent / "fixtures" / "landing_overlap_pr475_pr681.json").read_text())
PR475, PR681 = REC["475"], REC["681"]
OVERLAP_475 = ("hermes_cli/kanban_db_connect.py", "hermes_cli/kanban_session_activity.py")


class RecordedGH:
    """guard._gh_json backed by the recorded API answers; `main` resolves to the tip the PR actually landed on."""

    def __init__(self, rec, number, branch):
        self.rec, self.calls = rec, []
        self.pr = {"headRefOid": rec["head"], "baseRefName": "main", "headRefName": branch, "number": int(number)}

    def __call__(self, args, cwd=None):
        self.calls.append(list(args))
        if args[:2] == ["pr", "view"]:
            return dict(self.pr), None
        if args[0] == "api":
            path = args[1].replace("/compare/main...", f"/compare/{self.rec['tip']}...")
            if "/check-runs" in path:
                return {"total_count": 1, "check_runs": [run("completed", "success")]}, None
            if path in self.rec["calls"]:
                return self.rec["calls"][path], None
            return None, f"HTTP 404: not recorded: {path}"
        return None, "unexpected"


def _merge(work, gh, entry, n):
    command = f"gh pr merge {n} -R acme/core --merge --match-head-commit {gh.pr['headRefOid']}"
    if entry == "terminal":
        return block("terminal", {"command": command, "workdir": str(work)})
    return block("execute_code", {"code": f"terminal({command!r}, workdir={str(work)!r})"})


@pytest.mark.parametrize("entry", ["terminal", "execute_code"])
def test_pr475_stale_green_head_is_refused_naming_both_overlapping_files(core, monkeypatch, entry):
    work, _ = core
    gh = RecordedGH(PR475, 475, "fix/session-open-stalls")
    monkeypatch.setattr(guard, "_gh_json", gh)
    got = _merge(work, gh, entry, 475)
    assert got and got["action"] == "block", got
    msg = got["message"]
    for path in OVERLAP_475:
        assert path in msg
    assert "41b7bad881c3" in msg  # the base CI tested
    assert "250" in msg  # how far main moved
    assert "ab8d9bb45db" in msg or "b896f8ad686" in msg  # a commit that changed the files
    assert "rebase" in msg.lower() and CHECK in msg
    assert "hermes-core-wt ship" not in msg  # not a wt/* branch: rebase, push, wait
    # the other PR files main did not touch are not named
    assert "kanban_reader_lifecycle.py" not in msg


def test_pr475_wt_branch_points_at_ship(core, monkeypatch):
    work, _ = core
    gh = RecordedGH(PR475, 475, "wt/session-open-stalls")
    monkeypatch.setattr(guard, "_gh_json", gh)
    got = _merge(work, gh, "terminal", 475)
    assert got and "hermes-core-wt ship session-open-stalls" in got["message"]


@pytest.mark.parametrize("entry", ["terminal", "execute_code"])
def test_pr681_moved_main_without_overlap_is_admitted(core, monkeypatch, entry):
    work, _ = core
    gh = RecordedGH(PR681, 681, "wt/mirror-singleton")
    monkeypatch.setattr(guard, "_gh_json", gh)
    assert _merge(work, gh, entry, 681) is None


def test_pr475_override_still_skips(core, monkeypatch):
    work, _ = core
    gh = RecordedGH(PR475, 475, "fix/session-open-stalls")
    monkeypatch.setattr(guard, "_gh_json", gh)
    assert block("terminal", {"command": "PUSH_GUARD=off gh pr merge 475 -R acme/core --merge",
                              "workdir": str(work)}) is None
    assert not gh.calls


def test_truncated_compare_without_visible_overlap_checks_each_path(core, monkeypatch):
    """The 250-commit compare lists only 300 of its 718 files. Hide the overlap from that page: the per-path
    history must still find it (and nothing else)."""
    work, _ = core
    rec = json.loads(json.dumps(PR475))
    page1 = next(k for k in rec["calls"] if k.endswith("per_page=100&page=1") and "/compare/41b7bad" in k)
    kept = [f for f in rec["calls"][page1]["files"] if f["filename"] not in OVERLAP_475]
    assert len(kept) == 298
    rec["calls"][page1]["files"] = kept + [{"filename": "docs/pad-1.md"}, {"filename": "docs/pad-2.md"}]  # still 300
    gh = RecordedGH(rec, 475, "fix/session-open-stalls")
    monkeypatch.setattr(guard, "_gh_json", gh)
    got = _merge(work, gh, "terminal", 475)
    assert got and all(p in got["message"] for p in OVERLAP_475)
    assert "kanban_reader_lifecycle.py" not in got["message"]


def test_unreadable_compare_is_refused(core, monkeypatch):
    work, _ = core
    rec = json.loads(json.dumps(PR681))
    rec["calls"] = {}
    gh = RecordedGH(rec, 681, "wt/mirror-singleton")
    monkeypatch.setattr(guard, "_gh_json", gh)
    got = _merge(work, gh, "terminal", 681)
    assert got and "could not be compared" in got["message"]


def test_head_on_top_of_main_needs_no_compare_beyond_the_first(core, monkeypatch):
    work, _ = core
    rec = {"head": "a" * 40, "tip": "b" * 40, "calls": {
        f"repos/acme/core/compare/{'b' * 40}...{'a' * 40}": {
            "merge_base_commit": {"sha": "b" * 40}, "base_commit": {"sha": "b" * 40}, "status": "ahead",
            "ahead_by": 1, "behind_by": 0, "total_commits": 1, "commits": [], "files": [{"filename": "x.py"}]}}}
    gh = RecordedGH(rec, 5, "feat")
    monkeypatch.setattr(guard, "_gh_json", gh)
    assert _merge(work, gh, "terminal", 5) is None
    assert sum("/compare/" in c[1] for c in gh.calls if c[0] == "api") == 1


class SlowGH(RecordedGH):
    """RecordedGH where every answer takes DELAY seconds, like the live API (~2 s per call on the hub)."""
    DELAY = 0.2

    def __call__(self, args, cwd=None):
        time.sleep(self.DELAY)
        return super().__call__(args, cwd)


def test_history_reads_run_in_parallel_within_the_hook_budget(core, monkeypatch):
    """A PR whose base moved 300+ files needs a compare page per 100 base commits plus one history read per PR
    path. Read one by one at live latency (~2 s each) that took 37 s for daveey/hermes#810, past the 30 s
    pre_tool_call budget, so the host dropped the hook and refused an admissible merge. The reads after the
    first compare must overlap: wall time well under the serial sum, same verdict."""
    work, _ = core
    rec = json.loads(json.dumps(PR475))
    page1 = next(k for k in rec["calls"] if k.endswith("per_page=100&page=1") and "/compare/41b7bad" in k)
    kept = [f for f in rec["calls"][page1]["files"] if f["filename"] not in OVERLAP_475]
    rec["calls"][page1]["files"] = kept + [{"filename": "docs/pad-1.md"}, {"filename": "docs/pad-2.md"}]
    gh = SlowGH(rec, 475, "fix/session-open-stalls")
    monkeypatch.setattr(guard, "_gh_json", gh)
    t0 = time.monotonic()
    got = _merge(work, gh, "terminal", 475)
    wall = time.monotonic() - t0
    assert got and all(p in got["message"] for p in OVERLAP_475)
    assert "kanban_reader_lifecycle.py" not in got["message"]
    serial = len(gh.calls) * SlowGH.DELAY
    assert len(gh.calls) >= 8, gh.calls
    assert wall < serial / 2, (wall, serial, len(gh.calls))
