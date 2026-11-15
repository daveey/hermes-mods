"""A verifier of an earlier fix round hears when that fix set is superseded (agent-10bd14a55a).

cron-7c04faf754 was fixed by 687b316b (2026-11-10); the fix card made its own live-check child pinned to that one sha.
The fingerprint came back on 11-12 and 11-13 and was refixed by 0bbdd2f4/9074755f/f63c16cf, but nothing told the old
child: it re-measured 687b316b for five days and completed "the running gateway contains fix 687b316b", evidence for a
fix that had already failed on its own. The lane now comments on every open card naming the fingerprint when it
refiles a fixed fingerprint and when a later round records new fix shas.
"""
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


sh = load("coach_self_heal_superseded_test", ROOT / "self_heal.py")
core = load("coach_core_superseded_test", ROOT / "core.py")

NOW = 1794497000.0
HUB = {"deploy_grace_hours": 0}
OPEN = lambda board=None, now=None: {"usd": 1.0, "cap": 25.0, "running": 0, "ok_usd": True, "ok_running": True,
                                     "why": ""}
FP = "agent-0123456789"
OLD_SHA = "687b316b516daa986f528b1646789314882abfb9"
NEW_SHA = "0bbdd2f46883c97db1cd8291d29d57e2b5d12af1"


class Runner:
    def __init__(self):
        self.calls, self.n = [], 0

    def __call__(self, argv, capture_output=True, text=True, input=None, timeout=None):
        self.calls.append(list(argv))
        if "create" in argv:
            self.n += 1
            return SimpleNamespace(returncode=0, stdout=json.dumps({"id": f"t_{0xa0 + self.n:08x}"}), stderr="")
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    def comments(self):
        return [(a[a.index("comment") + 1], a[a.index("comment") + 2]) for a in self.calls if "comment" in a]


def _board(home: Path, rows):
    con = sqlite3.connect(home / "kanban.db")
    con.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT, body TEXT, status TEXT, idempotency_key TEXT)")
    con.executemany("INSERT INTO tasks (id, title, body, status) VALUES (?, ?, ?, ?)", rows)
    con.commit()
    con.close()


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("COACH_SELFHEAL_ONE_CARD", "1")
    _board(home, [
        ("t_00000002", "self-heal fix: defect", f"fingerprint: {FP}", "done"),
        ("t_0000000c", f"Verify {FP} fix live on the MacBook",
         f"Live check for self-heal fix {FP} (core commit {OLD_SHA}).", "scheduled"),
        ("t_0000000d", "unrelated", "nothing about it", "scheduled"),
        ("t_0000000e", f"old verify {FP}", "done long ago", "done"),
    ])
    store = core.Store(tmp_path / "coach")
    sh.LaneState(store.root).update(FP, status="fixed", card="t_00000001", fix_card="t_00000002",
                                    fixed_at=NOW - 2 * 86400, fix_shas=[OLD_SHA], component="agent",
                                    symptom="pumps wear out", gen=1, cards=["t_00000001", "t_00000002"])
    return SimpleNamespace(home=home, store=store)


def _defect():
    return {"component": "agent", "symptom": "pumps wear out", "source": "signal", "kind": "hermes_bug",
            "p_defect": 0.95, "session_id": "20261112_000000_abcdef", "quote": "it came back", "host": "hub",
            "at": NOW - 60, "fingerprint": FP}


def test_recurrence_tells_the_open_verifier_of_the_earlier_fix(env):
    r = Runner()
    out = sh.file_defect(_defect(), store=env.store, m="live", runner=r, now=NOW, cfg=HUB, budget_fn=OPEN)
    assert out["action"] == "filed"
    notes = r.comments()
    assert [c for c, _ in notes] == ["t_0000000c"], notes  # open + names the fp; not done/unrelated/the new card
    body = notes[0][1]
    assert OLD_SHA[:12] in body and out["card"] in body and "superseded" in body
    (rec,) = [json.loads(x) for x in (Path(env.store.root) / "log.jsonl").read_text().splitlines()
              if '"selfheal_superseded_notice"' in x]
    assert rec["cards"] == ["t_0000000c"] and rec["fingerprint"] == FP


def test_new_fix_shas_tell_the_open_verifier_the_full_set(env):
    r = Runner()
    sh.LaneState(env.store.root).update(FP, status="approved", card="t_000000a1", fix_card="t_000000a1", gen=2)
    sh.record_event("fixed", FP, "t_000000a1", note=NEW_SHA, store=env.store, now=NOW + 3600, runner=r)
    notes = r.comments()
    assert [c for c, _ in notes] == ["t_0000000c"], notes
    body = notes[0][1]
    assert OLD_SHA[:12] in body and NEW_SHA[:12] in body and "t_000000a1" in body


def test_first_fix_round_comments_on_nothing(env):
    r = Runner()
    sh.LaneState(env.store.root).update(FP, status="approved", fix_shas=[], card="t_000000a1",
                                        fix_card="t_000000a1")
    sh.record_event("fixed", FP, "t_000000a1", note=OLD_SHA, store=env.store, now=NOW + 3600, runner=r)
    assert r.comments() == []


def test_same_sha_set_again_comments_on_nothing(env):
    r = Runner()
    sh.record_event("fixed", FP, "t_00000002", note=OLD_SHA, store=env.store, now=NOW + 3600, runner=r)
    assert r.comments() == []
