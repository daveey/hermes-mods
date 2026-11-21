"""Tests for push-guard. Run: python3 -m pytest -q -p no:cacheprovider tests_guard.py"""

import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


plugin = _load("push_guard_plugin", "__init__.py")
guard = plugin.guard
GUARDS = [{"remote": "owner/repo", "branch": "main", "allow": ["docs/*", "*.md"], "use": "land <branch> --wait"}]


def git(*a, cwd):
    subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"})


@pytest.fixture
def repo(tmp_path, monkeypatch):
    bare = tmp_path / "owner" / "repo.git"
    bare.mkdir(parents=True)
    git("init", "-q", "--bare", "-b", "main", cwd=bare)
    work = tmp_path / "work"
    work.mkdir()
    git("init", "-q", "-b", "main", cwd=work)
    git("remote", "add", "origin", str(bare), cwd=work)
    (work / "a.py").write_text("x")
    git("add", ".", cwd=work)
    git("commit", "-qm", "base", cwd=work)
    git("push", "-q", "origin", "main", cwd=work)
    git("checkout", "-q", "-b", "feat", cwd=work)
    cfg = tmp_path / "push-guards.json"
    cfg.write_text(json.dumps({"guards": GUARDS}))
    monkeypatch.setenv("PUSH_GUARD_CONFIG", str(cfg))
    return work


def change(work, name):
    p = work / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("y")
    git("add", ".", cwd=work)
    git("commit", "-qm", name, cwd=work)


def block(tool, args):
    return plugin._pre_tool_call(tool_name=tool, args=args)


def test_code_push_to_main_is_blocked_with_the_land_command(repo):
    change(repo, "hub/x.py")
    got = block("terminal", {"command": "git push origin HEAD:main", "workdir": str(repo)})
    assert got and got["action"] == "block"
    assert "land <branch> --wait" in got["message"] and "hub/x.py" in got["message"]


def test_docs_only_push_to_main_is_allowed(repo):
    change(repo, "docs/plans/x.md")
    change(repo, "README.md")
    assert block("terminal", {"command": "git push origin HEAD:main", "workdir": str(repo)}) is None


def test_push_to_a_feature_branch_is_allowed(repo):
    change(repo, "hub/x.py")
    assert block("terminal", {"command": "git push -u origin feat", "workdir": str(repo)}) is None
    assert block("terminal", {"command": "git push --force-with-lease origin HEAD:feat", "workdir": str(repo)}) is None


def test_cd_and_dash_C_and_refs_heads_forms_are_seen(repo, tmp_path):
    change(repo, "hub/x.py")
    assert block("terminal", {"command": f"cd {repo} && git fetch && git push origin HEAD:refs/heads/main",
                              "workdir": str(tmp_path)})
    assert block("terminal", {"command": f"git -C {repo} push origin feat:main", "workdir": str(tmp_path)})


def test_pushing_local_main_by_name_or_bare_push_on_main_is_blocked(repo):
    git("checkout", "-q", "main", cwd=repo)
    git("branch", "--set-upstream-to=origin/main", cwd=repo)
    change(repo, "hub/y.py")
    assert block("terminal", {"command": "git push origin main", "workdir": str(repo)})
    assert block("terminal", {"command": "git push", "workdir": str(repo)})


def test_other_remotes_are_not_guarded(repo, tmp_path):
    other = tmp_path / "else" / "thing.git"
    other.mkdir(parents=True)
    git("init", "-q", "--bare", "-b", "main", cwd=other)
    git("remote", "add", "two", str(other), cwd=repo)
    change(repo, "hub/x.py")
    assert block("terminal", {"command": "git push two HEAD:main", "workdir": str(repo)}) is None


def test_override_skips_the_check(repo):
    change(repo, "hub/x.py")
    assert block("terminal", {"command": "PUSH_GUARD=off git push origin HEAD:main", "workdir": str(repo)}) is None


def test_execute_code_subprocess_strings_and_argv_lists(repo):
    change(repo, "hub/x.py")
    code = f'import subprocess\nsubprocess.run("git -C {repo} push origin HEAD:main", shell=True)\n'
    assert block("execute_code", {"code": code})
    code = f'subprocess.run(["git", "-C", "{repo}", "push", "origin", "HEAD:main"])\n'
    assert block("execute_code", {"code": code})


def test_no_config_is_a_no_op(repo, monkeypatch, tmp_path):
    monkeypatch.setenv("PUSH_GUARD_CONFIG", str(tmp_path / "missing.json"))
    change(repo, "hub/x.py")
    assert block("terminal", {"command": "git push origin HEAD:main", "workdir": str(repo)}) is None


def test_nested_tool_call_is_checked(repo):
    change(repo, "hub/x.py")
    args = {"calls": [{"name": "terminal", "arguments": {"command": "git push origin HEAD:main", "workdir": str(repo)}}]}
    assert block("mcp__tool_call", args)


# --- require_check guards (exact-sha CI gate) + gh pr merge --admin -------------------------------------------

CHECK = "All required checks pass (v2)"
MIXED = GUARDS + [{"remote": "acme/core.git", "branch": "main", "require_check": CHECK,
                   "use": "open a PR, wait for the check, then `gh pr merge <n>`"}]


class FakeGH:
    """Stands in for guard._gh_json: check-runs per sha, PRs per selector, and a log of every call."""

    def __init__(self):
        self.runs, self.prs, self.calls, self.fail, self.compares = {}, {}, [], False, {}

    def __call__(self, args, cwd=None):
        self.calls.append(list(args))
        if self.fail:
            return None, "HTTP 401: Bad credentials"
        if args[0] == "api":
            if "/compare/" in args[1]:  # landing-overlap read: by default the head already contains main
                return self.compares.get(args[1].split("/compare/")[1], {
                    "merge_base_commit": {"sha": "f" * 40}, "base_commit": {"sha": "f" * 40}, "behind_by": 0,
                    "files": []}), None
            sha = args[1].split("/commits/")[1].split("/")[0]
            return {"total_count": len(self.runs.get(sha, [])), "check_runs": self.runs.get(sha, [])}, None
        if args[:2] == ["pr", "view"]:
            pr = self.prs.get(args[2] if not args[2].startswith("-") else "")
            return (pr, None) if pr else (None, "no pull requests found")
        return None, "unexpected"


def run(status, conclusion=None, rid=1, started="2026-10-30T10:00:00Z", name=CHECK):
    return {"id": rid, "name": name, "status": status, "conclusion": conclusion, "started_at": started}


@pytest.fixture
def core(tmp_path, monkeypatch):
    """A clone of acme/core (require_check guard) with a new commit on `feat`, plus the merge-train guard config."""
    bare = tmp_path / "acme" / "core.git"
    bare.mkdir(parents=True)
    git("init", "-q", "--bare", "-b", "main", cwd=bare)
    work = tmp_path / "core"
    work.mkdir()
    git("init", "-q", "-b", "main", cwd=work)
    git("remote", "add", "origin", str(bare), cwd=work)
    (work / "a.py").write_text("x")
    git("add", ".", cwd=work)
    git("commit", "-qm", "base", cwd=work)
    git("push", "-q", "origin", "main", cwd=work)
    git("fetch", "-q", "origin", cwd=work)
    git("checkout", "-q", "-b", "feat", cwd=work)
    change(work, "docs/only.md")  # docs-only: the check guard must still require CI (allow is ignored there)
    cfg = tmp_path / "push-guards.json"
    cfg.write_text(json.dumps({"guards": MIXED}))
    monkeypatch.setenv("PUSH_GUARD_CONFIG", str(cfg))
    fake = FakeGH()
    monkeypatch.setattr(guard, "_gh_json", fake)
    return work, fake


def head(work):
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=work, capture_output=True, text=True).stdout.strip()


def test_push_of_a_sha_whose_required_check_is_in_progress_is_blocked(core):
    work, gh = core
    sha = head(work)
    gh.runs[sha] = [run("in_progress")]
    got = block("terminal", {"command": f"git push origin {sha}:main", "workdir": str(work)})
    assert got and got["action"] == "block"
    assert sha[:12] in got["message"] and "in_progress" in got["message"] and CHECK in got["message"]
    assert "gh pr merge <n>" in got["message"]  # the landing path
    assert gh.calls and gh.calls[0][1].startswith(f"repos/acme/core/commits/{sha}/check-runs")


def test_push_of_a_sha_whose_required_check_succeeded_is_allowed(core):
    work, gh = core
    gh.runs[head(work)] = [run("completed", "success")]
    assert block("terminal", {"command": "git push origin HEAD:main", "workdir": str(work)}) is None
    assert block("terminal", {"command": "git push origin feat:refs/heads/main", "workdir": str(work)}) is None


@pytest.mark.parametrize("runs,state", [
    ([], "missing"),
    ([run("completed", "failure")], "failure"),
    ([run("completed", "cancelled")], "cancelled"),
    ([run("queued")], "queued"),
    ([run("completed", "success", 1, "2026-10-30T10:00:00Z"), run("completed", "failure", 2, "2026-10-30T11:00:00Z")],
     "failure"),  # the newest run decides
    ([run("completed", "success", name="Other job")], "missing"),  # a different check's success does not count
    ([run("in_progress", name="Tests (slice 1)")], "missing (CI still running: 1 of 1 check-runs"),  # gate not made yet
    ([], "missing (no check-runs at all on this sha"),
])
def test_missing_failed_or_pending_check_is_blocked(core, runs, state):
    work, gh = core
    gh.runs[head(work)] = runs
    got = block("terminal", {"command": "git push origin HEAD:main", "workdir": str(work)})
    assert got and f"is {state}" in got["message"] and "on that exact sha" in got["message"]


def test_unreadable_check_status_is_blocked(core):
    work, gh = core
    gh.fail = True
    got = block("terminal", {"command": "git push origin HEAD:main", "workdir": str(work)})
    assert got and "unreadable" in got["message"] and "Bad credentials" in got["message"]


def _fake_gh(path, needs_token):
    """An executable gh that prints one successful CHECK run; with needs_token it fails like a gh with no login."""
    body = json.dumps({"total_count": 1, "check_runs": [run("completed", "success")]})
    gate = ('[ -n "$GH_TOKEN" ] || { echo "To get started with GitHub CLI, please run:  gh auth login" >&2; exit 4; }\n'
            if needs_token else "")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\n{gate}echo '{body}'\n")
    path.chmod(0o755)


def _service_env(tmp_path, monkeypatch):
    """HOME with nothing installed yet, and a PATH without gh: how hermes-hub's gateway and kanban workers run."""
    home, empty = tmp_path / "home", tmp_path / "empty"
    home.mkdir()
    empty.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", str(empty))
    monkeypatch.delenv("GH_TOKEN", raising=False)
    return home


def test_gh_missing_from_path_is_found_in_the_usual_install_dirs(tmp_path, monkeypatch):
    home = _service_env(tmp_path, monkeypatch)
    _fake_gh(home / "bin" / "gh", needs_token=False)
    assert guard.check_state("acme/core", "a" * 40, CHECK) == "success"


def test_broker_retry_runs_gh_by_absolute_path(tmp_path, monkeypatch):
    """gh with no login is retried through token-broker, which execs under the same PATH: it needs gh's full path."""
    home = _service_env(tmp_path, monkeypatch)
    _fake_gh(home / "bin" / "gh", needs_token=True)
    broker = home / ".local" / "bin" / "token-broker"
    broker.parent.mkdir(parents=True)
    broker.write_text('#!/bin/sh\nwhile [ $# -gt 0 ] && [ "$1" != "--" ]; do shift; done\nshift\n'
                      'GH_TOKEN=from-broker exec "$@"\n')
    broker.chmod(0o755)
    assert guard.check_state("acme/core", "a" * 40, CHECK) == "success"


def test_unresolvable_source_is_blocked_and_no_op_push_is_allowed(core):
    work, gh = core
    got = block("terminal", {"command": "git push origin nosuchref:main", "workdir": str(work)})
    assert got and "unverified" in got["message"]
    git("checkout", "-q", "main", cwd=work)
    assert block("terminal", {"command": "git push origin main", "workdir": str(work)}) is None  # tip == remote tip
    assert not gh.calls


def test_check_guard_matches_the_exact_repo_not_a_prefix(core, tmp_path):
    work, gh = core
    mods = tmp_path / "acme" / "core-mods.git"
    mods.mkdir(parents=True)
    git("init", "-q", "--bare", "-b", "main", cwd=mods)
    git("remote", "add", "mods", str(mods), cwd=work)
    assert block("terminal", {"command": "git push mods HEAD:main", "workdir": str(work)}) is None
    assert not gh.calls


def test_check_guard_override_and_feature_branches(core):
    work, gh = core
    gh.runs[head(work)] = [run("in_progress")]
    assert block("terminal", {"command": "PUSH_GUARD=off git push origin HEAD:main", "workdir": str(work)}) is None
    assert block("terminal", {"command": "git push -u origin feat", "workdir": str(work)}) is None
    assert not gh.calls


def test_check_guard_through_execute_code(core):
    work, gh = core
    gh.runs[head(work)] = [run("in_progress")]
    assert block("execute_code", {"code": f'subprocess.run(["git", "-C", "{work}", "push", "origin", "HEAD:main"])'})


def test_gh_pr_merge_admin_with_pending_aggregate_is_blocked(core, tmp_path):
    work, gh = core
    sha = "0123456789abcdef0123456789abcdef01234567"
    gh.prs["42"] = {"headRefOid": sha, "baseRefName": "main"}
    gh.runs[sha] = [run("in_progress")]
    got = block("terminal", {"command": "gh pr merge 42 --squash --admin", "workdir": str(work)})
    assert got and sha[:12] in got["message"] and "in_progress" in got["message"] and "admin merge" in got["message"]
    # -R from anywhere, and wrapped in token-broker exec (how hub agents run gh)
    assert block("terminal", {"command": "gh pr merge 42 --admin -R acme/core", "workdir": str(tmp_path)})
    assert block("terminal", {"command": "token-broker exec --scope user/github --reason 'land it' -- "
                                         "gh pr merge 42 --admin --squash", "workdir": str(work)})
    assert block("execute_code", {"code": f'terminal("cd {work} && gh pr merge 42 --admin")'})
    gh.runs[sha] = [run("completed", "success")]
    assert block("terminal", {"command": "gh pr merge 42 --squash --admin", "workdir": str(work)}) is None


def test_gh_pr_merge_admin_unreadable_pr_is_blocked(core):
    work, gh = core
    got = block("terminal", {"command": "gh pr merge 999 --admin", "workdir": str(work)})
    assert got and "could not be read" in got["message"]


def test_other_repos_are_not_checked(core, tmp_path):
    work, gh = core
    assert block("terminal", {"command": "gh pr merge 42 --admin -R someone/else", "workdir": str(tmp_path)}) is None
    assert not gh.calls


def test_babybear_style_guard_is_unchanged_by_check_guards(repo, monkeypatch, tmp_path):
    cfg = tmp_path / "push-guards.json"
    cfg.write_text(json.dumps({"guards": MIXED}))
    fake = FakeGH()
    monkeypatch.setattr(guard, "_gh_json", fake)
    change(repo, "hub/x.py")
    got = block("terminal", {"command": "git push origin HEAD:main", "workdir": str(repo)})
    assert got and "merge train" in got["message"] and "land <branch> --wait" in got["message"]
    git("reset", "-q", "--hard", "HEAD~1", cwd=repo)
    change(repo, "docs/plans/x.md")
    assert block("terminal", {"command": "git push origin HEAD:main", "workdir": str(repo)}) is None
    assert block("terminal", {"command": "gh pr merge 3 --admin", "workdir": str(repo)}) is None  # no require_check
    assert not fake.calls


def test_gh_json_falls_back_to_token_broker_when_gh_has_no_login(tmp_path, monkeypatch):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (bin_ / "gh").write_text("#!/bin/sh\necho 'You are not logged into any GitHub hosts.' >&2\nexit 4\n")
    (bin_ / "token-broker").write_text(
        "#!/bin/sh\n[ \"$1 $2 $3\" = 'exec --scope user/github' ] || exit 9\n"
        "echo '{\"check_runs\": [{\"id\": 1, \"name\": \"C\", \"status\": \"completed\", \"conclusion\": \"success\"}]}'\n")
    for f in bin_.iterdir():
        f.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_}:/usr/bin:/bin")
    assert guard.check_state("acme/core", "abc", "C") == "success"
    (bin_ / "token-broker").unlink()
    monkeypatch.setattr(guard.Path, "home", lambda: tmp_path)  # no ~/.local/bin/token-broker either
    assert guard.check_state("acme/core", "abc", "C").startswith("unreadable (You are not logged")


# --- source-custody namespace ----------------------------------------------------------------------------------

NS = "refs/hermes-source-custody"


def hold_custody_ref(work):
    git("update-ref", f"{NS}/store/op/unit/pin", "HEAD", cwd=work)


@pytest.mark.parametrize("spec", [
    f"{NS}/store/op/unit/pin", f"HEAD:{NS}/x", f"+{NS}/*:{NS}/*", "hermes-source-custody/x",
    f"feat:refs/heads/ok {NS}/a:{NS}/a", f":{NS}/x"])
def test_a_refspec_naming_the_custody_namespace_is_blocked_on_any_remote(repo, tmp_path, spec):
    other = tmp_path / "someone" / "else.git"
    other.mkdir(parents=True)
    git("init", "-q", "--bare", cwd=other)
    git("remote", "add", "backup", str(other), cwd=repo)
    for remote in ("origin", "backup"):  # backup has no configured guard
        got = block("terminal", {"command": f"git push {remote} {spec}", "workdir": str(repo)})
        assert got and got["action"] == "block" and NS in got["message"], (remote, spec)


def test_custody_check_needs_no_guard_config_and_ignores_the_override(repo, monkeypatch, tmp_path):
    monkeypatch.setenv("PUSH_GUARD_CONFIG", str(tmp_path / "absent.json"))
    cmd = f"PUSH_GUARD=off git push origin HEAD:{NS}/x"
    got = block("terminal", {"command": cmd, "workdir": str(repo)})
    assert got and "does not skip this check" in got["message"]
    code = f"import subprocess\nsubprocess.run(['git', 'push', 'origin', 'HEAD:{NS}/x'])\n"
    assert guard.check_python(code, str(repo))
    code = f"import subprocess\nsubprocess.run('git push origin HEAD:{NS}/x', shell=True)\n"
    assert guard.check_python(code, str(repo))


def test_mirror_and_glob_pushes_are_blocked_only_while_the_repo_holds_custody_refs(repo):
    broad = ["git push --mirror origin", "git push origin 'refs/*:refs/*'", "git push origin '+refs/hermes*:refs/x/*'"]
    for cmd in broad:  # nothing to leak yet
        assert block("terminal", {"command": cmd, "workdir": str(repo)}) is None, cmd
    hold_custody_ref(repo)
    for cmd in broad:
        got = block("terminal", {"command": cmd, "workdir": str(repo)})
        assert got and got["action"] == "block" and NS in got["message"], cmd
    # Named branches still go: the guard is about the namespace, not about pushing.
    assert block("terminal", {"command": "git push origin feat", "workdir": str(repo)}) is None
    for cmd in ("git push --all origin", "git push origin '+refs/heads/*:refs/heads/*'", "git push origin 'refs/tags/*'"):
        assert block("terminal", {"command": cmd, "workdir": str(repo)}) is None, cmd


def test_configured_mirror_or_broad_push_refspec_is_blocked_for_a_bare_push(repo):
    hold_custody_ref(repo)
    bare_push = {"command": "git push origin", "workdir": str(repo)}
    assert block("terminal", bare_push) is None
    git("config", "remote.origin.push", "refs/heads/*:refs/heads/*", cwd=repo)
    assert block("terminal", bare_push) is None  # branches only: cannot select the namespace
    git("config", "--add", "remote.origin.push", "refs/*:refs/*", cwd=repo)
    got = block("terminal", bare_push)
    assert got and "push config" in got["message"]
    git("config", "--unset-all", "remote.origin.push", cwd=repo)
    git("config", "remote.origin.mirror", "true", cwd=repo)
    got = block("terminal", bare_push)
    assert got and "is a mirror" in got["message"]
    git("config", "remote.origin.mirror", "false", cwd=repo)
    assert block("terminal", bare_push) is None


# Core's native source publisher pushes with exactly this argv (hermes_cli/kanban_source_publish._push): the scanned
# sha to the task branch, by URL, with a lease. The guard must neither block it nor miss it when it names custody refs.
def native_publisher_argv(url, refspec, branch="feat"):
    return (f"git -c push.default=nothing -c credential.helper= push --no-verify --porcelain "
            f"--force-with-lease=refs/heads/{branch}: {url} {refspec}")


def test_native_publisher_argv_for_a_task_branch_is_not_blocked_while_custody_refs_exist(repo, tmp_path):
    hold_custody_ref(repo)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
    url = str(tmp_path / "someone" / "else.git")
    cmd = native_publisher_argv(url, f"{head}:refs/heads/feat")
    assert block("terminal", {"command": cmd, "workdir": str(repo)}) is None


@pytest.mark.parametrize("refspec", [f"HEAD:{NS}/x", f"+{NS}/*:{NS}/*", f":{NS}/x"])
def test_native_publisher_argv_naming_custody_refs_is_blocked(repo, tmp_path, refspec):
    url = str(tmp_path / "someone" / "else.git")
    got = block("terminal", {"command": native_publisher_argv(url, refspec), "workdir": str(repo)})
    assert got and got["action"] == "block" and NS in got["message"], refspec


# --- custody: git config the parser used to drop ---------------------------------------------------------------

def _blocked(repo, cmd):
    got = block("terminal", {"command": cmd, "workdir": str(repo)})
    return bool(got and got["action"] == "block" and NS in got["message"])


@pytest.mark.parametrize("cmd", [
    "git -c remote.origin.mirror=true push origin",
    "git -c remote.origin.mirror=true push origin feat",  # explicit specs do not make a mirror remote safe
    "git -c remote.origin.push='refs/*:refs/*' push origin",
    f"git -c remote.origin.push=refs/heads/feat:{NS}/x push origin",  # destination side only
    "git -c remote.pushDefault=origin -c remote.origin.mirror=true push",
    "git -c remote.origin.mirror=true push --repo=origin",
])
def test_dash_c_config_that_turns_a_push_into_a_custody_leak_is_blocked(repo, cmd):
    assert not _blocked(repo, "git push origin feat")
    hold_custody_ref(repo)
    assert _blocked(repo, cmd), cmd


def test_configured_push_destination_in_the_custody_namespace_is_blocked(repo):
    hold_custody_ref(repo)
    git("config", "remote.origin.push", f"refs/heads/feat:{NS}/x", cwd=repo)
    assert _blocked(repo, "git push origin")
    assert not _blocked(repo, "git push origin feat")  # explicit refspec replaces the configured one


def test_configured_mirror_remote_with_explicit_refspecs_is_blocked(repo):
    hold_custody_ref(repo)
    git("config", "remote.origin.mirror", "true", cwd=repo)
    assert _blocked(repo, "git push origin feat")


def test_unprovable_custody_state_does_not_count_as_absence(repo, tmp_path):
    hold_custody_ref(repo)
    gd = subprocess.run(["git", "rev-parse", "--absolute-git-dir"], cwd=repo, capture_output=True, text=True).stdout.strip()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    # --git-dir re-points git away from the repo the guard inspected; unreadable cwd is likewise unproved
    assert _blocked(elsewhere, f"git --git-dir={gd} push --mirror origin")
    assert _blocked(elsewhere, "git push --mirror origin")
    assert _blocked(elsewhere, f"git --git-dir={gd} -c remote.origin.mirror=true push origin")



def test_native_publisher_dash_c_options_still_allowed_with_custody_refs(repo, tmp_path):
    hold_custody_ref(repo)
    url = str(tmp_path / "someone" / "else.git")
    cmd = native_publisher_argv(url, "HEAD:refs/heads/feat")
    assert not _blocked(repo, cmd)
    assert not _blocked(repo, "git -c push.default=nothing -c credential.helper= push origin feat")
