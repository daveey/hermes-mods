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
    assert argv[argv.index("--model") + 1] == "class:deep" and "--provider" not in argv and "--pin-mandatory" not in argv and argv[argv.index("--max-runtime") + 1] == str(sh.FIX_RUNTIME)
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


@pytest.mark.parametrize("via", ["key", "body"])
def test_open_owner_excludes_self_refile_and_finds_next_owner(env, via):
    fp = defect()["fingerprint"]
    for tid, age in (("t_self", 120), ("t_other", 60)):
        env.board.add(tid, key=f"selfheal:{fp}:{tid}" if via == "key" else tid,
                      body=sh.with_fp_line("hand-filed owner", fp) if via == "body" else "",
                      created_at=NOW - age)

    # Control: the re-filing card really is the oldest matching open owner.
    assert sh.open_owner(fp, None)["id"] == "t_self"
    owner = sh.open_owner(fp, None, exclude=["t_self"])
    assert owner["id"] == "t_other"
    assert owner["via"] == via
    assert sh.open_owner(fp, None, exclude=["t_self", "t_other"]) is None


@pytest.mark.parametrize("via", ["key", "body"])
def test_self_refile_comments_other_owner_not_itself(env, via):
    d = defect(filed_by="t_self")
    fp = d["fingerprint"]
    for tid, age in (("t_self", 120), ("t_other", 60)):
        env.board.add(tid, key=f"selfheal:{fp}:{tid}" if via == "key" else tid,
                      body=sh.with_fp_line("hand-filed owner", fp) if via == "body" else "",
                      created_at=NOW - age)

    out = file(env, d)
    assert out["action"] == "skip" and out["dedup"] is True
    assert out["card"] == "t_other"
    assert env.board.creates() == []
    ((card, body),) = env.board.comments
    assert card == "t_other" and fp in body
    (event,) = events(env.store, "selfheal_dedup_commented")
    assert event["card"] == "t_other" and event["via"] == via


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


@pytest.mark.parametrize("status", ["ready", "running", "blocked", "triage"])
def test_tracked_repeat_folds_even_without_ranked_backlog(env, status):
    d = defect()
    card = file(env, d)["card"]
    con = sqlite3.connect(env.board.db)
    try:
        with con:
            con.execute("ALTER TABLE tasks ADD COLUMN block_kind TEXT")
            con.execute("UPDATE tasks SET status=?, block_kind=? WHERE id=?",
                        (status, "lane_gate" if status == "triage" else None, card))
    finally:
        con.close()
    out = file(env, {**d, "session_id": "20261113_160000_aaaaaa"}, now=NOW + 60)
    assert out.get("dedup") and out["card"] == card
    assert len(env.board.creates()) == 1
    assert env.board.comments[-1][0] == card


@pytest.mark.parametrize("status", ["done", "archived"])
def test_tracked_closed_owner_refiles_same_day(env, status, monkeypatch):
    d = defect()
    card = file(env, d)["card"]
    con = sqlite3.connect(env.board.db)
    try:
        with con:
            con.execute("UPDATE tasks SET status=? WHERE id=?", (status, card))
    finally:
        con.close()
    monkeypatch.setattr(sh, "_lane_owner_lookup", lambda *a, **kw: status)
    out = file(env, d, now=NOW + 60)
    assert out["action"] == "filed" and out["card"] != card
    assert len(env.board.rows()) == 2 and env.board.comments == []


def test_backlog_fingerprint_owner_folds_held_repeat(env):
    d = defect()
    env.board.add("t_backlog", key=f"selfheal-backlog:{d['fingerprint']}:2026-11-12", status="triage")
    out = file(env, d)
    assert out.get("dedup") and out["card"] == "t_backlog"
    assert env.board.creates() == [] and env.board.comments[-1][0] == "t_backlog"


def test_owner_read_failure_defers_instead_of_creating(env, monkeypatch):
    d = defect()
    def broken(*a, **kw):
        raise sqlite3.OperationalError("owner read unavailable")
    monkeypatch.setattr(sh, "open_owner", broken)
    out = file(env, d)
    assert out["action"] == "deferred"
    assert env.board.creates() == []
    assert events(env.store, "selfheal_dedup_error")
    assert (Path(env.store.root) / "selfheal_pending.sqlite").exists()


def test_incomplete_owner_schema_defers_instead_of_creating(env):
    con = sqlite3.connect(env.board.db)
    try:
        with con:
            con.execute("ALTER TABLE tasks DROP COLUMN idempotency_key")
    finally:
        con.close()
    out = file(env, defect())
    assert out["action"] == "deferred" and env.board.creates() == []


def test_failed_owner_comment_retains_occurrence_for_retry(env):
    d = defect()
    env.board.add("t_owner001", key=f"selfheal:{d['fingerprint']}", status="triage")
    def fail_comment(argv, **kwargs):
        if "comment" in argv:
            return SimpleNamespace(returncode=1, stdout="", stderr="transport unavailable")
        return env.board(argv, **kwargs)
    out = sh.file_defect(d, store=env.store, m="live", runner=fail_comment, now=NOW, cfg=HUB, budget_fn=OPEN)
    assert out["action"] == "deferred" and env.board.creates() == []
    assert (Path(env.store.root) / "selfheal_pending.sqlite").exists()
    assert file(env, d, now=NOW + 60)["commented"] is True


def test_distinct_fingerprints_keep_separate_owners(env):
    a, b = defect(), defect("settings button does not respond")
    assert a["fingerprint"] != b["fingerprint"]
    assert file(env, a)["card"] != file(env, b)["card"]
    assert len(env.board.creates()) == 2


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
    assert len(env.board.creates()) == 1  # only the fortieth defect
    assert out["digest"] is None
    report = json.loads(Path(out["digest_report"]["path"]).read_text())
    fp41 = defect(symptom="forty-first")["fingerprint"]
    entry = report["fingerprints"][fp41]
    assert "forty-first" in entry["occurrence"] and defect(symptom="forty-first")["session_id"] in entry["occurrence"]
    assert env.board.comments == env.board.schedules == []
    (e,) = events(env.store, "selfheal_over_cap")
    assert e["fingerprint"] == fp41 and e["count"] == e["cap"] == 40
    assert e["digest_report"] == out["digest_report"]
    assert sh.cards_created_today(None, NOW) == 40


def test_digest_is_created_once_per_day_and_reused(env):
    fill(env.board, 40)
    for i in range(3):
        file(env, defect(symptom=f"over {('alpha', 'bravo', 'charlie')[i]}"), now=NOW + i)
    file(env, defect(symptom="over alpha"), now=NOW + 10)  # same fingerprint again: no second digest comment
    assert env.board.calls == []  # no create, schedule, comment, or agent launch
    reports = list((Path(env.store.root) / "selfheal-digests").rglob("*.json"))
    assert len(reports) == 1
    report = json.loads(reports[0].read_text())
    assert report["version"] == 1 and report["utc_day"] == DAY and report["board"] == "default"
    expected = {defect(symptom=f"over {word}")["fingerprint"] for word in ("alpha", "bravo", "charlie")}
    assert set(report["fingerprints"]) == expected
    assert report["unique_count"] == 3
    assert report["provenance"] == {"count": 40, "cap": 40, "source": "cards_created_today"}
    assert report["last_updated"] == sh._iso(NOW + 10)
    markdown = reports[0].with_suffix(".md").read_text()
    for fp, entry in report["fingerprints"].items():
        assert fp in markdown and "sort is broken" in entry["occurrence"]
        assert entry["kind"] == "defect"
    assert len(events(env.store, "selfheal_over_cap")) == 4


def test_digest_closed_mid_day_is_replaced_not_reused(env):
    """Historical closed/open digest cards are inert: do not replace, comment or edit them."""
    fill(env.board, 40)
    env.board.add("t_history", key=f"selfheal-digest:{DAY}", status="done")
    env.board.add("t_history2", key=f"selfheal-digest:{DAY}:2", status="scheduled")
    memo = Path(env.store.root) / sh.DIGEST_STATE
    memo.parent.mkdir(parents=True, exist_ok=True)
    memo.write_text(json.dumps({"day": DAY, "boards": {"default": {"card": "t_history2", "fps": []}}}))
    original = memo.read_bytes()
    out = file(env, defect(symptom="over delta"))
    out2 = file(env, defect(symptom="over echo"))
    assert out["digest_report"]["path"] == out2["digest_report"]["path"]
    assert out2["digest_report"]["count"] == 2
    assert memo.read_bytes() == original and env.board.calls == []
    assert len(env.board.rows("idempotency_key LIKE 'selfheal-digest:%'")) == 2


def test_digest_reused_when_its_state_file_is_lost(env):
    """Report is canonical across process restarts and loss of the obsolete card memo."""
    fill(env.board, 40)
    d = defect(symptom="over delta")
    first = file(env, d)
    (Path(env.store.root) / sh.DIGEST_STATE).write_text("obsolete")
    (Path(env.store.root) / sh.DIGEST_STATE).unlink()
    restarted = load("coach_digest_restart_test", ROOT / "self_heal.py")
    second = restarted._over_cap(d, d["fingerprint"], None, env.store, env.board, NOW + 1, {})
    assert second["digest_report"]["path"] == first["digest_report"]["path"]
    assert second["digest_report"]["count"] == 1 and env.board.calls == []
    third = file(env, defect(symptom="over echo"))
    assert third["digest_report"]["count"] == 2


def test_day_rollover_files_the_capped_defect_and_opens_a_new_digest(env):
    fill(env.board, 40)
    d = defect(symptom="waits for tomorrow")
    first = file(env, d)
    assert first["action"] == "deferred"
    first_bytes = Path(first["digest_report"]["path"]).read_bytes()
    nxt = MIDNIGHT + 300
    out = file(env, {**d, "at": nxt - 60}, now=nxt)
    assert out["action"] == "filed"
    assert sh.cards_created_today(None, nxt) == 1
    fill_next = [f"t_next{i:04d}" for i in range(39)]
    for tid in fill_next:
        env.board.add(tid, key=f"selfheal:{tid}", created_at=nxt)
    out = file(env, defect(symptom="next day over", at=nxt - 60), now=nxt + 1)
    assert out["action"] == "deferred"
    assert out["digest_report"]["path"] != first["digest_report"]["path"]
    assert json.loads(Path(out["digest_report"]["path"]).read_text())["utc_day"] == "2026-11-14"
    assert Path(first["digest_report"]["path"]).read_bytes() == first_bytes
    assert len(env.board.creates()) == 1


def test_backlog_reader_exposes_durable_digest_without_board_writes(env):
    fill(env.board, 40)
    first = file(env, defect())
    report = sh.backlog_report(env.store, NOW, filed_today=40, cap=40)
    assert report["digest_report"] == first["digest_report"]
    assert first["digest_report"]["markdown"] in report["body"]
    assert "Unique over-cap fingerprints: 1" in report["body"]
    assert env.board.calls == []


@pytest.mark.parametrize("damage", ["json", "markdown", "missing_json"])
def test_backlog_reader_exposes_report_damage_without_claiming_durability(env, damage):
    fill(env.board, 40)
    first = file(env, defect())
    path = Path(first["digest_report"]["path"])
    if damage == "missing_json":
        path.unlink()
    else:
        (path if damage == "json" else path.with_suffix(".md")).write_text("damaged")
    report = sh.backlog_report(env.store, NOW, filed_today=40, cap=40)
    assert report["digest_report"] is None
    assert "digest report unavailable" in report["digest_error"]
    assert report["digest_error"] in report["body"]
    assert env.board.calls == []


@pytest.mark.parametrize("ranked", [False, True])
@pytest.mark.parametrize("failure", ["json", "markdown", "read"])
def test_digest_io_failure_keeps_pending_and_reports_no_durability(env, monkeypatch, ranked, failure):
    fill(env.board, 40)
    original = sh._write_digest_file
    path = sh._digest_path(env.store, None, NOW)
    if failure == "read":
        path.parent.mkdir(parents=True)
        path.write_text("invalid JSON")
    else:
        def fail(path, text):
            if path.suffix == (".json" if failure == "json" else ".md"):
                raise OSError("fixture disk failure")
            original(path, text)
        monkeypatch.setattr(sh, "_write_digest_file", fail)
    d = defect()
    out = sh._over_cap(d, d["fingerprint"], None, env.store, env.board, NOW, {"backlog_ranked": ranked})
    assert out["over_cap"] and out["digest_report"] is None and out["digest"] is None
    assert "digest report unavailable" in out["error"]
    assert events(env.store, "selfheal_over_cap")[-1]["error"] == out["error"]
    with sh.contextlib.closing(sqlite3.connect(Path(env.store.root) / "selfheal_pending.sqlite")) as db:
        rows = db.execute("SELECT fp, retry FROM pending").fetchall()
    assert len(rows) == 1 and rows[0][0] == d["fingerprint"] and rows[0][1] >= NOW
    assert env.board.calls == [] and sh.cards_created_today(None, NOW) == 40
    # Remove the failure, then retry: retained JSON survives a failed Markdown write.
    monkeypatch.setattr(sh, "_write_digest_file", original)
    if failure == "read":
        assert path.read_text() == "invalid JSON"
        return
    retry = sh._over_cap(d, d["fingerprint"], None, env.store, env.board, NOW+1, {"backlog_ranked": ranked})
    assert retry["digest_report"]["count"] == 1 and "error" not in retry


def test_digest_reports_are_board_isolated_with_safe_collision_free_names(env):
    fill(env.board, 40)
    default = file(env, defect(symptom="default board"))
    other = Board(env.root / "kanban" / "boards" / "elsewhere" / "kanban.db", NOW)
    fill(other, 40)
    d = defect(symptom="other board", board="elsewhere")
    result = sh._over_cap(d, d["fingerprint"], "elsewhere", env.store, other, NOW, {})
    paths = [Path(r["digest_report"]["path"]) for r in (default, result)]
    assert paths[0] != paths[1]
    assert [json.loads(p.read_text())["board"] for p in paths] == ["default", "elsewhere"]
    assert all(json.loads(p.read_text())["unique_count"] == 1 for p in paths)
    unsafe = sh._digest_path(env.store, "../../a/b", NOW)
    collision = sh._digest_path(env.store, ".._.._a_b", NOW)
    assert unsafe != collision and unsafe.is_relative_to(Path(env.store.root) / "selfheal-digests")
    assert env.board.calls == other.calls == []


def test_digest_concurrent_merges_retain_all_unique_fingerprints(env):
    from concurrent.futures import ThreadPoolExecutor
    ds = [defect(symptom=f"concurrent {word}") for word in ("alpha", "bravo", "charlie", "delta")]
    def record(d):
        return sh._record_digest(env.store, None, NOW, 40, 40, d, d["fingerprint"], "defect")
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(record, ds))
    path = Path(results[0]["path"])
    report = json.loads(path.read_text())
    assert set(report["fingerprints"]) == {d["fingerprint"] for d in ds}
    assert report["unique_count"] == 4
    assert path.with_suffix(".md").read_text() == sh._digest_markdown(report)
    assert list(path.parent.glob(".digest-*")) == []


def test_atomic_report_replace_failure_preserves_previous_json_and_pending(env, monkeypatch):
    fill(env.board, 40)
    first = file(env, defect(symptom="before disk failure"))
    path = Path(first["digest_report"]["path"])
    before = path.read_bytes(), path.with_suffix(".md").read_bytes()
    replace = sh.os.replace
    def fail(src, dst):
        if Path(dst) == path:
            raise OSError("fixture replace failure")
        return replace(src, dst)
    monkeypatch.setattr(sh.os, "replace", fail)
    d = defect(symptom="after disk failure")
    result = file(env, d)
    assert result["digest_report"] is None and "replace failure" in result["error"]
    assert (path.read_bytes(), path.with_suffix(".md").read_bytes()) == before
    with sh.contextlib.closing(sqlite3.connect(Path(env.store.root) / "selfheal_pending.sqlite")) as db:
        assert {fp for fp, in db.execute("SELECT fp FROM pending")} == {
            defect(symptom="before disk failure")["fingerprint"], d["fingerprint"]}
    assert env.board.calls == [] and list(path.parent.glob(".digest-*")) == []


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



@pytest.mark.parametrize("count", [39, 40, 41])
def test_receipt_verifier_obeys_daily_card_cap(env, monkeypatch, count):
    """Receipt recovery must enforce the card count even with spend/concurrency admission open."""
    monkeypatch.setattr(sh, "budget", OPEN)
    fill(env.board, count)
    d = defect(component="kanban", symptom="completed owner missing receipt")
    fp = d["fingerprint"]
    lane = sh.LaneState(env.store.root)
    lane.update(fp, status="approved", component=d["component"], symptom=d["symptom"],
                fix_card="t_owner001")

    verifier = sh._recover_completion_receipt(fp, env.store, env.board, NOW)
    st = lane.get(fp)
    assert st["status"] == "approved"
    if count < 40:
        assert verifier and st["verify_card"] == verifier
        assert len(env.board.creates()) == 1
        assert env.board.rows("id = ?", (verifier,))[0][1].startswith(sh.VERIFY_TITLE)
        assert sh.cards_created_today(None, NOW) == 40
        assert not events(env.store, "selfheal_over_cap")
        return

    assert verifier is None
    assert not st.get("verify_card")
    assert not [row for row in env.board.rows() if row[1].startswith(sh.VERIFY_TITLE)]
    (cap,) = events(env.store, "selfheal_over_cap")
    assert (cap["kind"], cap["fingerprint"], cap["count"], cap["cap"]) == ("receipt", fp, count, 40)
    assert env.board.calls == []
    report = json.loads(Path(cap["digest_report"]["path"]).read_text())
    assert report["fingerprints"][fp]["kind"] == "receipt"
    assert sh.cards_created_today(None, NOW) == count

    # Still capped: no verifier and no duplicate digest/comment across retries.
    assert sh._recover_completion_receipt(fp, env.store, env.board, NOW + 60) is None
    assert env.board.calls == []
    assert json.loads(Path(cap["digest_report"]["path"]).read_text())["unique_count"] == 1
    # Tomorrow's real count releases the same approved lane, without a receipt inferred from the cap.
    env.board.now = MIDNIGHT + 60
    verifier = sh._recover_completion_receipt(fp, env.store, env.board, MIDNIGHT + 60)
    assert verifier and lane.get(fp)["verify_card"] == verifier
    assert sh.cards_created_today(None, MIDNIGHT + 60) == 1


@pytest.mark.parametrize("count", [39, 40])
def test_undeployed_card_obeys_daily_cap(env, count):
    """An overdue deployment check shares the cap, not just ordinary defects/backports."""
    fill(env.board, count)
    d = defect(component="agent", symptom="host has not synced its fix")
    fp = d["fingerprint"]
    key = f"selfheal:{fp}:undeployed:laptop:abc123def456"
    st = {"status": "fixed", "fix_card": "t_fixed", "fixed_at": NOW - 80 * 3600}
    lane = sh.LaneState(env.store.root)
    lane.update(fp, **st)
    before = lane.get(fp)
    dec = {"key": key, "label": "Laptop-A", "sha": "abc123def456" + "a" * 28,
           "last_sync": NOW - 90 * 3600, "deployed_at": None, "overdue": True,
           "at": NOW - 60, "why": "host still lacks the fix after 80 hours"}
    out = sh._file_undeployed(d, fp, st, dec, env.store, "live", env.board, NOW, HUB, OPEN)

    if count == 39:
        assert out["action"] == "not_deployed" and out["card"]
        assert len(env.board.creates()) == 1
        assert env.board.rows("idempotency_key = ?", (key,))[0][0] == out["card"]
        assert lane.get(fp)["undeployed"] == {key: out["card"]}
        assert sh.cards_created_today(None, NOW) == 40
        assert events(env.store, "selfheal_over_cap") == []
    else:
        assert out["action"] == "skip" and out["over_cap"] is True
        assert out["not_deployed"] is True
        assert env.board.rows("idempotency_key = ?", (key,)) == []
        assert lane.get(fp) == before  # no poisoned dedup state: next occurrence may retry
        assert env.board.calls == [] and out["digest"] is None
        report = json.loads(Path(out["digest_report"]["path"]).read_text())
        assert report["fingerprints"][fp]["kind"] == "undeployed"
        (event,) = events(env.store, "selfheal_over_cap")
        assert event["kind"] == "undeployed" and event["count"] == event["cap"] == 40
        assert event["digest_report"] == out["digest_report"]
        assert sh.cards_created_today(None, NOW) == 40



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
