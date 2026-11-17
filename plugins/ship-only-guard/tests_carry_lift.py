"""Shallow cuts whose parents are all present are lifted (t_01a1e3427f6574fc).

The carry fetches with a bounded --depth, so git records each boundary commit in the temp repo's `shallow`
file and grafts its parents away. When state moved more commits past a PR's merge base than that depth,
`_CarryObjects._base` refused the carry ("fetch depth cuts history before the merge base"). It did so even
when a borrowed store (the command's cwd clone or a policy `reference_repos` clone) held every commit below
the cut, so each refresh of a long-lived state PR needed a fresh LLM review. A cut is now lifted only when
every parent named in its raw commit is present. A failed cut read or parent probe keeps that cut;
independently validated cuts can still lift. Failed shallow-file reads or rewrites keep the whole file.

These tests use real git throughout, replacing the network URL with the fixture repository and injecting
failures at the read, parent-probe and rewrite boundaries for the fail-closed cases.
`seen["fetched"]` is the premise: what the fetch itself cut, read before any lift. `seen["kept"]` is what
was still cut when the store closed.
"""
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests_carry import GIT_VERIFY, R, REAL_RUN, g, git_history, local_git_verifier  # noqa: F401

CUT = "fetch depth cuts history before the merge base"
SPAN = 140      # long-path length, past the 128-commit fetch depth (G19's shallow-cut case)
BOUNDARY = 13   # index of the long-path commit at depth 128 below the carry's tips; asserted per test


def _git(cwd, *args):
    env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
    return REAL_RUN(["git", "-c", "user.name=test", "-c", "user.email=test@example.com",
                     "-c", "core.hooksPath=/dev/null", *args], cwd=cwd, env=env,
                    capture_output=True, text=True, check=True).stdout.strip()


def _path(repo, start, label, count=SPAN, first=0, extra=None):
    """`count` commits above `start`, each keeping its parent's tree; `extra` maps an index to an additional
    (second) parent. One `git fast-import` writes them all, and its ref is then dropped, so they stay
    unreferenced like the fixture's commit-tree commits. Dates are now, never older than their parents."""
    stamp = b"committer test <test@example.com> %d +0000" % int(time.time())
    script = []
    for n, i in enumerate(range(first, first + count), 1):
        msg = f"{label} {i}".encode()
        script += [b"commit refs/lift/path", b"mark :%d" % n, stamp, b"data %d" % len(msg), msg,
                   b"from " + (start.encode() if n == 1 else b":%d" % (n - 1))]
        if extra and i in extra:
            script.append(b"merge " + extra[i].encode())
        script.append(b"")
    marks = Path(repo, ".git", "lift-marks")
    env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
    REAL_RUN(["git", "fast-import", "--quiet", f"--export-marks={marks}"], input=b"\n".join(script) + b"\n",
             cwd=repo, env=env, capture_output=True, check=True)
    shas = dict(line.split() for line in marks.read_text().splitlines())
    marks.unlink()
    _git(repo, "update-ref", "-d", "refs/lift/path")
    return [shas[f":{n}"] for n in range(1, count + 1)]


def _carry(repo, approved, theirs, tips):
    """The PR joins its paths and a one-commit short path (which exposes the merge base), then merges state."""
    tree = _git(repo, "rev-parse", approved + "^{tree}")
    short = _git(repo, "commit-tree", tree, "-p", approved, "-m", "short path")
    parents = [arg for tip in (*tips, short) for arg in ("-p", tip)]
    ours = _git(repo, "commit-tree", tree, *parents, "-m", "join paths")
    merged = _git(repo, "merge-tree", "--write-tree", ours, theirs)
    return _git(repo, "commit-tree", merged, "-p", ours, "-p", theirs, "-m", "clean carry"), ours, theirs


def _start(git_history):
    repo, heads = git_history
    _, approved, theirs = heads["clean"]
    return repo, approved, theirs, _git(repo, "rev-parse", approved + "^{tree}")


def _clone(tmp_path, repo, name):
    """A local clone copies every object the fixture holds now (its commits are dangling commit-tree ones)."""
    store = tmp_path / name
    _git(tmp_path, "clone", "-q", str(repo), str(store))
    return store


def _watch(monkeypatch, tmp_path, repo):
    """Real-git verifier plus the cut lists: right after the fetch, and when the store closes."""
    calls = local_git_verifier(monkeypatch, tmp_path, repo)
    seen = {"fetched": [], "kept": []}
    run = g.subprocess.run

    def cuts(path):
        shallow = Path(path, "shallow")
        with open(shallow) if shallow.exists() else open(os.devnull) as f:
            return sorted(f.read().split())

    def spying(argv, **kw):
        result = run(argv, **kw)
        if "fetch" in argv and result.returncode == 0:
            seen["fetched"].append(cuts(kw["cwd"]))
        return result
    monkeypatch.setattr(g.subprocess, "run", spying)
    close = g._CarryObjects.close

    def closing(self):
        if self.path is not None:
            seen["kept"].append(cuts(self.path))
        close(self)
    monkeypatch.setattr(g._CarryObjects, "close", closing)
    return calls, seen


def _env(tmp_path, reference_repos=None):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    if reference_repos is not None:
        (home / "ship-only-guard.json").write_text(json.dumps({"repos": [R], "reference_repos": reference_repos}))
    return {"HERMES_HOME": str(home)}


@pytest.mark.parametrize("source", ["cwd", "policy"])
def test_store_holding_the_long_path_lifts_the_cut(monkeypatch, tmp_path, git_history, source):
    """Test 1: the store holds the cut's parent, so the carry proves the deep merge."""
    repo, approved, theirs, tree = _start(git_history)
    long = _path(repo, approved, "long path")
    merge, ours, theirs = _carry(repo, approved, theirs, [long[-1]])
    calls, seen = _watch(monkeypatch, tmp_path, repo)
    env = _env(tmp_path, [str(repo)] if source == "policy" else None)
    why = GIT_VERIFY(merge, ours, theirs, R, str(repo) if source == "cwd" else None, env)
    assert seen["fetched"] == [[long[BOUNDARY]]]        # premise: the fetch cut the long path
    assert why == ""
    assert seen["kept"] == [[]]                          # lifted: no `shallow` left
    fetches = [argv for argv, _ in calls if "fetch" in argv]
    assert len(fetches) == 1 and "--depth=128" in fetches[0]


def test_without_a_store_the_cut_stays(monkeypatch, tmp_path, git_history):
    """Test 2: no store, so the cut's parent is absent and the refusal is unchanged (G19 shallow-cut)."""
    repo, approved, theirs, tree = _start(git_history)
    long = _path(repo, approved, "long path")
    merge, ours, theirs = _carry(repo, approved, theirs, [long[-1]])
    _, seen = _watch(monkeypatch, tmp_path, repo)
    assert GIT_VERIFY(merge, ours, theirs, R, None, _env(tmp_path)) == CUT
    assert seen["fetched"] == seen["kept"] == [[long[BOUNDARY]]]


def test_store_cloned_before_the_long_path_keeps_the_cut(monkeypatch, tmp_path, git_history):
    """Test 3: a store that lacks the cut's parent (a clone taken before the long path was built)."""
    repo, approved, theirs, tree = _start(git_history)
    stale = _clone(tmp_path, repo, "stale")
    long = _path(repo, approved, "long path")
    merge, ours, theirs = _carry(repo, approved, theirs, [long[-1]])
    _, seen = _watch(monkeypatch, tmp_path, repo)
    assert GIT_VERIFY(merge, ours, theirs, R, str(stale), _env(tmp_path)) == CUT
    assert seen["fetched"] == seen["kept"] == [[long[BOUNDARY]]]


def test_only_cuts_whose_parents_are_present_are_lifted(monkeypatch, tmp_path, git_history):
    """Test 3b: two cut paths; the store holds one. That cut is lifted, the other stays and refuses."""
    repo, approved, theirs, tree = _start(git_history)
    held = _path(repo, approved, "held path")
    store = _clone(tmp_path, repo, "store")
    new = _path(repo, approved, "new path")
    merge, ours, theirs = _carry(repo, approved, theirs, [held[-1], new[-1]])
    _, seen = _watch(monkeypatch, tmp_path, repo)
    assert GIT_VERIFY(merge, ours, theirs, R, str(store), _env(tmp_path)) == CUT
    assert seen["fetched"] == [sorted([held[BOUNDARY], new[BOUNDARY]])]
    assert seen["kept"] == [[new[BOUNDARY]]]


@pytest.mark.parametrize("side_held", [False, True], ids=["one-parent-absent", "both-parents-present"])
def test_merge_commit_cut_needs_every_parent(monkeypatch, tmp_path, git_history, side_held):
    """Test 4: the cut is a merge whose first parent the store holds; its second parent decides."""
    repo, approved, theirs, tree = _start(git_history)
    low = _path(repo, approved, "long path", count=BOUNDARY)
    if not side_held:
        store = _clone(tmp_path, repo, "store")
    side = _git(repo, "commit-tree", tree, "-p", approved, "-m", "side parent")
    if side_held:
        store = _clone(tmp_path, repo, "store")
    high = _path(repo, low[-1], "long path", count=SPAN - BOUNDARY, first=BOUNDARY,
                 extra={BOUNDARY: side})
    long = low + high
    merge, ours, theirs = _carry(repo, approved, theirs, [long[-1]])
    _, seen = _watch(monkeypatch, tmp_path, repo)
    why = GIT_VERIFY(merge, ours, theirs, R, str(store), _env(tmp_path))
    assert seen["fetched"] == [[long[BOUNDARY]]]        # premise: the merge commit is the cut
    assert _git(repo, "rev-parse", long[BOUNDARY] + "^2") == side
    assert why == ("" if side_held else CUT)
    assert seen["kept"] == [[] if side_held else [long[BOUNDARY]]]


def test_resolvable_nonhex_parent_keeps_cut_and_refuses(tmp_path, git_history):
    """Even a resolvable revision is not a valid raw parent object ID."""
    repo, approved, theirs, tree = _start(git_history)
    _git(repo, "update-ref", "refs/heads/lift-parent", approved)
    raw = (f"tree {tree}\nparent refs/heads/lift-parent\n"
           "author test <test@example.com> 1700000000 +0000\n"
           "committer test <test@example.com> 1700000000 +0000\n\nmalformed parent\n")
    source = tmp_path / "raw-commit"
    source.write_text(raw)
    cut = _git(repo, "hash-object", "-t", "commit", "-w", "--literally", str(source))
    assert _git(repo, "cat-file", "commit", cut) == raw.strip()
    assert _git(repo, "cat-file", "-e", "refs/heads/lift-parent^{commit}") == ""
    # Git rejects a nonhex parent even while traversing a shallow commit. Keep
    # that object off the traversed path, alongside a syntactically valid cut
    # with an absent parent, to also exercise the real carry's exact refusal.
    absent = "f" * 40
    with pytest.raises(g.subprocess.CalledProcessError):
        _git(repo, "cat-file", "-e", absent + "^{commit}")
    source.write_text(raw.replace("refs/heads/lift-parent", absent))
    relevant = _git(repo, "hash-object", "-t", "commit", "-w", "--literally", str(source))
    shallow = Path(repo, ".git", "shallow")
    cuts = cut + "\n" + relevant + "\n"
    shallow.write_text(cuts)
    # Seed the store directly: fetch/fsck rejects the malformed object before
    # the defence-in-depth parent validator ever sees it.
    merge, ours, theirs = _carry(repo, approved, theirs, [relevant])
    objects = g._CarryObjects(R, _env(tmp_path), (merge, ours, theirs))
    objects.path = str(shallow.parent)
    try:
        objects._lift_cuts()
        assert shallow.exists(), "a nonhex parent must not lift the cut"
        assert shallow.read_text() == cuts
        assert objects.verify(merge, ours, theirs) == CUT
    finally:
        objects.close()


def test_parent_line_in_message_does_not_keep_cut(tmp_path, git_history):
    """Only header parents matter; a message can name an absent commit."""
    repo, approved, theirs, tree = _start(git_history)
    absent = "f" * 40
    with pytest.raises(g.subprocess.CalledProcessError):
        _git(repo, "cat-file", "-e", absent + "^{commit}")
    cut = _git(repo, "commit-tree", tree, "-p", approved,
               "-m", "message, not a header\n\nparent " + absent)
    assert "\n\nmessage, not a header\n\nparent " + absent in _git(repo, "cat-file", "commit", cut)
    assert _git(repo, "cat-file", "-e", approved + "^{commit}") == ""
    merge, ours, theirs = _carry(repo, approved, theirs, [cut])
    shallow = Path(repo, ".git", "shallow")
    shallow.write_text(cut + "\n")
    objects = g._CarryObjects(R, _env(tmp_path), (merge, ours, theirs))
    objects.path = str(shallow.parent)
    try:
        assert objects.verify(merge, ours, theirs) == CUT
        objects._lift_cuts()
        assert not shallow.exists(), "message lines must not become parent requirements"
        assert objects.verify(merge, ours, theirs) == ""
    finally:
        objects.close()


def test_parentless_root_cut_is_lifted(tmp_path, git_history):
    """A root has no missing parents, so its sole shallow entry is removed."""
    repo, approved, theirs, tree = _start(git_history)
    root = _git(repo, "commit-tree", tree, "-m", "parentless root")
    assert _git(repo, "show", "-s", "--format=%P", root) == ""
    shallow = Path(repo, ".git", "shallow")
    shallow.write_text(root + "\n")
    objects = g._CarryObjects(R, _env(tmp_path), (root,))
    objects.path = str(shallow.parent)
    try:
        objects._lift_cuts()
        assert not shallow.exists(), "a parentless root must not stay cut"
    finally:
        objects.close()


def _fail_lift_read(monkeypatch):
    read = Path.read_text

    def failing(self, *a, **kw):
        if self.name == "shallow" and sys._getframe(1).f_code.co_name == "_lift_cuts":
            raise PermissionError(13, "injected: shallow unreadable", str(self))
        return read(self, *a, **kw)
    monkeypatch.setattr(Path, "read_text", failing)


def _fail_os(monkeypatch, name):
    # Python 3.9 caches os.unlink in pathlib's accessor: patch the invoked
    # Path method, not os.unlink, so the failure is injected on every version.
    target = Path if name == "unlink" else g.os
    real = getattr(target, name)

    def failing(*args, **kwargs):
        if str(args[-1] if name == "replace" else args[0]).endswith(os.sep + "shallow"):
            raise PermissionError(13, f"injected: {name} refused", str(args[-1]))
        return real(*args, **kwargs)
    monkeypatch.setattr(target, name, failing)


def _fail_parent_probes(monkeypatch):
    run = g.subprocess.run

    def failing(argv, **kw):
        if "cat-file" in argv:
            return SimpleNamespace(returncode=128, stdout=b"" if not kw.get("text") else "", stderr="")
        return run(argv, **kw)
    monkeypatch.setattr(g.subprocess, "run", failing)


@pytest.mark.parametrize("failure", ["read", "unlink", "parent-probe"])
def test_failed_lift_leaves_the_cut_and_the_refusal(monkeypatch, tmp_path, git_history, failure):
    """Test 5: the store holds every parent, so only the injected failure keeps the cut (fail closed)."""
    repo, approved, theirs, tree = _start(git_history)
    long = _path(repo, approved, "long path")
    merge, ours, theirs = _carry(repo, approved, theirs, [long[-1]])
    _, seen = _watch(monkeypatch, tmp_path, repo)
    {"read": _fail_lift_read, "unlink": lambda m: _fail_os(m, "unlink"),
     "parent-probe": _fail_parent_probes}[failure](monkeypatch)
    assert GIT_VERIFY(merge, ours, theirs, R, str(repo), _env(tmp_path)) == CUT
    assert seen["fetched"] == seen["kept"] == [[long[BOUNDARY]]]


def test_failed_rewrite_keeps_every_cut(monkeypatch, tmp_path, git_history):
    """Test 5b: a partial lift whose atomic rewrite fails leaves `shallow` whole, never deleted."""
    repo, approved, theirs, tree = _start(git_history)
    held = _path(repo, approved, "held path")
    store = _clone(tmp_path, repo, "store")
    new = _path(repo, approved, "new path")
    merge, ours, theirs = _carry(repo, approved, theirs, [held[-1], new[-1]])
    _, seen = _watch(monkeypatch, tmp_path, repo)
    _fail_os(monkeypatch, "replace")
    assert GIT_VERIFY(merge, ours, theirs, R, str(store), _env(tmp_path)) == CUT
    assert seen["fetched"] == seen["kept"] == [sorted([held[BOUNDARY], new[BOUNDARY]])]


def test_chain_carry_lifts_the_cut_and_matches_the_merge_base(monkeypatch, tmp_path, git_history):
    """The chain store (head-only fetch, depth 131) lifts the same way, and the prefilter's local merge base
    then equals git's on the full history instead of falling back to gh."""
    repo, approved, theirs, tree = _start(git_history)
    long = _path(repo, approved, "long path", count=SPAN + g._CARRY_MERGES)
    merge, ours, theirs = _carry(repo, approved, theirs, [long[-1]])
    _, seen = _watch(monkeypatch, tmp_path, repo)
    objects = g._CarryObjects(R, _env(tmp_path), (merge,), chain=True, cwd=str(repo))
    try:
        assert objects.fetch() == ""
        assert objects.verify(merge, ours, theirs) == ""
        assert objects.merge_base(ours, theirs) == _git(repo, "merge-base", ours, theirs)
    finally:
        objects.close()
    assert len(seen["fetched"]) == 1 and len(seen["fetched"][0]) == 1
    assert seen["kept"] == [[]]
