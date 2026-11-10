"""workflow-state-hint: say a project-work workflow's real state next to its cards.

A project-work workflow (plugins/project-work) runs as several Kanban cards (implement, review, release), but its
own state lives in a separate ledger (``<home>/project-work/ledger.sqlite3``, ``workflows.data``): ``state``,
``error`` and ``result_receipt`` (set once the result is published). No Kanban tool shows that ledger, so an agent
that sees a card move from blocked to review can report the workflow "recovered" while the workflow still holds an
error and has published nothing (self-heal agent-55c981ebe2).

This hook reads the ledgers read-only and, when a ``kanban_*``/``terminal``/``execute_code``/``tool_call`` result
names a card of a workflow that has an error or is not finished, adds one line per workflow (at most 3):

    [workflow-state] card t_X belongs to workflow <id>: state=<s>, error=<e>, published=<yes/no>. ...

It fails open: a missing, locked or unreadable ledger leaves the result unchanged. It never writes a ledger.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

MARK = "[workflow-state]"
KEY = "workflow_state"
CARD_RE = re.compile(r"\bt_[0-9a-f]{16}\b")
TOOLS = {"terminal", "execute_code", "tool_call", "kanban_show", "kanban_list"}
# Workflow states that mean the run is over; with no error recorded these get no hint.
FINISHED = {"posted", "archived", "invalidated", "refused_retired", "done", "completed"}
MAX_WORKFLOWS = 3
BUDGET_S = 0.1
_ERR_CHARS = 200

# path -> ((mtime_ns, size, wal_mtime_ns, wal_size), {card_id: summary})
_cache: dict[str, tuple[tuple, dict[str, dict]]] = {}


def _hermes_home() -> Path:
    return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")


def ledgers(home: Optional[Path] = None) -> list[Path]:
    home = home or _hermes_home()
    found = [home / "project-work" / "ledger.sqlite3"]
    try:
        found += sorted((home / "profiles").glob("*/project-work/ledger.sqlite3"))
    except OSError:
        pass
    return [p for p in found if p.is_file()]


def _stamp(p: Path) -> tuple:
    st = p.stat()
    try:
        w = Path(str(p) + "-wal").stat()
        wal = (w.st_mtime_ns, w.st_size)
    except OSError:
        wal = (0, 0)
    return (st.st_mtime_ns, st.st_size) + wal


def _read_index(p: Path, budget_s: float) -> dict[str, dict]:
    """card id -> workflow summary for one ledger. Read-only: rw open (never creates: the file exists) plus
    query_only, because a bare ro open fails on a WAL db whose sidecars a writer just removed."""
    con = sqlite3.connect(f"file:{p}?mode=rw", uri=True, timeout=max(budget_s, 0.0))
    try:
        con.execute("PRAGMA query_only=ON")
        con.execute(f"PRAGMA busy_timeout={int(max(budget_s, 0.0) * 1000)}")
        index: dict[str, dict] = {}
        for wid, raw in con.execute("select workflow_id, data from workflows"):
            try:
                d = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if not isinstance(d, dict):
                continue
            summary = {"workflow_id": str(d.get("workflow_id") or wid), "state": d.get("state"),
                       "error": d.get("error"), "published": bool(d.get("result_receipt")),
                       "stages": {}}
            cards = d.get("cards")
            if not isinstance(cards, dict):
                cards = {}
            for stage, card in cards.items():
                cid = card.get("id") if isinstance(card, dict) else None
                if isinstance(cid, str):
                    summary["stages"][cid] = str(stage)
            for cid in summary["stages"]:
                index[cid] = summary
        return index
    finally:
        con.close()


def _index(p: Path, budget_s: float) -> dict[str, dict]:
    key = str(p)
    stamp = _stamp(p)
    hit = _cache.get(key)
    if hit and hit[0] == stamp:
        return hit[1]
    idx = _read_index(p, budget_s)
    _cache[key] = (stamp, idx)
    return idx


def _needs_hint(s: dict) -> bool:
    return bool(s.get("error")) or str(s.get("state") or "") not in FINISHED


def hint_line(card: str, s: dict) -> str:
    err = s.get("error")
    err_txt = " ".join(str(err).split())[:_ERR_CHARS] if err else "none"
    stage = s["stages"].get(card)
    others = ", ".join(f"{st} {cid}" for cid, st in s["stages"].items() if cid != card)
    return (f"{MARK} card {card}{f' ({stage} stage)' if stage else ''} belongs to project-work workflow "
            f"{s['workflow_id']}: state={s.get('state')}, error={err_txt}, "
            f"published={'yes' if s.get('published') else 'no'}"
            f"{f'; other cards: {others}' if others else ''}. "
            "A card status change is not workflow recovery; say which stage is still open.")


def hints_for(text: str, home: Optional[Path] = None) -> list[str]:
    ids = list(dict.fromkeys(CARD_RE.findall(text)))
    if not ids:
        return []
    deadline = time.monotonic() + BUDGET_S
    out: list[str] = []
    seen: set[str] = set()
    for p in ledgers(home):
        left = deadline - time.monotonic()
        if left <= 0:
            break
        try:
            idx = _index(p, left)
        except (sqlite3.Error, OSError, ValueError):
            continue  # missing/locked/corrupt ledger: fail open
        for cid in ids:
            s = idx.get(cid)
            if not s or s["workflow_id"] in seen or not _needs_hint(s):
                continue
            seen.add(s["workflow_id"])
            out.append(hint_line(cid, s))
            if len(out) >= MAX_WORKFLOWS:
                return out
    return out


def _tool(tool_name: str) -> str:
    return (tool_name or "").rsplit("__", 1)[-1]


def transform_tool_result(tool_name: str = "", args: Any = None, result: Any = None, **_) -> Optional[str]:
    try:
        if not isinstance(result, str) or _tool(tool_name) not in TOOLS or MARK in result:
            return None
        lines = hints_for(result)
        if not lines:
            return None
        try:
            data = json.loads(result)
        except (ValueError, TypeError):
            data = None
        if isinstance(data, dict):
            data[KEY] = "\n".join(lines)
            return json.dumps(data, ensure_ascii=False)
        return result.rstrip("\n") + "\n\n" + "\n".join(lines)
    except Exception:  # noqa: BLE001 - a hint must never break a tool result
        return None


def register(ctx) -> None:
    ctx.register_hook("transform_tool_result", transform_tool_result)
