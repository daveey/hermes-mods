"""Self-heal dedupe: canonical keys for machine signals, a cheap-model duplicate check, and a pending queue that holds
one row per defect.

Measured 2026-11-19: over 6 days 284 detections made 279 fingerprints. Machine-signal symptoms (and the fingerprints
their collectors computed) carried the varying payload (the reply quote a coach re-check failed on, a cron job's
stderr, the guard's refusal sub-type, an exception message), and the in-chat judges reword every complaint, so the
exact-fingerprint dedupe never fired and each occurrence became its own heavy card.
"""
import importlib.util
from contextlib import closing
import json
import sqlite3
import subprocess
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


sh = load("coach_self_heal_dedupe_keys_test", ROOT / "self_heal.py")
core = load("coach_core_dedupe_keys_test", ROOT / "core.py")
sig = load("coach_signals_dedupe_keys_test", ROOT / "signals.py")

NOW = 1794528000.0 + 15 * 3600  # 2026-11-13 15:00 UTC
HUB = {}  # no hub_ssh: this host is the hub and files locally
OPEN = lambda board=None, now=None: {"usd": 1.0, "cap": 200.0, "running": 0, "ok_usd": True, "ok_running": True,
                                     "available": True, "why": ""}
SHUT = lambda board=None, now=None: {"usd": 200.0, "cap": 200.0, "running": 0, "ok_usd": False, "ok_running": True,
                                     "available": True, "why": "daily budget spent"}


class Board:
    """Fake `hermes kanban` over a real tasks table (create honours the idempotency key; comment is recorded)."""

    def __init__(self, db: Path, now: float):
        self.db, self.now, self.calls, self.comments = db, now, [], []
        db.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(db)) as con, con:
            con.execute("CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, title TEXT, body TEXT, "
                        "status TEXT NOT NULL, created_by TEXT, created_at INTEGER NOT NULL, idempotency_key TEXT)")

    def add(self, tid, key=None, body="", status="ready", created_by="self-heal", created_at=None, title="t"):
        with closing(sqlite3.connect(self.db)) as con, con:
            con.execute("INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (tid, title, body, status, created_by, int(created_at or self.now), key))

    def __call__(self, argv, capture_output=True, text=True, input=None, timeout=None):
        argv = list(argv)
        self.calls.append(argv)
        if "create" in argv:
            def opt(flag):
                return argv[argv.index(flag) + 1] if flag in argv else None
            key = opt("--idempotency-key")
            with closing(sqlite3.connect(self.db)) as con:
                row = con.execute("SELECT id FROM tasks WHERE idempotency_key = ? AND status != 'archived'",
                                  (key,)).fetchone()
                n = con.execute("SELECT count(*) FROM tasks").fetchone()[0]
            if row:
                return SimpleNamespace(returncode=0, stdout=json.dumps({"id": row[0]}), stderr="")
            tid = f"t_{n + 1:08x}"
            self.add(tid, key=key, body=opt("--body"), created_by=opt("--created-by"), title=argv[argv.index("create") + 1])
            return SimpleNamespace(returncode=0, stdout=json.dumps({"id": tid}), stderr="")
        if "comment" in argv:
            i = argv.index("comment")
            self.comments.append((argv[i + 1], argv[i + 2]))
        if "show" in argv:
            return SimpleNamespace(returncode=0, stdout=json.dumps({"task": {"status": "ready"}}), stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def creates(self):
        return [a for a in self.calls if "create" in a]


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = tmp_path / "kanban-root"
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(root))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    for var in ("COACH_SELFHEAL_ONE_CARD", "COACH_SELFHEAL_DAILY_CAP"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("COACH_SELFHEAL_DUPE_CHECK", "0")  # the dupe tests turn it on with a stub judge
    return SimpleNamespace(store=core.Store(tmp_path / "coach"), board=Board(root / "kanban.db", NOW))


def events(store, name):
    p = Path(store.root) / "log.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines() if json.loads(x).get("event") == name] if p.exists() else []


def pending_rows(store):
    with closing(sqlite3.connect(Path(store.root) / "selfheal_pending.sqlite")) as con:
        return con.execute("SELECT fp, payload FROM pending ORDER BY fp").fetchall()


def signal(symptom, component="agent", fp=None, session="20261113_140000_aaaaaa", **kw):
    """A machine signal as the collector raised it: its own (payload-bearing) fingerprint. `produced` hands it over."""
    d = {"component": component, "symptom": symptom, "source": "signal", "kind": kw.pop("kind", "coach"),
         "session_id": session, "host": "hermes-hub", "at": NOW - 60, "details": symptom,
         "fingerprint": fp or sh.fingerprint(component, symptom)}
    d.update(kw)
    return d


def complaint(symptom, component="desktop", session="20261113_140000_bbbbbb", quote="the sidebar is broken again"):
    return {"component": component, "symptom": symptom, "source": "complaint", "kind": "hermes_bug", "p_defect": 0.9,
            "session_id": session, "quote": quote, "host": "macbook", "at": NOW - 60,
            "fingerprint": sh.fingerprint(component, symptom)}


def file(env, d, now=NOW, budget_fn=OPEN):
    env.board.now = now
    return sh.file_defect(d, store=env.store, m="live", runner=env.board, now=now, cfg=HUB, budget_fn=budget_fn)


def produced(d):
    """The file_defect input the real producer (signals.to_defect) makes of collector signal ``d``."""
    return sig.to_defect({"kind": d.get("kind"), "component": d["component"], "symptom": d["symptom"],
                          "details": d.get("details") or "", "at": d.get("at"), "fp": d["fingerprint"],
                          "session_id": d.get("session_id"), "host": d.get("host")})


def fp_of(d):
    return produced(d)["fingerprint"]


# ── 1. canonical keys ───────────────────────────────────────────────────────────────────────────────────────────

def test_report_only_revised_failures_with_different_quotes_share_a_fingerprint():
    a = signal("agent's revised reply still failed the coach report_only check: \"I'll file a card for the CI wait\"",
               fp="agent-1111111111", session="20261113_100000_aaaaaa")
    b = signal("agent's revised reply still failed the coach report_only check (revised reply left a different "
               "problem): \"the hub is fine now, nothing needs you\"", fp="agent-2222222222",
               session="20261114_110000_bbbbbb")
    assert fp_of(a) == fp_of(b)
    assert sh.FP_RE.match(fp_of(a))
    other_rule = signal("agent's revised reply still failed the coach pr_state_claim check: \"PR is merged\"")
    assert fp_of(other_rule) != fp_of(a)  # another rule is another defect
    assert produced(a)["raw_fingerprint"] == "agent-1111111111"  # the collector's id is kept


def test_same_cron_job_different_stderr_is_one_fingerprint_two_jobs_are_two():
    a = signal("cron job 'nightly-e2e-report' failed on hermes-hub: Script exited 1: ConnectionResetError",
               component="cron", kind="cron", fp="cron-aaaaaaaaaa")
    b = signal("cron job 'nightly-e2e-report' failed on macbook: Script execution failed: interpreter is missing",
               component="cron", kind="cron", fp="cron-bbbbbbbbbb")
    c = signal("cron job 'x-reply-watch' failed on hermes-hub: Script exited 1: ConnectionResetError",
               component="cron", kind="cron", fp="cron-cccccccccc")
    assert fp_of(a) == fp_of(b)
    assert fp_of(c) != fp_of(a)


@pytest.mark.parametrize("shapes", [("sqlite", "depth"), ("oversized/file", "other/unknown"),
                                    ("budget/paths", "other/nonregular")])
def test_lifecycle_guard_sub_types_collapse_to_one(shapes):
    fps = {fp_of(signal(f"lifecycle guard fail-closed refusal ({s})", component="gateway", kind="guard_refusal",
                        fp=sh.GUARD_FP_PREFIX + s.replace("/", "")[:12].ljust(12, "0")))
           for s in shapes}
    assert fps == {sh.GUARD_FP}


def test_guard_refusal_signal_emits_one_fingerprint_for_every_shape():
    head = "Blocked: the lifecycle guard could not scan this command or referenced script: "
    tail = ". Nothing in the command is known to contain a gateway lifecycle command, but a script must be scannable."

    def refusal(reason):
        return json.dumps({"output": "", "exit_code": 1, "error": head + reason + tail, "status": "error"})
    hits = [sh.guard_refusal_signal("terminal", {"command": "x"}, refusal(r)) for r in (
        "`/x/kanban.db` is a SQLite database open in this gateway process",
        "the scan budget was exhausted (paths at depth 1)")]
    assert {h["shape"] for h in hits} == {"sqlite", "budget/paths"}  # the shape stays as evidence
    assert {h["fingerprint"] for h in hits} == {sh.GUARD_FP}


def test_exception_keys_on_type_and_function_only():
    a = signal("OperationalError in _execute_write (hermes_state.py): api_content backfill failed for session=1")
    b = signal("OperationalError in _execute_write (hermes_state.py): session outcome refresh (card_change) failed")
    c = signal("OperationalError in observe_counter (store.py) [throughput]: database is locked")
    assert fp_of(a) == fp_of(b) != fp_of(c)


def test_turn_interrupted_stop_causes_stay_distinct():
    """Fixed strings, one per stop cause on purpose (with_stop_cause, gateway-9aaf151a15): no rule merges them."""
    fps = {fp_of(signal(s, component="gateway", kind="interrupted"))
           for s in ("turn interrupted before finishing", "turn interrupted (backend stopped)",
                     "turn interrupted (backend stopped: gateway shutdown)")}
    assert len(fps) == 3


def test_corruption_incident_keeps_its_own_identity():
    d = signal("StateDbCorruptError in _raise_if_db_corrupt (hermes_state.py): database disk image is malformed",
               fp="sqlitecorrupt-0123456789abcdef", incident={"db": "~/.hermes/state.db", "host": "h", "hour": 1})
    assert sh.canonicalize(d)["fingerprint"] == "sqlitecorrupt-0123456789abcdef"


def test_judge_prose_is_never_canonicalized():
    d = complaint("turn interrupted before finishing the sidebar fix", component="gateway")
    assert sh.canonicalize(d)["fingerprint"] == d["fingerprint"]
    assert "raw_fingerprint" not in sh.canonicalize(d)


def test_later_report_only_occurrences_file_no_second_card(env):
    a = signal("agent's revised reply still failed the coach report_only check: \"quote one\"", fp="agent-1111111111")
    b = signal("agent's revised reply still failed the coach report_only check: \"quote two\"", fp="agent-2222222222",
               session="20261113_150000_cccccc")
    first = file(env, produced(a))
    assert first["action"] == "filed"
    assert file(env, produced(b), now=NOW + 120)["action"] == "skip"  # same day: the lane already holds this fingerprint
    later = file(env, produced({**b, "session_id": "20261114_150000_dddddd"}), now=NOW + 86400)
    assert later["action"] == "skip" and first["card"] in later["why"]  # next day: still the one open card
    assert len(env.board.creates()) == 1


def test_old_style_card_owns_a_new_canonical_detection(env):
    """A card filed before canonical keys (under the collector's payload fingerprint) is found by its own evidence."""
    old_fp = "agent-0123456789"
    body = sh.with_fp_line(sh.fence_evidence(
        "- Symptom: agent's revised reply still failed the coach report_only check: \"old quote\"\n"
        "- Component: agent · detector: signal (coach, p=0.9)\n- When: x"), old_fp)
    env.board.add("t_oldstyle", key=f"selfheal:{old_fp}", body=body)
    d = signal("agent's revised reply still failed the coach report_only check: \"new quote\"", fp="agent-9999999999")
    out = file(env, produced(d))
    assert out["action"] == "skip" and out["card"] == "t_oldstyle"
    assert env.board.creates() == [] and events(env.store, "selfheal_dedup_commented")[0]["via"] == "canonical"


# ── 2. duplicate check ──────────────────────────────────────────────────────────────────────────────────────────

def open_card(env, tid, symptom, component="desktop", fp="desktop-abcdef0123"):
    body = sh.with_fp_line(sh.fence_evidence(f"- Symptom: {symptom}\n- Component: {component} · detector: complaint "
                                             f"(hermes_bug, p=0.9)\n- When: x"), fp)
    env.board.add(tid, key=f"selfheal:{fp}", body=body)


def judge_returning(monkeypatch, answer, calls=None):
    def factory():
        def judge(system, prompt):
            if calls is not None:
                calls.append(prompt)
            if isinstance(answer, BaseException):
                raise answer
            return answer
        return judge
    monkeypatch.setattr(sh, "dupe_judge", factory)


@pytest.fixture
def dupe(env, monkeypatch):
    monkeypatch.setenv("COACH_SELFHEAL_DUPE_CHECK", "1")
    open_card(env, "t_sidebar1", "session sidebar reorders itself after every reply")
    return env


def test_same_high_confidence_becomes_a_comment_and_files_nothing(dupe, monkeypatch):
    calls = []
    judge_returning(monkeypatch, json.dumps({"verdict": "same", "id": "t_sidebar1", "confidence": 0.93,
                                             "reason": "both describe the sidebar re-sorting sessions"}), calls)
    out = file(dupe, complaint("sidebar sessions jump around when a reply lands"))
    assert out["action"] == "skip" and out["card"] == "t_sidebar1" and out.get("commented")
    assert dupe.board.creates() == []
    assert len(dupe.board.comments) == 1 and "re-sorting" in dupe.board.comments[0][1]
    assert "session sidebar reorders" in calls[0]  # the open card's symptom was shown to the model
    v = events(dupe.store, "selfheal_dupe_check")[-1]
    assert v["verdict"] == "same" and v["merge"] is True and v["id"] == "t_sidebar1"
    # the next occurrence of the same fingerprint reuses the verdict: no second call
    file(dupe, complaint("sidebar sessions jump around when a reply lands", session="20261113_160000_dddddd"),
         now=NOW + 7200)
    assert len(calls) == 1 and len(dupe.board.comments) == 2 and dupe.board.creates() == []


@pytest.mark.parametrize("answer", [
    json.dumps({"verdict": "new", "id": "", "confidence": 0.9, "reason": "different feature"}),
    json.dumps({"verdict": "same", "id": "t_sidebar1", "confidence": 0.6, "reason": "maybe"}),
    json.dumps({"verdict": "same", "id": "t_unknown", "confidence": 0.99, "reason": "id not offered"}),
    "I cannot tell.",
    RuntimeError("pool exhausted"),
    subprocess.TimeoutExpired(["hermes-pool-llm"], 60),
], ids=["new", "low-confidence", "unknown-id", "unparsable", "error", "timeout"])
def test_new_low_confidence_error_or_timeout_files_a_card(dupe, monkeypatch, answer):
    judge_returning(monkeypatch, answer)
    out = file(dupe, complaint("settings page loses the model picker"))
    assert out["action"] == "filed"
    assert len(dupe.board.creates()) == 1 and dupe.board.comments == []
    assert events(dupe.store, "selfheal_dupe_check")  # every verdict is logged


def test_only_the_same_component_is_offered(dupe, monkeypatch):
    calls = []
    judge_returning(monkeypatch, json.dumps({"verdict": "new", "id": "", "confidence": 0.9, "reason": "x"}), calls)
    file(dupe, complaint("cron runs twice", component="cron"))
    assert calls == []  # no cron candidates: no model call at all
    assert events(dupe.store, "selfheal_dupe_check")[-1]["why"] == "no candidates"


def test_same_as_pending_row_folds_into_it(dupe, monkeypatch):
    queued = complaint("desktop settings drawer does not open on click", session="20261113_090000_eeeeee")
    judge_returning(monkeypatch, json.dumps({"verdict": "new", "id": "", "confidence": 0.9, "reason": "x"}))
    assert file(dupe, queued, budget_fn=SHUT)["action"] == "deferred"
    judge_returning(monkeypatch, json.dumps({"verdict": "same", "id": queued["fingerprint"], "confidence": 0.95,
                                             "reason": "both: settings drawer will not open"}))
    out = file(dupe, complaint("clicking settings does nothing in desktop", session="20261113_091000_ffffff"),
               budget_fn=SHUT)
    assert out["action"] == "skip" and out["folded"] == queued["fingerprint"]
    rows = pending_rows(dupe.store)
    assert [r[0] for r in rows] == [queued["fingerprint"]]
    payload = json.loads(rows[0][1])
    assert payload["occurrence_count"] == 2 and "clicking settings does nothing" in payload["details"]
    assert "settings drawer will not open" in payload["merge_reasons"][0]


# ── 3. pending queue ────────────────────────────────────────────────────────────────────────────────────────────



def test_pending_queue_merges_same_key_detections(env):
    denied = {"action": "deferred", "why": "cap", "retry_at": "2026-11-14T00:00:00Z"}
    for i, quote in enumerate(("quote one", "quote two", "quote three")):
        d = produced(signal(f"agent's revised reply still failed the coach report_only check: \"{quote}\"",
                                   fp=f"agent-{i}{i}{i}{i}{i}{i}{i}{i}{i}{i}", session=f"20261113_14000{i}_aaaaaa",
                                   at=NOW - 600 + i))
        sh._defer_investigation(d, denied, env.store)
    rows = pending_rows(env.store)
    assert len(rows) == 1
    p = json.loads(rows[0][1])
    assert p["occurrence_count"] == 3 and len(p["occurrences"]) == 3
    assert "quote three" in p["symptom"]  # the newest payload leads; the others are listed
    assert "quote one" in p["details"] and "quote two" in p["details"]


def test_retry_folds_rows_queued_under_old_fingerprints(env):
    """Rows deferred before canonical keys (one per payload fingerprint) collapse into one before the filing pass."""
    db = sh._pending_db(env.store)
    with db:
        for i, err in enumerate(("Script exited 1: boom", "Script exited 2: bang", "Traceback: KeyError")):
            d = signal(f"cron job 'nightly-e2e-report' failed on hermes-hub: {err}", component="cron", kind="cron",
                       fp=f"cron-{i}{i}{i}{i}{i}{i}{i}{i}{i}{i}", at=NOW - 900 + i)
            db.execute("INSERT INTO pending VALUES (?, ?, ?, ?)", (d["fingerprint"], json.dumps(d), NOW + 3600, f"v{i}"))
        other = signal("cron job 'x-reply-watch' failed on hermes-hub: boom", component="cron", kind="cron",
                       fp="cron-9999999999")
        db.execute("INSERT INTO pending VALUES (?, ?, ?, ?)", (other["fingerprint"], json.dumps(other), NOW + 3600, "v9"))
    db.close()
    sh.retry_deferred(store=env.store, now=NOW, filer=lambda *a, **k: pytest.fail("nothing is due"))
    rows = dict(pending_rows(env.store))
    assert len(rows) == 2
    job = json.loads(rows[fp_of(signal("cron job 'nightly-e2e-report' failed on h: x", component="cron"))])
    assert job["occurrence_count"] == 3 and job["raw_fingerprint"] == "cron-2222222222"
    assert "cron-9999999999" in rows  # a lone row keeps its own id (its lane and rechecks name it)
    assert len(events(env.store, "selfheal_pending_folded")) == 3


def test_redeferring_the_same_row_does_not_double_count(env):
    denied = {"action": "deferred", "why": "cap", "retry_at": "2026-11-14T00:00:00Z"}
    d = produced(signal("cron job 'a' failed on hub: x", component="cron", kind="cron"))
    sh._defer_investigation(d, denied, env.store)
    sh._defer_investigation(json.loads(pending_rows(env.store)[0][1]), denied, env.store)
    (row,) = pending_rows(env.store)
    assert json.loads(row[1]).get("occurrence_count", 1) == 1 and "occurrences" not in json.loads(row[1])


# ── review fixes ────────────────────────────────────────────────────────────────────────────────────────────────

def test_exception_key_keeps_file_and_plugin():
    base = signal("KeyError in register (__init__.py) [foo]: 'x'")
    assert fp_of(base) == fp_of(signal("KeyError in register (__init__.py) [foo]: 'another message'"))
    assert fp_of(base) != fp_of(signal("KeyError in register (__init__.py) [bar]: 'x'"))
    assert fp_of(signal("KeyError in get (cache.py): x")) != fp_of(signal("KeyError in get (config.py): x"))


def test_comment_cap_counts_every_fingerprint_that_reaches_one_card(env):
    owner = {"id": "t_owner", "status": "ready", "key": "k", "title": "t", "via": "dupe-check"}
    day = sh._utc_day(NOW)
    sh._save_json_state(env.store, sh.DEDUP_STATE, {
        f"agent-{i:010d}": {"owner": "t_owner", "at": NOW - 7200, "day": day, "n": 3, "sessions": ["s"]}
        for i in range(4)})  # 4 fingerprints x 3 comments = DEDUP_MAX_PER_DAY on this card today
    assert sh.DEDUP_MAX_PER_DAY == 12
    out = sh._dedupe_open_owner(complaint("yet another rewording"), "desktop-9999999999", None, env.store,
                                env.board, NOW, owner=owner, reason="duplicate check")
    assert out["action"] == "skip" and out["commented"] is False and env.board.comments == []
    assert "12 occurrence comments" in events(env.store, "selfheal_dedup_held")[-1]["why"]


def test_guard_refusal_keeps_an_old_per_shape_lane(env):
    head = "Blocked: the lifecycle guard could not scan this command or referenced script: "
    err = head + "`/x/kanban.db` is a SQLite database open in this gateway process. Nothing in the command is known " \
                 "to contain a gateway lifecycle command."
    hit = sh.guard_refusal_signal("terminal", {"command": "x"},
                                  json.dumps({"output": "", "exit_code": 1, "error": err, "status": "error"}))
    old = hit["raw_fingerprint"]
    assert old != sh.GUARD_FP and hit["canon_fingerprint"] == sh.GUARD_FP
    env.board.add("t_oldguard", key=f"selfheal:{old}", body=sh.with_fp_line("old guard card", old))
    sh.LaneState(env.store.root).update(old, status="filed", card="t_oldguard", gen=1,
                                        filed_day=sh._utc_day(NOW), filed_at=NOW - 600)
    out = file(env, {**hit, "session_id": "20261113_140000_aaaaaa", "host": "hermes-hub", "at": NOW - 60})
    assert env.board.creates() == [], out
    assert "t_oldguard" in json.dumps(out)


def test_fold_into_pending_bumps_the_row_version(env):
    denied = {"action": "deferred", "why": "cap", "retry_at": "2026-11-14T00:00:00Z"}
    d = complaint("drawer will not open")
    sh._defer_investigation(d, denied, env.store)
    with closing(sqlite3.connect(Path(env.store.root) / "selfheal_pending.sqlite")) as con:
        v0 = con.execute("SELECT version FROM pending").fetchone()[0]
    assert sh.fold_into_pending(env.store, d["fingerprint"], complaint("settings click does nothing",
                                                                      session="20261113_150000_eeeeee"), "same")
    with closing(sqlite3.connect(Path(env.store.root) / "selfheal_pending.sqlite")) as con:
        assert con.execute("SELECT version FROM pending").fetchone()[0] != v0
