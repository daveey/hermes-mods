"""Tests for chrome_recycle.py. Run: python3 -m pytest -q -p no:cacheprovider tests_chrome_recycle.py

Real processes stand in for everything the guard inspects through /proc:
  * "Chrome": child processes carrying --user-data-dir=<tmp>/profile in argv.
  * CDP: a real HTTP + websocket server on a free loopback port (FakeCDP), speaking Target.getTargets/closeTarget.
  * browser_harness daemons: child processes with `browser_harness.daemon` in argv that hold a TCP connection to the
    fake CDP port and answer current_tab / shutdown on a real AF_UNIX IPC socket.
  * state.db: a real sqlite file with the sessions/messages columns the guard reads.
systemctl is a recording fake.
"""

import base64
import hashlib
import importlib.util
import json
import os
import shutil
import socket
import sqlite3
import struct
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("chrome_recycle", HERE / "chrome_recycle.py")
cr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cr)

SLEEPER = "import time\nwhile True: time.sleep(1)"
SPINNER = "while True: pass"

# A fake browser_harness daemon: connect to CDP, serve the IPC socket, exit on shutdown.
DAEMON = r"""
import json, os, socket, sys
cdp = socket.create_connection(("127.0.0.1", int(os.environ["FAKE_CDP_PORT"])))
cdp.sendall(b"GET /hold HTTP/1.1\r\nHost: x\r\n\r\n")
path = os.path.join(os.environ["BH_RUNTIME_DIR"], "bu.sock")
srv = socket.socket(socket.AF_UNIX); srv.bind(path); srv.listen(4)
open(os.environ["BH_RUNTIME_DIR"] + "/ready", "w").close()
while True:
    c, _ = srv.accept()
    req = json.loads(c.makefile().readline())
    if req.get("meta") == "current_tab":
        tab = os.environ.get("FAKE_TAB")
        c.sendall((json.dumps({"targetId": tab, "url": "x"} if tab else {"error": "not_attached"}) + "\n").encode())
    elif req.get("meta") == "shutdown":
        c.sendall(b'{"ok": true}\n'); c.close(); os.unlink(path); sys.exit(0)
    c.close()
"""

HOLDER = "import socket,sys,time\ns=socket.create_connection(('127.0.0.1',int(sys.argv[1])))\n" \
         "s.sendall(b'GET /hold HTTP/1.1\\r\\n\\r\\n')\ntime.sleep(60)"


class Runner:
    def __init__(self, rc=0):
        self.calls, self.rc = [], rc

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        return subprocess.CompletedProcess(cmd, self.rc, "", "")


class FakeCDP:
    """HTTP /json/version + a websocket endpoint that serves Target.getTargets and Target.closeTarget."""

    def __init__(self, targets):
        self.targets = list(targets)
        self.closed = []
        self.srv = socket.socket()
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(16)
        self.port = self.srv.getsockname()[1]
        self.conns = []
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try:
                c, _ = self.srv.accept()
            except OSError:
                return
            self.conns.append(c)
            threading.Thread(target=self._serve, args=(c,), daemon=True).start()

    def _serve(self, c):
        try:
            self._serve_inner(c)
        except OSError:
            pass

    def _serve_inner(self, c):
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = c.recv(4096)
            if not chunk:
                return
            head += chunk
        line = head.split(b"\r\n", 1)[0].decode()
        if line.startswith("GET /json/version"):
            body = json.dumps({"webSocketDebuggerUrl": f"ws://127.0.0.1:{self.port}/devtools/browser/x"}).encode()
            c.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: %d\r\n\r\n" % len(body)
                      + body)
            c.close()
            return
        if line.startswith("GET /hold"):  # a fake daemon's long-lived connection
            return
        key = [h.split(b":", 1)[1].strip() for h in head.split(b"\r\n") if h.lower().startswith(b"sec-websocket-key")][0]
        acc = base64.b64encode(hashlib.sha1(key + b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11").digest())
        c.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                  b"Sec-WebSocket-Accept: " + acc + b"\r\n\r\n")
        f = c.makefile("rb")
        while True:
            hdr = f.read(2)
            if len(hdr) < 2:
                return
            n = hdr[1] & 0x7F
            if n == 126:
                n = struct.unpack(">H", f.read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", f.read(8))[0]
            mask = f.read(4)
            msg = json.loads(bytes(b ^ mask[i % 4] for i, b in enumerate(f.read(n))))
            if msg["method"] == "Target.getTargets":
                res = {"targetInfos": self.targets}
            elif msg["method"] == "Target.closeTarget":
                tid = msg["params"]["targetId"]
                self.closed.append(tid)
                self.targets = [t for t in self.targets if t["targetId"] != tid]
                res = {"success": True}
            else:
                res = {}
            out = json.dumps({"id": msg["id"], "result": res}).encode()
            # Server frames are unmasked; the 64-bit length form exercises the client's big-frame path.
            c.sendall(bytes([0x81, 127]) + struct.pack(">Q", len(out)) + out)

    def close(self):
        self.srv.close()
        for c in self.conns:
            try:
                c.close()
            except OSError:
                pass


def page(tid, url, attached=True):
    return {"targetId": tid, "type": "page", "url": url, "attached": attached}


@pytest.fixture
def chrome(tmp_path):
    procs = []

    def start(code=SLEEPER):
        p = subprocess.Popen([sys.executable, "-c", code, f"--user-data-dir={tmp_path}/profile"])
        procs.append(p)
        time.sleep(0.3)
        return p

    yield start
    for p in procs:
        p.kill()
        p.wait()


@pytest.fixture
def cdp():
    made = []

    def make(targets=()):
        f = FakeCDP(targets)
        made.append(f)
        return f

    yield make
    for f in made:
        f.close()


@pytest.fixture
def holder():
    procs = []

    def start(port):
        p = subprocess.Popen([sys.executable, "-c", HOLDER, str(port)])
        procs.append(p)
        time.sleep(0.5)
        return p

    yield start
    for p in procs:
        p.kill()
        p.wait()


def make_state_db(home: Path, sessions):
    con = sqlite3.connect(home / "state.db")
    con.execute("create table sessions (id text primary key, started_at real, ended_at real)")
    con.execute("create table messages (session_id text, timestamp real)")
    for sid, started, ended, last_msg in sessions:
        con.execute("insert into sessions values (?,?,?)", (sid, started, ended))
        if last_msg is not None:
            con.execute("insert into messages values (?,?)", (sid, last_msg))
    con.commit()
    con.close()


@pytest.fixture
def daemon():
    procs, dirs = [], []

    def start(port, name, session_id=None, tab=None):
        d = tempfile.mkdtemp(prefix="crg")  # short: AF_UNIX paths are limited to 108 bytes
        dirs.append(d)
        env = {**os.environ, "FAKE_CDP_PORT": str(port), "BU_NAME": name, "BH_RUNTIME_DIR": d, "BH_TMP_DIR": d}
        env.pop("HERMES_SESSION_ID", None)
        if session_id:
            env["HERMES_SESSION_ID"] = session_id
        if tab:
            env["FAKE_TAB"] = tab
        p = subprocess.Popen([sys.executable, "-c", DAEMON, "-m", "browser_harness.daemon"], env=env)
        procs.append(p)
        for _ in range(50):
            if os.path.exists(f"{d}/ready"):
                break
            time.sleep(0.1)
        time.sleep(0.2)
        return p

    yield start
    for p in procs:
        p.kill()
        p.wait()
    for d in dirs:
        shutil.rmtree(d, ignore_errors=True)


def run(tmp_path, port, *flags, runner=None):
    runner = runner or Runner()
    rc = cr.main(["--home", str(tmp_path), "--user-data-dir", f"{tmp_path}/profile", "--port", str(port),
                  "--sample-seconds", "1", *flags], runner=runner, sleep=lambda s: time.sleep(min(s, 1)))
    assert rc == 0
    return runner


def verdict(capsys):
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


# ---------------------------------------------------------------- process discovery

def test_pids_match_user_data_dir_even_when_argv_is_one_space_joined_string(tmp_path, chrome):
    a = chrome()
    b = subprocess.Popen([sys.executable, "-c", SLEEPER, f"x --user-data-dir={tmp_path}/profile y"])
    other = subprocess.Popen([sys.executable, "-c", SLEEPER, "--user-data-dir=/elsewhere"])
    try:
        time.sleep(0.3)
        pids = cr.chrome_pids(f"{tmp_path}/profile")
        assert a.pid in pids and b.pid in pids and other.pid not in pids
    finally:
        b.kill(), other.kill()


def test_survey_maps_cdp_connections_to_daemons_and_foreign_clients(cdp, daemon, holder):
    f = cdp()
    d = daemon(f.port, "alpha", session_id="s1", tab="T1")
    holder(f.port)
    daemons, n_foreign = cr.survey(f.port)
    assert [(x["pid"], x["name"], x["session_id"]) for x in daemons] == [(d.pid, "alpha", "s1")]
    assert n_foreign == 1
    assert cr.daemon_request(daemons[0]["sock"], {"meta": "current_tab"})["targetId"] == "T1"


# ---------------------------------------------------------------- tab pruning

def ids(ts):
    return sorted(x["targetId"] for x in ts)


def test_plan_prune_keeps_protected_and_one_copy_and_min_pages():
    t = [page("A1", "https://x.com/home"), page("A2", "https://x.com/home"), page("A3", "https://x.com/home"),
         page("B1", "about:blank"), page("C1", "https://unique.example/"), page("D1", "about:blank")]
    # A2 is a daemon's current tab: it is the copy kept; both blanks go; the unique page stays.
    assert ids(cr.plan_prune(t, {"A2"}, trust_attached=False)) == ["A1", "A3", "B1", "D1"]
    # Nothing protected: the first copy is kept.
    assert ids(cr.plan_prune(t, set(), trust_attached=False)) == ["A2", "A3", "B1", "D1"]
    # Unknown clients: every attached tab is protected; only unattached ones may go.
    t2 = t + [page("A4", "https://x.com/home", attached=False), page("E1", "about:blank", attached=False)]
    assert ids(cr.plan_prune(t2, set(), trust_attached=True)) == ["A4", "E1"]
    # Never close the last pages.
    assert cr.plan_prune([page("B1", "about:blank")], set(), trust_attached=False) == []
    assert len(cr.plan_prune(t, set(), trust_attached=False, keep_pages=5)) == 1


def test_prune_closes_stale_tabs_over_cdp_but_not_the_tab_a_live_session_drives(tmp_path, chrome, cdp, daemon,
                                                                                    capsys):
    chrome()
    now = time.time()
    make_state_db(tmp_path, [("live", now - 600, None, now - 60)])
    f = cdp([page("A1", "https://x.com/home"), page("A2", "https://x.com/home"), page("A3", "https://x.com/home"),
             page("B1", "about:blank"), page("C1", "https://unique.example/"),
             {"targetId": "W1", "type": "service_worker", "url": "https://x.com/sw.js", "attached": False}])
    d = daemon(f.port, "live-session", session_id="live", tab="A3")
    r = run(tmp_path, f.port, "--threshold-mb", "999999")
    v = verdict(capsys)
    assert sorted(f.closed) == ["A1", "A2", "B1"]
    assert v["prune"]["closed"] == 3 and v["daemons"] == ["live-session"] and v["verdict"] == "below"
    assert d.poll() is None and r.calls == []  # the live session's daemon was left running


def test_foreign_client_protects_every_attached_tab_and_blocks_restart(tmp_path, chrome, cdp, holder, capsys):
    chrome()
    f = cdp([page("A1", "https://x.com/home"), page("A2", "https://x.com/home"),
             page("A3", "https://x.com/home", attached=False)])
    holder(f.port)
    r = run(tmp_path, f.port, "--pressure-avail-mb", "1e12", "--pressure-threshold-mb", "0")
    v = verdict(capsys)
    assert f.closed == ["A3"] and v["foreign_clients"] == 1
    assert v["verdict"] == "in_use" and r.calls == []


def test_prune_is_skipped_when_a_new_client_connects_mid_run(cdp):
    f = cdp([page("B1", "about:blank"), page("C1", "https://c/")])
    pr = cr.prune(f.port, set(), trust_attached=False, keep_pages=1, dry_run=False, unchanged=lambda: False)
    assert pr["aborted"] == "new_cdp_client" and pr["closed"] == 0 and f.closed == []


# ---------------------------------------------------------------- daemon reaping and the in-use refusal

def test_in_use_daemon_refuses_restart_even_under_memory_pressure(tmp_path, chrome, cdp, daemon, capsys):
    chrome()
    now = time.time()
    make_state_db(tmp_path, [("live", now - 600, None, now - 60)])
    f = cdp([page("A1", "https://example.com/")])
    d = daemon(f.port, "busy-session", session_id="live", tab="A1")
    r = run(tmp_path, f.port, "--pressure-avail-mb", "1e12", "--pressure-threshold-mb", "0")
    v = verdict(capsys)
    assert v["verdict"] == "in_use" and v["pressure"] is True and v["in_use"] == 1
    assert r.calls == [] and d.poll() is None and "reaped" not in v and f.closed == []


def test_daemons_of_ended_or_idle_sessions_are_reaped_then_restart_is_allowed(tmp_path, chrome, cdp, daemon, capsys):
    chrome()
    now = time.time()
    make_state_db(tmp_path, [("ended", now - 9000, now - 8000, now - 8000),
                             ("idle", now - 90000, None, now - 80000)])
    f = cdp([page("A1", "https://example.com/")])
    d1 = daemon(f.port, "gone", session_id="ended", tab="A1")
    d2 = daemon(f.port, "idle", session_id="idle")
    r = run(tmp_path, f.port, "--threshold-mb", "0", "--pressure-avail-mb", "0", "--reap-hours", "6")
    v = verdict(capsys)
    assert sorted((x["name"], x["why"], x["how"]) for x in v["reaped"]) == [
        ("gone", "owner_session_ended", "shutdown"), ("idle", "owner_session_idle", "shutdown")]
    assert d1.wait(5) == 0 and d2.wait(5) == 0
    assert v["daemons"] == [] and v["verdict"] == "recycle"
    assert r.calls == [["systemctl", "--user", "restart", "hub-chrome.service"]]


def test_shared_default_daemon_is_stale_only_when_its_log_is_idle(cdp, daemon):
    f = cdp()
    p = daemon(f.port, "default", session_id="whoever-spawned-it")
    d = cr.survey(f.port)[0][0]
    assert d["name"] == "default"
    Path(d["log"]).write_text("attached ...\n")
    assert cr.daemon_stale(d, now=time.time(), reap_hours=6, state_db=Path("/nonexistent")) is None
    old = time.time() - 7 * 3600
    os.utime(d["log"], (old, old))
    assert cr.daemon_stale(d, now=time.time(), reap_hours=6, state_db=Path("/nonexistent")) == "log_idle"
    assert p.poll() is None


def test_unreachable_cdp_refuses_restart(tmp_path, chrome, capsys):
    chrome()
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()  # nothing listens here
    r = run(tmp_path, port, "--pressure-avail-mb", "1e12", "--pressure-threshold-mb", "0")
    v = verdict(capsys)
    assert "prune_error" in v and v["verdict"] == "in_use" and r.calls == []


# ---------------------------------------------------------------- restart decision (no clients connected)

def test_below_threshold_does_not_restart(tmp_path, chrome, cdp, capsys):
    chrome()
    r = run(tmp_path, cdp([page("A", "https://a/")]).port, "--threshold-mb", "999999", "--pressure-avail-mb", "0")
    assert verdict(capsys)["verdict"] == "below" and r.calls == []


def test_idle_over_threshold_restarts_and_records_cooldown(tmp_path, chrome, cdp, capsys):
    chrome()
    port = cdp([page("A", "https://a/")]).port
    r = run(tmp_path, port, "--threshold-mb", "0", "--pressure-avail-mb", "0")
    v = verdict(capsys)
    assert v["verdict"] == "recycle" and v["pss_mb"] > 0
    assert r.calls == [["systemctl", "--user", "restart", "hub-chrome.service"]]
    assert json.loads((tmp_path / "state" / "chrome-recycle.json").read_text())["last_recycle"] > 0
    r2 = run(tmp_path, port, "--threshold-mb", "0", "--pressure-avail-mb", "0")
    assert verdict(capsys)["verdict"] == "cooldown" and r2.calls == []


def test_busy_chrome_is_left_alone_even_under_pressure(tmp_path, chrome, cdp, capsys):
    chrome(SPINNER)
    r = run(tmp_path, cdp([page("A", "https://a/")]).port, "--pressure-avail-mb", "1e12",
            "--pressure-threshold-mb", "0", "--idle-cpu-pct", "25")
    v = verdict(capsys)
    assert v["verdict"] == "busy" and v["cpu_pct"] > 25 and r.calls == []


def test_hold_file_blocks_reap_prune_and_restart(tmp_path, chrome, cdp, daemon, capsys):
    chrome()
    now = time.time()
    make_state_db(tmp_path, [("ended", now - 9000, now - 8000, None)])
    f = cdp([page("A1", "about:blank"), page("A2", "about:blank")])
    d = daemon(f.port, "gone", session_id="ended")
    (tmp_path / "chrome-recycle-hold").write_text("")
    r = run(tmp_path, f.port, "--pressure-avail-mb", "1e12", "--pressure-threshold-mb", "0")
    assert verdict(capsys)["verdict"] == "hold" and r.calls == [] and f.closed == [] and d.poll() is None


def test_dry_run_changes_nothing_and_failed_restart_does_not_record_cooldown(tmp_path, chrome, cdp, daemon, capsys):
    chrome()
    now = time.time()
    make_state_db(tmp_path, [("ended", now - 9000, now - 8000, None)])
    f = cdp([page("A1", "about:blank"), page("A2", "about:blank")])
    d = daemon(f.port, "gone", session_id="ended")
    r = run(tmp_path, f.port, "--threshold-mb", "0", "--pressure-avail-mb", "0", "--dry-run")
    v = verdict(capsys)
    assert v["verdict"] == "recycle" and v["prune"]["prunable"] == 1 and v["reaped"][0]["how"] == "dry_run"
    assert r.calls == [] and f.closed == [] and d.poll() is None
    d.kill()
    d.wait()
    time.sleep(0.3)
    run(tmp_path, f.port, "--threshold-mb", "0", "--pressure-avail-mb", "0", runner=Runner(rc=1))
    assert verdict(capsys)["rc"] == 1
    assert not (tmp_path / "state" / "chrome-recycle.json").exists()


def test_session_activity_falls_back_to_query_only_when_ro_open_fails(tmp_path, monkeypatch):
    db = tmp_path / "state.db"
    c = sqlite3.connect(db)
    c.execute("create table sessions(id, started_at, ended_at)")
    c.execute("create table messages(session_id, timestamp)")
    c.execute("insert into sessions values ('s1', 10.0, null)")
    c.execute("insert into messages values ('s1', 20.0)")
    c.commit(); c.close()
    real = sqlite3.connect

    def connect(target, *a, **kw):
        if "mode=ro" in str(target):
            raise sqlite3.OperationalError("unable to open database file")
        return real(target, *a, **kw)
    monkeypatch.setattr(sqlite3, "connect", connect)
    assert cr.session_activity(db, "s1") == (None, 20.0)
    con = cr._ro_connect(db)
    try:
        with pytest.raises(sqlite3.OperationalError):
            con.execute("insert into messages values ('s1', 1)")
    finally:
        con.close()
    assert cr.session_activity(tmp_path / "missing.db", "s1") is None
    assert not (tmp_path / "missing.db").exists()
