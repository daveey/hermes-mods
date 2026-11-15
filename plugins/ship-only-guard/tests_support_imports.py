"""A flat-dir support module overlaps only the test modules that reach it (t_01a1e103810098d4).

State PR #3207 changed tests/placement_support.py. The flat-dir `shared` rule made that overlap every
tests/test_*.py change, so #3331 (test_fixture_git_override_maintenance.py), #3323 (test_hub_install_manifest.py)
and state fe13a4c3bb (test_github_quota_wake.py) each drifted it or held it behind their land turn, although none
of them imports placement_support (on 2026-11-15 only 6 state test modules do). Fake gh only; never merges.
"""
import base64
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location("support_imports_guard", Path(__file__).with_name("guard.py"))
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)

R = "example/protected"
HEAD = "a" * 40          # PR head
BASE = "3" * 40          # PR's CI base
TIP = "c" * 40           # state tip in the drift compare
SUPPORT = "tests/placement_support.py"
# #3207's files (gh pr view 3207 --json files, 2026-11-15).
F3207 = ["docs/disk-alert-delivery.md", "hosts/hermes-hub/cron-inbox/cron_to_inbox.sh",
         "hosts/hermes-hub/disk-guard/hub-disk-failure@.service", "hosts/hermes-hub/disk-guard/hub-disk-guard.service",
         "hosts/hermes-hub/disk-guard/hub_disk_retention.py", "hosts/hermes-hub/disk-guard/test_alert_delivery.py",
         "scripts/hub-collection-lock.json", "scripts/hub-offline-tests.json", SUPPORT,
         "tests/test_disk_alert_failure_route.py", "tests/test_placement_producers.py"]

# The import lines of the real modules (origin/state, 2026-11-15): none reaches placement_support.
UNRELATED = {
    "tests/test_fixture_git_override_maintenance.py": "import io\nimport os\nimport unittest\nfrom unittest.mock import patch\n",
    "tests/test_hub_install_manifest.py": "import json\nimport unittest\nfrom sync_test_support import fixture_git_config\n",
    "tests/test_github_quota_wake.py": "import contextlib\nimport importlib.util\nimport unittest\n",
    "tests/test_turn_urgency_effective.py": "import json\nimport unittest\nfrom sync_test_support import fixture_git_config\n",
}
SUPPORT_TEXT = {
    SUPPORT: "import json\nfrom pathlib import Path\n",
    "tests/sync_test_support.py": "import json\nimport subprocess\n",
    "tests/__init__.py": "",
    "tests/test_placement_producers.py": "import placement_support as ps\n",
    "tests/test_disk_alert_failure_route.py": "import placement_support\n",
}


def blob_sha(text):
    data = text.encode()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def payload(files, head=HEAD):
    return {"number": 12, "state": "OPEN", "baseRefName": "state", "headRefOid": head, "baseRefOid": BASE,
            "url": f"https://github.com/{R}/pull/12", "comments": [],
            "reviews": [{"id": "PRR_x", "author": {"login": "you"}, "authorAssociation": "MEMBER",
                         "state": "COMMENTED", "submittedAt": "2026-11-15T15:00:00Z", "commit": {"oid": head},
                         "body": f"Independent exact-head review @ {head}: approve"}],
            "files": [{"path": f} for f in files],
            "statusCheckRollup": [{"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "SUCCESS"}]}


def delta(files):
    return {"status": "ahead", "ahead_by": 1, "behind_by": 0, "total_commits": 1,
            "commits": [{"sha": TIP, "parents": [{"sha": BASE}], "commit": {"message": "base change"}}],
            "files": [{"filename": f, "status": "modified", "previous_filename": None} for f in files]}


class Fake:
    """gh pr view, the drift compare, `contents/<dir>?ref=` listings and `git/blobs/<sha>` reads."""

    def __init__(self, data, compare, trees):
        self.data, self.compare, self.trees, self.calls = data, compare, trees, []
        self.fail_listing, self.corrupt = set(), set()
        self.blobs = {blob_sha(t): t for tree in trees.values() for t in tree.values()}

    def run(self, argv, **kw):
        self.calls.append(argv)
        ok = lambda body: SimpleNamespace(returncode=0, stdout=json.dumps(body), stderr="")
        fail = SimpleNamespace(returncode=1, stdout=json.dumps({"message": "Not Found"}), stderr="")
        if argv[:3] == ["gh", "pr", "view"]:
            return ok(self.data)
        path = argv[2]
        if "/compare/" in path:
            return ok(self.compare)
        if "/contents/" in path:
            directory, ref = path.split("/contents/", 1)[1].split("?ref=")
            if ref in self.fail_listing or ref not in self.trees:
                return fail
            return ok([{"name": p.rsplit("/", 1)[1], "path": p, "sha": blob_sha(t), "size": len(t.encode()),
                        "type": "file"} for p, t in sorted(self.trees[ref].items())
                       if p.rsplit("/", 1)[0] == directory])
        if "/git/blobs/" in path:
            sha = path.rsplit("/", 1)[1]
            if sha not in self.blobs:
                return fail
            text = self.blobs[sha] + ("corrupt" if sha in self.corrupt else "")
            return ok({"encoding": "base64", "content": base64.b64encode(text.encode()).decode()})
        if "/commits?" in path:
            return fail
        raise AssertionError(f"unexpected gh call {argv}")

    def reads(self):
        return [a[2] for a in self.calls if a[:2] == ["gh", "api"] and ("/contents/" in a[2] or "/git/blobs/" in a[2])]


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setattr(g, "_live_turns", lambda *a: [])
    monkeypatch.setattr(g, "_turn_acquire", lambda *a: "")

    def make(pr_files, base_files, pr_tree=None, base_tree=None):
        f = Fake(payload(pr_files), delta(base_files),
                 {HEAD: {**SUPPORT_TEXT, **UNRELATED, **(pr_tree or {})},
                  TIP: {**SUPPORT_TEXT, **UNRELATED, **(base_tree or {})}})
        monkeypatch.setattr(g.subprocess, "run", f.run)
        return f
    return make


def merge():
    return g.bounded_preflight(
        g.check_argv, ["gh", "pr", "merge", "12", "--repo", R, "--rebase", "--match-head-commit", HEAD],
        repos={R}, env={"GH_REPO": R})


@pytest.mark.parametrize("module", sorted(UNRELATED))
def test_3207_support_change_not_drifted_by_unrelated_test_module(fake, module):
    # 11-15 sequence: each of these landed on state inside #3207's CI window and refused it as drift.
    f = fake(F3207, [module])
    assert merge() is None
    assert any(f"/contents/tests?ref={TIP}" in r for r in f.reads())


def test_support_change_drifted_by_a_test_that_imports_it(fake):
    fake(F3207, ["tests/test_github_quota_alerts.py"],
         base_tree={"tests/test_github_quota_alerts.py": "import placement_support as ps  # noqa: E402\n"})
    why = merge()
    assert why and f"tests/test_github_quota_alerts.py (shared ~ {SUPPORT})" in why


def test_support_reached_through_another_support_module_still_drifts(fake):
    fake(F3207, ["tests/test_hub_install_manifest.py"],
         base_tree={"tests/sync_test_support.py": "import placement_support\n"})
    why = merge()
    assert why and f"tests/test_hub_install_manifest.py (shared ~ {SUPPORT})" in why


def test_support_loaded_by_path_string_still_drifts(fake):
    fake(F3207, ["tests/test_x.py"],
         base_tree={"tests/test_x.py": 'spec = spec_from_file_location("p", HERE / "placement_support.py")\n'})
    assert "shared ~ tests/placement_support.py" in merge()


def test_test_change_not_drifted_by_unimported_base_support_change(fake):
    # Reverse direction: the PR changes one test module, state changed a support module it never reaches.
    f = fake(["tests/test_hub_install_manifest.py"], [SUPPORT])
    assert merge() is None
    assert any(f"/contents/tests?ref={HEAD}" in r for r in f.reads())     # read at the PR's own head


def test_test_change_drifted_by_imported_base_support_change(fake):
    fake(["tests/test_placement_producers.py"], [SUPPORT])
    assert "shared ~ tests/test_placement_producers.py" in merge()


@pytest.mark.parametrize("breakage", ["listing", "blob"])
def test_unreadable_import_scan_fails_closed(fake, breakage):
    f = fake(F3207, ["tests/test_hub_install_manifest.py"])
    if breakage == "listing":
        f.fail_listing.add(TIP)
    else:
        f.corrupt.add(blob_sha(UNRELATED["tests/test_hub_install_manifest.py"]))
    assert "shared ~ tests/placement_support.py" in merge()


@pytest.mark.parametrize("support", ["tests/__init__.py", "tests/conftest.py", "tests/ci_durations.json"])
def test_package_conftest_and_data_support_stay_shared_without_reads(fake, support):
    # pytest/package import machinery and data files are not reached through an import name.
    f = fake([support], ["tests/test_hub_install_manifest.py"])
    assert "shared ~ " + support in merge()
    assert not f.reads()


def test_two_support_modules_stay_shared(fake):
    f = fake([SUPPORT], ["tests/sync_test_support.py"])
    assert "shared ~ tests/placement_support.py" in merge()
    assert not f.reads()


def test_path_only_overlap_is_unchanged():
    # Callers without content (effective-turn ordering, the attribution text) keep the conservative rule.
    assert g._overlap(SUPPORT, "tests/test_hub_install_manifest.py") == "shared"
    assert g._overlap("tests/test_hub_install_manifest.py", SUPPORT) == "shared"


# --- land turn: #3323 waited behind #3207's turn on the same rule (17:19Z refusal) -----------------------------
def test_turn_holder_with_support_change_does_not_hold_unrelated_test_pr(monkeypatch):
    holder_head, waiter_head = "d" * 40, "e" * 40
    rec = {"pr": 3207, "head": holder_head, "acquired": 1, "expires": 2, "files": F3207}
    waiter = payload(["hosts/hermes-hub/bin/hub-core-update", "tests/test_hub_install_manifest.py"], waiter_head)
    trees = {holder_head: {**SUPPORT_TEXT, **UNRELATED}, waiter_head: {**SUPPORT_TEXT, **UNRELATED}}
    f = Fake(waiter, delta([]), trees)
    monkeypatch.setattr(g.subprocess, "run", f.run)
    monkeypatch.setattr(g, "_live_turns", lambda *a: [("refs/land-turn/pr-3207", rec)])
    monkeypatch.setattr(g, "_turn_holder_data", lambda n, *a: {"state": "OPEN"})
    monkeypatch.setattr(g, "_turn_yielded", lambda *a: False)
    assert g._turn_hold_refusal(3323, waiter, R, None, {}) is None
    # The same waiter is held when its test module does import the holder's support module.
    trees[waiter_head]["tests/test_hub_install_manifest.py"] = "import placement_support\n"
    f.blobs[blob_sha("import placement_support\n")] = "import placement_support\n"
    why = g._turn_hold_refusal(3323, waiter, R, None, {})
    assert why and "PR #3207 holds the state land turn" in why
    assert "tests/test_hub_install_manifest.py ~ tests/placement_support.py" in why


def test_reach_is_read_not_failed_closed(monkeypatch):
    # The held cases above must be decided by content: the reach read itself succeeds and names the module.
    f = Fake(payload([]), delta([]), {TIP: {**SUPPORT_TEXT, **UNRELATED,
                                           "tests/sync_test_support.py": "import placement_support\n"}})
    monkeypatch.setattr(g.subprocess, "run", f.run)
    once = g._Once()
    assert g._support_reach(R, TIP, "tests/test_hub_install_manifest.py", None, {}, once) == {
        "sync_test_support", "placement_support"}
    assert g._support_reach(R, TIP, "tests/test_github_quota_wake.py", None, {}, once) == frozenset()
    listings = [r for r in f.reads() if "/contents/" in r]
    assert listings == [f"repos/{R}/contents/tests?ref={TIP}"]          # one listing per tree, shared
