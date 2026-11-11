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

Second lane, leftover test chats: an agent's OWN test chat (the first user turn is the probe) is fine while it runs,
but one left visible in the sidebar is litter the user has to look at. A latency harness drove six real new chats
through a Desktop window ("[latency probe, please reply just ok] ...") and only closed the window; the six chats sat
in David's sidebar until he asked. RPC harnesses create theirs ``hidden``; UI-driven ones cannot, so their teardown
must delete them. A user-facing session whose digest says ``hidden: false`` and not archived, with at most
LEFTOVER_MAX_TURNS user turns, a probe-shaped first turn and no activity for LEFTOVER_IDLE_S, is a hit. New hits on
one host share ONE card (``leftover-test-chats:<host>:<first session>``) that asks to delete them AND fix the harness
that left them.

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
# Probe-shaped FIRST turns of an agent's own test chat: "[latency probe, ...]", "[probe t_01a...]",
# "Reply with just the word: fixb6233", "Reply with only: fresh_armF_one".
PROBE_TAG = re.compile(r"^\s*\[[^\]\n]{0,80}\bprobe\b[^\]\n]{0,80}\]", re.I)
REPLY_JUST = re.compile(r"\breply\s+(?:with\s+)?(?:just|only)\b(?:\s+the\s+word)?\s*[:`\"']?\s*[A-Za-z0-9_-]+\s*[.`\"']?\s*$", re.I)
LEFTOVER_IDLE_S = 3600
LEFTOVER_MAX_TURNS = 3


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
    evidence, real_before, probe_ts = [], False, 0.0
    for t in dg.get("user_turns") or []:
        text = str(t.get("text") or "")
        if _probe_ask(text):
            if real_before:
                evidence.append(f"user turn at {_pt(t.get('t'))}: {text[:200]!r}")
                try:
                    probe_ts = max(probe_ts, float(t.get("t") or 0))
                except (TypeError, ValueError):
                    pass
        elif text.strip():
            real_before = True
    last = str(dg.get("last_reply") or "").strip()
    ts = float(dg.get("last_activity_at") or 0)
    if TOKEN_REPLY.match(last) and real_before:
        evidence.append(f"last reply is a bare token: {last!r}")
        probe_ts = max(probe_ts, ts)  # the digest has no reply time; a token that is still the last reply is "now"
    if not evidence:
        return None
    # ``ts`` is chat activity (the person keeps talking after a probe); ``probe_ts`` is the newest PROBE evidence.
    # Only new evidence lines / ``probe_ts`` decide whether a hit is new (see _is_new).
    return {"session": dg.get("id"), "host": dg.get("host") or "", "profile": dg.get("profile") or "default",
            "source": dg.get("source"), "title": dg.get("title") or "", "ts": ts, "probe_ts": probe_ts or ts,
            "evidence": "\n".join(evidence[-6:]), "evidence_lines": evidence}


def _probe_opener(text: str) -> bool:
    first_line = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
    return bool(_probe_ask(text) or PROBE_TAG.search(text or "") or REPLY_JUST.search(first_line))


def leftover(dg: dict, now: float) -> Optional[dict]:
    """A visible, idle test chat an agent opened for itself (first user turn is the probe), or None."""
    if str(dg.get("source") or "").lower() not in USER_SOURCES:
        return None
    if dg.get("archived") or dg.get("hidden") is not False:  # hidden/unknown (pre-v9 digest) is not sidebar litter
        return None
    turns = [t for t in dg.get("user_turns") or [] if str(t.get("text") or "").strip()]
    if not turns or len(turns) > LEFTOVER_MAX_TURNS or not _probe_opener(str(turns[0].get("text") or "")):
        return None
    ts = float(dg.get("last_activity_at") or 0)
    if now - ts < LEFTOVER_IDLE_S:
        return None
    return {"session": dg.get("id"), "host": dg.get("host") or "", "profile": dg.get("profile") or "default",
            "source": dg.get("source"), "title": dg.get("title") or "", "ts": ts,
            "first": str(turns[0].get("text") or "")[:160]}


def scan_leftovers(data: Path, since: float, now: float) -> list:
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
            hit = leftover(dg, now)
            if hit:
                hit["host"] = hit["host"] or host_dir.name
                hits.append(hit)
    return sorted(hits, key=lambda h: (h["host"], h["ts"]))


def leftover_body(host: str, hits: list) -> str:
    rows = "\n".join(f"- `{h['session']}` ({h['profile']}, {h['source']}, {_pt(h['ts'])}) \"{h['title'][:60]}\": "
                     f"{h['first'][:100]!r}" for h in hits)
    ids = " ".join(h["session"] for h in hits)
    return f"""Agent test chats were left VISIBLE in the user's sidebar on host {host}: each one's first user turn is a
probe and nothing deleted it afterwards.

{rows}

An RPC harness creates its test chats with `hidden: true` (gateway_rpc.py, `?client=agent`); a harness that drives a
real Desktop window (Cmd-N + Enter over the inspector) cannot, so its teardown must delete every chat it created.
These got neither.

## Do this
1. Delete them on {host} (they are test litter, not the user's): `hermes sessions delete --yes <id>` for each of
   `{ids}` (use `-p <profile>` for a non-default profile; `tailnet_exec` for another host).
2. Find the harness that made them (grep `state.db` tool calls on {host} near the times above for the first-turn
   text) and fix it at the source with five-whys: create the chat hidden, or delete each created id in the
   harness's own teardown (a `finally`, not the agent's memory). A one-off scratch script gets the rule added to
   the skill it followed instead.
3. Do not add a sweeper that archives by title: it hides the symptom and the next harness leaks again.

## Acceptance
- `python3 ~/code/hermes-mods/plugins/coach/probe_token_watch.py --dry-run --days 2` lists none of these sessions
  under `leftovers` once the digest refreshes; paste it in a comment.
- The harness fix (commit or skill patch) is named in the completion summary.
"""


def file_leftovers(home: Path, hits: list, runner: Callable) -> list:
    st = load_state(home)
    known = st.setdefault("leftovers", {})
    out = []
    by_host: dict = {}
    for h in hits:
        if f"{h['host']}:{h['session']}" not in known:
            by_host.setdefault(h["host"], []).append(h)
    for host, new in by_host.items():
        key = f"leftover-test-chats:{host}:{new[0]['session']}"
        title = f"agent test chats left visible in the sidebar: {host} ({len(new)})"
        r = _run(runner, ["hermes", "kanban", "create", "--assignee", "default", "--idempotency-key", key,
                          "--created-by", AUTHOR, "--body-file", "-", "--json", title], leftover_body(host, new))
        if r.returncode != 0:
            out.append({"key": key, "action": "create_failed", "out": ((r.stdout or "") + (r.stderr or ""))[-300:]})
            continue
        try:
            tid = json.loads(r.stdout).get("id")
        except (ValueError, TypeError, AttributeError):
            out.append({"key": key, "action": "create_unparsed", "out": (r.stdout or "")[-300:]})
            continue
        for h in new:
            known[f"{host}:{h['session']}"] = {"task_id": tid, "ts": h["ts"]}
        save_state(home, st)
        out.append({"key": key, "action": "created", "task_id": tid, "sessions": [h["session"] for h in new]})
    return out


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
2. If every probe turn above predates the guard (hermes-mods 5910070, 2026-10-30 05:11 UTC), it was not a bypass:
   say so and stop at step 3. Otherwise find why the guard did not stop it (plugin not enabled on that host,
   ssh/remote path, Claude Code without the
   adapter, an opt-out, a command form the classifier misses) and fix that in
   `~/code/hermes-mods/plugins/user-chat-probe-guard` with a regression test for the exact command (five-whys).
3. If the probe buried a real question the user still has to answer, tell the agent that owns that question
   (its card) so it re-asks; do not post into the user's chat yourself.

## Acceptance
- The exact command from step 1 is blocked by a new test in `plugins/user-chat-probe-guard/tests`; suite passes.
- `python3 ~/code/hermes-mods/plugins/coach/probe_token_watch.py --dry-run --days 2` shows no newer hit for this
  session. Paste it in a comment before completing.
"""


def _seen_lines(known: dict) -> set:
    return set(known.get("evidence_lines") or [])


def _is_new(h: dict, known: dict) -> bool:
    """New PROBE evidence since the recorded state, not merely newer chat activity.

    The person keeps chatting after a probe, so the digest's last_activity_at moves on while the evidence (the same
    old probe turns) does not. Keying on activity re-filed a card for an incident already fixed (t_01a18b702067f62d:
    a seeded session re-fired at the user's next message). Entries written before evidence_lines existed fall back
    to the newest probe time vs the recorded time."""
    if known.get("evidence_lines") is not None:
        return bool(set(h.get("evidence_lines") or []) - _seen_lines(known))
    # digest turn times are whole seconds while last_activity_at is a float: allow the rounding
    return float(h.get("probe_ts") or h["ts"]) > float(known.get("ts") or 0) + 1


def _record(h: dict, task_id: Optional[str] = None, known: Optional[dict] = None, **extra) -> dict:
    rec = {"ts": h["ts"], "probe_ts": h.get("probe_ts") or h["ts"],
           "evidence_lines": sorted(_seen_lines(known or {}) | set(h.get("evidence_lines") or [])), **extra}
    if task_id:
        rec["task_id"] = task_id
    return rec


def file_card(home: Path, h: dict, runner: Callable) -> dict:
    key = f"probe-token:{h['host']}:{h['session']}"
    st = load_state(home)
    known = (st.get("sessions") or {}).get(key) or {}
    if known and not _is_new(h, known):
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
        st.setdefault("sessions", {})[key] = _record(h, task_id=tid, known=known)
        save_state(home, st)
        return {"key": key, "action": "created", "task_id": tid}
    r = _run(runner, ["hermes", "kanban", "comment", tid, "--author", AUTHOR,
                      f"Newer probe evidence in {h['session']} (last activity {_pt(h['ts'])}):\n{h['evidence']}"])
    if r.returncode == 0:
        st.setdefault("sessions", {})[key] = _record(h, task_id=tid, known=known)
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
    lefts = scan_leftovers(data, now - a.days * 86400, now)
    rep = {"count": len(hits), "sessions": [f"{h['host']}:{h['session']}" for h in hits], "hits": hits[:20],
           "leftovers": [f"{h['host']}:{h['session']}" for h in lefts]}
    if a.seed:
        st = load_state(home)
        for h in hits:
            st.setdefault("sessions", {}).setdefault(f"probe-token:{h['host']}:{h['session']}",
                                                     _record(h, seeded=True))
        save_state(home, st)
        rep["seeded"] = len(hits)
    elif not a.dry_run:
        rep["cards"] = [c for c in (file_card(home, h, runner or subprocess.run) for h in hits)
                        if c["action"] != "known"] + file_leftovers(home, lefts, runner or subprocess.run)
    if a.json or a.dry_run or a.seed:
        print(json.dumps(rep, indent=1))
    elif rep.get("cards"):
        print(f"probe-token-watch: {len(rep['cards'])} action(s): {rep['cards']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
