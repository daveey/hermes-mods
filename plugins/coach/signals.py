"""signals: the self-heal lane's machine-signal sweeper. No agent, no LLM, stdlib only, Python 3.9+ (the Macs run it
under /usr/bin/python3).

It turns raw machine signals into `self_heal.file_defect` calls with stable fingerprints:
- errors      new tracebacks in <home>/logs/errors.log (any level), minus tool output and provider transients;
              a provider 401/403 logged as "Streaming failed before delivery" (core's retry loop refreshes or
              rotates) only as a storm: 3+ in 10 min per host, fingerprinted by status + reason class
              (revoked/expired/invalid/other); the e0a5909348 credential-guard warnings (no traceback) always;
- hook        plugin hook exceptions (`Hook '<name>' callback <cb> raised: ...` from hermes_cli/plugins_dispatch);
- worker      managed kanban worker crashes in <home>/logs/managed-worker.log (`[pid N] ` lines; newer core also
              writes one `[pid N] @ <UTC> core=<sha>` header per worker) only
              as a storm: 3+ distinct pids dying with the same exception + normalized message within 30 min per
              host (state in the cursor; `at` = latest logged crash time when every pid has a header, else the
              pass time), filed once per fingerprint per 30 min;
- cron        a cron job whose sticky `last_failure.at` is newer than the cursor (cron/jobs.json), filed only while
              failure_streak >= cron.failure_nudge_threshold (default 3) or after that many recovered failures in
              24 h (cursor `cron_fails`); a one-off failure the next run recovered from is only counted;
- kanban      a card with >= 2 runs that crashed / timed out / failed to spawn (every board db); and the dispatcher's
              systemic breaker holding cards in triage (`gave_up` events with `sticky: true`, event-id cursor),
              one signal per board + normalized error head per 30 min;
- gateway     gateway crash-restarts: `gateway.previous_unclean_exit`, `asyncio.run.exception`,
              `gateway.exit_nonzero` in <home>/logs/gateway-exit-diag.log (fingerprinted by its `cause` +
              reason class; `cause: unrecorded` = an exit path with no cause note); a `gateway.exit_signal`
              (unplanned SIGTERM bounce) or `gateway.start_refused` (second start refused next to a live
              gateway) only as a storm: 3+ in 10 min per profile; a refusal by a dead owner pid always;
- coach       coach `fixloop_card` (2nd confirmed failure of a fix) and reply_check `reply_nudge` events (any
              rule) whose `reply_nudge_outcome` says the revised reply still fails (or none came within 15 min);
              a failed revision that is in no assistant row of its session (a detached fork's text the user never
              saw) files as a coach bug (`judged_unsent_draft`), not an agent defect.
Profiles (<home>/profiles/*) are swept too, except the coach log, which lives in <home>/coach only.

Every collector reads from a cursor (<home>/coach/signals-cursor.json: byte offsets + inode per log, last seen
run id per board, last failure time per cron job) so each signal is seen once. A first run (no cursor) looks back
BACKFILL_S (7 days). A truncated or rotated log (inode change or size < offset) is read from the start.

Commands:
  sweep [--mode shadow|live]   hub: collect local signals + drain the forwarded spool, file each one (hub cron)
  collect [--dry-run]          print new raw signals as JSONL (advances the cursor unless --dry-run)
  forward [--hub user@addr]    Mac/mini: collect and ship to the hub's `ingest` over ssh; cursor advances on success
  ingest                       hub side of forward: append JSONL from stdin to <coach>/signals/spool.jsonl
  route-monitor                file one monitor finding (JSON on stdin) and print whether the lane owns it now

The fingerprint never contains the host for code defects (one traceback on two hosts is one defect); it does for
per-host objects (a cron job, a card, a gateway), whose names are only unique per host.
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple

BACKFILL_S = 7 * 86400
HOLD_BACKFILL_S = 86400  # first sight of a board: sticky holds older than this were already handled (kanban-754a3e3cf1)
MAX_READ = 4 * 1024 * 1024        # never read more than this from one log per pass (start at the tail instead)
MAX_PER_KIND = 25                 # cap per collector per pass: a log storm files a handful, not hundreds
FAIL_OUTCOMES = ("crashed", "timed_out", "spawn_failed")
GATEWAY_TAGS = ("gateway.previous_unclean_exit", "asyncio.run.exception", "gateway.exit_nonzero", "gateway.exit_signal",
                "gateway.start_refused")
# gateway.exit_signal = an unplanned stop signal (bootout, kickstart -k, systemctl stop): exit 1 on purpose
# so the supervisor revives it. gateway.start_refused = a second start refused because another gateway owns
# the home/host (already_running / pid_claim / host_refused). One of either is a bounce, not a defect; only a
# storm is filed, plus a refusal by an owner pid that is not running (a stale record blocking every start).
SIGNAL_STORM_N, SIGNAL_STORM_S = 3, 600
KINDS = ("errors", "hook", "worker", "cron", "kanban", "gateway", "coach", "monitor")
SELF_DIR = Path(__file__).resolve().parent
HUB_CMD = "python3 ~/code/hermes-mods/plugins/coach/signals.py ingest"

REC_RE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)(?:,\d+)? (DEBUG|INFO|WARNING|ERROR|CRITICAL) "
                    r"(?:\[([^\]]+)\] )?([\w.\-]+): ?(.*)$")
FRAME_RE = re.compile(r'^\s*File "([^"]+)", line \d+, in (\S+)')
EXC_RE = re.compile(r"^([A-Za-z_][\w.]*(?:Error|Exception|Exit|Interrupt|Failure|Timeout|Warning|Expired|Refused|"
                    r"Closed|Missing|Invalid|Denied|NotFound)\w*|[A-Za-z_][\w.]*\.[A-Z]\w+)(?::\s*(.*))?$")
HOOK_RE = re.compile(r"^(Hook|Event) '([\w.]+)' callback (\S+) raised: (.*?)(?: \(\w+ provides: .*)?$")
# Tool output (a traceback the agent's own command printed) and provider-side transients are not Hermes defects.
SKIP_LOGGERS = re.compile(r"^(agent\.tool_executor|tools\.terminal_tool|tools\.code_execution)")
TRANSIENT_RE = re.compile(r"RateLimitError|rate_limit_error|\b429\b|overloaded_error|\b529\b|APIConnectionError|"
                          r"APITimeoutError|ReadTimeout|ConnectTimeout|RemoteProtocolError|CancelledError|"
                          r"KeyboardInterrupt|InternalServerError.*\b5\d\d\b", re.I)
# A provider status error whose body is a proxy/CDN HTML page (Cloudflare's "502 Bad Gateway" in front of
# api.anthropic.com): the SDK uses the raw body as the message, so the exception line reads `...ServerError: <html>`
# and the status sits on the body's later lines (agent-ee16fb8bb9). Only provider exception types get this
# multi-line look, so our own exception whose message happens to mention 502 still files.
PROVIDER_STATUS_EXC_RE = re.compile(r"(?:^|\.)(?:\w*ServerError|APIStatusError)$")
PROXY_5XX_RE = re.compile(r"<title>\s*5\d\d\b|\b5\d\d\s+(?:Bad Gateway|Service|Gateway|Internal Server Error)|"
                          r"Bad Gateway|Service (?:Temporarily )?Unavailable|Gateway Time-?out|"
                          r"Error code: 5\d\d\b|\bstatus(?:_code)?[\s:=]+5\d\d\b", re.I)
EXC_BODY_LINES, EXC_BODY_CHARS = 20, 2000


def _exc_body(block: List[str], exc_line: str) -> str:
    """The exception message's continuation lines after `exc_line` in its traceback block (an HTML error page,
    a multi-line provider message), capped at EXC_BODY_LINES lines / EXC_BODY_CHARS chars."""
    if not exc_line or exc_line not in block:
        return ""
    i = len(block) - 1 - block[::-1].index(exc_line)
    return "\n".join(block[i + 1:i + 1 + EXC_BODY_LINES])[:EXC_BODY_CHARS]


def provider_5xx(exc_type: str, exc_line: str, body: str) -> bool:
    """A provider status error (by exception type) whose message, including its continuation lines, carries a
    5xx status or a proxy 5xx page marker. Transient: core retries it; a real outage files via retry-exhausted."""
    if not PROVIDER_STATUS_EXC_RE.search(exc_type or ""):
        return False
    text = (exc_line or "") + "\n" + (body or "")
    return bool(PROXY_5XX_RE.search(text) or TRANSIENT_RE.search(text)
                or re.search(r"InternalServerError[\s\S]{0,600}?\b5\d\d\b", text))


PLUGIN_PATH_RE = re.compile(r"/plugins/([\w.\-]+)/")
# CPython refusing new work while the process exits (Desktop quit, update, restart mid-turn): the turn's thread
# outlived the main thread. Exit noise, not a defect (agent-9a2a3cc05d); core logs it as one line since c5153adda1.
EXIT_NOISE_RE = re.compile(r"cannot schedule new futures after|can't register atexit after shutdown", re.I)
# A network library that logs its own auto-reconnect at ERROR with the traceback, then reconnects by itself
# (discord.py client.py:769-781: "sometimes, discord sends us 1000 for unknown reasons so we should reconnect").
# Not a defect unless a Hermes logger or frame is involved; a storm of them on one host/profile still files one.
RECONNECT_RE = re.compile(r"^Attempting a reconnect in \d")
OWN_LOGGER_RE = re.compile(r"^(hermes|gateway|agent|tools|cron|tui_gateway|plugins|run_agent|kanban|coach|acp)")
# Provider 401/403 raised while opening a stream: core logs the traceback ("Streaming failed before delivery",
# chat_completion_helpers) BEFORE its retry loop refreshes or rotates the credential, so one that recovered looks
# like one that killed the turn (agent-101f72db15). Not filed per occurrence; AUTH_STORM_N within AUTH_STORM_S on
# one host files one "auth-failure storm". 429 stays a TRANSIENT_RE skip.
STREAM_FAIL_RE = re.compile(r"^Streaming failed before delivery\b")
AUTH_EXC_RE = re.compile(r"(?:^|\.)(AuthenticationError|PermissionDeniedError)$")
STATUS_RE = re.compile(r"Error code: (\d{3})\b")
PROVIDER_MSG_RE = re.compile(r"""['"]message['"]:\s*['"]([^'"]*)['"]""")
AUTH_STORM_N, AUTH_STORM_S = 3, 600
# The guard warnings core commit e0a5909348 added around 401 recovery. They carry no traceback, so the traceback
# path never saw them; "persists after N refreshes" and "moved off pool entry" are the detection-level signal that
# the pinned-account 401 loop came back. Pool entry ids are left out of the fingerprint (one defect, any entry).
GUARD_RES = (
    ("refresh-persists", re.compile(r"^Credential auth failure persists after \d+ refreshes for pool entry \S+")),
    ("moved-off-entry", re.compile(r"^(?:(\w+) )?pre-request credential check moved the session off pool entry \S+",
                                   re.I)),
    ("key-owner-mismatch", re.compile(r"^Credential auth failure: the failing key belongs to pool entry \S+, but the "
                                      r"session is attributed to entry \S+")),
)
# A platform adapter's "[X] Failed to connect to X" whose final exception says the host is unreachable (laptop
# asleep/offline, DNS down, Wi-Fi switch): the adapter marks it retryable and the gateway retries with backoff, so
# one attempt is not a defect (plugin-0561bb7919). A real outage is the gateway's own NEEDS_ATTENTION warning after
# agent.reconnect_attention_after of continuous failure (run_adapters._flag_reconnect_needs_attention), filed once
# per host/profile/platform. Certificate/auth/other exceptions on connect still file.
CONNECT_FAIL_RE = re.compile(r"^\[[^\]]+\] Failed to connect to \S")
# the final exception, by type alone (exact class names: ClientConnectorCertificateError/SSL errors do not match)
NETWORK_EXC_RE = re.compile(r"^(?:[\w.]*\.)?(?:ClientConnectorDNSError|gaierror|ConnectionRefusedError|"
                            r"ConnectionResetError|ClientConnectionResetError):")
# ... or a generic connect/OS error whose text names a can't-reach-the-host condition
NETWORK_RE = re.compile(r"^(?:[\w.]*\.)?(?:ClientConnectorError|OSError|ConnectionError):.*(?:"
                        r"nodename nor servname provided|Name or service not known|"
                        r"Temporary failure in name resolution|Network is unreachable|No route to host|"
                        r"Connection refused|Connection reset by peer)", re.I)
ATTENTION_RE = re.compile(r"^(\S+) has been failing/reconnecting continuously for ([\d.]+) hours \((\d+) attempts\)"
                          r".*NEEDS_ATTENTION")
RECONNECT_STORM = 20               # more than this many library reconnects in RECONNECT_WINDOW_S files one defect
RECONNECT_WINDOW_S = 3600


# ── small helpers ─────────────────────────────────────────────────────────────────────────────────────────────

def h10(*parts) -> str:
    return hashlib.sha1("\x1f".join(str(p) for p in parts).encode()).hexdigest()[:10]


def fp(component: str, *parts) -> str:
    return f"{re.sub(r'[^a-z]', '', component.lower()) or 'other'}-{h10(component, *parts)}"


REASON_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{2,40}$")


def _reason_code(exc_line: str) -> tuple:
    """A wrapper exception whose whole message is one identifier-like token (`WorkerPersistenceError:
    owner_unavailable`) carries its cause in that token, so it joins the fingerprint. Free-text messages stay out
    (ids and paths vary per occurrence). -> (code,) or ()."""
    m = EXC_RE.match(exc_line or "")
    msg = ((m.group(2) if m else "") or "").strip()
    return (msg,) if REASON_CODE_RE.match(msg) else ()


def home_tilde(s: str) -> str:
    return re.sub(r"/(?:Users|home)/[^/\s\"']+", "~", s or "")


def _ts(s: str) -> float:
    try:
        return time.mktime(time.strptime(s, "%Y-%m-%d %H:%M:%S"))
    except Exception:
        return 0.0


def _iso_ts(s) -> float:
    if isinstance(s, (int, float)):
        return float(s)
    try:
        from datetime import datetime
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def homes(home: Path) -> List[Tuple[str, Path]]:
    """(profile, home) for the default home and every profile under it."""
    out = [("default", home)]
    for p in sorted(glob.glob(str(home / "profiles" / "*"))):
        if Path(p).is_dir():
            out.append((Path(p).name, Path(p)))
    return out


def read_new(path: Path, cur: dict, key: str, now: float, span: Optional[list] = None) -> List[str]:
    """New complete lines of `path` since cur[key] = {"ino", "off"}; updates cur. First sight: the last MAX_READ.
    `span`, if given, receives the [start, end) byte range consumed."""
    try:
        st = path.stat()
    except OSError:
        return []
    c = cur.get(key) or {}
    off = int(c.get("off") or 0)
    if c.get("ino") != st.st_ino or st.st_size < off:
        off = 0
    if not c:  # first sight: read the tail only
        off = max(0, st.st_size - MAX_READ)
    if st.st_size - off > MAX_READ:
        off = st.st_size - MAX_READ
    with path.open("rb") as f:
        f.seek(off)
        data = f.read(st.st_size - off)
    end = data.rfind(b"\n") + 1  # leave a half-written last line for the next pass
    cur[key] = {"ino": st.st_ino, "off": off + end}
    if span is not None:
        span[:] = [off, off + end]
    lines = data[:end].decode("utf-8", "replace").splitlines()
    if off and lines and not c:  # started mid-file: the first line may be partial
        lines = lines[1:]
    return lines


def seed_only_first_sight(path: Path, cur: dict, key: str) -> bool:
    """For logs whose lines carry no timestamp: on first sight (no cursor for `key`) set the cursor at the end of
    `path` and return True, so the caller files nothing. A backlog read on first sight (new host, new profile,
    cursor reset) cannot be told from new lines and would replay old events as live (kanban-e001f2d4a0). A log
    that does not exist yet is seeded at offset 0 (ino None), so everything later written to it counts as new."""
    if cur.get(key):
        return False
    try:
        st = path.stat()
        cur[key] = {"ino": st.st_ino, "off": st.st_size}
    except OSError:
        cur[key] = {"ino": None, "off": 0}
    return True


# ── collectors: each returns raw signals {kind, key, component, symptom, details, at, session_id?, quote?, fp} ─

def _own_frame(path: str) -> bool:
    q = path.replace("\\", "/")
    return "site-packages" not in q and "/lib/python" not in q


def component_of(paths: List[str]) -> Tuple[str, str]:
    """(component, plugin name) from traceback frame paths, innermost first."""
    for p in reversed(paths):
        m = PLUGIN_PATH_RE.search(p)
        if m:
            return "plugin", m.group(1)
    for p in reversed(paths):
        q = p.replace("\\", "/")
        if "site-packages" in q or "/lib/python" in q:
            continue
        for needle, comp in (("/gateway/", "gateway"), ("/cron/", "cron"), ("kanban", "kanban"),
                             ("/tui_gateway/", "desktop"), ("/apps/desktop/", "desktop"), ("/hermes_cli/", "agent"),
                             ("/agent/", "agent"), ("/tools/", "agent")):
            if needle in q:
                return comp, ""
    return "agent", ""


def auth_reason(text: str) -> str:
    """The provider's 401/403 reason class: revoked / expired / invalid / other (from the error body's message)."""
    m = PROVIDER_MSG_RE.search(text or "")
    t = (m.group(1) if m else text or "").lower()
    for word in ("revoked", "expired", "invalid"):
        if word in t:
            return word
    return "other"


def _provider_of(exc_type: str, msg: str = "") -> str:
    if "." in exc_type:
        return exc_type.split(".")[0].lower()
    m = re.search(r"\b(anthropic|openai|xai|gemini|google|openrouter)\b", msg or "", re.I)
    return m.group(1).lower() if m else "unknown"


def parse_errors(lines: List[str], reconnects: Optional[list] = None, auth: Optional[list] = None,
                 host: str = "", prof: str = "default") -> List[dict]:
    """Records (header + continuation lines) -> traceback, hook-exception and credential-guard signals. Library
    auto-reconnects are not signals; each is appended to `reconnects` (when given) for the storm count. Provider
    401/403s raised while opening a stream are handled by core's retry loop: not signals either, each is appended
    to `auth` (when given) for the auth-failure storm count."""
    recs, cur = [], None
    for ln in lines:
        m = REC_RE.match(ln)
        if m:
            cur = {"at": _ts(m.group(1)), "level": m.group(2), "sid": m.group(3) or "", "logger": m.group(4),
                   "msg": m.group(5), "cont": []}
            recs.append(cur)
        elif cur is not None:
            cur["cont"].append(ln)
    out = []
    for r in recs:
        if SKIP_LOGGERS.match(r["logger"]):
            continue
        hm = HOOK_RE.match(r["msg"])
        if hm:
            surface, hook, cb, err = hm.groups()
            exc = err.split(":")[0][:60]
            plug = cb.split(".")[0]
            out.append({"kind": "hook", "component": "plugin", "at": r["at"], "session_id": r["sid"],
                        "symptom": f"plugin hook {hook} callback {cb} raised {err[:120]}",
                        "details": home_tilde(r["msg"])[:1500],
                        "fp": fp("plugin", "hook", hook, cb, re.sub(r"\d+", "N", exc))})
            continue
        guard = next(((k, m) for k, rx in GUARD_RES for m in [rx.search(r["msg"])] if m), None)
        if guard:
            gkind, gm = guard
            prov = ((gm.group(1) or "").lower() if gkind == "moved-off-entry" and gm.group(1)
                    else _provider_of("", r["msg"]))
            out.append({"kind": "errors", "component": "agent", "at": r["at"], "session_id": r["sid"],
                        "symptom": f"credential guard {gkind} ({prov}): {r['msg'][:160]}",
                        "details": home_tilde("\n".join([f"{r['level']} {r['logger']}: {r['msg']}"]
                                                        + r["cont"]))[:1500],
                        "fp": fp("agent", "credential-guard", prov, gkind)})
            continue
        am = ATTENTION_RE.match(r["msg"])
        if am:
            key, hours, attempts = am.groups()
            out.append({"kind": "errors", "component": "gateway", "at": r["at"], "session_id": "",
                        "symptom": f"{key} NEEDS_ATTENTION on {host or 'this host'} ({prof}): failing/reconnecting "
                                   f"continuously for {hours} hours ({attempts} attempts)",
                        "details": home_tilde(f"{r['level']} {r['logger']}: {r['msg']}")[:1500],
                        "fp": fp("gateway", "needs-attention", host, prof, key)})
            continue
        if not any(l.startswith("Traceback (most recent call last)") for l in r["cont"]):
            continue
        # the last chained exception block
        start = max(i for i, l in enumerate(r["cont"]) if l.startswith("Traceback (most recent call last)"))
        block = r["cont"][start:]
        frames = [FRAME_RE.match(l).groups() for l in block if FRAME_RE.match(l)]
        exc_line = next((l for l in reversed(block) if l and not l[0].isspace() and EXC_RE.match(l)), "")
        exc_type = (EXC_RE.match(exc_line).group(1) if exc_line else "") or "Exception"
        text = r["msg"] + "\n" + exc_line
        if (RECONNECT_RE.match(r["msg"]) and not OWN_LOGGER_RE.match(r["logger"])
                and not any(_own_frame(p) for p, _fn in frames)):
            if reconnects is not None:  # the library reconnects by itself; only a storm of these is ours to file
                reconnects.append({"at": r["at"], "logger": r["logger"],
                                   "details": home_tilde("\n".join([f"{r['level']} {r['logger']}: {r['msg']}"]
                                                                   + block))[-1500:]})
            continue
        if (TRANSIENT_RE.search(text) or EXIT_NOISE_RE.search(exc_line)
                or provider_5xx(exc_type, exc_line, _exc_body(block, exc_line))):
            continue
        last = exc_line or next((l for l in reversed(block) if l and not l[0].isspace()), "")  # socket.gaierror
        if CONNECT_FAIL_RE.match(r["msg"]) and (NETWORK_EXC_RE.match(last) or NETWORK_RE.match(last)):
            continue  # a retried platform connect with no network; a long outage files via NEEDS_ATTENTION
        comp, plug = component_of([f[0] for f in frames])
        if r["level"] == "WARNING" and not any(_own_frame(p) for p, _fn in frames):
            continue  # a library logging a handled exception (e.g. mcp's PermissionError); nothing of ours to fix
        inner = [(os.path.basename(p), fn) for p, fn in frames[-4:]]
        where = f"{inner[-1][1]} ({inner[-1][0]})" if inner else r["logger"]
        details = home_tilde("\n".join([f"{r['level']} {r['logger']}: {r['msg']}"] + block))
        sm = STATUS_RE.search(exc_line) or STATUS_RE.search(r["msg"])
        status = sm.group(1) if sm else ""
        if AUTH_EXC_RE.search(exc_type) or status in ("401", "403"):
            reason = auth_reason(exc_line or r["msg"])
            if STREAM_FAIL_RE.match(r["msg"]):
                if auth is not None:  # the retry loop refreshes/rotates; only a storm of these is ours to file
                    auth.append({"at": r["at"], "sid": r["sid"], "status": status or "401", "reason": reason,
                                 "provider": _provider_of(exc_type, exc_line), "details": details[-1500:]})
                continue
            tag = f"{status or 'auth'} {reason}"
            sym = (f"{exc_type.split('.')[-1]} [{tag}] in {where}" + (f" [{plug}]" if plug else "")
                   + f": {r['msg'][:90]}")
            out.append({"kind": "errors", "component": comp, "at": r["at"], "session_id": r["sid"], "symptom": sym,
                        "details": details[-1500:],
                        "fp": fp(comp, "tb", exc_type, tag, *[f"{a}:{b}" for a, b in inner])})
            continue
        sym = f"{exc_type.split('.')[-1]} in {where}" + (f" [{plug}]" if plug else "") + f": {r['msg'][:90]}"
        out.append({"kind": "errors", "component": comp, "at": r["at"], "session_id": r["sid"], "symptom": sym,
                    "details": details[-1500:],
                    "fp": fp(comp, "tb", exc_type, *_reason_code(exc_line), *[f"{a}:{b}" for a, b in inner])})
    return out


def reconnect_storms(cur: dict, prof: str, recs: List[dict], now: float, host: str) -> List[dict]:
    """Keep each (profile, library logger)'s reconnect times of the last hour in the cursor (a storm spans many
    passes); more than RECONNECT_STORM of them files one defect per host/profile/logger per hour."""
    store = cur.setdefault("reconnects", {})
    by_logger: Dict[str, List[dict]] = {}
    for r in recs:
        by_logger.setdefault(r["logger"], []).append(r)
    for k in list(store):  # loggers quiet this pass still age out
        if k.startswith(f"{prof}:") and k.split(":", 1)[1] not in by_logger:
            by_logger[k.split(":", 1)[1]] = []
    out = []
    for logger, rs in by_logger.items():
        k = f"{prof}:{logger}"
        st = store.get(k) or {}
        ts = sorted(t for t in list(st.get("ts") or []) + [r["at"] for r in rs] if t >= now - RECONNECT_WINDOW_S)
        st = {"ts": ts[-(RECONNECT_STORM * 5):], "filed": float(st.get("filed") or 0)}
        if not ts and st["filed"] < now - RECONNECT_WINDOW_S:
            store.pop(k, None)
            continue
        store[k] = st
        if len(ts) > RECONNECT_STORM and st["filed"] < now - RECONNECT_WINDOW_S:
            st["filed"] = now
            last = rs[-1]["details"] if rs else ""
            out.append({"kind": "errors", "component": "gateway", "at": ts[-1], "session_id": "", "profile": prof,
                        "symptom": f"{logger} keeps reconnecting on {host or 'this host'} ({prof}): {len(ts)} "
                                   f"reconnects in the last hour (more than {RECONNECT_STORM})",
                        "details": (f"{len(ts)} library auto-reconnects logged by {logger} in the last hour; "
                                    f"each one alone is not a defect. Latest:\n{last}")[:1500],
                        "fp": fp("gateway", "reconnect-storm", host, prof, logger)})
    return out


def auth_storms(cur: dict, recs: List[dict], now: float, host: str) -> List[dict]:
    """Keep this host's recent stream 401/403s in the cursor (a storm spans passes); AUTH_STORM_N of them within
    AUTH_STORM_S files one auth-failure storm. The fingerprint carries provider, status and reason classes, so a
    revoked-token storm and an expired-token storm are different defects."""
    st = cur.get("auth") or {}
    evs = [e for e in st.get("ev") or [] if e.get("at", 0) >= now - BACKFILL_S]
    filed = float(st.get("filed") or 0)
    out = []
    for r in sorted((r for r in recs if r["at"] >= now - BACKFILL_S), key=lambda r: r["at"]):
        evs = [e for e in evs if e["at"] > r["at"] - AUTH_STORM_S] + [
            {k: r[k] for k in ("at", "sid", "status", "reason", "provider")}]
        if len(evs) >= AUTH_STORM_N and filed <= r["at"] - AUTH_STORM_S:
            filed = r["at"]
            reasons = ",".join(sorted({e["reason"] for e in evs}))
            statuses = ",".join(sorted({e["status"] for e in evs}))
            provs = ",".join(sorted({e["provider"] for e in evs}))
            sids = ", ".join(sorted({e["sid"] for e in evs if e["sid"]}))
            out.append({"kind": "errors", "component": "agent", "at": r["at"], "session_id": r["sid"],
                        "profile": r.get("profile") or "default",
                        "symptom": f"auth-failure storm on {host or 'this host'}: {len(evs)} {provs} {statuses} "
                                   f"({reasons}) stream errors in {AUTH_STORM_S // 60} min",
                        "details": (f"{len(evs)} provider {statuses} errors ({reasons}) logged as 'Streaming failed "
                                    f"before delivery' within {AUTH_STORM_S // 60} min; one alone is handled by the "
                                    f"retry loop's refresh/rotation. Sessions: {sids}\nLatest:\n"
                                    f"{r['details']}")[:1500],
                        "fp": fp("agent", "auth-storm", provs, statuses, reasons)})
            evs = []
    evs = [e for e in evs if e["at"] > now - AUTH_STORM_S]
    if evs or filed > now - AUTH_STORM_S:
        cur["auth"] = {"ev": evs, "filed": filed}
    else:
        cur.pop("auth", None)
    return out


def collect_errors(home: Path, cur: dict, now: float, host: str = "") -> List[dict]:
    out = []
    auth: List[dict] = []
    for prof, h in homes(home):
        lines = read_new(h / "logs" / "errors.log", cur, f"errors:{prof}", now)
        recs: List[dict] = []
        prof_auth: List[dict] = []
        for s in parse_errors(lines, recs, prof_auth, host=host, prof=prof):
            if s["at"] >= now - BACKFILL_S:
                s["profile"] = prof
                out.append(s)
        auth += [dict(a, profile=prof) for a in prof_auth]
        out += reconnect_storms(cur, prof, recs, now, host)
    out += auth_storms(cur, auth, now, host)
    return out


# Managed kanban workers (agent/managed_worker.py) log their crash to <home>/logs/managed-worker.log, every line
# prefixed "[pid N] ", with no timestamps; errors.log never sees it. One crash is the card's business (its run
# fails, the retry tier or failure limit handles it). The same exception killing WORKER_STORM_N distinct workers
# within WORKER_STORM_S is a fleet-wide defect (10-29: ~40 workers died at agent build with ModelRoutingError and
# nothing was filed), so that files one signal per (exception, normalized message) per window.
WORKER_STORM_N, WORKER_STORM_S = 3, 1800
PID_LINE_RE = re.compile(r"^\[pid (\d+)\] ?(.*)$")
# Core since the managed-worker.log header commit writes, before a worker's first line, one
# "[pid N] @ <ISO8601 UTC> core=<short sha>" header. Older logs have none; their crashes keep the pass time.
PID_HEADER_RE = re.compile(r"^\[pid (\d+)\] @ (\d{4}-\d\d-\d\dT[\d:.]+(?:Z|[+-]\d\d:?\d\d)) core=(\S+)$")
WORKER_EXC_RE = re.compile(r"^([A-Za-z_][\w.]*): (.*)$")
SECRET_RE = re.compile(r"\bsk-[A-Za-z0-9_\-]{6,}")


def _scrub(s: str) -> str:
    return SECRET_RE.sub("sk-***", home_tilde(s or ""))


def _norm_msg(msg: str) -> str:
    """A message with homes, paths, card ids, hex ids and numbers collapsed, so one crash compares equal."""
    s = _scrub(msg)
    s = re.sub(r"(?:~|/)[\w.\-]*(?:/[\w.\-]+)+", lambda m: ".../" + m.group(0).rsplit("/", 1)[-1], s)
    s = re.sub(r"\bt_[0-9a-zA-Z]+", "t_#", s)
    s = re.sub(r"\b[0-9a-f]{8,}\b", "#", s)
    return re.sub(r"\d+", "N", s).strip()


def _header_time(stamp: str) -> Optional[float]:
    try:
        t = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t.timestamp() if t.tzinfo is not None else None


def _utc(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_worker_crashes(lines: List[str]) -> List[dict]:
    """managed-worker.log lines -> one {pid, exc, msg, block} per pid block that holds a traceback, from its last
    `Type: msg` line after the last `Traceback` line. Provider transients and exit noise are not crashes. A block
    under a `[pid N] @ <UTC> core=<sha>` header also carries `t` (epoch of that header) and `core`."""
    by_pid: Dict[str, List[str]] = {}
    stamps: Dict[str, tuple] = {}
    for ln in lines:
        h = PID_HEADER_RE.match(ln)
        if h and (t := _header_time(h.group(2))) is not None:
            stamps[h.group(1)] = (t, h.group(3))
            by_pid.setdefault(h.group(1), [])
            continue
        m = PID_LINE_RE.match(ln)
        if m:
            by_pid.setdefault(m.group(1), []).append(m.group(2))
    out = []
    for pid, blk in by_pid.items():
        tbs = [i for i, l in enumerate(blk) if l.startswith("Traceback (most recent call last)")]
        if not tbs:
            continue
        tail = blk[tbs[-1]:]
        em_i, em = next(((i, m) for i in range(len(tail) - 1, -1, -1) for m in [WORKER_EXC_RE.match(tail[i])] if m),
                        (-1, None))
        if em is None:
            continue
        exc, msg = em.group(1), em.group(2)
        if (TRANSIENT_RE.search(f"{exc}: {msg}") or EXIT_NOISE_RE.search(msg)
                or provider_5xx(exc, tail[em_i], _exc_body(tail, tail[em_i]))):
            continue
        crash = {"pid": pid, "exc": exc, "msg": msg, "block": tail}
        if pid in stamps:
            crash["t"], crash["core"] = stamps[pid]
        out.append(crash)
    return out


def worker_storms(cur: dict, crashes: List[dict], now: float, host: str) -> List[dict]:
    """Keep this host's recent worker crashes in the cursor (a storm spans passes, and the window runs on pass
    times); WORKER_STORM_N distinct pids with the same (exception, normalized message) within WORKER_STORM_S
    file one signal, and the same fingerprint is not filed again within WORKER_STORM_S. Crashes from headered
    blocks also carry their logged UTC time and core sha: the details name the earliest/latest crash and the
    sha(s), and when every pid in the storm has a time the signal's `at` is the latest crash, not the pass."""
    st = cur.get("worker_storm") or {}
    evs = [e for e in st.get("ev") or [] if e.get("at", 0) > now - WORKER_STORM_S]
    filed = {k: v for k, v in (st.get("filed") or {}).items() if v > now - WORKER_STORM_S}
    out = []
    latest: Dict[str, dict] = {}
    for c in crashes:
        k = h10(c["exc"], _norm_msg(c["msg"]))
        ev = {"at": now, "pid": c["pid"], "k": k}
        if c.get("t") is not None:
            ev["t"], ev["core"] = c["t"], c.get("core") or "unknown"
        evs.append(ev)
        latest[k] = c
    for k, c in latest.items():
        mine = [e for e in evs if e["k"] == k]
        pids = sorted({e["pid"] for e in mine}, key=lambda p: (len(p), p))
        if len(pids) < WORKER_STORM_N or k in filed:
            continue
        filed[k] = now
        evs = [e for e in evs if e["k"] != k]
        norm = _norm_msg(c["msg"])
        tb = "\n".join(c["block"][-15:])
        when, at = _crash_times(mine, pids, now)
        out.append({"kind": "worker", "component": "kanban", "at": at, "session_id": "",
                    "symptom": f"managed-worker crash storm on {host or 'this host'}: {len(pids)} workers died "
                               f"with {c['exc'].split('.')[-1]}: {_scrub(c['msg'])[:120]}",
                    "details": _scrub(f"{len(pids)} managed kanban workers died with {c['exc']} within "
                                      f"{WORKER_STORM_S // 60} min (logs/managed-worker.log). pids: "
                                      f"{', '.join(pids)}\n{when}{_span_note(c)}"
                                      f"Latest (pid {c['pid']}):\n{tb}")[:1500],
                    "fp": fp("kanban", "worker-storm", c["exc"], norm)})
    if evs or filed:
        cur["worker_storm"] = {"ev": evs[-200:], "filed": filed}
    else:
        cur.pop("worker_storm", None)
    return out


def _crash_times(evs: List[dict], pids: List[str], now: float) -> Tuple[str, float]:
    """(details line, signal time) from the storm's headered crashes; ("", now) when none carries a time."""
    stamped = {e["pid"]: e for e in evs if e.get("t") is not None}
    if not stamped:
        return "", now
    ts = sorted(e["t"] for e in stamped.values())
    cores = sorted({e.get("core") or "unknown" for e in stamped.values()})
    every = all(p in stamped for p in pids)
    part = "" if every else f" ({len(stamped)} of {len(pids)} pids stamped)"
    line = (f"Crash times (UTC, log headers){part}: earliest {_utc(ts[0])}, latest {_utc(ts[-1])}; "
            f"core: {', '.join(cores)}\n")
    return line, (ts[-1] if every else now)


def _span_note(c: dict) -> str:
    """Where the latest crash came from: the byte range read this pass, which shows a late or replayed signal in
    the card evidence (kanban-e001f2d4a0) whether or not the block carried a header."""
    if not c.get("span"):
        return ""
    prof = c.get("profile") or "default"
    plain = " (log lines carry no timestamps)" if c.get("t") is None else ""
    return (f"Latest crash first seen this pass in {prof} logs/managed-worker.log bytes "
            f"{c['span'][0]}-{c['span'][1]}{plain}.\n")


def collect_worker(home: Path, cur: dict, now: float, host: str = "") -> List[dict]:
    crashes: List[dict] = []
    for prof, h in homes(home):
        path, key = h / "logs" / "managed-worker.log", f"worker:{prof}"
        if seed_only_first_sight(path, cur, key):
            continue  # first sight: the backlog is old crashes, not a live storm (kanban-e001f2d4a0)
        span: list = []
        for c in parse_worker_crashes(read_new(path, cur, key, now, span)):
            c["pid"] = c["pid"] if prof == "default" else f"{prof}:{c['pid']}"
            c["profile"], c["span"] = prof, list(span)
            crashes.append(c)
    return worker_storms(cur, crashes, now, host)


CRON_WINDOW_S = 86400


def _cron_threshold(h: Path) -> int:
    """cron.failure_nudge_threshold from <home>/config.yaml (core's own streak warning); default 3, 0/bad -> 3."""
    try:
        txt = (h / "config.yaml").read_text()
    except OSError:
        return 3
    try:
        import yaml  # optional: the sweep may run under a bare stdlib python
        n = ((yaml.safe_load(txt) or {}).get("cron") or {}).get("failure_nudge_threshold", 3)
    except ImportError:
        m = re.search(r"^cron:[ \t]*\n(?:[ \t]+.*\n)*?[ \t]+failure_nudge_threshold:[ \t]*(\d+)", txt, re.M)
        n = m.group(1) if m else 3
    except Exception:
        return 3
    try:
        return int(n) if int(n) > 0 else 3
    except (TypeError, ValueError):
        return 3


def collect_cron(home: Path, cur: dict, now: float, host: str) -> List[dict]:
    """File a cron job only while it is still failing (failure_streak >= threshold), or when it failed
    `threshold` times within 24 h although each run recovered (count kept in cur["cron_fails"]).
    A one-off failure the next run already recovered from is only counted: core keeps last_failure
    after a success on purpose and leaves "how recent counts" to the reader (cron/jobs.py)."""
    out = []
    seen = cur.setdefault("cron", {})
    fails = cur.setdefault("cron_fails", {})
    first = not seen
    for prof, h in homes(home):
        try:
            j = json.loads((h / "cron" / "jobs.json").read_text())
        except Exception:
            continue
        threshold = _cron_threshold(h)
        for job in (j.get("jobs") if isinstance(j, dict) else j) or []:
            lf = job.get("last_failure") or {}
            at = _iso_ts(lf.get("at"))
            k = f"{prof}:{job.get('id')}"
            prev = float(seen.get(k) or 0)
            if not at or at <= prev or (first and at < now - BACKFILL_S):
                seen[k] = max(prev, at)
                continue
            seen[k] = at
            name = job.get("name") or job.get("id")
            detail = str(lf.get("detail") or job.get("last_error") or "run failed")
            streak = int(job.get("failure_streak") or 0)
            recent = [t for t in fails.get(k, []) if t > at - CRON_WINDOW_S]
            if not detail.startswith("Gateway shutdown ("):
                recent.append(at)  # a run killed by a gateway restart is not the job's fault: never counted
            fails[k] = recent
            if streak < threshold and len(recent) < threshold:
                continue  # one-off / under core's own nudge threshold (cursor advanced): counted, not filed
            why = (f"failure_streak={streak} >= {threshold}" if streak >= threshold
                   else f"{len(recent)} failures in 24h (each recovered)")
            fails.pop(k, None)  # one card per burst; the fp dedupes repeats anyway
            out.append({"kind": "cron", "component": "cron", "at": at, "profile": prof,
                        "symptom": f"cron job '{name}' failed on {host}: {detail.splitlines()[0][:120] if detail else ''}",
                        "details": home_tilde(f"job {job.get('id')} ({name}, profile {prof}) failure_streak="
                                              f"{job.get('failure_streak')} last_status={job.get('last_status')} "
                                              f"filed: {why}\nscript={job.get('script')}\n{detail}")[:1500],
                        "fp": fp("cron", host, prof, name)})
    for k in [k for k, v in fails.items() if not v or max(v) < now - CRON_WINDOW_S]:
        fails.pop(k, None)  # keep the cursor small
    return out


def _ro(db: Path):
    """Read-only-safe open of a live WAL db (mode=ro fails when -wal/-shm were deleted): ro, else rw + query_only."""
    try:
        c = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
        c.execute("select 1 from sqlite_master limit 1")
        return c
    except sqlite3.Error:
        c = sqlite3.connect(f"file:{db}?mode=rw", uri=True, timeout=10)
        c.execute("PRAGMA query_only=ON")
        return c


def board_dbs(home: Path) -> List[Tuple[str, Path]]:
    out, seen = [], set()
    for prof, h in homes(home):
        cands = [("default", h / "kanban.db")] + [(Path(p).parent.name, Path(p)) for p in
                                                  sorted(glob.glob(str(h / "kanban" / "boards" / "*" / "kanban.db")))]
        for name, db in cands:
            rp = os.path.realpath(db)
            if db.is_file() and db.stat().st_size and rp not in seen:
                seen.add(rp)
                out.append((name if prof == "default" else f"{prof}/{name}", db))
    return out


def collect_kanban(home: Path, cur: dict, now: float, host: str) -> List[dict]:
    out = []
    seen = cur.setdefault("kanban", {})
    q = ",".join("?" * len(FAIL_OUTCOMES))
    for board, db in board_dbs(home):
        try:
            c = _ro(db)
            last = int(seen.get(board) or 0)
            top = c.execute("select coalesce(max(id),0) from task_runs").fetchone()[0]
            rows = c.execute(
                f"select r.task_id, count(*), max(r.id), max(coalesce(r.ended_at, r.started_at)), "
                f"group_concat(r.outcome), group_concat(substr(coalesce(r.error,''),1,200), ' | '), t.title, "
                f"coalesce(t.idempotency_key,''), t.status from task_runs r join tasks t on t.id = r.task_id "
                f"where r.outcome in ({q}) group by r.task_id having count(*) >= 2 and max(r.id) > ?",
                (*FAIL_OUTCOMES, last)).fetchall()
            c.close()
        except Exception:
            continue
        seen[board] = top
        out += _systemic_holds(cur, board, db, now, host)
        groups: Dict[tuple, list] = {}
        for row in rows:
            tid, n, _mx, ended, outcomes, errs, title, ikey, status = row
            if (ended or 0) < now - BACKFILL_S or ikey.startswith("selfheal:"):
                continue  # self-heal's own cards: the lane reconciles them (no card about a card about a card)
            stem = _TITLE_NUM_RE.sub("", title or "").strip()
            ek = _err_key(errs)
            # siblings of one batch ("load #14", "load #20") failing the same way are one defect, not one per card
            key = (stem, ek) if stem and ek else (tid,)
            groups.setdefault(key, []).append(row)
        for key, grp in groups.items():
            if len(grp) == 1 and (len(key) == 1 or key[0] == (grp[0][6] or "").strip()):
                tid, n, _mx, ended, outcomes, errs, title, ikey, status = grp[0]
                out.append({"kind": "kanban", "component": "kanban", "at": float(ended or now),
                            "symptom": f"kanban card {tid} on {host}/{board} failed {n}x ({outcomes}): {title[:80]}",
                            "details": home_tilde(f"board {board} card {tid} status={status} title={title}\n"
                                                  f"outcomes: {outcomes}\nerrors: {errs}")[:1500],
                            "kanban": {"board": board, "cards": [tid]},
                            "fp": fp("kanban", host, board, tid)})
                continue
            grp.sort(key=lambda r: r[0])
            tids = [r[0] for r in grp]
            stem, ek = key
            n = sum(r[1] for r in grp)
            ended = max(r[3] or 0 for r in grp)
            outcomes = ",".join(r[4] for r in grp)
            statuses = ",".join(sorted({str(r[8]) for r in grp}))
            out.append({"kind": "kanban", "component": "kanban", "at": float(ended or now),
                        "symptom": f"kanban cards {tids[0]}+{len(tids) - 1} on {host}/{board} failed {n}x "
                                   f"({len(tids)} siblings '{stem[:60]} #N'): same error",
                        "details": home_tilde(f"board {board} cards {', '.join(tids)} status={statuses} "
                                              f"title={stem} #N\noutcomes: {outcomes}\n"
                                              f"errors: {grp[0][5]}")[:1500],
                        "kanban": {"board": board, "cards": tids},
                        # one fingerprint per batch+error, so siblings failing in a later pass dedupe onto it
                        "fp": fp("kanban", host, board, "batch", stem, ek)})
    return out


_TITLE_NUM_RE = re.compile(r"\s*#\d+\s*$")


def _err_key(errs: Optional[str]) -> str:
    """The distinct run errors of a card with ids, numbers and pids masked, so sibling cards compare equal."""
    parts = {re.sub(r"\bt_[0-9a-zA-Z]+|\d+", "#", p.strip()) for p in (errs or "").split(" | ")}
    return " | ".join(sorted(p for p in parts if p))


def _hold_head(err: Optional[str]) -> str:
    """The card-independent head of a run error (what precedes "Worker's last output"), normalized."""
    head = re.split(r"\s*Worker's last output\b", str(err or ""), maxsplit=1)[0][:200]
    return _norm_msg(head) or "unknown"


def _systemic_holds(cur: dict, board: str, db: Path, now: float, host: str) -> List[dict]:
    """The dispatcher's systemic breaker (3+ same-fingerprint crashes in one tick) holds each card in triage with a
    `gave_up` event whose payload has `sticky: true`; every card crashed only once, so the >= 2 failed-runs rule
    never sees it. One signal per (board, normalized error head) per WORKER_STORM_S, by event time. Event ids are
    the cursor (cur["kanban_ev"][board]); a first sight looks back only HOLD_BACKFILL_S (24 h): an older hold on a
    board seen for the first time was handled long ago (kanban-754a3e3cf1 filed a 2-day-old hold whose cards the
    operator had archived and rerun clean). The held cards ride in ``kanban_hold`` (not ``kanban``: they are no
    per-card trigger) so self_heal.kanban_trigger_closed can drop a stale hold whose cards are all closed."""
    evc = cur.setdefault("kanban_ev", {})
    filed = cur.setdefault("kanban_hold", {})
    try:
        c = _ro(db)
        try:
            last = evc.get(board)
            top = c.execute("select coalesce(max(id),0) from task_events").fetchone()[0]
            if last is None:
                rows = c.execute("select id, task_id, payload, created_at from task_events where kind='gave_up' "
                                 "and created_at >= ? order by id", (int(now - HOLD_BACKFILL_S),)).fetchall()
            else:
                rows = c.execute("select id, task_id, payload, created_at from task_events where kind='gave_up' "
                                 "and id > ? order by id", (int(last),)).fetchall()
        finally:
            c.close()
    except Exception:
        return []  # an old board without task_events: nothing to read
    evc[board] = top
    clusters: Dict[str, dict] = {}
    out = []
    for _id, tid, payload, at in rows:
        try:
            p = json.loads(payload or "{}")
        except Exception:
            continue
        if not isinstance(p, dict) or p.get("sticky") is not True:
            continue
        head = _hold_head(p.get("error"))
        k = f"{board}\x1f{head}"
        at = float(at or now)
        cl = clusters.get(k)
        if cl is not None and at - cl["at0"] < WORKER_STORM_S:
            cl["tids"].append(tid)
            cl["at"] = at
            continue
        if float(filed.get(k) or 0) > at - WORKER_STORM_S:
            continue  # filed for this window by an earlier pass
        filed[k] = at
        clusters[k] = cl = {"at0": at, "at": at, "head": head, "tids": [tid], "err": str(p.get("error") or "")}
        out.append(cl)
    for k in [k for k, v in filed.items() if float(v or 0) < now - BACKFILL_S]:
        filed.pop(k, None)
    sigs = []
    for cl in out:
        tids = list(dict.fromkeys(cl["tids"]))
        sigs.append({"kind": "kanban", "component": "kanban", "at": cl["at"],
                     "symptom": f"systemic kanban crash on {host or 'this host'}: dispatcher held {len(tids)} "
                                f"card(s) in triage",
                     "details": _scrub(f"The dispatcher's systemic breaker (3+ same-fingerprint worker crashes in one "
                                       f"tick) held these cards on {host}/{board} with a sticky gave_up: "
                                       f"{', '.join(tids)}\nerror: {cl['err']}")[:1500],
                     "kanban_hold": {"board": board, "cards": tids, "at0": cl["at0"]},
                     "fp": fp("kanban", "systemic-hold", cl["head"])})
    return sigs


def _signal_storm(cur: dict, prof: str, r: dict, at: float, host: str) -> Optional[dict]:
    """Count one ``gateway.exit_signal`` / ``gateway.start_refused``; return a signal only for the
    SIGNAL_STORM_N-th within SIGNAL_STORM_S."""
    recent = [t for t in cur.setdefault("gw_signals", {}).get(prof, []) if t > at - SIGNAL_STORM_S] + [at]
    if len(recent) < SIGNAL_STORM_N:
        cur["gw_signals"][prof] = recent
        return None
    cur["gw_signals"].pop(prof, None)  # one card per storm; the fp dedupes repeats anyway
    last = r.get("signal") or (f"refused start: {r.get('reason')}" if r.get("reason") else "UNKNOWN")
    return {"kind": "gateway", "component": "gateway", "at": at, "profile": prof,
            "symptom": f"gateway ({prof}) on {host} restart storm: {len(recent)} stop signals / refused starts in "
                       f"{SIGNAL_STORM_S // 60} min (last {last})",
            "details": home_tilde(json.dumps({k: v for k, v in r.items() if k != "argv"}))[:1500],
            "fp": fp("gateway", host, prof, "gateway.exit_signal", "storm")}


# A supervised start refused by the SAME live owner pid again and again is not a copy still draining (that one
# leaves within its drain budget): the owner holds the home and the supervisor loops forever (gateway-e2cd786685,
# a client-spawned gateway beside the mini's KeepAlive job). The core now asks a manual owner to drain; if one
# owner is still refusing WEDGE_N+ supervised starts WEDGE_S after its first refusal (no gap over WEDGE_S), that
# handback failed or the owner is not draining: file it as its own defect (own fingerprint, never folded into the
# open storm card as a comment). Launchd retries every ~10 s, so a real wedge reaches this in ~10 min.
WEDGE_N, WEDGE_S = 3, 600


def _wedged_owner(cur: dict, prof: str, r: dict, at: float, host: str) -> Optional[dict]:
    owner = r.get("owner_pid")
    if owner is None:
        return None
    seen = cur.setdefault("gw_wedge", {})
    for key in [k for k, v in seen.items() if not isinstance(v, list) or len(v) != 3 or at - v[1] > WEDGE_S]:
        seen.pop(key, None)  # owners that stopped refusing; keeps the cursor small
    key = f"{prof}:{owner}"
    first, _last, count = seen.get(key) or (at, at, 0)
    count += 1
    if count < WEDGE_N or at - first < WEDGE_S:
        seen[key] = [first, at, count]
        return None
    seen.pop(key, None)  # one signal per wedge; the fingerprint dedupes a later one
    handback = r.get("handback") or "none"
    return {"kind": "gateway", "component": "gateway", "at": at, "profile": prof,
            "symptom": f"gateway ({prof}) on {host} supervisor wedged behind a live owner: {count} supervised "
                       f"starts refused by owner_pid={owner} over {int(at - first) // 60} min (handback {handback})",
            "details": home_tilde(json.dumps({k: v for k, v in r.items() if k != "argv"}))[:1500],
            "fp": fp("gateway", host, prof, "gateway.start_refused", "wedged-owner")}


def _reason_class(reason: str) -> str:
    """A gateway exit_reason with its volatile parts (numbers, paths) removed, so one cause with the same text
    keeps one fingerprint across pids and ports."""
    s = re.sub(r"(/[^\s'\"]+)+", "<path>", reason.lower())
    return re.sub(r"\d+", "#", s).strip()[:80]


def collect_gateway(home: Path, cur: dict, now: float, host: str) -> List[dict]:
    out = []
    for prof, h in homes(home):
        for ln in read_new(h / "logs" / "gateway-exit-diag.log", cur, f"gateway:{prof}", now):
            try:
                r = json.loads(ln)
            except Exception:
                continue
            tag = r.get("tag")
            if tag not in GATEWAY_TAGS:
                continue
            at = _iso_ts(r.get("ts"))
            if at < now - BACKFILL_S:
                continue
            if tag == "gateway.start_refused" and r.get("owner_alive") is False:
                out.append({"kind": "gateway", "component": "gateway", "at": at, "profile": prof,
                            "symptom": f"gateway ({prof}) on {host} start refused by a gateway that is not "
                                       f"running: {r.get('reason')} owner_pid={r.get('owner_pid')}",
                            "details": home_tilde(json.dumps({k: v for k, v in r.items() if k != "argv"}))[:1500],
                            "fp": fp("gateway", host, prof, tag, str(r.get("reason") or ""))})
                continue
            if tag == "gateway.start_refused" and r.get("owner_alive") is True and r.get("external_supervisor"):
                # The service manager restarted into a gateway still draining (its main pid ended first):
                # the restart-storm defect itself, so it goes to that lane at once and, while its card is open,
                # lands there as a comment instead of a new card (gateway-cc28b19bca).
                out.append({"kind": "gateway", "component": "gateway", "at": at, "profile": prof,
                            "symptom": f"gateway ({prof}) on {host} supervisor restarted into a live gateway: start "
                                       f"refused by owner_pid={r.get('owner_pid')} ({r.get('reason')})",
                            "details": home_tilde(json.dumps({k: v for k, v in r.items() if k != "argv"}))[:1500],
                            "fp": fp("gateway", host, prof, "gateway.exit_signal", "storm"),
                            "comment_open_card": True})
                if (sig := _wedged_owner(cur, prof, r, at, host)) is not None:
                    out.append(sig)
                continue
            if tag in ("gateway.exit_signal", "gateway.start_refused"):
                if (sig := _signal_storm(cur, prof, r, at, host)) is not None:
                    out.append(sig)
                continue
            why = r.get("exc_type") or ("suspected OOM" if r.get("suspected_oom") else "") or tag.split(".")[-1]
            fp_key = r.get("exc_type") or ""
            cause = str(r.get("cause") or "") if tag == "gateway.exit_nonzero" else ""
            if cause == "unrecorded":
                # The exit path returned False without note_exit_cause(): that missing note is the defect.
                why, fp_key = "exit path missing a cause note (cause=unrecorded)", "unrecorded"
            elif cause:
                # gateway-3cf2256311: each cause (and reason class) is its own defect, never a "recurrence" of
                # another cause's fixed crash. Records without a cause (old core) keep the old fingerprint.
                reason = str(r.get("exit_reason") or "")
                why = f"cause={cause}" + (f": {reason[:200]}" if reason else "")
                fp_key = f"{cause}:{_reason_class(reason)}"
            out.append({"kind": "gateway", "component": "gateway", "at": at, "profile": prof,
                        "symptom": f"gateway ({prof}) on {host} died uncleanly: {tag} {why}",
                        "details": home_tilde(json.dumps({k: v for k, v in r.items() if k != "argv"}))[:1500],
                        "fp": fp("gateway", host, prof, tag, fp_key)})
    return out


HANDOFF_GRACE_S = 15 * 60  # a nudge waits this long for its reply_nudge_outcome before it files anyway
# reply_check rules whose nudges get a reply_nudge_outcome (same as reply_check.RULES; a test pins the match).
OUTCOME_RULES = ("handoff", "confirm", "unverified", "unwatched", "report_only", "status_only", "denied_ask",
                 "credit_ask", "laptop_job")
NUDGE_WHAT = {"handoff": "agent handed the user a step it could do itself"}


def _handoff_signal(r: dict, why: str) -> dict:
    ev = str(r.get("evidence") or "")
    rule = r.get("rule") or "handoff"
    what = NUDGE_WHAT.get(rule) or (f"coach {rule} nudge got no outcome (no revised reply)" if r.get("no_outcome")
                                    else f"agent's revised reply still failed the coach {rule} check")
    reply = str(r.get("reply") or "")
    return {"kind": "coach", "component": "agent", "at": float(r.get("ts") or 0),
            "session_id": r.get("session_id") or "",
            "symptom": f"{what}: {ev[:120]}",
            "details": (f"coach reply_check {rule} nudge (p={r.get('p')}): {ev}; {why}"
                        + (f"; revised reply: {reply}" if reply else ""))[:1500],
            "fp": fp("agent", rule, r.get("session_id") or "")}


def _outcome_matches(n: dict, o: dict) -> bool:
    """The reply_nudge_outcome `o` judges the revision of nudge `n`: same session, same draft when both carry one,
    and logged within the grace window after the nudge."""
    if (o.get("session_id") or "") != (n.get("session_id") or ""):
        return False
    if (o.get("rule") or "handoff") != (n.get("rule") or "handoff"):
        return False
    if n.get("draft_id") and o.get("draft_id") and n["draft_id"] != o["draft_id"]:
        return False
    dt = float(o.get("ts") or 0) - float(n.get("ts") or 0)
    return -5 <= dt <= HANDOFF_GRACE_S


def _squash(s: str) -> str:
    return " ".join(str(s or "").split())


def unsent_reply(home: Path, session_id: str, reply: str) -> bool:
    """True when a nudge's revised reply provably never became an assistant row of its session: the session has
    rows in some state.db and none of its assistant rows starts with the reply. A live turn's revision is always
    persisted (78 of 78 judged outcomes on the hub, 2026-10-31); a detached fork's (background review, side question
    sharing the parent's session_id, self-heal agent-77325213e1) never is, so its "failed revision" is a coach bug,
    not an agent defect. Short or redacted reply, no state.db or no rows for the session = False (file as before)."""
    head = _squash(reply)[:80]
    if len(head) < 20 or not session_id or any(m in head.lower() for m in ("[redacted", "***", "«redacted")):
        return False  # too little or redacted text can't be compared with the stored row
    seen_session = False
    for _prof, h in homes(home):
        db = h / "state.db"
        if not db.is_file():
            continue
        try:
            c = _ro(db)
            try:
                rows = c.execute("select role, content from messages where session_id = ?",
                                 (session_id,)).fetchall()
            finally:
                c.close()
        except sqlite3.Error:
            return False  # unreadable: can't prove it was unsent
        if rows:
            seen_session = True
        if any(role == "assistant" and _squash(content).startswith(head) for role, content in rows):
            return False
    return seen_session


def _unsent_draft_signal(r: dict, why: str) -> dict:
    rule = r.get("rule") or "handoff"
    ev = str(r.get("evidence") or "")
    return {"kind": "coach", "component": "coach", "at": float(r.get("ts") or 0),
            "session_id": r.get("session_id") or "",
            "symptom": f"coach {rule} check judged a text that never reached the user (fork turn?): {ev[:100]}",
            "details": (f"coach reply_check {rule} nudge (p={r.get('p')}) on draft {r.get('draft_id')}: its revised "
                        f"reply is in no assistant row of session {r.get('session_id')}, so a detached fork "
                        f"(background review, side question) sharing the session_id reached pre_verify. {why}"
                        + (f"; revised reply: {r.get('reply')}" if r.get("reply") else ""))[:1500],
            "fp": fp("coach", "judged_unsent_draft", rule)}


INTERRUPT_RE = re.compile(r"^\s*(?:Operation interrupted\b|An automated delivery was interrupted\b)")


def _turn_lost(home: Path, n: dict) -> bool:
    """True when the nudged session's visible transcript after the nudge ends in an interruption marker with no later
    final assistant reply (the turn was killed, so no reply_nudge_outcome can be logged). Any doubt (session not
    found, unreadable db) is False: the nudge files as before (fail-open)."""
    sid, ts = n.get("session_id") or "", float(n.get("ts") or 0)
    if not sid:
        return False
    for _prof, h in homes(home):
        db = h / "state.db"
        if not db.is_file():
            continue
        try:
            c = _ro(db)
            try:
                rows = c.execute("select content, tool_calls from messages where session_id=? and role='assistant' "
                                 "and timestamp>=? and (active=1 or compacted=1) order by timestamp, id",
                                 (sid, ts)).fetchall()
            finally:
                c.close()
        except sqlite3.Error:
            continue
        lost = None
        for content, tool_calls in rows:
            text = str(content or "")
            if INTERRUPT_RE.search(text):
                lost = True
            elif text.strip() and not (tool_calls or "").strip("[] "):
                lost = False  # a final reply after the interrupt: the turn went on (or a new one ran)
        if lost is not None:
            return lost
    return False


def _coach_log(home: Path, rec: dict) -> None:
    try:
        with (home / "coach" / "log.jsonl").open("a") as f:
            f.write(json.dumps(rec) + "\n")
    except OSError:
        pass


# One reply_check rule holding replies whose user turn opens with the same text, in several chats: the rule is firing
# on a fixed prompt (automation text), not on what users ask (agent-21185525f4: 5 of the hub's 8 status_only holds
# opened with "[Kanban close-out]", in 4 chats, and nothing noticed). Filed once per (rule, opening); later hits ride
# comment_open_card onto the open card instead of filing again.
TEMPLATE_HOLD_N = 3
TEMPLATE_HOLD_CHATS = 2
TEMPLATE_HOLD_S = 7 * 86400
TEMPLATE_HEAD = 40
_ASKED_RE = re.compile(r'^asked "(.*?)"; ')


def _ask_head(r: dict) -> str:
    """The first TEMPLATE_HEAD chars of the held turn's user text: the row's ask_head, else (rows from an older
    plugin) the status_only evidence quote. '' when unknown."""
    head = r.get("ask_head")
    if not head:
        m = _ASKED_RE.match(str(r.get("evidence") or ""))
        head = m.group(1) if m else ""
    return " ".join(str(head).split())[:TEMPLATE_HEAD]


def template_holds(cur: dict, nudges: List[dict], now: float, host: str = "") -> List[dict]:
    """nudges: this pass's reply_nudge rows. Keeps a 7-day window in cur["coach_template"] across passes."""
    st = cur.get("coach_template") or {}
    evs = [e for e in st.get("ev") or [] if e.get("at", 0) > now - TEMPLATE_HOLD_S]
    filed = {k: v for k, v in (st.get("filed") or {}).items() if v > now - TEMPLATE_HOLD_S}
    for r in nudges:
        head = _ask_head(r)
        at = float(r.get("ts") or now)
        if len(head) < 12 or at <= now - TEMPLATE_HOLD_S:
            continue
        evs.append({"at": at, "rule": r.get("rule") or "", "head": head, "sid": r.get("session_id") or ""})
    out = []
    new_keys = {(r.get("rule") or "", _ask_head(r).lower()) for r in nudges}
    groups: Dict[Tuple[str, str], List[dict]] = {}
    for e in evs:
        groups.setdefault((e["rule"], e["head"].lower()), []).append(e)
    for (rule, _), mine in groups.items():
        head = mine[-1]["head"]
        chats = sorted({e["sid"] for e in mine if e["sid"]})
        key = h10(rule, head.lower())
        if len(mine) < TEMPLATE_HOLD_N or len(chats) < TEMPLATE_HOLD_CHATS:
            continue
        if key in filed and (rule, head.lower()) not in new_keys:
            continue  # filed already; only a new hit adds a comment
        filed.setdefault(key, now)
        out.append({"kind": "coach", "component": "agent", "at": max(e["at"] for e in mine), "session_id": "",
                    "symptom": f"coach {rule} held {len(mine)} replies whose user turn has the same opening text "
                               f"\"{head}\" in {len(chats)} chats: the rule is firing on a fixed (automation) prompt",
                    "details": (f"{len(mine)} holds in {len(chats)} chats within {TEMPLATE_HOLD_S // 86400} days "
                                f"on {host or 'this host'} (coach/log.jsonl reply_nudge, rule={rule}). Opening: "
                                f"\"{head}\". Chats: {', '.join(chats[:10])}. Check whether the opening is a Hermes-"
                                f"written prompt the rule should skip (reply_check.notice_turn) or a real pattern."
                                )[:1500],
                    "fp": fp("agent", "template-hold", rule, head.lower()),
                    "comment_open_card": True})
    if evs or filed:
        cur["coach_template"] = {"ev": evs[-500:], "filed": filed}
    else:
        cur.pop("coach_template", None)
    return out


def confirm_cap_check(cur: dict, capped: List[dict], now: float, host: str = "") -> List[dict]:
    """agent-f668487724: report_only's second opinion has a daily cap (reply_check.CONFIRM_DAILY_CAP, sized to the
    busiest day seen). Past it, an unconfirmed report_only no longer nudges, so the check silently goes quiet. Any
    report_only_confirm_capped row means the cap is too small again: one signal per day it happens (the same card gets
    a comment on later days). capped: this pass's report_only_confirm_capped rows."""
    filed = [d for d in cur.get("coach_confirm_cap") or [] if isinstance(d, str)][-14:]
    by_day: Dict[str, List[dict]] = {}
    for r in capped:
        at = float(r.get("ts") or now)
        by_day.setdefault(time.strftime("%Y-%m-%d", time.localtime(at)), []).append(r)
    out = []
    for day, rows in sorted(by_day.items()):
        if day in filed:
            continue
        filed.append(day)
        r = rows[0]
        out.append({"kind": "coach", "component": "plugin", "at": float(r.get("ts") or now), "session_id": "",
                    "symptom": "coach report_only second-opinion daily cap reached: unconfirmed report_only replies "
                               "go unchecked",
                    "details": (f"coach/log.jsonl on {host or 'this host'} has report_only_confirm_capped on {day} "
                                f"(used={r.get('used')} cap={r.get('cap')}). Past the cap the screen-only score no "
                                f"longer nudges (report_only_fallback=skip), so report_only misses replies for the "
                                f"rest of the day. Raise reply_check.CONFIRM_DAILY_CAP (or COACH_REPORT_ONLY_CONFIRM_CAP)"
                                f" to cover the day's report_only_confirm count, or lower the confirm volume.")[:1500],
                    "fp": fp("plugin", "report-only-confirm-cap"),
                    "comment_open_card": True})
    if filed:
        cur["coach_confirm_cap"] = filed[-14:]
    return out


def collect_coach(home: Path, cur: dict, now: float, host: str = "") -> List[dict]:
    """fixloop_card events file at once. A reply_check nudge (any rule in OUTCOME_RULES) is the guard WORKING (the
    agent was sent back to fix its reply), so it files only when its reply_nudge_outcome says the revised reply still
    fails the same rule, or when no outcome arrived within HANDOFF_GRACE_S (crash, a lost continuation): an ignored
    nudge is never lost. A non-handoff nudge without the `outcome` marker comes from a backend still on the old
    plugin, where no outcome will ever come: not filed (handoff nudges always were). Nudges still waiting for an
    outcome live in cur["coach_handoff_pending"] across passes."""
    out = []
    pending: List[dict] = list(cur.get("coach_handoff_pending") or [])
    nudges: List[dict] = []
    capped: List[dict] = []
    for ln in read_new(home / "coach" / "log.jsonl", cur, "coach", now):
        if '"fixloop_card"' not in ln and '"reply_nudge' not in ln and '"report_only_confirm_capped"' not in ln:
            continue
        try:
            r = json.loads(ln)
        except Exception:
            continue
        at = float(r.get("ts") or now)
        if at < now - BACKFILL_S:
            continue
        if r.get("event") == "report_only_confirm_capped":
            capped.append(r)
            continue
        rule = r.get("rule")
        if r.get("event") == "reply_nudge" and rule:
            nudges.append(r)
        if r.get("event") == "fixloop_card":
            issue = str(r.get("issue") or "a fix that keeps failing")
            out.append({"kind": "coach", "component": "agent", "at": at, "session_id": r.get("session_id") or "",
                        "symptom": f"claimed fix failed {r.get('n') or 2}x: {issue[:120]}",
                        "details": f"coach fix_loop opened review card {r.get('task_id')} after the "
                                   f"{r.get('n') or 2}nd confirmed failure: {issue}"[:1500],
                        "fp": fp("agent", "fixloop", r.get("session_id") or "", issue[:80])})
        elif r.get("event") == "reply_nudge" and rule in OUTCOME_RULES and (rule == "handoff" or r.get("outcome")):
            pending.append({**{k: r.get(k) for k in ("session_id", "rule", "p", "evidence", "draft_id")}, "ts": at})
        elif r.get("event") == "reply_nudge_outcome" and rule in OUTCOME_RULES:
            n = next((n for n in pending if _outcome_matches(n, r)), None)
            if n is None:
                continue
            pending.remove(n)
            if r.get("resolved") is not True:  # only an explicit "resolved" clears it
                still = "still handed off" if rule == "handoff" else f"still failed {rule}"
                why = f"the revised reply {still} (outcome p={r.get('p')})"
                rec = {**n, "reply": r.get("reply") or ""}
                if unsent_reply(home, str(n.get("session_id") or ""), rec["reply"]):
                    out.append(_unsent_draft_signal(rec, why))  # a fork's text: route to the coach, not the agent
                else:
                    out.append(_handoff_signal(rec, why))
    keep = []
    for n in pending:
        if now - float(n.get("ts") or 0) > HANDOFF_GRACE_S:
            if _turn_lost(home, n):
                # agent-fb58ec3a8f: the turn died after the nudge (gateway restart), so no final reply ran and no
                # outcome can come. Gateway/drain signals own that restart; it is not an agent that ignored a nudge.
                _coach_log(home, {"ts": now, "event": "nudge_outcome_lost", "reason": "interrupted",
                                  "session_id": n.get("session_id") or "", "rule": n.get("rule") or "handoff",
                                  "nudge_ts": n.get("ts"), "draft_id": n.get("draft_id")})
                continue
            out.append(_handoff_signal({**n, "no_outcome": True},
                                       f"no outcome logged within {HANDOFF_GRACE_S // 60} min of the nudge"))
        else:
            keep.append(n)
    if keep:
        cur["coach_handoff_pending"] = keep
    else:
        cur.pop("coach_handoff_pending", None)
    out += template_holds(cur, nudges, now, host)
    out += confirm_cap_check(cur, capped, now, host)
    return out


def collect(home: Path, cur: dict, host: str, now: Optional[float] = None) -> List[dict]:
    now = now or time.time()
    out = []
    for kind, fn in (("errors", lambda: collect_errors(home, cur, now, host)),
                     ("worker", lambda: collect_worker(home, cur, now, host)),
                     ("cron", lambda: collect_cron(home, cur, now, host)),
                     ("kanban", lambda: collect_kanban(home, cur, now, host)),
                     ("gateway", lambda: collect_gateway(home, cur, now, host)),
                     ("coach", lambda: collect_coach(home, cur, now, host))):
        try:
            sigs = fn()
        except Exception as exc:  # one broken source never stops the others
            sigs = [{"kind": "collector", "component": "plugin", "at": now,
                     "symptom": f"self-heal signal collector '{kind}' crashed: {type(exc).__name__}",
                     "details": f"{type(exc).__name__}: {exc}"[:600], "fp": fp("plugin", "collector", kind)}]
        by_fp: Dict[str, dict] = {}
        for s in sigs:  # one signal per fingerprint per pass (the latest), then the cap
            s.setdefault("count", 1)
            if s["fp"] in by_fp:
                s["count"] = by_fp[s["fp"]]["count"] + 1
            by_fp[s["fp"]] = s
        out += sorted(by_fp.values(), key=lambda s: s["at"])[-MAX_PER_KIND:]
    for s in out:
        s["host"] = host
    return out


def to_defect(s: dict) -> dict:
    """Raw signal -> self_heal.file_defect input."""
    d = {"component": s.get("component") or "other", "symptom": s.get("symptom") or s.get("kind"),
         "source": "signal", "kind": s.get("kind"), "details": s.get("details") or "", "host": s.get("host"),
         "at": s.get("at"), "fingerprint": s.get("fp"), "session_id": s.get("session_id") or None,
         "quote": s.get("quote") or ""}
    if (s.get("count") or 1) > 1:
        d["details"] = f"(seen {s['count']}x this pass)\n" + d["details"]
    for k in ("profile", "monitor", "kanban", "kanban_hold", "comment_open_card"):
        if s.get(k):
            d[k] = s[k]
    return d


# ── state ────────────────────────────────────────────────────────────────────────────────────────────────────

def coach_dir(home: Path) -> Path:
    return home / "coach"


def load_cursor(home: Path) -> dict:
    try:
        return json.loads((coach_dir(home) / "signals-cursor.json").read_text())
    except Exception:
        return {}


def save_cursor(home: Path, cur: dict) -> None:
    p = coach_dir(home) / "signals-cursor.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(cur, indent=1))
    tmp.replace(p)


def config(home: Path) -> dict:
    try:
        return json.loads((coach_dir(home) / "selfheal-config.json").read_text())
    except Exception:
        return {}


def short_host() -> str:
    return os.uname().nodename.split(".")[0]


def _self_heal():
    import importlib.util
    spec = importlib.util.spec_from_file_location("_coach_self_heal_signals", SELF_DIR / "self_heal.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def drain_spool(home: Path) -> List[dict]:
    spool = coach_dir(home) / "signals" / "spool.jsonl"
    offp = coach_dir(home) / "signals" / "spool.offset"
    if not spool.exists():
        return []
    data = spool.read_bytes()
    # Only newline-terminated lines are complete: a concurrent `ingest` may be
    # mid-write, and a partial last line must be left for the next sweep, not
    # dropped and skipped by the offset (the forwarder's cursor already moved).
    lines = data[:data.rfind(b"\n") + 1].decode("utf-8", "replace").split("\n")[:-1]
    try:
        off = int(offp.read_text().strip() or 0)
    except Exception:
        off = 0
    if off > len(lines):
        off = 0
    out = []
    for ln in lines[off:]:
        try:
            s = json.loads(ln)
        except Exception:
            continue
        if isinstance(s, dict) and s.get("fp") and s.get("kind") in KINDS + ("collector",):
            out.append(s)
    offp.write_text(str(len(lines)))
    return out


def sweep(home: Path, host: str, m: Optional[str] = None, filer: Optional[Callable] = None,
          now: Optional[float] = None) -> List[dict]:
    """Hub pass: local signals + forwarded ones -> file_defect. Returns [{signal, result}]."""
    now = now or time.time()
    cur = load_cursor(home)
    sigs = collect(home, cur, host, now) + drain_spool(home) + silent_forwarders(home, now)
    if filer is None:
        sh = _self_heal()
        filer = lambda d: sh.file_defect(d, m=m)  # noqa: E731
    out = []
    for s in sigs:
        try:
            res = filer(to_defect(s))
        except Exception as exc:
            res = {"action": "error", "why": f"{type(exc).__name__}: {exc}"[:300]}
        out.append({"signal": s, "result": res})
    save_cursor(home, cur)  # after filing: a crash mid-pass re-reads the same lines (file_defect dedupes)
    return out


def ssh_argv(cfg: dict) -> List[str]:
    """Same as self_heal.hub_ssh_argv (kept local so the Mac forwarder needs only this file): pin the hub key with
    IdentitiesOnly, since launchd's default identities are not the shell's and the hub refuses them preauth."""
    argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
    kp = Path(os.path.expanduser(cfg.get("hub_ssh_key") or "~/.ssh/id_ed25519"))
    if cfg.get("hub_ssh_key") or kp.exists():
        argv += ["-i", str(kp), "-o", "IdentitiesOnly=yes"]
    return argv


def forward(home: Path, host: str, hub: str, runner: Optional[Callable] = None, now: Optional[float] = None,
            cfg: Optional[dict] = None) -> dict:
    """Mac/mini: ship new raw signals to the hub. The cursor only moves when the hub accepted them. Every pass sends
    a heartbeat line first (even with no signals), so the hub knows the forwarder is alive (signals/hosts.json) and
    its sweep flags a host whose forwarder went silent."""
    cur = load_cursor(home)
    sigs = collect(home, cur, host, now)
    beat = {"kind": "heartbeat", "host": host, "at": now or time.time(), "n": len(sigs)}
    payload = "".join(json.dumps(s) + "\n" for s in [beat] + sigs)
    run = runner or (lambda argv, stdin: subprocess.run(argv, input=stdin, capture_output=True, text=True, timeout=90))
    try:
        r = run(ssh_argv(cfg if cfg is not None else config(home)) + [hub, HUB_CMD], payload)
        ok, detail = r.returncode == 0, (r.stdout or r.stderr or "").strip()[-200:]
    except Exception as exc:
        ok, detail = False, f"{type(exc).__name__}: {exc}"[:200]
    if ok:
        save_cursor(home, cur)
    return {"ok": ok, "sent": len(sigs) if ok else 0, "pending": 0 if ok else len(sigs), "detail": detail}


SILENT_S = 6 * 3600              # a forwarder (launchd, every 30 min) quiet this long is itself a defect


def ingest(home: Path, text: str, now: Optional[float] = None) -> int:
    spool = coach_dir(home) / "signals" / "spool.jsonl"
    spool.parent.mkdir(parents=True, exist_ok=True)
    now = now or time.time()
    n = 0
    beats = {}
    with spool.open("a") as f:
        for ln in text.splitlines():
            try:
                s = json.loads(ln)
            except Exception:
                continue
            if isinstance(s, dict) and s.get("kind") == "heartbeat" and s.get("host"):
                beats[str(s["host"])[:40]] = {"at": now, "n": s.get("n")}
                continue
            if not isinstance(s, dict) or not s.get("fp") or s.get("kind") not in KINDS + ("collector",):
                continue
            s["received_at"] = now
            f.write(json.dumps(s) + "\n")
            n += 1
    if beats:
        hp = coach_dir(home) / "signals" / "hosts.json"
        try:
            hosts = json.loads(hp.read_text())
        except Exception:
            hosts = {}
        hosts.update(beats)
        tmp = hp.with_suffix(".tmp")
        tmp.write_text(json.dumps(hosts, indent=1))
        tmp.replace(hp)
    return n


def silent_forwarders(home: Path, now: Optional[float] = None) -> List[dict]:
    """Hosts that forwarded before but not in SILENT_S: the forwarder (or the host's feed job / ssh) broke."""
    now = now or time.time()
    try:
        hosts = json.loads((coach_dir(home) / "signals" / "hosts.json").read_text())
    except Exception:
        return []
    out = []
    for h, v in sorted(hosts.items()):
        age = now - float(v.get("at") or 0)
        if age > SILENT_S:
            out.append({"kind": "collector", "component": "plugin", "at": now, "host": h,
                        "symptom": f"self-heal signal forwarder on {h} has not reported for {age / 3600:.0f} h",
                        "details": f"last heartbeat {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(v.get('at') or 0))}"
                                   f"; the forwarder rides on that host's launchd coach feed (coach_feed.py, every "
                                   f"30 min) and ssh to the hub",
                        "fp": fp("plugin", "forwarder-silent", h)})
    return out


# ── monitors: a propose-only monitor files its finding to the lane instead of posting a bare Do-it card ──────

def monitor_defect(m: dict, host: Optional[str] = None) -> dict:
    """{monitor, key, title, body, prompt?, component?} -> file_defect input. The fingerprint is the monitor's own
    dedupe key (e.g. worktree-leaks:<host>), so a daily re-report of the same finding dedupes in the lane. The old
    Do-it prompt rides along as the monitor's suggested first step; the investigation card turns the finding into
    the What happened / Why / Plan proposal."""
    key = m.get("key") or m.get("title") or "monitor"
    comp = m.get("component") or "agent"
    body = home_tilde(m.get("body") or "")
    hint = home_tilde(m.get("prompt") or "")
    details = (body[:1100] + (f"\n\nMonitor's own suggested next step: {hint}" if hint else ""))[:1500]
    s = {"kind": "monitor", "component": comp, "at": time.time(), "host": host or short_host(),
         "symptom": m.get("title") or key, "details": details,
         "fp": fp(comp, "monitor", key), "monitor": m.get("monitor") or key.split(":")[0]}
    return to_defect(s)


def lane_owns(res: dict) -> bool:
    """True when the self-heal lane took the finding (filed, forwarded and filed on the hub, or already open there),
    so the monitor must not also post its old Do-it card. Shadow (would_file), off, and errors -> False."""
    a = (res or {}).get("action")
    if a == "forwarded":
        return lane_owns(res.get("hub_result") or {})
    if a == "filed":
        return True
    return a == "skip" and "COACH_SELFHEAL=off" not in str(res.get("why"))


def route_monitor(m: dict, filer: Optional[Callable] = None) -> dict:
    d = monitor_defect(m)
    try:
        res = (filer or _self_heal().file_defect)(d)
    except Exception as exc:
        res = {"action": "error", "why": f"{type(exc).__name__}: {exc}"[:300]}
    return {"owned": lane_owns(res), "fingerprint": d["fingerprint"], "result": res}


# ── CLI ──────────────────────────────────────────────────────────────────────────────────────────────────────

def _summ(res: List[dict]) -> List[str]:
    lines = []
    for r in res:
        a = r["result"].get("action")
        if a == "skip":
            continue
        s = r["signal"]
        lines.append(f"{a}: [{s.get('host')}/{s.get('kind')}] {s.get('fp')} {s.get('symptom', '')[:140]}"
                     + (f" -> {r['result'].get('card')}" if r["result"].get("card") else "")
                     + (f" ({r['result'].get('why')})" if a == "error" else ""))
    return lines


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="signals.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("--home", default=os.environ.get("HERMES_HOME") or str(Path.home() / ".hermes"))
    ap.add_argument("--host", default=None, help="host label (default: the short hostname)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sweep")
    s.add_argument("--mode", choices=("shadow", "live"))
    s.add_argument("--verbose", action="store_true", help="also print skipped signals")
    c = sub.add_parser("collect")
    c.add_argument("--dry-run", action="store_true", help="do not advance the cursor")
    f = sub.add_parser("forward")
    f.add_argument("--hub", help="ssh target (default: hub_ssh in <home>/coach/selfheal-config.json)")
    sub.add_parser("ingest")
    sub.add_parser("route-monitor")
    a = ap.parse_args(argv)
    home = Path(a.home).expanduser()
    host = a.host or short_host()
    if a.cmd == "sweep":
        res = sweep(home, host, m=a.mode)
        for ln in _summ(res) if not a.verbose else [json.dumps(r)[:400] for r in res]:
            print(ln)
    elif a.cmd == "collect":
        cur = load_cursor(home)
        for sg in collect(home, cur, host):
            print(json.dumps(sg))
        if not a.dry_run:
            save_cursor(home, cur)
    elif a.cmd == "forward":
        cfg = config(home)
        hub = a.hub or cfg.get("hub_ssh")
        # Every outcome prints one line: the feed records it (<host>/feed-status.json in the coach data repo), so
        # the hub can tell "no config yet" from "ssh failing" from "working" without a shell on this host.
        if cfg.get("hub_host") and cfg.get("hub_host") == short_host():
            print("signals: this host is the hub (its own cron sweeps locally)")
            return 0
        if not hub:
            print(f"signals: no hub configured (<home>/coach/selfheal-config.json missing or has no hub_ssh)")
            return 0
        r = forward(home, host, hub, cfg=cfg)
        if not r["ok"]:
            print(f"signals forward to {hub} failed ({r['pending']} pending): {r['detail']}", file=sys.stderr)
            return 1
        print(f"signals: forwarded {r['sent']} to {hub}" if r["sent"] else f"signals: heartbeat ok to {hub}")
    elif a.cmd == "ingest":
        print(json.dumps({"ingested": ingest(home, sys.stdin.read())}))
    elif a.cmd == "route-monitor":
        print(json.dumps(route_monitor(json.loads(sys.stdin.read()))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
