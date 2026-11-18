"""Waiting priority across real Git refreshes; only GitHub transport is faked."""
import json

import pytest

import tests_carry as c
import tests_turn as f
from tests_carry import real_merges  # noqa: F401

g = c.g


def setup(monkeypatch, tmp_path, real_merges):
    h0, heads, answers = real_merges
    c.fake(monkeypatch, answers=answers)
    carry_api = g.subprocess.run
    fake = f.FakeGitHub({n: f.pr(n, h0, ["build.py", "shared.txt"]) for n in (1, 2, 3)})
    tree = c.REAL_RUN(["git", "rev-parse", h0 + "^{tree}"], cwd=c.REAL_REPO["path"],
                      capture_output=True, text=True, check=True).stdout.strip()
    fake.commits[h0] = {"sha": h0, "tree": {"sha": tree}, "message": "approved patch"}
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if argv[0] == "git":
            argv = [str(c.REAL_REPO["path"]) if a == f"https://github.com/{c.R}.git" else a for a in argv]
            return c.REAL_RUN(argv, **kwargs)
        if any("/compare/" in a or "/git/trees/" in a or "/git/blobs/" in a for a in argv):
            return carry_api(argv, **kwargs)
        return fake.run(argv, **kwargs)

    monkeypatch.setattr(g.subprocess, "run", run)
    monkeypatch.setattr(f, "g", g)
    monkeypatch.setattr(g, "_now", lambda: f.NOW)
    monkeypatch.setattr(g, "_turn_owner", lambda env: {})
    monkeypatch.setattr(g, "_WAITING_WRITER_ENABLED", True)
    # Exercise real fetch, object reads, patch-id, merge-file and merge-tree.
    monkeypatch.setattr(g, "_git_merge_tree_refusal", c.GIT_VERIFY)
    monkeypatch.setattr(g._CarryObjects, "_fetch", c.REAL_FETCH)
    return fake, calls, {"HERMES_HOME": str(tmp_path / "home"), "GH_REPO": c.R}


@pytest.mark.parametrize("refresh", ["clean", "relocated", "edited"])
def test_waiting_real_refresh_preserves_original_generation(monkeypatch, tmp_path, real_merges, refresh):
    h0, heads, _ = real_merges
    fake, calls, env = setup(monkeypatch, tmp_path, real_merges)
    f.seed_turn(fake, 1, f.NOW - 60, ["build.py"])
    # The holder's record must describe its actual head.
    holder = fake.turn(1)
    holder["head"] = h0
    fake.commits[fake.refs["refs/land-turn/pr-1"]]["message"] = "land-turn pr=1\n\n" + json.dumps(holder)
    status, message = g.land_turn(2, c.R, h0, env=env)
    assert status == 1 and "waiting place recorded" in message, message
    original = dict(fake.turn(2))
    oid = fake.refs["refs/land-turn/pr-2"]
    assert original["head"] == h0 and original["files"] == ["build.py", "shared.txt"]
    fake.pr_state[1] = "closed"
    fake.prs[2]["headRefOid"] = heads[refresh]
    # No new review for the clean merge; for rewrites even a new approval cannot
    # acquire the original patch's older place.
    if refresh != "clean":
        fake.prs[2]["reviews"] = f.pr(2, heads[refresh], ["build.py"])["reviews"]
    with g._scope():
        yielded = g._turn_yielded(original, fake.prs[2], c.R, None, env)
    assert yielded is (refresh != "clean")
    if refresh == "clean":
        result = g.bounded_preflight(g.check_argv,
            ["gh", "pr", "merge", "2", "--repo", c.R, "--rebase", "--match-head-commit", heads[refresh]],
            repos={c.R}, env=env)
        assert result is None, result
        assert len(fake.prs[2]["reviews"]) == 1
        assert fake.prs[2]["reviews"][0]["commit"]["oid"] == h0
        # A newcomer cannot pass this refreshed waiting place, even though its
        # bounded peer read cannot independently carry the historical approval.
        why = f.merge(3, h0)
        assert "PR #2 may still hold" in why, why
        assert any("merge-tree" in argv for argv in calls)
        assert any("patch-id" in argv for argv in calls)
    assert fake.turn(2) == original
    assert fake.refs["refs/land-turn/pr-2"] == oid
    assert not any("DELETE" in argv for argv in calls)
