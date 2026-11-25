"""Taking a request back out of the outbox races a courier's claim (os.replace of the same file). On APFS an
unlink racing that rename can also return success, so both sides "won": a cancelled forward could still be
delivered, and cross_task could say "not picked up" while a Desktop ran the request. relay_files.take_back
uses os.replace too, so exactly one side wins."""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import threading
import time
from pathlib import Path

_spec = importlib.util.spec_from_file_location("sm_plugin_take_back", Path(__file__).resolve().parent.parent / "__init__.py")
sm = importlib.util.module_from_spec(_spec)
sys.modules["sm_plugin_take_back"] = sm
_spec.loader.exec_module(sm)  # type: ignore[union-attr]
rf = sm.rf


def _base(tmp_path: Path) -> Path:
    base = tmp_path / "sm"
    for sub in ("outbox", "claimed", "results", "couriers"):
        (base / sub).mkdir(parents=True)
    return base


def _claim(path: Path) -> bool:
    """A courier's claim, as relay_files.claim_pending does it."""
    try:
        os.replace(path, path.parent.parent / "claimed" / path.name)
        return True
    except OSError:
        return False


def _race(base: Path, take) -> None:
    """300 races of a claim against ``take(rid)``; both look the name up first, as the real callers do."""
    for i in range(300):
        rid = f"r{i}"
        path = base / "outbox" / f"{rid}.json"
        rf.atomic_write(path, {"id": rid, "action": "send", "created_at": time.time(), "ttl": 600})
        go, got = threading.Barrier(2), {}

        def look():
            try:
                path.read_text()
            except OSError:
                pass

        def claim():
            go.wait()
            look()
            got["claimed"] = _claim(path)

        def back():
            go.wait()
            look()
            got["taken"] = take(rid)

        ts = [threading.Thread(target=claim), threading.Thread(target=back)]
        [t.start() for t in ts]
        [t.join(10) for t in ts]
        assert got["claimed"] != got["taken"], (i, got)
        assert (base / "claimed" / path.name).exists() == got["claimed"]
        assert not list((base / "outbox").iterdir())  # the taken copy is gone too
        (base / "claimed" / path.name).unlink(missing_ok=True)


def test_take_back_and_claim_race_exactly_one_wins(tmp_path):
    base = _base(tmp_path)
    _race(base, lambda rid: rf.take_back(base / "outbox" / f"{rid}.json"))


def test_cancel_and_claim_race_exactly_one_wins(tmp_path):
    """POST /cancel (Desktop forwardSend): True only when the forward was taken back, so it is never delivered."""
    base = _base(tmp_path)
    _race(base, lambda rid: rf.cancel(rid, base))


def test_cancel_that_cannot_take_back_says_not_taken_and_logs(tmp_path, monkeypatch, caplog):
    """An OSError other than not-found (permissions, I/O) is "not taken" (False, logged), never a 500."""
    base = _base(tmp_path)
    rf.atomic_write(base / "outbox" / "r1.json", {"id": "r1", "action": "send", "created_at": time.time(), "ttl": 600})

    def denied(path):
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(rf, "take_back", denied)
    with caplog.at_level("WARNING"):
        assert rf.cancel("r1", base) is False
    assert "r1" in caplog.text and "Permission denied" in caplog.text


def test_sweep_drops_hidden_leftovers_older_than_an_hour(tmp_path):
    base = _base(tmp_path)
    old = time.time() - rf.STALE_SECONDS - 60
    stale = [base / "outbox" / ".a.taken", base / "outbox" / ".b.tmp", base / "results" / ".c.json.123.tmp"]
    fresh = [base / "outbox" / ".d.taken", base / "outbox" / ".e.tmp"]
    for p in stale + fresh:
        p.write_text("{}")
    for p in stale:
        os.utime(p, (old, old))
    keep = base / "outbox" / ".notes"  # not a protocol leftover: never touched
    keep.write_text("x")
    os.utime(keep, (old, old))
    rf.sweep(base)
    assert [p for p in stale if p.exists()] == []
    assert all(p.exists() for p in fresh) and keep.exists()


def _relay_env(monkeypatch, base: Path) -> None:
    monkeypatch.setattr(rf, "root", lambda: base)
    monkeypatch.setattr(sm, "PICKUP_TIMEOUT", 0.05)
    (base / "couriers" / "macbook.json").write_text(json.dumps(
        {"device": "macbook", "aliases": ["macbook"], "reach": ["nous-1"], "home": False, "at": time.time()}))


def test_cross_task_pickup_timeout_waits_for_a_courier_that_claimed_at_the_deadline(tmp_path, monkeypatch):
    """A Desktop that claims just as pickup expires: the answer is its result, never "did not pick up"."""
    base = _base(tmp_path)
    _relay_env(monkeypatch, base)
    real, claimed = rf.take_back, []

    def courier_first(path):
        if not claimed:
            claimed.append(_claim(path))
            threading.Timer(0.3, rf.write_result, (path.stem, {"ok": True, "tasks": []}, base)).start()
        return real(path)

    monkeypatch.setattr(rf, "take_back", courier_first)
    out = json.loads(sm._relay("task_list", {"gateway": "nous-1"}, "s1", 0))
    assert claimed == [True]
    assert out == {"ok": True, "tasks": []}, out
    assert not list((base / "outbox").iterdir())


def test_cross_task_unclaimed_request_is_taken_back_at_the_pickup_deadline(tmp_path, monkeypatch):
    base = _base(tmp_path)
    _relay_env(monkeypatch, base)
    out = json.loads(sm._relay("task_list", {"gateway": "nous-1"}, "s1", 0))
    assert "error" in out and "picked up" in out["error"], out
    assert not list((base / "outbox").iterdir())  # no request and no taken copy left


def test_bounded_read_timeout_takes_back_its_unclaimed_request(tmp_path, monkeypatch):
    base = _base(tmp_path)
    _relay_env(monkeypatch, base)
    monkeypatch.setattr(sm, "PICKUP_TIMEOUT", 30.0)
    out = json.loads(sm._relay("task_list", {"gateway": "nous-1"}, "s1", 0, timeout_seconds=0.2))
    assert "Timed out" in out.get("error", ""), out
    assert not list((base / "outbox").iterdir())


def test_routeless_pickup_timeout_says_why_nobody_claimed(tmp_path, monkeypatch):
    """The bare "did not pick up" gave no way to tell a quit Desktop from a busy one; it now diagnoses the couriers."""
    base = _base(tmp_path)
    _relay_env(monkeypatch, base)
    (base / "couriers" / "macbook.json").unlink()
    out = json.loads(sm._relay("send", {}, "s1", 0))
    assert "did not pick up" in out["error"] and "no courier record" in out["error"], out
    stale = base / "couriers" / "macbook.json"
    stale.write_text(json.dumps({"device": "macbook", "aliases": ["macbook"], "reach": [], "at": 0}))
    old = time.time() - 300
    os.utime(stale, (old, old))
    out = json.loads(sm._relay("send", {}, "s1", 0))
    assert "last courier `macbook` was seen" in out["error"] and "asleep" in out["error"], out
    os.utime(stale, None)
    out = json.loads(sm._relay("send", {}, "s1", 0))
    assert "1 Desktop courier(s) are live (macbook)" in out["error"], out
