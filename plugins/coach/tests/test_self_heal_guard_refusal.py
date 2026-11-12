"""Lifecycle-guard false refusals reach the self-heal lane: one card per shape, later occurrences comment on the open
card, a fresh card once that card is done, nothing for a true lifecycle refusal or a `[selfheal-probe]` session.

The refusal texts below are verbatim from core main (tools/terminal_tool_guards.py gateway_lifecycle_block, cron/
lifecycle_guard.py _unreadable_reason / _budget_exhausted / check_gateway_lifecycle). The core-backed tests at the end
produce the same refusals from the real guard and skip where core is not importable (public CI)."""
import contextlib
import functools
import hashlib
import importlib.util
import json
import sqlite3
import sys
import time
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


core = load("coach_core_test_guard_refusal", ROOT / "core.py")
plugin = load("coach_plugin_test_guard_refusal", ROOT / "__init__.py")

HUB = {}  # no hub configured: this host is the hub and files locally
OPEN = lambda board=None, now=None: {"usd": 1.0, "cap": 25.0, "running": 0, "ok_usd": True, "ok_running": True,
                                     "why": ""}

TAIL = (". Nothing in the command is known to contain a gateway lifecycle command, but a script the command executes "
        "must be scannable (a regular text file under 1 MiB) before it can run inside the gateway process.")
HEAD = "Blocked: the lifecycle guard could not scan this command or referenced script: "


def terminal_refusal(reason: str) -> str:
    """The terminal tool's result for a fail-closed refusal (core _blocked_json envelope)."""
    return json.dumps({"output": "", "exit_code": 1, "error": HEAD + reason + TAIL, "status": "error"})


SQLITE = terminal_refusal("`/Users/x/.hermes/kanban.db` is a SQLite database open in this gateway process")
SQLITE_2 = terminal_refusal("`/Users/x/.hermes/state.db` is a SQLite database open in this gateway process")
BUDGET = terminal_refusal("the scan budget was exhausted (paths at depth 1)")
OVERSIZED = terminal_refusal("`/tmp/big.log` is larger than the scan cap (1048576 bytes) or the remaining walk budget")
CRON = ("Blocked: the lifecycle guard could not scan this cron job or referenced script: `/h/.hermes/scripts/x.sh` "
        "lives on a cloud-synced path (iCloud Drive / ~/Library/CloudStorage) that the guard refuses to open. Nothing "
        "in the job is known to contain a gateway lifecycle command, but a script the job executes must be scannable "
        "(a regular text file under 1 MiB) before it can run.")
TRUE_LIFECYCLE = json.dumps({"output": "", "exit_code": 1, "status": "error", "error": (
    "Blocked: command or referenced script cannot restart, stop, or uninstall the gateway from inside the gateway "
    "process. The gateway would kill this command before it could complete (SIGTERM propagates to child processes). "
    "Run `hermes gateway restart` from a separate shell outside the running gateway.")})


class Runner:
    """Fake subprocess.run for `hermes kanban`: create -> new id, comment -> ok, show -> the card's status."""

    def __init__(self):
        self.calls, self.n, self.status = [], 0, {}

    def __call__(self, argv, capture_output=True, text=True, input=None, timeout=None):
        argv = list(argv)
        self.calls.append(argv)
        if "create" in argv:
            self.n += 1
            tid = f"t_{self.n:08x}"
            self.status[tid] = "ready"
            return SimpleNamespace(returncode=0, stdout=json.dumps({"id": tid}), stderr="")
        if "show" in argv:
            tid = argv[argv.index("show") + 1]
            if tid not in self.status:
                return SimpleNamespace(returncode=1, stdout="", stderr="no such task")
            return SimpleNamespace(returncode=0, stdout=json.dumps({"task": {"id": tid, "status": self.status[tid]}}),
                                   stderr="")
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    def creates(self):
        return [a for a in self.calls if "create" in a]

    def comments(self):
        return [a for a in self.calls if "comment" in a]


def body(argv):
    return argv[argv.index("--body") + 1]


def ev(store, name):
    p = Path(store.root) / "log.jsonl"
    return [r for r in (json.loads(x) for x in p.read_text().splitlines())
            if r.get("event") == name] if p.exists() else []


@pytest.fixture
def lane(tmp_path, monkeypatch):
    """The plugin's hooks over a live hub lane whose `hermes kanban` is a fake runner."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    monkeypatch.setenv("COACH_SELFHEAL", "live")
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    store = core.Store(tmp_path / "coach")
    _, hooks = plugin.build(store=store, checkout=None, selfheal_sync=True, selfheal_llm=lambda s, p: "{}")
    sh = plugin._load("self_heal")
    r = Runner()
    monkeypatch.setattr(sh, "file_defect", functools.partial(sh.file_defect, runner=r, cfg=HUB, budget_fn=OPEN))
    return SimpleNamespace(store=store, hooks=hooks, sh=sh, runner=r)


def refuse(lane, result, session_id="20261112_090000_aaaaaa", command="sqlite3 ~/.hermes/kanban.db .tables",
           tool="terminal"):
    lane.hooks["post_tool_call"](tool_name=tool, args={"command": command}, result=result, session_id=session_id,
                                 status="error")


def test_three_false_refusals_of_one_shape_open_one_card_and_comment(lane):
    refuse(lane, SQLITE, session_id="20261112_090000_aaaaaa")
    refuse(lane, SQLITE_2, session_id="20261112_090100_bbbbbb")  # another db file: the same shape
    refuse(lane, SQLITE, session_id="20261112_090200_cccccc")
    creates = lane.runner.creates()
    assert len(creates) == 1, creates
    card = "t_00000001"
    assert "lifecycle guard fail-closed refusal (sqlite)" in body(creates[0])
    assert "kanban.db` is a SQLite database open" in body(creates[0])
    comments = lane.runner.comments()
    assert len(comments) == 2 and all(card in c for c in comments), comments
    assert "state.db" in " ".join(comments[0])  # the occurrence's own evidence rides the comment
    assert len(ev(lane.store, "selfheal_occurrence_comment")) == 2
    det = ev(lane.store, "selfheal_detect")
    assert len(det) == 3 and {d["fingerprint"] for d in det} == {det[0]["fingerprint"]}
    assert det[0]["fingerprint"].startswith("lifecycleguard-")


def test_second_shape_opens_second_card(lane):
    refuse(lane, SQLITE)
    refuse(lane, BUDGET, command="bash ./deploy.sh", session_id="20261112_090100_bbbbbb")
    refuse(lane, BUDGET, command="bash ./deploy.sh", session_id="20261112_090200_cccccc")
    creates = lane.runner.creates()
    assert len(creates) == 2, creates
    assert "refusal (sqlite)" in body(creates[0]) and "refusal (budget/paths)" in body(creates[1])
    assert len(lane.runner.comments()) == 1  # the 2nd budget refusal comments on the budget card
    assert "t_00000002" in lane.runner.comments()[0]


def test_refusal_after_card_done_opens_fresh_card(lane):
    """The stale path: the investigation card ended (done, later archived) without a fix or proposal. The next
    occurrence reconciles that card inline, marks the lane stale and files a fresh generation the same UTC day."""
    refuse(lane, OVERSIZED, command="sh /tmp/big.log")
    assert len(lane.runner.creates()) == 1
    lane.runner.status["t_00000001"] = "done"  # the investigation finished without a fix (no proposal)
    refuse(lane, OVERSIZED, command="sh /tmp/big.log", session_id="20261112_090100_bbbbbb")  # same UTC day
    creates = lane.runner.creates()
    assert len(creates) == 2, creates
    key = creates[1][creates[1].index("--idempotency-key") + 1]
    assert key.endswith(":r2"), key  # a new generation, never the done card's key
    assert not lane.runner.comments()  # nothing commented on the done card
    stale = ev(lane.store, "selfheal_stale")
    assert stale and stale[0]["via"] == "occurrence" and stale[0]["card"] == "t_00000001"
    lane.runner.status["t_00000002"] = "archived"
    refuse(lane, OVERSIZED, command="sh /tmp/big.log", session_id="20261112_090200_cccccc")
    assert len(lane.runner.creates()) == 3


def test_true_lifecycle_refusal_opens_no_card(lane):
    """Regression guard (passes before and after): a real lifecycle refusal is the guard working."""
    for _ in range(3):
        refuse(lane, TRUE_LIFECYCLE, command="bash ./restart-gateway.sh")
    assert not lane.runner.creates() and not lane.runner.comments()
    assert not ev(lane.store, "selfheal_detect")


@pytest.mark.parametrize("tool", ["cronjob_manage", "cronjob"])
def test_cron_create_refusal_is_detected(lane, tool):
    refuse(lane, json.dumps({"error": CRON, "success": False}), tool=tool, command="")
    (c,) = lane.runner.creates()
    assert "refusal (other/cloud)" in body(c)


def test_probe_session_files_nothing(lane, tmp_path):
    db = sqlite3.connect(tmp_path / "home" / "state.db")
    db.execute("create table messages (id integer primary key, session_id text, role text, content text, "
               "timestamp real)")
    db.execute("insert into messages (session_id, role, content, timestamp) values (?,?,?,?)",
               ("20261112_093000_probe1", "user", "[selfheal-probe] run sqlite3 ~/.hermes/kanban.db .tables", 1.0))
    db.commit()
    db.close()
    refuse(lane, SQLITE, session_id="20261112_093000_probe1")
    assert not lane.runner.creates() and not lane.runner.comments()
    (det,) = ev(lane.store, "selfheal_detect")
    assert det["probe"] is True and det["fingerprint"].startswith("lifecycleguard-")
    assert [s["why"] for s in ev(lane.store, "selfheal_skip")] == ["probe"]
    lane_file = Path(lane.store.root) / "selfheal_lane.json"
    assert not lane_file.exists() or "lifecycleguard" not in lane_file.read_text()


@pytest.mark.parametrize("reason,shape", [
    ("`/a/kanban.db` is a SQLite database open in this gateway process", "sqlite"),
    ("`/a/b.log` is larger than the scan cap (1048576 bytes) or the remaining walk budget", "oversized/local"),
    ("`/a/b.sh` read from the backend exceeds the scan cap (1048576 bytes) or the remaining walk budget",
     "oversized/remote"),
    ("the scan budget was exhausted (text at depth 0)", "budget/text"),
    ("the scan budget was exhausted (remote reads at depth 2)", "budget/remote reads"),
    ("the scan depth limit was reached (depth 8)", "depth"),  # the text core's depth-cap owner card specifies
    ("`/dev/zero` is not a regular file", "other/nonregular"),
    ("`/a/x` could not be read", "other/unreadable"),
    ("something new", "other/unknown"),
])
def test_shape_classes(reason, shape):
    sh = plugin._load("self_heal")
    assert sh.guard_refusal_shape(reason) == shape
    hit = sh.guard_refusal_signal("terminal", {"command": "x"}, terminal_refusal(reason))
    assert hit["shape"] == shape and sh.FP_RE.match(hit["fingerprint"])


def test_other_tool_output_is_no_hit():
    sh = plugin._load("self_heal")
    assert sh.guard_refusal_signal("terminal", {}, TRUE_LIFECYCLE) is None
    assert sh.guard_refusal_signal("read_file", {}, "docs mention: the lifecycle guard could not scan this file") is None


def test_quoted_refusal_outside_the_guard_envelope_opens_no_card(lane):
    """B1: tools that merely QUOTE the refusal (a test file, a card body, pytest output) never count."""
    quoted = HEAD + "`/a/kanban.db` is a SQLite database open in this gateway process" + TAIL
    refuse(lane, json.dumps({"content": "1|" + quoted, "total_lines": 1}), tool="read_file")
    refuse(lane, json.dumps({"task": {"id": "t_1", "body": quoted}}), tool="kanban_show")
    refuse(lane, json.dumps({"output": "FAILED test.py - " + quoted, "exit_code": 1, "error": None}))
    refuse(lane, json.dumps({"output": "partial", "exit_code": 1, "error": quoted}))  # it ran: not the guard
    refuse(lane, json.dumps({"output": "", "exit_code": 0, "error": quoted}))
    refuse(lane, json.dumps({"output": "", "exit_code": 1, "error": "see: " + quoted}))  # not at the start
    refuse(lane, quoted)  # bare text, no JSON envelope
    assert not lane.runner.creates() and not lane.runner.comments()
    assert not ev(lane.store, "selfheal_detect")


def test_same_refused_command_retried_within_window_is_one_occurrence(lane):
    refuse(lane, SQLITE)  # files the card
    for _ in range(3):
        refuse(lane, SQLITE, session_id="20261112_090100_bbbbbb")  # one agent retrying the same command
    assert len(lane.runner.creates()) == 1
    assert len(lane.runner.comments()) == 1, lane.runner.comments()
    assert len([s for s in ev(lane.store, "selfheal_skip") if s["why"] == "guard_repeat"]) == 2
    refuse(lane, SQLITE, session_id="20261112_090100_bbbbbb", command="sqlite3 ~/.hermes/state.db .schema")
    assert len(lane.runner.comments()) == 2  # another command in the same session is a new occurrence


def test_dedupe_window_expires(tmp_path):
    sh = plugin._load("self_heal")
    store = core.Store(tmp_path / "coach")
    assert sh.guard_occurrence_new(store, "s", "c", "sqlite", now=1000.0)
    assert not sh.guard_occurrence_new(store, "s", "c", "sqlite", now=1000.0 + sh.GUARD_DEDUPE_S - 1)
    assert sh.guard_occurrence_new(store, "s", "c", "budget/paths", now=1001.0)  # another shape
    assert sh.guard_occurrence_new(store, "s", "c", "sqlite", now=1000.0 + 2 * sh.GUARD_DEDUPE_S)


def _fp(sh, shape):
    return sh.GUARD_FP_PREFIX + hashlib.sha1(shape.encode()).hexdigest()[:12]


def _no_reentry(real):
    """The admission lock, failing at once (instead of spinning 180 s) if it is asked for while already held."""
    held = []

    @contextlib.contextmanager
    def lock(store, live=True, timeout=180.0):
        if held and live:
            raise AssertionError("admission lock re-entered")
        with real(store, live=live, timeout=timeout):
            held.append(live)
            try:
                yield
            finally:
                held.pop()
    return lock


def test_approved_owner_with_done_fix_card_comments_without_relocking(lane, monkeypatch):
    """B2: an `approved` lane whose fix card is done takes an occurrence on a live hub. The inline reconcile runs only
    for `filed`, so receipt recovery (which re-takes the admission lock) is left to sync and never reached here."""
    sh = lane.sh
    fp = _fp(sh, "sqlite")
    lane.runner.status.update({"t_000000aa": "done", "t_000000bb": "done"})
    sh.LaneState(lane.store.root).update(fp, status="approved", card="t_000000aa", fix_card="t_000000bb",
                                         filed_day="2026-11-11", filed_at=1.0, gen=1)
    called = []
    monkeypatch.setattr(sh, "_recover_completion_receipt", lambda *a, **k: called.append(a))
    monkeypatch.setattr(sh, "_admission_lock", _no_reentry(sh._admission_lock))
    t0 = time.monotonic()
    refuse(lane, SQLITE)
    assert time.monotonic() - t0 < 20
    assert not called and not lane.runner.creates()
    (c,) = lane.runner.comments()
    assert "t_000000bb" in c


def test_probe_recognised_from_the_turn_ask_without_a_stored_user_row(lane, tmp_path):
    """B4: a first-turn CLI `-q` probe has no state.db row yet, so only the turn's ask (pre_llm_call) marks it."""
    assert not (tmp_path / "home" / "state.db").exists()
    sid = "20261112_093000_probe2"
    lane.hooks["pre_llm_call"](session_id=sid, user_message="[selfheal-probe] create the cron job", platform="cli",
                               conversation_history=[])
    refuse(lane, SQLITE, session_id=sid)
    assert not lane.runner.creates() and not lane.runner.comments()
    (det,) = ev(lane.store, "selfheal_detect")
    assert det["probe"] is True
    assert [s["why"] for s in ev(lane.store, "selfheal_skip")] == ["probe"]


def test_fixed_lane_holds_two_occurrences_then_the_third_files(lane, monkeypatch):
    """A fixed shape inside the rollout window: occurrences from hosts still on pre-fix core are held (they only log
    `selfheal_recurrence_held`); the 3rd files a new generation."""
    sh = lane.sh
    fp = _fp(sh, "budget/paths")
    sh.LaneState(lane.store.root).update(fp, status="fixed", card="t_000000aa", fix_card="t_000000bb",
                                         fixed_at=time.time() - 60, filed_day="2026-11-11", gen=1, fix_shas=[])
    monkeypatch.setattr(sh, "deploy_grace_s", lambda cfg=None: 6 * 3600.0)
    for i in range(2):
        refuse(lane, BUDGET, session_id=f"20261112_09000{i}_held{i}", command="bash ./deploy.sh")
        time.sleep(0.01)  # held occurrences are deduped by detection time
    assert not lane.runner.creates() and not lane.runner.comments()
    assert len(ev(lane.store, "selfheal_recurrence_held")) == 2
    refuse(lane, BUDGET, session_id="20261112_090009_third", command="bash ./deploy.sh")
    (c,) = lane.runner.creates()
    assert c[c.index("--idempotency-key") + 1].endswith(":r2")


@pytest.mark.parametrize("status", ["declined", "reverted"])
def test_closed_without_fix_refiles_the_same_day(lane, status):
    sh = lane.sh
    fp = _fp(sh, "sqlite")
    sh.LaneState(lane.store.root).update(fp, status=status, card="t_000000aa", gen=1,
                                         filed_day=sh._utc_day(time.time()), closed_at=time.time() - 60)
    refuse(lane, SQLITE)
    (c,) = lane.runner.creates()
    assert c[c.index("--idempotency-key") + 1].endswith(":r2")


# ── core-backed: the real guard produces the refusals the detector reads (skip without core) ─────────────────────


@pytest.fixture
def guard(monkeypatch):
    g = pytest.importorskip("tools.terminal_tool_guards")
    pr = pytest.importorskip("tools.process_registry")
    monkeypatch.setattr(pr, "_is_supervised_gateway_process", lambda: True)
    lg = pytest.importorskip("cron.lifecycle_guard")

    def run(command, cwd):
        return g.gateway_lifecycle_block(command=command, env=SimpleNamespace(cwd=str(cwd)), env_type="local",
                                         cwd=str(cwd), workdir=None, session_key="coach-guard-refusal-test")
    return SimpleNamespace(run=run, lg=lg)


def test_core_oversized_and_budget_refusals_are_hits_of_two_shapes(guard, tmp_path, monkeypatch):
    sh = plugin._load("self_heal")
    big = tmp_path / "big.sh"
    big.write_text("# pad\n" * 300000 + "echo hi\n")  # > 1 MiB, no lifecycle text
    out = guard.run(f"sh {big}", tmp_path)
    hit = sh.guard_refusal_signal("terminal", {"command": f"sh {big}"}, out)
    assert hit and hit["shape"] == "oversized/file", out  # the file alone is over the 1 MiB cap
    small = tmp_path / "deploy.sh"
    small.write_text("echo deploy\n")
    monkeypatch.setattr(guard.lg, "_MAX_LIFECYCLE_SCAN_PATHS", 0)
    out2 = guard.run(f"sh {small}", tmp_path)
    hit2 = sh.guard_refusal_signal("terminal", {"command": f"sh {small}"}, out2)
    assert hit2 and hit2["shape"] == "budget/paths", out2
    assert hit["fingerprint"] != hit2["fingerprint"]


def test_core_true_lifecycle_refusal_opens_no_card(guard, lane, tmp_path):
    """A referenced script whose text runs a gateway restart: refused by the real guard, no card, no detection."""
    script = tmp_path / "bounce.sh"
    script.write_text("#!/bin/sh\nhermes gateway restart\n")
    out = guard.run(f"sh {script}", tmp_path)
    assert out and "cannot restart, stop, or uninstall" in out, out
    for _ in range(3):
        refuse(lane, out, command=f"sh {script}")
    assert not lane.runner.creates() and not lane.runner.comments()
    assert not ev(lane.store, "selfheal_detect")
