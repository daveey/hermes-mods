"""The carry fetch borrows validated local object stores (t_01a1dfacf4785131).

On the landing host the guard's shallow full-blob fetch of a state PR head took 16-44 s (a ~20 MiB pack GitHub
builds per request), so every state-PR merge preflight ran out of its 18 s budget in the carry stages. The fetch
now borrows the command's cwd clone (or a host-policy `reference_repos` clone) as `objects/info/alternates`:
same URL, shas and depth, but only objects the clone lacks move. These cases pin that bound (0 objects fetched
when the reference holds everything), that verdicts are unchanged, and that odd references are ignored.
Real git throughout; only the network URL is replaced by the fixture repository.
"""
import json
import subprocess
import time

import pytest

from tests_carry import GIT_VERIFY, R, REAL_RUN, g, git_history, local_git_verifier  # noqa: F401


def _git(cwd, *args):
    return REAL_RUN(["git", "-c", "core.hooksPath=/dev/null", *args], cwd=cwd,
                    capture_output=True, text=True, check=True).stdout.strip()


def _record_stores(monkeypatch):
    """Per closed _CarryObjects: (alternates text or None, locally fetched object count)."""
    seen = []
    close = g._CarryObjects.close

    def recording(self):
        if self.path is not None:
            alt = g.Path(self.path, "objects", "info", "alternates")
            counts = dict(line.split(": ") for line in _git(self.path, "count-objects", "-v").splitlines())
            seen.append((alt.read_text() if alt.exists() else None,
                         int(counts["count"]) + int(counts["in-pack"])))
        close(self)
    monkeypatch.setattr(g._CarryObjects, "close", recording)
    return seen


@pytest.fixture
def reference(tmp_path, git_history):
    repo, _ = git_history
    clone = tmp_path / "reference"
    REAL_RUN(["git", "clone", "-q", str(repo), str(clone)], check=True, capture_output=True)
    return clone


def _verdicts(heads, cwd, env):
    return {name: GIT_VERIFY(*heads[name], R, cwd, env) for name in ("clean", "moved", "conflict")}


def test_reference_cwd_fetches_nothing_and_keeps_every_verdict(monkeypatch, tmp_path, git_history, reference):
    repo, heads = git_history
    local_git_verifier(monkeypatch, tmp_path, repo)
    env = {"HERMES_HOME": str(tmp_path / "home")}
    seen = _record_stores(monkeypatch)
    baseline = _verdicts(heads, None, env)
    assert [alt for alt, _ in seen] == [None] * 3 and all(n > 0 for _, n in seen)
    seen.clear()
    assert _verdicts(heads, str(reference), env) == baseline
    assert baseline["clean"] == "" and "tree differs" in baseline["moved"] and "merge-tree" in baseline["conflict"]
    objects = str((reference / ".git" / "objects").resolve())
    # The bound: with the reference holding every object, the fetch moves none.
    assert seen == [(objects + "\n", 0)] * 3


def test_reference_chain_fetch_borrows_from_cwd(monkeypatch, tmp_path, git_history, reference):
    repo, heads = git_history
    calls = local_git_verifier(monkeypatch, tmp_path, repo)
    merge, ours, theirs = heads["clean"]
    env = {"HERMES_HOME": str(tmp_path / "home")}
    seen = _record_stores(monkeypatch)
    objects = g._CarryObjects(R, env, (merge,), chain=True, cwd=str(reference))
    try:
        assert objects.fetch() == ""
        assert objects.verify(merge, ours, theirs) == ""
        assert objects.merge_base(ours, theirs) == _git(repo, "merge-base", ours, theirs)
    finally:
        objects.close()
    assert seen[0][1] == 0
    fetches = [argv for argv, _ in calls if "fetch" in argv]
    assert len(fetches) == 1 and f"--depth={g._FETCH_DEPTH + g._CARRY_MERGES}" in fetches[0]


def test_linked_worktree_cwd_borrows_common_objects(monkeypatch, tmp_path, git_history, reference):
    repo, heads = git_history
    local_git_verifier(monkeypatch, tmp_path, repo)
    wt = tmp_path / "linked"
    _git(reference, "worktree", "add", "-q", "--detach", str(wt))
    seen = _record_stores(monkeypatch)
    assert GIT_VERIFY(*heads["clean"], R, str(wt), {"HERMES_HOME": str(tmp_path / "home")}) == ""
    assert seen == [(str((reference / ".git" / "objects").resolve()) + "\n", 0)]


@pytest.mark.parametrize("kind", ["shallow", "not-a-repo", "missing", "probe-timeout", "probe-failed"])
def test_unusable_reference_is_ignored(monkeypatch, tmp_path, git_history, reference, kind):
    repo, heads = git_history
    local_git_verifier(monkeypatch, tmp_path, repo)
    cwd = {"shallow": tmp_path / "shallow", "not-a-repo": tmp_path / "plain",
           "missing": tmp_path / "absent"}.get(kind, reference)
    if kind == "shallow":
        REAL_RUN(["git", "clone", "-q", "--depth=1", "file://" + str(repo), str(cwd)],
                 check=True, capture_output=True)
    if kind == "not-a-repo":
        cwd.mkdir()
    if kind.startswith("probe-"):
        run = g.subprocess.run

        def broken(argv, **kw):
            if "--is-shallow-repository" in argv:
                if kind == "probe-timeout":
                    raise subprocess.TimeoutExpired(argv, kw["timeout"])
                return subprocess.CompletedProcess(argv, 128, "", "boom")
            return run(argv, **kw)
        monkeypatch.setattr(g.subprocess, "run", broken)
    env = {"HERMES_HOME": str(tmp_path / "home")}
    seen = _record_stores(monkeypatch)
    assert _verdicts(heads, str(cwd), env)["clean"] == ""
    assert seen and all(alt is None and n > 0 for alt, n in seen)


def test_policy_reference_repos_used_when_cwd_is_not_a_clone(monkeypatch, tmp_path, git_history, reference):
    repo, heads = git_history
    local_git_verifier(monkeypatch, tmp_path, repo)
    home = tmp_path / "home"
    home.mkdir()
    (home / "ship-only-guard.json").write_text(json.dumps(
        {"repos": [R], "reference_repos": [str(tmp_path / "absent"), str(reference)]}))
    plain = tmp_path / "plain"
    plain.mkdir()
    seen = _record_stores(monkeypatch)
    assert GIT_VERIFY(*heads["clean"], R, str(plain), {"HERMES_HOME": str(home)}) == ""
    assert seen == [(str((reference / ".git" / "objects").resolve()) + "\n", 0)]


def test_bare_policy_reference_is_borrowed(monkeypatch, tmp_path, git_history):
    repo, heads = git_history
    local_git_verifier(monkeypatch, tmp_path, repo)
    bare = tmp_path / "ref.git"
    REAL_RUN(["git", "clone", "-q", "--bare", str(repo), str(bare)], check=True, capture_output=True)
    home = tmp_path / "home"
    home.mkdir()
    (home / "ship-only-guard.json").write_text(json.dumps({"repos": [R], "reference_repos": [str(bare)]}))
    seen = _record_stores(monkeypatch)
    assert GIT_VERIFY(*heads["clean"], R, None, {"HERMES_HOME": str(home)}) == ""
    assert seen == [(str((bare / "objects").resolve()) + "\n", 0)]


@pytest.mark.parametrize("value", ['{"repos": [], "reference_repos": "x"}', '{"reference_repos": [1]}',
                                   '{"reference_repos": [""]}', "[]", "not json"])
def test_odd_policy_reference_repos_yield_none(tmp_path, value):
    (tmp_path / "ship-only-guard.json").write_text(value)
    assert g.load_reference_repos({"HERMES_HOME": str(tmp_path)}) == []


def test_deadline_during_reference_probe_still_fails_closed(monkeypatch, tmp_path, git_history, reference):
    """An expired preflight during the probe is never swallowed as 'no reference': it raises, and no fetch runs."""
    repo, heads = git_history
    calls = local_git_verifier(monkeypatch, tmp_path, repo)
    run = g.subprocess.run

    def slow(argv, **kw):
        if "--is-shallow-repository" in argv:
            time.sleep(kw["timeout"] + 0.05)
            raise subprocess.TimeoutExpired(argv, kw["timeout"])
        return run(argv, **kw)
    monkeypatch.setattr(g.subprocess, "run", slow)
    token = g._PREFLIGHT_DEADLINE.set(time.monotonic() + 0.3)
    try:
        with pytest.raises(g._PreflightExpired):
            GIT_VERIFY(*heads["clean"], R, str(reference), {"HERMES_HOME": str(tmp_path / "home")})
    finally:
        g._PREFLIGHT_DEADLINE.reset(token)
    assert not any("fetch" in argv for argv, _ in calls)


@pytest.mark.parametrize("checkout", [False, True])
def test_partial_clone_reference_is_not_borrowed(monkeypatch, tmp_path, git_history, checkout):
    """A blob:none partial clone advertises commits whose blobs it lacks (t_01a1de17dd481488).

    Borrowed as alternates, those commits become fetch "haves", so the server omits their blobs and the
    fetch dies `missing blob object` / `remote did not send all necessary objects`. On the hub the core
    checkout (a blob:none clone) was the observer's cwd; every carry then fell back to `gh api git/trees`
    reads larger than the observer's bound. A promisor store is skipped like a shallow one.
    """
    repo, heads = git_history
    _git(repo, "config", "uploadpack.allowFilter", "true")
    for i, sha in enumerate(sorted({s for trio in heads.values() for s in trio})):
        _git(repo, "update-ref", f"refs/heads/h{i}", sha)
    local_git_verifier(monkeypatch, tmp_path, repo)
    partial = tmp_path / "partial"
    REAL_RUN(["git", "clone", "-q", "--filter=blob:none", *([] if checkout else ["--no-checkout"]),
              "file://" + str(repo), str(partial)], check=True, capture_output=True)
    env = {"HERMES_HOME": str(tmp_path / "home")}
    seen = _record_stores(monkeypatch)
    baseline = _verdicts(heads, None, env)
    seen.clear()
    assert _verdicts(heads, str(partial), env) == baseline
    assert seen and all(alt is None and n > 0 for alt, n in seen)


def test_partial_clone_policy_reference_skipped_but_full_one_borrowed(monkeypatch, tmp_path, git_history,
                                                                      reference):
    repo, heads = git_history
    _git(repo, "config", "uploadpack.allowFilter", "true")
    local_git_verifier(monkeypatch, tmp_path, repo)
    partial = tmp_path / "partial"
    REAL_RUN(["git", "clone", "-q", "--filter=blob:none", "--no-checkout", "file://" + str(repo), str(partial)],
             check=True, capture_output=True)
    home = tmp_path / "home"
    home.mkdir()
    (home / "ship-only-guard.json").write_text(json.dumps(
        {"repos": [R], "reference_repos": [str(partial), str(reference)]}))
    seen = _record_stores(monkeypatch)
    assert GIT_VERIFY(*heads["clean"], R, str(partial), {"HERMES_HOME": str(home)}) == ""
    assert seen == [(str((reference / ".git" / "objects").resolve()) + "\n", 0)]
