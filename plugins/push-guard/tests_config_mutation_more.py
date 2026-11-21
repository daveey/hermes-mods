"""Further same-command config-invalidation regressions (include paths/scopes, recursive writers, controls).

Reuses the local-bare-remote fixture from tests_config_mutation; nothing here touches a network or live remote.
"""
import shlex

import pytest

from tests_config_mutation import hook, mutation_repo, refs  # noqa: F401  (fixture re-export)

MORE_UNSAFE = [
    ("include-path", "git config --file @R/inc.cfg remote.origin.mirror true && git config include.path @R/inc.cfg && git push origin"),
    ("include-path-global", "printf '[remote \"origin\"]\\n\\tmirror = true\\n' > @R/inc.cfg && git config --global include.path @R/inc.cfg && git push origin"),
    ("alias-body-writer", "git config alias.w '!git config remote.origin.mirror true' && git w && git push origin"),
    ("substitution-writer", "echo \"$(git config remote.origin.mirror true)\"; git push origin"),
    ("bash-c-both", "bash -c 'git config remote.origin.mirror true && git push origin'"),
    ("remote-add-mirror", "git remote add --mirror=push zz @R/../mir.git && git push zz"),
    ("unset-then-bare", "git config --unset remote.pushDefault; git config branch.feat.pushRemote mir && git push"),
    ("alias-after-unrelated-write", "git config user.name Test && git config alias.z 'push mir' && git z"),
]


@pytest.mark.parametrize("case,command", MORE_UNSAFE, ids=[c for c, _ in MORE_UNSAFE])
def test_more_config_mutation_forms_are_refused_and_not_executed(mutation_repo, case, command):
    repo, remotes = mutation_repo
    command = command.replace("@R", shlex.quote(str(repo)))
    config_before = (repo / ".git/config").read_bytes()
    decision = hook(repo, command)
    assert (repo / ".git/config").read_bytes() == config_before and not (repo / "inc.cfg").exists()
    assert all(not refs(bare) for bare in remotes.values())
    assert decision and decision.get("action") == "block", (case, command, decision)


MORE_CONTROLS = [
    ("read-list", "git config -l >/dev/null && git push origin feat"),
    ("read-scoped", "git config --global --get user.name; git push origin HEAD:refs/heads/feature"),
    ("write-then-explicit-branch", "git config user.name Test && git push origin HEAD:refs/heads/feature"),
    ("write-after-push", "git push origin HEAD:refs/heads/feature && git config remote.origin.mirror true"),
    ("write-then-status", "git config alias.z 'push mir' && git status --short"),
]


@pytest.mark.parametrize("case,command", MORE_CONTROLS, ids=[c for c, _ in MORE_CONTROLS])
def test_more_controls_remain_allowed(mutation_repo, case, command):
    repo, remotes = mutation_repo
    assert hook(repo, command) is None, case


FRESHNESS_UNSAFE = [
    ("explicit-custody-spec-after-mirror-write",
     "git config remote.origin.mirror true && git push origin refs/hermes-source-custody/fixture:refs/hermes-source-custody/fixture"),
    ("operand-then-read-flag-is-a-write", "git config remote.origin.mirror true --get && git push origin"),
]


@pytest.mark.parametrize("case,command", FRESHNESS_UNSAFE, ids=[c for c, _ in FRESHNESS_UNSAFE])
def test_explicit_specs_and_operand_order(mutation_repo, case, command):
    repo, remotes = mutation_repo
    decision = hook(repo, command)
    assert decision and decision.get("action") == "block", (case, decision)


def test_explicit_branch_spec_after_mirror_write_is_judged_on_text(mutation_repo):
    repo, _ = mutation_repo
    assert hook(repo, "git config remote.origin.mirror true && git push origin HEAD:refs/heads/feature") is None
