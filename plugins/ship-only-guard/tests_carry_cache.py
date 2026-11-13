"""Scratch location and lifetime must be observed, not inferred from an empty cache."""
from pathlib import Path

import pytest

from tests_carry import GIT_VERIFY, R, REAL_RUN, g, git_history, local_git_verifier


@pytest.mark.parametrize("outcome", ["clean", "moved", "failed", "timeout"])
def test_native_carry_child_cwd_is_in_home_cache_and_removed(
    monkeypatch, tmp_path, git_history, outcome
):
    repo, heads = git_history
    home = tmp_path / "caller-home"
    cache = (home / "cache").resolve()
    # A different ambient home prevents accidentally proving only the default location.
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "ambient-home"))
    calls = local_git_verifier(
        monkeypatch, tmp_path, repo,
        failure=outcome if outcome in ("failed", "timeout") else None,
    )
    run = g.subprocess.run
    child_dirs = []

    def observe(argv, **kwargs):
        result = run(argv, **kwargs)
        if "init" in argv and result.returncode == 0:
            # Ask a real child in the initialized bare repo, without mocking tempfile.
            probe = REAL_RUN(
                ["git", "rev-parse", "--absolute-git-dir"],
                cwd=kwargs["cwd"], env=kwargs["env"],
                capture_output=True, text=True, check=True, timeout=30,
            )
            child = Path(probe.stdout.strip()).resolve()
            assert child.is_dir()
            child_dirs.append(child)
        return result

    monkeypatch.setattr(g.subprocess, "run", observe)
    why = GIT_VERIFY(
        *heads["moved" if outcome == "moved" else "clean"],
        R, None, {"HERMES_HOME": str(home)},
    )
    expected = {
        "clean": "", "moved": "tree differs from the clean merge",
        "failed": "git fetch exited 128", "timeout": "TimeoutExpired",
    }[outcome]
    assert why == "" if outcome == "clean" else expected in why
    assert len(child_dirs) == 1, "must observe a real initialized scratch repository"
    child = child_dirs[0]
    assert child.parent == cache, "carry child cwd escaped HERMES_HOME/cache"
    assert all(Path(kw["cwd"]).resolve() == child for _, kw in calls)
    assert not child.exists(), "observed scratch repository leaked after verifier return"
    assert cache.is_dir()
    assert list(cache.iterdir()) == []
