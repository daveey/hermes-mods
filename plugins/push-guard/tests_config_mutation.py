"""Actual-hook regressions for config writes preceding publication in one tool command.

All execution oracles use fixture-owned local bare remotes. The hook must not
execute/simulate the write to decide: invalidating a no-longer-proven view is OK.
"""
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess

import pytest

HERE = Path(__file__).resolve().parent
NS = "refs/hermes-source-custody"
spec = importlib.util.spec_from_file_location("push_guard_config_mutation", HERE / "__init__.py")
plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin)


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def mutation_repo(tmp_path, monkeypatch):
    # No inherited credentials, aliases, includes or hooks; no non-local transport.
    for key in list(os.environ):
        if key.startswith("GIT_") or key in {"PUSH_GUARD", "BASH_ENV", "ENV"}:
            monkeypatch.delenv(key, raising=False)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / "xdg"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_ALLOW_PROTOCOL", "file")
    for key, value in {"GIT_AUTHOR_NAME": "Test", "GIT_COMMITTER_NAME": "Test",
                       "GIT_AUTHOR_EMAIL": "test@example.com",
                       "GIT_COMMITTER_EMAIL": "test@example.com"}.items():
        monkeypatch.setenv(key, value)
    cfg = home / ".gitconfig"
    cfg.write_text("[core]\n\thooksPath = /dev/null\n")
    guards = tmp_path / "push-guards.json"
    guards.write_text(json.dumps({"guards": []}))
    monkeypatch.setenv("PUSH_GUARD_CONFIG", str(guards))
    repo = tmp_path / "held"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "feat")
    (repo / "f").write_text("fixture\n")
    git(repo, "add", "f")
    git(repo, "commit", "-qm", "fixture")
    git(repo, "update-ref", NS + "/fixture", "HEAD")
    remotes = {}
    for name in ("origin", "mir"):
        bare = tmp_path / (name + ".git")
        bare.mkdir()
        git(bare, "init", "-q", "--bare")
        git(repo, "remote", "add", name, str(bare))
        remotes[name] = bare
    git(repo, "config", "remote.mir.mirror", "true")
    return repo, remotes


def hook(repo, command):
    return plugin._pre_tool_call(tool_name="terminal",
                                 args={"command": command, "workdir": str(repo)})


def refs(bare):
    return git(bare, "for-each-ref", "--format=%(refname)").splitlines()


# Each cell independently proves that real Bash/Git reaches a custody ref on a
# LOCAL bare remote. No assumption that a parsed config mutation took effect.
UNSAFE = [
    ("scope-modifier-write", "git config --show-scope remote.origin.mirror true && git push origin"),
    ("cancelled-read-action", "git config --get --no-get remote.origin.mirror true && git push origin"),
    ("mirror-value-pattern", "git config remote.origin.mirror true --name-only && git push origin"),
    ("mirror-origin-pattern", "git config remote.origin.mirror true --show-origin && git push origin"),
    ("mirror-default-pattern", "git config remote.origin.mirror true --default && git push origin"),
    ("push-get-pattern", "git config remote.origin.push 'refs/*:refs/*' --get && git push origin"),
    ("route-list-pattern", "git config remote.pushDefault mir --list && git push"),
    ("branch-help-pattern", "git config branch.feat.pushRemote mir -h && git push"),
    ("alias-option-value", "git config alias.z '-c remote.origin.mirror=true push origin' && git z"),
    ("alias-substitution", 'git config alias.z \'!echo "$(git push mir)"\' && git z'),
    ("alias-direct", "git config alias.z '!git push mir' && git z"),
    ("alias-argv", "git config alias.z 'push mir' && git z"),
    ("alias-overwrite", "git config alias.z '!echo safe' && git config alias.z '!git push mir' && git z"),
    ("mirror", "git config remote.origin.mirror true && git push origin"),
    ("mirror-semicolon", "git config remote.origin.mirror true; git push origin"),
    ("mirror-local", "git config --local remote.origin.mirror true && git push origin"),
    ("mirror-global", "git config --global remote.origin.mirror true && git push origin"),
    ("mirror-file", "git config --file .git/config remote.origin.mirror true && git push origin"),
    ("mirror-add", "git config --add remote.origin.mirror true && git push origin"),
    ("push-refspec", "git config remote.origin.push 'refs/*:refs/*' && git push origin"),
    ("push-refspec-add", "git config --add remote.origin.push 'refs/*:refs/*' && git push origin"),
    ("push-default-route", "git config remote.pushDefault mir && git push"),
    ("branch-push-route", "git config branch.feat.pushRemote mir && git push"),
    ("branch-upstream-route", "git config branch.feat.remote mir && git push"),
    ("dash-C-writer", "git -C @R config remote.origin.mirror true && git push origin"),
    ("git-dir-writer", "git --git-dir=@R/.git config remote.origin.mirror true && git push origin"),
    ("git-dir-reader", "git config remote.origin.mirror true && git --git-dir=@R/.git push origin"),
    ("env-reader", "git config remote.origin.mirror true && GIT_DIR=@R/.git git push origin"),
    ("child-shell-writer", "sh -c 'git config remote.origin.mirror true' && git push origin"),
    ("subshell-writer", "(git config remote.origin.mirror true) && git push origin"),
    ("child-shell-reader", "git config remote.origin.mirror true && sh -c 'git push origin'"),
]


@pytest.mark.parametrize("case,command", UNSAFE, ids=[case for case, _ in UNSAFE])
def test_same_command_config_mutation_cannot_use_a_stale_publication_view(mutation_repo, case, command):
    repo, remotes = mutation_repo
    command = command.replace("@R", shlex.quote(str(repo)))
    config_before = (repo / ".git/config").read_bytes()
    global_before = (Path.home() / ".gitconfig").read_bytes()
    decision = hook(repo, command)
    assert (repo / ".git/config").read_bytes() == config_before, "hook executed a config write"
    assert (Path.home() / ".gitconfig").read_bytes() == global_before, "hook wrote global config"
    assert all(not refs(bare) for bare in remotes.values()), "hook published fixture refs"
    oracle = subprocess.run(["bash", "--noprofile", "--norc", "-c", command],
                            cwd=repo, capture_output=True, text=True, timeout=15)
    leaked = [name for name, bare in remotes.items()
              if NS + "/fixture" in refs(bare)]
    assert oracle.returncode == 0 and leaked, (case, oracle.returncode, oracle.stderr, leaked)
    assert decision and decision.get("action") == "block", (
        f"{case}: actual terminal hook allowed stale config; local Git leaked to {leaked}: {command}", decision)


CONTROLS = [
    ("alias-config-only", 'git config alias.z \'!echo "$(git push mir)"\''),
    ("mirror-config-only", "git config remote.origin.mirror true"),
    ("refspec-config-only", "git config remote.origin.push 'refs/*:refs/*'"),
    ("alias-never-invoked", "git config alias.z '!git push mir' && git status --short"),
    ("mirror-no-publish", "git config remote.origin.mirror true && git status --short"),
    ("inert-config-text", "printf '%s\\n' 'git config remote.origin.mirror true && git push origin'"),
    ("inert-push-text", "git config user.name Test && printf '%s\\n' 'git push mir'"),
    ("config-read-before-branch", "git config --get remote.origin.url && git push origin feat"),
    ("ordinary-branch", "git push origin HEAD:refs/heads/feature"),
]


@pytest.mark.parametrize("case,command", CONTROLS, ids=[case for case, _ in CONTROLS])
def test_config_only_inert_and_ordinary_branch_controls_remain_allowed(mutation_repo, case, command):
    repo, remotes = mutation_repo
    assert hook(repo, command) is None, case
    oracle = subprocess.run(["bash", "--noprofile", "--norc", "-c", command],
                            cwd=repo, capture_output=True, text=True, timeout=15)
    assert oracle.returncode == 0, (case, oracle.stderr)
    assert all(NS + "/fixture" not in refs(bare) for bare in remotes.values())
