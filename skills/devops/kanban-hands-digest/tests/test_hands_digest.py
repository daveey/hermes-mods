"""Tests for hands_digest.py (stdlib + pytest). Run:
uv run --with pytest python -m pytest -q -p no:cacheprovider skills/devops/kanban-hands-digest/tests
"""
import datetime as dt
import importlib.util
import json
import os
import sqlite3
import stat
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("hands_digest", HERE.parent / "scripts" / "hands_digest.py")
hd = importlib.util.module_from_spec(spec)
sys.modules["hands_digest"] = hd
spec.loader.exec_module(hd)

SCHEMA = """
create table tasks (id text primary key, title text not null, body text, assignee text, status text not null,
  priority integer default 0, created_at integer not null, block_kind text);
create table task_events (id integer primary key autoincrement, task_id text not null, run_id integer,
  kind text not null, payload text, created_at integer not null);
"""
T0 = 1_792_200_000  # 2026-10-16 ~18:20 PDT


def make_board(path: Path, cards):
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    for i, c in enumerate(cards):
        con.execute("insert into tasks(id,title,body,status,priority,created_at,block_kind) values(?,?,?,?,?,?,?)",
                    (c["id"], c.get("title", c["id"]), c.get("body", ""), c.get("status", "blocked"),
                     c.get("priority", 0), T0, c.get("kind")))
        if c.get("reason") is not None:
            payload = {"reason": c["reason"], "kind": c.get("kind")}
            if c.get("initial"):
                payload = {"reason": "initial_status", "status": "blocked"}
            con.execute("insert into task_events(task_id,kind,payload,created_at) values(?,?,?,?)",
                        (c["id"], "blocked", json.dumps(payload), T0 + i))
    con.commit()
    con.close()


CFG = {
    "groups": [
        {"name": "Alex", "keywords": ["alex"], "weight": 4},
        {"name": "Watch", "keywords": ["watch"]},
        {"name": "iPhone", "keywords": ["iphone", "phone"]},
        {"name": "mini", "keywords": ["mini", "screen lock"]},
    ],
    "shared_steps": [{
        "id": "ios-install", "sentence": "(?i)\\binstall", "value": "(?:ios-app-)?(\\d{3,4})\\b", "agg": "max",
        "group": "iPhone", "label": "Install iOS build >= {max}", "check": "build {max}",
    }],
    "rules": [{"match": "(?i)her invite", "group": "Alex", "check": "invite alex", "action": "Get Alex to open her invite"}],
    "checks": {},
}


def cfg(**kw):
    c = hd.load_config(None)
    c.update(json.loads(json.dumps(CFG)))
    c.update(kw)
    return c


@pytest.fixture
def home(tmp_path):
    make_board(tmp_path / "kanban.db", [
        {"id": "t_a", "title": "Card A", "kind": "needs_input", "priority": 0,
         "reason": "Code is shipped (hub deployed). Install ios-app-298 on the iPhone by tapping the banner (it's on 290 now). "
                   "Then after 07:00 PT, run the morning script on the Watch. Unblock afterwards."},
        {"id": "t_b", "title": "Card B", "kind": "needs_input", "priority": 2,
         "reason": "Everything is built. He needs to install ios-app-295 from the iPhone banner, then say items 1-7 on the watch."},
        {"id": "t_c", "title": "Card C", "kind": "capability",
         "reason": "Alex hasn't used her invite yet; it expires 2026-10-18 19:55. Unblock once she has opened it."},
        {"id": "t_d", "title": "Card D", "kind": "transient", "reason": "network flaked"},
        {"id": "t_e", "title": "Card E", "kind": None, "initial": True, "reason": "",
         "body": "Goal: x\n\nBlocked until: Sam says which Mac and when Alex is available.\n"},
        {"id": "t_f", "title": "Card F", "kind": "needs_input",
         "reason": "ACTION: turn off screen lock on the mini DEVICE: mini UNBLOCKS-WHEN: sysadminctl says off CHECK: lock_off"},
        {"id": "t_g", "title": "Running", "status": "running", "kind": None},
    ])
    make_board(tmp_path / "kanban/boards/other/kanban.db", [
        {"id": "t_x", "title": "Other board", "kind": "needs_input", "reason": "ACTION: plug the watch in"}])
    make_board(tmp_path / "kanban/boards/_archived/kanban.db", [
        {"id": "t_z", "title": "Archived", "kind": "needs_input", "reason": "ACTION: ignore me"}])
    return tmp_path


def now():
    return dt.datetime(2026, 10, 17, 8, 0, tzinfo=hd.tz(cfg()))


def test_collect_filters_human_blocks_and_boards(home):
    cards = hd.collect(cfg(), home)
    ids = sorted((c["board"], c["id"]) for c in cards)
    assert ids == [("default", "t_a"), ("default", "t_b"), ("default", "t_c"), ("default", "t_e"),
                   ("default", "t_f"), ("other", "t_x")]
    e = next(c for c in cards if c["id"] == "t_e")
    assert e["reason"] == "Sam says which Mac and when Alex is available."


def test_parse_structured_single_line():
    st = hd.parse_structured("ACTION: tap the bear DEVICE: EchoEar UNBLOCKS-WHEN: hub sees tap CHECK: tapped 3 "
                             "DEADLINE: 2026-10-18T19:55-07:00")
    assert st == {"actions": ["tap the bear"], "device": "EchoEar", "unblocks_when": "hub sees tap",
                  "check": "tapped 3", "deadline": "2026-10-18T19:55-07:00"}
    multi = hd.parse_structured("Built.\nACTION: one\nACTION: two\nUNBLOCKS-WHEN: x")
    assert multi["actions"] == ["one", "two"]
    assert hd.parse_structured("no keys here: just text") == {}


def test_shared_step_dedupes_and_keeps_rest_of_sentence(home):
    d = hd.build(hd.collect(cfg(), home), cfg(), now=now())
    shared = [i for i in d["items"] if i["kind"] == "shared"]
    assert len(shared) == 1
    s = shared[0]
    assert s["action"] == "Install iOS build >= 298" and s["check"] == "build 298"
    assert sorted(c["id"] for c in s["cards"]) == ["t_a", "t_b"]
    b = next(i for i in d["items"] if i["kind"] == "card" and i["cards"][0]["id"] == "t_b")
    assert b["action"] == "Say items 1-7 on the watch."
    assert b["group"] == "Watch"
    a = next(i for i in d["items"] if i["kind"] == "card" and i["cards"][0]["id"] == "t_a")
    assert "install" not in a["action"].lower() and "shipped" not in a["action"].lower()
    assert a["action"].startswith("After 07:00 PT, run the morning script")
    assert a["windows"] == ["after 07:00 PT"]
    assert a["unblocks_when"] == "Unblock afterwards."


def test_rules_deadline_and_ordering(home):
    d = hd.build(hd.collect(cfg(), home), cfg(), now=now())
    first = d["items"][0]
    assert first["cards"][0]["id"] == "t_c" and first["urgent"]
    assert first["action"] == "Get Alex to open her invite" and first["check"] == "invite alex"
    assert first["deadline"] == dt.datetime(2026, 10, 18, 19, 55, tzinfo=hd.tz(cfg()))
    # beyond the horizon -> not urgent
    d2 = hd.build(hd.collect(cfg(deadline_horizon_hours=12), home), cfg(deadline_horizon_hours=12), now=now())
    assert not next(i for i in d2["items"] if i["cards"][0]["id"] == "t_c")["urgent"]
    groups = [i["group"] for i in d["items"]]
    # group order follows config after the urgent block; shared step leads its group
    assert groups.index("Alex") < groups.index("Watch") < groups.index("iPhone") < groups.index("mini")
    iphone = [i for i in d["items"] if i["group"] == "iPhone"]
    assert iphone[0]["kind"] == "shared"


def test_structured_reason_wins(home):
    d = hd.build(hd.collect(cfg(), home), cfg(), now=now())
    f = next(i for i in d["items"] if i["cards"][0]["id"] == "t_f")
    assert (f["action"], f["group"], f["unblocks_when"], f["check"]) == (
        "turn off screen lock on the mini", "mini", "sysadminctl says off", "lock_off")


def test_summarize_drops_status_and_lead_in():
    a, w = hd.summarize_action("Everything else has shipped (BB Pod 918). Sam: on the iPhone 8, open Settings and "
                               "turn ON UI Automation. Unblock and I'll do the proof.")
    assert a == "On the iPhone 8, open Settings and turn ON UI Automation."
    assert w == "Unblock and I'll do the proof."
    a, _ = hd.summarize_action("The rest needs Sam's hands, in this order (before 22:00 PT): 1) plug the phone in; "
                               "2) lock it.")
    assert a.startswith("1) plug the phone in")


def test_pick_group_scores_keywords():
    g = cfg()["groups"]
    assert hd.pick_group("open the app on the watch, then the watch again, phone", g) == "Watch"
    assert hd.pick_group("ask Alex on her iPhone", g) == "Alex"
    assert hd.pick_group("nothing matches", g) == "Other"


def _script(path: Path, body: str) -> str:
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def test_checks_auto_unblock_end_to_end(home, tmp_path):
    log = tmp_path / "hermes.log"
    fake = _script(tmp_path / "hermes", f'echo "$@" >> {log}\necho unblocked\n')
    ok = _script(tmp_path / "ok.sh", 'echo "invite state enrolled ($1)"; exit 0\n')
    pending = _script(tmp_path / "pending.sh", 'echo "still waiting"; exit 1\n')
    c = cfg(hermes_bin=fake, checks={
        "invite": {"cmd": ok + " {0}", "unblock": True},
        "build": {"cmd": ok + " {0}", "unblock": True},      # bound to a shared step: never unblocks a card
        "lock_off": {"cmd": pending, "unblock": True},
    })
    d = hd.build(hd.collect(c, home), c, now=now())
    events = hd.apply_checks(d, c)
    assert [(e["card"], e["unblocked"]) for e in events] == [("t_c", True)]
    args = log.read_text().strip().splitlines()
    assert args == ["kanban unblock --reason auto-check `invite alex` passed: invite state enrolled (alex) t_c"]
    shared = next(i for i in d["items"] if i["kind"] == "shared")
    assert shared["status"]["state"] == "ok"
    f = next(i for i in d["items"] if i["cards"][0]["id"] == "t_f")
    assert f["status"] == {"state": "pending", "line": "still waiting"}
    text = hd.render(d, c)
    assert "== ✓ Already done (auto-checked)" in text and "→ card unblocked" in text
    # --no-unblock and dry-run paths
    log.unlink()
    d = hd.build(hd.collect(c, home), c, now=now())
    assert hd.apply_checks(d, c, do_unblock=False) == []
    assert not log.exists()
    ev = hd.apply_checks(hd.build(hd.collect(c, home), c, now=now()), c, dry=True)
    assert ev[0]["out"].startswith("dry-run:") and not log.exists()


def test_unblock_on_named_board_passes_board(tmp_path):
    log = tmp_path / "log"
    fake = _script(tmp_path / "hermes", f'echo "$@" >> {log}\n')
    ok, _ = hd.unblock({"id": "t_x", "board": "other"}, "note", {"hermes_bin": fake})
    assert ok and log.read_text().strip() == "kanban --board other unblock --reason note t_x"


def test_unknown_check_is_reported_not_crashing(home):
    c = cfg()
    d = hd.build(hd.collect(c, home), c, now=now())
    hd.apply_checks(d, c)
    first = d["items"][0]
    assert first["status"]["state"] == "unknown"


def test_render_fits_max_chars_and_counts(home):
    c = cfg()
    d = hd.build(hd.collect(c, home), c, now=now())
    full = hd.render(d, c)
    assert full.startswith("Hands-on digest: 6 cards waiting on you")
    assert "⏰ TIME-SENSITIVE" in full and "t_x@other" in full
    long = [{"id": f"t_{i}", "board": "default", "title": "T" * 80, "priority": 0, "kind": "needs_input",
             "blocked_at": T0, "reason": "ACTION: " + "open the phone and tap the thing " * 10} for i in range(8)]
    d = hd.build(long, c, now=now())
    big = hd.render(d, c)
    small = hd.render(d, c, max_chars=len(big) // 2)
    assert len(small) <= len(big) // 2 and small.count("\n1. ") + small.count("\n8. ") == 2
    assert hd.render(hd.build([], c, now=now()), c) == ""


def test_cli_json_and_checks_only_silent(home, capsys):
    cfgp = home / "cfg.json"
    cfgp.write_text(json.dumps(CFG))
    assert hd.main(["--config", str(cfgp), "--home", str(home), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert len(data["items"]) == 7  # 6 card items + 1 shared install step
    assert hd.main(["--config", str(cfgp), "--home", str(home), "--checks-only"]) == 0
    assert capsys.readouterr().out == ""
