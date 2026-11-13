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
REAL_OVERLAP_CHECKER = plugin._overlap_checker


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


# ---- metered transport: the hook reads GitHub only through `gh api` (quota ledger), never urllib + raw token ------

FAKE_GH = r'''#!{python}
"""Fake gh: logs argv, answers `gh api --include <path>` from a JSON spec like the GitHub REST API."""
import json, os, sys
from urllib.parse import urlparse, parse_qs
spec = json.load(open(os.environ["FAKE_GH_SPEC"]))
with open(os.environ["FAKE_GH_LOG"], "a") as log:
    log.write(json.dumps({{"argv": sys.argv[1:], "token": bool(os.environ.get("GH_TOKEN"))}}) + "\n")
if spec.get("exit"):
    sys.stderr.write("GitHub quota guard: refused.\n")
    sys.exit(spec["exit"])
path = sys.argv[-1]
u = urlparse(path)
parts = u.path.strip("/").split("/")
q = parse_qs(u.query)
link = ""
status = spec.get("status", 200)
if parts[-1] == "pulls":   # repos/o/r/pulls, or a next-page repositories/<id>/pulls
    rows = [{{"number": p["number"], "head": {{"sha": p["sha"]}}, "html_url": "https://github.com/" + parts[1] + "/"
             + parts[2] + "/pull/" + str(p["number"]), "title": "PR " + str(p["number"]),
             "base": {{"repo": {{"full_name": spec.get("canonical") or parts[1] + "/" + parts[2]}}}}}}
            for p in spec["prs"] if p.get("base", "state") == q["base"][0]]
    page = int(q.get("page", ["1"])[0])
    rows = rows[(page - 1) * 2: page * 2]   # two per page so pagination is exercised
    if len(spec["prs"]) > page * 2:
        link = '<https://api.github.com/repositories/1/pulls?state=open&base=' + q["base"][0] + '&per_page=100&page=' \
               + str(page + 1) + '>; rel="next", <https://api.github.com/x?page=9>; rel="last"'
else:                 # repos/o/r/pulls/<n>/files
    rows = [{{"filename": f}} for p in spec["prs"] if p["number"] == int(parts[4]) for f in p["files"]]
body = json.dumps(rows if status < 400 else {{"message": "Not Found"}})
sys.stdout.write("HTTP/1.0 %d X\nContent-Type: application/json\r\n" % status
                 + ("Link: " + link + "\r\n" if link else "") + "X-Ratelimit-Resource: core\r\n\r\n" + body)
sys.exit(1 if status >= 400 else 0)
'''


@pytest.fixture
def fake_gh(tmp_path, monkeypatch):
    import json
    import urllib.request
    gh = tmp_path / "bin" / "gh"
    gh.parent.mkdir()
    gh.write_text(FAKE_GH.format(python=sys.executable))
    gh.chmod(0o755)
    spec, log = tmp_path / "gh-spec.json", tmp_path / "gh-log.jsonl"
    monkeypatch.setenv("FAKE_GH_SPEC", str(spec))
    monkeypatch.setenv("FAKE_GH_LOG", str(log))
    monkeypatch.setenv("PR_OVERLAP_GH", str(gh))
    monkeypatch.setenv("PR_OVERLAP_CACHE", str(tmp_path / "gh-cache"))
    for var in ("GH_TOKEN", "GITHUB_TOKEN"):
        monkeypatch.delenv(var, raising=False)

    def no_urllib(*a, **k):
        raise AssertionError("pr_overlap read GitHub over urllib (unmetered) instead of gh")
    monkeypatch.setattr(urllib.request, "urlopen", no_urllib)

    class F:
        def set(self, prs, **extra):
            spec.write_text(json.dumps(dict(prs=prs, **extra)))

        def calls(self):
            return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    return F()


def test_hook_lookup_goes_through_metered_gh(env, fake_gh, monkeypatch):
    """The real hook checker (no injected fetch) asks gh for every read: list pages, then each PR's files (from the
    PR's canonical base repo, so a moved repo's 301 does not double each read)."""
    monkeypatch.setattr(plugin, "_overlap_checker", REAL_OVERLAP_CHECKER)
    fake_gh.set([{"number": 21, "sha": "a", "files": ["x.py"]}, {"number": 22, "sha": "b", "files": ["y.py"]},
                 {"number": 23, "sha": "c", "files": [GUIDE]}], canonical="acme/moved-state")
    r = call(patch_op("launch-hub-worker", "references/static-launch-recipe.md"))
    assert r and r["action"] == "block" and "#23" in r["message"]
    calls = fake_gh.calls()
    paths = sorted(c["argv"][-1] for c in calls)
    assert paths == ["repos/acme/moved-state/pulls/21/files?per_page=100",
                     "repos/acme/moved-state/pulls/22/files?per_page=100",
                     "repos/acme/moved-state/pulls/23/files?per_page=100",
                     "repos/acme/private-state/pulls?state=open&base=state&per_page=100",
                     "repositories/1/pulls?state=open&base=state&per_page=100&page=2"]
    assert all(c["argv"][:2] == ["api", "--include"] for c in calls)   # never `gh auth token`
    assert not any(c["token"] for c in calls)                          # no raw token handed to gh
    assert env.gh.calls == 0                                           # the injected fake was not used


def test_gh_failures_are_unknown_and_never_block(env, fake_gh, monkeypatch):
    monkeypatch.setattr(plugin, "_overlap_checker", REAL_OVERLAP_CHECKER)
    mod = plugin_overlap()
    prs = [{"number": 31, "sha": "a", "files": [GUIDE]}]
    for extra, reason in (({"exit": 75}, "quota guard refused"), ({"status": 404}, "GitHub HTTP 404 for repos/")):
        fake_gh.set(prs, **extra)
        res = mod.Checker(fetch=mod.gh_fetch, cache_dir=env.cache, list_ttl=0).check("acme/private-state", "state",
                                                                                      [GUIDE])
        assert res["status"] == "unknown" and reason in res["reason"], res
        assert not call(patch_op("launch-hub-worker", "references/static-launch-recipe.md"))
    monkeypatch.setenv("PR_OVERLAP_GH", str(env.cache / "no-such-gh"))
    res = mod.Checker(fetch=mod.gh_fetch, cache_dir=env.cache, list_ttl=0).check("acme/private-state", "state", [GUIDE])
    assert res["status"] == "unknown" and "gh CLI not found" in res["reason"]


def test_first_failure_stops_further_file_reads(env):
    """Concurrent file reads keep the bounds: after one read fails, no further request starts."""
    mod = plugin_overlap()
    env.gh.prs[("acme/private-state", "state")] = [{"number": n, "sha": "s", "files": ["f"]} for n in range(1, 41)]
    real = env.gh.__call__

    def flaky(url, token, timeout):
        if "/files" in url:
            env.gh.calls += 1
            raise OSError("GitHub HTTP 502 for " + url.split("?")[0])
        return real(url, token, timeout)
    c = mod.Checker(fetch=flaky, token="t", cache_dir=env.cache, list_ttl=0, workers=4)
    res = c.check("acme/private-state", "state", [GUIDE])
    assert res["status"] == "unknown" and "502" in res["reason"]
    assert env.gh.calls <= 1 + 4   # the list read, then at most one in-flight read per worker
