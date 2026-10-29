#!/usr/bin/env python3
"""Zero-LLM change watcher for the concierge.

Polls the Claude desktop app's per-session JSON files every --interval seconds
and exits (printing what changed) as soon as an open session finishes a turn,
starts or stops waiting on the user, gets archived, is retitled by someone else,
or one of its PRs changes state. Run it in the background from the concierge
session; its exit wakes that session, which does the real update and restarts it.

Only files whose mtime moved are re-read, so a quiet minute costs a stat() per
session file and no model tokens.
"""

import argparse
import glob
import json
import os
import sys
import time

ROOT = os.path.expanduser("~/Library/Application Support/Claude/claude-code-sessions")
EMPTY_STATUS = ":  | needs: "


def signature(path):
    try:
        with open(path) as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    return {
        "title": d.get("title"),
        "archived": bool(d.get("isArchived")),
        "turns": d.get("completedTurns"),
        "awaiting": bool(d.get("interruptedAwaitingPerson")),
        "prs": sorted(f"{p.get('repo')}#{p.get('prNumber')}:{p.get('state')}" for p in d.get("prs") or []),
        # The app's own post-turn summary: what the session is doing and what it needs from the user.
        "status": "{status_category}: {status_detail} | needs: {needs_action}".format(
            **{k: (d.get("postTurnSummary") or {}).get(k) or "" for k in ("status_category", "status_detail", "needs_action")}
        ),
    }


def describe(sid, old, new):
    if new["archived"] and not old["archived"]:
        return f"{sid} archived: {new['title']}"
    out = []
    if new["turns"] != old["turns"]:
        out.append(f"finished a turn ({old['turns']}->{new['turns']})")
    if new["awaiting"] != old["awaiting"]:
        out.append("now waiting on the user" if new["awaiting"] else "no longer waiting on the user")
    if new["prs"] != old["prs"]:
        out.append(f"PRs {old['prs']} -> {new['prs']}")
    if new["title"] != old["title"]:
        out.append(f"retitled {old['title']!r} -> {new['title']!r}")
    # A summary cleared to empty only means a new turn started; the turn's end reports it.
    if new["status"] != old["status"] and new["status"] != EMPTY_STATUS and not out:
        out.append("status changed")
    return f"{sid} {new['title']!r}: " + "; ".join(out) + f" [{new['status']}]" if out else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=int, default=60)
    ap.add_argument("--days", type=int, default=60, help="only track sessions active in this many days")
    ap.add_argument("--ignore", action="append", default=[], help="session id to skip (the concierge itself)")
    ap.add_argument("--ignore-title-only", action="store_true",
                    help="don't wake for title changes alone (the concierge sets titles itself)")
    args = ap.parse_args()

    cutoff = time.time() - args.days * 86400
    mtimes, sigs = {}, {}
    for path in glob.glob(os.path.join(ROOT, "*", "*", "local_*.json")):
        sid = os.path.basename(path)[:-5]
        if sid in args.ignore:
            continue
        m = os.stat(path).st_mtime
        if m < cutoff:
            continue
        s = signature(path)
        if s and not s["archived"]:
            mtimes[path], sigs[path] = m, s

    while True:
        time.sleep(args.interval)
        changes = []
        for path in glob.glob(os.path.join(ROOT, "*", "*", "local_*.json")):
            sid = os.path.basename(path)[:-5]
            if sid in args.ignore:
                continue
            try:
                m = os.stat(path).st_mtime
            except OSError:
                continue
            if mtimes.get(path) == m:
                continue
            new = signature(path)
            if new is None:
                continue
            old = sigs.get(path)
            mtimes[path], sigs[path] = m, new
            if old is None:
                if not new["archived"] and m >= cutoff:
                    changes.append(f"{sid} new session: {new['title']!r}")
                continue
            quiet = ("title", "status") if new["status"] == EMPTY_STATUS else ("title",)
            if args.ignore_title_only and {k: v for k, v in old.items() if k not in quiet} == {
                k: v for k, v in new.items() if k not in quiet
            }:
                continue
            line = describe(sid, old, new)
            if line:
                changes.append(line)
        if changes:
            print("\n".join(changes))
            sys.stdout.flush()
            return 0


if __name__ == "__main__":
    sys.exit(main())
