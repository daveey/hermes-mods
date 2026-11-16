"""ship-only-guard under Python 3.9 (t_01a1e2f9c67ad78b).

gh-shim ran the guard with Apple's /usr/bin/python3 3.9.6. The carry fetch passed `ignore_cleanup_errors`, which
only Python 3.10+ accepts. The preflight's catch-all then reported the resulting TypeError as "`gh pr view N`
returned an unexpected shape", so a policy-clean merge of #3391 was refused for an interpreter bug.
These cases run on any Python: they install a 3.9-signature TemporaryDirectory and report 3.9 to the guard.
"""
import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location("ship_py39_compat_guard", Path(__file__).with_name("guard.py"))
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)


class _Py39TemporaryDirectory(tempfile.TemporaryDirectory):
    """Python 3.9's signature: no ignore_cleanup_errors keyword."""

    def __init__(self, suffix=None, prefix=None, dir=None):
        super().__init__(suffix=suffix, prefix=prefix, dir=dir)


def test_carry_fetch_creates_its_cache_under_python39(monkeypatch, tmp_path):
    objs = g._CarryObjects("o/r", {"HERMES_HOME": str(tmp_path)}, ["a" * 40], chain=True)

    def git(*args, **kwargs):
        raise g._Unreadable(f"git {args[0]} exited 1")   # stop before any real git or network

    monkeypatch.setattr(objs, "git", git)
    monkeypatch.setattr(tempfile, "TemporaryDirectory", _Py39TemporaryDirectory)
    with monkeypatch.context() as m:
        m.setattr(sys, "version_info", (3, 9, 6, "final", 0))
        why = g._CarryObjects._fetch(objs)
    # Reaching the first git step proves the cache directory was created without a TypeError.
    assert why == "git init exited 1"
    assert objs.path and Path(objs.path).parent == tmp_path / "cache"
    objs.close()
    assert not Path(objs.path).exists()


def test_close_tolerates_a_failed_cache_cleanup(tmp_path):
    objs = g._CarryObjects("o/r", {"HERMES_HOME": str(tmp_path)}, ["a" * 40])

    class _Tmp:
        def cleanup(self):
            raise PermissionError(13, "Permission denied", str(tmp_path / "cache" / "ship-carry-x"))

    objs._tmp = _Tmp()
    objs.close()              # a leftover cache dir must never turn a finished check into a crash
    assert objs._settled.is_set()


def _view():
    return {
        "number": 1, "url": "https://github.com/o/r/pull/1", "state": "OPEN", "baseRefName": "state",
        "baseRefOid": "b" * 40, "headRefOid": "a" * 40,
        "statusCheckRollup": [{"__typename": "CheckRun", "name": "ci", "status": "COMPLETED",
                               "conclusion": "SUCCESS"}],
        "reviews": [], "comments": [], "files": [],
    }


@pytest.mark.parametrize("exc", [
    TypeError("__init__() got an unexpected keyword argument 'ignore_cleanup_errors'"),
    ValueError("stage value\nwith a second line"),
])
def test_a_stage_error_is_named_not_blamed_on_the_view(monkeypatch, tmp_path, exc):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout=json.dumps(_view()), stderr="")

    monkeypatch.setattr(g, "subprocess", SimpleNamespace(
        run=run, SubprocessError=g.subprocess.SubprocessError, TimeoutExpired=g.subprocess.TimeoutExpired))

    def stage(*args, **kwargs):
        raise exc

    monkeypatch.setattr(g, "_review_reply_refusal", stage)   # the first stage after the view's own checks
    refusal = g._state_merge(["pr", "merge", "1", "--repo", "o/r", "--rebase", "--match-head-commit", "a" * 40],
                             "o/r", str(tmp_path), {})
    assert isinstance(refusal, str) and "preflight failed" in refusal
    assert "unexpected shape" not in refusal
    detail = " ".join(f"{type(exc).__name__}: {exc}".split())
    assert detail[:100] in refusal
    step = refusal.split("preflight failed: ", 1)[1].split(". Fix that", 1)[0]
    assert "\n" not in step and len(step) <= 160
    assert len(calls) == 1 and calls[0][:3] == ["gh", "pr", "view"]
