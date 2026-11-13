"""Owner adoption needs defect evidence, and "missing" needs lookup authority (card t_01a1c887ce541598).

Fixtures are the persisted incidents (coach log `selfheal_adopted_owner` / `selfheal_stale` rows; card titles as
they stood on the boards):

* A user complaint adopted the origin session's newest card with no subject evidence (`cards[0]`):
  desktop-d39268b86c ("the response dissapeard" -> a delivery-disposition card), agent-2e9530b2b5 (a landing timeout
  -> a SQLite read-helper fix), desktop-9b8be07a02 (an empty choice panel -> a main-loop stall card).
* A wait notice named the stalled card and that same card was adopted as the scheduler's repair owner
  (kanban-04bdba70ac): the card a complaint names existed before it, so it is the subject, not the response.
* Another fingerprint's VERIFY card was adopted as the implementing owner (desktop-727bbca8b8).
* `hermes kanban show` said "no such task" for owners that lived on another board or another gateway
  (kanban-f3b72e7010, bb-448ec69637 on the MacBook; agent-04039a9129 on a named board) and the lane went stale.
  "missing" now needs every readable source to agree; an unreadable or stale source is unknown, never closure.
"""
import importlib.util
import json
import os
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


sh = load("coach_self_heal_adopt_authority_test", ROOT / "self_heal.py")
core = load("coach_core_adopt_authority_test", ROOT / "core.py")

NOW = 1794450000.0
HUB = {}
SID = "20261111_091916_d2d36a"


def OPEN(board=None, now=None):
    return {"usd": 1.0, "cap": 25.0, "running": 0, "ok_usd": True, "ok_running": True, "why": ""}


class Runner:
    """Fake subprocess.run. `show` answers from `status` ("no such task" otherwise); create returns a new id."""

    def __init__(self, status=None):
        self.calls, self.n, self.status = [], 0, dict(status or {})

    def __call__(self, argv, capture_output=True, text=True, input=None, timeout=None):
        argv = list(argv)
        self.calls.append(argv)
        if "create" in argv:
            self.n += 1
            return SimpleNamespace(returncode=0, stdout=json.dumps({"id": f"t_{self.n:08x}"}), stderr="")
        if "show" in argv:
            tid = argv[argv.index("show") + 1]
            if tid not in self.status:
                return SimpleNamespace(returncode=1, stdout="", stderr=f"no such task: {tid}")
            return SimpleNamespace(returncode=0, stdout=json.dumps({"task": {"id": tid, "status": self.status[tid]}}),
                                   stderr="")
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    def creates(self):
        return [a for a in self.calls if "create" in a]


@pytest.fixture
def store(tmp_path):
    return core.Store(tmp_path / "coach")


def ev(store, name):
    p = Path(store.root) / "log.jsonl"
    return [r for r in (json.loads(x) for x in p.read_text().splitlines())
            if r.get("event") == name] if p.exists() else []


def defect(fp, component, symptom, quote, source="complaint", sid=SID, **kw):
    d = {"component": component, "symptom": symptom, "source": source, "kind": "hermes_bug", "p_defect": 0.9,
         "session_id": sid, "quote": quote, "host": "hub", "at": NOW - 60, "fingerprint": fp}
    d.update(kw)
    return d


def card(cid, title, status="done", board=None, body="", created_at=NOW - 30, **kw):
    return {"id": cid, "title": title, "status": status, "board": board, "body": body, "created_at": created_at,
            "idempotency_key": None, **kw}


def owner(cards):
    return lambda sid, since: sorted(cards, key=lambda c: -c["created_at"])


def adopt(store, d, cards):
    return sh._adopt_session_card(d, d["fingerprint"], store, NOW, owner(cards))


SQLITE_HELPER = card("t_01a1ccbb1ad1d3ea", "Fix repo-guards failure in red-sweep SQLite read helper", board="hermes")
PLACEMENT = card("t_01a1cc903e617723", "Hermes bug: kanban_create: placement='worker-<n>' was not honoured",
                 board="hermes", created_at=NOW - 90)
STALL = card("t_01a1c7d517a4477a", "Mini Hermes Desktop: fix >2s main-loop stalls persisting with CI at background QoS",
             status="running", board="runners")
REVIEW = card("t_01a1c7be66ca5d95", "K2 G2 narrowed class: pinned Opus review of the captured predicate evidence",
              board="runners", created_at=NOW - 100)
CHOICE_FIX = card("t_01a1c7dbe207955d", "Choice panel shows nothing for blocked-card asks; action_request rejects "
                  "multi-choice kinds (Invalid kind)", status="running", created_at=NOW - 200,
                  body="Problem: the user opened the choice panel and saw no choices, although 3 decisions waited.")

VANISHED = defect("desktop-d39268b86c", "desktop", "agent response vanished from chat display",
                  "the response dissapeard", sid="20261111_033213_db5894")
LANDING = defect("agent-2e9530b2b5", "agent", "Landing timeout misconfigured; access setup handed to user",
                 "ts to the Access login (302). The steps are in your For you queue.", source="admission")
NO_CHOICES = defect("desktop-9b8be07a02", "desktop", "choice panel shows no choices",
                    "i am not seeing any choices in the choice pannel", sid="20261023_040944_37ae77")
NONE_VISIBLE = defect("desktop-727bbca8b8", "desktop", "claimed choice panel visible but user sees none",
                      "i dont see any desktop choice pannels", sid="20261023_040944_37ae77")
SHIP_CARD = card("t_01a1c6c260c64981", "Ship transcript-cache body/metadata split (Desktop scroll stalls) and verify "
                 "on mbp", status="ready")
SHIP_STALLED = defect(
    "kanban-04bdba70ac", "kanban", "Ship card stalled 30 min with no worker, queued behind others",
    "[default] @default Kanban t_01a1c6c260c64981: t_01a1c6c260c64981 (Ship transcript-cache body/metadata split "
    "(Desktop scroll stalls) and verify on mbp), which this chat waits on, has been ready 30 min with no worker: "
    "17 card(s) ahead of it, 3/7 worker slots busy", source="backstop", sid="20261110_183044_2e1656")


# ── adoption: sharing the session is not owning the defect ──
@pytest.mark.parametrize("d, cards", [
    (VANISHED, [card("t_01a1c86efe3c8e77", "K2 delivery disposition: reconcile unmet install gate after G2 "
                     "non-success", status="blocked", board="runners")]),
    (LANDING, [SQLITE_HELPER, PLACEMENT]),
    (NO_CHOICES, [STALL, REVIEW]),
], ids=["desktop-d39268b86c", "agent-2e9530b2b5", "desktop-9b8be07a02"])
def test_complaint_does_not_adopt_an_unrelated_card_of_the_same_session(store, d, cards):
    assert adopt(store, d, cards) is None
    assert not ev(store, "selfheal_adopted_owner")
    assert sh.LaneState(store.root).get(d["fingerprint"]).get("fix_card") is None
    (skip,) = ev(store, "selfheal_adopt_skipped")
    assert skip["candidates"] == [c["id"] for c in sorted(cards, key=lambda c: -c["created_at"])]
    assert skip["source"] == d["source"]


def test_unrelated_session_card_leaves_the_complaint_to_be_filed(store):
    r = Runner()
    out = sh.file_defect(dict(VANISHED), store=store, m="live", runner=r, now=NOW, cfg=HUB, budget_fn=OPEN,
                         owner_fn=owner([SQLITE_HELPER]))
    assert out["action"] == "filed" and len(r.creates()) == 1


def test_complaint_still_adopts_the_session_card_that_names_the_defect(store):
    """desktop-9b8be07a02's real repair: created by the same session, older than the unrelated cards."""
    out = adopt(store, NO_CHOICES, [STALL, REVIEW, CHOICE_FIX])
    assert out["adopted"] and out["card"] == CHOICE_FIX["id"]
    (e,) = ev(store, "selfheal_adopted_owner")
    assert e["card"] == CHOICE_FIX["id"] and e["candidates"] == [CHOICE_FIX["id"]]
    assert sh.LaneState(store.root).get(NO_CHOICES["fingerprint"])["fix_card"] == CHOICE_FIX["id"]


def test_card_the_complaint_itself_names_is_the_subject_not_the_repair_owner(store):
    """kanban-04bdba70ac: the stalled ship card shares the symptom's words and was adopted as the scheduler fix."""
    assert sh.card_names_defect(SHIP_CARD, SHIP_STALLED)  # word overlap alone would adopt it
    assert adopt(store, SHIP_STALLED, [SHIP_CARD]) is None
    assert not ev(store, "selfheal_adopted_owner")
    (skip,) = ev(store, "selfheal_adopt_skipped")
    assert skip["subject_cards"] == [SHIP_CARD["id"]]
    # a different card that names the defect is still adopted next to it
    fix = card("t_01a1c6d000000001", "Scheduler: ready ship card stalled with no worker while slots were free",
               status="running")
    store2 = core.Store(Path(store.root).parent / "coach2")
    assert adopt(store2, SHIP_STALLED, [SHIP_CARD, fix])["card"] == fix["id"]


def test_another_fingerprints_verify_or_investigation_card_is_not_an_implementing_owner(store):
    """desktop-727bbca8b8 adopted desktop-9b8be07a02's VERIFY card (its title repeats that lane's symptom)."""
    verify = card("t_01a1cd8ba05bf6d5", "self-heal verify: choice panel shows no choices", status="running",
                  board="runners")
    lane = sh.LaneState(store.root)
    lane.update(NO_CHOICES["fingerprint"], status="approved", fix_card=CHOICE_FIX["id"], verify_card=verify["id"],
                card="t_01a1c7d000000002")
    assert sh.card_names_defect(verify, NONE_VISIBLE)
    assert adopt(store, NONE_VISIBLE, [verify]) is None
    investigation = card("t_01a1c7d000000002", "self-heal: choice panel shows no choices", status="running")
    assert adopt(store, NONE_VISIBLE, [investigation]) is None
    assert not ev(store, "selfheal_adopted_owner")
    # the other lane's implementing fix card is real repair work and still adopts
    out = adopt(store, NONE_VISIBLE, [verify, CHOICE_FIX])
    assert out["card"] == CHOICE_FIX["id"]


def test_lane_card_that_names_this_fingerprint_is_adopted(store):
    """A self-heal card filed for THIS fingerprint (its key or body names it) is its owner, whatever lane lists it."""
    own = card("t_01a1c7d000000003", "self-heal verify: something else", status="running",
               idempotency_key=f"selfheal:{NONE_VISIBLE['fingerprint']}:verify")
    sh.LaneState(store.root).update("desktop-0000000000", status="approved", verify_card=own["id"])
    assert adopt(store, NONE_VISIBLE, [own])["card"] == own["id"]


# ── lookup authority: unknown is not missing ──
def board_db(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.execute("create table tasks(id text primary key, title text, status text, created_at int)")
    db.executemany("insert into tasks values(?,?,?,?)", [(i, "t", s, 1) for i, s in rows])
    db.commit()
    db.close()


def hub_db(path, tasks=(), scopes=(), period=185):
    """A hermes-hub replica: tasks = [(gw, board, id, status)], scopes = [(gw, board, state, observed_at)]."""
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.execute("create table replica (gw text, coll text, key text, version integer, seq integer, data text, "
               "updated_at integer, primary key (gw, coll, key))")
    for gw, board, tid, status in tasks:
        db.execute("insert into replica values(?,?,?,?,?,?,?)",
                   (gw, "tasks", f"default/{board}/{tid}", 1, 1, json.dumps(
                       {"id": tid, "status": status, "board": board, "gateway": gw, "profile": "default"}), 1))
    by_gw: dict = {}
    for gw, board, state, at in scopes:
        s = {"profile": "default", "board": board, "state": state}
        if at is not None:
            s["observed_at"] = at
        by_gw.setdefault(gw, []).append(s)
    for gw, sc in by_gw.items():
        db.execute("insert into replica values(?,?,?,?,?,?,?)",
                   (gw, "gateway", "info", 1, 1, json.dumps(
                       {"gateway": gw, "scope_heartbeat_period_s": period, "task_scopes": sc}), 1))
    db.commit()
    db.close()
    return path


OWNER = "t_01a1cc1a8bb39958"  # bb-448ec69637's real owner, a MacBook card the hub board never held


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    board_db(h / "kanban.db", [("t_local0001", "running")])
    return h


def lookup(cid, board=None, runner=None, **kw):
    kw.setdefault("now", NOW)
    return sh.card_lookup(cid, board, runner or Runner(), **kw)


def test_card_on_another_local_board_is_found_not_missing(home):
    """agent-04039a9129 / agent-69f58b8c13: the default-board `show` said no such task for a card that existed."""
    board_db(home / "kanban" / "boards" / "runners" / "kanban.db", [("t_01a1850e77f7c0b8", "blocked")])
    got = lookup("t_01a1850e77f7c0b8", home=home, hub_db=home / "none.db")
    assert got["status"] == "blocked" and got["board"] == "runners" and got["authority"] == "local_board"
    assert sh.card_status("t_01a1850e77f7c0b8", None, Runner(), home=home, hub_db=home / "none.db") == "blocked"


def test_remote_owner_is_read_from_the_fresh_replica(home, tmp_path):
    hdb = hub_db(tmp_path / "hub" / "hub.db", [("macbook", "default", OWNER, "blocked")],
                 [("macbook", "default", "ok", NOW - 40)])
    got = lookup(OWNER, "hermes", home=home, hub_db=hdb)
    assert (got["status"], got["gateway"], got["board"], got["authority"]) == ("blocked", "macbook", "default",
                                                                               "replica")


def test_stale_or_unknown_replica_scope_is_unknown_not_a_status(home, tmp_path):
    for i, scope in enumerate([("macbook", "default", "ok", NOW - 7200), ("macbook", "default", "unknown", None)]):
        hdb = hub_db(tmp_path / f"hub{i}" / "hub.db", [("macbook", "default", OWNER, "done")], [scope])
        got = lookup(OWNER, home=home, hub_db=hdb)
        assert got["status"] is None and got["gateway"] == "macbook" and got["last_seen"] == "done", got
        assert sh.card_status(OWNER, None, Runner(), home=home, hub_db=hdb) is None


def test_missing_needs_every_source_readable_and_empty(home, tmp_path):
    hdb = hub_db(tmp_path / "hub" / "hub.db", [("macbook", "default", "t_other00001", "done")],
                 [("macbook", "default", "ok", NOW - 40)])
    got = lookup("t_deadbeef0001", home=home, hub_db=hdb)
    assert got["status"] == "missing" and got["authority"] == "all_sources"
    # no replica on this host at all: the local boards are the whole world (unchanged behavior)
    assert lookup("t_deadbeef0001", home=home, hub_db=tmp_path / "absent.db")["status"] == "missing"


def test_unreadable_source_is_unknown(home, tmp_path):
    bad = tmp_path / "hub" / "hub.db"
    bad.parent.mkdir()
    bad.write_bytes(b"not a database")
    got = lookup("t_deadbeef0001", home=home, hub_db=bad)
    assert got["status"] is None and "replica" in got["why"]
    # a board mid-recovery cannot vouch for absence either
    (home / "kanban.db.recover.pid").write_text(json.dumps({"pid": os.getpid()}))
    got = lookup("t_deadbeef0001", home=home, hub_db=tmp_path / "absent.db")
    assert got["status"] is None and "recover" in got["why"]


def test_cli_read_failure_stays_unknown(home):
    class Boom(Runner):
        def __call__(self, argv, **kw):
            return SimpleNamespace(returncode=1, stdout="", stderr="disk I/O error")
    assert lookup("t_local0001", runner=Boom(), home=home, hub_db=home / "none.db")["status"] is None


def test_host_qualified_board_routes_to_that_gateway_without_an_invalid_cli_call(home, tmp_path):
    """digest_owner_cards records board '<host>:default'; `hermes kanban --board macbook:default` is an invalid slug."""
    hdb = hub_db(tmp_path / "hub" / "hub.db", [("macbook", "default", OWNER, "running")],
                 [("macbook", "default", "ok", NOW - 40)])
    r = Runner()
    got = lookup(OWNER, "macbook:default", runner=r, home=home, hub_db=hdb)
    assert got["status"] == "running" and got["gateway"] == "macbook"
    assert not [a for a in r.calls if "macbook:default" in a]


def test_lookup_has_no_side_effects(home, tmp_path):
    hdb = hub_db(tmp_path / "hub" / "hub.db", [("macbook", "default", OWNER, "blocked")],
                 [("macbook", "default", "ok", NOW - 40)])
    before = {p: (p.stat().st_mtime_ns, p.stat().st_size) for p in (home / "kanban.db", hdb)}
    r = Runner()
    lookup(OWNER, home=home, hub_db=hdb, runner=r)
    lookup("t_deadbeef0001", home=home, hub_db=hdb, runner=r)
    assert all(a[:2] == ["hermes", "kanban"] and "show" in a for a in r.calls), r.calls
    assert before == {p: (p.stat().st_mtime_ns, p.stat().st_size) for p in before}
    assert sorted(x.name for x in hdb.parent.iterdir()) == ["hub.db"]


def _approved(store, fp, fix_card, board=None):
    sh.LaneState(store.root).update(fp, status="approved", fix_card=fix_card, approved_at=NOW - 600, board=board,
                                    component="bb", symptom="Installed test app crashes on launch",
                                    cards=[fix_card])


def test_reconcile_keeps_a_live_remote_owner_and_records_its_gateway(store, home, tmp_path, monkeypatch):
    """bb-448ec69637 / kanban-f3b72e7010: the owner lives on the MacBook; the hub board says no such task."""
    hdb = hub_db(tmp_path / "hub" / "hub.db", [("macbook", "default", OWNER, "blocked")],
                 [("macbook", "default", "ok", NOW - 40)])
    monkeypatch.setattr(sh, "_lookup_sources", lambda runner: (home, hdb))
    _approved(store, "bb-448ec69637", OWNER, board="hermes")
    assert sh.reconcile(store, runner=Runner(), now=NOW) == []
    st = sh.LaneState(store.root).get("bb-448ec69637")
    assert st["status"] == "approved" and st["owner_gateway"] == "macbook" and st["owner_board"] == "default"
    assert not ev(store, "selfheal_stale")
    (e,) = ev(store, "selfheal_owner_located")
    assert e["card"] == OWNER and e["gateway"] == "macbook"
    sh.reconcile(store, runner=Runner(), now=NOW + 300)
    assert len(ev(store, "selfheal_owner_located")) == 1  # logged once per location


def test_reconcile_treats_an_unknown_lookup_as_open_and_logs_it_once(store, home, tmp_path, monkeypatch):
    hdb = hub_db(tmp_path / "hub" / "hub.db", [("macbook", "default", OWNER, "done")],
                 [("macbook", "default", "ok", NOW - 7200)])
    monkeypatch.setattr(sh, "_lookup_sources", lambda runner: (home, hdb))
    _approved(store, "bb-448ec69637", OWNER, board="hermes")
    for dt in (0, 300):
        assert sh.reconcile(store, runner=Runner(), now=NOW + dt) == []
    assert sh.LaneState(store.root).get("bb-448ec69637")["status"] == "approved"
    (e,) = ev(store, "selfheal_lookup_unknown")
    assert e["card"] == OWNER and e["last_seen"] == "done" and "stale" in e["why"]
    assert not ev(store, "selfheal_stale")


def test_reconcile_still_closes_a_card_every_source_confirms_missing(store, home, tmp_path, monkeypatch):
    hdb = hub_db(tmp_path / "hub" / "hub.db", [], [("macbook", "default", "ok", NOW - 40)])
    monkeypatch.setattr(sh, "_lookup_sources", lambda runner: (home, hdb))
    _approved(store, "bb-448ec69637", "t_deadbeef0001")
    assert dict(sh.reconcile(store, runner=Runner(), now=NOW))["bb-448ec69637"].startswith("stale (approved")
    (e,) = ev(store, "selfheal_stale")
    assert e["kanban"] == "missing"


# ── review findings on the first cut ──
def test_absence_needs_the_asked_scope_and_every_in_service_scope_to_be_clean(home, tmp_path):
    """One clean scope does not vouch for a board its gateway could not read."""
    scopes = [("macbook", "default", "ok", NOW - 40), ("macbook", "work", "error", NOW - 5000)]
    hdb = hub_db(tmp_path / "hub" / "hub.db", [], scopes)
    for board in ("macbook:work", None):
        got = lookup("t_deadbeef0001", board, home=home, hub_db=hdb)
        assert got["status"] is None and "macbook/work" in got["why"], got
    assert lookup("t_deadbeef0001", "macbook:default", home=home, hub_db=hdb)["status"] == "missing"
    # a gateway out of service for longer than LOOKUP_RETIRED_S neither vouches nor vetoes
    old = NOW - sh.LOOKUP_RETIRED_S - 60
    hdb2 = hub_db(tmp_path / "hub2" / "hub.db", [], [("macbook", "default", "ok", NOW - 40), ("nous-1", "default", "ok", old)])
    assert lookup("t_deadbeef0001", home=home, hub_db=hdb2)["status"] == "missing"


def test_absence_is_unknown_until_every_gateway_synced_after_the_lane_learned_of_the_card(home, tmp_path):
    hdb = hub_db(tmp_path / "hub" / "hub.db", [], [("macbook", "default", "ok", NOW - 300)])
    assert lookup("t_deadbeef0001", home=home, hub_db=hdb, since=NOW - 100)["status"] is None
    assert lookup("t_deadbeef0001", home=home, hub_db=hdb, since=NOW - 900)["status"] == "missing"


def test_owner_location_does_not_outlive_its_card(store, home, tmp_path, monkeypatch):
    """A lane re-approved onto a local fix card must not read it on the previous owner's gateway."""
    hdb = hub_db(tmp_path / "hub" / "hub.db", [("macbook", "default", OWNER, "blocked")],
                 [("macbook", "default", "ok", NOW - 40)])
    monkeypatch.setattr(sh, "_lookup_sources", lambda runner: (home, hdb))
    _approved(store, "bb-448ec69637", OWNER, board=None)
    sh.reconcile(store, runner=Runner(), now=NOW)
    lane = sh.LaneState(store.root)
    assert lane.get("bb-448ec69637")["owner_card"] == OWNER
    lane.update("bb-448ec69637", fix_card="t_local0001", approved_at=NOW)
    assert sh.reconcile(store, runner=Runner({"t_local0001": "running"}), now=NOW + 60) == []
    assert lane.get("bb-448ec69637")["status"] == "approved"
    assert sh._owner_location(lane.get("bb-448ec69637"), "t_local0001") is None
    assert not ev(store, "selfheal_stale")


def test_owner_on_another_local_board_is_recorded(store, home, tmp_path, monkeypatch):
    board_db(home / "kanban" / "boards" / "runners" / "kanban.db", [("t_01a1850e77f7c0b8", "running")])
    monkeypatch.setattr(sh, "_lookup_sources", lambda runner: (home, tmp_path / "absent.db"))
    _approved(store, "agent-04039a9129", "t_01a1850e77f7c0b8", board=None)
    assert sh.reconcile(store, runner=Runner(), now=NOW) == []
    st = sh.LaneState(store.root).get("agent-04039a9129")
    assert (st["status"], st["owner_gateway"], st["owner_board"]) == ("approved", None, "runners")
    (e,) = ev(store, "selfheal_owner_located")
    assert e["board"] == "runners" and e["gateway"] is None
    # a named lane board whose owner is on the default board is "off the lane's board" as well
    store2 = core.Store(Path(store.root).parent / "coach2")
    _approved(store2, "agent-04039a9129", "t_local0001", board="runners")
    sh.reconcile(store2, runner=Runner(), now=NOW)
    assert sh.LaneState(store2.root).get("agent-04039a9129")["owner_board"] == "default"


@pytest.mark.parametrize("board", [None, "macbook:default"])
def test_verify_card_for_a_remote_owner_is_not_parented_on_a_card_this_board_lacks(store, home, tmp_path, monkeypatch, board):
    hdb = hub_db(tmp_path / "hub" / "hub.db", [("macbook", "default", OWNER, "done")],
                 [("macbook", "default", "ok", NOW - 40)])
    monkeypatch.setattr(sh, "_lookup_sources", lambda runner: (home, hdb))
    _approved(store, "bb-448ec69637", OWNER, board=board)
    r = Runner()
    sh.reconcile(store, runner=r, now=NOW)
    st = sh.LaneState(store.root).get("bb-448ec69637")
    assert st["status"] == "approved" and st.get("verify_card"), st
    (create,) = r.creates()
    text = " ".join(map(str, create))
    assert "--parent" not in create and "gateway `macbook`" in text and "cross_task" in text
    expected_board = "default" if board else "bb"
    assert create[create.index("--board") + 1] == expected_board
    assert st["board"] == board  # retain the digest owner reference for later lookups
    assert not ev(store, "selfheal_stale")


@pytest.mark.parametrize("board, gateway, expected", [
    ("remote:work", "remote", "default"),
    ("local:work", None, "work"),
    ("work", "remote", "work"),
    (None, "remote", None),
])
def test_verify_create_normalizes_only_qualified_boards(store, monkeypatch, board, gateway, expected):
    st = {"owner_card": OWNER, "owner_gateway": gateway, "owner_board": "work"}
    calls = []
    monkeypatch.setattr(sh, "_run_card", lambda args, runner: (calls.append(args) or (0, "t_0000000000000002", "")))
    sh._file_verify("agent-test", st, OWNER, {"board": board}, store, Runner(), NOW)
    assert calls[0]["board"] == expected
    assert calls[0]["parents"] == ([] if gateway else [OWNER])


def test_replica_described_candidates_obey_adopt_statuses(tmp_path):
    hdb = hub_db(tmp_path / "hub" / "hub.db", [("macbook", "default", OWNER, "archived"),
                                                ("macbook", "default", "t_01a1cc1a8bb39959", "running")], [])
    cards = [{"id": OWNER, "board": "macbook:default", "status": "?", "created_at": 1},
             {"id": "t_01a1cc1a8bb39959", "board": "macbook:default", "status": "?", "created_at": 1},
             {"id": "t_0000000000000001", "board": "macbook:default", "status": "?", "created_at": 1}]
    got = sh._replica_card_text(cards, hdb)
    assert [c["id"] for c in got] == ["t_01a1cc1a8bb39959", "t_0000000000000001"]  # undescribed stays, textless
    assert got[0]["status"] == "running" and "title" not in got[1]
