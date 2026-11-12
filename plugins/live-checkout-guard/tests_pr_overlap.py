"""Open-PR overlap guard for shared skill_manage edits. Run: python3 -m pytest -q -p no:cacheprovider tests_pr_overlap.py

A chat edit to a shared guide that an open PR also changes forced that PR to reconcile and get another exact-head
review (self-heal agent-a138244f3c). These tests drive the real pre_tool_call hook with a fake GitHub API.
"""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import pytest

HERE = Path(__file__).resolve().parent
ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
       "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


plugin = _load("live_checkout_guard_plugin_overlap", "__init__.py")


def git(*a, cwd):
    subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, env=ENV)


def clone(tmp, name, url, branch):
    d = tmp / name
    d.mkdir()
    git("init", "-q", "-b", branch, cwd=d)
    skill = d / "home/skills/devops/launch-hub-worker"
    (skill / "references").mkdir(parents=True)
    (skill / "SKILL.md").write_text("skill\n")
    (skill / "references/static-launch-recipe.md").write_text("recipe\n")
    other = d / "home/skills/devops/other-skill"
    other.mkdir(parents=True)
    (other / "SKILL.md").write_text("other\n")
    git("add", ".", cwd=d)
    git("commit", "-qm", "init", cwd=d)
    git("remote", "add", "origin", url, cwd=d)
    git("update-ref", f"refs/remotes/origin/{branch}", "HEAD", cwd=d)
    git("branch", "-u", f"origin/{branch}", cwd=d)
    return d


class FakeGitHub:
    """open PRs: {(slug, base): [{number, sha, files}]}; ``fail``/``endless`` simulate lookup problems."""

    def __init__(self):
        self.prs = {}
        self.fail = False
        self.endless = False
        self.calls = 0

    def __call__(self, url, token, timeout):
        self.calls += 1
        if self.fail:
            raise OSError("GitHub HTTP 503 for " + url.split("?")[0])
        u = urlparse(url)
        parts = u.path.strip("/").split("/")   # repos/o/r/pulls[/n/files]
        slug = f"{parts[1]}/{parts[2]}"
        q = parse_qs(u.query)
        if len(parts) == 4:
            if self.endless:
                return [], url + "&page=2"
            base = q["base"][0]
            rows = [{"number": p["number"], "head": {"sha": p["sha"]}, "html_url": f"https://github.com/{slug}/pull/{p['number']}",
                     "title": f"PR {p['number']}"} for p in self.prs.get((slug, base), [])]
            return rows, None
        n = int(parts[4])
        for (s, _b), prs in self.prs.items():
            for p in prs:
                if s == slug and p["number"] == n:
                    return [{"filename": f} for f in p["files"]], None
        return [], None


def plugin_overlap():
    return _load("pr_overlap_unit", "pr_overlap.py")


@pytest.fixture
def env(tmp_path, monkeypatch):
    state = clone(tmp_path, "state", "https://github.com/acme/private-state.git", "state")
    mods = clone(tmp_path, "mods", "git@github.com:acme/public-mods.git", "main")
    skills = {"launch-hub-worker": state / "home/skills/devops/launch-hub-worker",
              "other-skill": state / "home/skills/devops/other-skill",
              "mods-skill": mods / "home/skills/devops/launch-hub-worker"}
    gh = FakeGitHub()
    monkeypatch.setenv("LIVE_CHECKOUT_ROOT", str(tmp_path / "no-live-root"))
    monkeypatch.delenv("LIVE_CHECKOUT_GUARD", raising=False)
    monkeypatch.delenv("SKILL_PR_OVERLAP_GUARD", raising=False)
    # raising=False: on code without the guard these attributes are absent and the block tests fail (RED).
    monkeypatch.setattr(plugin, "_skill_finder", raising=False,
                        value=lambda: (lambda n: {"path": skills[n]} if n in skills else None))
    cache = tmp_path / "cache"
    monkeypatch.setattr(plugin, "_overlap_checker", raising=False,
                        value=lambda mod: mod.Checker(fetch=gh, token="t", cache_dir=cache, list_ttl=0))
    return type("E", (), dict(state=state, mods=mods, gh=gh, cache=cache))


GUIDE = "home/skills/devops/launch-hub-worker/references/static-launch-recipe.md"


def patch_op(name, file_path=None):
    op = {"action": "patch", "name": name, "old_string": "a", "new_string": "b"}
    if file_path:
        op["file_path"] = file_path
    return op


def call(*ops, flat=False):
    args = dict(ops[0]) if flat else {"operations": list(ops)}
    return plugin._pre_tool_call("skill_manage", args)


def test_overlapping_reference_blocked_names_pr(env):
    env.gh.prs[("acme/private-state", "state")] = [{"number": 1724, "sha": "a1", "files": [GUIDE, "x.py"]}]
    r = call(patch_op("launch-hub-worker", "references/static-launch-recipe.md"))
    assert r and r["action"] == "block"
    assert "#1724" in r["message"] and GUIDE in r["message"] and "owner" in r["message"]


def test_overlapping_skill_md_flat_args_blocked(env):
    env.gh.prs[("acme/private-state", "state")] = [
        {"number": 7, "sha": "a", "files": ["home/skills/devops/launch-hub-worker/SKILL.md"]}]
    assert call(patch_op("launch-hub-worker"), flat=True)["action"] == "block"


def test_same_repo_different_file_allowed(env):
    env.gh.prs[("acme/private-state", "state")] = [{"number": 1, "sha": "a", "files": [GUIDE]}]
    assert not call(patch_op("other-skill"))
    assert not call(patch_op("launch-hub-worker"))   # SKILL.md is not in the PR


def test_other_repository_or_base_allowed(env):
    env.gh.prs[("acme/public-mods", "state")] = [{"number": 2, "sha": "a", "files": [GUIDE]}]   # wrong base
    env.gh.prs[("acme/other", "state")] = [{"number": 3, "sha": "a", "files": [GUIDE]}]        # wrong repo
    assert not call(patch_op("launch-hub-worker", "references/static-launch-recipe.md"))
    assert not call(patch_op("mods-skill", "references/static-launch-recipe.md"))
    env.gh.prs[("acme/public-mods", "main")] = [{"number": 4, "sha": "a", "files": [GUIDE]}]
    r = call(patch_op("mods-skill", "references/static-launch-recipe.md"))
    assert r and "#4" in r["message"] and "acme/public-mods@main" in r["message"]


def test_batch_with_one_overlapping_op_blocked(env):
    env.gh.prs[("acme/private-state", "state")] = [{"number": 9, "sha": "a", "files": [GUIDE]}]
    r = call(patch_op("other-skill"), {"action": "write_file", "name": "launch-hub-worker",
                                       "file_path": "references/static-launch-recipe.md", "file_content": "x"})
    assert r and "#9" in r["message"]


def test_delete_skill_blocked_when_pr_touches_any_file(env):
    env.gh.prs[("acme/private-state", "state")] = [{"number": 5, "sha": "a", "files": [GUIDE]}]
    assert call({"action": "delete", "name": "launch-hub-worker"})["action"] == "block"


def test_create_and_unknown_skill_allowed(env):
    env.gh.prs[("acme/private-state", "state")] = [{"number": 5, "sha": "a", "files": [GUIDE]}]
    assert not call({"action": "create", "name": "brand-new", "content": "x"})
    assert not call(patch_op("missing"))
    assert env.gh.calls == 0   # nothing shared to check: no lookup


def test_lookup_failure_does_not_block_edit(env):
    """Unknown is not 'no overlap' for publication (skills_sync holds it); the edit itself stays possible."""
    env.gh.fail = True
    assert not call(patch_op("launch-hub-worker", "references/static-launch-recipe.md"))


def test_incomplete_pagination_is_unknown_not_clear(env):
    env.gh.endless = True
    mod = plugin_overlap()
    res = mod.Checker(fetch=env.gh, token="t", cache_dir=env.cache, list_ttl=0).check("acme/private-state", "state", [GUIDE])
    assert res["status"] == "unknown" and "pagination" in res["reason"]


def test_pr_closing_clears_block(env):
    env.gh.prs[("acme/private-state", "state")] = [{"number": 11, "sha": "a", "files": [GUIDE]}]
    assert call(patch_op("launch-hub-worker", "references/static-launch-recipe.md"))
    env.gh.prs[("acme/private-state", "state")] = []
    assert not call(patch_op("launch-hub-worker", "references/static-launch-recipe.md"))


def test_files_cached_by_head_sha_and_list_ttl(env, monkeypatch):
    mod = plugin_overlap()
    env.gh.prs[("acme/private-state", "state")] = [{"number": 12, "sha": "a", "files": [GUIDE]}]
    c = mod.Checker(fetch=env.gh, token="t", cache_dir=env.cache, list_ttl=600)
    assert c.check("acme/private-state", "state", [GUIDE])["status"] == "overlap"
    before = env.gh.calls
    c2 = mod.Checker(fetch=env.gh, token="t", cache_dir=env.cache, list_ttl=600)
    assert c2.check("acme/private-state", "state", [GUIDE])["status"] == "overlap"
    assert env.gh.calls == before   # list within TTL, files by sha: no requests


def test_kill_switch(env, monkeypatch):
    env.gh.prs[("acme/private-state", "state")] = [{"number": 13, "sha": "a", "files": [GUIDE]}]
    monkeypatch.setenv("SKILL_PR_OVERLAP_GUARD", "0")
    assert not call(patch_op("launch-hub-worker", "references/static-launch-recipe.md"))


def test_cli_exit_codes(env, monkeypatch, tmp_path):
    """The publisher contract: 0 clear, 10 overlap (HOLD lines), 11 unknown."""
    import contextlib
    import io
    mod = _load("pr_overlap_cli", "pr_overlap.py")
    real = mod.Checker
    env.gh.prs[("acme/private-state", "state")] = [{"number": 14, "sha": "a", "files": [GUIDE]}]
    monkeypatch.setattr(mod, "Checker", lambda deadline: real(fetch=env.gh, token="t", cache_dir=env.cache,
                                                              list_ttl=0))

    class Stdin:
        def __init__(self, data):
            self.buffer = io.BytesIO(data)

    def run(paths):
        monkeypatch.setattr(sys, "stdin", Stdin("\0".join(paths).encode()))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = mod.main(["check", "--repo", "acme/private-state", "--base", "state"])
        return code, out.getvalue()

    code, out = run([GUIDE, "home/skills/devops/other-skill/SKILL.md"])
    assert code == 10 and out == f"HOLD\t{GUIDE}\t#14\thttps://github.com/acme/private-state/pull/14\n"
    assert run(["home/skills/devops/other-skill/SKILL.md"]) == (0, "")
    env.gh.fail = True
    code, out = run([GUIDE])
    assert code == 11 and out.startswith("UNKNOWN\tGitHub HTTP 503")


def test_repo_slug_forms():
    mod = _load("pr_overlap_slug", "pr_overlap.py")
    assert mod.repo_slug("https://github.com/softmax-universe/daveey-hermes.git") == "softmax-universe/daveey-hermes"
    assert mod.repo_slug("git@github.com:daveey/hermes-mods.git") == "daveey/hermes-mods"
    assert mod.repo_slug("ssh://git@github.com/o/r") == "o/r"
    assert mod.repo_slug("/tmp/remote.git") is None
