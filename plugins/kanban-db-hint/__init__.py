"""kanban-db-hint: give a refused Kanban board read a next step.

A Kanban tool that refuses a board ("Refusing to open corrupt kanban DB ...", or the newer
KanbanDbUnreliableViewError wording) used to give the agent nothing to act on. Often the board is fine and only
this process has a stale view (it still holds a deleted WAL/SHM), so every kanban_* tool in the session fails,
kanban_create included. This hook appends one hint to that result: the read-only check to run from a fresh process,
and what to do for each answer.

Optional config ``<HERMES_HOME>/kanban-db-hint.json``: ``{"owner_cards": ["t_..."]}`` names the cards that own
the stale-view fix, so the agent can point at them instead of opening a new card it can't create.

A kanban_show "task <id> not found" gets a second hint: the card may live on another gateway's board, so read it with
cross_task instead of retrying kanban_show.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Optional

MARK = "kanban-db-hint:"
# Core's KanbanDbCorruptError, and PR daveey/hermes#125's KanbanDbUnreliableViewError (two variants).
PATTERNS = re.compile(r"Refusing to open corrupt kanban DB|attached to a deleted WAL/SHM generation"
                      r"|integrity probe disagrees with an independent process|KanbanDbUnreliableViewError")
DB_PATH = re.compile(r"kanban DB at (/\S+?\.db)\b")


def _hermes_home() -> Path:
    return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")


def owner_cards() -> list[str]:
    try:
        data = json.loads((_hermes_home() / "kanban-db-hint.json").read_text())
    except (OSError, ValueError):
        return []
    cards = data.get("owner_cards") if isinstance(data, dict) else None
    return [str(c) for c in cards if isinstance(c, str) and c.strip()] if isinstance(cards, list) else []


def check_command(db: str) -> str:
    """A fresh-process, read-only check. No heredoc or inline script: those wait for approval."""
    return f"sqlite3 -readonly {db} 'pragma quick_check'"


def hint_text(message: str) -> str:
    m = DB_PATH.search(message)
    db = m.group(1) if m else "<board db>"
    where = "" if m else " (~/.hermes/kanban.db, or ~/.hermes/kanban/boards/<slug>/kanban.db)"
    cards = owner_cards()
    owners = (f" The stale-view fix is owned by {', '.join(cards)} (check it with `hermes kanban show {cards[0]}` "
              "in the terminal): name that card in your reply rather than opening a new one." if cards else "")
    return (f"{MARK} this may be a stale view in THIS process, not a damaged board. "
            f"1) Check from a fresh process: `{check_command(db)}`{where}. It is read-only and needs no approval. "
            "2) If it prints ok, the board is fine: this backend holds an out-of-date view, so every kanban_* tool "
            "in this session fails until the backend restarts, kanban_create included. Don't call it corruption. "
            "`hermes kanban show/list` in the terminal runs in a fresh process and still works." + owners +
            " Ask for a restart of this backend with action_request (tool_call name action_request). "
            "3) If it does not print ok, the file really is damaged: don't REINDEX, VACUUM, repair or replace the "
            "live file. Report the quick_check output and ask for recovery with action_request.")


def _is_kanban_tool(tool_name: str, args: Any) -> bool:
    name = (tool_name or "").rsplit("__", 1)[-1]
    if name.startswith("kanban_"):
        return True
    if name == "tool_call":  # deferred kanban tools arrive through the tool_call bridge
        return "kanban_" in json.dumps(args, default=str) if args else True
    return False


# kanban_show's missing-card error. Core says "task <id> not found" (tool layer may prefix "kanban_show: ").
# A card created on another gateway (hub, a worker node) is not on this host's board, so a bare kanban_show
# fails and agents kept retrying it. Only the read tool is covered: a mutation refusal must stay as-is.
MISSING = re.compile(r"^(?:kanban_show: )?task (t_[0-9A-Za-z_]+) not found\.?$")


def missing_hint(task_id: str, board: Optional[str] = None) -> str:
    where = f"board '{board}' on this gateway" if board else "this gateway's board"
    return (f"{MARK} {task_id} is not on {where}. That is not proof it doesn't exist: cards made on another "
            "gateway (hub, a worker node) or another board live there, and kanban_show cannot see them. "
            f"Don't retry kanban_show. Read it with cross_task(action='read', target='{task_id}') "
            "(tool_call name cross_task; a bare id is searched on every gateway). If that also finds nothing, "
            "report the id as unknown rather than guessing its state.")


def _calls_kanban_show(tool_name: str, args: Any) -> Optional[dict]:
    """The kanban_show args when this result is a kanban_show call (direct or via tool_call), else None."""
    name = (tool_name or "").rsplit("__", 1)[-1]
    if name == "kanban_show":
        return args if isinstance(args, dict) else {}
    if name == "tool_call" and isinstance(args, dict):
        calls = args.get("calls")
        shows = [c for c in calls if isinstance(c, dict) and str(c.get("name", "")).rsplit("__", 1)[-1]
                 == "kanban_show"] if isinstance(calls, list) else []
        if shows:
            a = shows[0].get("arguments")
            return a if isinstance(a, dict) else {}
    return None


def _missing_card(tool_name: str, args: Any, result: str) -> Optional[str]:
    """Hint for kanban_show's exact "task <id> not found" error. Core unwraps tool_call before this hook,
    so the bridge branch in _calls_kanban_show is only a fallback for older runtimes."""
    show_args = _calls_kanban_show(tool_name, args)
    if show_args is None:
        return None
    board = show_args.get("board") if isinstance(show_args.get("board"), str) else None
    try:
        data = json.loads(result)
    except (ValueError, TypeError):
        data = None
    if data is not None:
        err = data.get("error") if isinstance(data, dict) else None
        m = MISSING.match(err.strip()) if isinstance(err, str) else None
        if not m:  # a card body or comment that merely quotes the phrase is not this error
            return None
        data["hint"] = missing_hint(m.group(1), board)
        return json.dumps(data, ensure_ascii=False)
    m = MISSING.match(re.sub(r"^Error: ", "", result.strip()))
    return result + "\n\n" + missing_hint(m.group(1), board) if m else None


def transform_tool_result(tool_name: str = "", args: Any = None, result: Any = None, **_) -> Optional[str]:
    if not isinstance(result, str) or MARK in result:
        return None
    if not PATTERNS.search(result):
        return _missing_card(tool_name, args, result)
    if not _is_kanban_tool(tool_name, args):
        return None
    try:
        data = json.loads(result)
    except (ValueError, TypeError):
        data = None
    if isinstance(data, dict):
        data["hint"] = hint_text(result)
        return json.dumps(data, ensure_ascii=False)
    return result + "\n\n" + hint_text(result)


def register(ctx) -> None:
    ctx.register_hook("transform_tool_result", transform_tool_result)
