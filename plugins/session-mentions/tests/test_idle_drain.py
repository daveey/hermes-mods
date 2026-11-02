"""Idle drain cost. Every Desktop courier drains each connected gateway every ~3s, and each drain used to
list the (normally empty) outbox, read every courier record, rewrite this courier's record through a
temp file + rename, and sweep all four protocol dirs. On an idle gateway that is pure overhead.

Invariants the cheaper drain must keep (each is exercised below):
  - an empty-directory listing is reused only while the directory's identity (dev, ino, mtime_ns,
    ctime_ns) is unchanged, the directory was already settled (older than DIR_SETTLE_SECONDS) when it
    was listed, and for at most OUTBOX_RECHECK_SECONDS;
  - a courier's liveness (its record's mtime) advances on every drain; changed record fields are
    written at once; a record replaced by someone else is rewritten;
  - stale cleanup still expires files by age: directory mtime does not change when files merely age or
    are edited in place, so a sweep skip ends at the earliest file deadline, and at the latest after
    SWEEP_RECHECK_SECONDS.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
import types
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("sm_relay_files_idle", Path(__file__).resolve().parent.parent / "relay_files.py")
rf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rf)  # type: ignore[union-attr]

_EVENTS: list | None = None


def _audit(event, args):
    if _EVENTS is not None and event in ("os.scandir", "os.listdir", "open"):
        _EVENTS.append((event, str(args[0]) if args else "", args[1] if event == "open" and len(args) > 1 else None))


sys.addaudithook(_audit)


class _Trace:
    def __enter__(self):
        global _EVENTS
        _EVENTS = []
        self.events = _EVENTS
        return self

    def __exit__(self, *exc):
        global _EVENTS
        _EVENTS = None

    def listed(self, path: Path) -> bool:
        want = str(path).rstrip("/")
        return any(e in ("os.scandir", "os.listdir") and p.rstrip("/") == want for e, p, _ in self.events)

    def read(self, path: Path) -> bool:
        return any(e == "open" and p == str(path) and (m in (None, "r", "rb")) for e, p, m in self.events)


@pytest.fixture
def clock(monkeypatch):
    """Module clock = wall clock + offset, so freshly created test dirs can be treated as settled."""
    state = {"offset": 0.0}
    fake = types.SimpleNamespace(time=lambda: time.time() + state["offset"],
                                 time_ns=lambda: time.time_ns() + int(state["offset"] * 1e9),
                                 sleep=time.sleep, monotonic=time.monotonic)
    monkeypatch.setattr(rf, "time", fake)
    for cache in ("_EMPTY_DIRS", "_SWEEP_STATE", "_COURIER_WRITES"):
        if hasattr(rf, cache):
            monkeypatch.setattr(rf, cache, {})

    def advance(seconds: float) -> None:
        state["offset"] += seconds
    return advance


def _base(tmp_path: Path) -> Path:
    for sub in ("outbox", "claimed", "results"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    rf.write_result(rf.HOME_COURIER_ID, {"at": time.time()}, tmp_path)
    rf.register_courier(tmp_path, "desk-b", ["desk-b"], ["gw-b"], home=False)
    return tmp_path


def _drain(base: Path, **kw):
    args = {"home": True, "courier": "desk-a", "reach": "gw-a", "aliases": "desk-a,host-a"}
    args.update(kw)
    return rf.claim_pending(base, **args)


def _request(base: Path, rid: str, route: str = "") -> None:
    rf.atomic_write(base / "outbox" / f"{rid}.json", {"id": rid, "action": "send", "route": route,
                                                       "created_at": rf.time.time(), "ttl": 600})


# ── RED on the baseline: idle drains do no listing or record reads ─────────────────────────────

def test_idle_drain_reuses_settled_empty_outbox_listing(tmp_path, clock):
    base = _base(tmp_path)
    clock(5)  # every dir is now older than the settle margin
    assert _drain(base) == []  # lists once, remembers the settled empty outbox
    with _Trace() as t:
        assert _drain(base) == []
    assert not t.listed(base / "outbox")


def test_idle_drain_reads_no_courier_records(tmp_path, clock):
    base = _base(tmp_path)
    clock(5)
    _drain(base)
    with _Trace() as t:
        _drain(base)
    assert not t.read(base / "couriers" / "desk-b.json")


def test_idle_sweep_skips_settled_dirs_before_their_deadline(tmp_path, clock):
    base = _base(tmp_path)
    clock(5)
    rf.sweep(base)
    with _Trace() as t:
        rf.sweep(base)
    for sub in ("outbox", "claimed", "results", "couriers"):
        assert not t.listed(base / sub), sub


def test_unchanged_registration_refreshes_liveness_without_rewriting(tmp_path, clock):
    base = _base(tmp_path)
    path = base / "couriers" / "desk-a.json"
    _drain(base)
    before = path.stat()
    time.sleep(0.05)
    _drain(base)
    after = path.stat()
    assert after.st_ino == before.st_ino  # same file: no temp-file + rename per drain
    assert after.st_mtime_ns > before.st_mtime_ns  # liveness still advanced
    assert any(c["device"] == "desk-a" for c in rf.live_couriers(base))


# ── guards that hold on the baseline and must keep holding ─────────────────────────────────────

def test_request_written_after_cached_empty_listing_is_claimed(tmp_path, clock):
    base = _base(tmp_path)
    clock(5)
    _drain(base)
    _drain(base)
    _request(base, "late")
    assert [r["id"] for r in _drain(base)] == ["late"]


def test_request_hidden_behind_restored_dir_mtime_is_still_found(tmp_path, clock):
    """A coarse clock (or a restore) can leave the dir mtime unchanged; ctime still moves."""
    base = _base(tmp_path)
    clock(5)
    _drain(base)
    st = (base / "outbox").stat()
    _request(base, "same-tick")
    os.utime(base / "outbox", ns=(st.st_atime_ns, st.st_mtime_ns))
    assert (base / "outbox").stat().st_mtime_ns == st.st_mtime_ns
    assert [r["id"] for r in _drain(base)] == ["same-tick"]


def test_change_invisible_to_an_unsettled_dir_key_is_still_found(tmp_path, clock, monkeypatch):
    """A coarse timestamp (1-2s on some filesystems) can hide a same-tick change from the dir key, so an
    empty listing of a dir modified within DIR_SETTLE_SECONDS is never reused."""
    base = _base(tmp_path)  # dirs just modified: inside the settle margin
    real = getattr(rf, "_dir_key", None)
    frozen: dict = {}
    if real is not None:  # model a filesystem whose dir times did not move for this change
        monkeypatch.setattr(rf, "_dir_key", lambda p: frozen.setdefault(str(p), real(p)))
    assert _drain(base) == []
    _request(base, "same-tick")
    assert [r["id"] for r in _drain(base)] == ["same-tick"]


def test_cached_empty_outbox_is_relisted_after_bounded_fallback(tmp_path, clock):
    base = _base(tmp_path)
    clock(5)
    _drain(base)
    clock(getattr(rf, "OUTBOX_RECHECK_SECONDS", 30.0) + 1)
    with _Trace() as t:
        _drain(base)
    assert t.listed(base / "outbox")


def test_routing_uses_couriers_registered_after_an_idle_period(tmp_path, clock):
    """Courier records are read when a request arrives, so a courier that registered while the outbox
    was cached-empty still owns its route (that Mac answers for itself)."""
    base = _base(tmp_path)
    clock(5)
    _drain(base)
    _drain(base)
    rf.register_courier(base, "desk-c", ["desk-c"], ["gw-c"], home=False)
    _request(base, "for-c", route="desk-c")
    _request(base, "for-a", route="gw-a")
    assert [r["id"] for r in _drain(base, reach="gw-a,desk-c")] == ["for-a"]
    assert (base / "outbox" / "for-c.json").exists()
    assert [r["id"] for r in rf.claim_pending(base, home=False, courier="desk-c", reach="gw-c")] == ["for-c"]


def test_changed_registration_is_written_immediately(tmp_path, clock):
    base = _base(tmp_path)
    _drain(base)
    _drain(base, reach="gw-a,gw-new")
    assert json.loads((base / "couriers" / "desk-a.json").read_text())["reach"] == ["gw-a", "gw-new"]


def test_registration_replaced_by_another_writer_is_rewritten(tmp_path, clock):
    base = _base(tmp_path)
    path = base / "couriers" / "desk-a.json"
    _drain(base)
    rf.atomic_write(path, {"device": "desk-a", "aliases": ["desk-a"], "reach": ["elsewhere"], "home": False, "at": 1})
    _drain(base)
    rec = json.loads(path.read_text())
    assert rec["home"] is True and "gw-a" in rec["reach"]


def test_registration_timestamp_stays_bounded_fresh(tmp_path, clock):
    base = _base(tmp_path)
    path = base / "couriers" / "desk-a.json"
    _drain(base)
    first = json.loads(path.read_text())["at"]
    clock(getattr(rf, "COURIER_REWRITE_SECONDS", 60.0) + 1)
    _drain(base)
    assert json.loads(path.read_text())["at"] > first + 60


def test_sweep_still_expires_a_file_that_merely_ages(tmp_path, clock):
    """No directory change at all: only time passes. The skip must end at the file's deadline."""
    base = _base(tmp_path)
    old = base / "claimed" / "aging.json"
    rf.atomic_write(old, {"id": "aging"})
    t = time.time() - rf.STALE_SECONDS + 10
    os.utime(old, (t, t))
    clock(5)
    rf.sweep(base)
    rf.sweep(base)
    assert old.exists()
    clock(10)
    rf.sweep(base)
    assert not old.exists()


def test_sweep_expires_an_in_place_backdated_file_within_the_fallback(tmp_path, clock):
    base = _base(tmp_path)
    clock(5)
    rf.sweep(base)
    rf.sweep(base)
    beat = base / "results" / f"{rf.HOME_COURIER_ID}.json"
    t = time.time() - rf.STALE_SECONDS - 100
    os.utime(beat, (t, t))  # in-place: the directory does not change
    clock(getattr(rf, "SWEEP_RECHECK_SECONDS", 60.0) + 1)
    rf.sweep(base)
    assert not beat.exists()


def test_sweep_expires_an_old_file_moved_into_a_cached_dir(tmp_path, clock):
    base = _base(tmp_path)
    clock(5)
    rf.sweep(base)
    rf.sweep(base)
    src = tmp_path / "elsewhere.json"
    src.write_text("{}")
    t = time.time() - rf.STALE_SECONDS - 100
    os.utime(src, (t, t))
    os.replace(src, base / "claimed" / "moved.json")
    rf.sweep(base)
    assert not (base / "claimed" / "moved.json").exists()


def test_expired_request_still_answered_after_idle_period(tmp_path, clock):
    base = _base(tmp_path)
    clock(5)
    _drain(base)
    rf.atomic_write(base / "outbox" / "old.json", {"id": "old", "action": "send", "created_at": rf.time.time() - 700,
                                                     "ttl": 600})
    assert _drain(base) == []
    assert "expired" in json.loads((base / "results" / "old.json").read_text())["error"]
