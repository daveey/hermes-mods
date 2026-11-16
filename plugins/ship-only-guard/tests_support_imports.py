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

# The import lines of the real modules (origin/state, 2026-11-15): none reaches placement_support. On their full
# real closures test_hub_install_manifest and test_github_quota_wake narrow; test_fixture_git_override_maintenance
# runs eight other test modules by name, one of which loads a computed target, so it stays shared (correctly).
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
            return ok([{"name": p.rsplit("/", 1)[-1], "path": p, "sha": blob_sha(t), "size": len(t.encode()),
                        "type": "file"} for p, t in sorted(self.trees[ref].items())
                       if (p.rsplit("/", 1)[0] if "/" in p else "") == directory])
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
    assert {"sync_test_support", "placement_support"} <= g._support_reach(
        R, TIP, "tests/test_hub_install_manifest.py", None, {}, once)
    assert "placement_support" not in g._support_reach(R, TIP, "tests/test_github_quota_wake.py", None, {}, once)
    listings = sorted(r for r in f.reads() if "/contents/" in r)
    # one listing per directory per tree, shared across test modules: the test dir and the repo root
    assert listings == [f"repos/{R}/contents/?ref={TIP}", f"repos/{R}/contents/tests?ref={TIP}"]


# --- review t_01a1e1747162693f: absence of the stem as a listed word is not proof of non-reach ---------------
def drift(pr_files, base_files, head_tree, tip_tree, monkeypatch):
    """Drift preflight over explicit trees (no default support files)."""
    f = Fake(payload(pr_files), delta(base_files), {HEAD: head_tree, TIP: tip_tree})
    monkeypatch.setattr(g.subprocess, "run", f.run)
    monkeypatch.setattr(g, "_live_turns", lambda *a: [])
    monkeypatch.setattr(g, "_turn_acquire", lambda *a: "")
    return merge()


def changed(tip, monkeypatch, pr_files=(SUPPORT,), base_files=("tests/test_x.py",)):
    """The PR changes placement_support's VALUE 1 -> 2; state changed `base_files` meanwhile."""
    return drift(list(pr_files), list(base_files), {**tip, SUPPORT: "VALUE = 2\n"}, tip, monkeypatch)


OPTIONAL = "try:\n    from tests.placement_support import VALUE\nexcept ImportError:\n    VALUE = 1\nassert VALUE == 1\n"


def test_added_support_named_by_test_on_the_other_side_drifts(monkeypatch):
    # P1 #1: the PR adds the support module; the test on state names it but its tree has no such file.
    tip = {"tests/__init__.py": "", "tests/test_x.py": OPTIONAL}
    why = changed(tip, monkeypatch)
    assert why and "shared ~ tests/placement_support.py" in why


def test_test_naming_support_added_by_state_drifts(monkeypatch):
    # P1 #1, other direction: state added the support module, the PR's test (own tree lacks it) names it.
    head = {"tests/__init__.py": "", "tests/test_x.py": OPTIONAL}
    why = drift(["tests/test_x.py"], [SUPPORT], head, {**head, SUPPORT: "VALUE = 2\n"}, monkeypatch)
    assert why and "shared ~ tests/test_x.py" in why


def test_deleted_support_named_by_test_drifts(monkeypatch):
    tip = {"tests/__init__.py": "", SUPPORT: "VALUE = 1\n", "tests/test_x.py": OPTIONAL}
    head = {k: v for k, v in tip.items() if k != SUPPORT}
    why = drift([SUPPORT], ["tests/test_x.py"], head, tip, monkeypatch)
    assert why and "shared ~ tests/placement_support.py" in why


@pytest.mark.parametrize("seed", ["tests/__init__.py", "tests/conftest.py", "conftest.py", "pytest.ini"])
def test_unchanged_package_or_pytest_machinery_importing_support_drifts(monkeypatch, seed):
    # P1 #2: an unchanged initializer/conftest/pytest config naming the support module reaches every test beside it.
    tip = {"tests/__init__.py": "", SUPPORT: "VALUE = 1\n", "tests/test_x.py": "import tests\nassert tests.VALUE == 1\n",
           seed: ("[pytest]\naddopts = -p tests.placement_support\n" if seed.endswith(".ini")
                  else "from tests.placement_support import VALUE\n")}
    why = changed(tip, monkeypatch)
    assert why and "shared ~ tests/placement_support.py" in why


HELPER = ('import importlib\ndef load():\n'
          '    return importlib.import_module("tests." + "placement" + "_support")\n')


@pytest.mark.parametrize("test_text", [
    'from importlib import import_module\nm = import_module("tests." + "placement_" + "support")\n',
    'import importlib as il\nm = il.import_module("tests.%s_support" % "placement")\n',
    'from importlib import import_module as im\nm = im(f"tests.{PREFIX}_support")\n',
    'import runpy\nNAME = "placement" + "_support.py"\nns = runpy.run_path(str(HERE / NAME))\n',
    'import importlib.util\nspec = importlib.util.spec_from_file_location("p", HERE / ("placement" + "_support.py"))\n',
    'exec(open(HERE / ("placement" + "_support.py")).read())\n',
    'import importlib\nload = importlib.import_module\nm = load("tests.placement_" + "support")\n',
    'import importlib\nm = getattr(importlib, "import_module")("tests.placement_" + "support")\n',
    'import sys, subprocess\nsubprocess.run([sys.executable, "-c", f"import {MOD}"])\n',
    'import sys, subprocess\nsubprocess.run([sys.executable, "-m", "pytest", "tests"])\n',
    'import unittest\nunittest.defaultTestLoader.discover("tests")\n',
    'from sync_helper_support import load\nload()\n',
])
def test_unresolved_dynamic_load_keeps_support_shared(monkeypatch, test_text):
    # P1 #3: a computed import name has no contiguous stem; unresolved loaders stay conservative,
    # including inside a support module the test names (sync_helper_support).
    tip = {"tests/__init__.py": "", SUPPORT: "VALUE = 1\n", "tests/test_x.py": test_text,
           "tests/sync_helper_support.py": HELPER}
    why = changed(tip, monkeypatch)
    assert why and "shared ~ tests/placement_support.py" in why


@pytest.mark.parametrize("test_text", [
    # literal targets: the shapes state tests use to load scripts and modules
    'import importlib.util\nfrom pathlib import Path\nROOT = Path(__file__).resolve().parents[1]\n'
    'SCRIPT = ROOT / "scripts" / "check_thing.py"\n'
    'spec = importlib.util.spec_from_file_location("check_thing", SCRIPT)\n',
    'import importlib.machinery\n'
    'loader = importlib.machinery.SourceFileLoader("x", str(Path(__file__).parent.parent / "home/bin/tool"))\n',
    'import runpy\nROOT = Path(__file__).parent.parent\nBIN = ROOT / "home" / "bin"\n'
    'ns = runpy.run_path(str(BIN / "token-broker-audit"))\n',
    'x = __import__("time").time()\n',
    'import sys, subprocess\nsubprocess.run([sys.executable, "-c", "import json; print(1)"])\n',
    'import sys, subprocess\nROOT = Path(__file__).parent\nTOOL = ROOT / "scripts" / "tool.py"\nsubprocess.run([sys.executable, str(TOOL), "--dry-run"])\n',
    # a loader whose target is a parameter, called only with literals (sync_test_support.require_module)
    'import importlib\ndef require_module(test, name, package=None):\n    return importlib.import_module(name)\n'
    'class T:\n    def test(self):\n        require_module(self, "yaml")\n',
    'from unittest.mock import patch\nimport importlib\n'
    'with patch.object(importlib.machinery, "SourceFileLoader", side_effect=f):\n    pass\n',
])
def test_resolved_dynamic_load_still_narrows(monkeypatch, test_text):
    tip = {"tests/__init__.py": "", SUPPORT: "VALUE = 1\n", "tests/test_x.py": test_text}
    assert changed(tip, monkeypatch) is None


def test_parameter_loader_called_with_computed_name_stays_shared(monkeypatch):
    text = ('import importlib\ndef require_module(test, name):\n    return importlib.import_module(name)\n'
            'require_module(None, "tests.placement" + "_support")\n')
    tip = {"tests/__init__.py": "", SUPPORT: "VALUE = 1\n", "tests/test_x.py": text}
    why = changed(tip, monkeypatch)
    assert why and "shared ~ tests/placement_support.py" in why


def test_unparseable_python_keeps_support_shared(monkeypatch):
    tip = {"tests/__init__.py": "", SUPPORT: "VALUE = 1\n", "tests/test_x.py": "def broken(:\n"}
    why = changed(tip, monkeypatch)
    assert why and "shared ~ tests/placement_support.py" in why


def test_state_shaped_sync_test_support_still_narrows(monkeypatch):
    # Acceptance: #3207 vs #3323 with the real dynamic shapes of tests/sync_test_support.py (origin/state 11-16):
    # a parameter-delegated import_module and a `run_module` attribute compared as a string.
    sts = ('import ast, importlib, runpy\n'
           'def bootstrap(tree):\n    entry = tree.body[-1].value\n'
           '    return entry.func.attr == "run_module"\n'
           'def require_module(test, name, package=None):\n    import importlib\n'
           '    return importlib.import_module(name)\n'
           'def fixture_git_config():\n    return {}\n')
    tip = {"tests/__init__.py": ("import sys\nfrom pathlib import Path\n"
                                 "sys.path.insert(0, str(Path(__file__).resolve().parent))\n"),
           SUPPORT: "VALUE = 1\n", "tests/sync_test_support.py": sts,
           "tests/test_hub_install_manifest.py": UNRELATED["tests/test_hub_install_manifest.py"]}
    assert changed(tip, monkeypatch, F3207, ["tests/test_hub_install_manifest.py"]) is None


# --- a test module that runs or imports another test module reaches what that module imports ------------------
RUNNER = ('import unittest\n'
          'class T(unittest.TestCase):\n'
          '    def check_fixture(self, name):\n'
          '        prefix = "tests." if __package__ == "tests" else ""\n'
          '        suite = unittest.defaultTestLoader.loadTestsFromName(prefix + name)\n'
          '    def test_one(self):\n'
          '        self.check_fixture("test_y.Case.test_z")\n')       # test_fixture_git_override_maintenance shape


@pytest.mark.parametrize("y_imports, overlaps", [("import placement_support\n", True), ("import json\n", False)])
def test_test_module_run_by_name_carries_its_imports(monkeypatch, y_imports, overlaps):
    tip = {"tests/__init__.py": "", SUPPORT: "VALUE = 1\n", "tests/test_x.py": RUNNER, "tests/test_y.py": y_imports}
    why = changed(tip, monkeypatch)
    assert bool(why and "shared ~ tests/placement_support.py" in why) is overlaps


def test_test_module_imported_by_another_carries_its_imports(monkeypatch):
    tip = {"tests/__init__.py": "", SUPPORT: "VALUE = 1\n", "tests/test_x.py": "from test_base import Case\n",
           "tests/test_base.py": "import placement_support\nclass Case: pass\n"}
    why = changed(tip, monkeypatch)
    assert why and "shared ~ tests/placement_support.py" in why


def test_import_alias_inside_exec_code_is_still_a_loader(monkeypatch):
    text = 'exec("from importlib import import_module as im\\nim(\'tests.placement_\' + MOD)")\n'
    tip = {"tests/__init__.py": "", SUPPORT: "VALUE = 1\n", "tests/test_x.py": text}
    why = changed(tip, monkeypatch)
    assert why and "shared ~ tests/placement_support.py" in why


# --- python subprocess argv: interpreter, options, then ONE target (-c code, -m module, or script); the rest are
# the program's arguments. A !r-formatted value in -c code is a literal at run time (repr), never code.
SCRIPT_DEF = ('import os, sys, subprocess\nfrom pathlib import Path\n'
              'SCRIPT = Path(__file__).parent.parent / "home" / "bin" / "tool"\n')


@pytest.mark.parametrize("call", [
    'def at_least(minimum):\n    subprocess.run([sys.executable, "-I", "-c",\n'
    '                    f"import sys; sys.exit(not (sys.version_info >= {minimum!r}))"])\n'
    'at_least(os.environ["MIN"])\n',
    'def run(*args):\n    subprocess.run([sys.executable, str(SCRIPT), *args])\nrun(*sys.argv)\n',
    'def run(extra):\n    subprocess.run([sys.executable, "-I", "-B", "-X", "utf8", str(SCRIPT), extra, "--x"])\n'
    'run(os.environ["X"])\n',
    # test_turn_urgency_observer: the code is a local built from literals and a !r-formatted runtime path
    'def reap(tmp):\n    code = ("import pathlib; "\n            f"pathlib.Path({str(tmp)!r}).write_text(\'x\')")\n'
    '    subprocess.run([sys.executable, "-c", code])\nreap(os.environ["T"])\n',
])
def test_python_subprocess_literal_target_narrows(monkeypatch, call):
    tip = {"tests/__init__.py": "", SUPPORT: "VALUE = 1\n", "tests/test_x.py": SCRIPT_DEF + call}
    assert changed(tip, monkeypatch) is None


@pytest.mark.parametrize("call", [
    'def load(mod):\n    subprocess.run([sys.executable, "-c", f"import importlib; importlib.import_module({mod!r})"])\n',
    'def run(opts):\n    subprocess.run([sys.executable, *opts, str(SCRIPT)])\n',
    'def run(name):\n    subprocess.run([sys.executable, "-c",\n'
    '                    "import importlib, sys; importlib.import_module(sys.argv[1])", name])\n',
    'def run(code):\n    subprocess.run([sys.executable, "-c", f"x = 1\\n{code}"])\n',
    'def run(opt):\n    subprocess.run([sys.executable, opt, str(SCRIPT)])\n',
    'def run(mod):\n    code = "import importlib; " + f"importlib.import_module({mod!r})"\n'
    '    subprocess.run([sys.executable, "-c", code])\n',
    'def run(stmt):\n    code = "import json; " + f"{stmt}"\n    subprocess.run([sys.executable, "-c", code])\n',
])
def test_python_subprocess_computed_target_stays_shared(monkeypatch, call):
    fn = call.split("(", 1)[0][4:]
    tip = {"tests/__init__.py": "", SUPPORT: "VALUE = 1\n",
           "tests/test_x.py": SCRIPT_DEF + call + f'{fn}(os.environ["X"])\n'}
    why = changed(tip, monkeypatch)
    assert why and "shared ~ tests/placement_support.py" in why


# --- a loader parameter with no call site in the closure ------------------------------------------------------
@pytest.mark.parametrize("text", [
    # pytest injects test function and fixture arguments: no call site names them
    'import importlib, pytest\n@pytest.mark.parametrize("mod", ["tests.placement" + "_support"])\n'
    'def test_load(mod):\n    importlib.import_module(mod)\n',
    'import importlib\ndef test_load(mod):\n    importlib.import_module(mod)\n',
    'import importlib, pytest\n@pytest.fixture\ndef loaded(request):\n    return importlib.import_module(request.param)\n',
    'import importlib\nclass Lazy:\n    def __getattr__(self, name):\n        return importlib.import_module(name)\n',
    # a bound method handed out as a callback
    'import importlib\nclass T:\n    def load(self, name):\n        return importlib.import_module(name)\n'
    '    def test(self):\n        list(map(self.load, NAMES))\n',
])
def test_framework_or_callback_supplied_loader_argument_stays_shared(monkeypatch, text):
    tip = {"tests/__init__.py": "", SUPPORT: "VALUE = 1\n", "tests/test_x.py": text}
    why = changed(tip, monkeypatch)
    assert why and "shared ~ tests/placement_support.py" in why


def test_uncalled_helper_loader_narrows(monkeypatch):
    # sync_test_support.require_module: a plain helper no closure module calls cannot load anything here.
    text = 'import importlib\ndef require_module(test, name, package=None):\n    return importlib.import_module(name)\n'
    tip = {"tests/__init__.py": "", SUPPORT: "VALUE = 1\n", "tests/test_x.py": text}
    assert changed(tip, monkeypatch) is None


def test_program_argument_naming_support_by_implicit_concatenation_drifts(monkeypatch):
    # A program argument is not a load of this closure, but a literal one still counts as naming the module:
    # adjacent literals are one constant ("placement" "_support"), so its words are the constant's.
    text = SCRIPT_DEF + 'subprocess.run([sys.executable, str(SCRIPT), "--module", "placement" "_support"])\n'
    tip = {"tests/__init__.py": "", SUPPORT: "VALUE = 1\n", "tests/test_x.py": text}
    why = changed(tip, monkeypatch)
    assert why and "shared ~ tests/placement_support.py" in why
