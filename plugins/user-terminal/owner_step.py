"""kanban_unblock of an owner-terminal ask: say that the owner step still has to run (self-heal kanban-e23aa32ffd).

A card whose run needs a command only the owner's own chat may launch (run_in_user_terminal refuses Kanban workers)
blocks with ``human_request.interaction = "user_terminal"`` and the canonical host_only ask ("From your own Hermes
chat open the owner-terminal flow for task t_…"). When David then says "approved, unblock", the chat called
``kanban_unblock`` and reported "ready and queued": the step the approval was for never ran, and no worker could run
it. ``kanban_unblock`` returns only status/queue/priority fields, so nothing told the chat otherwise.

``transform_unblock_result`` is the plugin's ``transform_tool_result`` hook body for ``kanban_unblock``. In a chat (no
Kanban worker identity in the environment), after a successful unblock (status ``ready``), it reads the task's latest
``blocked`` event (the unblock already cleared ``tasks.human_request``). When that ask was an owner-terminal step
(``interaction == "user_terminal"``, or its text names host_only / the owner-terminal flow) and nothing shows the step
ran after the block, it adds ``next_step`` (and ``owner_step``: the ask's action/scope) to the result. Proof the step
ran is either a local user-terminal job for the task (meta owner card == task, or the task id in its reason/command)
whose final status is ``ran`` and whose final status file is newer than the block, or a card comment after the
block carrying a user-terminal job id (a screenless gateway relays the job to the Mac, so the job store is not
local; the asking-the-user skill has the chat comment the job id and log path). Read-only (``mode=ro`` +
``query_only``); any read error logs a warning and leaves the result unchanged.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

log = logging.getLogger("user_terminal")

WORKER_ENV = ("HERMES_KANBAN_TASK", "HERMES_KANBAN_WORKER_TASK", "HERMES_MANAGED_WORKER")
_OWNER_TEXT = re.compile(r"host[_ -]only|owner[- ]terminal", re.I)
JOB_ID = re.compile(r"\b\d{8}-\d{6}-[0-9a-f]{6}\b")  # jobs_dir() names: %Y%m%d-%H%M%S-<6 hex>
NEXT_STEP = ("This card's ask was an owner-terminal step (interaction=user_terminal, host_only). Unblocking did not "
             "run it, and no Kanban worker can run it. Run it now from this chat with run_in_user_terminal (the "
             "command is in owner_step.scope / the card's ask and handoff comments), comment the job id and result "
             "on the card, then let the card verify. Do not report 'ready'/'queued' or a priority raise as progress.")


def _ro(path: str) -> sqlite3.Connection:
    uri = "file:" + quote(str(path))
    try:
        con = sqlite3.connect(uri + "?mode=ro", uri=True, timeout=2)
        con.execute("select 1 from sqlite_master limit 1")
    except sqlite3.OperationalError:
        con = sqlite3.connect(uri + "?mode=rw", uri=True, timeout=2)  # WAL without sidecars; never creates a db
    con.execute("PRAGMA query_only=ON")
    return con


def owner_terminal_request(payload: dict) -> dict | None:
    """The block's human_request when it asks for an owner-terminal step, else None."""
    req = payload.get("human_request")
    if not isinstance(req, dict):
        return None
    if str(req.get("interaction") or "").strip().lower() == "user_terminal":
        return req
    text = " ".join(str(req.get(k) or "") for k in ("action", "why_human")) + " " + str(payload.get("reason") or "")
    return req if _OWNER_TEXT.search(text) else None


def _job_ran_for(task: str, jobs: Path, since: float) -> bool:
    if not jobs.is_dir():
        return False
    for job in jobs.iterdir():
        if not job.is_dir():
            continue
        final = job / "final.lock" / "result.json"
        status_file = final if final.exists() else job / "status.json"
        try:
            if json.loads(status_file.read_text()).get("status") != "ran" or status_file.stat().st_mtime <= since:
                continue
        except (OSError, ValueError, AttributeError):
            continue
        try:
            owner = (json.loads((job / "meta.json").read_text()) or {}).get("owner") or {}
        except (OSError, ValueError):
            owner = {}
        if isinstance(owner, dict) and task in (owner.get("task_id"), owner.get("task")):
            return True
        text = ""
        for name in ("reason.txt", "cmd.sh"):
            try:
                text += (job / name).read_text(errors="replace")
            except OSError:
                pass
        if re.search(rf"\b{re.escape(task)}\b", text):
            return True
    return False


def transform_unblock_result(tool_name: str, args: Any, result: Any, *, db_path: Callable[[str | None], str],
                             jobs_root: Callable[[], Path]) -> str | None:
    """New result string for a chat's kanban_unblock of an unrun owner-terminal step, else None (unchanged)."""
    if tool_name != "kanban_unblock" or any(os.environ.get(k) for k in WORKER_ENV):
        return None
    try:
        res = json.loads(result) if isinstance(result, str) else None
    except ValueError:
        return None
    if not isinstance(res, dict) or res.get("ok") is not True or res.get("status") != "ready":
        return None
    task = str((args or {}).get("task_id") or res.get("task_id") or "")
    if not task:
        return None
    try:
        con = _ro(db_path((args or {}).get("board")))
        try:
            row = con.execute("SELECT payload, created_at FROM task_events WHERE task_id=? AND kind='blocked' "
                              "ORDER BY id DESC LIMIT 1", (task,)).fetchone()
            if not row:
                return None
            payload = json.loads(row[0] or "{}")
            req = owner_terminal_request(payload if isinstance(payload, dict) else {})
            if req is None:
                return None
            blocked_at = float(row[1] or 0)
            comments = [b for (b,) in con.execute(
                "SELECT body FROM task_comments WHERE task_id=? AND created_at>=?", (task, int(blocked_at)))]
        finally:
            con.close()
        if any(JOB_ID.search(str(b or "")) for b in comments) or _job_ran_for(task, Path(jobs_root()), blocked_at):
            return None
    except Exception as exc:  # fail open: the unblock already committed; this is advisory
        log.warning("user_terminal owner_step: could not check kanban_unblock of %s: %s", task, exc)
        return None
    res["next_step"] = NEXT_STEP
    res["owner_step"] = {k: str(req.get(k) or "")[:1500] for k in ("interaction", "action", "scope")}
    return json.dumps(res)
