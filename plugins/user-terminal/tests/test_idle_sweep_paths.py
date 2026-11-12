"""Steady sweep enumeration: one directory scan and prebuilt paths, same signatures.

A host keeps every settled job directory (hundreds after a few weeks). The sweep's
memo already skipped their inspection, but each 15 s pass still built a ``Path`` per
state file and ran ``Path.is_dir``/``Path.stat`` for every one of them. These tests pin
the cheap form and prove it enumerates and signs exactly what the pathlib form did.

All jobs are synthetic and ownerless (no board, no core needed).
"""
import importlib.util
import json
import os
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "__init__.py"
spec = importlib.util.spec_from_file_location("user_terminal_sweep_paths", SOURCE)
ut = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ut)


@pytest.fixture
def root(tmp_path, monkeypatch):
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    monkeypatch.setenv("HERMES_USER_TERMINAL_DIR", str(jobs))
    ut._SWEEP_FINAL.clear()
    getattr(ut, "_SWEEP_PATHS", {}).clear()
    yield jobs
    ut._SWEEP_FINAL.clear()
    getattr(ut, "_SWEEP_PATHS", {}).clear()


def final_job(root: Path, name: str, status: str = "ran") -> Path:
    job = root / name
    (job / "final.lock").mkdir(parents=True)
    (job / "status.json").write_text(json.dumps({"status": status}))
    (job / "meta.json").write_text(json.dumps({"command": "true", "board_db": None}))
    (job / "final.lock" / "result.json").write_text(json.dumps({"status": status}))
    return job


def reference(root: Path):
    """The enumeration and signatures the sweep used before the prebuilt paths."""
    jobs = [p for p in root.iterdir() if p.is_dir()]
    return jobs, {job: tuple(ut._sweep_stat(job / name) for name in ut._SWEEP_FILES) for job in jobs}


def pathlib_calls(monkeypatch, root: Path) -> list:
    """Record every Path.stat / is_dir / iterdir under the jobs root."""
    calls: list = []
    for method in ("stat", "is_dir", "iterdir"):
        original = getattr(Path, method)

        def spy(self, *a, _original=original, _method=method, **kw):
            if str(self).startswith(str(root)):
                calls.append((_method, str(self)))
            return _original(self, *a, **kw)
        monkeypatch.setattr(Path, method, spy)
    return calls


def test_steady_pass_builds_no_paths_and_stats_each_state_file_once(root, monkeypatch):
    jobs = [final_job(root, f"job-{i:03d}") for i in range(40)]
    ut.sweep_jobs()
    assert set(ut._SWEEP_FINAL) == set(jobs)
    calls = pathlib_calls(monkeypatch, root)
    stats: list = []
    original = ut._sweep_stat
    monkeypatch.setattr(ut, "_sweep_stat", lambda p: (stats.append(p), original(p))[1])
    read = []
    monkeypatch.setattr(ut, "read_status", lambda job: read.append(job) or {"status": "ran"})
    ut.sweep_jobs()
    assert calls == []  # parent: 40 is_dir + 320 Path.stat + 1 iterdir per pass
    assert read == []
    assert sorted(stats) == sorted(os.path.join(str(job), name) for job in jobs for name in ut._SWEEP_FILES)
    assert all(type(p) is str for p in stats)


def test_enumeration_and_signatures_equal_the_pathlib_form(root):
    final_job(root, "plain")
    final_job(root, "target")
    (root / "a-file").write_text("not a job")
    os.symlink(root / "target", root / "link-to-job")
    os.symlink(root / "missing", root / "dangling")
    (root / "empty-dir").mkdir()
    jobs, files = ut._sweep_enumerate(root)
    expected_jobs, expected = reference(root)
    assert jobs == expected_jobs  # same Path objects' values, same directory order
    assert all(type(job) is type(root) for job in jobs)
    assert {job: tuple(ut._sweep_stat(p) for p in paths) for job, paths in files.items()} == expected
    assert {job: paths for job, paths in files.items()} == {
        job: tuple(str(job / name) for name in ut._SWEEP_FILES) for job in expected_jobs}
    # ... and again from the cached paths
    assert ut._sweep_enumerate(root)[0] == expected_jobs


def test_changes_are_seen_on_the_next_pass(root, monkeypatch):
    a, b = final_job(root, "a"), final_job(root, "b")
    ut.sweep_jobs()
    ut.sweep_jobs()
    inspected = []
    real = ut.read_status
    monkeypatch.setattr(ut, "read_status", lambda job: (inspected.append(job), real(job))[1])
    ut.sweep_jobs()
    assert inspected == []
    # a state file rewritten in place
    (a / "status.json").write_text(json.dumps({"status": "ran", "exit_code": 3}))
    ut.sweep_jobs()
    assert inspected == [a]
    inspected.clear()
    # a sidecar appearing
    (b / "attachment.json").write_text("{}")
    ut.sweep_jobs()
    assert inspected == [b]
    inspected.clear()
    # a new job, and a removed one (memo and path cache both forget it)
    c = final_job(root, "c")
    for path in sorted(a.rglob("*"), reverse=True):
        path.rmdir() if path.is_dir() else path.unlink()
    a.rmdir()
    ut.sweep_jobs()
    assert inspected == [c]
    assert set(ut._SWEEP_FINAL) == {b, c}
    assert set(ut._SWEEP_PATHS) == {"b", "c"}
    # the removed name coming back is a new job, inspected again
    inspected.clear()
    final_job(root, "a")
    ut.sweep_jobs()
    assert inspected == [a]


def test_same_job_name_under_another_root_never_reuses_paths(root, tmp_path, monkeypatch):
    job = final_job(root, "same-name")
    ut.sweep_jobs()
    other = tmp_path / "other-jobs"
    other.mkdir()
    twin = final_job(other, "same-name")
    monkeypatch.setenv("HERMES_USER_TERMINAL_DIR", str(other))
    jobs, files = ut._sweep_enumerate(other)
    assert jobs == [twin]
    assert files[twin] == tuple(str(twin / name) for name in ut._SWEEP_FILES)
    inspected = []
    real = ut.read_status
    monkeypatch.setattr(ut, "read_status", lambda j: (inspected.append(j), real(j))[1])
    ut.sweep_jobs()
    assert inspected == [twin]
    assert job in ut._SWEEP_FINAL and twin in ut._SWEEP_FINAL  # the other root's memo is not pruned


def test_unreadable_entry_is_not_a_job(root, monkeypatch):
    final_job(root, "ok")

    class Broken:
        name = "broken"

        def is_dir(self):
            raise PermissionError("injected")
    assert ut._sweep_entry_is_dir(Broken()) is False
    assert [j.name for j in ut._sweep_enumerate(root)[0]] == ["ok"]


def test_missing_root_is_quiet(root):
    root.rmdir()
    ut.sweep_jobs()  # no exception, nothing remembered
    assert ut._SWEEP_FINAL == {}


def test_skip_still_matches_the_enumerated_job(root, monkeypatch):
    a, b = final_job(root, "a"), final_job(root, "b")
    inspected = []
    real = ut.read_status
    monkeypatch.setattr(ut, "read_status", lambda job: (inspected.append(job), real(job))[1])
    ut.sweep_jobs(skip=a)
    assert inspected == [b] and set(ut._SWEEP_FINAL) == {b}
    monkeypatch.chdir(root)
    ut.sweep_jobs(skip=Path("b"))  # relative skip is compared as an absolute path
    assert inspected == [b, a]
