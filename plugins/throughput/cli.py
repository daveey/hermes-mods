"""``hermes throttle``: read and set the fleet's token cap from a terminal (the same setting as the Runners panel).

    hermes throttle                 # the cap in force, the budget, the fleet rate and this gateway's slots
    hermes throttle set 150k        # cap the fleet at 150,000 tokens/min (k, m suffixes; plain numbers too)
    hermes throttle set 0           # no model call of a card or cron starts, and no card starts
    hermes throttle set off         # no cap of your own: the subscription budget alone (or no throttle)

The setting is stored on this gateway as a ``manual`` level and reaches every gateway through the hermes-hub
link within about a minute; the budget-publish host then caps at the lower of it and the budget.
"""
from __future__ import annotations

import re
import sys
import time
from typing import Optional

_AMOUNT = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*([kKmM]?)\s*$")


def parse_per_min(text: str) -> Optional[float]:
    """Tokens per minute from ``150k``, ``1.5m``, ``90000``; None for ``off``/``none``/``auto``. ValueError else."""
    if str(text).strip().lower() in ("off", "none", "auto", "budget"):
        return None
    match = _AMOUNT.match(str(text))
    if not match:
        raise ValueError(f"not a token amount: {text!r} (try 150k, 2m, 90000 or off)")
    return float(match.group(1)) * {"": 1, "k": 1e3, "m": 1e6}[match.group(2).lower()]


def fmt(per_s: Optional[float]) -> str:
    if per_s is None:
        return "none"
    per_min = per_s * 60.0
    return f"{per_min / 1e6:.2f}M/min" if per_min >= 1e6 else f"{per_min / 1e3:.1f}k/min"


def setup(parser) -> None:
    sub = parser.add_subparsers(dest="throttle_cmd")
    sub.add_parser("status", help="the cap in force, the budget and this gateway's slots (the default)")
    put = sub.add_parser("set", help="set the fleet's token cap in tokens/min (150k, 2m, 0, off)")
    put.add_argument("amount")


def _paths():
    from . import sampler, throttle
    root = sampler.hermes_root()
    return root, sampler.series_path(root), throttle.hub_db_path()


def status_lines(now: Optional[float] = None) -> list[str]:
    from . import slotgate, throttle
    root, series, hub = _paths()
    now = time.time() if now is None else now
    st = throttle.status(root, series, now, hub)
    b = st.get("budget") or {}
    gate = slotgate.local(root, series, st["cap_per_s"], now)
    running = slotgate.active_runs()
    allowed = "no limit" if gate["allowed"] is None else str(gate["allowed"])
    per_slot = "unmeasured" if gate["per_slot_per_s"] is None else fmt(gate["per_slot_per_s"])
    return [
        f"cap in force:   {fmt(st['cap_per_s'])}" + (" (every call held)" if st["cap_per_s"] == 0 else ""),
        f"your cap:       {fmt(st.get('user_cap_per_s'))}",
        f"budget:         {fmt(b.get('budget_per_s'))}" + (f" ({b['state']})" if b.get("state") else ""),
        f"fleet rate:     {fmt(st['rate_per_s'])}",
        f"this gateway:   {'?' if running is None else running} running, {allowed} allowed "
        f"(share {gate['share']:.0%}, {per_slot} per slot)",
    ]


def set_cap(amount: str, now: Optional[float] = None) -> Optional[float]:
    """Store a person's cap (tokens/min text) on this gateway; returns tokens/s (None = no cap of their own)."""
    from . import throttle
    per_min = parse_per_min(amount)
    root, _, hub = _paths()
    now = time.time() if now is None else now
    level = throttle.effective(root, hub, now)
    stamp = max(now, level["set_at"] + 0.002)  # outrank the level in force even when its setter's clock runs ahead
    value = throttle.write_cap(root, None if per_min is None else per_min / 60.0, stamp)
    throttle.note_seen(root, hub, stamp)
    return value


def handler(args) -> int:
    try:
        if getattr(args, "throttle_cmd", None) == "set":
            value = set_cap(args.amount)
            print(f"token cap set to {fmt(value)}; every gateway follows within about a minute")
        for line in status_lines():
            print(line)
        return 0
    except ValueError as exc:
        print(f"hermes throttle: {exc}", file=sys.stderr)
        return 2
