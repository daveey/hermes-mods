#!/usr/bin/env python3
"""Replay coach session digests through the repeat-ask hold (deterministic, no model call).

    replay_repeat.py DIGEST_DIR [DIGEST_DIR ...] [--days 30] [--show N] [--json OUT]

Each digest's user turns carry `prev`, the agent reply before that turn (head+tail clip, where asks sit). Per
session, in time order: find the asks (core.find_asks), hold the reply when core.repeated_asks finds an item this
chat already showed REPEAT_MAX_SHOWN times in 48 h, else record its items as shown (the judge is assumed to pass
them, which is what made the restatements reach the user). A held reply is not recorded, as in the live gate.
Prints the number of replies replayed, replies with asks, and holds; --show prints N holds for hand-checking.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import random
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("ask_gate_core_replay", HERE / "core.py")
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)  # type: ignore[union-attr]


def replies(digest: dict) -> list[tuple[float, str, str]]:
    """[(t, reply, user message that prompted it)] in time order."""
    out, last_user = [], ""
    for turn in digest.get("user_turns") or []:
        prev = turn.get("prev") or ""
        if prev:
            out.append((float(turn.get("t") or 0) - 1, prev, last_user))
        last_user = turn.get("text") or ""
    return out


def replay(dirs: list[Path], days: float) -> dict:
    cutoff = time.time() - days * 86400
    n_replies = n_asks = 0
    holds = []
    for d in dirs:
        for f in sorted(d.rglob("*.json")):
            try:
                dg = json.loads(f.read_text())
            except (OSError, ValueError):
                continue
            if not isinstance(dg, dict) or float(dg.get("last_activity_at") or 0) < cutoff:
                continue
            shown: list[dict] = []
            for t, reply, user in replies(dg):
                if t < cutoff:
                    continue
                n_replies += 1
                howto = core.asked_how_to(user)
                asks = core.find_asks(reply, howto=howto)
                if not asks:
                    continue
                n_asks += 1
                window = [r for r in shown if t - r["ts"] <= core.REPEAT_WINDOW_S]
                items = core.ask_items(asks)
                reps = [] if howto else core.repeated_asks(items, window, [user] if user else [], reply=reply, now=t)
                if reps:
                    holds.append({"file": str(f), "t": t, "repeats": reps, "reply_tail": reply[-500:],
                                  "user": user[:200]})
                    continue
                for it in items:
                    shown.append({"ts": t, "reply": f"{f.name}:{t}", "text": it,
                                  "facts": core.fact_tokens(core.ask_context(it, reply))})
    return {"replies": n_replies, "with_asks": n_asks, "holds": holds}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dirs", nargs="+", type=Path)
    ap.add_argument("--days", type=float, default=30)
    ap.add_argument("--show", type=int, default=0)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--json", type=Path)
    a = ap.parse_args()
    res = replay(a.dirs, a.days)
    h = res["holds"]
    print(f"replies={res['replies']} with_asks={res['with_asks']} holds={len(h)} "
          f"({100 * len(h) / max(1, res['replies']):.2f}% of replies)")
    if a.json:
        a.json.write_text(json.dumps(res, indent=1, default=list))
    random.seed(a.seed)
    for x in random.sample(h, min(a.show, len(h))):
        print("\n==", x["file"], core.fmt_ts(x["t"]))
        for r in x["repeats"]:
            print(f"  repeat (n={r['n']}): {r['item'][:220]}")
        print("  user before:", x["user"][:160].replace("\n", " "))
    return 0


if __name__ == "__main__":
    sys.exit(main())
