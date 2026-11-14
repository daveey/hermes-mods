"""Land turn for state merges (t_01a1d6b3538e8e43): ordering, never admitting. Fake gh only; never merges."""
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location("turn_guard", Path(__file__).with_name("guard.py"))
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)

R = "example/protected"
NOW = 1_794_604_000
BASE = "3" * 40
H2563 = "4559fc21fb586bf9b813010e973ba04a84eac8bf"
H2600 = "b" * 40
# #2563's files at its 4th refusal (2026-11-13 20:53 UTC).
F2563 = ["hermes-sync", "home/skills/hermes-mods-repo-sync-pitfalls/references/core-update-runtime.md",
         "tests/test_core_drain_gateway_chat_pause.py", "tests/test_core_park_ack.py"]


def pr(number, head, files):
    return {"number": number, "state": "OPEN", "baseRefName": "state", "headRefOid": head, "baseRefOid": BASE,
            "url": f"https://github.com/{R}/pull/{number}", "comments": [],
            "reviews": [{"id": f"PRR_{number}", "author": {"login": "you"}, "authorAssociation": "MEMBER",
                         "state": "COMMENTED", "submittedAt": "2026-11-13T15:00:00Z", "commit": {"oid": head},
                         "body": f"Independent exact-head review @ {head}: approve"}],
            "files": [{"path": f} for f in files],
            "statusCheckRollup": [{"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "SUCCESS"}]}


def delta(files, subject="base change"):
    return {"status": "ahead", "ahead_by": 1, "behind_by": 0, "total_commits": 1,
            "commits": [{"sha": "c" * 40, "commit": {"message": subject}}],
            "files": [{"filename": f, "status": "modified", "previous_filename": None} for f in files]}


IDENTICAL = {"status": "identical", "ahead_by": 0, "behind_by": 0, "total_commits": 0, "commits": [], "files": []}


class FakeGitHub:
    """Just enough of the REST API: PR views, the drift compare, git commits/refs and PR state."""

    def __init__(self, prs, compare=IDENTICAL, pr_state=None, refs_fail=False):
        self.prs, self.compare, self.pr_state, self.refs_fail = prs, compare, pr_state or {}, refs_fail
        self.commits = {h: {"sha": h, "tree": {"sha": "e" * 40}, "message": "x"} for h in (H2563, H2600)}
        self.refs, self.calls = {}, []

    def run(self, argv, **kw):
        self.calls.append(argv)
        ok = lambda body: SimpleNamespace(returncode=0, stdout=json.dumps(body), stderr="")
        fail = SimpleNamespace(returncode=1, stdout=json.dumps({"message": "Not Found"}), stderr="")
        if argv[:3] == ["gh", "pr", "view"]:
            return ok(self.prs[int(argv[3])])
        args = argv[2:]
        method = "GET"
        if args[0] == "-X":
            method, args = args[1], args[2:]
        path = args[0]
        body = json.loads(kw["input"]) if "--input" in args else None
        if body is not None and method == "GET":
            method = "POST"
        if "/compare/" in path:
            return ok(self.compare)
        if "/commits?" in path:
            return fail
        if path.endswith("/git/matching-refs/land-turn/pr-"):
            if self.refs_fail:
                return fail
            return ok([{"ref": r, "object": {"sha": s}} for r, s in sorted(self.refs.items())])
        if path.endswith("/git/commits") and method == "POST":
            sha = hashlib.sha1(json.dumps(body, sort_keys=True).encode()).hexdigest()
            self.commits[sha] = {"sha": sha, "tree": {"sha": body["tree"]}, "message": body["message"],
                                 "parents": body["parents"]}
            return ok(self.commits[sha])
        if "/git/commits/" in path:
            c = self.commits.get(path.rsplit("/", 1)[1])
            return ok(c) if c else fail
        if "/git/ref/" in path:
            ref = "refs/" + path.split("/git/ref/", 1)[1]
            return ok({"ref": ref, "object": {"sha": self.refs[ref]}}) if ref in self.refs else fail
        if path.endswith("/git/refs") and method == "POST":
            if body["ref"] in self.refs:
                return fail
            self.refs[body["ref"]] = body["sha"]
            return ok({"ref": body["ref"]})
        if "/git/refs/" in path:
            ref = "refs/" + path.split("/git/refs/", 1)[1]
            if method == "DELETE":
                self.refs.pop(ref, None)
                return ok({})
            if method == "PATCH":
                self.refs[ref] = body["sha"]
                return ok({"ref": ref})
        if "/pulls/" in path:
            return ok({"state": self.pr_state.get(int(path.rsplit("/", 1)[1]), "open")})
        raise AssertionError(f"unexpected gh call {argv}")

    def turn(self, number):
        sha = self.refs.get(f"refs/land-turn/pr-{number}")
        return g.parse_turn(self.commits[sha]["message"]) if sha else None


@pytest.fixture
def gh(monkeypatch):
    holder = {}

    def make(*a, **kw):
        fake = FakeGitHub(*a, **kw)
        monkeypatch.setattr(g.subprocess, "run", fake.run)
        holder["fake"] = fake
        return fake
    monkeypatch.setattr(g, "_now", lambda: NOW)
    return make


def merge(number, head):
    return g.check_argv(["gh", "pr", "merge", str(number), "--repo", R, "--rebase", "--match-head-commit", head],
                        repos={R}, env={"GH_REPO": R})


def test_2563_drift_refusal_takes_the_turn(gh):
    # 4th refusal: b36b5421a9 changed tests/state_guard_rules.py (shared ~ tests/test_core_park_ack.py).
    fake = gh({2563: pr(2563, H2563, F2563)}, compare=delta(["tests/state_guard_rules.py"]))
    why = merge(2563, H2563)
    assert why and "update-branch" in why and "now holds the state land turn" in why
    rec = fake.turn(2563)
    assert rec == {"pr": 2563, "head": H2563, "expires": NOW + g._TURN_TTL, "files": F2563}
    assert g._TURN_TTL >= 3600


def test_overlapping_merge_waits_for_the_holder(gh):
    fake = gh({2563: pr(2563, H2563, F2563), 2600: pr(2600, H2600, ["hermes-sync", "tests/test_x.py"])},
              compare=delta(["tests/state_guard_rules.py"]))
    merge(2563, H2563)
    fake.compare = IDENTICAL            # #2600's own CI base is current: only the turn holds it
    why = merge(2600, H2600)
    assert why and "PR #2563 holds the state land turn" in why and "hermes-sync ~ hermes-sync" in why
    # A PR whose files cannot drift the holder lands as before.
    fake.prs[2601] = pr(2601, H2600, ["hosts/hermes-hub/bb-tuner/x.py"])
    fake.prs[2601]["number"] = 2601
    assert merge(2601, H2600) is None
    # The holder itself is never held by its own turn; with a fresh base it merges.
    assert merge(2563, H2563) is None


def test_turn_covers_ci_files_too(gh):
    fake = gh({2563: pr(2563, H2563, F2563), 2600: pr(2600, H2600, [".github/workflows/x.yml"])},
              compare=delta(["hermes-sync"]))
    merge(2563, H2563)
    fake.compare = IDENTICAL
    assert "holds the state land turn" in merge(2600, H2600)


def test_merged_holder_releases_and_cleans_up(gh):
    fake = gh({2563: pr(2563, H2563, F2563), 2600: pr(2600, H2600, ["hermes-sync"])},
              compare=delta(["hermes-sync"]))
    merge(2563, H2563)
    fake.compare, fake.pr_state[2563] = IDENTICAL, "closed"
    assert merge(2600, H2600) is None
    # Merge preflight is read-only while approval runs; non-speculative readers
    # retain cleanup, rather than deleting a ref observed before approval.
    assert "refs/land-turn/pr-2563" in fake.refs
    assert g._turn_hold_refusal("2600", pr(2600, H2600, ["hermes-sync"]), R, None, {}) is None
    assert "refs/land-turn/pr-2563" not in fake.refs


def test_expired_turn_is_ignored(gh, monkeypatch):
    fake = gh({2563: pr(2563, H2563, F2563), 2600: pr(2600, H2600, ["hermes-sync"])},
              compare=delta(["hermes-sync"]))
    merge(2563, H2563)
    fake.compare = IDENTICAL
    monkeypatch.setattr(g, "_now", lambda: NOW + g._TURN_TTL + 1)
    assert merge(2600, H2600) is None


def test_unreadable_turns_fail_open(gh):
    # The turn is liveness only: a listing failure never refuses (the drift compare still guards).
    gh({2600: pr(2600, H2600, ["hermes-sync"])}, refs_fail=True)
    assert merge(2600, H2600) is None


def test_live_turn_is_not_extended(gh, monkeypatch):
    fake = gh({2563: pr(2563, H2563, F2563)}, compare=delta(["hermes-sync"]))
    merge(2563, H2563)
    first = fake.refs["refs/land-turn/pr-2563"]
    monkeypatch.setattr(g, "_now", lambda: NOW + 600)
    why = merge(2563, H2563)
    assert fake.refs["refs/land-turn/pr-2563"] == first and "now holds the state land turn" in why
    # After expiry the PR cools down for another TTL (others in its area get a window) ...
    monkeypatch.setattr(g, "_now", lambda: NOW + g._TURN_TTL + 5)
    why = merge(2563, H2563)
    assert fake.refs["refs/land-turn/pr-2563"] == first and "now holds" not in why
    # ... then a new refusal records a new turn (PATCH of the existing ref).
    monkeypatch.setattr(g, "_now", lambda: NOW + 2 * g._TURN_TTL + 5)
    merge(2563, H2563)
    assert fake.turn(2563)["expires"] == NOW + 3 * g._TURN_TTL + 5


def test_readers_delete_cooled_and_junk_turns(gh, monkeypatch):
    fake = gh({2563: pr(2563, H2563, F2563), 2600: pr(2600, H2600, ["hosts/x/y.py"])},
              compare=delta(["hermes-sync"]))
    merge(2563, H2563)
    fake.refs["refs/land-turn/pr-7"] = H2600          # junk: an ordinary commit, not a turn record
    fake.compare = IDENTICAL
    monkeypatch.setattr(g, "_now", lambda: NOW + g._TURN_TTL + 5)   # cooling: kept
    assert merge(2600, H2600) is None
    assert "refs/land-turn/pr-7" in fake.refs
    g._live_turns(R, None, {})
    assert set(fake.refs) == {"refs/land-turn/pr-2563"}
    monkeypatch.setattr(g, "_now", lambda: NOW + 2 * g._TURN_TTL + 5)
    assert merge(2600, H2600) is None
    g._live_turns(R, None, {})
    assert not fake.refs


def test_newest_turns_read_first_and_bounded(gh):
    fake = gh({2600: pr(2600, H2600, ["hermes-sync"])})
    for n in range(1, 20):
        fake.refs[f"refs/land-turn/pr-{n}"] = H2600      # junk entries, each costs a read
    msg = g._TURN_SUBJECT + "2599\n\n" + json.dumps({"pr": 2599, "head": H2563, "expires": NOW + 60,
                                                      "files": ["hermes-sync"]})
    fake.commits["d" * 40] = {"sha": "d" * 40, "tree": {"sha": "e" * 40}, "message": msg}
    fake.refs["refs/land-turn/pr-2599"] = "d" * 40
    assert "PR #2599 holds the state land turn" in merge(2600, H2600)
    reads = [c for c in fake.calls if "/git/commits/" in c[-1]]
    assert len(reads) <= g._TURN_MAX


def test_turn_errors_add_nothing(gh, monkeypatch):
    gh({2600: pr(2600, H2600, ["hermes-sync"])})

    def boom(*a):
        raise OverflowError("x")
    monkeypatch.setattr(g, "_live_turns", boom)
    assert merge(2600, H2600) is None


def test_other_refusals_take_no_turn(gh):
    data = pr(2563, H2563, F2563)
    data["statusCheckRollup"][0]["conclusion"] = "FAILURE"
    fake = gh({2563: data}, compare=delta(["hermes-sync"]))
    assert "not green" in merge(2563, H2563)
    assert not fake.refs


@pytest.mark.parametrize("msg", [
    "", "other subject\n\n{\"pr\": 1, \"expires\": 1, \"files\": [\"a\"]}",
    "land-turn pr=1\n\n{\"pr\": true, \"expires\": 1, \"files\": [\"a\"]}",
    "land-turn pr=1\n\n{\"pr\": 1, \"expires\": \"x\", \"files\": [\"a\"]}",
    "land-turn pr=1\n\n{\"pr\": 1, \"expires\": Infinity, \"files\": [\"a\"]}",
    "land-turn pr=1\n\n{\"pr\": 1, \"expires\": NaN, \"files\": [\"a\"]}",
    "land-turn pr=1\n\n{\"pr\": 1, \"expires\": 1, \"files\": [\"\"]}", "land-turn pr=1\n\n{not json"])
def test_parse_turn_rejects_junk(msg):
    assert g.parse_turn(msg) is None


@pytest.mark.parametrize("pr_file,base_file,held", [
    # Skill prose against skill prose never refuses (#2563 vs sync 5fbd15ce4f, same reference file) ...
    ("home/skills/a/references/x.md", "home/skills/a/references/x.md", False),
    ("home/skills/a/references/x.md", "home/skills/a/SKILL.md", False),
    ("skills/devops/a/SKILL.md", "skills/devops/a/references/y.md", False),
    # ... but prose beside code, non-skill markdown and CI files still do.
    ("home/skills/a/scripts/test_ex.py", "home/skills/a/SKILL.md", True),
    ("home/skills/a/SKILL.md", "home/skills/a/scripts/tool.py", True),
    ("hosts/hermes-hub/x/README.md", "hosts/hermes-hub/x/notes.md", True),
    ("home/skills/a/SKILL.md", ".github/workflows/ci.yml", True),
])
def test_skill_prose_rule(pr_file, base_file, held):
    assert bool(g._overlap(pr_file, base_file)) is held
