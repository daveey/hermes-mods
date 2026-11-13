"""Self-heal files one card per defect, dedupes on the fingerprint at filing, and caps cards per UTC day
(t_01a1d69064f6064c).

In the 24 h to 2026-11-13 20:00Z, 336 of 678 default-board cards were created_by self-heal: every defect opened an
investigation, a fix and a verify card, and 49 investigations closed as duplicates of an existing owner. Now:
1. a new defect gets ONE `selfheal:<fp>` card that triages, fixes, verifies live and records `event fixed` itself;
   lanes filed before keep their split fix/verify cards until they finish;
2. before any create, an OPEN card on the target board keyed `selfheal:<fp>[...]` or carrying the body line
   `fingerprint: <fp>` takes the occurrence as a comment, and nothing is filed;
3. at `daily_card_cap` self-heal cards created on the board today, the defect goes to the day's single digest card.

The board is a real SQLite `tasks` table (the columns core's kanban.db has); the fake `hermes kanban` writes to it,
so the lane's own reads (open owner, today's count) see the cards it created.
"""
import hashlib
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


sh = load("coach_self_heal_one_card_test", ROOT / "self_heal.py")
core = load("coach_core_one_card_test", ROOT / "core.py")

NOW = 1794528000.0 + 15 * 3600  # 2026-11-13 15:00 UTC
DAY = "2026-11-13"
MIDNIGHT = 1794614400.0  # 2026-11-14 00:00 UTC
HUB = {}  # no hub_ssh: this host is the hub and files locally
OPEN = lambda board=None, now=None: {"usd": 1.0, "cap": 200.0, "running": 0, "ok_usd": True, "ok_running": True,
                                     "available": True, "why": ""}


class Board:
    """Fake `hermes kanban` over a real tasks table: create (idempotency key returns the non-archived card),
    comment, schedule, show."""

    def __init__(self, db: Path, now: float):
        self.db, self.now, self.calls, self.comments, self.schedules = db, now, [], [], []
        db.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(db)
        try:
            with con:
                con.execute("CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, title TEXT, body TEXT, "
                            "status TEXT NOT NULL, created_by TEXT, created_at INTEGER NOT NULL, "
                            "idempotency_key TEXT)")
        finally:
            con.close()

    def add(self, tid, key=None, body="", status="ready", created_by="self-heal", created_at=None, title="t"):
        con = sqlite3.connect(self.db)
        try:
            with con:
                con.execute("INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (tid, title, body, status, created_by, int(created_at or self.now), key))
        finally:
            con.close()

    def rows(self, where="1", args=()):
        con = sqlite3.connect(self.db)
        try:
            return con.execute(f"SELECT id, title, idempotency_key, created_by FROM tasks WHERE {where}", args).fetchall()
        finally:
            con.close()

    def __call__(self, argv, capture_output=True, text=True, input=None, timeout=None):
        argv = list(argv)
        self.calls.append(argv)
        ok = SimpleNamespace(returncode=0, stdout="", stderr="")
        if "create" in argv:
            def opt(flag):
                return argv[argv.index(flag) + 1] if flag in argv else None
            key = opt("--idempotency-key")
            con = sqlite3.connect(self.db)
            try:
                row = con.execute("SELECT id FROM tasks WHERE idempotency_key = ? AND status != 'archived'",
                                  (key,)).fetchone()
                n = con.execute("SELECT count(*) FROM tasks").fetchone()[0]
            finally:
                con.close()
            if row:
                return SimpleNamespace(returncode=0, stdout=json.dumps({"id": row[0]}), stderr="")
            tid = f"t_{n + 1:08x}"
            self.add(tid, key=key, body=opt("--body"), created_by=opt("--created-by"), created_at=self.now,
                     title=argv[argv.index("create") + 1])
            return SimpleNamespace(returncode=0, stdout=json.dumps({"id": tid}), stderr="")
        if "comment" in argv:
            i = argv.index("comment")
            self.comments.append((argv[i + 1], argv[i + 2]))
            return ok
        if "schedule" in argv:
            self.schedules.append(argv[argv.index("schedule") + 1:])
            return ok
        if "show" in argv:
            return SimpleNamespace(returncode=0, stdout=json.dumps({"task": {"status": "ready"}}), stderr="")
        return ok

    def creates(self):
        return [a for a in self.calls if "create" in a]


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = tmp_path / "kanban-root"
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(root))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    for var in ("COACH_SELFHEAL_ONE_CARD", "COACH_SELFHEAL_DAILY_CAP"):
        monkeypatch.delenv(var, raising=False)
    store = core.Store(tmp_path / "coach")
    board = Board(root / "kanban.db", NOW)
    return SimpleNamespace(store=store, board=board, root=root)


def defect(symptom="sidebar sessions reshuffle", session=None, component="desktop", **kw):
    # one session per symptom: two defects from one complaint turn are one owner (complaint_turn adoption)
    session = session or "20261113_140000_" + hashlib.sha1(symptom.encode()).hexdigest()[:6]
    d = {"component": component, "symptom": symptom, "source": "complaint", "kind": "hermes_bug", "p_defect": 0.9,
         "session_id": session, "quote": "i think the sort is broken", "host": "macbook", "at": NOW - 60}
    d.update(kw)
    d["fingerprint"] = sh.fingerprint(d["component"], d["symptom"])
    return d


def file(env, d, now=NOW, cfg=None):
    env.board.now = now
    return sh.file_defect(d, store=env.store, m="live", runner=env.board, now=now, cfg=HUB if cfg is None else cfg,
                          budget_fn=OPEN)


def events(store, name):
    path = Path(store.root) / "log.jsonl"
    if not path.exists():
        return []
    return [r for r in map(json.loads, path.read_text().splitlines()) if r.get("event") == name]


def fill(board, n, now=NOW):
    for i in range(n):
        board.add(f"t_fill{i:04d}", key=f"selfheal:other-{i:010d}", created_at=now - 60)


# ── 1. one card per defect ─────────────────────────────────────────────────────────────────────────────────────
def test_new_defect_files_one_card_that_fixes_and_verifies(env):
    d = defect()
    fp = d["fingerprint"]
    out = file(env, d)
    (argv,) = env.board.creates()
    assert out["action"] == "filed" and out["key"] == f"selfheal:{fp}"
    title, body = argv[argv.index("create") + 1], argv[argv.index("--body") + 1]
    assert title.startswith("self-heal:") and not title.startswith((sh.FIX_TITLE, sh.VERIFY_TITLE))
    assert sh.fp_line(fp) in body.splitlines()
    assert f"{sh.SELF_EVENT} fixed {fp}" in body and "--result result.md" in body  # the verify card's receipt
    assert "event proposed" not in body and "proposal.md" not in body
    assert sh.NATURAL_EVENT_RULE.strip() in body  # the exact-symptom live proof stays on the card
    assert argv[argv.index("--model") + 1] == sh.MODEL and argv[argv.index("--max-runtime") + 1] == str(sh.FIX_RUNTIME)
    st = sh.LaneState(env.store.root).get(fp)
    assert st["status"] == "filed" and st["one_card"] is True
    assert events(env.store, "selfheal_filed")[-1]["one_card"] is True


def test_one_card_lane_never_files_a_fix_or_verify_card(env):
    d = defect()
    fp = d["fingerprint"]
    card = file(env, d)["card"]
    # a stray proposal (old habit) must not open the split: sync auto-approves `proposed` lanes into file_fix
    sh.record_event("proposed", fp, card, store=env.store, now=NOW + 10)
    done = sh.sync(store=env.store, runner=env.board, now=NOW + 20, auto=True)
    assert [c for c in env.board.creates()][1:] == [], done
    assert sh.LaneState(env.store.root).get(fp)["status"] == "filed"
    assert events(env.store, "selfheal_fix_skipped")[-1]["why"] == "one_card"
    # the card records the receipt itself, like the VERIFY child did
    e = sh.record_event("fixed", fp, card, note="fixed by abc1234def", store=env.store, now=NOW + 30)
    assert e["status"] == "fixed" and e["fix_card"] == card
    titles = [r[1] for r in env.board.rows()]
    assert not [t for t in titles if t.startswith((sh.FIX_TITLE, sh.VERIFY_TITLE))]


def test_one_card_that_ends_with_a_proposal_goes_stale_not_to_a_fix_card(env, monkeypatch):
    d = defect()
    fp = d["fingerprint"]
    card = file(env, d)["card"]
    (Path(env.store.root) / "selfheal" / fp).mkdir(parents=True)
    (Path(env.store.root) / "selfheal" / fp / "proposal.md").write_text("Title\n\nPlan\n")
    lane = sh.LaneState(env.store.root)
    monkeypatch.setattr(sh, "_lane_owner_lookup", lambda *a, **k: "done")
    assert sh._reconcile_fp(lane, env.store, fp, lane.get(fp), env.board, NOW + 60).startswith("stale")
    assert len(env.board.creates()) == 1


def test_legacy_split_lane_still_files_its_fix_and_verify(env):
    """A lane filed before deploy (no one_card) keeps the split: its proposal becomes a fix card + VERIFY child."""
    fp = defect(component="kanban", symptom="legacy defect")["fingerprint"]
    lane = sh.LaneState(env.store.root)
    lane.update(fp, status="proposed", component="kanban", symptom="legacy defect", card="t_legacyinv", gen=1,
                filed_at=NOW - 3600, proposed_at=NOW - 60)
    (Path(env.store.root) / "selfheal" / fp).mkdir(parents=True)
    (Path(env.store.root) / "selfheal" / fp / "proposal.md").write_text("Title\n\nPlan: change self_heal.py\n")
    r = sh.file_fix(fp, store=env.store, runner=env.board, now=NOW, budget_fn=OPEN)
    assert r["action"] == "filed", r
    titles = [a[a.index("create") + 1] for a in env.board.creates()]
    assert titles[0].startswith(sh.FIX_TITLE) and titles[1].startswith(sh.VERIFY_TITLE)
    for a in env.board.creates():  # every self-heal card body carries the fingerprint line
        assert sh.fp_line(fp) in a[a.index("--body") + 1].splitlines()
    assert sh.LaneState(env.store.root).get(fp)["status"] == "approved"


def test_one_card_switch_config_and_env(env, monkeypatch):
    assert sh.one_card({}) is True
    assert sh.one_card({"one_card_per_defect": False}) is False
    monkeypatch.setenv("COACH_SELFHEAL_ONE_CARD", "1")
    assert sh.one_card({"one_card_per_defect": False}) is True
    monkeypatch.setenv("COACH_SELFHEAL_ONE_CARD", "0")
    assert sh.one_card({}) is False
    args = sh.card_args(defect(), "k", cfg={})
    assert "event proposed" in args["body"] and not args.get("one_card")


# ── 2. dedupe at filing ────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("key", ["selfheal:{fp}", "selfheal:{fp}:r2", "selfheal:{fp}:fix"])
def test_open_owner_by_key_takes_a_comment_and_nothing_is_filed(env, key):
    d = defect()
    fp = d["fingerprint"]
    env.board.add("t_owner001", key=key.format(fp=fp), status="running", created_by="default")
    out = file(env, d)
    assert out["action"] == "skip" and out["dedup"] is True and out["card"] == "t_owner001"
    assert env.board.creates() == []
    ((card, body),) = env.board.comments
    assert card == "t_owner001" and fp in body and d["session_id"] in body and "sort is broken" in body
    (e,) = events(env.store, "selfheal_dedup_commented")
    assert e["card"] == "t_owner001" and e["via"] == "key" and e["ok"] is True


def test_open_owner_by_body_fingerprint_line_takes_a_comment(env):
    d = defect()
    fp = d["fingerprint"]
    env.board.add("t_handcard", key="some-other-key", status="blocked", created_by="default",
                  body=sh.with_fp_line("A hand-filed card for this defect.", fp))
    out = file(env, d)
    assert out["dedup"] is True and out["card"] == "t_handcard" and env.board.creates() == []
    assert events(env.store, "selfheal_dedup_commented")[0]["via"] == "body"


def test_fingerprint_mentioned_inline_is_not_an_owner(env):
    d = defect()
    fp = d["fingerprint"]
    env.board.add("t_mention", key="x", body=f"see also fingerprint: {fp} in passing", created_by="default")
    assert file(env, d)["action"] == "filed"


@pytest.mark.parametrize("status", ["done", "archived"])
def test_done_or_archived_owner_does_not_dedupe(env, status):
    d = defect()
    fp = d["fingerprint"]
    env.board.add("t_closed01", key=f"selfheal:{fp}:r9", status=status,
                  body=sh.with_fp_line("old", fp), created_by="self-heal")
    out = file(env, d)
    assert out["action"] == "filed" and env.board.comments == []
    assert events(env.store, "selfheal_dedup_commented") == []


def test_repeat_occurrence_comments_once_per_session_and_hour(env):
    d = defect()
    fp = d["fingerprint"]
    env.board.add("t_owner001", key=f"selfheal:{fp}", status="ready", created_by="default")
    file(env, d)
    file(env, d, now=NOW + 300)  # same session within the hour: held
    assert len(env.board.comments) == 1 and events(env.store, "selfheal_dedup_held")
    file(env, defect(session="20261113_141000_ffffff"), now=NOW + 400)  # another session: comments
    file(env, d, now=NOW + 400 + sh.DEDUP_COMMENT_S)  # an hour after the last comment: comments again
    assert len(env.board.comments) == 3 and env.board.creates() == []


@pytest.mark.parametrize("kind", ["backport:abc123def456:0011aabb", "undeployed:macbook:abc123def456"])
def test_open_backport_or_deploy_check_is_not_the_defects_owner(env, kind):
    """A deploy-kind card owns one host's missing fix; a recurrence elsewhere must still file (review finding 4)."""
    d = defect()
    fp = d["fingerprint"]
    env.board.add("t_deploy01", key=f"selfheal:{fp}:{kind}", status="ready",
                  body=sh.with_fp_line(sh.fence_evidence("host x"), fp))
    assert file(env, d)["action"] == "filed" and env.board.comments == []


def test_owner_comments_are_bounded_per_day(env):
    d = defect()
    env.board.add("t_owner001", key=f"selfheal:{d['fingerprint']}", status="ready", created_by="default")
    for i in range(sh.DEDUP_MAX_PER_DAY + 5):  # a new session every time: only the daily bound holds them
        file(env, defect(session=f"20261113_1500{i:02d}_aaaaaa"), now=NOW + i)
    assert len(env.board.comments) == sh.DEDUP_MAX_PER_DAY and env.board.creates() == []
    lane = sh.LaneState(env.store.root).read()
    assert d["fingerprint"] not in lane  # the dedupe never invents lane entries for unfiled fingerprints


def test_fingerprint_line_sits_in_the_one_evidence_fence(env, monkeypatch):
    d = defect()
    for one in ("1", "0"):
        monkeypatch.setenv("COACH_SELFHEAL_ONE_CARD", one)
        body = sh.card_args(d, "k", cfg={})["body"]
        assert body.count(sh.EVIDENCE_OPEN) == 1 and sh.fp_line(d["fingerprint"]) in body.splitlines()
        start, end = body.index(sh.EVIDENCE_OPEN), body.index(sh.EVIDENCE_CLOSE)
        assert start < body.index(sh.fp_line(d["fingerprint"])) < end
    assert sh.with_fp_line("plain body", "x-1") .count(sh.EVIDENCE_OPEN) == 1
    fenced = sh.with_fp_line(f"a\n{sh.fence_evidence('ev')}\nb", "x-1")
    assert fenced.count(sh.EVIDENCE_OPEN) == 1 and sh.with_fp_line(fenced, "x-1") == fenced


def test_one_card_admission_reserves_the_fix_budget(env, monkeypatch):
    seen = []

    def fake_budget(board=None, now=None, new_card_usd=sh.INVESTIGATION_BUDGET):
        seen.append(new_card_usd)
        return OPEN()
    monkeypatch.setattr(sh, "budget", fake_budget)
    env.board.now = NOW
    sh.file_defect(defect(), store=env.store, m="live", runner=env.board, now=NOW, cfg=HUB)
    assert seen == [sh.DEFECT_BUDGET]


def test_owner_on_another_board_does_not_dedupe(env):
    d = defect()
    fp = d["fingerprint"]
    other = Board(env.root / "kanban" / "boards" / "elsewhere" / "kanban.db", NOW)
    other.add("t_otherbd", key=f"selfheal:{fp}", status="ready")
    assert file(env, d)["action"] == "filed"


# ── 3. daily card cap and the digest ───────────────────────────────────────────────────────────────────────────
def test_fortieth_card_files_and_the_fortyfirst_goes_to_the_digest(env):
    fill(env.board, 39)
    env.board.add("t_yesterday", key="selfheal:old-0000000001", created_at=NOW - 86400)  # yesterday: not counted
    env.board.add("t_humancard", key=None, created_by="default")  # another creator: not counted
    assert file(env, defect(symptom="fortieth"))["action"] == "filed"
    assert sh.cards_created_today(None, NOW) == 40
    out = file(env, defect(symptom="forty-first"))
    assert out["action"] == "deferred" and out["over_cap"] is True and out["retry_at"] == "2026-11-14T00:00:00Z"
    creates = env.board.creates()
    assert len(creates) == 2
    digest = creates[1]
    assert digest[digest.index("create") + 1] == f"self-heal digest {DAY}: defects over the daily cap"
    assert digest[digest.index("--idempotency-key") + 1] == f"selfheal-digest:{DAY}"
    (did,) = [r[0] for r in env.board.rows("idempotency_key = ?", (f"selfheal-digest:{DAY}",))]
    assert out["digest"] == did
    ((card, body),) = env.board.comments
    fp41 = defect(symptom="forty-first")["fingerprint"]
    d41 = defect(symptom="forty-first")
    assert card == did and fp41 in body and "forty-first" in body and d41["session_id"] in body
    (sched,) = env.board.schedules
    assert sched[0] == did and "--clock-only" in sched and "SCHEDULED_UNTIL=2026-11-14T00:00:00Z" in " ".join(sched)
    (e,) = events(env.store, "selfheal_over_cap")
    assert e["fingerprint"] == fp41 and e["count"] == 40 and e["cap"] == 40 and e["digest"] == did
    assert sh.cards_created_today(None, NOW) == 40  # the digest is not counted


def test_digest_is_created_once_per_day_and_reused(env):
    fill(env.board, 40)
    for i in range(3):
        file(env, defect(symptom=f"over {('alpha', 'bravo', 'charlie')[i]}"), now=NOW + i)
    file(env, defect(symptom="over alpha"), now=NOW + 10)  # same fingerprint again: no second digest comment
    digests = [a for a in env.board.creates() if f"selfheal-digest:{DAY}" in a]
    assert len(digests) == 1 and len(env.board.creates()) == 1
    assert len(env.board.comments) == 3 and len({c for c, _ in env.board.comments}) == 1
    assert len(events(env.store, "selfheal_over_cap")) == 4


def test_digest_closed_mid_day_is_replaced_not_reused(env):
    fill(env.board, 40)
    file(env, defect(symptom="over delta"))
    (did,) = [r[0] for r in env.board.rows("idempotency_key = ?", (f"selfheal-digest:{DAY}",))]
    con = sqlite3.connect(env.board.db)
    with con:
        con.execute("UPDATE tasks SET status = 'done' WHERE id = ?", (did,))
    con.close()
    out = file(env, defect(symptom="over echo"))
    assert out["digest"] != did and env.board.comments[-1][0] == out["digest"]
    assert env.board.rows("idempotency_key = ?", (f"selfheal-digest:{DAY}:2",))
    (Path(env.store.root) / sh.DIGEST_STATE).unlink()  # state lost: the closed key is skipped, the open :2 reused
    out3 = file(env, defect(symptom="over foxtrot"))
    assert out3["digest"] == out["digest"]


def test_digest_reused_when_its_state_file_is_lost(env):
    """The idempotency key holds the one-per-day promise even if <coach>/selfheal-digest.json is gone."""
    fill(env.board, 40)
    file(env, defect(symptom="over delta"))
    (Path(env.store.root) / sh.DIGEST_STATE).unlink()
    file(env, defect(symptom="over echo"))
    assert len(env.board.rows("idempotency_key = ?", (f"selfheal-digest:{DAY}",))) == 1


def test_day_rollover_files_the_capped_defect_and_opens_a_new_digest(env):
    fill(env.board, 40)
    d = defect(symptom="waits for tomorrow")
    assert file(env, d)["action"] == "deferred"
    nxt = MIDNIGHT + 300
    out = file(env, {**d, "at": nxt - 60}, now=nxt)
    assert out["action"] == "filed"
    assert sh.cards_created_today(None, nxt) == 1
    fill_next = [f"t_next{i:04d}" for i in range(39)]
    for tid in fill_next:
        env.board.add(tid, key=f"selfheal:{tid}", created_at=nxt)
    out = file(env, defect(symptom="next day over", at=nxt - 60), now=nxt + 1)
    assert out["action"] == "deferred"
    assert env.board.rows("idempotency_key = ?", ("selfheal-digest:2026-11-14",))


def test_dedupe_still_applies_over_the_cap(env):
    fill(env.board, 40)
    d = defect()
    env.board.add("t_owner001", key=f"selfheal:{d['fingerprint']}", status="ready", created_by="default")
    out = file(env, d)
    assert out["dedup"] is True and env.board.creates() == [] and events(env.store, "selfheal_over_cap") == []


def test_backport_counts_and_is_capped(env):
    fill(env.board, 40)
    d = defect(component="agent", symptom="stale runtime")
    dec = {"key": f"selfheal:{d['fingerprint']}:backport:abc", "checkout": "/x", "sha": "a" * 40, "head": "b" * 40,
           "why": "stale"}
    out = sh._file_backport(d, d["fingerprint"], {"fix_card": "t_f"}, dec, env.store, "live", env.board, NOW, HUB, OPEN)
    assert out["action"] == "skip" and out["over_cap"] is True
    assert [a for a in env.board.creates() if "backport" in a[a.index("create") + 1]] == []
    assert events(env.store, "selfheal_over_cap")[0]["kind"] == "backport"


def test_text_created_at_rows_never_count(env):
    """SQLite sorts text after every integer: an ISO-string created_at must not count as 'today' forever."""
    con = sqlite3.connect(env.board.db)
    with con:
        con.execute("INSERT INTO tasks VALUES ('t_isotext', 't', '', 'ready', 'self-heal', '2026-01-01T00:00:00Z', "
                    "'selfheal:x-0000000000')")
    con.close()
    assert sh.cards_created_today(None, NOW) == 0


def test_unreadable_count_refuses_rather_than_files(env, monkeypatch):
    def boom(*a, **k):
        raise sqlite3.DatabaseError("file is not a database")
    monkeypatch.setattr(sh, "cards_created_today", boom)
    out = file(env, defect())
    assert out["action"] == "error" and env.board.creates() == []
    assert events(env.store, "selfheal_cap_unavailable")


@pytest.mark.parametrize("cfg,envval,want", [
    ({}, None, 40), ({"daily_card_cap": 5}, None, 5), ({"daily_card_cap": 5}, "2", 2), ({}, "0", 0),
    ({"daily_card_cap": "x"}, None, 40), ({"daily_card_cap": 5}, "-3", 5), ({"daily_card_cap": True}, None, 40)])
def test_daily_cap_config_and_env_override(monkeypatch, cfg, envval, want):
    if envval is None:
        monkeypatch.delenv("COACH_SELFHEAL_DAILY_CAP", raising=False)
    else:
        monkeypatch.setenv("COACH_SELFHEAL_DAILY_CAP", envval)
    assert sh.daily_card_cap(cfg) == want


def test_env_cap_applies_to_filing(env, monkeypatch):
    monkeypatch.setenv("COACH_SELFHEAL_DAILY_CAP", "2")
    fill(env.board, 1)
    assert file(env, defect(symptom="second"))["action"] == "filed"
    assert file(env, defect(symptom="third"))["action"] == "deferred"
