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


# --- custody: the effective git view (--git-dir / GIT_DIR / --config-env / GIT_CONFIG_*) --------------------------

def _held_view(repo, tmp_path):
    hold_custody_ref(repo)
    gd = subprocess.run(["git", "rev-parse", "--absolute-git-dir"], cwd=repo, capture_output=True, text=True).stdout.strip()
    elsewhere = tmp_path / "clean-unrelated"
    elsewhere.mkdir()
    return gd, elsewhere


def test_git_dir_view_reads_the_held_repos_mirror_config_from_a_clean_cwd(repo, tmp_path):
    gd, elsewhere = _held_view(repo, tmp_path)
    git("config", "remote.origin.mirror", "true", cwd=repo)
    for cmd in (f"git --git-dir={gd} push origin", f"git --git-dir {gd} push origin",
                f"git --git-dir={gd} --work-tree={repo} push origin", f"GIT_DIR={gd} git push origin",
                f"env GIT_DIR={gd} git push origin", f"export GIT_DIR={gd}; git push origin",
                f"GIT_DIR={gd}\ngit push origin", f"git --git-dir={gd} push --repo=origin"):
        assert _blocked(elsewhere, cmd), cmd


def test_git_dir_view_allows_explicit_branch_pushes_when_not_a_mirror(repo, tmp_path):
    gd, elsewhere = _held_view(repo, tmp_path)
    for cmd in (f"git --git-dir={gd} push origin feat", f"GIT_DIR={gd} git push origin feat",
                f"git --git-dir={gd} push origin HEAD:refs/heads/feat", f"GIT_DIR={gd} git push -u origin feat",
                f"git --git-dir={gd} push origin refs/heads/*:refs/heads/*"):
        assert not _blocked(elsewhere, cmd), cmd
    # a view proved to hold no custody refs is also fine for a broad push
    clean = tmp_path / "clean-repo"
    clean.mkdir()
    git("init", "-q", "-b", "main", cwd=clean)
    git("remote", "add", "origin", str(tmp_path / "owner" / "repo.git"), cwd=clean)
    assert not _blocked(elsewhere, f"git --git-dir={clean}/.git push --mirror origin")


def test_git_dir_env_keeps_routing_for_mirror_flag_and_assigned_remote(repo, tmp_path):
    gd, elsewhere = _held_view(repo, tmp_path)
    assert _blocked(elsewhere, f"GIT_DIR={gd} git push --mirror origin")
    assert _blocked(elsewhere, f"GIT_DIR={gd} git push --mirror")
    git("config", "remote.up.url", str(tmp_path / "owner" / "repo.git"), cwd=repo)
    git("config", "remote.up.mirror", "true", cwd=repo)
    git("config", "remote.pushDefault", "up", cwd=repo)
    assert _blocked(elsewhere, f"GIT_DIR={gd} git push")
    assert not _blocked(elsewhere, f"GIT_DIR={gd} git push origin feat")


@pytest.mark.parametrize("form", [
    "MIRROR=true git --config-env=remote.origin.mirror=MIRROR push origin",
    "MIRROR=true git --config-env remote.origin.mirror=MIRROR push origin",
    "env MIRROR=true git --config-env=remote.origin.mirror=MIRROR push origin",
    "export MIRROR=true; git --config-env=remote.origin.mirror=MIRROR push origin",
    "MIRROR=true git --config-env=remote.origin.mirror=MIRROR push origin feat",
    "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=remote.origin.mirror GIT_CONFIG_VALUE_0=true git push origin",
    "GIT_CONFIG_PARAMETERS=\"'remote.origin.mirror=true'\" git push origin",
])
def test_config_env_and_git_config_env_overrides_are_applied(repo, form):
    hold_custody_ref(repo)
    assert _blocked(repo, form), form


def test_config_env_override_can_also_switch_mirror_off(repo):
    hold_custody_ref(repo)
    git("config", "remote.origin.mirror", "true", cwd=repo)
    assert _blocked(repo, "git push origin")
    assert not _blocked(repo, "MIRROR=false git --config-env=remote.origin.mirror=MIRROR push origin")


def test_unresolvable_view_inputs_fail_closed_but_url_pushes_with_refspecs_pass(repo, tmp_path):
    gd, elsewhere = _held_view(repo, tmp_path)
    url = str(tmp_path / "someone" / "else.git")
    for cmd in ("git --git-dir=$HOLD push origin feat", "GIT_DIR=$(pwd)/.git git push origin feat",
                "git --config-env=remote.origin.mirror=NOT_SET_ANYWHERE_XYZ push origin feat",
                "git -C $WHERE push origin feat"):
        assert _blocked(elsewhere, cmd), cmd
    assert not _blocked(elsewhere, f"git --git-dir=$HOLD push {url} feat")


# --- custody: abbreviations, wrappers, nested shells, aliases, failed views --------------------------------------

@pytest.fixture
def held_mirror(repo, tmp_path):
    """A repo holding a custody ref, with a configured mirror remote `mir` and a clean cwd elsewhere."""
    hold_custody_ref(repo)
    mir = tmp_path / "mirror.git"
    mir.mkdir()
    git("init", "-q", "--bare", cwd=mir)
    git("remote", "add", "mir", str(mir), cwd=repo)
    git("config", "remote.mir.mirror", "true", cwd=repo)
    return repo, mir


def _leaked(mir):
    out = subprocess.run(["git", "-C", str(mir), "for-each-ref"], capture_output=True, text=True).stdout
    return "hermes-source-custody" in out


@pytest.mark.parametrize("cmd", [
    "git push --mir @M", "git push --mi mir", "git push --mirror=1 mir", "git push --m mir",
    "env -u FOO git push mir", "env -u FOO -u BAR git push --mirror @M", "env -i git push mir",
    "env -iu FOO git push mir", "env --unset=FOO git push mir", "env -C . git push mir", "env -S 'git push mir'",
    "env -S'git push --mirror @M'", "env -i -- git push mir", "sudo -u root git push mir",
    "sudo -E -u root git push --mirror @M", "timeout -s KILL 5 git push mir", "nice -n 5 git push mir",
    "nohup git push mir", "time -p git push mir", "command git push mir", "xargs git push --mirror",
    "bash -c 'git push mir'", "sh -c \"git push --mirror @M\"", "bash -lc 'git push mir'",
    "sudo bash -c 'env -u X git push mir'", "bash -c \"bash -c 'git push mir'\"", "eval 'git push mir'",
    "su -c 'git push mir'", "(git push mir)", "{ git push mir; }", "if true; then git push mir; fi",
    "true && (git push mir)", "echo x | git push mir", "git push mir &", "echo $(git push mir)",
    "echo \"$(git push mir)\"", "echo `git push mir`", "\\git push mir", "/usr/bin/git push mir",
    "git -c alias.p='push --mirror' p @M", "git -c alias.p='push' p mir", "git -c alias.p='!git push mir' p",
    "git -c alias.p='!git push' p mir", "git -c alias.p='push' -c alias.q=p q mir", "GIT=git; $GIT push mir",
    "$GIT push mir", "git push --rep=mir", "git push --repo mir", "git push -- mir",
    "git push --exec x mir", "git push --receive-pack=x --mir @M", "git push --push-opt x --mirror @M",
    "env GIT_DIR=.git git push mir", "(cd /; true); git push mir", "cd /tmp | git push mir",
    "git --git-dir=.git -C . push mir",
])
def test_custody_escapes_in_the_effective_view_are_blocked(held_mirror, cmd):
    repo, mir = held_mirror
    assert _blocked(repo, cmd.replace("@M", str(mir))), cmd


def test_cd_into_an_unresolved_or_missing_directory_is_a_failed_view_not_absent_config(held_mirror, tmp_path):
    repo, mir = held_mirror
    elsewhere = tmp_path / "clean-elsewhere"
    elsewhere.mkdir()
    for cmd in (f"R={repo}; cd $R && git push mir", f"R={repo}; cd ${{R}}; git push mir",
                "cd $UNKNOWN_DIR_XYZ && git push mir", "cd /does/not/exist; git push mir",
                f"cd {repo} && git push mir", f"pushd {repo}; git push mir"):
        assert _blocked(elsewhere, cmd), cmd
    # the shell variable is resolved, so an explicit branch push from the held repo is fine
    assert not _blocked(elsewhere, f"R={repo}; cd $R && git push origin feat")
    assert not _blocked(repo, f"(cd {elsewhere}; true); git push origin feat")


def test_env_wrappers_change_the_view_the_guard_reads(held_mirror, tmp_path):
    repo, mir = held_mirror
    gd = subprocess.run(["git", "rev-parse", "--absolute-git-dir"], cwd=repo, capture_output=True,
                        text=True).stdout.strip()
    elsewhere = tmp_path / "clean-elsewhere"
    elsewhere.mkdir()
    assert _blocked(elsewhere, f"env -C {repo} git push mir")
    assert _blocked(elsewhere, f"env --chdir={repo} -u FOO git push mir")
    assert _blocked(elsewhere, f"env -S 'GIT_DIR={gd} git push mir'")
    assert _blocked(elsewhere, f"env -i GIT_DIR={gd} git push mir")
    # sudo may keep or drop GIT_* by policy: an assigned GIT_* var makes the view unproved
    assert _blocked(repo, f"GIT_DIR={gd} sudo git push")
    assert not _blocked(repo, "sudo git push origin feat")


def test_non_push_aliases_and_explicit_branch_pushes_through_wrappers_are_not_blocked(held_mirror):
    repo, mir = held_mirror
    for cmd in ("git -c alias.p='status' p", "git status", "git -c alias.p='!echo push' p", "env -u FOO git status",
                "bash -c 'git status'", "echo 'git push --mirror'", "git push origin feat",
                "env -u FOO git push origin feat", "sudo git push origin feat", "bash -c 'git push origin feat'",
                "(git push origin feat)", "git -c alias.p='push origin' p feat",
                "git push origin HEAD:refs/heads/feat", "git push --no-verify origin feat"):
        assert not _blocked(repo, cmd), cmd


def test_blocked_mirror_pushes_really_would_have_reached_the_remote(held_mirror):
    repo, mir = held_mirror
    for cmd in ("git push --mir mir", "env -u FOO git push mir", "git -c alias.p='push --mirror' p mir"):
        assert _blocked(repo, cmd), cmd
    assert not _leaked(mir)
    # the same argv really leaks when it is not routed through the guard (the probe is meaningful)
    subprocess.run(["git", "push", "--mir", "mir"], cwd=repo, capture_output=True,
                   env={**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null"})
    assert _leaked(mir)


@pytest.mark.parametrize("alias", ["push --mirror", "!git push --mirror"])
@pytest.mark.parametrize("route", ["terminal", "python-string", "python-argv", "tool_call"])
def test_configured_alias_without_push_literal_reaches_actual_hook(held_mirror, alias, route):
    repo, mir = held_mirror
    git("config", "alias.p", alias, cwd=repo)
    cmd = "git p mir"
    args = {"command": cmd, "workdir": str(repo)}
    tool = "terminal"
    if route.startswith("python"):
        tool = "execute_code"
        payload = repr(cmd) if route == "python-string" else repr(["git", "p", "mir"])
        args = {"code": f"import subprocess; subprocess.run({payload})", "workdir": str(repo)}
    elif route == "tool_call":
        tool, args = "tool_call", {"calls": [{"name": tool, "arguments": args}]}
    assert "push" not in cmd
    if tool == "execute_code":
        assert "push" not in args["code"]
    got = block(tool, args)
    assert got and got["action"] == "block" and NS in got["message"]
    assert not _leaked(mir)


@pytest.mark.parametrize("option", ["--config-env=remote.origin.mirror=MIRROR",
                                    "--config-env remote.origin.mirror=MIRROR"])
def test_shell_alias_config_env_is_not_silently_lost(repo, option):
    hold_custody_ref(repo)
    git("config", "alias.p", "!git push origin", cwd=repo)
    cmd = f"MIRROR=true git {option} p"
    assert "push" not in cmd
    assert _blocked(repo, cmd)


@pytest.mark.parametrize("wrapper", ["sudo", "sudo -u root", "doas", "doas -u root"])
def test_other_user_view_never_proves_bare_push_safe_even_without_local_refs(repo, wrapper):
    # No custody refs or mirror config in this user's view does not prove the other user's view safe.
    assert _blocked(repo, f"{wrapper} git push origin")
    assert _blocked(repo, f"{wrapper} git push")
    # Explicit safe refspecs replace configured push specs; Git rejects mirror + refspecs.
    assert not _blocked(repo, f"{wrapper} git push origin feat")
    assert not _blocked(repo, f"{wrapper} git push origin refs/heads/*:refs/heads/*")
    assert _blocked(repo, f"{wrapper} git push --mirror origin")


def test_configured_non_push_alias_without_push_literal_is_allowed(repo):
    git("config", "alias.s", "status", cwd=repo)
    assert block("terminal", {"command": "git s", "workdir": str(repo)}) is None
    assert block("execute_code", {"code": "subprocess.run(['git', 's'])", "workdir": str(repo)}) is None


# --- custody: review round (inherited config, alias cwd, variable operands, env -S, quoted substitution) ------------

def _term(cmd, workdir):
    got = block("terminal", {"command": cmd, "workdir": str(workdir)})
    return bool(got and got["action"] == "block" and NS in got["message"])


@pytest.mark.parametrize("cmd", [
    """GIT_CONFIG_PARAMETERS="'remote.origin.mirror=true'" git -c alias.p="!git push origin" p""",
    """GIT_CONFIG_PARAMETERS="'remote.origin.mirror=true'" git -c alias.p="!git push origin" -c user.name=x p""",
    """export GIT_CONFIG_PARAMETERS="'remote.origin.mirror=true'"; git -c alias.p="!git push origin" p""",
])
def test_inherited_git_config_parameters_survive_a_shell_alias(repo, cmd):
    hold_custody_ref(repo)
    assert _term(cmd, repo)
    # control: the same alias without the inherited mirror config is a plain push of the current branch
    assert not _term("""git -c alias.p="!git push origin" p""", repo)
    assert not _term("""GIT_CONFIG_PARAMETERS="'user.name=x'" git -c alias.p="!git push origin" p""", repo)


def test_alias_cwd_is_the_worktree_root_not_the_invocation_subdir(tmp_path):
    held = tmp_path / "held"
    held.mkdir()
    git("init", "-q", "-b", "main", cwd=held)
    (held / "f").write_text("x")
    git("add", ".", cwd=held)
    git("commit", "-qm", "b", cwd=held)
    bare = tmp_path / "held-remote.git"
    bare.mkdir()
    git("init", "-q", "--bare", cwd=bare)
    git("remote", "add", "origin", str(bare), cwd=held)
    hold_custody_ref(held)
    outer = tmp_path / "outer"
    (outer / "sub").mkdir(parents=True)
    git("init", "-q", "-b", "main", cwd=outer)
    decoy = outer / "held"  # what `../held` means from outer/sub: a repo with no refs at all
    decoy.mkdir()
    git("init", "-q", cwd=decoy)
    git("remote", "add", "origin", str(bare), cwd=decoy)
    git("config", "alias.p", "!git -C ../held push --mirror origin", cwd=outer)
    assert _term("git p", outer / "sub")  # alias runs in outer/, so ../held is the sibling holding refs
    assert _term("git p", outer)
    # control: an explicit branch push through the same alias shape is allowed
    git("config", "alias.q", "!git -C ../held push origin main", cwd=outer)
    assert not _term("git q", outer / "sub")
    assert not _leaked(bare)


def test_alias_whose_worktree_root_cannot_be_found_fails_closed(tmp_path):
    bare = tmp_path / "b.git"
    bare.mkdir()
    git("init", "-q", "--bare", cwd=bare)
    git("config", "alias.p", "!git push --mirror origin", cwd=bare)
    assert _term("git --git-dir=%s p" % bare, tmp_path)


@pytest.mark.parametrize("cmd", [
    "R=mir; git push \"$R\"", "R=mir; git push ${R}", "R=mir; git push --mirror \"$R\"",
    "S=refs/hermes-source-custody/store/unit/pin; git push origin \"$S\"",
    "S=refs/hermes-source-custody/store/unit/pin; git push origin HEAD:$S",
    "export S=refs/hermes-source-custody/x; git push origin $S",
    "git push origin \"$UNSET_SPEC_XYZ\"", "git push \"$UNSET_REMOTE_XYZ\"",
    "S=x git push origin \"$(echo refs/hermes-source-custody/x)\"",
    "R=; git push origin $R", "export R=; git push origin $R",
    "S='origin refs/hermes-source-custody/store/unit/pin'; git push $S",
    "R='--mirror mir'; git push $R", "R='main refs/hermes-source-custody/store/unit/pin'; git push origin $R",
    "USER=--mirror; A=\"$USER\"; git push $A mir", "unset HOME; git push origin $HOME",
    "git push `echo mir`", "git push --mirror $(echo mir)",
    "B=feat; git push origin \"$B\"", "R=origin B=feat; git push $R $B", "B=feat; git push origin HEAD:$B",
])
def test_push_operands_with_expansion_fail_closed_before_expansion(held_mirror, cmd):
    repo, mir = held_mirror
    assert _term(cmd, repo), cmd
    assert not _leaked(mir)


def test_push_operands_without_expansion_are_allowed(held_mirror):
    repo, mir = held_mirror
    for cmd in ("git push origin feat", "git push origin HEAD:feat", "B=feat; git push origin feat"):
        assert not _term(cmd, repo), cmd


@pytest.mark.parametrize("cmd", [
    "env -S 'git\\_push\\_mir'", "env -S 'git\\_push\\_--mirror\\_@M'", "env -S 'git push\\_mir'",
    "env -S'git\\_push\\_mir'", "env --split-string='git\\_push\\_mir'", "env -i -S 'git\\_push\\_mir'",
    "env -S 'git\\_push mir'", "env -S 'git push \\qmir'", "env -S 'git\\_push\\_\\x'",
    "env -S \"git push 'x\\_y'\"",  # single-quoted \_ is literal to GNU env: any backslash is refused
    "env -S '-i git push mir'", "env -S '-u FOO git push mir'", "env -S '-- git push mir'",
    "env -S \"-S'git push mir'\"", "env -S ' -i git push mir'", "env -S '--ignore-environment git push mir'",
    "env -S 'git push " + chr(36) + "X'", "env -S 'git push mir # c'",
])
def test_env_split_string_backslash_expansion_or_leading_option_is_refused(held_mirror, cmd):
    repo, mir = held_mirror
    assert _term(cmd.replace("@M", str(mir)), repo), cmd
    assert not _leaked(mir)


def test_env_split_string_safe_forms_are_not_over_blocked(held_mirror):
    repo, mir = held_mirror
    for cmd in ("env -S 'git push origin feat'", "env -S 'git status'", "env -S 'echo hi'",
                "env -i -S 'git status'", "env -u FOO -S 'git status'"):
        assert not _term(cmd, repo), cmd


@pytest.mark.parametrize("cmd", [
    "echo $'a\\'b' $(git push mir) c\\'", "echo $'a\\'b' `git push mir` c\\'",
    "echo $'x'; git push mir", "git push mir $'x'", "echo $'$(git push mir)'",
])
def test_ansi_c_quoting_with_push_refuses_conservatively(held_mirror, cmd):
    repo, mir = held_mirror
    git("config", "alias.p", "push mir", cwd=repo)
    assert _term("echo $'a\\'b' $(git p) c\\'", repo)
    assert _term(cmd, repo), cmd
    assert not _leaked(mir)


@pytest.mark.parametrize("cmd", [
    "find . -maxdepth 0 -exec sh -c 'echo $(git push mir)' \\;",
    "find . -maxdepth 0 -exec sh -c 'echo `git push mir`' \\;",
    "find . -maxdepth 0 -execdir bash -c 'git push mir' +",
    "script -qc 'echo $(git push mir)' /dev/null", "script -qc 'git push mir' /dev/null",
    "script -c 'git push --mirror mir' /dev/null", "script --command 'git push mir' /dev/null",
    "script -q -c 'git push mir' /dev/null",
])
def test_find_exec_and_script_payloads_are_scanned_as_executing(held_mirror, cmd):
    repo, mir = held_mirror
    assert _term(cmd, repo), cmd
    assert not _leaked(mir)


def test_inert_single_quoted_text_around_find_and_script_is_allowed(held_mirror):
    repo, mir = held_mirror
    for cmd in ("find . -maxdepth 0 -exec echo '$(git push mir)' \\;", "echo '$(git push mir)'",
                "printf '%s' '`git push mir`'", "git commit -q --allow-empty -m 'doc: $(git push mir)'",
                "script -qc 'echo hi' /dev/null"):
        assert not _term(cmd, repo), cmd


@pytest.mark.parametrize("cmd", [
    "echo '`git push mir`'", "echo '$(git push --mirror mir)'", "echo 'a `git push mir` b'",
    "printf '%s' '`git push mir`'",
    "echo \\`git push mir\\`",
])
def test_inert_quoted_command_substitution_is_not_executed_or_refused(held_mirror, cmd):
    repo, mir = held_mirror
    assert not _term(cmd, repo), cmd


@pytest.mark.parametrize("cmd", [
    "echo `git push mir`", "echo \"`git push mir`\"", "echo \"$(git push mir)\"", "echo $(git push mir)",
    "echo '`ok`' `git push mir`", "echo '$(ok)' \"$(git push mir)\"",
])
def test_live_command_substitution_is_still_refused(held_mirror, cmd):
    repo, mir = held_mirror
    assert _term(cmd, repo), cmd
