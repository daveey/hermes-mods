#!/usr/bin/env python3
"""Engagement ops from outside the bot's gateway process (another machine via a remote shell).

  engage_cli.py --home <bot profile HERMES_HOME> <op> [--json-file F | --b64 B64JSON] [--hex]

op: create | list | get | pause | resume | end | update | message | test_inbox | mbp_pull | mbp_done | queue. Prints one JSON line
({"ok": ...}); with --hex the JSON is hex-encoded in 32-char lines between HEX-BEGIN/HEX-END
markers (for shells that mangle long tokens). ``create`` checks the channel with the bot token
from <home>/.env: a private channel the bot is not in fails with an invite instruction.
The bot gateway's tick picks a new engagement up within a minute (join, follower kickoff).
"""
from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name: str, file: str):
    mod = sys.modules.get(name)
    if mod is None:
        spec = importlib.util.spec_from_file_location(name, HERE / file)
        mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        sys.modules[name] = mod
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


core = _load("slack_workspace_core", "core.py")
engage = _load("slack_workspace_engage", "engage.py")
follow = _load("slack_workspace_follow", "follow.py")


def check_channel(home: Path, args: dict) -> dict | None:
    tok = follow.read_env_value(home / ".env", "SLACK_BOT_TOKEN")
    if not tok:
        return None
    try:
        c = core.call(tok, "conversations.info", {"channel": args["channel"]})["channel"]
    except core.SlackError as exc:
        if exc.error == "channel_not_found":
            return {"ok": False, "error": "channel_not_found: it is private (or unknown) and the bot is not a "
                                          "member. Invite the bot first (/invite @<bot> in the channel), then retry.",
                    "needs_invite": True}
        return {"ok": False, "error": f"conversations.info: {exc.error}"}
    args["private"] = bool(c.get("is_private"))
    args.setdefault("name", c.get("name") or "")
    if args["private"] and not c.get("is_member"):
        return {"ok": False, "error": "private channel and the bot is not a member: /invite the bot first",
                "needs_invite": True}
    return None


def run(home: Path, op: str, args: dict) -> dict:
    engs = engage.Engagements(core.Store(home / "slack-workspace"))
    if op == "queue":
        return {"ok": True, "queue": engs.mbp_list(args.get("id") or None)}
    if op == "create":
        bad = check_channel(home, args)
        if bad:
            return bad
    try:
        return follow.engagement_op(engs, op, args)
    except (ValueError, KeyError) as exc:
        return {"ok": False, "error": str(exc)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", required=True)
    ap.add_argument("op")
    ap.add_argument("--json-file")
    ap.add_argument("--b64")
    ap.add_argument("--hex", action="store_true")
    a = ap.parse_args(argv)
    args = {}
    if a.json_file:
        args = json.loads(Path(a.json_file).read_text())
    elif a.b64:
        args = json.loads(base64.b64decode(a.b64))
    out = json.dumps(run(Path(a.home), a.op, args), default=str)
    if a.hex:
        h = out.encode().hex()
        print("HEX-BEGIN")
        for i in range(0, len(h), 32):
            print(h[i:i + 32])
        print("HEX-END")
    else:
        print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
