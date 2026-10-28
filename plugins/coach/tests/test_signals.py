"""self-heal machine-signal sweeper (signals.py): one test per signal source with a fixture, the cursor, the hub
sweep (file_defect per signal, shadow), the Mac forwarder (cursor only moves on success), hub ingest, and the
monitor re-route (lane owns it -> no Do-it card)."""
import importlib.util
import json
import shutil
import sqlite3
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIX = Path(__file__).resolve().parent / "fixtures" / "signals"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


sg = load("coach_signals_test", ROOT / "signals.py")
sh = load("coach_self_heal_signals_test", ROOT / "self_heal.py")
core = load("coach_core_test_signals", ROOT / "core.py")

NOW = 1792660000.0  # 2026-10-22 ~09:06 UTC
OPEN = lambda board=None, now=None: {"usd": 0.0, "cap": 25.0, "running": 0, "ok_usd": True, "ok_running": True,
                                     "why": ""}


def make_board(db: Path, runs):
    db.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(db)
    c.execute("create table tasks (id text primary key, title text, status text, idempotency_key text)")
    c.execute("create table task_runs (id integer primary key autoincrement, task_id text, status text, "
              "started_at integer, ended_at integer, outcome text, error text)")
    for tid, title, key in {(r[0], r[1], r[2]) for r in runs}:
        c.execute("insert into tasks values (?,?,?,?)", (tid, title, "ready", key))
    for tid, _title, _key, outcome, err in runs:
        c.execute("insert into task_runs (task_id, status, started_at, ended_at, outcome, error) values "
                  "(?,?,?,?,?,?)", (tid, outcome, int(NOW) - 600, int(NOW) - 300, outcome, err))
    c.commit()
    c.close()


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    (h / "logs").mkdir(parents=True)
    (h / "cron").mkdir()
    (h / "coach").mkdir()
    shutil.copy(FIX / "errors.txt", h / "logs" / "errors.log")
    shutil.copy(FIX / "gateway-exit-diag.txt", h / "logs" / "gateway-exit-diag.log")
    shutil.copy(FIX / "jobs.json", h / "cron" / "jobs.json")
    shutil.copy(FIX / "coach-log.jsonl", h / "coach" / "log.jsonl")
    make_board(h / "kanban.db", [
        ("t_crash2", "build the thing", None, "crashed", "worker died: SIGKILL"),
        ("t_crash2", "build the thing", None, "timed_out", "max_runtime exceeded"),
        ("t_once", "crashed once", None, "crashed", "worker died"),
        ("t_ok", "fine card", None, "completed", None),
        ("t_sh", "self-heal: x", "selfheal:agent-0123456789", "crashed", "a"),
        ("t_sh", "self-heal: x", "selfheal:agent-0123456789", "crashed", "b"),
    ])
    return h


def kinds(sigs, kind):
    return [s for s in sigs if s["kind"] == kind]


# ── one test per source ────────────────────────────────────────────────────────────────────────────────────

def test_errors_log_tracebacks(home):
    sigs = kinds(sg.collect(home, {}, "hubhost", NOW), "errors")
    syms = [s["symptom"] for s in sigs]
    # 429 (provider transient) and the tool-output traceback are not defects; the two malformed-db tracebacks
    # with different line numbers are ONE fingerprint (frames by file:function, no line numbers)
    assert not any("RateLimit" in x for x in syms)
    assert not any("tool_executor" in s["details"] for s in sigs)
    db = [s for s in sigs if "DatabaseError" in s["symptom"]]
    assert len(db) == 1 and db[0]["count"] == 2
    assert db[0]["symptom"].startswith("DatabaseError in reconcile_state_schema (hermes_state_schema.py)")
    plug = [s for s in sigs if s["component"] == "plugin"]
    assert len(plug) == 1 and "[example-plugin]" in plug[0]["symptom"] and plug[0]["fp"].startswith("plugin-")
    assert "/home/you" not in db[0]["details"] and "~/.hermes/hermes-agent" in db[0]["details"]


def test_library_warning_traceback_is_not_a_defect_but_ours_is():
    def rec(level, logger, path):
        return [f"2026-10-22 08:00:00,000 {level} {logger}: Failed to clean up child process",
                "Traceback (most recent call last):",
                f'  File "{path}", line 10, in terminate_tree',
                "    os.killpg(pgid, sig)",
                "PermissionError: [Errno 1] Operation not permitted"]
    lib = "/home/you/.hermes/hermes-agent/venv/lib/python3.11/site-packages/mcp/os/posix/utilities.py"
    own = "/home/you/.hermes/hermes-agent/tools/mcp_tool.py"
    assert sg.parse_errors(rec("WARNING", "mcp.os.posix.utilities", lib)) == []
    assert len(sg.parse_errors(rec("WARNING", "tools.mcp_tool", own))) == 1
    assert len(sg.parse_errors(rec("ERROR", "mcp.os.posix.utilities", lib))) == 1


def test_process_exit_scheduling_traceback_is_not_a_defect():
    """agent-9a2a3cc05d: a turn finishing while the backend exits logs CPython's shutdown refusal from our own
    frames (pre_final_gate). That is exit noise, not a defect; any other RuntimeError there still files."""
    own = "/home/you/.hermes/hermes-agent/agent/pre_final_gate.py"
    pool = "/home/you/.hermes/tools/python-3.14.7/lib/python3.14/concurrent/futures/thread.py"

    def rec(exc_line):
        return ["2026-10-27 15:55:01,000 WARNING [20261027_152740_fa067b] agent.conversation_loop: "
                "pre_final_gate hook pre_final_gate raised",
                "Traceback (most recent call last):",
                f'  File "{own}", line 235, in _invoke',
                "    return _call(cb, call_kwargs, limit)",
                f'  File "{own}", line 220, in _call',
                "    return pool.submit(copy_context().run, cb, **kwargs).result(timeout=limit)",
                f'  File "{pool}", line 207, in submit',
                "    raise RuntimeError('cannot schedule new futures after '",
                exc_line]
    assert sg.parse_errors(rec("RuntimeError: cannot schedule new futures after interpreter shutdown")) == []
    assert sg.parse_errors(rec("RuntimeError: can't register atexit after shutdown")) == []
    assert len(sg.parse_errors(rec("RuntimeError: dictionary changed size during iteration"))) == 1


LIB = "/home/you/.hermes/installs/x/venv/lib/python3.14/site-packages"
RECONNECT_TAILS = {
    "close-1000": [f'  File "{LIB}/discord/client.py", line 731, in connect',
                   "    await self.ws.poll_event()",
                   f'  File "{LIB}/discord/gateway.py", line 668, in poll_event',
                   "    raise ConnectionClosed(self.socket, shard_id=self.shard_id, code=code) from None",
                   "discord.errors.ConnectionClosed: Shard ID None WebSocket closed with 1000"],
    "reset": [f'  File "{LIB}/discord/gateway.py", line 677, in send',
              "    await self.socket.send_str(data)",
              f'  File "{LIB}/aiohttp/_websocket/writer.py", line 150, in _write_websocket_frame',
              '    raise ClientConnectionResetError("Cannot write to closing transport")',
              "aiohttp.client_exceptions.ClientConnectionResetError: Cannot write to closing transport"],
    "dns": [f'  File "{LIB}/discord/client.py", line 728, in connect',
            "    self.ws = await asyncio.wait_for(coro, timeout=60.0)",
            '  File "/home/you/Applications/Hermes Python.app/Contents/lib/python3.14/asyncio/tasks.py", line 488, '
            "in wait_for",
            "    return await fut",
            f'  File "{LIB}/aiohttp/connector.py", line 1577, in _create_direct_connection',
            "    raise ClientConnectorDNSError(req.connection_key, exc) from exc",
            "aiohttp.client_exceptions.ClientConnectorDNSError: Cannot connect to host gateway-us-east1-c.discord.gg:443 "
            "ssl:default [nodename nor servname provided, or not known]"],
    "handshake": [f'  File "{LIB}/aiohttp/client.py", line 1266, in _ws_connect',
                  "    raise WSServerHandshakeError(",
                  "aiohttp.client_exceptions.WSServerHandshakeError: 520, message='Invalid response status', "
                  "url='wss://gateway.discord.gg/?v=10&encoding=json&compress=zstd-stream'"],
}


def reconnect_rec(tail, logger="discord.client", msg="Attempting a reconnect in 2.36s", at="2026-10-22 08:00:00"):
    return [f"{at},000 ERROR {logger}: {msg}", "Traceback (most recent call last):"] + tail


@pytest.mark.parametrize("variant", sorted(RECONNECT_TAILS))
def test_library_auto_reconnect_is_not_a_defect(variant):
    # discord.py logs every self-healing reconnect at ERROR with a traceback, then reconnects (client.py:769-781)
    assert sg.parse_errors(reconnect_rec(RECONNECT_TAILS[variant])) == []


def test_reconnect_message_with_a_hermes_frame_or_hermes_failed_to_connect_still_files():
    own = ['  File "/home/you/.hermes/hermes-agent/gateway/platforms/discord.py", line 40, in start',
           "    await self.client.start(token)"] + RECONNECT_TAILS["close-1000"]
    assert len(sg.parse_errors(reconnect_rec(own))) == 1
    ours = reconnect_rec(RECONNECT_TAILS["dns"], logger="hermes_plugins.platforms__discord.adapter",
                         msg="[Discord] Failed to connect to Discord: Cannot connect to host discord.com:443")
    assert len(sg.parse_errors(ours)) == 1
    # a Hermes logger saying "Attempting a reconnect" is ours to look at, frames or not
    assert len(sg.parse_errors(reconnect_rec(RECONNECT_TAILS["reset"], logger="gateway.run"))) == 1


def _reconnect_log(home, n, start):
    lines = []
    for i in range(n):
        t = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(start + i * 60))
        lines += reconnect_rec(RECONNECT_TAILS["close-1000"], at=t)
    with (home / "logs" / "errors.log").open("a") as f:
        f.write("\n".join(lines) + "\n")


def test_reconnect_storm_files_one_defect_per_host_and_profile(tmp_path):
    h = tmp_path / "home"
    (h / "logs").mkdir(parents=True)
    cur = {}
    _reconnect_log(h, 20, NOW - 1500)  # 20 in the hour: the library is coping, nothing to file
    assert kinds(sg.collect(h, cur, "macbook", NOW), "errors") == []
    _reconnect_log(h, 1, NOW - 200)  # the 21st within an hour, on a later pass: one storm defect
    storm = kinds(sg.collect(h, cur, "macbook", NOW), "errors")
    assert len(storm) == 1
    assert storm[0]["symptom"].startswith("discord.client keeps reconnecting on macbook (default): 21 reconnects")
    assert storm[0]["component"] == "gateway" and storm[0]["count"] == 1
    _reconnect_log(h, 3, NOW - 100)  # same storm, same hour: not filed again
    assert kinds(sg.collect(h, cur, "macbook", NOW + 60), "errors") == []
    other = tmp_path / "other"
    (other / "logs").mkdir(parents=True)
    _reconnect_log(other, 21, NOW - 1500)
    assert kinds(sg.collect(other, {}, "mini", NOW), "errors")[0]["fp"] != storm[0]["fp"]  # per host


def test_plugin_hook_exception(home):
    hooks = kinds(sg.collect(home, {}, "hubhost", NOW), "hook")
    assert len(hooks) == 1
    h = hooks[0]
    assert h["component"] == "plugin" and "pre_llm_call" in h["symptom"] and "on_pre" in h["symptom"]
    assert h["session_id"] == "20261022_080400_bbbbbb"


def test_failed_cron_runs(home):
    crons = kinds(sg.collect(home, {}, "hubhost", NOW), "cron")
    names = sorted(s["symptom"].split("'")[1] for s in crons)
    # a job still failing at core's nudge threshold (streak 3) is filed; a one-off the next run recovered from
    # ("recovered job", streak 0) is only counted; a month-old one is not seen at all
    assert names == ["nightly export"]
    assert all(s["fp"].startswith("cron-") for s in crons)


def test_gateway_shutdown_cron_kill_is_one_off_unless_repeated(home):
    gw = "Gateway shutdown (post-interrupt) killed the job's tool subprocess before the run finished."

    def job(i, streak, detail):
        return {"id": i, "name": f"job {i}", "last_status": "error", "failure_streak": streak,
                "last_failure": {"at": "2026-10-22T08:10:00+00:00", "detail": detail}}
    (home / "cron" / "jobs.json").write_text(json.dumps({"jobs": [
        job("g1", 1, gw), job("g3", 3, gw), job("o3", 3, "Script exited with code 1")]}))
    cur = {}
    names = sorted(s["symptom"].split("'")[1] for s in sg.collect_cron(home, cur, NOW, "macbook"))
    assert names == ["job g3", "job o3"]
    assert "default:g1" not in cur["cron_fails"]  # a gateway kill never counts toward the 24h burst
    # the suppressed run still advanced the seen cursor: no re-emit on the next pass
    assert cur["cron"]["default:g1"] > 0
    assert sg.collect_cron(home, cur, NOW + 60, "macbook") == []


def test_kanban_cards_failed_twice(home):
    ks = kinds(sg.collect(home, {}, "hubhost", NOW), "kanban")
    assert [s["symptom"].split()[2] for s in ks] == ["t_crash2"]  # once is not enough; self-heal's own cards skip
    assert "crashed" in ks[0]["details"] and "timed_out" in ks[0]["details"]


def test_gateway_crash_restarts(home):
    gw = kinds(sg.collect(home, {}, "hubhost", NOW), "gateway")
    tags = sorted(s["symptom"].split(": ")[1].split()[0] for s in gw)
    # a clean exit and a code-75 restart request are not crashes
    assert tags == ["asyncio.run.exception", "gateway.previous_unclean_exit"]
    assert any("suspected OOM" in s["symptom"] for s in gw)


def test_coach_fixloop_and_handoff_events(home):
    cs = kinds(sg.collect(home, {}, "hubhost", NOW), "coach")
    assert len(cs) == 2  # fixloop_card + the handoff nudge; a confirm nudge is not a handoff
    assert any("claimed fix failed 2x" in s["symptom"] for s in cs)
    assert any("handed the user a step" in s["symptom"] for s in cs)


# ── cursor ───────────────────────────────────────────────────────────────────────────────────────────────────

def test_cursor_sees_each_signal_once_and_reads_appends(home):
    cur = {}
    first = sg.collect(home, cur, "hubhost", NOW)
    assert first
    assert sg.collect(home, cur, "hubhost", NOW) == []
    with (home / "logs" / "errors.log").open("a") as f:
        f.write("2026-10-22 09:00:00,000 ERROR cron.scheduler: tick failed\nTraceback (most recent call last):\n"
                '  File "/home/you/.hermes/hermes-agent/cron/scheduler.py", line 9, in tick\n    x()\n'
                "ZeroDivisionError: division by zero\n")
    again = sg.collect(home, cur, "hubhost", NOW)
    assert [s["component"] for s in again] == ["cron"] and "ZeroDivisionError" in again[0]["symptom"]


def test_cursor_rereads_a_rotated_log(home):
    cur = {}
    sg.collect(home, cur, "hubhost", NOW)
    p = home / "logs" / "errors.log"
    text = p.read_text()
    tmp = p.with_name("errors.log.new")
    tmp.write_text(text)  # written while the old file still exists: a new inode
    tmp.replace(p)
    assert kinds(sg.collect(home, cur, "hubhost", NOW), "errors")


# ── hub sweep / forwarder / ingest ───────────────────────────────────────────────────────────────────────────

def test_sweep_files_each_signal_in_shadow_with_stable_fingerprints(home):
    store = core.Store(home / "coach")
    got = []

    def filer(d):
        got.append(d)
        return sh.file_defect(d, store=store, m="shadow", cfg={}, now=NOW, budget_fn=OPEN)
    res = sg.sweep(home, "hubhost", filer=filer, now=NOW)
    assert {r["signal"]["kind"] for r in res} == {"errors", "hook", "cron", "kanban", "gateway", "coach"}
    assert all(r["result"]["action"] == "would_file" for r in res)
    assert all(d["source"] == "signal" and sh.FP_RE.match(d["fingerprint"]) for d in got)
    logged = [json.loads(x) for x in (home / "coach" / "log.jsonl").read_text().splitlines()]
    wf = [r for r in logged if r.get("event") == "selfheal_would_file" and r.get("source") == "signal"]
    assert len(wf) == len(res) and all(r["args"]["idempotency_key"].startswith("selfheal:") for r in wf)
    # second pass: nothing new
    assert sg.sweep(home, "hubhost", filer=filer, now=NOW) == []


def test_same_traceback_on_two_hosts_is_one_fingerprint(home, tmp_path):
    a = kinds(sg.collect(home, {}, "macbook", NOW), "errors")
    b = kinds(sg.collect(home, {}, "mini", NOW), "errors")
    assert {s["fp"] for s in a} == {s["fp"] for s in b}
    ca, cb = kinds(sg.collect(home, {}, "macbook", NOW), "cron"), kinds(sg.collect(home, {}, "mini", NOW), "cron")
    assert {s["fp"] for s in ca}.isdisjoint({s["fp"] for s in cb})  # per-host objects stay per host


def test_forward_moves_cursor_only_on_success(home):
    calls = []

    def bad(argv, stdin):
        calls.append(argv)
        return SimpleNamespace(returncode=255, stdout="", stderr="ssh: connect to host: timed out")
    r = sg.forward(home, "macbook", "you@hub.example.com", runner=bad, now=NOW)
    assert not r["ok"] and r["pending"] > 0 and not (home / "coach" / "signals-cursor.json").exists()
    sent = []

    def good(argv, stdin):
        sent.append(stdin)
        return SimpleNamespace(returncode=0, stdout='{"ingested": 9}', stderr="")
    r = sg.forward(home, "macbook", "you@hub.example.com", runner=good, now=NOW)
    assert r["ok"] and r["sent"] == r_count(sent[0]) - 1  # + the heartbeat line
    assert json.loads(sent[0].splitlines()[0])["kind"] == "heartbeat"
    assert calls[0][:3] == ["ssh", "-o", "BatchMode=yes"] and calls[0][-1] == sg.HUB_CMD
    assert sg.forward(home, "macbook", "you@hub.example.com", runner=good, now=NOW)["sent"] == 0
    assert r_count(sent[-1]) == 1  # nothing new: the heartbeat still goes, so the hub sees the forwarder alive


def test_forward_pins_the_hub_key(home, tmp_path, monkeypatch):
    # The mini's launchd forward was refused preauth while its shell ssh to the hub worked: pin -i + IdentitiesOnly.
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".ssh").mkdir(exist_ok=True)
    (tmp_path / ".ssh" / "id_ed25519").write_text("x")
    calls = []

    def good(argv, stdin):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout='{"ingested": 1}', stderr="")
    sg.forward(home, "mini", "you@hub.example.com", runner=good, now=NOW, cfg={})
    argv = calls[0]
    assert argv[argv.index("-i") + 1] == str(tmp_path / ".ssh" / "id_ed25519")
    assert "IdentitiesOnly=yes" in argv and argv[-2:] == ["you@hub.example.com", sg.HUB_CMD]
    sg.forward(home, "mini", "you@hub.example.com", runner=good, now=NOW, cfg={"hub_ssh_key": "/k/hub"})
    assert calls[1][calls[1].index("-i") + 1] == "/k/hub"


def test_hub_flags_a_silent_forwarder(tmp_path):
    hub = tmp_path / "hub"
    beat = json.dumps({"kind": "heartbeat", "host": "macbook", "n": 0})
    assert sg.ingest(hub, beat + "\n", now=NOW) == 0  # heartbeats are not spooled
    assert sg.silent_forwarders(hub, now=NOW + 3600) == []
    late = sg.silent_forwarders(hub, now=NOW + 7 * 3600)
    assert [s["host"] for s in late] == ["macbook"] and "has not reported for 7 h" in late[0]["symptom"]
    got = []
    sg.sweep(hub, "hermes-hub", filer=lambda d: got.append(d) or {"action": "would_file"}, now=NOW + 7 * 3600)
    assert [d["host"] for d in got] == ["macbook"] and got[0]["component"] == "plugin"


def r_count(payload):
    return len([x for x in payload.splitlines() if x.strip()])


def test_ingest_then_hub_sweep_files_forwarded_signals(home, tmp_path):
    mac = home
    hub = tmp_path / "hub"
    (hub / "coach").mkdir(parents=True)
    payload = "".join(json.dumps(s) + "\n" for s in sg.collect(mac, {}, "macbook", NOW))
    n = sg.ingest(hub, payload + "not json\n" + json.dumps({"kind": "evil"}) + "\n", now=NOW)
    assert n == r_count(payload)
    got = []
    res = sg.sweep(hub, "hermes-hub", filer=lambda d: got.append(d) or {"action": "would_file"}, now=NOW)
    assert len(res) == n and {d["host"] for d in got} == {"macbook"}
    assert sg.sweep(hub, "hermes-hub", filer=lambda d: {"action": "would_file"}, now=NOW) == []  # spool drained


def test_drain_spool_leaves_a_half_written_last_line_for_the_next_sweep(tmp_path):
    hub = tmp_path / "hub"
    spool = hub / "coach" / "signals" / "spool.jsonl"
    spool.parent.mkdir(parents=True)
    a = {"kind": "cron", "fp": "cron-aaaaaaaaaa", "host": "macbook"}
    b = {"kind": "errors", "fp": "agent-bbbbbbbbbb", "host": "macbook"}
    whole_b = json.dumps(b) + "\n"
    spool.write_text(json.dumps(a) + "\n" + whole_b[:15])  # `ingest` is mid-write of b
    assert [s["fp"] for s in sg.drain_spool(hub)] == ["cron-aaaaaaaaaa"]
    with spool.open("a") as f:  # the write finishes
        f.write(whole_b[15:])
    assert [s["fp"] for s in sg.drain_spool(hub)] == ["agent-bbbbbbbbbb"]
    assert sg.drain_spool(hub) == []


# ── monitors re-routed to the lane ───────────────────────────────────────────────────────────────────────────

MON = {"monitor": "mac-worktree-gc", "key": "worktree-gc-abort:macbook",
       "title": "macbook: worktree GC aborted, cannot read what is live",
       "body": "`mac-worktree-gc.py` (GC) removed nothing and reported nothing: lsof timed out after 600s",
       "prompt": "Find why mac-worktree-gc.py can't read live state, fix the cause."}


def test_monitor_finding_becomes_a_defect_with_its_hint():
    d = sg.monitor_defect(MON, host="macbook")
    assert d["source"] == "signal" and d["kind"] == "monitor" and d["host"] == "macbook"
    assert d["fingerprint"] == sg.monitor_defect(dict(MON, body="other text"), host="mini")["fingerprint"]
    assert "Monitor's own suggested next step" in d["details"] and d["symptom"] == MON["title"]


@pytest.mark.parametrize("res,owned", [
    ({"action": "filed", "card": "t_1"}, True),
    ({"action": "skip", "why": "open: filed t_1"}, True),
    ({"action": "skip", "why": "COACH_SELFHEAL=off"}, False),
    ({"action": "would_file"}, False),
    ({"action": "error", "why": "boom"}, False),
    ({"action": "forwarded", "hub_result": {"action": "filed", "card": "t_2"}}, True),
    ({"action": "forwarded", "hub_result": {"action": "would_file"}}, False),
    ({"action": "forwarded"}, False),
])
def test_lane_owns(res, owned):
    assert sg.lane_owns(res) is owned


def test_route_monitor_live_files_once_then_dedupes(tmp_path):
    store = core.Store(tmp_path / "coach")
    created = []

    def runner(argv, capture_output=True, text=True, input=None, timeout=None):
        if "create" in argv:
            created.append(argv)
            return SimpleNamespace(returncode=0, stdout=json.dumps({"id": f"t_{len(created):08x}"}), stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def filer(d):
        return sh.file_defect(d, store=store, m="live", cfg={}, runner=runner, now=NOW, budget_fn=OPEN)
    a = sg.route_monitor(MON, filer=filer)
    b = sg.route_monitor(MON, filer=filer)
    assert a["owned"] and a["result"]["action"] == "filed" and len(created) == 1
    assert b["owned"] and b["result"]["action"] == "skip"  # same finding tomorrow: still the lane's, no new card


def test_route_monitor_shadow_is_not_owned(tmp_path):
    store = core.Store(tmp_path / "coach")
    r = sg.route_monitor(MON, filer=lambda d: sh.file_defect(d, store=store, m="shadow", cfg={}, now=NOW,
                                                             budget_fn=OPEN))
    assert not r["owned"] and r["result"]["action"] == "would_file"  # shadow: the monitor still posts its card


def test_forward_parses_hub_result():
    out = json.dumps({"action": "filed", "card": "t_9"})
    runner = lambda argv, capture_output=True, text=True, input=None, timeout=None: SimpleNamespace(
        returncode=0, stdout="warning: noise\n" + out + "\n", stderr="")
    store = SimpleNamespace(root=Path("/nonexistent"), log=lambda rec: None)
    r = sh.file_defect(sg.monitor_defect(MON), store=store, m="live", cfg={"hub_ssh": "you@hub.example.com",
                                                                            "hub_host": "elsewhere"},
                       runner=runner, now=NOW)
    assert r["action"] == "forwarded" and r["hub_result"]["card"] == "t_9" and sg.lane_owns(r)


def _cron_job(i, streak, status, at, detail="Script exited with code 1"):
    return {"id": i, "name": f"job {i}", "last_status": status, "failure_streak": streak,
            "last_failure": {"at": at, "detail": detail}}


def _write_jobs(home, *jobs):
    (home / "cron" / "jobs.json").write_text(json.dumps({"jobs": list(jobs)}))


def _iso(ts):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def test_recovered_one_off_cron_failure_is_not_filed(home):
    # bsky-notify 10/27: failed once, next run ok (streak 0) -> counted, never a self-heal card
    _write_jobs(home, _cron_job("r1", 0, "ok", _iso(NOW - 1200)),
                _cron_job("f1", 1, "error", _iso(NOW - 300)))
    cur = {}
    assert sg.collect_cron(home, cur, NOW, "hubhost") == []
    assert cur["cron"]["default:r1"] > 0  # cursor still advanced: no later re-emit
    assert len(cur["cron_fails"]["default:r1"]) == 1


def test_cron_failure_streak_at_threshold_is_filed(home):
    _write_jobs(home, _cron_job("s3", 3, "error", _iso(NOW - 300)))
    names = [s["symptom"].split("'")[1] for s in sg.collect_cron(home, {}, NOW, "hubhost")]
    assert names == ["job s3"]


def test_cron_threshold_follows_failure_nudge_threshold(home):
    (home / "config.yaml").write_text("model: x\ncron:\n  failure_nudge_threshold: 2\n")
    _write_jobs(home, _cron_job("s2", 2, "error", _iso(NOW - 300)))
    assert len(sg.collect_cron(home, {}, NOW, "hubhost")) == 1


def test_three_recovered_cron_failures_in_24h_are_filed(home):
    cur = {}
    for n, t in enumerate((NOW - 7200, NOW - 3600, NOW - 600)):
        _write_jobs(home, _cron_job("flaky", 0, "ok", _iso(t)))
        got = sg.collect_cron(home, cur, t + 300, "hubhost")
        assert len(got) == (1 if n == 2 else 0), (n, got)
    assert "3 failures in 24h" in got[0]["details"]


def test_recovered_cron_failures_older_than_24h_do_not_accumulate(home):
    cur = {}
    for t in (NOW - 3 * 86400, NOW - 2 * 86400, NOW - 600):
        _write_jobs(home, _cron_job("slow", 0, "ok", _iso(t)))
        assert sg.collect_cron(home, cur, t + 300, "hubhost") == []


def _gw_rows(home, *offsets, tag="gateway.exit_signal"):
    """Append exit-diag rows `offsets` seconds before NOW (UTC ISO ts, as core writes them)."""
    from datetime import datetime, timezone
    with open(home / "logs" / "gateway-exit-diag.log", "a") as f:
        for off in offsets:
            row = {"ts": datetime.fromtimestamp(NOW - off, timezone.utc).isoformat(), "tag": tag,
                   "level": "info", "pid": 200 + off}
            if tag == "gateway.exit_signal":
                row.update(signal="SIGTERM", ppid=1, parent={"pid": 1})
            f.write(json.dumps(row) + "\n")


def test_single_exit_signal_is_a_bounce_not_a_defect(home):
    """A planned launchd bounce (kickstart -k / bootout) logs one gateway.exit_signal: nothing filed."""
    (home / "logs" / "gateway-exit-diag.log").write_text("")
    _gw_rows(home, 120)
    assert kinds(sg.collect(home, {}, "mini", NOW), "gateway") == []


def test_exit_signal_storm_files_one_card(home):
    (home / "logs" / "gateway-exit-diag.log").write_text("")
    cur = {}
    _gw_rows(home, 500, 300)
    assert kinds(sg.collect(home, cur, "mini", NOW - 100), "gateway") == []  # two: still a bounce
    _gw_rows(home, 60)
    gw = kinds(sg.collect(home, cur, "mini", NOW), "gateway")  # the third within 10 min, across sweeps
    assert len(gw) == 1
    assert "restart storm: 3 stop signals" in gw[0]["symptom"] and "SIGTERM" in gw[0]["symptom"]
    _gw_rows(home, 30)
    assert kinds(sg.collect(home, cur, "mini", NOW), "gateway") == []  # filed once; the counter reset


def test_exit_signals_spread_out_never_file(home):
    (home / "logs" / "gateway-exit-diag.log").write_text("")
    _gw_rows(home, 3000, 1500, 60)  # three, but never three within 10 min
    assert kinds(sg.collect(home, {}, "mini", NOW), "gateway") == []


def test_bare_exit_nonzero_still_files(home):
    (home / "logs" / "gateway-exit-diag.log").write_text("")
    _gw_rows(home, 60, tag="gateway.exit_nonzero")
    gw = kinds(sg.collect(home, {}, "mini", NOW), "gateway")
    assert len(gw) == 1 and "gateway.exit_nonzero" in gw[0]["symptom"]
