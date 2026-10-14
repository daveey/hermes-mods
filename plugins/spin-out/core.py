"""spin-out core: read a session's transcript and turn the follow-up work at its END into
self-contained child-session prompts (each with a pointer back to the parent).

Read-only on state.db. The LLM call is injected (`llm`) so tests can fake it; production uses
Hermes' auxiliary client (task "spin_out" -> `auxiliary.spin_out.*` config, else the auto model).
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Callable, Optional

MAX_TASKS = 6
TAIL_MSGS = 40          # transcript window sent to the planner
LAST_REPLY_CHARS = 9000  # the final assistant reply is where the follow-ups live: keep it whole
OLDER_MSG_CHARS = 900

LLM = Callable[[list, int], str]  # (messages, max_tokens) -> text


def hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home  # type: ignore
        return Path(get_hermes_home())
    except Exception:
        return Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))


def _db(path: Optional[Path] = None) -> sqlite3.Connection:
    p = path or hermes_home() / "state.db"
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=5)
    con.row_factory = sqlite3.Row
    return con


def _norm(s: str) -> str:
    return re.sub(r"[ \t]+", " ", (s or "")).strip()


def _lineage(con, session_id: str, depth: int = 4) -> list[str]:
    """This session plus its compression ancestors (oldest first)."""
    ids, cur = [], session_id
    for _ in range(depth + 1):
        row = con.execute("select id, parent_session_id from sessions where id=?", (cur,)).fetchone()
        if not row:
            break
        ids.append(row["id"])
        parent = row["parent_session_id"]
        if not parent:
            break
        prow = con.execute("select end_reason from sessions where id=?", (parent,)).fetchone()
        if not prow or "compress" not in str(prow["end_reason"] or ""):
            break
        cur = parent
    return list(reversed(ids))


def session_info(session_id: str, db_path: Optional[Path] = None) -> dict:
    con = _db(db_path)
    try:
        row = con.execute("select * from sessions where id=?", (session_id,)).fetchone()
        if not row:
            return {}
        keys = row.keys()
        info = {k: row[k] for k in ("id", "title", "cwd", "git_branch", "git_repo_root", "model", "profile_name")
                if k in keys}
        ids = _lineage(con, session_id)
        q = ",".join("?" * len(ids))
        msgs = con.execute(
            f"""select role, content, tool_calls from messages
                where session_id in ({q}) and role in ('user','assistant')
                and coalesce(display_kind,'user') in ('user','') order by id""", ids).fetchall()
        info["messages"] = [dict(m) for m in msgs]
        return info
    finally:
        con.close()


def touched_files(messages: list[dict], limit: int = 20) -> list[str]:
    seen: list[str] = []
    for m in messages:
        try:
            calls = json.loads(m.get("tool_calls") or "[]") or []
        except Exception:
            continue
        for call in calls if isinstance(calls, list) else []:
            fn = (call or {}).get("function") or {}
            if fn.get("name") not in ("write_file", "patch", "read_file"):
                continue
            try:
                path = json.loads(fn.get("arguments") or "{}").get("path")
            except Exception:
                path = None
            if path and path not in seen:
                seen.append(path)
    return seen[-limit:]


def _transcript(messages: list[dict]) -> str:
    msgs = [m for m in messages if (m.get("content") or "").strip()]
    window = msgs[-TAIL_MSGS:]
    out = []
    for i, m in enumerate(window):
        last = i == len(window) - 1 or (m["role"] == "assistant" and all(x["role"] != "assistant" for x in window[i + 1:]))
        cap = LAST_REPLY_CHARS if last else OLDER_MSG_CHARS
        text = _norm(m["content"])
        if len(text) > cap:
            text = text[:cap] + " …[truncated]"
        out.append(f"### {m['role'].upper()}\n{text}")
    return "\n\n".join(out)


def _parse_json(raw: str) -> dict:
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except Exception:
        return {}


PLAN_SYSTEM = """You split the follow-up work left at the END of an AI agent session into independent child sessions.

Find the follow-up work: next steps, open items, "not done yet", gaps, proposed fixes, things the assistant
offered or said should happen next, and user requests that were never completed. Look mainly at the LAST
assistant reply and the latest user messages. Ignore work that is already done.

Rules:
- One task per independent piece of work. Merge items that touch the same code/change. At most %d tasks.
- Skip items that only the human can do or that need the human to decide first (purchases, choosing between
  options, credentials, physical actions). List them under "skipped" with a reason.
- Each child starts with NO access to this transcript, so its prompt must be self-contained: what the goal is,
  why, the environment (working dir, repo, branch, hosts, services), exact file paths, commands, IDs, URLs,
  decisions already made and constraints, what was already tried or is done, and a concrete done-when check.
  Copy facts verbatim from the transcript; never invent paths, IDs or results.
- Write each prompt as a direct instruction to the child agent ("Fix ...", "Add ..."), in Markdown, 80-350 words.
- Set needs_parent=true when the child will likely need to read the parent transcript for details you could
  not fit (long logs, earlier designs).

Reply with ONLY JSON:
{"tasks": [{"title": "<=7 words", "prompt": "...", "needs_parent": bool}],
 "skipped": [{"item": "...", "reason": "..."}]}""" % MAX_TASKS


def plan(session_id: str, llm: LLM, db_path: Optional[Path] = None, auto: bool = False) -> dict:
    """``auto=True`` (fired after every finished turn) skips the planner unless the last reply hints at follow-ups."""
    info = session_info(session_id, db_path)
    if not info:
        return {"tasks": [], "skipped": [], "error": "unknown session"}
    idx, reply = last_reply(info)
    if idx < 0:
        return {"tasks": [], "skipped": [], "error": "session has no assistant reply yet"}
    if auto and not has_followups(reply):
        return {"tasks": [], "skipped": [], "auto_skip": "no follow-up cues in the last reply", "reply_index": idx}
    files = touched_files(info["messages"])
    user = (
        f"Session title: {info.get('title') or '(none)'}\n"
        f"Working dir: {info.get('cwd') or '(none)'}\n"
        f"Repo: {info.get('git_repo_root') or '(none)'}  branch: {info.get('git_branch') or '(none)'}\n"
        f"Files the session read/edited: {', '.join(files) or 'none'}\n\n"
        f"TRANSCRIPT (latest messages, oldest first):\n\n{_transcript(info['messages'])[-60000:]}"
    )
    try:
        out = _parse_json(llm([{"role": "system", "content": PLAN_SYSTEM}, {"role": "user", "content": user}], 6000))
    except Exception as exc:
        return {"tasks": [], "skipped": [], "error": f"planner failed: {type(exc).__name__}: {exc}"[:300]}
    tasks = []
    for t in (out.get("tasks") or [])[:MAX_TASKS]:
        if not isinstance(t, dict) or not str(t.get("prompt") or "").strip():
            continue
        tasks.append({"title": str(t.get("title") or "Follow-up").strip()[:80],
                      "prompt": str(t["prompt"]).strip(),
                      "needs_parent": bool(t.get("needs_parent"))})
    skipped = [{"item": str(s.get("item") or "")[:200], "reason": str(s.get("reason") or "")[:200]}
               for s in (out.get("skipped") or []) if isinstance(s, dict)]
    return {"tasks": tasks, "skipped": skipped, "title": info.get("title") or "", "cwd": info.get("cwd") or "",
            "profile": info.get("profile_name") or "default", "reply_index": idx}


FOLLOWUP_CUES = re.compile(
    r"(?i)\b(next steps?|follow[- ]ups?|still (open|owed|to do|todo|needed|pending|missing)|not (done|built|wired|tested) yet"
    r"|remaining|left (to do|for you|open)|todo\b|pending|is owed|next is|next up|phase \d|want me to|shall i|should i"
    r"|i can (also|then)|after that|later,? (i|we)|open (items|threads|questions)|not yet)\b")


def last_reply(info: dict) -> tuple[int, str]:
    """(index, text) of the final assistant message with content; (-1, '') when none."""
    msgs = info.get("messages") or []
    for i in range(len(msgs) - 1, -1, -1):
        m = msgs[i]
        if m["role"] == "assistant" and (m.get("content") or "").strip():
            return i, m["content"]
    return -1, ""


def has_followups(text: str) -> bool:
    """Cheap pre-filter for the automatic check: only spend a planner call when the last reply hints at more work."""
    return bool(FOLLOWUP_CUES.search(text or ""))


def kanban_cards(tasks: list[dict], parent_id: str, profile: str, cwd: str = "", board: Optional[str] = None,
                 kb=None) -> dict:
    """Create one Kanban card per task (title + full child message as body) on ``board`` (None = active board).

    Cards are assigned to ``profile``, linked to the parent session (``session_id``), and work in the parent's
    directory when it still exists (``dir`` workspace), else the board default. ``idempotency_key`` makes a
    second click return the same card instead of a duplicate. ``kb`` = (kanban_db, kanban_db_connect) for tests.
    """
    if kb is None:
        from hermes_cli import kanban_db, kanban_db_connect  # type: ignore
        kb = (kanban_db, kanban_db_connect)
    db, connect = kb
    try:
        db.init_db(board=board)
    except Exception:
        pass
    ws = {"workspace_kind": "dir", "workspace_path": cwd} if cwd and os.path.isdir(cwd) else {}
    out, slug = [], board
    con = connect.connect(board=board)
    try:
        try:
            slug = board or db.get_current_board()
        except Exception:
            slug = board or "default"
        for t in tasks:
            title = str(t.get("title") or "Follow-up").strip()[:80]
            try:
                tid = db.create_task(con, title=title, body=t.get("message") or t.get("prompt") or "",
                                     assignee=profile or "default", created_by="spin-out", session_id=parent_id,
                                     idempotency_key=f"spin-out:{parent_id}:{title}", board=board, **ws)
                out.append({"title": title, "task_id": tid})
            except Exception as exc:
                out.append({"title": title, "error": f"{type(exc).__name__}: {exc}"[:300]})
    finally:
        con.close()
    return {"board": slug, "cards": out}


def child_message(task: dict, parent_id: str, profile: str, parent_title: str) -> str:
    ref = f"@session:{profile or 'default'}/{parent_id}"
    named = f' ("{parent_title}")' if parent_title else ""
    if task.get("needs_parent"):
        tail = (f"Spun out of parent session {ref}{named}. Read the parent transcript for details this brief "
                "does not cover before you start.")
    else:
        tail = (f"Spun out of parent session {ref}{named}. The brief above should be enough; read the parent only "
                "if you hit a gap.")
    return f"{task['prompt']}\n\n---\n{tail}"


def aux_llm(messages: list, max_tokens: int) -> str:
    from agent.auxiliary_client import call_llm  # type: ignore
    resp = call_llm(task="spin_out", messages=messages, max_tokens=max_tokens, temperature=0, timeout=120)
    return (getattr(resp.choices[0].message, "content", None) or "").strip()
