"""The bounded carry fetch must not auto-follow source tags (G20)."""
import importlib.util
import os
import subprocess
from pathlib import Path


spec = importlib.util.spec_from_file_location("carry_no_tags_guard", Path(__file__).with_name("guard.py"))
assert spec is not None and spec.loader is not None
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)
REAL_RUN = subprocess.run


def test_clean_merge_fetch_excludes_source_tags(monkeypatch, tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_AUTHOR_DATE="2026-01-01T00:00:00Z", GIT_COMMITTER_DATE="2026-01-01T00:00:00Z")

    def git(*args, cwd=source):
        return REAL_RUN(["git", "-c", "core.hooksPath=" + os.devnull,
                         "-c", "user.name=test", "-c", "user.email=test@example.com", *args],
                        cwd=cwd, env=env, capture_output=True, text=True, check=True, timeout=30).stdout.strip()

    git("init", "--template=", "-q")
    (source / "base").write_text("base\n")
    git("add", ".")
    git("commit", "-qm", "base")
    base = git("rev-parse", "HEAD")
    (source / "ours").write_text("reviewed\n")
    git("add", ".")
    git("commit", "-qm", "ours")
    ours = git("rev-parse", "HEAD")
    git("checkout", "-q", "--detach", base)
    (source / "theirs").write_text("state\n")
    git("add", ".")
    git("commit", "-qm", "theirs")
    theirs = git("rev-parse", "HEAD")
    tree = git("merge-tree", "--write-tree", ours, theirs)
    merge = git("commit-tree", tree, "-p", ours, "-p", theirs, "-m", "clean merge")
    git("tag", "reviewed", ours)
    git("tag", "-a", "merged", merge, "-m", "annotated merge tag")
    source_tags = git("for-each-ref", "--format=%(refname)", "refs/tags").splitlines()
    assert source_tags == ["refs/tags/merged", "refs/tags/reviewed"]

    # Raw-SHA fetches do not auto-follow tags on every Git version/transport.
    # Check the real argv contract as well as refs, not incidental Git defaults.
    observed = []
    fetch_argv = []

    def local_fetch(argv, **kwargs):
        if "fetch" not in argv:
            return REAL_RUN(argv, **kwargs)
        remote = "https://github.com/example/protected.git"
        assert argv.count(remote) == 1
        fetch_argv.append(list(argv))
        # Only redirect transport; preserve all production fetch flags and refs.
        result = REAL_RUN([source.as_uri() if arg == remote else arg for arg in argv], **kwargs)
        assert result.returncode == 0, result.stderr
        observed.append(git("for-each-ref", "--format=%(refname)", "refs/tags", cwd=kwargs["cwd"]))
        return result

    monkeypatch.setattr(g.subprocess, "run", local_fetch)
    home = tmp_path / "home"
    assert g._git_merge_tree_refusal(merge, ours, theirs, "example/protected", None,
                                    {"HERMES_HOME": str(home)}) == ""
    assert observed == [""], "bounded scratch fetch must not import source tag refs"
    assert len(fetch_argv) == 1
    assert "--no-tags" in fetch_argv[0], "bounded scratch fetch must explicitly disable tag following"
    assert not list((home / "cache").iterdir())
