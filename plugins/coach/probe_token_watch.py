#!/usr/bin/env python3
"""Flag a person's chat whose turns include an agent's "Reply exactly <TOKEN>" probe; ONE Kanban card per session.

A fix worker proved its fix with ``hermes chat --resume <David's chat> --oneshot -q "... Reply exactly
ROUTE_INSTALLED_23ef49"``: the probe and the bare token became the newest turns of his Desktop chat and buried the
question waiting for him (self-heal agent-93a55211b1). The user-chat-probe-guard plugin now refuses that command;
this watcher catches whatever still gets through (a host without the plugin, an ssh path, another harness). It
reads the coach digests (``~/code/hermes-coach-data/<host>/<profile>/<session>.json``, every host) and flags a
user-facing session (desktop, cli, messaging) where a real user turn is followed by a turn containing ``Reply
exactly <TOKEN>``, or whose last reply is just such a token. Sessions an agent opened for its own test (the first
user turn is the probe) are not flagged. Each session gets one card (``probe-token:<host>:<session>``); newer evidence in the
same chat is a comment on it.

    probe_token_watch.py [--dry-run] [--json] [--seed] [--days N] [--data DIR] [--home PATH] [--no-pull]

--dry-run prints the findings and files nothing; --seed records current hits as known without filing (install).
No Inbox items. Exit 0 unless the scan itself crashes.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

AUTHOR = "probe-token-watch"
USER_SOURCES = {"desktop", "cli", "tui", "telegram", "discord", "slack", "whatsapp", "signal", "imessage", "sms",
                "email", "matrix", "mattermost", "webui", "web", "bluebubbles", "homeassistant"}
# "Reply exactly ROUTE_ACCEPTED_23ef49." / "reply exactly `PONG`": an uppercase token, not prose ("the same").
PROBE_ASK = re.compile(r"\breply\s+(?:with\s+)?exactly[:\s]+[`\"']?([A-Z][A-Z0-9]+(?:_[A-Za-z0-9]+)*)\b", re.I)
# A whole reply that is one token with an underscore (ROUTE_INSTALLED_23ef49); "OK"/"Yes." are ordinary answers.
TOKEN_REPLY = re.compile(r"^[`\"']?[A-Z][A-Z0-9]*(?:_[A-Za-z0-9]+)+[`\"'.]?$")


def hermes_home(home: Optional[str] = None) -> Path:
    return Path(home or os.environ.get("HERMES_HOME") or Path.home() / ".hermes")


def default_data() -> Path:
    return Path(os.environ.get("HERMES_COACH_DATA") or Path.home() / "code" / "hermes-coach-data")


def _probe_ask(text: str) -> Optional[str]:
    m = PROBE_ASK.search(text or "")
    if not m:
        return None
    tok = m.group(1)  # the regex is case-insensitive for "reply exactly"; the token's head must be UPPERCASE
    head = tok.split("_")[0]
    return tok if head.isupper() and len(head) >= 2 else None


def check(dg: dict) -> Optional[dict]:
    """A hit for one digest, or None."""
    if str(dg.get("source") or "").lower() not in USER_SOURCES:
        return None
    # A person's chat = a real (non-probe) user turn came BEFORE the first probe. A session an agent opened for its
    # own live test starts with the probe (Session Mentions transport tests, pause/resume probes): not flagged.
    evidence, real_before = [], False
    for t in dg.get("user_turns") or []:
        text = str(t.get("text") or "")
        if _probe_ask(text):
            if real_before:
                evidence.append(f"user turn at {_pt(t.get('t'))}: {text[:200]!r}")
        elif text.strip():
            real_before = True
    last = str(dg.get("last_reply") or "").strip()
    if TOKEN_REPLY.match(last) and real_before:
        evidence.append(f"last reply is a bare token: {last!r}")
    if not evidence:
        return None
    return {"session": dg.get("id"), "host": dg.get("host") or "", "profile": dg.get("profile") or "default",
            "source": dg.get("source"), "title": dg.get("title") or "", "ts": float(dg.get("last_activity_at") or 0),
            "evidence": "\n".join(evidence[-6:])}


def _pt(ts) -> str:
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return "?"


def scan(data: Path, since: float) -> list:
    hits = []
    for host_dir in sorted(p for p in data.iterdir() if p.is_dir() and not p.name.startswith(".")) \
            if data.exists() else []:
        for f in host_dir.glob("*/*.json"):
            try:
                if f.stat().st_mtime < since:
                    continue
                dg = json.loads(f.read_text())
            except (OSError, ValueError):
                continue
            if not isinstance(dg, dict):
                continue
            dg.setdefault("host", host_dir.name)
            dg.setdefault("profile", f.parent.name)
            hit = check(dg)
            if hit:
                hit["host"] = hit["host"] or host_dir.name
                hits.append(hit)
    return sorted(hits, key=lambda h: h["ts"])


def state_path(home: Path) -> Path:
    return home / "coach" / "probe_token" / "state.json"


def load_state(home: Path) -> dict:
    try:
        return json.loads(state_path(home).read_text())
    except (OSError, ValueError):
        return {}


def save_state(home: Path, st: dict) -> None:
    p = state_path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1))
    os.replace(tmp, p)


def _hermes_bin() -> str:
    import shutil
    if shutil.which("hermes"):
        return "hermes"
    for p in (Path.home() / ".local" / "bin" / "hermes", Path("/opt/homebrew/bin/hermes"), Path("/usr/local/bin/hermes")):
        if p.exists():
            return str(p)
    return "hermes"


def _run(runner: Callable, argv: list, body: Optional[str] = None):
    if runner is subprocess.run and argv and argv[0] == "hermes":
        argv = [_hermes_bin(), *argv[1:]]
    return runner(argv, input=body, capture_output=True, text=True, timeout=120)


def card_body(h: dict) -> str:
    return f"""A person's chat got an agent's test turn. Session `{h['session']}` ("{h['title'][:80]}", {h['source']},
host {h['host']}, profile {h['profile']}) has "Reply exactly <TOKEN>" probe turns or a bare-token reply:

{h['evidence']}

An agent sent a one-shot `hermes chat --resume <this session> -q ...` (or similar) into the user's real chat, so its
probe and token reply are what the user sees. The user-chat-probe-guard plugin refuses that command and names
`hermes sessions branch <id>` (probe a hidden copy); this one got through (self-heal agent-93a55211b1).

## Do this
1. Find the sender: grep Kanban comments/cards and `state.db` tool calls on host {h['host']} around
   {_pt(h['ts'])} for the token and `--resume {h['session'][-6:]}`.
2. Find why the guard did not stop it (plugin not enabled on that host, ssh/remote path, Claude Code without the
   adapter, an opt-out, a command form the classifier misses) and fix that in
   `~/code/hermes-mods/plugins/user-chat-probe-guard` with a regression test for the exact command (five-whys).
3. If the probe buried a real question the user still has to answer, tell the agent that owns that question
   (its card) so it re-asks; do not post into the user's chat yourself.

## Acceptance
- The exact command from step 1 is blocked by a new test in `plugins/user-chat-probe-guard/tests`; suite passes.
- `python3 ~/code/hermes-mods/plugins/coach/probe_token_watch.py --dry-run --days 2` shows no newer hit for this
  session. Paste it in a comment before completing.
"""


def file_card(home: Path, h: dict, runner: Callable) -> dict:
    key = f"probe-token:{h['host']}:{h['session']}"
    st = load_state(home)
    known = (st.get("sessions") or {}).get(key) or {}
    if known and h["ts"] <= float(known.get("ts") or 0):
        return {"key": key, "action": "known"}
    tid = known.get("task_id")
    if not tid:
        title = f"agent probe turns in a user's chat: {h['host']} {h['session']}"
        r = _run(runner, ["hermes", "kanban", "create", "--assignee", "default", "--idempotency-key", key,
                          "--created-by", AUTHOR, "--body-file", "-", "--json", title], card_body(h))
        if r.returncode != 0:
            return {"key": key, "action": "create_failed", "out": ((r.stdout or "") + (r.stderr or ""))[-300:]}
        try:
            tid = json.loads(r.stdout).get("id")
        except (ValueError, TypeError, AttributeError):
            return {"key": key, "action": "create_unparsed", "out": (r.stdout or "")[-300:]}
        st.setdefault("sessions", {})[key] = {"task_id": tid, "ts": h["ts"]}
        save_state(home, st)
        return {"key": key, "action": "created", "task_id": tid}
    r = _run(runner, ["hermes", "kanban", "comment", tid, "--author", AUTHOR,
                      f"Newer probe evidence in {h['session']} (last activity {_pt(h['ts'])}):\n{h['evidence']}"])
    if r.returncode == 0:
        st.setdefault("sessions", {})[key] = {"task_id": tid, "ts": h["ts"]}
        save_state(home, st)
    return {"key": key, "action": "commented" if r.returncode == 0 else "comment_failed", "task_id": tid}


def main(argv=None, runner: Optional[Callable] = None, now: Optional[float] = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--seed", action="store_true", help="record current hits as known without filing (install)")
    ap.add_argument("--days", type=float, default=3.0, help="only digests written in the last N days")
    ap.add_argument("--data", help="coach digest dir (default ~/code/hermes-coach-data)")
    ap.add_argument("--home")
    ap.add_argument("--no-pull", action="store_true", help="skip `git pull` of the digest repo")
    a = ap.parse_args(argv)
    home = hermes_home(a.home)
    data = Path(a.data) if a.data else default_data()
    now = now or time.time()
    if not a.no_pull and (data / ".git").exists():
        subprocess.run(["git", "-C", str(data), "pull", "-q", "--ff-only"], capture_output=True, timeout=120)
    hits = scan(data, now - a.days * 86400)
    rep = {"count": len(hits), "sessions": [f"{h['host']}:{h['session']}" for h in hits], "hits": hits[:20]}
    if a.seed:
        st = load_state(home)
        for h in hits:
            st.setdefault("sessions", {}).setdefault(f"probe-token:{h['host']}:{h['session']}",
                                                     {"ts": h["ts"], "seeded": True})
        save_state(home, st)
        rep["seeded"] = len(hits)
    elif not a.dry_run:
        rep["cards"] = [c for c in (file_card(home, h, runner or subprocess.run) for h in hits)
                        if c["action"] != "known"]
    if a.json or a.dry_run or a.seed:
        print(json.dumps(rep, indent=1))
    elif rep.get("cards"):
        print(f"probe-token-watch: {len(rep['cards'])} action(s): {rep['cards']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
