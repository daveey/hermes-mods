#!/usr/bin/env python3
"""Keep the shared agent Chrome (systemd --user unit, CDP on --port) small on Linux.

Why: agent Chrome collects tabs nobody uses. browser_harness daemons (one per BU_NAME) outlive the Hermes session
that started them, and every switch_tab()/new_tab() attaches a new CDP session without detaching the old one, so
`attached` stays true on every tab a daemon ever touched. The hub unit runs with --disable-renderer-backgrounding, so
each stale tab (29 copies of x.com/home on one hub) keeps running. That hub reached ~18 GB of PSS at ~230% CPU across
83 pages, which pushed the host into "memory pressure elevated" and made the Kanban dispatcher admit one worker per
tick.

    chrome_recycle.py --unit hub-chrome.service --user-data-dir ~/.hermes/chrome-agent --port 9333 [--dry-run]

Each run prints one JSON line and takes these steps (all skipped when <home>/chrome-recycle-hold exists):

1. Find CDP clients: ESTABLISHED TCP connections to --port, mapped to pids. A client whose argv carries
   `browser_harness.daemon` is a daemon. Anything else is a foreign client.
2. Reap stale daemons (unless --no-reap): send {"meta": "shutdown"} over the daemon's IPC socket (the daemon then
   closes its own dedicated tab), SIGTERM if that fails. A named daemon is stale when its owner session
   (HERMES_SESSION_ID in its environment, looked up in <home>/state.db) has ended or has had no message for
   --reap-hours. The shared `default` daemon has no single owner, so it is stale only when its log has not been
   written for --reap-hours.
3. Prune tabs (unless --no-prune): Target.getTargets, then Target.closeTarget on page targets that are not protected
   and are either a blank tab (about:blank, chrome://newtab) or a duplicate of another page's exact URL. One copy of
   each URL is kept, preferring a protected copy. Protected: the current tab of every live daemon (IPC
   {"meta": "current_tab"}), plus every attached tab when a foreign client is connected or a daemon does not answer
   (their sessions are unknown). At least --keep-pages pages always stay open.
4. Restart decision, in order (first match wins):
   cooldown  last recycle was less than --cooldown-hours ago
   below     summed PSS of the Chrome process tree (measured after pruning) is under the threshold
             (--threshold-mb, or --pressure-threshold-mb when MemAvailable < --pressure-avail-mb)
   in_use    a live daemon or a foreign client is still connected, or the targets could not be read. A restart
             would kill a session's tabs, so it is refused, under memory pressure too. Pruning still ran.
   busy      Chrome used more than --idle-cpu-pct of one core over --sample-seconds
   recycle   systemctl --user restart <unit>  (cookies live in the profile dir, so logins survive; tabs do not)
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import signal
import socket
import sqlite3
import struct
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlparse

CLK = os.sysconf("SC_CLK_TCK")
BLANK_URLS = {"about:blank", "chrome://newtab/", "chrome://new-tab-page/"}
PROC = Path("/proc")


# ---------------------------------------------------------------- /proc readers

def _argv_tokens(pid: int, proc: Path = PROC) -> list[bytes]:
    # Chrome rewrites argv into one space-joined string, so split on NUL and spaces.
    return (proc / str(pid) / "cmdline").read_bytes().replace(b"\0", b" ").split(b" ")


def chrome_pids(user_data_dir: str, proc: Path = PROC) -> list[int]:
    needle = f"--user-data-dir={user_data_dir}".encode()
    out = []
    for d in proc.iterdir():
        if d.name.isdigit():
            try:
                if needle in _argv_tokens(int(d.name), proc):
                    out.append(int(d.name))
            except OSError:
                pass
    return out


def pss_mb(pid: int, proc: Path = PROC) -> float:
    try:
        for line in (proc / str(pid) / "smaps_rollup").read_text().splitlines():
            if line.startswith("Pss:"):
                return int(line.split()[1]) / 1024
    except OSError:
        pass
    return 0.0


def cpu_ticks(pid: int, proc: Path = PROC) -> int:
    try:
        rest = (proc / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()
        return int(rest[11]) + int(rest[12])  # utime + stime
    except (OSError, IndexError, ValueError):
        return 0


def mem_available_mb(meminfo: Path = Path("/proc/meminfo")) -> float:
    for line in meminfo.read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) / 1024
    return float("inf")


def cdp_client_pids(port: int, proc: Path = PROC) -> list[int]:
    """Pids holding the client end of an ESTABLISHED TCP connection to `port` (one entry per connection)."""
    inodes = set()
    for name in ("tcp", "tcp6"):
        try:
            lines = (proc / "net" / name).read_text().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            f = line.split()
            if len(f) > 9 and f[3] == "01" and int(f[2].rsplit(":", 1)[1], 16) == port:
                inodes.add(f[9])
    out: list[int] = []
    if not inodes:
        return out
    for d in proc.iterdir():
        if not d.name.isdigit():
            continue
        try:
            fds = list((d / "fd").iterdir())
        except OSError:
            continue
        for fd in fds:
            try:
                link = os.readlink(fd)
            except OSError:
                continue
            if link.startswith("socket:[") and link[8:-1] in inodes:
                out.append(int(d.name))
    return out


# ---------------------------------------------------------------- browser_harness daemons

def _environ(pid: int, proc: Path = PROC) -> dict[str, str]:
    env = {}
    for item in (proc / str(pid) / "environ").read_bytes().split(b"\0"):
        k, sep, v = item.partition(b"=")
        if sep:
            env[k.decode(errors="replace")] = v.decode(errors="replace")
    return env


def daemon_info(pid: int, proc: Path = PROC) -> Optional[dict]:
    """Describe a browser_harness daemon pid (name, owner session, IPC socket, log), or None for any other process."""
    try:
        if b"browser_harness.daemon" not in _argv_tokens(pid, proc):
            return None
        env = _environ(pid, proc)
    except OSError:
        return None
    name = env.get("BU_NAME") or "default"
    # Mirror the file layout of browser_harness/_ipc.py and paths.py.
    if env.get("BH_HOME") or env.get("BROWSER_HARNESS_HOME"):
        bh_home = Path(env.get("BH_HOME") or env["BROWSER_HARNESS_HOME"]).expanduser()
    elif env.get("XDG_CONFIG_HOME"):
        bh_home = Path(env["XDG_CONFIG_HOME"]).expanduser() / "browser-harness"
    else:
        bh_home = Path(env.get("HOME", str(Path.home()))) / ".config" / "browser-harness"
    tmp_env = env.get("BH_TMP_DIR")
    run_env = env.get("BH_RUNTIME_DIR") or tmp_env
    run_dir = Path(run_env) if run_env else bh_home / "runtime"
    tmp_dir = Path(tmp_env) if tmp_env else bh_home / "tmp"
    run_stem = "bu" if run_env and env.get("BH_RUNTIME_DIR_SHARED") != "1" else f"bu-{name}"
    tmp_stem = "bu" if tmp_env and env.get("BH_TMP_DIR_SHARED") != "1" else f"bu-{name}"
    return {"pid": pid, "name": name, "session_id": env.get("HERMES_SESSION_ID") or None,
            "sock": str(run_dir / f"{run_stem}.sock"), "log": str(tmp_dir / f"{tmp_stem}.log")}


def daemon_request(sock_path: str, req: dict, timeout: float = 5) -> dict:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(sock_path)
        s.sendall((json.dumps(req) + "\n").encode())
        data = b""
        while not data.endswith(b"\n"):
            chunk = s.recv(1 << 16)
            if not chunk:
                break
            data += chunk
        out = json.loads(data or b"{}")
        return out if isinstance(out, dict) else {}
    finally:
        s.close()


def session_activity(state_db: Path, session_id: str) -> Optional[tuple[Optional[float], float]]:
    """(ended_at, last activity ts) for a Hermes session in state.db, or None when the session is unknown."""
    try:
        con = sqlite3.connect(f"file:{state_db}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error:
        return None
    try:
        row = con.execute("select ended_at, started_at, (select max(timestamp) from messages m where m.session_id=s.id)"
                          " from sessions s where id=?", (session_id,)).fetchone()
    except sqlite3.Error:
        return None
    finally:
        con.close()
    if not row:
        return None
    ended, started, last_msg = row
    return ended, max(x for x in (started, last_msg, ended) if x is not None)


def daemon_stale(d: dict, *, now: float, reap_hours: float, state_db: Path) -> Optional[str]:
    """Why daemon `d` is stale, or None when it may still be in use."""
    cutoff = now - reap_hours * 3600
    if d["name"] != "default" and d["session_id"]:
        act = session_activity(state_db, d["session_id"])
        if act is not None:
            ended, last = act
            if ended is not None:
                return "owner_session_ended"
            return "owner_session_idle" if last < cutoff else None
    try:  # unknown owner, or the shared default daemon: fall back to its log
        return "log_idle" if os.stat(d["log"]).st_mtime < cutoff else None
    except OSError:
        return None


def _alive(pid: int, proc: Path = PROC) -> bool:
    try:
        return (proc / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, IndexError):
        return False


def reap(d: dict, *, wait: float = 5, proc: Path = PROC) -> str:
    try:
        if daemon_request(d["sock"], {"meta": "shutdown"}).get("ok"):
            for _ in range(int(wait * 10)):
                if not _alive(d["pid"], proc):
                    return "shutdown"
                time.sleep(0.1)
    except (OSError, ValueError):
        pass
    try:
        os.kill(d["pid"], signal.SIGTERM)
        return "sigterm"
    except OSError as e:
        return f"failed: {e}"


# ---------------------------------------------------------------- minimal CDP websocket client (stdlib only)

class CDP:
    def __init__(self, ws_url: str, timeout: float = 30):
        u = urlparse(ws_url)
        self.sock = socket.create_connection((u.hostname, u.port or 80), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET {u.path} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\nUpgrade: websocket\r\n"
                           f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
                          .encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("websocket handshake closed")
            head += chunk
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise ConnectionError(f"websocket handshake refused: {head[:80]!r}")
        self.buf = head.split(b"\r\n\r\n", 1)[1]
        self.next_id = 0

    def _recv_exact(self, n: int) -> bytes:
        while len(self.buf) < n:
            chunk = self.sock.recv(1 << 20)
            if not chunk:
                raise ConnectionError("websocket closed")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def _send(self, text: str) -> None:
        data = text.encode()
        mask = os.urandom(4)
        n = len(data)
        if n < 126:
            hdr = bytes([0x81, 0x80 | n])
        elif n < 65536:
            hdr = bytes([0x81, 0x80 | 126]) + struct.pack(">H", n)
        else:
            hdr = bytes([0x81, 0x80 | 127]) + struct.pack(">Q", n)
        self.sock.sendall(hdr + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def _recv_message(self) -> str:
        parts = []
        while True:
            b0, b1 = self._recv_exact(2)
            n = b1 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._recv_exact(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._recv_exact(8))[0]
            mask = self._recv_exact(4) if b1 & 0x80 else None
            payload = self._recv_exact(n)
            if mask:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            op = b0 & 0x0F
            if op == 8:
                raise ConnectionError("websocket closed by peer")
            if op in (9, 10):  # ping/pong
                continue
            parts.append(payload)
            if b0 & 0x80:
                return b"".join(parts).decode()

    def call(self, method: str, **params) -> dict:
        self.next_id += 1
        mid = self.next_id
        self._send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            msg = json.loads(self._recv_message())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


def browser_ws_url(port: int, host: str = "127.0.0.1") -> str:
    with urllib.request.urlopen(f"http://{host}:{port}/json/version", timeout=10) as r:
        return json.load(r)["webSocketDebuggerUrl"]


# ---------------------------------------------------------------- decisions

def plan_prune(targets: list[dict], protected: set[str], *, trust_attached: bool, keep_pages: int = 1) -> list[dict]:
    """Page targets safe to close: unprotected blanks, and unprotected duplicates of an exact URL (one copy kept)."""
    pages = [t for t in targets if t.get("type") == "page"]

    def is_protected(t: dict) -> bool:
        return t["targetId"] in protected or (trust_attached and bool(t.get("attached")))

    close: list[dict] = []
    by_url: dict[str, list[dict]] = {}
    for t in pages:
        by_url.setdefault(t.get("url", ""), []).append(t)
    for url, group in by_url.items():
        free = [t for t in group if not is_protected(t)]
        if url in BLANK_URLS:
            close += free
        elif len(group) > 1:
            close += free if len(free) < len(group) else free[1:]  # keep a protected copy, else the first
    return close[:max(0, len(pages) - keep_pages)]


def prune(port: int, protected: set[str], *, trust_attached: bool, keep_pages: int, dry_run: bool,
          connect: Callable[[str], CDP] = CDP, unchanged: Callable[[], bool] = lambda: True) -> dict:
    cdp = connect(browser_ws_url(port))
    try:
        targets = cdp.call("Target.getTargets")["targetInfos"]
        pages = [t for t in targets if t.get("type") == "page"]
        doomed = plan_prune(targets, protected, trust_attached=trust_attached, keep_pages=keep_pages)
        closed = 0
        # A session that connected since the survey may be about to drive a fresh about:blank: skip this tick.
        aborted = bool(doomed) and not dry_run and not unchanged()
        if not dry_run and not aborted:
            for t in doomed:
                try:
                    if cdp.call("Target.closeTarget", targetId=t["targetId"]).get("success", True):
                        closed += 1
                except RuntimeError:
                    pass  # the tab went away meanwhile
        return {"pages": len(pages), "protected": len(protected), "trust_attached": trust_attached,
                "prunable": len(doomed), "closed": closed, **({"aborted": "new_cdp_client"} if aborted else {})}
    finally:
        cdp.close()


def decide(args, *, now: float, pids: list[int], total_pss: float, avail_mb: float, cpu_pct: Optional[float],
           last_recycle: float, held: bool, in_use: int = 0) -> tuple[str, dict]:
    pressure = avail_mb < args.pressure_avail_mb
    threshold = args.pressure_threshold_mb if pressure else args.threshold_mb
    info = {"pss_mb": round(total_pss), "procs": len(pids), "avail_mb": round(avail_mb), "threshold_mb": threshold,
            "pressure": pressure, "in_use": in_use}
    if held:
        return "hold", info
    if now - last_recycle < args.cooldown_hours * 3600:
        return "cooldown", info
    if not pids or total_pss < threshold:
        return "below", info
    if in_use:  # some session may be using Chrome: never kill its tabs, memory pressure or not
        return "in_use", info
    info["cpu_pct"] = None if cpu_pct is None else round(cpu_pct, 1)
    if cpu_pct is None or cpu_pct > args.idle_cpu_pct:
        return "busy", info
    return "recycle", info


def survey(port: int, proc: Path = PROC) -> tuple[list[dict], int]:
    """(live daemons connected to Chrome, count of foreign CDP connections)."""
    daemons: dict[int, dict] = {}
    foreign = 0
    for pid in cdp_client_pids(port, proc):
        if pid == os.getpid():
            continue
        d = daemons.get(pid) or daemon_info(pid, proc)
        if d is None:
            foreign += 1
        else:
            daemons[pid] = d
    return list(daemons.values()), foreign


def main(argv=None, *, sleep: Callable[[float], None] = time.sleep, runner: Callable[..., object] = subprocess.run,
         connect: Callable[[str], CDP] = CDP, proc: Path = PROC) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--unit", default="hub-chrome.service")
    ap.add_argument("--user-data-dir", default=str(Path.home() / ".hermes" / "chrome-agent"))
    ap.add_argument("--home", default=str(Path.home() / ".hermes"))
    ap.add_argument("--port", type=int, default=9333)
    ap.add_argument("--keep-pages", type=int, default=1)
    ap.add_argument("--no-prune", action="store_true")
    ap.add_argument("--no-reap", action="store_true")
    ap.add_argument("--reap-hours", type=float, default=6)
    ap.add_argument("--threshold-mb", type=float, default=12000)
    ap.add_argument("--pressure-threshold-mb", type=float, default=3000)
    ap.add_argument("--pressure-avail-mb", type=float, default=8000)
    ap.add_argument("--idle-cpu-pct", type=float, default=25)
    ap.add_argument("--sample-seconds", type=float, default=20)
    ap.add_argument("--cooldown-hours", type=float, default=6)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    home = Path(args.home)
    state = home / "state" / "chrome-recycle.json"
    try:
        last = float(json.loads(state.read_text()).get("last_recycle", 0))
    except (OSError, ValueError):
        last = 0.0
    held = (home / "chrome-recycle-hold").exists()
    now = time.time()

    pids = chrome_pids(args.user_data_dir, proc)
    extra: dict[str, Any] = {"pss_before_mb": round(sum(pss_mb(p, proc) for p in pids))}
    daemons, foreign = survey(args.port, proc)
    in_use = len(daemons) + foreign
    dry_reaped: set[int] = set()  # --dry-run: daemons that would have been reaped count as gone
    if pids and not held:
        reaped = []
        if not args.no_reap:
            for d in daemons:
                why = daemon_stale(d, now=now, reap_hours=args.reap_hours, state_db=home / "state.db")
                if why:
                    how = "dry_run" if args.dry_run else reap(d, proc=proc)
                    reaped.append({"name": d["name"], "pid": d["pid"], "why": why, "how": how})
            if reaped:
                extra["reaped"] = reaped
                if args.dry_run:
                    dry_reaped = {r["pid"] for r in reaped}
                    daemons = [d for d in daemons if d["pid"] not in dry_reaped]
                else:
                    sleep(1)
                    daemons, foreign = survey(args.port, proc)
        protected: set[str] = set()
        unknown = 0
        for d in daemons:
            try:
                resp = daemon_request(d["sock"], {"meta": "current_tab"})
            except (OSError, ValueError):
                resp = {}
            if resp.get("targetId"):
                protected.add(resp["targetId"])
            elif resp.get("error") not in ("not_attached", "cdp_disconnected"):
                # No answer: cannot tell which tab it drives, so every attached tab is protected. (An answer of
                # not_attached / cdp_disconnected means its current tab is gone: it drives none right now.)
                unknown += 1
        in_use = len(daemons) + foreign
        extra["daemons"] = sorted(d["name"] for d in daemons)
        extra["foreign_clients"] = foreign
        if not args.no_prune:
            known = {d["pid"] for d in daemons}

            def unchanged() -> bool:
                d3, f3 = survey(args.port, proc)
                return f3 <= foreign and {d["pid"] for d in d3} <= known

            try:
                pr = prune(args.port, protected, trust_attached=foreign + unknown > 0, keep_pages=args.keep_pages,
                           dry_run=args.dry_run, connect=connect, unchanged=unchanged)
                extra["prune"] = pr
                if pr["closed"]:
                    sleep(3)  # let the renderers exit before re-measuring
                    pids = chrome_pids(args.user_data_dir, proc)
            except (OSError, ValueError, KeyError, RuntimeError) as e:
                extra["prune_error"] = f"{type(e).__name__}: {e}"[:200]
                in_use += 1  # the targets could not be read: do not restart blind
    total = sum(pss_mb(p, proc) for p in pids)
    common: dict[str, Any] = dict(pids=pids, total_pss=total, last_recycle=last, held=held, in_use=in_use)
    verdict, info = decide(args, now=time.time(), avail_mb=mem_available_mb(), cpu_pct=0.0, **common)
    if verdict == "recycle":  # only now pay for the CPU sample
        before = {p: cpu_ticks(p, proc) for p in pids}
        sleep(args.sample_seconds)
        after = {p: cpu_ticks(p, proc) for p in chrome_pids(args.user_data_dir, proc)}
        used = sum(max(0, after[p] - before.get(p, after[p])) for p in after) / CLK
        d2, f2 = survey(args.port, proc)  # a session may have connected during the sample
        common["in_use"] = len([d for d in d2 if d["pid"] not in dry_reaped]) + f2
        verdict, info = decide(args, now=time.time(), avail_mb=mem_available_mb(),
                               cpu_pct=100 * used / args.sample_seconds, **common)
    result = {"verdict": verdict, **info, **extra, "unit": args.unit, "dry_run": args.dry_run}
    if verdict == "recycle" and not args.dry_run:
        r = runner(["systemctl", "--user", "restart", args.unit], capture_output=True, text=True)
        result["rc"] = getattr(r, "returncode", 0)
        if result["rc"] == 0:
            state.parent.mkdir(parents=True, exist_ok=True)
            state.write_text(json.dumps({"last_recycle": time.time(), "pss_mb": round(total)}))
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
