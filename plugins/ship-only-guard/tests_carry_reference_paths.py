"""Every carry entry point hands the command's cwd to its fetch; policy paths expand `~` (t_01a1dfacf4785131).

Mutation check of 111081de: dropping `cwd=cwd` from `_carried_approval` (the path a state-PR merge takes: one chain
fetch of the head) or from `_carry_one`'s own fetch failed no test, nor did dropping the `~` expansion of
`reference_repos` or the `_maybe_repo` filter. Losing the hand-off brings back the landing-host deadline silently:
verdicts stay identical, only the fetch grows from a few objects back to a ~20 MiB pack. Real git; only the network
URL is replaced by the fixture repository.
"""
import json

import pytest

from tests_carry import GIT_VERIFY, R, g, git_history, local_git_verifier, payload, review  # noqa: F401
from tests_carry_reference import _record_stores, reference  # noqa: F401


@pytest.mark.parametrize("owner", ["carried-approval", "carry-one"])
def test_chain_carry_entry_points_borrow_the_command_cwd(monkeypatch, tmp_path, git_history, reference, owner):
    repo, heads = git_history
    local_git_verifier(monkeypatch, tmp_path, repo)
    env = {"HERMES_HOME": str(tmp_path / "home")}
    merge, ours, theirs = heads["clean"]

    def chain(head, h0, repo_, cwd_, env_, objects):
        assert objects.fetch() == ""
        why = objects.verify(merge, ours, theirs)
        return (not why, why or h0)

    monkeypatch.setattr(g, "_carry_chain", chain)
    seen = _record_stores(monkeypatch)
    if owner == "carried-approval":
        data = payload([review(f"Independent exact-head review at {ours}. **Verdict: approve**", commit=ours)])
        assert g._carried_approval(merge, data, R, str(reference), env) == (True, ours)
    else:
        assert g._carry_one(merge, ours, False, R, str(reference), env) == (True, ours)
    objects = str((reference / ".git" / "objects").resolve())
    assert seen == [(objects + "\n", 0)]


def test_policy_reference_repos_expand_the_home_directory(monkeypatch, tmp_path):
    home = tmp_path / "user"
    hermes = home / ".hermes"
    hermes.mkdir(parents=True)
    (hermes / "ship-only-guard.json").write_text(json.dumps({"repos": [R], "reference_repos": ["~/code/state"]}))
    monkeypatch.setenv("HOME", str(home))
    assert g.load_reference_repos({"HERMES_HOME": str(hermes)}) == [str(home / "code" / "state")]


def test_a_cwd_that_cannot_be_a_repository_spawns_no_probe(monkeypatch, tmp_path, git_history):
    repo, heads = git_history
    local_git_verifier(monkeypatch, tmp_path, repo)
    plain = tmp_path / "plain"
    plain.mkdir()
    argvs = []
    run = g._run_probe

    def recording(argv, *a, **k):
        argvs.append([str(x) for x in argv])
        return run(argv, *a, **k)

    monkeypatch.setattr(g, "_run_probe", recording)
    assert GIT_VERIFY(*heads["clean"], R, str(plain), {"HERMES_HOME": str(tmp_path / "home")}) == ""
    assert argvs and not [a for a in argvs if "--git-common-dir" in a]
