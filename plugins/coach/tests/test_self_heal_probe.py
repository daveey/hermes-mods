"""self-heal: a deliberate live probe of a detector never becomes a defect card (agent-771fbd2b85).

A session whose FIRST user message starts with `[selfheal-probe]` (or the older "Live test of a detector" wording)
is a planned failure. Every entry path must drop it with `selfheal_skip why=probe`: the in-process hooks
(handle), the hub's file_defect, and the deferred-retry pass that refiled agent-771fbd2b85 after its pending row
was deleted by hand. A copy of the same session without the marker still files (control).
"""
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


sh = load("coach_self_heal_probe_test", ROOT / "self_heal.py")
core = load("coach_core_test_probe", ROOT / "core.py")
plugin = load("coach_plugin_test_probe", ROOT / "__init__.py")

NOW = 1793413298.0  # 2026-10-31 03:21Z, when agent-771fbd2b85 was detected
OPEN = lambda board=None, now=None: {"usd": 1.0, "cap": 25.0, "running": 0, "ok_usd": True, "ok_running": True,
                                     "why": ""}
BUSY = lambda board=None, now=None: {"usd": 1.0, "cap": 25.0, "running": 3, "ok_usd": True, "ok_running": False,
                                     "why": ""}
PROBE_SID = "local-probe-session"
REAL_SID = "local-real-session"
CMD = 'sqlite3 "/nonexistent-dir-xyz/q.db" "select 1"'
SYMPTOM = "repeated terminal error: Error: unable to open database : unable to open database fil"


class Runner:
    def __init__(self):
        self.calls, self.n = [], 0

    def __call__(self, argv, capture_output=True, text=True, input=None, timeout=None):
        from types import SimpleNamespace
        self.calls.append(list(argv))
        if "create" in argv:
            self.n += 1
            return SimpleNamespace(returncode=0, stdout=json.dumps({"id": f"t_{self.n:08x}"}), stderr="")
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    def creates(self):
        return [a for a in self.calls if "create" in a]


def events(store, name):
    p = Path(store.root) / "log.jsonl"
    return [r for r in (json.loads(x) for x in p.read_text().splitlines())
            if r.get("event") == name] if p.exists() else []


def pending_rows(store):
    p = Path(store.root) / "selfheal_pending.sqlite"
    if not p.exists():
        return 0
    db = sqlite3.connect(p)
    try:
        return db.execute("SELECT count(*) FROM pending").fetchone()[0]
    finally:
        db.close()


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A HERMES_HOME whose state.db holds a probe session and an ordinary session with the same failure."""
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(h))
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    db = sqlite3.connect(h / "state.db")
    db.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, "
               "role TEXT NOT NULL, content TEXT, timestamp REAL NOT NULL, active INTEGER NOT NULL DEFAULT 1, "
               "compacted INTEGER NOT NULL DEFAULT 0)")
    rows = [
        (PROBE_SID, "user", f"[selfheal-probe] run {CMD} three times"),
        (PROBE_SID, "assistant", "done"),
        (REAL_SID, "user", "check the coach db for me"),
        (REAL_SID, "assistant", "It failed three times."),
        # a later turn of a real chat that mentions the marker does not make it a probe
        (REAL_SID, "user", "[selfheal-probe] is the tag, right?"),
    ]
    for i, (sid, role, content) in enumerate(rows):
        db.execute("INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
                   (sid, role, content, NOW - 100 + i))
    db.commit()
    db.close()
    return h


@pytest.fixture
def store(home):
    return core.Store(home / "coach")


def hit():
    fp = sh.fingerprint("agent", SYMPTOM)
    return {"source": "signal", "kind": "signal", "p_defect": 0.85, "component": "agent", "symptom": SYMPTOM,
            "fingerprint": fp, "evidence": f"terminal failed 3x with the same error: {CMD}"}


def defect(session_id):
    return {**hit(), "session_id": session_id, "quote": "terminal failed 3x", "host": sh.host_name(),
            "platform": "cli", "at": NOW}


def file_live(r, **kw):
    return lambda d, store=None, m=None, now=None, **_: sh.file_defect(d, store=store, m="live", runner=r,
                                                                       now=now, cfg={}, budget_fn=OPEN, **kw)


# ── the marker itself ──
@pytest.mark.parametrize("text, probe", [
    ("[selfheal-probe] run sqlite3 three times", True),
    ("  [SELFHEAL-PROBE] anything", True),
    ("Live test of a detector. Run exactly this terminal command three separate times", True),
    ("please fix the [selfheal-probe] handling", False),
    ("the live test of a detector failed", False),
    ("", False),
])
def test_probe_marker(text, probe):
    assert bool(sh.PROBE_RE.match(text)) is probe


def test_only_the_first_user_message_marks_a_probe(home):
    assert sh.probe_session(PROBE_SID)
    assert not sh.probe_session(REAL_SID)
    assert not sh.probe_session("no-such-session")


# ── entry path 1: handle() (in-process hooks), live mode ──
def test_handle_skips_a_probe_session_without_queueing_or_filing(store, monkeypatch):
    monkeypatch.setenv("COACH_SELFHEAL", "live")
    filed = []
    sh.handle(hit(), PROBE_SID, store, evidence="done", m="live", filer=lambda d, **kw: filed.append(d))
    assert filed == []
    skips = events(store, "selfheal_skip")
    assert skips and skips[-1]["why"] == "probe" and skips[-1]["session_id"] == PROBE_SID
    assert events(store, "selfheal_detect")[-1]["probe"] is True
    assert not (Path(store.root) / "selfheal_queue.jsonl").exists()
    # the probe does not use up the fingerprint's one filing per day: a real hit later still files
    sh.handle(hit(), REAL_SID, store, evidence="It failed three times.", m="live",
              filer=lambda d, **kw: filed.append(d))
    assert [d["session_id"] for d in filed] == [REAL_SID]


def test_handle_uses_the_turn_ask_when_the_session_is_not_stored_yet(store):
    filed = []
    sh.handle(hit(), "unsaved-session", store, m="live", filer=lambda d, **kw: filed.append(d),
              ask=f"[selfheal-probe] run {CMD} three times")
    assert filed == [] and events(store, "selfheal_skip")[-1]["why"] == "probe"


# ── entry path 2: the hub's file_defect (forwarded hosts, digest backstop, monitors) ──
def test_file_defect_skips_a_probe_session_and_files_the_control(store):
    r = Runner()
    out = sh.file_defect(defect(PROBE_SID), store=store, m="live", runner=r, now=NOW, cfg={}, budget_fn=OPEN)
    assert out == {"action": "skip", "why": "probe"} and r.creates() == []
    ctl = sh.file_defect(defect(REAL_SID), store=store, m="live", runner=r, now=NOW, cfg={}, budget_fn=OPEN)
    assert ctl["action"] == "filed" and len(r.creates()) == 1


def test_file_defect_honours_an_explicit_probe_flag_from_another_host(store):
    r = Runner()
    d = {**defect("macbook-session"), "host": "macbook", "probe": True}
    assert sh.file_defect(d, store=store, m="live", runner=r, now=NOW, cfg={}, budget_fn=OPEN)["action"] == "skip"
    assert r.creates() == []


# ── entry path 3: deferred retry (how agent-771fbd2b85 was refiled) ──
def test_deferred_probe_is_dropped_on_retry_not_filed(store):
    r = Runner()
    # A pending row written before the guard existed (or by an older host): the retry must still drop it.
    sh._defer_investigation(defect(PROBE_SID), {"action": "deferred", "retry_at": sh._iso(NOW + 60),
                                                "why": "self-heal concurrency limit reached"}, store)
    assert pending_rows(store) == 1
    out = sh.retry_deferred(store, now=NOW + 3600, filer=file_live(r))
    assert r.creates() == [] and pending_rows(store) == 0
    assert out and out[0]["action"] == "skip" and out[0]["why"] == "probe"


def test_deferred_control_still_files_after_the_cap_clears(store):
    r = Runner()
    out = sh.file_defect(defect(REAL_SID), store=store, m="live", runner=r, now=NOW, cfg={}, budget_fn=BUSY)
    assert out["action"] == "deferred" and pending_rows(store) == 1
    sh.retry_deferred(store, now=NOW + 3600, filer=file_live(r))
    assert len(r.creates()) == 1 and pending_rows(store) == 0


def test_probe_under_cap_pressure_never_reaches_the_pending_queue(store):
    r = Runner()
    out = sh.file_defect(defect(PROBE_SID), store=store, m="live", runner=r, now=NOW, cfg={}, budget_fn=BUSY)
    assert out["action"] == "skip" and pending_rows(store) == 0


def test_fix_brief_tells_live_proofs_to_use_the_marker_not_row_deletion():
    brief = sh.fix_brief("agent-771fbd2b85", {"component": "agent", "symptom": SYMPTOM, "inv_card": "t_x"})
    assert "[selfheal-probe]" in brief and "selfheal_skip why=probe" in brief
    assert "never delete selfheal_pending rows by hand" in brief


# ── entry path 4: the real plugin hooks ──
def test_post_llm_call_admission_from_a_probe_session_is_skipped(store, monkeypatch):
    monkeypatch.setenv("COACH_SELFHEAL", "shadow")

    def llm(system, prompt):
        return json.dumps({"p_defect": 0.95, "kind": "admission", "component": "agent", "symptom": SYMPTOM})
    _, hooks = plugin.build(store=store, checkout=None, selfheal_sync=True, selfheal_llm=llm, selfheal_confirm=None)
    hooks["post_llm_call"](session_id=PROBE_SID, assistant_response="You're right, that failed: my mistake.")
    assert events(store, "selfheal_would_file") == []
    assert events(store, "selfheal_skip")[-1]["why"] == "probe"
    hooks["post_llm_call"](session_id=REAL_SID, assistant_response="You're right, that failed: my mistake.")
    assert len(events(store, "selfheal_would_file")) == 1


def test_pre_llm_call_complaint_from_a_probe_session_is_skipped(store, monkeypatch):
    monkeypatch.setenv("COACH_SELFHEAL", "shadow")

    def llm(system, prompt):
        return json.dumps({"p_defect": 0.95, "kind": "hermes_bug", "component": "agent", "symptom": "x broken"})
    _, hooks = plugin.build(store=store, checkout=None, selfheal_sync=True, selfheal_llm=llm, selfheal_confirm=None)
    hooks["pre_llm_call"](session_id=PROBE_SID, user_message="it is still broken")
    assert events(store, "selfheal_would_file") == []
    assert events(store, "selfheal_skip")[-1]["why"] == "probe"
