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


@pytest.mark.parametrize("traceback", [False, True])
def test_offline_cron_summary_is_not_a_defect(traceback):
    message = "RuntimeError: Hermes can't reach the model provider. You may be offline."
    lines = ["2026-10-22 08:00:00,000 ERROR cron.scheduler: "
             f"Job 'example-monitor' failed: {message}"]
    if traceback:
        lines += ["Traceback (most recent call last):",
                  '  File "/home/you/.hermes/hermes-agent/cron/scheduler.py", line 10, in run_job',
                  message]
    assert sg.parse_errors(lines) == []


@pytest.mark.parametrize("traceback", [False, True])
def test_provider_dns_misconfiguration_is_a_defect(traceback):
    message = ("provider host does not resolve while network is up: api.example.com "
               "(control host github.com resolves) — check the provider base_url")
    lines = [f"2026-10-22 08:00:00,000 ERROR run_agent: {message}"]
    if traceback:
        lines += ["Traceback (most recent call last):",
                  '  File "/home/you/.hermes/hermes-agent/run_agent.py", line 10, in run',
                  f"RuntimeError: {message}"]
    signals = sg.parse_errors(lines)
    assert len(signals) == 1
    assert signals[0]["component"] == "agent"
    assert message in signals[0]["details"]


@pytest.mark.parametrize("message", [
    "RuntimeError: Hermes can't reach the model provider.",
    "RuntimeError: provider host does not resolve while network is up: api.example.com "
    "(control host github.com resolves) — check the provider base_url",
    "RuntimeError: unexpected failure; Hermes can't reach the model provider. You may be offline.",
    "ValueError: Hermes can't reach the model provider. You may be offline.",
])
def test_offline_suppression_does_not_hide_other_errors(message):
    lines = ["2026-10-22 08:00:00,000 ERROR cron.scheduler: Job 'example-monitor' failed",
             "Traceback (most recent call last):",
             '  File "/home/you/.hermes/hermes-agent/cron/scheduler.py", line 10, in run_job',
             message]
    assert len(sg.parse_errors(lines)) == 1


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


def test_api_worker_stream_error_carries_its_session_id():
    """agent-1572c64e24: the API worker thread's "Streaming failed before delivery" traceback filed with an
    empty session id, so the self-heal card could not link the session. Core now tags the worker thread's log
    lines with the caller's session (agent/chat_completion_helpers._context_thread_target); the signal row
    must carry that id."""
    lib = "/home/you/.hermes/installs/x/venv/lib/python3.14/site-packages/openai/_base_client.py"
    own = "/home/you/.hermes/hermes-agent/agent/chat_completion_helpers.py"
    lines = ["2026-10-27 22:00:17,031 ERROR [20261027_220000_5326d3] agent.chat_completion_helpers: "
             "Streaming failed before delivery: Error code: 400",
             "Traceback (most recent call last):",
             f'  File "{own}", line 3640, in _call',
             "    stream = request_client.chat.completions.create(**stream_kwargs)",
             f'  File "{lib}", line 1070, in request',
             "    raise self._make_status_error_from_response(err.response) from None",
             "openai.BadRequestError: Error code: 400 - {'error': {'message': 'claude-opus-5-5 is not a valid "
             "model ID', 'code': 400}}"]
    rows = sg.parse_errors(lines)
    assert [r["session_id"] for r in rows] == ["20261027_220000_5326d3"]


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
    # a Hermes "Failed to connect" whose final exception is not a network error is ours to look at
    ours = reconnect_rec(ADAPTER_FRAME + ["discord.errors.LoginFailure: Improper token has been passed."],
                         logger="hermes_plugins.platforms__discord.adapter",
                         msg="[Discord] Failed to connect to Discord: Improper token has been passed.")
    assert len(sg.parse_errors(ours)) == 1
    # the DNS case is no longer filed per attempt (plugin-0561bb7919): see the NETWORK_TAILS tests below
    dns = reconnect_rec(RECONNECT_TAILS["dns"], logger="hermes_plugins.platforms__discord.adapter",
                        msg="[Discord] Failed to connect to Discord: Cannot connect to host discord.com:443")
    assert sg.parse_errors(dns) == []
    # a Hermes logger saying "Attempting a reconnect" is ours to look at, frames or not
    assert len(sg.parse_errors(reconnect_rec(RECONNECT_TAILS["reset"], logger="gateway.run"))) == 1


ADAPTER = "/home/you/.hermes/hermes-agent/plugins/platforms/discord/adapter.py"
ADAPTER_FRAME = [f'  File "{ADAPTER}", line 1660, in connect', "    await self._client.start(self.config.token)"]
NETWORK_TAILS = {
    # the real MacBook record (plugin-0561bb7919): the laptop had no network after sleep/wake
    "dns": RECONNECT_TAILS["dns"][-3:-1] + [
        "aiohttp.client_exceptions.ClientConnectorDNSError: Cannot connect to host discord.com:443 ssl:default "
        "[nodename nor servname provided, or not known]"],
    "dns-linux": ["socket.gaierror: [Errno -3] Temporary failure in name resolution"],
    "dns-unknown": ["socket.gaierror: [Errno -2] Name or service not known"],
    "refused": ["aiohttp.client_exceptions.ClientConnectorError: Cannot connect to host discord.com:443 "
                "ssl:default [Connection refused]"],
    "unreachable": ["OSError: [Errno 51] Network is unreachable"],
    "reset": ["ConnectionResetError: [Errno 54] Connection reset by peer"],
}


def connect_fail_rec(tail, msg="[Discord] Failed to connect to Discord: Cannot connect to host discord.com:443"):
    return reconnect_rec(ADAPTER_FRAME + tail, logger="hermes_plugins.platforms__discord.adapter", msg=msg)


@pytest.mark.parametrize("variant", sorted(NETWORK_TAILS))
def test_platform_connect_failure_with_no_network_is_not_a_defect(variant):
    # the adapter marks it retryable and the gateway retries with backoff; one failed attempt is not an outage
    # (the gateway's NEEDS_ATTENTION warning after reconnect_attention_after is the outage signal)
    assert sg.parse_errors(connect_fail_rec(NETWORK_TAILS[variant])) == []


def test_network_error_outside_a_platform_connect_still_files():
    # the same DNS error from another Hermes code path (not the retried adapter connect) is still a signal
    rec = connect_fail_rec(NETWORK_TAILS["dns"], msg="[Discord] Failed to send message: Cannot connect to host")
    assert len(sg.parse_errors(rec)) == 1
    # a certificate failure while connecting is not "no network": it files
    cert = ["aiohttp.client_exceptions.ClientConnectorCertificateError: Cannot connect to host discord.com:443 "
            "ssl:True [SSLCertVerificationError: certificate has expired]"]
    assert len(sg.parse_errors(connect_fail_rec(cert))) == 1


ATTENTION = ("{at},000 WARNING gateway.run_adapters: {key} has been failing/reconnecting continuously for 2.0 hours "
             "(41 attempts) — flagging NEEDS_ATTENTION. Retries continue, but this usually means a permanent "
             "problem (revoked credentials, missing intents, broken sidecar). Check `hermes status` / "
             "`/platform list`.")


def test_needs_attention_warning_files_one_defect_per_host_profile_platform(tmp_path):
    h = tmp_path / "home"
    (h / "logs").mkdir(parents=True)
    at = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(NOW - 300))
    (h / "logs" / "errors.log").write_text("\n".join([ATTENTION.format(at=at, key="discord")] * 2
                                                     + [ATTENTION.format(at=at, key="telegram")]) + "\n")
    sigs = kinds(sg.collect(h, {}, "macbook", NOW), "errors")
    disc = [s for s in sigs if "discord" in s["symptom"]]
    assert len(disc) == 1 and disc[0]["count"] == 2  # same platform twice: one defect
    assert disc[0]["component"] == "gateway" and "NEEDS_ATTENTION" in disc[0]["symptom"]
    assert "macbook" in disc[0]["symptom"] and "(default)" in disc[0]["symptom"]
    tele = [s for s in sigs if "telegram" in s["symptom"]]
    assert len(tele) == 1 and tele[0]["fp"] != disc[0]["fp"]  # per platform
    other = tmp_path / "other"
    (other / "logs").mkdir(parents=True)
    (other / "logs" / "errors.log").write_text(ATTENTION.format(at=at, key="discord") + "\n")
    assert kinds(sg.collect(other, {}, "mini", NOW), "errors")[0]["fp"] != disc[0]["fp"]  # per host


ADMISSION_BUSY = ("{at},000 WARNING hermes_cli.backend_retirement: {what} unavailable: update admission lock busy "
                  "for 0.5s (no update hold)")
ADMISSION_BUSY_OLD = ["{at},000 WARNING hermes_cli.backend_retirement: Update admission unavailable",
                      "Traceback (most recent call last):",
                      '  File "/x/hermes_cli/backend_retirement.py", line 153, in reservation',
                      "    pending = reserve(root)",
                      '  File "/x/hermes_cli/admission_fence.py", line 37, in admission_lock',
                      '    raise TimeoutError("Hermes update admission lock is busy") from None',
                      "TimeoutError: Hermes update admission lock is busy"]


def test_admission_busy_final_refusal_files_one_defect_without_traceback(tmp_path):
    """agent-6f2f71c662: core now rides out a briefly busy update lock and logs one traceback-less WARNING only when
    the lock stayed busy past its retry. That line is the detector: one defect per host/profile/source."""
    h = tmp_path / "home"
    (h / "logs").mkdir(parents=True)
    at = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(NOW - 300))
    (h / "logs" / "errors.log").write_text("\n".join(
        [ADMISSION_BUSY.format(at=at, what="Update admission")] * 2
        + [ADMISSION_BUSY.format(at=at, what="Review update admission")]) + "\n")
    sigs = kinds(sg.collect(h, {}, "macbook", NOW), "errors")
    turn = [s for s in sigs if s["symptom"].startswith("Update admission refused")]
    assert len(turn) == 1 and turn[0]["count"] == 2
    assert turn[0]["component"] == "agent" and "busy for 0.5s" in turn[0]["symptom"]
    assert "macbook" in turn[0]["symptom"] and "(default)" in turn[0]["symptom"]
    review = [s for s in sigs if s["symptom"].startswith("Review update admission refused")]
    assert len(review) == 1 and review[0]["fp"] != turn[0]["fp"]
    # a line that merely quotes the phrase (not at message start) is not a refusal
    quoted = f"{at},000 WARNING tools.terminal: output: Update admission unavailable: update admission lock busy " \
             "for 0.5s (no update hold)"
    assert sg.parse_errors([quoted]) == []
    # an older core's traceback form still files through the generic traceback path
    old = sg.parse_errors([l.format(at=at) for l in ADMISSION_BUSY_OLD])
    assert len(old) == 1 and old[0]["symptom"].startswith("TimeoutError in admission_lock")


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


def _refused(home, off, alive=True, reason="already_running", owner=4242):
    from datetime import datetime, timezone
    row = {"ts": datetime.fromtimestamp(NOW - off, timezone.utc).isoformat(), "tag": "gateway.start_refused",
           "level": "info", "pid": 300 + off, "reason": reason, "owner_pid": owner, "owner_alive": alive}
    with open(home / "logs" / "gateway-exit-diag.log", "a") as f:
        f.write(json.dumps(row) + "\n")


def test_start_refused_next_to_live_gateway_is_not_a_defect(home):
    """A second `gateway run` refused because a live gateway owns the home (MacBook, gateway-6efc15a270)."""
    (home / "logs" / "gateway-exit-diag.log").write_text("")
    _refused(home, 120)
    _refused(home, 90, alive=None, reason="pid_claim", owner=None)  # runtime lock held: a live holder
    assert kinds(sg.collect(home, {}, "macbook", NOW), "gateway") == []


def test_start_refused_by_a_dead_owner_files(home):
    """Refused by a pid that is not running: a stale record blocks every start, which is a defect."""
    (home / "logs" / "gateway-exit-diag.log").write_text("")
    _refused(home, 60, alive=False)
    gw = kinds(sg.collect(home, {}, "macbook", NOW), "gateway")
    assert len(gw) == 1
    assert "start refused by a gateway that is not running" in gw[0]["symptom"]
    assert "already_running" in gw[0]["symptom"]


def test_supervised_start_refused_by_live_owner_goes_to_the_storm_lane_at_once(home):
    """One refusal by a LIVE owner under --external-supervisor is the restart-storm defect itself (the supervisor's
    main pid ended before the draining gateway): it files on the storm fingerprint without waiting for 3, and is
    flagged so an open card gets it as a comment (gateway-cc28b19bca)."""
    (home / "logs" / "gateway-exit-diag.log").write_text("")
    from datetime import datetime, timezone
    row = {"ts": datetime.fromtimestamp(NOW - 60, timezone.utc).isoformat(), "tag": "gateway.start_refused",
           "level": "info", "pid": 360, "reason": "host_refused", "owner_pid": 97265, "owner_alive": True,
           "external_supervisor": True}
    with open(home / "logs" / "gateway-exit-diag.log", "a") as f:
        f.write(json.dumps(row) + "\n")
    sigs = sg.collect(home, {}, "macbook", NOW)
    gw = kinds(sigs, "gateway")
    assert len(gw) == 1
    assert gw[0]["fp"] == sg.fp("gateway", "macbook", "default", "gateway.exit_signal", "storm")
    assert "supervisor restarted into a live gateway" in gw[0]["symptom"]
    d = sg.to_defect(gw[0])
    assert d["comment_open_card"] is True


def test_signals_and_refused_starts_share_one_restart_storm(home):
    """3+ bounces or refused starts within 10 min on one host+profile is a restart storm: one card."""
    (home / "logs" / "gateway-exit-diag.log").write_text("")
    _gw_rows(home, 400, 200)
    _refused(home, 100)
    gw = kinds(sg.collect(home, {}, "macbook", NOW), "gateway")
    assert len(gw) == 1 and "restart storm" in gw[0]["symptom"]


# ── provider 401/403 the retry loop handles (agent-101f72db15) ────────────────────────────────────────────────

AUTH_OWN = "/home/you/.hermes/hermes-agent/agent/chat_completion_helpers.py"
AUTH_LIB = "/home/you/.hermes/installs/x/venv/lib/python3.14/site-packages/anthropic/_base_client.py"
AUTH_MSG = {
    "invalid": "Invalid authentication credentials",
    "revoked": "OAuth access token has been revoked.",
    "expired": "OAuth token has expired. Please obtain a new token or refresh your existing token.",
}


def auth_rec(ts="2026-10-28 13:19:38", reason="invalid", sid="20261028_130956_a12ab0",
             msg="Streaming failed before delivery", status=401, exc="anthropic.AuthenticationError"):
    """The real 13:19 PT macbook record (spool row agent-101f72db15, session 20261028_130956_a12ab0)."""
    body = (f"Error code: {status} - {{'type': 'error', 'error': {{'type': 'authentication_error', "
            f"'message': '{AUTH_MSG[reason]}'}}, 'request_id': 'req_011CgVFiqNZAjYwHe8qaTVMi'}}")
    return [f"{ts},123 ERROR [{sid}] agent.chat_completion_helpers: {msg}: {body}",
            "Traceback (most recent call last):",
            f'  File "{AUTH_OWN}", line 3420, in _open_anthropic_stream',
            "    return normalize_stream_usage(manager.__enter__())",
            f'  File "{AUTH_LIB}", line 1364, in post',
            "    return cast(ResponseT, self.request(cast_to, opts, stream=stream, stream_cls=stream_cls))",
            f'  File "{AUTH_LIB}", line 1137, in request',
            "    raise self._make_status_error_from_response(err.response) from None",
            f"{exc}: {body}"]


def _auth_home(tmp_path, lines):
    h = tmp_path / "authhome"
    (h / "logs").mkdir(parents=True)
    (h / "logs" / "errors.log").write_text("\n".join(lines) + "\n")
    return h


def _ts(s):
    return time.mktime(time.strptime(s, "%Y-%m-%d %H:%M:%S"))


def test_recovered_stream_401_is_not_filed_per_occurrence(tmp_path):
    """(a) the real 13:19 record: the retry loop refreshed/rotated and the turn finished; no per-occurrence signal."""
    assert sg.parse_errors(auth_rec()) == []
    h = _auth_home(tmp_path, auth_rec())
    sigs = sg.collect(h, {}, "macbook", _ts("2026-10-28 13:25:00"))
    assert [s for s in sigs if s["kind"] == "errors"] == []


def test_three_stream_401s_in_ten_minutes_file_one_storm(tmp_path):
    """(b) 3 in 10 min on one host = one auth-failure storm signal, also when they span sweep passes."""
    lines = auth_rec("2026-10-28 13:19:00") + auth_rec("2026-10-28 13:22:00") + auth_rec("2026-10-28 13:27:00")
    h = _auth_home(tmp_path, lines)
    sigs = [s for s in sg.collect(h, {}, "macbook", _ts("2026-10-28 13:30:00")) if s["kind"] == "errors"]
    assert len(sigs) == 1
    st = sigs[0]
    assert "auth-failure storm" in st["symptom"] and "invalid" in st["symptom"] and "macbook" in st["symptom"]
    assert st["session_id"] == "20261028_130956_a12ab0"
    # two of them spread over two passes: the cursor carries the count
    h2 = tmp_path / "h2"
    (h2 / "logs").mkdir(parents=True)
    log = h2 / "logs" / "errors.log"
    cur = {}
    log.write_text("\n".join(auth_rec("2026-10-28 13:19:00") + auth_rec("2026-10-28 13:20:00")) + "\n")
    assert [s for s in sg.collect(h2, cur, "macbook", _ts("2026-10-28 13:21:00")) if s["kind"] == "errors"] == []
    with log.open("a") as f:
        f.write("\n".join(auth_rec("2026-10-28 13:24:00")) + "\n")
    assert len([s for s in sg.collect(h2, cur, "macbook", _ts("2026-10-28 13:25:00")) if s["kind"] == "errors"]) == 1
    # spread over more than 10 minutes: no storm
    h3 = _auth_home(tmp_path / "x", auth_rec("2026-10-28 13:00:00") + auth_rec("2026-10-28 13:09:00")
                    + auth_rec("2026-10-28 13:19:30"))
    assert [s for s in sg.collect(h3, {}, "macbook", _ts("2026-10-28 13:30:00")) if s["kind"] == "errors"] == []


def test_401_reason_class_splits_fingerprints(tmp_path):
    """(c) "revoked" and "expired" are different defects, in storms and in plain (non-stream) tracebacks."""
    def storm(reason):
        lines = sum((auth_rec(f"2026-10-28 13:2{i}:00", reason) for i in range(3)), [])
        sig = [s for s in sg.collect(_auth_home(tmp_path / reason, lines), {}, "macbook",
                                     _ts("2026-10-28 13:30:00")) if s["kind"] == "errors"]
        assert len(sig) == 1 and reason in sig[0]["symptom"]
        return sig[0]["fp"]
    assert len({storm("revoked"), storm("expired"), storm("invalid")}) == 3
    plain = {r: sg.parse_errors(auth_rec(reason=r, msg="Anthropic client call failed")) for r in ("revoked", "expired")}
    assert all(len(v) == 1 for v in plain.values())
    assert plain["revoked"][0]["fp"] != plain["expired"][0]["fp"]
    assert "revoked" in plain["revoked"][0]["symptom"] and "expired" in plain["expired"][0]["symptom"]


GUARD_LINES = {
    "refresh-persists": "2026-10-28 13:19:40,001 WARNING [20261028_130956_a12ab0] run_agent: Credential auth failure "
                        "persists after 2 refreshes for pool entry acct-b — treating as unrecoverable and allowing "
                        "fallback to activate.",
    "moved-off-entry": "2026-10-28 13:19:41,001 WARNING [20261028_130956_a12ab0] run_agent: Anthropic pre-request "
                       "credential check moved the session off pool entry acct-b (now acct-a)",
    "key-owner-mismatch": "2026-10-28 13:19:42,001 WARNING [20261028_130956_a12ab0] agent.credential_pool_pin: "
                          "Credential auth failure: the failing key belongs to pool entry acct-a, but the session is "
                          "attributed to entry acct-b",
}


def test_each_e0a5909348_guard_warning_files_a_signal(tmp_path):
    """(d) the guard warnings have no traceback; each one still files, fingerprinted by provider + warning kind."""
    fps = set()
    for kind, line in GUARD_LINES.items():
        rows = sg.parse_errors([line])
        assert len(rows) == 1, kind
        r = rows[0]
        assert r["kind"] == "errors" and r["session_id"] == "20261028_130956_a12ab0"
        assert kind in r["symptom"] and "acct-" in r["details"]
        fps.add(r["fp"])
    assert len(fps) == 3
    # the same warning for another pool entry is the same defect (entry ids are not in the fingerprint)
    again = sg.parse_errors([GUARD_LINES["refresh-persists"].replace("acct-b", "acct-z")])
    assert again[0]["fp"] in fps
    # and through the real collector
    h = _auth_home(tmp_path, list(GUARD_LINES.values()))
    assert len([s for s in sg.collect(h, {}, "macbook", _ts("2026-10-28 13:25:00")) if s["kind"] == "errors"]) == 3


def test_kanban_sibling_cards_with_same_stem_and_error_are_one_signal(tmp_path):
    h = tmp_path / "h"
    err = "worker exited cleanly (rc=0) without kanban_complete — protocol violation"
    runs = []
    for n in (14, 20, 23):
        runs += [(f"t_load{n}", f"load with-worker #{n}", None, "crashed", err)] * 2
    runs += [("t_other", "load with-worker #30", None, "crashed", "worker died: SIGKILL"),
             ("t_other", "load with-worker #30", None, "crashed", "worker died: SIGKILL"),
             ("t_build", "build the thing", None, "crashed", err),
             ("t_build", "build the thing", None, "crashed", err)]
    make_board(h / "kanban" / "boards" / "worker-load" / "kanban.db", runs)
    ks = sg.collect_kanban(h, {}, NOW, "hubhost")
    assert len(ks) == 3, [s["symptom"] for s in ks]
    batch = [s for s in ks if "t_load14" in s["details"]]
    assert len(batch) == 1
    assert all(t in batch[0]["details"] for t in ("t_load14", "t_load20", "t_load23"))
    assert batch[0]["kanban"] == {"board": "worker-load", "cards": ["t_load14", "t_load20", "t_load23"]}
    # the batch keeps one fingerprint across passes; unrelated cards stay separate
    again = sg.collect_kanban(h, {}, NOW, "hubhost")
    assert {s["fp"] for s in again} == {s["fp"] for s in ks}
    assert len({s["fp"] for s in ks}) == 3
    d = sg.to_defect(batch[0])
    assert d["kanban"]["cards"] == ["t_load14", "t_load20", "t_load23"]


# ── handoff nudge outcome (self-heal agent-e278947864): file the outcome, not the nudge ──────────────────────

def _coach_home(tmp_path, rows):
    h = tmp_path / "home"
    (h / "coach").mkdir(parents=True)
    (h / "coach" / "log.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    return h


NUDGE = {"ts": NOW - 120, "event": "reply_nudge", "session_id": "20261027_234200_abcdef", "rule": "handoff",
         "p": 0.82, "evidence": "Asks user to sign into Telnyx", "draft_id": "d1"}


def _outcome(resolved, ts=NOW - 86, **kw):
    return {"ts": ts, "event": "reply_nudge_outcome", "rule": "handoff", "session_id": NUDGE["session_id"],
            "draft_id": "d1", "p": 0.1 if resolved else 0.9, "resolved": resolved, **kw}


def _handoffs(sigs):
    return [s for s in sigs if s["kind"] == "coach" and "handed the user a step" in s["symptom"]]


def test_handoff_nudge_resolved_by_the_revised_reply_files_nothing(tmp_path):
    h = _coach_home(tmp_path, [NUDGE, _outcome(True)])
    cur = {}
    assert _handoffs(sg.collect_coach(h, cur, NOW)) == []
    assert _handoffs(sg.collect_coach(h, cur, NOW + 3600)) == []  # and never later either


def test_handoff_nudge_still_handing_off_after_revision_files_one_defect(tmp_path):
    h = _coach_home(tmp_path, [NUDGE, _outcome(False)])
    got = _handoffs(sg.collect_coach(h, {}, NOW))
    assert len(got) == 1 and got[0]["session_id"] == NUDGE["session_id"]
    assert "Telnyx" in got[0]["symptom"] and "still handed off" in got[0]["details"]


def test_handoff_nudge_without_outcome_files_only_after_the_grace_window(tmp_path):
    h = _coach_home(tmp_path, [NUDGE])
    cur = {}
    assert _handoffs(sg.collect_coach(h, cur, NOW)) == []  # 2 min old: the revision may still be running
    assert _handoffs(sg.collect_coach(h, cur, NUDGE["ts"] + 14 * 60)) == []
    late = _handoffs(sg.collect_coach(h, cur, NUDGE["ts"] + sg.HANDOFF_GRACE_S + 1))
    assert len(late) == 1 and "no outcome" in late[0]["details"]
    assert _handoffs(sg.collect_coach(h, cur, NUDGE["ts"] + 3600)) == []  # filed once, then forgotten


def test_handoff_outcome_in_a_later_pass_resolves_the_pending_nudge(tmp_path):
    h = _coach_home(tmp_path, [NUDGE])
    cur = {}
    assert _handoffs(sg.collect_coach(h, cur, NOW)) == []
    with (h / "coach" / "log.jsonl").open("a") as f:
        f.write(json.dumps(_outcome(True, ts=NOW + 30)) + "\n")
    assert _handoffs(sg.collect_coach(h, cur, NOW + 3600)) == []


def test_handoff_outcome_of_another_session_does_not_resolve_it(tmp_path):
    other = {**_outcome(True), "session_id": "someone_else", "draft_id": "d9"}
    h = _coach_home(tmp_path, [NUDGE, other])
    assert len(_handoffs(sg.collect_coach(h, {}, NUDGE["ts"] + sg.HANDOFF_GRACE_S + 1))) == 1


# ── managed-worker crash storm + dispatcher systemic hold (the 10-29 ModelRoutingError outage) ────────────────

MR_MSG = "Class selectors require provider auto; use a concrete model to pin a provider"


def _worker_block(pid, exc="agent.model_routing.ModelRoutingError", msg=MR_MSG, stamp=None, core="8ccfb38284"):
    lines = ["managed worker failed", "Traceback (most recent call last):",
             '  File "/home/you/.hermes/hermes-agent/agent/managed_worker.py", line 464, in main',
             "    execute(frame, channel)",
             '  File "/home/you/.hermes/hermes-agent/agent/model_routing.py", line 125, in resolve_model_route',
             f'    raise {exc.split(".")[-1]}("{msg}")',
             f"{exc}: {msg}"]
    head = f"[pid {pid}] @ {stamp} core={core}\n" if stamp else ""
    return head + "".join(f"[pid {pid}] {l}\n" for l in lines)


def _worker_home(tmp_path):
    h = tmp_path / "home"
    (h / "logs").mkdir(parents=True)
    (h / "logs" / "managed-worker.log").write_text("")
    return h


def _primed_worker_home(tmp_path):
    """A worker home whose (empty) log the detector has already seen once, so crashes appended after it are new
    (the first pass over a log only seeds the cursor: kanban-e001f2d4a0)."""
    h, cur = _worker_home(tmp_path), {}
    assert _storms(sg.collect(h, cur, "hubhost", NOW - 600)) == []
    return h, cur


def _append_worker(h, *blocks):
    with (h / "logs" / "managed-worker.log").open("a") as f:
        f.write("".join(blocks))


def _storms(sigs):
    return [s for s in sigs if s["kind"] == "worker"]


def test_worker_crash_storm_across_two_passes_files_one_signal(tmp_path):
    """(a) 3 pids dying with the same exception over two passes file exactly one storm signal."""
    h, cur = _primed_worker_home(tmp_path)
    _append_worker(h, _worker_block(101), _worker_block(102))
    assert _storms(sg.collect(h, cur, "hubhost", NOW)) == []
    _append_worker(h, _worker_block(103))
    st = _storms(sg.collect(h, cur, "hubhost", NOW + 60))
    assert len(st) == 1, st
    s = st[0]
    assert s["component"] == "kanban"
    assert ("managed-worker crash storm on hubhost: 3 workers died with ModelRoutingError: Class selectors"
            in s["symptom"])
    assert all(p in s["details"] for p in ("101", "102", "103"))
    assert "Traceback (most recent call last):" in s["details"] and len(s["details"]) <= 1500
    norm = sg._norm_msg(MR_MSG)
    assert s["fp"] == sg.fp("kanban", "worker-storm", "agent.model_routing.ModelRoutingError", norm)
    # the defect it becomes keeps its kind, so the hub's ingest/drain accept it
    assert "worker" in sg.KINDS and sg.to_defect(s)["kind"] == "worker"


def test_two_worker_crashes_are_not_a_storm(tmp_path):
    """(b) 2 pids give none."""
    h, cur = _primed_worker_home(tmp_path)
    _append_worker(h, _worker_block(101), _worker_block(102))
    assert _storms(sg.collect(h, cur, "hubhost", NOW)) == []


def test_three_different_worker_exceptions_are_not_a_storm(tmp_path):
    """(c) 3 pids with 3 different exceptions give none."""
    h, cur = _primed_worker_home(tmp_path)
    _append_worker(h, _worker_block(101, "agent.a.FooError", "foo broke"),
                   _worker_block(102, "agent.b.BarError", "bar broke"),
                   _worker_block(103, "ValueError", "baz broke"))
    assert _storms(sg.collect(h, cur, "hubhost", NOW)) == []


def test_worker_storm_message_numbers_are_normalized(tmp_path):
    """the same crash with a different pid/number in its message is still one storm"""
    h, cur = _primed_worker_home(tmp_path)
    _append_worker(h, *[_worker_block(p, "RuntimeError", f"worker {p} lost lease 7{p}") for p in (101, 102, 103)])
    assert len(_storms(sg.collect(h, cur, "hubhost", NOW))) == 1


def test_worker_storm_continuing_inside_the_window_files_no_second_signal(tmp_path):
    """(d) the storm keeps going in the next pass inside WORKER_STORM_S: no second signal; after it, one again."""
    h, cur = _primed_worker_home(tmp_path)
    _append_worker(h, *[_worker_block(p) for p in (101, 102, 103)])
    assert len(_storms(sg.collect(h, cur, "hubhost", NOW))) == 1
    _append_worker(h, *[_worker_block(p) for p in (104, 105, 106, 107)])
    assert _storms(sg.collect(h, cur, "hubhost", NOW + 300)) == []
    _append_worker(h, *[_worker_block(p) for p in (108, 109, 110)])
    assert len(_storms(sg.collect(h, cur, "hubhost", NOW + sg.WORKER_STORM_S + 400))) == 1


def test_worker_crashes_outside_the_window_do_not_add_up(tmp_path):
    h, cur = _primed_worker_home(tmp_path)
    _append_worker(h, _worker_block(101), _worker_block(102))
    sg.collect(h, cur, "hubhost", NOW)
    _append_worker(h, _worker_block(103))
    assert _storms(sg.collect(h, cur, "hubhost", NOW + sg.WORKER_STORM_S + 1)) == []


def test_worker_log_cursor_advances_so_lines_are_never_reread(tmp_path):
    """(f) a pass reads each line once: the same 2 crashes never count twice, and the cursor offset moves."""
    h, cur = _primed_worker_home(tmp_path)
    _append_worker(h, _worker_block(101), _worker_block(102))
    sg.collect(h, cur, "hubhost", NOW)
    size = (h / "logs" / "managed-worker.log").stat().st_size
    assert cur["worker:default"]["off"] == size
    # nothing appended: a re-run reads no lines, so the two old pids never become three
    assert sg.read_new(h / "logs" / "managed-worker.log", dict(cur), "worker:default", NOW) == []
    for i in range(3):
        assert _storms(sg.collect(h, cur, "hubhost", NOW + 10 * (i + 1))) == []
    assert cur["worker:default"]["off"] == size


def test_worker_backlog_at_first_sight_files_nothing(tmp_path):
    """kanban-e001f2d4a0: the log has no timestamps, so crashes already in it when the detector first reads it
    (new host, new profile, cursor reset) are old news, not a live storm: the first pass only seeds the cursor."""
    h, cur = _worker_home(tmp_path), {}
    _append_worker(h, *[_worker_block(p) for p in range(39500, 39525)])
    assert _storms(sg.collect(h, cur, "hubhost", NOW)) == []
    assert cur["worker:default"]["off"] == (h / "logs" / "managed-worker.log").stat().st_size
    assert _storms(sg.collect(h, cur, "hubhost", NOW + 60)) == []


def test_worker_storm_after_first_sight_still_files(tmp_path):
    """after the first (seeding) pass, 3 new crash blocks still file one storm, and the details say which byte
    range of the log the crashes came from (so a late or replayed signal is visible in the card evidence)."""
    h, cur = _worker_home(tmp_path), {}
    _append_worker(h, *[_worker_block(p) for p in range(201, 226)])
    assert _storms(sg.collect(h, cur, "hubhost", NOW)) == []
    start = (h / "logs" / "managed-worker.log").stat().st_size
    _append_worker(h, *[_worker_block(p) for p in (301, 302, 303)])
    end = (h / "logs" / "managed-worker.log").stat().st_size
    st = _storms(sg.collect(h, cur, "hubhost", NOW + 60))
    assert len(st) == 1, st
    d = st[0]["details"]
    assert all(p in d for p in ("301", "302", "303")) and "201" not in d
    assert "first seen this pass" in d and f"bytes {start}-{end}" in d


def test_worker_log_missing_at_first_sight_then_created_counts_its_crashes(tmp_path):
    """a log that does not exist yet at first sight is seeded at offset 0: crashes written into it later are new."""
    h, cur = tmp_path / "home", {}
    (h / "logs").mkdir(parents=True)
    assert _storms(sg.collect(h, cur, "hubhost", NOW)) == []
    _append_worker(h, *[_worker_block(p) for p in (101, 102, 103)])
    assert len(_storms(sg.collect(h, cur, "hubhost", NOW + 60))) == 1


def test_worker_storm_scrubs_secrets_from_details(tmp_path):
    h, cur = _primed_worker_home(tmp_path)
    _append_worker(h, *[_worker_block(p, "RuntimeError", "bad key sk-ant-abc123DEF456ghi789") for p in (1, 2, 3)])
    st = _storms(sg.collect(h, cur, "hubhost", NOW))
    assert len(st) == 1 and "sk-ant-abc123" not in st[0]["details"] + st[0]["symptom"]


def _iso_epoch(stamp):
    from datetime import datetime
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()


def test_worker_storm_from_headered_blocks_reports_crash_times_and_core_sha(tmp_path):
    """core's `[pid N] @ <UTC> core=<sha>` header dates each crash: the storm names the earliest/latest crash and
    the sha(s), `at` is the latest crash (not the pass time), and the header is not part of the traceback."""
    h, cur = _primed_worker_home(tmp_path)
    stamps = ("2026-10-30T07:39:02Z", "2026-10-30T07:41:30Z", "2026-10-30T08:01:15Z")
    _append_worker(h, _worker_block(101, stamp=stamps[0], core="76dedd30c8"),
                   _worker_block(102, stamp=stamps[1], core="7c00ede0c4"),
                   _worker_block(103, stamp=stamps[2], core="7c00ede0c4"))
    st = _storms(sg.collect(h, cur, "hubhost", NOW))
    assert len(st) == 1, st
    s = st[0]
    assert ("Crash times (UTC, log headers): earliest 2026-10-30T07:39:02Z, latest 2026-10-30T08:01:15Z; "
            "core: 76dedd30c8, 7c00ede0c4") in s["details"]
    assert s["at"] == _iso_epoch(stamps[2]) != NOW
    assert "@ 2026" not in s["details"].split("Latest (pid")[1]
    assert "log lines carry no timestamps" not in s["details"]
    # same fingerprint as a headerless storm of the same crash
    assert s["fp"] == sg.fp("kanban", "worker-storm", "agent.model_routing.ModelRoutingError", sg._norm_msg(MR_MSG))


def test_worker_storm_with_some_headerless_pids_keeps_the_pass_time(tmp_path):
    """a storm mixing old-core (no header) and new-core blocks reports the stamped times but keeps `at` = pass."""
    h, cur = _primed_worker_home(tmp_path)
    _append_worker(h, _worker_block(101), _worker_block(102, stamp="2026-10-30T07:41:30Z"), _worker_block(103))
    s = _storms(sg.collect(h, cur, "hubhost", NOW))[0]
    assert s["at"] == NOW
    assert "(1 of 3 pids stamped): earliest 2026-10-30T07:41:30Z" in s["details"]


def test_worker_storm_header_times_span_passes(tmp_path):
    """crash times survive in the cursor between passes, so a storm completed in a later pass still dates all 3."""
    h, cur = _primed_worker_home(tmp_path)
    _append_worker(h, _worker_block(101, stamp="2026-10-30T07:00:00Z"), _worker_block(102, stamp="2026-10-30T07:05:00Z"))
    assert _storms(sg.collect(h, cur, "hubhost", NOW)) == []
    _append_worker(h, _worker_block(103, stamp="2026-10-30T07:09:00Z"))
    s = _storms(sg.collect(h, cur, "hubhost", NOW + 60))[0]
    assert "earliest 2026-10-30T07:00:00Z, latest 2026-10-30T07:09:00Z; core: 8ccfb38284" in s["details"]
    assert s["at"] == _iso_epoch("2026-10-30T07:09:00Z")


def test_headerless_worker_storm_has_no_crash_time_line(tmp_path):
    h, cur = _primed_worker_home(tmp_path)
    _append_worker(h, *[_worker_block(p) for p in (101, 102, 103)])
    s = _storms(sg.collect(h, cur, "hubhost", NOW))[0]
    assert s["at"] == NOW and "Crash times" not in s["details"]
    assert "log lines carry no timestamps" in s["details"]


def _hold_board(db, events):
    db.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(db)
    c.execute("create table tasks (id text primary key, title text, status text, idempotency_key text)")
    c.execute("create table task_runs (id integer primary key autoincrement, task_id text, status text, "
              "started_at integer, ended_at integer, outcome text, error text)")
    c.execute("create table task_events (id integer primary key autoincrement, task_id text not null, run_id integer,"
              " kind text not null, payload text, created_at integer not null, uid text)")
    for tid, payload, at in events:
        c.execute("insert or ignore into tasks values (?,?,?,?)", (tid, f"card {tid}", "triage", None))
        c.execute("insert into task_events (task_id, kind, payload, created_at) values (?,?,?,?)",
                  (tid, "gave_up", json.dumps(payload), int(at)))
    c.commit()
    c.close()


HOLD_ERR = ("pid {pid} exited with code 1 [managed worker lost: managed_worker_failed] Worker's last output: "
            "'something card-specific {pid}'")


def _holds(sigs):
    return [s for s in sigs if s["kind"] == "kanban" and "systemic" in s["symptom"]]


def test_sticky_gave_up_files_one_systemic_hold_signal(tmp_path):
    """(e) the dispatcher's systemic breaker held 3 cards (sticky gave_up): one signal naming the cards."""
    h = tmp_path / "home"
    evs = [(f"t_hold{i}", {"failures": 1, "effective_limit": 1, "sticky": True, "pid": 3210800 + i,
                            "error": HOLD_ERR.format(pid=3210800 + i)}, NOW - 120) for i in range(3)]
    _hold_board(h / "kanban.db", evs)
    cur = {}
    hs = _holds(sg.collect_kanban(h, cur, NOW, "hubhost"))
    assert len(hs) == 1, hs
    s = hs[0]
    assert s["symptom"] == "systemic kanban crash on hubhost: dispatcher held 3 card(s) in triage"
    assert all(f"t_hold{i}" in s["details"] for i in range(3)) and "managed_worker_failed" in s["details"]
    head = sg._hold_head(HOLD_ERR.format(pid=1))
    assert s["fp"] == sg.fp("kanban", "systemic-hold", head)
    # no card trigger: the held cards finishing later (they rerun after the hold) must not drop the defect
    assert "kanban" not in s and sh.kanban_trigger(sg.to_defect(s)) is None
    # the event cursor moved: the same events are not filed again
    assert _holds(sg.collect_kanban(h, cur, NOW + 60, "hubhost")) == []


def test_non_sticky_gave_up_files_no_systemic_hold(tmp_path):
    """(e) an ordinary gave_up (one card used up its own retries) keeps today's behavior: no signal."""
    h = tmp_path / "home"
    _hold_board(h / "kanban.db", [("t_plain", {"failures": 3, "effective_limit": 3,
                                               "error": HOLD_ERR.format(pid=5)}, NOW - 120)])
    assert _holds(sg.collect_kanban(h, {}, NOW, "hubhost")) == []


def test_sticky_holds_inside_the_window_are_not_refiled(tmp_path):
    h = tmp_path / "home"
    db = h / "kanban.db"
    _hold_board(db, [("t_a", {"sticky": True, "error": HOLD_ERR.format(pid=1)}, NOW - 100)])
    cur = {}
    assert len(_holds(sg.collect_kanban(h, cur, NOW, "hubhost"))) == 1
    c = sqlite3.connect(db)
    c.execute("insert into task_events (task_id, kind, payload, created_at) values (?,?,?,?)",
              ("t_b", "gave_up", json.dumps({"sticky": True, "error": HOLD_ERR.format(pid=2)}), int(NOW) + 200))
    c.commit()
    c.close()
    assert _holds(sg.collect_kanban(h, cur, NOW + 300, "hubhost")) == []


def _set_status(db, status, tids):
    c = sqlite3.connect(db)
    c.executemany("update tasks set status=? where id=?", [(status, t) for t in tids])
    c.commit()
    c.close()


def test_first_sight_skips_sticky_holds_older_than_24h(tmp_path):
    """(kanban-754a3e3cf1) the collector's first pass over a board with history (no cursor) filed a 45 h old hold
    the operator had already cleared. A first sight looks back at most 24 h for sticky holds."""
    h = tmp_path / "home"
    _hold_board(h / "kanban.db", [(f"t_old{i}", {"sticky": True, "error": HOLD_ERR.format(pid=i)}, NOW - 45 * 3600)
                                  for i in range(2)])
    assert _holds(sg.collect_kanban(h, {}, NOW, "hubhost")) == []
    # a hold inside the 24 h window on a first sight is still filed
    h2 = tmp_path / "home2"
    _hold_board(h2 / "kanban.db", [("t_new", {"sticky": True, "error": HOLD_ERR.format(pid=1)}, NOW - 20 * 3600)])
    assert len(_holds(sg.collect_kanban(h2, {}, NOW, "hubhost"))) == 1


def _filed_hold(tmp_path, at):
    h = tmp_path / "home"
    db = h / "kanban.db"
    tids = ["t_held1", "t_held2"]
    _hold_board(db, [(t, {"sticky": True, "error": HOLD_ERR.format(pid=i)}, at) for i, t in enumerate(tids)])
    hs = _holds(sg.collect_kanban(h, {}, at + 60, "hubhost"))
    assert len(hs) == 1, hs
    return h, db, tids, sg.to_defect(hs[0])


def test_stale_hold_with_closed_cards_is_dropped(tmp_path):
    """(kanban-754a3e3cf1) a hold filed 48 h after it fired, whose cards were all archived: nothing is broken."""
    h, db, tids, d = _filed_hold(tmp_path, NOW - 48 * 3600)
    _set_status(db, "archived", tids)
    why = sh.kanban_trigger_closed(d, home=h, now=NOW)
    assert why and "stale systemic hold" in why and all(t in why for t in tids), why
    # one held card still open: file it
    _set_status(db, "triage", tids[:1])
    assert sh.kanban_trigger_closed(d, home=h, now=NOW) is None


def test_fresh_hold_with_done_cards_still_files(tmp_path):
    """A fresh hold is filed even when its cards rerun and finish right after: the crash cause may still be live."""
    h, db, tids, d = _filed_hold(tmp_path, NOW - 600)
    _set_status(db, "done", tids)
    assert sh.kanban_trigger_closed(d, home=h, now=NOW) is None
    # the held cards are not a per-card trigger (their finishing must never drop a fresh hold)
    assert sh.kanban_trigger(d) is None


# ── the exception's reason code is part of the error fingerprint ──
def _wrapper_tb(exc_line, own="/home/you/.hermes/hermes-agent/gateway/session_store.py"):
    return ["2026-10-29 23:36:00,000 ERROR [20261029_233600_aaaaaa] gateway.run: Failed to release session turn lease",
            "Traceback (most recent call last):",
            f'  File "{own}", line 10, in release_turn',
            "    raise WorkerPersistenceError(reason)", exc_line]


def _fp_of(exc_line):
    (row,) = sg.parse_errors(_wrapper_tb(exc_line))
    return row["fp"]


def test_reason_code_exceptions_with_the_same_frames_get_different_fingerprints():
    assert _fp_of("WorkerPersistenceError: owner_unavailable") != _fp_of("WorkerPersistenceError: worker_receipt_timeout")


def test_same_reason_code_with_different_session_ids_keeps_one_fingerprint():
    a = _fp_of("WorkerPersistenceError: owner_unavailable")
    assert a == _fp_of("WorkerPersistenceError: owner_unavailable ")
    assert _fp_of("WorkerPersistenceError: owner_unavailable (session 20261029_1)") == \
        _fp_of("WorkerPersistenceError: owner_unavailable (session 20261030_2)")


def test_free_text_message_keeps_the_fingerprint_it_had_before():
    (row,) = sg.parse_errors(_wrapper_tb("ValueError: bad value 123 for x"))
    old = sg.fp(row["component"], "tb", "ValueError", "session_store.py:release_turn")
    assert row["fp"] == old
    assert _fp_of("ValueError: bad value 123 for x") == _fp_of("ValueError: bad value 456 for x")


# ── a git timeout's verb is part of the error fingerprint (agent-23a0c3b69f) ──
def _git_timeout_tb(cmd):
    return ["2026-10-30 17:13:48,000 ERROR [20261030_171348_aaaaaa] tools.checkpoint_manager: "
            f"git timed out after 30s in /Users/you/work/t_1/mods: {cmd}",
            "Traceback (most recent call last):",
            '  File "/home/you/.hermes/hermes-agent/tools/checkpoint_manager.py", line 402, in _run_git',
            "    result = subprocess.run(",
            '  File "/opt/homebrew/lib/python3.14/subprocess.py", line 1280, in _check_timeout',
            "    raise TimeoutExpired(",
            "subprocess.TimeoutExpired: Command '['git']' timed out after 30 seconds"]


def _git_fp(cmd):
    (row,) = sg.parse_errors(_git_timeout_tb(cmd))
    return row["fp"]


def test_git_timeouts_of_different_steps_get_different_fingerprints():
    add = _git_fp("/opt/homebrew/bin/git add -A -- .")
    rev = _git_fp("/opt/homebrew/bin/git rev-list --reverse refs/hermes/fa5246b628e9fa44")
    gc = _git_fp("/usr/bin/nice -n 10 /usr/bin/git gc --prune=now --quiet")
    assert len({add, rev, gc}) == 3
    # the same step in another folder / on another ref stays one defect
    assert rev == _git_fp("/usr/bin/git rev-list --reverse refs/hermes/e009524edf0d5d92")
    assert gc == _git_fp("/usr/bin/git gc --prune=now --quiet")


def test_non_git_timeout_message_keeps_its_fingerprint():
    assert sg._git_step("Command timed out after 30s") == ()


# ── a nudge whose revision never reached the user (self-heal agent-77325213e1: a background-review fork) ─────

def _fork_home(tmp_path, revised, rows):
    """A coach home with one report_only nudge whose failed revision is `revised`, and a state.db holding `rows`
    (role, content) for that session."""
    import sqlite3
    nudge = {**NUDGE, "rule": "report_only", "outcome": True, "evidence": "Final reports documenting limits"}
    h = _coach_home(tmp_path, [nudge, {**_outcome(False), "rule": "report_only", "reply": revised}])
    db = sqlite3.connect(h / "state.db")
    db.execute("create table messages (id integer primary key, session_id text, role text, content text,"
               " timestamp real, active integer default 1)")
    db.executemany("insert into messages (session_id, role, content, timestamp) values (?, ?, ?, ?)",
                   [(NUDGE["session_id"], r, c, NOW - 200) for r, c in rows])
    db.commit()
    db.close()
    return h


def test_revision_missing_from_the_session_routes_as_coach_fork_bug(tmp_path):
    h = _fork_home(tmp_path, "The rating page is already fixed and live in the chat, two messages up.",
                   [("user", "submit isnt working"), ("assistant", "Fixed the rating page; Submit now saves.")])
    got = sg.collect_coach(h, {}, NOW)
    assert len(got) == 1
    s = got[0]
    assert s["component"] == "coach" and "never reached the user" in s["symptom"]
    assert "agent's revised reply" not in s["symptom"]
    assert s["fp"] == sg.fp("coach", "judged_unsent_draft", "report_only")


def test_revision_present_in_the_session_still_files_as_agent_defect(tmp_path):
    text = "Here is what I documented about the Submit limits; the save path is untested."
    h = _fork_home(tmp_path, text, [("user", "submit isnt working"), ("assistant", text + "\n\nMore detail.")])
    got = sg.collect_coach(h, {}, NOW)
    assert len(got) == 1 and got[0]["component"] == "agent"
    assert "agent's revised reply still failed the coach report_only check" in got[0]["symptom"]


def test_revision_check_without_state_db_files_as_agent_defect(tmp_path):
    nudge = {**NUDGE, "rule": "report_only", "outcome": True}
    h = _coach_home(tmp_path, [nudge, {**_outcome(False), "rule": "report_only",
                                       "reply": "A revision long enough to compare with stored rows."}])
    got = sg.collect_coach(h, {}, NOW)
    assert len(got) == 1 and got[0]["component"] == "agent"


# ── one reply_check rule holding the same fixed text across chats (self-heal agent-21185525f4) ─────────────────
# 5 of the hub's 8 status_only holds since 10-28 began with the same "[Kanban close-out]" prompt, in 4 chats, and
# nothing noticed until one revision failed too. Rows log `ask_head` (the first 40 chars of the user turn).

TPL = "[Kanban close-out] The Kanban notice abo"


def _tpl_nudge(i, sid, head=TPL, rule="status_only", ts=None):
    return {"ts": NOW - 3 * 86400 + i * 3600 if ts is None else ts, "event": "reply_nudge", "session_id": sid,
            "rule": rule, "p": 1.0, "evidence": "x", "ask_head": head, "outcome": True}


def _templates(sigs):
    return [s for s in sigs if s["kind"] == "coach" and "same opening text" in s["symptom"]]


def test_rule_holding_one_template_in_three_turns_across_two_chats_files_one_card(tmp_path):
    rows = [_tpl_nudge(0, "s1"), _tpl_nudge(1, "s2")]
    h = _coach_home(tmp_path, rows)
    cur = {}
    assert _templates(sg.collect_coach(h, cur, NOW)) == []  # 2 holds: not yet
    with (h / "coach" / "log.jsonl").open("a") as f:
        f.write(json.dumps(_tpl_nudge(2, "s1")) + "\n")
    got = _templates(sg.collect_coach(h, cur, NOW + 60))
    assert len(got) == 1
    s = got[0]
    assert "status_only" in s["symptom"] and TPL in s["symptom"] and s["comment_open_card"] is True
    assert "3 holds" in s["details"] and "2 chats" in s["details"]
    first_fp = s["fp"]
    # a later hit of the same template keeps the fingerprint and rides comment_open_card (a comment, not a new card)
    with (h / "coach" / "log.jsonl").open("a") as f:
        f.write(json.dumps(_tpl_nudge(3, "s3")) + "\n")
    again = _templates(sg.collect_coach(h, cur, NOW + 120))
    assert len(again) == 1 and again[0]["fp"] == first_fp and again[0]["comment_open_card"] is True
    assert sg.to_defect(again[0])["comment_open_card"] is True


def test_template_holds_need_two_chats_seven_days_and_the_same_rule(tmp_path):
    one_chat = [_tpl_nudge(i, "s1") for i in range(4)]
    assert _templates(sg.collect_coach(_coach_home(tmp_path / "a", one_chat), {}, NOW)) == []
    old = [_tpl_nudge(0, "s1", ts=NOW - 8 * 86400), _tpl_nudge(1, "s2"), _tpl_nudge(2, "s3")]
    assert _templates(sg.collect_coach(_coach_home(tmp_path / "b", old), {}, NOW)) == []
    mixed = [_tpl_nudge(0, "s1"), _tpl_nudge(1, "s2", rule="report_only"), _tpl_nudge(2, "s3")]
    assert _templates(sg.collect_coach(_coach_home(tmp_path / "c", mixed), {}, NOW)) == []
    human = [_tpl_nudge(0, "s1", head="why is it slow"), _tpl_nudge(1, "s2", head="whats going on"),
             _tpl_nudge(2, "s3", head="any update?")]
    assert _templates(sg.collect_coach(_coach_home(tmp_path / "d", human), {}, NOW)) == []
    no_head = [{k: v for k, v in _tpl_nudge(i, f"s{i}").items() if k != "ask_head"} for i in range(3)]
    assert _templates(sg.collect_coach(_coach_home(tmp_path / "e", no_head), {}, NOW)) == []


def test_template_head_falls_back_to_the_status_only_evidence_quote(tmp_path):
    """Rows from backends still on the old plugin carry no ask_head; status_only's evidence quotes the ask."""
    ev = 'asked "[Kanban close-out] The Kanban notice above reached this chat and no turn followe"; this turn only ran x'
    rows = [{**{k: v for k, v in _tpl_nudge(i, f"s{i % 2}").items() if k != "ask_head"}, "evidence": ev}
            for i in range(3)]
    assert len(_templates(sg.collect_coach(_coach_home(tmp_path, rows), {}, NOW))) == 1


# ── provider 5xx whose body is a proxy/CDN HTML page (agent-ee16fb8bb9) ─────────────────────────────────────

def _html_502_record():
    """The hub's 2026-10-30 11:42:45 errors.log record, byte for byte (CRLF HTML body from Cloudflare): the
    exception line reads `anthropic.InternalServerError: <html>` and the status is only on lines 2-4."""
    return (FIX / "errors-html-502.txt").read_bytes().decode().split("\n")


def test_provider_5xx_with_html_proxy_body_is_transient():
    lines = _html_502_record()
    assert any(l.startswith("anthropic.InternalServerError: <html>") for l in lines)
    assert sg.parse_errors(lines) == []


@pytest.mark.parametrize("title", ["503 Service Temporarily Unavailable", "504 Gateway Time-out",
                                   "500 Internal Server Error"])
def test_provider_5xx_html_variants_are_transient(title):
    lines = [f"2026-10-30 11:42:45,778 ERROR agent.chat_completion_helpers: Streaming failed before delivery: <html>",
             "Traceback (most recent call last):",
             '  File "/home/you/.hermes/hermes-agent/agent/chat_completion_helpers.py", line 3410, in _open',
             "    return manager.__enter__()",
             "anthropic.InternalServerError: <html>\r", f"<head><title>{title}</title></head>\r", "</html>"]
    assert sg.parse_errors(lines) == []


def test_non_provider_exception_mentioning_502_on_later_lines_still_files():
    """Control: the multi-line skip is scoped to provider exception types; our own error whose message spans
    lines that mention 502 / Bad Gateway is still a defect."""
    lines = ["2026-10-30 11:42:45,778 ERROR agent.chat_completion_helpers: lookup failed",
             "Traceback (most recent call last):",
             '  File "/home/you/.hermes/hermes-agent/agent/chat_completion_helpers.py", line 10, in _pick',
             "    return table[key]",
             "KeyError: 'route'",
             "known keys: <title>502 Bad Gateway</title> Service Unavailable 503"]
    assert len(sg.parse_errors(lines)) == 1


def test_provider_error_without_5xx_in_body_still_files():
    """A provider error whose HTML body carries no 5xx status (or a 4xx page) is not skipped as transient."""
    lines = ["2026-10-30 11:42:45,778 ERROR agent.chat_completion_helpers: Streaming failed before delivery: <html>",
             "Traceback (most recent call last):",
             '  File "/home/you/.hermes/hermes-agent/agent/chat_completion_helpers.py", line 3410, in _open',
             "    return manager.__enter__()",
             "anthropic.BadRequestError: <html>\r", "<head><title>400 Bad Request</title></head>\r", "</html>"]
    assert len(sg.parse_errors(lines)) == 1


def test_worker_crash_with_html_5xx_body_is_transient():
    lines = ["managed worker failed", "Traceback (most recent call last):",
             '  File "/home/you/.hermes/hermes-agent/agent/managed_worker.py", line 464, in main',
             "    execute(frame, channel)",
             "anthropic.InternalServerError: <html>\r", "<head><title>502 Bad Gateway</title></head>\r",
             "<center><h1>502 Bad Gateway</h1></center>\r", "</html>"]
    log = [f"[pid 41] {l}" for l in lines]
    assert sg.parse_worker_crashes(log) == []
    ctl = [f"[pid 42] {l}" for l in lines[:4] + ["KeyError: 'route'", "<title>502 Bad Gateway</title>"]]
    assert len(sg.parse_worker_crashes(ctl)) == 1


# agent-c46bf512ae: 15 turns died on "Non-retryable client error: Chat ownership lease is unavailable" (a Hermes-side
# refusal, no traceback) and errors.log raised nothing. Each distinct Hermes refusal files one signal; provider HTTP
# errors (already owned by the provider/credit lanes) do not.
def test_non_retryable_hermes_refusal_files_one_signal_per_message():
    lines = ["2026-10-30 21:27:08,405 ERROR [20261022_204322_4d8ce1] agent.conversation_loop: "
             "Non-retryable client error: Chat ownership lease is unavailable",
             "2026-10-30 23:45:50,100 ERROR [20261022_221516_1b9736] agent.conversation_loop: "
             "Non-retryable client error: Chat ownership lease is unavailable"]
    got = sg.parse_errors(lines)
    assert len(got) == 2 and got[0]["fp"] == got[1]["fp"]
    assert got[0]["component"] == "agent" and got[0]["session_id"] == "20261022_204322_4d8ce1"
    assert "Chat ownership lease is unavailable" in got[0]["symptom"]
    other = sg.parse_errors(["2026-10-30 21:27:08,405 ERROR agent.conversation_loop: "
                             "Non-retryable client error: Selected class route is ineligible for this request"])
    assert len(other) == 1 and other[0]["fp"] != got[0]["fp"]


def test_non_retryable_provider_http_error_is_not_a_hermes_defect():
    lines = ["2026-10-30 21:27:08,405 ERROR agent.conversation_loop: Non-retryable client error: Error code: 403 - "
             "{'error': {'message': 'Key limit exceeded (total limit).'}}",
             "2026-10-30 21:27:09,405 ERROR agent.conversation_loop: Non-retryable client error: Provider has been "
             "unresponsive (no response received) for 5 consecutive stale attempts",
             "2026-10-30 21:27:10,405 WARNING agent.conversation_loop: Non-retryable client error: whatever"]
    assert sg.parse_errors(lines) == []
