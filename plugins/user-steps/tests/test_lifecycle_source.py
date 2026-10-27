"""Exact lifecycle source reads: isolated files, no Hermes runtime or live state."""
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "user_steps_lifecycle_source", Path(__file__).resolve().parents[1] / "core.py")
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)


@pytest.fixture
def ledger(tmp_path):
    return core.Ledger(tmp_path)


def seed(ledger, **overrides):
    data = {"root": "chat", "revision": 4,
            "steps": [{"id": "us_one", "status": "open"}]}
    data.update(overrides)
    ledger.path("chat").write_text(json.dumps(data))
    return data


def test_revision_new_migration_and_noop(ledger):
    with ledger.edit("chat") as d:
        d["steps"].append({"id": "us_one", "status": "open"})
    assert ledger._read("chat")["revision"] == 1
    with ledger.edit("chat"):
        pass
    assert ledger._read("chat")["revision"] == 2
    data = seed(ledger)
    del data["revision"]
    ledger.path("chat").write_text(json.dumps(data))
    with pytest.raises(ValueError):
        ledger.read_exact("chat", "us_one")
    with ledger.edit("chat"):
        pass
    assert ledger.read_exact("chat", "us_one")["revision"] == 1


def test_revision_owned_by_writer_and_abort_preserves_file(ledger):
    seed(ledger)
    with ledger.edit("chat") as d:
        d["revision"] = 999
    assert ledger._read("chat")["revision"] == 5
    before = ledger.path("chat").read_bytes()
    with pytest.raises(RuntimeError):
        with ledger.edit("chat") as d:
            d["steps"] = []
            raise RuntimeError("abort")
    assert ledger.path("chat").read_bytes() == before


@pytest.mark.parametrize("revision", [None, True, False, 0, -1, 1.5, "4", [], {}])
def test_invalid_revision_never_reset(ledger, revision):
    seed(ledger, revision=revision)
    before = ledger.path("chat").read_bytes()
    with pytest.raises(ValueError):
        with ledger.edit("chat"):
            pass
    with pytest.raises(ValueError):
        ledger.read_exact("chat", "us_one")
    assert ledger.path("chat").read_bytes() == before


@pytest.mark.parametrize("raw", [b"{", b"[]", b"null", b"\xff",
    b'{"root":"chat","revision":1,"revision":2,"steps":[]}',
    b'{"root":"chat","revision":1,"steps":null}',
    b'{"root":"chat","revision":1,"steps":[null]}'])
def test_corruption_not_overwritten(ledger, raw):
    ledger.path("chat").write_bytes(raw)
    with pytest.raises(ValueError):
        with ledger.edit("chat"):
            pass
    with pytest.raises(ValueError):
        ledger.read_exact("chat", "us_one")
    assert ledger.path("chat").read_bytes() == raw


def test_exact_snapshot_no_scan_or_lineage_and_detached(ledger, monkeypatch):
    data = seed(ledger)
    def forbidden(*args, **kwargs):
        pytest.fail("exact read must not scan or resolve lineage")
    monkeypatch.setattr(Path, "glob", forbidden)
    monkeypatch.setattr(ledger, "root", forbidden)
    monkeypatch.setattr(ledger, "find", forbidden)
    snapshot = ledger.read_exact("chat", "us_one")
    assert snapshot == {"root": "chat", "revision": 4, "step": data["steps"][0]}
    snapshot["step"]["status"] = "resolved"
    assert ledger.read_exact("chat", "us_one")["step"]["status"] == "open"
    with pytest.raises(FileNotFoundError):
        ledger.read_exact("elsewhere", "us_one")
    with pytest.raises(LookupError):
        ledger.read_exact("chat", "us_missing")


def test_mismatch_collision_and_duplicate_id_fail(ledger):
    seed(ledger, root="other")
    with pytest.raises(ValueError):
        ledger.read_exact("chat", "us_one")
    with pytest.raises(ValueError):
        with ledger.edit("chat"):
            pass
    with ledger.edit("a/b") as d:
        d["steps"].append({"id": "us_one"})
    assert ledger.path("a/b") == ledger.path("a_b")
    with pytest.raises(ValueError):
        ledger.read_exact("a_b", "us_one")
    seed(ledger, steps=[{"id": "us_one"}, {"id": "us_one"}])
    with pytest.raises(ValueError):
        ledger.read_exact("chat", "us_one")


def test_bounded_read_and_unreadable_fail(ledger, monkeypatch):
    seed(ledger)
    monkeypatch.setattr(core, "MAX_LEDGER_BYTES", 64)
    with pytest.raises(ValueError, match="size|large|limit"):
        ledger.read_exact("chat", "us_one")
    def denied(*args, **kwargs):
        raise PermissionError("denied")
    monkeypatch.setattr(core, "open", denied, raising=False)
    with pytest.raises(PermissionError):
        ledger.read_exact("chat", "us_one")


def test_failed_write_preserves_revision_and_cleans_temp(ledger, monkeypatch):
    seed(ledger)
    before = ledger.path("chat").read_bytes()
    def failed_sync(fd):
        raise OSError("disk failure")
    monkeypatch.setattr(core.os, "fsync", failed_sync)
    with pytest.raises(OSError):
        with ledger.edit("chat"):
            pass
    assert ledger.path("chat").read_bytes() == before
    assert not list(ledger.dir.glob("*.tmp"))


@pytest.mark.parametrize("operation", ["open", "fsync"])
def test_post_commit_directory_error_is_logged_not_false_abort(ledger, monkeypatch, caplog, operation):
    import stat
    seed(ledger)
    original_open, original_sync = core.os.open, core.os.fsync
    def open_dir(path, flags, *args, **kwargs):
        if operation == "open" and flags & core.os.O_DIRECTORY:
            raise OSError("directory unavailable")
        return original_open(path, flags, *args, **kwargs)
    def sync_dir(fd):
        if operation == "fsync" and stat.S_ISDIR(core.os.fstat(fd).st_mode):
            raise OSError("directory sync unavailable")
        return original_sync(fd)
    monkeypatch.setattr(core.os, "open", open_dir)
    monkeypatch.setattr(core.os, "fsync", sync_dir)
    assert core.resolve(ledger, "chat", ["us_one"], "done", "user") == ["us_one"]
    assert ledger.read_exact("chat", "us_one")["revision"] == 5
    assert "durability" in caplog.text


def test_parallel_edits_are_monotonic(ledger):
    from concurrent.futures import ThreadPoolExecutor
    seed(ledger)
    def edit_once(index):
        with ledger.edit("chat") as d:
            d["steps"].append({"id": f"us_{index}"})
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(edit_once, range(12)))
    assert ledger.read_exact("chat", "us_one")["revision"] == 16
    assert len(ledger._read("chat")["steps"]) == 13


def test_symlink_and_special_source_fail_without_overwrite(ledger):
    seed(ledger)
    ledger.path("alias").symlink_to(ledger.path("chat"))
    with pytest.raises(OSError):
        ledger.read_exact("alias", "us_one")
    with pytest.raises(OSError):
        with ledger.edit("alias"):
            pass
    core.os.mkfifo(ledger.path("pipe"))
    with pytest.raises(ValueError):
        ledger.read_exact("pipe", "us_one")


def test_size_boundary_and_oversized_write(ledger, monkeypatch):
    seed(ledger)
    before = ledger.path("chat").read_bytes()
    monkeypatch.setattr(core, "MAX_LEDGER_BYTES", len(before))
    assert ledger.read_exact("chat", "us_one")["revision"] == 4
    # Snapshot budgets must not strand existing steps or prevent resolution.
    with ledger.edit("chat") as d:
        d["extra"] = "x" * len(before)
    assert core.resolve(ledger, "chat", ["us_one"], "done", "user") == ["us_one"]
    assert ledger._read("chat")["revision"] == 6
    with pytest.raises(ValueError, match="size"):
        ledger.read_exact("chat", "us_one")


def test_resolved_snapshot_does_not_invent_authority(ledger):
    seed(ledger, steps=[{"id": "us_one", "status": "resolved", "resolved_by": "model"}])
    snapshot = ledger.read_exact("chat", "us_one")
    assert snapshot == {"root": "chat", "revision": 4,
                        "step": {"id": "us_one", "status": "resolved", "resolved_by": "model"}}
