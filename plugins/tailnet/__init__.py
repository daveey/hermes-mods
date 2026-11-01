"""tailnet — run commands on the owner's reachable tailnet machines, with credentials only from the token broker.

Tools (gateway-side: they run in the gateway process, never inside an agent sandbox):

* ``tailnet_hosts()``                         the inventory (name, os, notes, reachability); no credentials
* ``tailnet_exec(host, command, reason)``     run a shell command on a host over ssh
* ``tailnet_copy(host, direction, ...)``      scp a file to/from a staging dir on the gateway

Credentials: every call is ``token-broker exec --scope <scope> -- python runner.py``. The broker puts the ssh key
in the *runner's* environment only; the runner writes it to a 0600 tmpfs file, runs ssh with a scrubbed
environment, deletes the file, and returns capped, scrubbed JSON. The key never enters the model context, the
agent sandbox, a log, or a tool result.

Gate (decided by the gateway-bound session identity, not by anything the model passes): the owner's own Slack DM
turn uses the auto-tier scope and runs now. Any other turn (channel thread, another user, cron, CLI) opens an
approval-tier broker request (grant=once) that DMs the owner host + command + requester; the command runs only
when the model calls the tool again with that request_id after the owner approved, and it runs the command
stored with the request, not new model text. Only the same Slack user + channel (or the owner's DM) can collect
it. MAC_OPS_* identity is honoured only outside a gateway process. This plugin never approves anything. The one
exception to the request is a profile's own ``auto_hosts`` opt-in: its local turns (no chat user) run on the auto
scope on those hosts, audited with ``approval: auto_hosts``.

Approval text (shown in full, never cut; refused if over the broker's 1000-char limit): gateway-bound requester,
host and a sha256 of the exact job first, then the model-written reason/command JSON-quoted so they can't fake a
field. Non-owner uploads are pinned to a read-only content-addressed copy whose hash the runner re-checks.

Config (profile config.yaml)::

    tailnet:
      enabled: true
      hosts_file: ~/.hermes/tailnet/hosts.yaml     # default: <hermes home>/tailnet/hosts.yaml
      broker: ~/.local/bin/token-broker
      auto_hosts: [mini]     # this profile's local turns (CLI, Desktop, cron; never a chat user) skip approval here

hosts.yaml::

    owners: [U0EXAMPLE1]                  # Slack user ids whose own DM turns are "owner" turns
    scope: user/tailnet-ssh-key            # auto tier (owner turns)
    approval_scope: user/tailnet-ssh-key-approval   # approval tier, grant=once (everyone else)
    hosts:
      mini: {addr: 192.0.2.10, user: me, os: macOS, notes: "..."}
      den: {addr: den.local, user: me, via: mini}   # reached by hopping through `mini`
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

TOOLSET = "tailnet"
HERE = Path(__file__).parent
RUNNER = HERE / "runner.py"
MAX_OUT = 8000            # chars of stdout / stderr returned per call
MAX_CMD = 4000
MAX_REASON = 1000         # token broker VendRequest.reason max_length; the approval DM shows it in full
MAX_COPY_BYTES = 25 * 1024 * 1024
EXEC_TIMEOUT_S = 120
MAX_TIMEOUT_S = 600
PENDING_TTL_S = 3600
HOST_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,40}$")
ADDR_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,100}$")
USER_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,40}$")
NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,100}$")
REQ_ID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f-]{27}|req_[A-Za-z0-9_-]{6,60}|[A-Za-z0-9_-]{8,64}")
AUTO = "auto_hosts"       # audit value of `approval` for a turn local_auto let through
LOCAL_USER_RE = re.compile(r"^(?:uid|sid):[A-Za-z0-9_-]+$")  # the gateway's same-user local peer ids
KEY_BLOCK = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)", re.S)


# ---------------------------------------------------------------- config / inventory

def _home() -> Path:
    try:
        from hermes_constants import get_hermes_home  # type: ignore
        return Path(get_hermes_home())
    except Exception:
        return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")


def _config() -> dict:
    try:
        from hermes_cli.config import load_config  # type: ignore
        return load_config() or {}
    except Exception:
        return {}


def settings() -> dict:
    return _config().get("tailnet") or {}


def _read_yaml(path: Path) -> dict:
    if path.suffix == ".json":
        return json.loads(path.read_text())
    try:
        from utils import load_yaml_file_readonly  # type: ignore
        return dict(load_yaml_file_readonly(path) or {})
    except ImportError:
        pass
    import yaml  # type: ignore
    return yaml.safe_load(path.read_text()) or {}


def hosts_file() -> Path:
    s = settings()
    p = os.environ.get("TAILNET_HOSTS_FILE") or s.get("hosts_file")
    if p:
        return Path(os.path.expanduser(str(p)))
    return _home() / "tailnet" / "hosts.yaml"


class Inventory:
    def __init__(self, raw: dict):
        self.raw = raw or {}
        self.scope = str(self.raw.get("scope") or "user/tailnet-ssh-key")
        self.approval_scope = str(self.raw.get("approval_scope") or "user/tailnet-ssh-key-approval")
        o = self.raw.get("owners") or []
        if isinstance(o, str):
            o = [x for x in re.split(r"[,\s]+", o) if x]
        self.owners = frozenset(str(x).strip() for x in o if str(x).strip())
        self.hosts: dict = {}
        for name, h in (self.raw.get("hosts") or {}).items():
            n = str(name).strip().lower()
            if not (HOST_RE.match(n) and isinstance(h, dict)):
                continue
            addr, user = str(h.get("addr") or ""), str(h.get("user") or "")
            if not (ADDR_RE.match(addr) and USER_RE.match(user)):
                continue
            self.hosts[n] = {"addr": addr, "user": user, "os": str(h.get("os") or ""),
                             "notes": str(h.get("notes") or ""), "via": str(h.get("via") or "").lower() or None}
        for n, h in self.hosts.items():  # a hop must exist, be direct, and not loop
            v = h["via"]
            if v and (v not in self.hosts or self.hosts[v]["via"] or v == n):
                h["via"] = "!invalid"

    def get(self, name: str) -> Optional[dict]:
        h = self.hosts.get(str(name or "").strip().lower())
        return h if h and h["via"] != "!invalid" else None


def load_inventory() -> Inventory:
    return Inventory(_read_yaml(hosts_file()))


# ---------------------------------------------------------------- who is asking

def _session(name: str) -> str:
    try:
        from gateway.session_context import get_session_env  # type: ignore
        return get_session_env("HERMES_SESSION_" + name, "") or ""
    except Exception:
        return os.environ.get("HERMES_SESSION_" + name, "")


def _gateway_session_bound() -> bool:
    """True in a gateway process (it binds per-message session vars) or when this turn has a session platform."""
    try:
        from gateway.session_context import session_context_engaged  # type: ignore
        if session_context_engaged():
            return True
    except Exception:
        pass
    return bool(_session("PLATFORM"))


def requester(inv: Inventory, extra_owners: frozenset = frozenset()) -> dict:
    """Who this turn is, from gateway-bound identity. owner=True only for the owner's own Slack DM."""
    owners = inv.owners | extra_owners
    # The owner-only mac-ops profile gets its turn from the gateway via MAC_OPS_* (a Slack-verified owner DM),
    # set on the `hermes chat` subprocess it spawns. Never honour it inside a gateway process that binds
    # per-message sessions: there os.environ is shared by every concurrent turn, so a stray MAC_OPS_* would make
    # a stranger's channel turn an owner turn.
    ch, th, ow = (os.environ.get("MAC_OPS_CHANNEL", ""), os.environ.get("MAC_OPS_THREAD", ""),
                  os.environ.get("MAC_OPS_OWNER", ""))
    if ch.startswith("D") and th and ow and ow in owners and not _gateway_session_bound():
        return {"owner": True, "user": ow, "channel": ch, "thread": th, "via": "mac-ops",
                "bot_home": os.environ.get("MAC_OPS_BOT_HOME", "")}
    platform, ctype = _session("PLATFORM"), _session("CHAT_TYPE")
    user, chat, thread = _session("USER_ID"), _session("CHAT_ID"), _session("THREAD_ID")
    name = _session("USER_NAME")
    is_owner = (platform.lower() == "slack" and ctype.lower() in ("dm", "im") and user in owners
                and chat.startswith("D"))
    return {"owner": bool(is_owner), "user": user or "", "channel": chat, "thread": thread, "name": name,
            "platform": platform, "via": "session", "bot_home": ""}


def local_auto(req: dict, host: str) -> bool:
    """True when this profile's ``tailnet.auto_hosts`` lists the host and the turn has no chat user behind it: no
    identity at all (CLI, cron) or the gateway's local peer in a local chat (Desktop). Read per profile, so it never
    reaches a profile that faces Slack, and a Slack id or channel never matches."""
    hosts = {str(h).strip().lower() for h in settings().get("auto_hosts") or []}
    user, chat = req.get("user") or "", req.get("channel") or ""
    return host in hosts and ((not user and not chat) or bool(LOCAL_USER_RE.match(user) and chat.startswith("local-")))


def who(req: dict) -> str:
    if req.get("user"):
        # the id is the gateway-bound identity; the display name is self-set, so it is quoted and comes second
        name = f" (name {_q(req['name'][:40])})" if req.get("name") else ""
        return f"slack:{req['user']}{name} in {req.get('channel') or '?'}"
    return "no slack session (cron/cli/other)"


# ---------------------------------------------------------------- scrubbing, audit, thread line

def scrub(text: str) -> str:
    """Remove private-key material (and any literal secret in the env of this process) from text."""
    text = KEY_BLOCK.sub("[redacted key]", text or "")
    for k in ("TAILNET_SSH_KEY",):
        v = os.environ.get(k)
        if v and len(v) > 20:
            text = text.replace(v, "[redacted]")
    return text


def cap(text: str, n: int = MAX_OUT) -> tuple:
    text = scrub(text)
    return (text[:n], True) if len(text) > n else (text, False)


def audit_path() -> Path:
    return _home() / "tailnet" / "audit.jsonl"


def audit(rec: dict) -> None:
    rec = dict(rec, ts=time.strftime("%Y-%m-%dT%H:%M:%S%z"))
    for k in ("command", "reason"):
        if k in rec:
            rec[k] = scrub(str(rec[k]))[:600]
    try:
        p = audit_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a") as f:
            f.write(json.dumps(rec) + "\n")
    except OSError as exc:
        logger.warning("tailnet audit write failed: %s", exc)


def _bot_token(req: dict) -> str:
    for home in (req.get("bot_home"), str(_home())):
        if not home:
            continue
        try:
            for line in (Path(home) / ".env").read_text().splitlines():
                if line.startswith("SLACK_BOT_TOKEN="):
                    return line.split("=", 1)[1].strip().strip("'\"")
        except OSError:
            continue
    return ""


def mrkdwn_escape(text: str) -> str:
    """Slack treats only & < > as markup: escaped, agent text can't @channel, mention or hide a link."""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def slack_post(req: dict, text: str) -> None:
    tok = _bot_token(req)
    if not (tok and req.get("channel")):
        return
    params = {"channel": req["channel"], "text": text[:500]}
    if req.get("thread"):
        params["thread_ts"] = req["thread"]
    r = urllib.request.Request("https://slack.com/api/chat.postMessage", data=urllib.parse.urlencode(params).encode(),
                               headers={"Authorization": "Bearer " + tok})
    urllib.request.urlopen(r, timeout=15).read()


POST: Callable[[dict, str], None] = slack_post


def thread_line(req: dict, text: str) -> None:
    try:
        POST(req, mrkdwn_escape(text))
    except Exception as exc:  # the audit jsonl is the record; the thread line is a courtesy
        logger.info("tailnet thread audit not posted: %s", exc)


# ---------------------------------------------------------------- broker

def broker_bin() -> str:
    b = settings().get("broker")
    if b:
        return os.path.expanduser(str(b))
    return shutil.which("token-broker") or os.path.expanduser("~/.local/bin/token-broker")


def _q(text: str) -> str:
    """JSON-quote agent-written text: newlines, quotes and non-ASCII (bidi overrides etc.) come out escaped,
    so it can't fake another field of the approval text."""
    return json.dumps(str(text), ensure_ascii=True)


def fingerprint(payload: dict) -> str:
    """sha256 prefix of the exact job that will run (target, mode, command / paths / content hash)."""
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


def _reason(kind: str, host: str, what: str, req: dict, why: str, fp: str, extra: str = "") -> str:
    """Approval/vend text. Gateway-bound fields (requester, host, fingerprint) come first and unquoted; everything
    the model wrote is JSON-quoted after them. Callers must check the length; it is never silently cut."""
    r = f"tailnet {kind} | requester {who(req)} | host {host} | sha256 {fp} | why {_q(why)} | command {_q(what)}"
    return r + (f" | {extra}" if extra else "")


def run_via_broker(scope: str, reason: str, job: dict, *, request_id: str = "", timeout: int) -> dict:
    """token-broker exec --scope S -- runner. The job (no credentials) goes in on stdin; JSON comes back."""
    argv = [broker_bin(), "exec", "--scope", scope, "--reason", reason]
    if request_id:
        argv += ["--request-id", request_id]
    argv += ["--", sys.executable, str(RUNNER)]
    env = {k: v for k, v in os.environ.items() if k != "TAILNET_SSH_KEY"}
    try:
        p = subprocess.run(argv, input=json.dumps(job), capture_output=True, text=True, timeout=timeout + 45, env=env)
    except subprocess.TimeoutExpired:
        return {"ran": False, "error": "broker/ssh call timed out"}
    except OSError as exc:
        return {"ran": False, "error": f"cannot start token-broker: {exc}"}
    lines = [ln for ln in (p.stdout or "").strip().splitlines() if ln.strip().startswith("{")]
    if lines:
        try:
            out = json.loads(lines[-1])
            if isinstance(out, dict) and "rc" in out:
                out.setdefault("ran", True)
                return out
        except ValueError:
            pass
    return {"ran": False, "error": "no result from runner (broker refused or not approved yet)",
            "detail": scrub((p.stderr or p.stdout or "")[-400:]), "broker_rc": p.returncode}


def open_request(scope: str, reason: str) -> dict:
    """Non-blocking approval request. Returns {request_id} or {error}. Never approves."""
    try:
        p = subprocess.run([broker_bin(), "request", "--scope", scope, "--reason", reason],
                           capture_output=True, text=True, timeout=60, env={k: v for k, v in os.environ.items()
                                                                           if k != "TAILNET_SSH_KEY"})
    except (subprocess.TimeoutExpired, OSError) as exc:
        return {"error": f"cannot open approval request: {exc}"}
    text = (p.stdout or "") + "\n" + (p.stderr or "")
    rid = ""
    for src in (p.stdout or "", p.stderr or ""):
        try:
            j = json.loads(src)
            rid = str(j.get("request_id") or j.get("id") or "")
        except ValueError:
            m = re.search(r"request[_ ]id\W+([A-Za-z0-9_-]{6,64})", src, re.I)
            rid = m.group(1) if m else ""
        if rid:
            break
    if p.returncode != 0 or not rid:
        return {"error": "approval request failed: " + scrub(text)[-300:]}
    return {"request_id": rid}


# ---------------------------------------------------------------- pending approvals

def _pending_path() -> Path:
    return _home() / "tailnet" / "pending.json"


def _pending_load() -> dict:
    try:
        d = json.loads(_pending_path().read_text())
    except (OSError, ValueError):
        d = {}
    now = time.time()
    return {k: v for k, v in d.items() if isinstance(v, dict) and now - v.get("at", 0) < PENDING_TTL_S}


def _pending_save(d: dict) -> None:
    p = _pending_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".pending.{uuid.uuid4().hex}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps(d))
    os.replace(tmp, p)


def _same_requester(stored: dict, req: dict) -> bool:
    """An approved request's output goes back only to the turn that asked for it (same Slack user + channel), or
    to the owner's own DM. Anyone else who learns the request_id (it is posted in the requester's thread) gets
    nothing."""
    if req.get("owner"):
        return True
    ident = stored.get("ident") or {}
    # (cron/CLI turns have no Slack identity: they match only each other, and no Slack turn has an empty one)
    return "ident" in stored and all((ident.get(k) or "") == (req.get(k) or "") for k in ("user", "channel", "platform"))


# ---------------------------------------------------------------- tools

def _jerr(msg: str, **kw: Any) -> str:
    return json.dumps({"ok": False, "error": msg, **kw})


def staging_dir() -> Path:
    d = _home() / "tailnet" / "files"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _job_base(inv: Inventory, host: str, timeout: int) -> dict:
    h = inv.get(host) or {}
    job: dict = {"target": {"addr": h["addr"], "user": h["user"]}, "timeout": timeout, "cap": MAX_OUT,
           "known_hosts": str(_home() / "tailnet" / "known_hosts")}
    if h["via"]:
        v = inv.hosts[h["via"]]
        job["via"] = {"addr": v["addr"], "user": v["user"]}
    return job


def _finish(kind: str, host: str, what: str, req: dict, why: str, scope: str, res: dict, approval: str = "") -> str:
    rc = res.get("rc")
    out, t1 = cap(str(res.get("stdout") or ""))
    err, t2 = cap(str(res.get("stderr") or ""))
    rec = {"kind": kind, "host": host, "command": what, "requester": who(req), "owner_turn": req["owner"],
           "scope": scope, "rc": rc, "ran": bool(res.get("ran")), "reason": why, "approval": approval or None}
    if not res.get("ran"):
        rec["error"] = scrub(str(res.get("error") or ""))[:300]
    audit(rec)
    status = "rc=%s" % rc if res.get("ran") else "did not run"
    thread_line(req, f"tailnet {kind} on {host}: `{what[:160]}` -> {status} "
                     f"({'owner DM' if req['owner'] else approval if approval == AUTO else 'approved'}; scope {scope})")
    if not res.get("ran"):
        return json.dumps({"ok": False, "ran": False, "error": res.get("error"), "detail": res.get("detail")})
    return json.dumps({"ok": rc == 0, "ran": True, "host": host, "rc": rc, "stdout": out, "stderr": err,
                       "truncated": t1 or t2, "seconds": res.get("seconds")})


def _gate(inv: Inventory, req: dict, kind: str, host: str, what: str, why: str, payload: dict,
          args: dict, timeout: int, extra: str = "", prepare: Optional[Callable[[dict], dict]] = None) -> str:
    """Owner DM turn, or a local turn on an auto_hosts host -> run now on the auto scope. Otherwise open (or
    collect) a per-command approval.

    ``prepare`` (non-owner new requests only) may pin request-specific state into the payload, e.g. a
    content-addressed copy of a file to upload, before it is fingerprinted and shown to the owner."""
    rid = str(args.get("request_id") or "").strip()
    if prepare and not rid and not req["owner"]:
        try:
            payload = prepare(payload)
        except Exception as exc:
            return _jerr(f"cannot stage the request: {type(exc).__name__}", ran=False)
    auto = not rid and not req["owner"] and local_auto(req, host)
    if (req["owner"] or auto) and not rid:
        fp = fingerprint(payload)
        res = run_via_broker(inv.scope, _reason(kind, host, what, req, why, fp, extra)[:MAX_REASON], payload,
                             timeout=timeout)
        return _finish(kind, host, what, req, why, inv.scope, res, approval=AUTO if auto else "")
    pending = _pending_load()
    if rid:
        p = pending.get(rid)
        if not p:
            return _jerr("unknown or expired request_id; make a new request")
        if not _same_requester(p, req):
            audit({"kind": p.get("kind"), "host": p.get("host"), "command": p.get("what"), "requester": who(req),
                   "owner_turn": req["owner"], "ran": False, "approval": rid,
                   "error": f"collect refused: request belongs to {p.get('requester')}"})
            return _jerr("this request_id belongs to another requester or conversation; nothing ran. Only the "
                         "turn that asked for it can collect it.", ran=False)
        if p.get("fp") != fingerprint(p.get("payload") or {}) or f"sha256 {p.get('fp')} " not in p.get("reason", ""):
            audit({"kind": p.get("kind"), "host": p.get("host"), "command": p.get("what"), "requester": who(req),
                   "owner_turn": req["owner"], "ran": False, "approval": rid,
                   "error": "stored request does not match what was approved"})
            pending.pop(rid, None)
            _pending_save(pending)
            return _jerr("stored request no longer matches what the owner was shown; nothing ran. Make a new request.",
                         ran=False)
        # run exactly what was requested, for the requester that asked for it
        res = run_via_broker(inv.approval_scope, p["reason"], p["payload"], request_id=rid, timeout=p["timeout"])
        if res.get("ran") or res.get("blocked"):
            pending.pop(rid, None)
            _pending_save(pending)
        else:
            audit({"kind": kind, "host": p["host"], "command": p["what"], "requester": p["requester"],
                   "owner_turn": False, "scope": inv.approval_scope, "ran": False, "approval": rid,
                   "error": "not approved yet or denied"})
            return json.dumps({"ok": False, "ran": False, "status": "waiting_for_approval", "request_id": rid,
                               "message": "Not approved yet (or denied). Nothing was run. Tell the requester it is "
                                          "waiting on the owner, and do not retry in a loop.",
                               "detail": res.get("detail")})
        return _finish(p["kind"], p["host"], p["what"], {"owner": False, "user": req["user"], "channel": req["channel"],
                                                         "thread": req["thread"]}, p["why"], inv.approval_scope, res,
                       approval=rid)
    fp = fingerprint(payload)
    reason = _reason(kind, host, what, req, why, fp, extra)
    if len(reason) > MAX_REASON:
        return _jerr(f"command too long to show the owner in full ({len(reason)} chars of approval text, max "
                     f"{MAX_REASON}); nothing was requested. Shorten it (e.g. put a script on the host with "
                     f"tailnet_copy first, then run that).", ran=False)
    r = open_request(inv.approval_scope, reason)
    if r.get("error"):
        audit({"kind": kind, "host": host, "command": what, "requester": who(req), "owner_turn": False,
               "ran": False, "error": r["error"]})
        return _jerr(r["error"], ran=False)
    rid = r["request_id"]
    pending[rid] = {"kind": kind, "host": host, "what": what, "why": why, "payload": payload, "timeout": timeout,
                    "reason": reason, "fp": fp, "requester": who(req), "at": time.time(),
                    "ident": {"user": req.get("user") or "", "channel": req.get("channel") or "",
                              "platform": req.get("platform") or ""}}
    _pending_save(pending)
    audit({"kind": kind, "host": host, "command": what, "requester": who(req), "owner_turn": False,
           "scope": inv.approval_scope, "ran": False, "approval": rid, "status": "approval requested"})
    thread_line(req, f"tailnet {kind} on {host}: `{what[:160]}` needs the owner's approval (request {rid}); "
                     f"nothing has run.")
    return json.dumps({"ok": False, "ran": False, "status": "approval_requested", "request_id": rid,
                       "message": "The owner got a Slack DM with the host, command and requester. Nothing has run. "
                                  "Tell the requester it is waiting for approval; after the owner approves, call "
                                  "this tool again with this request_id (and nothing else changed)."})


def _gc_pinned(d: Path) -> None:
    """Drop pinned upload copies no pending request can still use (older than the request TTL)."""
    cutoff = time.time() - PENDING_TTL_S - 60
    for f in d.iterdir():
        try:
            if f.is_file() and f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            pass


def _common(args: dict) -> tuple:
    try:
        inv = load_inventory()
    except Exception as exc:
        return None, None, "", _jerr(f"tailnet inventory unavailable: {type(exc).__name__}")
    host = str(args.get("host") or "").strip().lower()
    if not inv.get(host):
        return None, None, host, _jerr(f"unknown host {host!r}; known: {', '.join(sorted(inv.hosts)) or 'none'}")
    return inv, requester(inv), host, ""


def tool_hosts(args: dict, **_: Any) -> str:
    try:
        inv = load_inventory()
    except Exception as exc:
        return _jerr(f"tailnet inventory unavailable: {type(exc).__name__}")
    rows = [{"host": n, "os": h["os"], "via": h["via"], "notes": h["notes"]} for n, h in sorted(inv.hosts.items())
            if h["via"] != "!invalid"]
    return json.dumps({"ok": True, "hosts": rows})


def _timeout(args: dict) -> int:
    try:
        return max(5, min(int(args.get("timeout") or EXEC_TIMEOUT_S), MAX_TIMEOUT_S))
    except (TypeError, ValueError):
        return EXEC_TIMEOUT_S


def tool_exec(args: dict, **_: Any) -> str:
    inv, req, host, err = _common(args)
    if err:
        return err
    rid = str(args.get("request_id") or "").strip()
    command = str(args.get("command") or "")
    why = str(args.get("reason") or "").strip()
    if not rid:
        if not command.strip() or len(command) > MAX_CMD or "\x00" in command:
            return _jerr(f"command is required (at most {MAX_CMD} chars)")
        if len(why) < 5:
            return _jerr("reason is required (at least 5 characters): why this is being run")
    timeout = _timeout(args)
    payload = dict(_job_base(inv, host, timeout), mode="exec", command=command)
    return _gate(inv, req, "exec", host, command, why, payload, args, timeout)


def tool_copy(args: dict, **_: Any) -> str:
    inv, req, host, err = _common(args)
    if err:
        return err
    rid = str(args.get("request_id") or "").strip()
    direction = str(args.get("direction") or "").strip().lower()
    remote = str(args.get("remote_path") or "")
    name = str(args.get("local_name") or "")
    why = str(args.get("reason") or "").strip()
    content = args.get("content")
    if not rid:
        if direction not in ("put", "get"):
            return _jerr("direction must be put or get")
        if not remote or len(remote) > 500 or "\x00" in remote or "\n" in remote:
            return _jerr("remote_path is required (one line)")
        if not NAME_RE.match(name) or name in (".", ".."):
            return _jerr("local_name must be a plain file name in the gateway staging dir (no slashes)")
        if len(why) < 5:
            return _jerr("reason is required (at least 5 characters)")
    local = staging_dir() / (name or "unnamed")
    data = None
    if not rid and direction == "put":
        if content is not None:
            data = str(content).encode()
            if len(data) > 1024 * 1024:
                return _jerr("content over 1 MB; stage large files on the gateway host instead")
            # Only the owner's own turn writes the shared staging file. A non-owner's content goes only into its
            # pinned per-request copy: otherwise a stranger could replace staging/<name> and a later owner-DM
            # put of that name (auto, no approval DM) would ship the stranger's bytes.
            if req["owner"]:
                local.write_bytes(data)
        if data is None:
            if not local.is_file():
                return _jerr("put needs content, or a file already in the staging dir named local_name")
            if local.stat().st_size > MAX_COPY_BYTES:
                return _jerr("file over 25 MB")
    timeout = _timeout(args)
    payload: dict = dict(_job_base(inv, host, timeout), mode=direction, remote=remote, local=str(local),
                         max_bytes=MAX_COPY_BYTES)
    what = f"{direction} {remote} {'<-' if direction == 'put' else '->'} staging/{name}"
    extra, prepare = "", None
    if not rid and direction == "put":
        if data is None:
            data = local.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        payload["sha256"] = digest  # the runner refuses to upload anything else
        extra = f"content sha256 {digest[:16]} size {len(data)} bytes"
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = None
        extra += f" content {_q(text)}" if text is not None and len(_q(text)) <= 400 else " (content not shown)"

        def prepare(pl: dict, _data=data, _digest=digest) -> dict:
            # per-request, content-addressed, read-only copy: a later call reusing local_name can't swap it
            d = staging_dir() / ".pending"
            d.mkdir(mode=0o700, exist_ok=True)
            _gc_pinned(d)
            pinned = d / _digest
            if pinned.exists():
                os.utime(pinned)  # reused by a new request: keep it past that request's TTL
            else:
                tmp = d / f".{_digest}.{uuid.uuid4().hex}"
                tmp.write_bytes(_data)
                tmp.chmod(0o400)
                tmp.replace(pinned)
            if hashlib.sha256(pinned.read_bytes()).hexdigest() != _digest:
                raise ValueError("pinned copy does not match")
            return dict(pl, local=str(pinned))
    return _gate(inv, req, "copy", host, what, why, payload, args, timeout, extra=extra, prepare=prepare)


SCHEMAS = {
    "tailnet_hosts": ("List the owner's machines reachable over the tailnet (name, OS, notes). Use before "
                      "tailnet_exec. Lists names only; no credentials.", {}, [], tool_hosts),
    "tailnet_exec": ("Run a shell command on one of the owner's tailnet machines over ssh. Credentials come from the "
                     "token broker per call and never reach you. In the owner's own Slack DM it runs now. For anyone "
                     "else, or any other context (unless this profile lists the host in auto_hosts), it opens an approval request that DMs the owner the host, command "
                     "and requester and returns a request_id; nothing runs until the owner approves, then call again "
                     "with request_id. Use this instead of refusing or handing someone a command to paste.",
                     {"host": {"type": "string"}, "command": {"type": "string"},
                      "reason": {"type": "string", "description": "why this is being run (shown to the owner)"},
                      "timeout": {"type": "integer", "description": "seconds, default 120, max 600"},
                      "request_id": {"type": "string", "description": "approved request to collect; omit otherwise"}},
                     ["host"], tool_exec),
    "tailnet_copy": ("Copy a file to (put) or from (get) a tailnet machine. Local side is the gateway's staging "
                     "dir (local_name, a plain file name; or content= for a small text put). Same gate as "
                     "tailnet_exec.",
                     {"host": {"type": "string"}, "direction": {"type": "string", "enum": ["put", "get"]},
                      "remote_path": {"type": "string"}, "local_name": {"type": "string"},
                      "content": {"type": "string", "description": "put only: small text to write first"},
                      "reason": {"type": "string"}, "timeout": {"type": "integer"},
                      "request_id": {"type": "string"}}, ["host"], tool_copy),
}


def enabled() -> bool:
    return bool(settings().get("enabled", True))


def register(ctx) -> None:
    if not enabled():
        return
    for name, (desc, props, required, fn) in SCHEMAS.items():
        def handler(args, _fn=fn, **kw):
            try:
                return _fn(dict(args or {}), **kw)
            except Exception as exc:
                logger.warning("tailnet tool failed: %s", exc, exc_info=True)
                return _jerr(f"{type(exc).__name__}: {scrub(str(exc))[:200]}")
        schema = {"name": name, "description": desc,
                  "parameters": {"type": "object", "properties": props, "required": required}}
        ctx.register_tool(name=name, toolset=TOOLSET, schema=schema, handler=handler, description=desc,
                          check_fn=lambda: hosts_file().is_file())
