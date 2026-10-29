"""Daily cross-gateway Inbox junk audit (defect bb-ce4c46ed94).

The store's guards act per gateway at post time. This audit reads every gateway's Inbox through
`hermes-hub inbox --all --json` and reports what still slipped through, so an agent (not David) notices:

- a sender (poster group / key family) with 2+ cards archived untouched in the last 7 days, on any gateway;
- a key re-posted after David archived it;
- a test/probe-titled card.

Findings with new evidence (a card created in the last 24 h) open ONE Kanban card per day
(idempotency key ``inbox-junk-audit:<date>``, assignee default). Never the Inbox. Counts go to stdout, which the
no-agent cron keeps locally (`hermes cron list`).

Run: ``python3 junk_audit.py [--json FILE] [--dry-run]`` (hub cron ``inbox-junk-audit``, daily).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

WINDOW_S = 7 * 86400
FRESH_S = 86400
_STORE = Path(__file__).resolve().parent / "store.py"


def _store():
    spec = importlib.util.spec_from_file_location("_inbox_store_audit", _STORE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _sender(item, st) -> str:
    poster = item.get("poster") or st.key_poster(item.get("key")) or "(none)"
    return st.poster_group(poster) or poster


def _untouched(item) -> bool:
    return item.get("state") == "archived" and (item.get("outcome") in (None, "archived"))


def audit(items: list[dict], now: float) -> dict:
    """Findings over the last WINDOW_S. Each finding lists its cards; ``fresh`` = has a card from the last FRESH_S."""
    st = _store()
    recent = [i for i in items if float(i.get("created_at") or 0) >= now - WINDOW_S]
    fresh = lambda cards: any(float(c.get("created_at") or 0) >= now - FRESH_S for c in cards)  # noqa: E731
    out = {"window_items": len(recent), "senders": [], "reposted_keys": [], "test_cards": []}

    by_sender = defaultdict(list)
    for i in recent:
        if _untouched(i) and i.get("poster"):  # key-family senders are judged per key (reposted_keys)
            by_sender[(i.get("gw"), _sender(i, st))].append(i)
    for (gw, sender), cards in sorted(by_sender.items()):
        if len(cards) >= 2:
            out["senders"].append({"gw": gw, "sender": sender, "archived_untouched": len(cards),
                                   "fresh": fresh(cards), "titles": [c.get("title") for c in cards][:5]})

    by_key = defaultdict(list)
    for i in recent:
        if i.get("key"):
            by_key[(i.get("gw"), i["key"])].append(i)
    for (gw, key), cards in sorted(by_key.items()):
        cards.sort(key=lambda c: float(c.get("created_at") or 0))
        reposts = [b for a, b in zip(cards, cards[1:]) if _untouched(a)]
        if reposts:
            out["reposted_keys"].append({"gw": gw, "key": key, "reposts": len(reposts), "fresh": fresh(reposts),
                                         "title": cards[-1].get("title")})

    for i in recent:
        why = st.junk_reason(i.get("title") or "", i.get("key"))
        if why:
            out["test_cards"].append({"gw": i.get("gw"), "key": i.get("key"), "title": i.get("title"),
                                      "fresh": fresh([i])})
    out["fresh"] = any(f["fresh"] for k in ("senders", "reposted_keys", "test_cards") for f in out[k])
    return out


def card_body(res: dict) -> str:
    lines = ["The daily Inbox junk audit (hermes-mods plugins/inbox/junk_audit.py) found cards that should not have",
             "reached David's Inbox in the last 7 days. For each sender/key below: find the poster, route what it",
             "reports to Kanban (agent-fixable) or nowhere (status), and keep only decisions David alone can make.",
             "Guards that should already refuse these: archived keys (store.ArchivedKey), noise breaker per sender",
             "group (PosterMuted), test/probe titles (JunkPost). A fresh hit means one of them was bypassed or this",
             "gateway runs an old hermes-mods: check `git -C ~/code/hermes-mods log -1` there.", ""]
    for f in res["senders"]:
        lines.append(f"- sender `{f['sender']}` on {f['gw']}: {f['archived_untouched']} archived untouched"
                     f"{' (NEW)' if f['fresh'] else ''}: " + "; ".join(str(t)[:60] for t in f["titles"]))
    for f in res["reposted_keys"]:
        lines.append(f"- key `{f['key']}` on {f['gw']} re-posted {f['reposts']}x after an archive"
                     f"{' (NEW)' if f['fresh'] else ''}: {str(f['title'])[:80]}")
    for f in res["test_cards"]:
        lines.append(f"- test/probe card on {f['gw']}{' (NEW)' if f['fresh'] else ''}: `{f['key']}` {str(f['title'])[:80]}")
    return "\n".join(lines)


def file_card(res: dict, now: float, runner=subprocess.run) -> str | None:
    day = time.strftime("%Y-%m-%d", time.gmtime(now))
    argv = ["hermes", "kanban", "create", f"Inbox junk audit {day}: junk reached the Inbox",
            "--assignee", "default", "--body-file", "-", "--json", "--created-by", "inbox-junk-audit",
            "--idempotency-key", f"inbox-junk-audit:{day}", "--no-requires"]
    p = runner(argv, input=card_body(res), capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        print(f"card create failed rc={p.returncode}: {(p.stderr or p.stdout)[-300:]}", file=sys.stderr)
        return None
    try:
        return json.loads(p.stdout or "{}").get("id")
    except ValueError:
        return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", help="read items from this file instead of `hermes-hub inbox --all --json`")
    ap.add_argument("--dry-run", action="store_true", help="print findings, file no card")
    a = ap.parse_args(argv)
    if a.json:
        items = json.loads(Path(a.json).read_text())
    else:
        p = subprocess.run(["hermes-hub", "inbox", "--all", "--json"], capture_output=True, text=True, timeout=300)
        if p.returncode != 0:
            print(f"hermes-hub inbox failed rc={p.returncode}: {p.stderr[-300:]}", file=sys.stderr)
            return 1
        items = json.loads(p.stdout)
    now = time.time()
    res = audit(items, now)
    card = file_card(res, now) if res["fresh"] and not a.dry_run else None
    print(json.dumps({"items_7d": res["window_items"], "senders": len(res["senders"]),
                      "reposted_keys": len(res["reposted_keys"]), "test_cards": len(res["test_cards"]),
                      "fresh": res["fresh"], "card": card}))
    if a.dry_run:
        print(card_body(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
