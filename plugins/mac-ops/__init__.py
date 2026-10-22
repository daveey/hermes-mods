"""mac-ops — owner-only tools to diagnose and repair the owner's Macs from a screenless gateway.

Tools (registered ONLY in the profile whose config says ``mac_ops.enabled: true``; ambient bot profiles never
enable this plugin, so they never have these tools or any use of the key):

* ``mac_diag(host)``            read-only health report from the Mac-side shim
* ``mac_fix(host, playbook)``   a named, vetted repair; logout/reboot run only after the owner replies yes in
                                the Slack thread (enforced here by reading the thread, not by trusting the model)
* ``mac_screen(host, op)``      look at / drive that Mac's screen through the OTHER Mac (works with a dead mouse
                                or a dead app); a screenshot is posted into the thread
* ``mac_reply(text)``           the only way this profile talks to the owner: posts into the owner's DM thread

Transport: ``ssh -i <key> <user>@<addr> <action...>`` into a forced-command shim that enforces policy and prints
one JSON object ``{ok, action, result|error, log_id}`` (exit 0 ok / 2 refused / 1 failed). Config (profile
config.yaml)::

    mac_ops:
      enabled: true
      key: ~/.ssh/id_mac_fixer
      hosts: {mbp: {addr: <tailscale ip>, user: <login>, peer: <name the OTHER Mac's shim uses for it>},
              mini: {addr: ..., user: ..., peer: ...}}    # peer defaults to the key (mbp, mini)

The gateway sets MAC_OPS_CHANNEL / MAC_OPS_THREAD / MAC_OPS_OWNER / MAC_OPS_BOT_HOME for the turn (from a
Slack-verified owner DM); the tools post only there.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shlex
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional

TOOLSET = "mac_ops"
PLAYBOOKS = ("kill-virtual-display", "restart-hermes-desktop", "restart-gateway", "reset-display-config",
             "restart-dock-finder", "logout", "reboot")
CONFIRM = frozenset({"logout", "reboot"})
SCREEN_OPS = ("shot", "click", "key", "type")
PENDING_TTL_S = 15 * 60
SSH_TIMEOUT_S = 120
MAX_OUT = 7000
YES = re.compile(r"^\s*(yes|y|yep|yeah|yup|confirm|confirmed|do it|go ahead|go for it|ok|okay|sure)\W*$", re.I)
NO = re.compile(r"^\s*(no|n|nope|cancel|stop|don'?t|abort|never mind|nevermind)\W*$", re.I)
KEY_RE = re.compile(r"^[A-Za-z0-9+_\-]{1,40}$")
PEER_RE = re.compile(r"^[A-Za-z0-9_\-]{1,40}$")


# ---------------------------------------------------------------- config / context

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
    return (_config().get("mac_ops") or {})


def enabled() -> bool:
    return bool(settings().get("enabled"))


def hosts() -> dict:
    return {str(k).lower(): v for k, v in (settings().get("hosts") or {}).items() if isinstance(v, dict)}


def owner_ids() -> frozenset:
    ids = ((_config().get("owner") or {}).get("slack_user_ids")) or []
    if isinstance(ids, str):
        ids = [x for x in re.split(r"[,\s]+", ids) if x]
    return frozenset(str(i).strip() for i in ids if str(i).strip())


def turn_context() -> dict:
    """Where this turn must answer. Set by the gateway for a verified owner DM; the model cannot change it."""
    ch, th, owner = (os.environ.get("MAC_OPS_CHANNEL", ""), os.environ.get("MAC_OPS_THREAD", ""),
                     os.environ.get("MAC_OPS_OWNER", ""))
    ok = bool(ch.startswith("D") and th and owner and owner in owner_ids())
    return {"ok": ok, "channel": ch, "thread": th, "owner": owner,
            "bot_home": os.environ.get("MAC_OPS_BOT_HOME", "")}


# ---------------------------------------------------------------- Slack (bot token read from the bot profile)

def _bot_token(bot_home: str) -> str:
    try:
        for line in (Path(bot_home) / ".env").read_text().splitlines():
            if line.startswith("SLACK_BOT_TOKEN="):
                return line.split("=", 1)[1].strip().strip("'\"")
    except OSError:
        pass
    return ""


def slack_api(ctx: dict, method: str, params: dict) -> dict:
    tok = _bot_token(ctx["bot_home"])
    if not tok:
        raise RuntimeError("no Slack bot token available")
    # Form-encoded: read methods such as conversations.replies reject a JSON body (invalid_arguments).
    form = urllib.parse.urlencode({k: (json.dumps(v) if isinstance(v, (dict, list)) else v)
                                   for k, v in params.items() if v is not None}).encode()
    req = urllib.request.Request("https://slack.com/api/" + method, data=form, headers={
        "Authorization": "Bearer " + tok, "Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=30) as r:
        out = json.load(r)
    if not out.get("ok"):
        raise RuntimeError(f"slack {method}: {out.get('error')}")
    return out


def slack_upload(ctx: dict, png: bytes, name: str, comment: str) -> dict:
    tok = _bot_token(ctx["bot_home"])
    form = urllib.parse.urlencode({"filename": name, "length": len(png)}).encode()
    req = urllib.request.Request("https://slack.com/api/files.getUploadURLExternal", data=form, headers={
        "Authorization": "Bearer " + tok, "Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=30) as r:
        up = json.load(r)
    if not up.get("ok"):
        raise RuntimeError(f"slack upload: {up.get('error')}")
    urllib.request.urlopen(urllib.request.Request(up["upload_url"], data=png, method="POST"), timeout=60).read()
    return slack_api(ctx, "files.completeUploadExternal", {
        "files": [{"id": up["file_id"], "title": name}], "channel_id": ctx["channel"],
        "thread_ts": ctx["thread"], "initial_comment": comment})


API: Callable[[dict, str, dict], dict] = slack_api
UPLOAD: Callable[[dict, bytes, str, str], dict] = slack_upload


def _jerr(msg: str, **kw: Any) -> str:
    return json.dumps({"ok": False, "error": msg, **kw})


# ---------------------------------------------------------------- ssh to the shim

def _clip(obj: Any) -> Any:
    s = json.dumps(obj)
    if len(s) <= MAX_OUT:
        return obj
    return {"truncated": True, "text": s[:MAX_OUT]}


def ssh_argv(host: str, action: list) -> list:
    h = hosts().get(host)
    s = settings()
    key = os.path.expanduser(str(s.get("key") or "~/.ssh/id_mac_fixer"))
    remote = " ".join(shlex.quote(str(a)) for a in action)
    return [str(s.get("ssh") or "ssh"), "-i", key, "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=accept-new",
            f"{h.get('user') or 'root'}@{h['addr']}", remote]


def run_shim(host: str, action: list, timeout: int = SSH_TIMEOUT_S) -> dict:
    if host not in hosts():
        return {"ok": False, "error": f"unknown host {host!r}; known: {', '.join(sorted(hosts())) or 'none'}"}
    try:
        r = subprocess.run(ssh_argv(host, action), capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"{host}: no answer in {timeout}s (asleep, offline, or hung)"}
    except OSError as exc:
        return {"ok": False, "error": f"ssh could not start: {exc}"}
    lines = [ln for ln in (r.stdout or "").strip().splitlines() if ln.strip()]
    try:
        out = json.loads(lines[-1])
        if not isinstance(out, dict):
            raise ValueError
    except (IndexError, ValueError):
        return {"ok": False, "exit": r.returncode,
                "error": f"{host}: no JSON from the shim (ssh rc={r.returncode}): {(r.stderr or r.stdout or '')[-300:]}"}
    out["exit"] = r.returncode
    return out


# ---------------------------------------------------------------- tools

def _host(args: dict) -> tuple:
    host = str(args.get("host") or "").strip().lower()
    if host not in hosts():
        return "", _jerr(f"host must be one of: {', '.join(sorted(hosts())) or '(none configured)'}")
    return host, ""


def tool_diag(args: dict, **_: Any) -> str:
    host, err = _host(args)
    if err:
        return err
    out = run_shim(host, ["diag"])
    if "result" in out:
        out["result"] = _clip(out["result"])
    return json.dumps(out)


def _confirmed_by_owner(ctx: dict, host: str, playbook: str) -> tuple:
    """(status, message). Reads the DM thread: a yes from the owner AFTER our question is the only way through."""
    pf = _home() / "mac-ops" / "pending.json"
    key = f"{ctx['channel']}:{ctx['thread']}"
    try:
        pending = json.loads(pf.read_text())
    except (OSError, ValueError):
        pending = {}
    p = pending.get(key)
    fresh = p and p.get("host") == host and p.get("playbook") == playbook and time.time() - p["at"] < PENDING_TTL_S
    if not fresh:
        text = (f"Run `{playbook}` on {host}? This {'logs you out' if playbook == 'logout' else 'reboots the Mac'} "
                f"and ends anything open there. Reply *yes* in this thread to go ahead.")
        res = API(ctx, "chat.postMessage", {"channel": ctx["channel"], "thread_ts": ctx["thread"], "text": text})
        pending[key] = {"host": host, "playbook": playbook, "ask_ts": res.get("ts") or "0", "at": time.time()}
        pf.parent.mkdir(parents=True, exist_ok=True)
        pf.write_text(json.dumps(pending))
        return "asked", "Asked the owner to confirm in the thread. Stop now; do not run it until they answer yes."
    rep = API(ctx, "conversations.replies", {"channel": ctx["channel"], "ts": ctx["thread"], "oldest": p["ask_ts"],
                                             "limit": 50})
    answers = [m for m in rep.get("messages", [])
               if m.get("user") == ctx["owner"] and not m.get("bot_id") and float(m.get("ts") or 0) > float(p["ask_ts"])]
    for m in answers:
        if NO.match(m.get("text") or ""):
            pending.pop(key, None)
            pf.write_text(json.dumps(pending))
            return "declined", "The owner said no. Nothing was run."
    for m in answers:
        if YES.match(m.get("text") or ""):
            pending.pop(key, None)
            pf.write_text(json.dumps(pending))
            return "yes", ""
    return "waiting", "Still waiting for the owner's yes in the thread. Do not run it."


def tool_fix(args: dict, **_: Any) -> str:
    ctx = turn_context()
    host, err = _host(args)
    if err:
        return err
    playbook = str(args.get("playbook") or "").strip()
    if playbook not in PLAYBOOKS:
        return _jerr(f"unknown playbook {playbook!r}; allowed: {', '.join(PLAYBOOKS)}")
    action = ["fix", playbook]
    if playbook in CONFIRM:
        if not ctx["ok"]:
            return _jerr("logout/reboot need the owner's yes in their Slack DM; this turn is not one")
        status, msg = _confirmed_by_owner(ctx, host, playbook)
        if status != "yes":
            return json.dumps({"ok": False, "status": status, "ran": False, "message": msg})
        action.append("--confirmed")
    out = run_shim(host, action)
    return json.dumps(dict(out, ran=bool(out.get("ok"))))


def _screen_argv(peer: str, op: str, args: dict) -> tuple:
    op = str(op or "").strip().lower()
    if op not in SCREEN_OPS:
        return [], f"op must be one of: {', '.join(SCREEN_OPS)}"
    a = ["screen", peer, op]
    if op == "click":
        try:
            a += [str(int(args["x"])), str(int(args["y"]))]
        except (KeyError, TypeError, ValueError):
            return [], "click needs integer x and y"
    elif op == "key":
        combo = str(args.get("combo") or "")
        if not KEY_RE.match(combo):
            return [], "key needs combo like cmd+space or Return"
        a.append(combo)
    elif op == "type":
        text = str(args.get("text") or "")
        if not text or len(text) > 200 or "\n" in text:
            return [], "type needs one line of text, at most 200 chars"
        a.append(text)
    return a, ""


def tool_screen(args: dict, **_: Any) -> str:
    """Look at `host` through the other Mac (the viewer), so it works with a dead mouse or a dead Hermes app."""
    ctx = turn_context()
    target, err = _host(args)
    if err:
        return err
    others = [h for h in hosts() if h != target]
    viewer = str(args.get("via") or "").strip().lower() or (others[0] if others else "")
    if viewer not in hosts() or viewer == target:
        return _jerr(f"no other Mac to look from; configured: {', '.join(sorted(hosts()))}")
    # The viewer's shim names its peer in its own terms (e.g. the hub's "mbp" is the mini's "macbook").
    peer = str(hosts()[target].get("peer") or target).strip()
    if not PEER_RE.match(peer):
        return _jerr(f"bad peer name {peer!r} in mac_ops.hosts.{target}.peer")
    action, perr = _screen_argv(peer, args.get("op"), args)
    if perr:
        return _jerr(perr)
    out = run_shim(viewer, action, timeout=150)
    res = out.get("result") if isinstance(out.get("result"), dict) else {}
    b64 = res.get("png_base64")
    if out.get("ok") and b64:
        png = base64.b64decode(b64)
        if not ctx["ok"]:
            return _jerr("screenshot taken but this turn has no owner DM to post it in")
        name = f"{target}-{time.strftime('%H%M%S')}.png"
        UPLOAD(ctx, png, name, f"{target} screen")
        res = {k: v for k, v in res.items() if k != "png_base64"}
        res["posted_to_thread"] = True
        res["bytes"] = len(png)
        out["result"] = res
    return json.dumps(out)


def tool_reply(args: dict, **_: Any) -> str:
    ctx = turn_context()
    if not ctx["ok"]:
        return _jerr("no owner DM bound to this turn; nothing was posted")
    text = str(args.get("text") or "").strip()
    if not text:
        return _jerr("text is required")
    res = API(ctx, "chat.postMessage", {"channel": ctx["channel"], "thread_ts": ctx["thread"], "text": text[:3500]})
    return json.dumps({"ok": True, "ts": res.get("ts")})


SCHEMAS = {
    "mac_diag": ("Read-only health report of one of the owner's Macs (processes, displays, HID, power, load, "
                 "crash reports, WindowServer log) from its fixer shim.",
                 {"host": {"type": "string", "description": "mbp or mini"}}, ["host"], tool_diag),
    "mac_fix": ("Run a vetted repair playbook on a Mac: " + ", ".join(PLAYBOOKS) + ". logout and reboot first ask the "
                "owner in the Slack thread and run only after they reply yes; call again after the owner answers.",
                {"host": {"type": "string"}, "playbook": {"type": "string", "enum": list(PLAYBOOKS)}},
                ["host", "playbook"], tool_fix),
    "mac_screen": ("See or drive host's screen through the other Mac. op=shot posts a screenshot in the owner's "
                   "thread; click(x,y), key(combo), type(text) act on it.",
                   {"host": {"type": "string", "description": "the Mac to look at"},
                    "op": {"type": "string", "enum": list(SCREEN_OPS)}, "x": {"type": "integer"},
                    "y": {"type": "integer"}, "combo": {"type": "string"}, "text": {"type": "string"}},
                   ["host", "op"], tool_screen),
    "mac_reply": ("Post a message to the owner in their Slack DM thread. The only way the owner sees your answer.",
                  {"text": {"type": "string"}}, ["text"], tool_reply),
}


def register(ctx) -> None:
    if not enabled():  # ambient/bot profiles: plugin loads but exposes nothing
        return
    for name, (desc, props, required, fn) in SCHEMAS.items():
        def handler(args, _fn=fn, **kw):
            try:
                return _fn(dict(args or {}), **kw)
            except Exception as exc:
                return _jerr(f"{type(exc).__name__}: {exc}")
        schema = {"name": name, "description": desc, "parameters": {
            "type": "object", "properties": props, "required": required}}
        ctx.register_tool(name=name, toolset=TOOLSET, schema=schema, handler=handler, description=desc,
                          check_fn=lambda: bool(hosts()))
