"""Cross-host skill usage: merge every host's/profile's skills/.usage.json (collected by collect_usage.sh).

A dump is text: "### <path>" lines, each followed by that .usage.json. Usage records are per Hermes home, so a
skill is idle only when no home on any host viewed or used it inside the window.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone


def parse_dump(text: str, host: str) -> dict[str, dict]:
    """{"host:path": usage dict} from one collect_usage.sh output."""
    out: dict[str, dict] = {}
    cur, buf = None, []
    for ln in text.splitlines() + ["### END"]:
        if ln.startswith("### "):
            if cur is not None and "".join(buf).strip():
                try:
                    out[f"{host}:{cur}"] = json.loads("\n".join(buf))
                except json.JSONDecodeError:
                    out[f"{host}:{cur}"] = {}
            cur, buf = ln[4:].strip(), []
        else:
            buf.append(ln)
    return out


def _ts(v) -> datetime | None:
    if not v:
        return None
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def merge(homes: dict[str, dict], now: datetime | None = None, days: int = 30) -> dict[str, dict]:
    """{skill: {use_count, view_count, last_used, homes_recent: [..], recent: bool}} across all homes."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    out: dict[str, dict] = {}
    for home, data in homes.items():
        for skill, rec in (data or {}).items():
            if not isinstance(rec, dict):
                continue
            agg = out.setdefault(skill, {"use_count": 0, "view_count": 0, "last_used": None, "homes_recent": []})
            agg["use_count"] += int(rec.get("use_count") or 0)
            agg["view_count"] += int(rec.get("view_count") or 0)
            last = max([t for t in (_ts(rec.get("last_used_at")), _ts(rec.get("last_viewed_at"))) if t], default=None)
            if last and (agg["last_used"] is None or last > agg["last_used"]):
                agg["last_used"] = last
            if last and last >= cutoff:
                agg["homes_recent"].append(home)
    for agg in out.values():
        agg["recent"] = bool(agg["homes_recent"])
        agg["last_used"] = agg["last_used"].isoformat() if agg["last_used"] else None
    return out


def idle(skills: list[str], merged: dict[str, dict]) -> list[str]:
    """Skills with no view/use in the window on any home (never-recorded skills count as idle)."""
    return sorted(s for s in skills if not merged.get(s, {}).get("recent"))
