"""topic-shift core: detect a topic change in a just-sent message, and build a
context brief for carrying a session's state into a new session.

Pure logic + state.db reads (read-only). The LLM call is injected (`llm`) so
tests can fake it; production uses Hermes' auxiliary client (task
"topic_shift" → `auxiliary.topic_shift.*` config, else auto/cheap model).
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Callable, Optional

MIN_WORDS = 4
MIN_PRIOR_USER_MSGS = 3
CONFIDENCE_THRESHOLD = 0.75
PRIOR_USER_WINDOW = 8
BRIEF_MSG_WINDOW = 60

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
    return re.sub(r"\s+", " ", (s or "")).strip()


def _lineage(con, session_id: str, depth: int = 3) -> list[str]:
    """This session plus compression ancestors (oldest first)."""
    ids, cur = [], session_id
    for _ in range(depth + 1):
        row = con.execute("select id, parent_session_id, end_reason from sessions where id=?", (cur,)).fetchone()
        if not row:
            break
        ids.append(row["id"])
        parent = row["parent_session_id"]
        if not parent:
            break
        prow = con.execute("select end_reason from sessions where id=?", (parent,)).fetchone()
        # Only follow compression continuations, not subagent parents.
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
        info = {k: row[k] for k in ("id", "title", "cwd", "git_branch", "git_repo_root", "model", "profile_name")
                if k in row.keys()}
        ids = _lineage(con, session_id)
        q = ",".join("?" * len(ids))
        msgs = con.execute(
            f"""select role, content, tool_calls, display_kind from messages
                where session_id in ({q}) and role in ('user','assistant')
                and coalesce(display_kind,'user') in ('user','') order by id""", ids).fetchall()
        info["messages"] = [dict(m) for m in msgs]
        return info
    finally:
        con.close()


def skip_reason(text: str, prior_user: list[str]) -> Optional[str]:
    t = (text or "").strip()
    if not t:
        return "empty"
    if t.startswith("/"):
        return "slash"
    if len(t.split()) < MIN_WORDS:
        return "short"
    if len(prior_user) < MIN_PRIOR_USER_MSGS:
        return "young_session"
    return None


def _prior_user(info: dict, text: str) -> list[str]:
    users = [_norm(m["content"]) for m in info.get("messages", []) if m["role"] == "user" and m["content"]]
    # The just-sent message is usually already persisted: drop it.
    if users and users[-1][:200] == _norm(text)[:200]:
        users = users[:-1]
    return users


def _parse_json(raw: str) -> dict:
    m = re.search(r"\{.*\}", raw or "", re.S)
    return json.loads(m.group(0)) if m else {}


CLASSIFY_SYSTEM = (
    "You decide whether a user's newest chat message starts a NEW, UNRELATED task compared with the "
    "ongoing session, or continues/follows up on it. Follow-ups, refinements, bug reports about the same "
    "work, 'also do X' on the same project, and questions about results are NOT new topics. A new topic "
    "is a different goal that would be better handled in a fresh session (e.g. switching from debugging a "
    "voice pipeline to filing taxes, or to an unrelated subsystem). Reply with ONLY JSON: "
    '{"new_topic": bool, "confidence": 0..1, "label": "<=6 word name of the NEW message topic", '
    '"reason": "<=15 words"}'
)


def classify(session_id: str, text: str, llm: LLM, db_path: Optional[Path] = None) -> dict:
    info = session_info(session_id, db_path)
    if not info:
        return {"shift": False, "skipped": "unknown_session"}
    prior = _prior_user(info, text)
    if (why := skip_reason(text, prior)):
        return {"shift": False, "skipped": why}
    recent = prior[-PRIOR_USER_WINDOW:]
    last_asst = next((_norm(m["content"])[:600] for m in reversed(info["messages"])
                      if m["role"] == "assistant" and m["content"]), "")
    user = (
        f"Session title: {info.get('title') or '(none)'}\n"
        f"Working dir: {info.get('cwd') or '?'}\n"
        "Recent user messages (oldest first):\n" + "\n".join(f"- {u[:300]}" for u in recent) +
        f"\nLast assistant reply (truncated): {last_asst}\n\nNEWEST message:\n{_norm(text)[:1500]}"
    )
    try:
        out = _parse_json(llm([{"role": "system", "content": CLASSIFY_SYSTEM},
                               {"role": "user", "content": user}], 150))
    except Exception as exc:  # fail open: never block the user
        return {"shift": False, "skipped": f"llm_error: {type(exc).__name__}"}
    conf = float(out.get("confidence") or 0)
    shift = bool(out.get("new_topic")) and conf >= CONFIDENCE_THRESHOLD
    return {"shift": shift, "confidence": conf, "label": str(out.get("label") or "")[:60],
            "reason": str(out.get("reason") or "")[:140], "title": info.get("title") or ""}


def _touched_files(messages: list[dict], limit: int = 15) -> list[str]:
    seen: list[str] = []
    for m in messages:
        for call in json.loads(m.get("tool_calls") or "[]") if m.get("tool_calls") else []:
            fn = (call.get("function") or {})
            if fn.get("name") not in ("write_file", "patch", "read_file"):
                continue
            try:
                path = json.loads(fn.get("arguments") or "{}").get("path")
            except Exception:
                path = None
            if path and path not in seen:
                seen.append(path)
    return seen[-limit:]


BRIEF_SYSTEM = (
    "You write a compact hand-off brief so a NEW agent session can continue work without the old transcript. "
    "Use only facts present in the transcript. Markdown, <=220 words, sections: **Was working on**, "
    "**Environment** (repo/dir/branch/hosts), **Key files**, **Decisions & facts**, **Open issues**, and "
    "**Relevant to the new request** (what from the old session matters for the NEW message; say 'nothing "
    "specific' if none). Omit empty sections."
)


def brief(session_id: str, new_text: str, llm: LLM, db_path: Optional[Path] = None) -> dict:
    info = session_info(session_id, db_path)
    if not info:
        return {"brief": "", "title": ""}
    msgs = [m for m in info["messages"] if m["content"]]
    # The just-sent message may still be in the transcript (with a partial reply after it):
    # cut from its last occurrence onward so it only appears as the NEW REQUEST.
    key = _norm(new_text)[:200]
    for i in range(len(msgs) - 1, max(len(msgs) - 6, -1), -1):
        if msgs[i]["role"] == "user" and _norm(msgs[i]["content"])[:200] == key:
            msgs = msgs[:i]
            break
    window = msgs[-BRIEF_MSG_WINDOW:]
    transcript = "\n".join(f"{m['role'].upper()}: {_norm(m['content'])[:700]}" for m in window)
    files = _touched_files(info["messages"])
    user = (
        f"Title: {info.get('title')}\nCwd: {info.get('cwd')}\nRepo: {info.get('git_repo_root')} "
        f"branch {info.get('git_branch')}\nFiles touched: {', '.join(files) or 'none'}\n\n"
        f"TRANSCRIPT (latest {len(window)} messages):\n{transcript[-24000:]}\n\nNEW REQUEST:\n{_norm(new_text)[:1500]}"
    )
    try:
        text = llm([{"role": "system", "content": BRIEF_SYSTEM}, {"role": "user", "content": user}], 600).strip()
    except Exception as exc:
        text = f"(brief unavailable: {type(exc).__name__}) Files touched: {', '.join(files) or 'none'}"
    return {"brief": text, "title": info.get("title") or "", "cwd": info.get("cwd") or "",
            "profile": info.get("profile_name") or "default"}


def seeded_message(session_id: str, profile: str, title: str, brief_md: str, text: str) -> str:
    ref = f"@session:{profile or 'default'}/{session_id}"
    head = f'Context carried over from {ref} ("{title}"):' if title else f"Context carried over from {ref}:"
    return f"{head}\n\n{brief_md}\n\n---\n\n{text}"


def aux_llm(messages: list, max_tokens: int) -> str:
    from agent.auxiliary_client import call_llm  # type: ignore
    resp = call_llm(task="topic_shift", messages=messages, max_tokens=max_tokens, temperature=0, timeout=30)
    choice = resp.choices[0]
    return (getattr(choice.message, "content", None) or "").strip()
