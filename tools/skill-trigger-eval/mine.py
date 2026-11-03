"""Mine real phrasings: user messages that immediately preceded a skill_view(skill) in a human chat.

stdlib only, so it can run on another host: `ssh host python3 - < mine.py` prints JSON {skill: [asks]}.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path


_HUMAN_SOURCES = ("desktop", "cli", "tui", "local", "telegram", "slack", "discord", "imessage", "signal", "whatsapp")


def mine_phrasings(db: Path, max_len: int = 400, per_skill: int = 6, min_len: int = 25) -> dict[str, list[str]]:
    """{skill: [user message that immediately preceded a skill_view(skill) in a human chat]}.

    Parses tool_calls as JSON (a regex over the raw string misses escaped arguments)."""
    out: dict[str, list[str]] = {}
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
    try:
        q = (f"select m.session_id, m.role, m.content, m.tool_calls from messages m join sessions s on s.id=m.session_id "
             f"where s.source in ({','.join('?' * len(_HUMAN_SOURCES))}) and m.role in ('user','assistant') "
             f"order by m.session_id, m.id")
        last_user: dict[str, str] = {}
        for sid, role, content, tool_calls in con.execute(q, _HUMAN_SOURCES):
            if role == "user":
                text = (content or "").strip()
                last_user[sid] = text if min_len <= len(text) <= max_len and not text.startswith(("[", "<", "#")) else ""
                continue
            if not tool_calls or not last_user.get(sid):
                continue
            try:
                calls = json.loads(tool_calls)
            except Exception:
                continue
            for c in calls if isinstance(calls, list) else []:
                fn = (c or {}).get("function") or {}
                if fn.get("name") != "skill_view":
                    continue
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except Exception:
                    continue
                name = args.get("name")
                if isinstance(name, str) and not args.get("file_path"):
                    lst = out.setdefault(name, [])
                    if last_user[sid] not in lst and len(lst) < per_skill:
                        lst.append(last_user[sid])
            last_user[sid] = ""  # only the first assistant turn after the ask counts
    finally:
        con.close()
    return out




if __name__ == "__main__":
    db = Path(sys.argv[1] if len(sys.argv) > 1 else Path.home() / ".hermes" / "state.db").expanduser()
    print(json.dumps(mine_phrasings(db)))
