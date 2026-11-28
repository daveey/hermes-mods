"""Rank what fills re-sent context. Usage: context_carry.py <state.db> [days]
For every active message: carried = chars x assistant turns after it in the session (times re-read).
Prints JSON {sessions, role_chars: [[ [source, role|tool:name], count, chars, carried ], ...]}."""
import sqlite3, sys, time, json
from collections import defaultdict
db = sys.argv[1]; days = float(sys.argv[2]) if len(sys.argv) > 2 else 3
def _ro_connect(path):
    """Read rows without creating a missing DB; allow WAL sidecar setup if needed."""
    from pathlib import Path
    uri = Path(path).expanduser().resolve().as_uri()
    con = None
    try:
        con = sqlite3.connect(uri + "?mode=ro", uri=True)
        con.execute("PRAGMA query_only=ON")
        con.execute("SELECT 1 FROM sqlite_master LIMIT 1")
        return con
    except sqlite3.OperationalError:
        if con is not None:
            con.close()
    # mode=rw can initialize WAL sidecars but cannot create a missing database.
    con = sqlite3.connect(uri + "?mode=rw", uri=True)
    try:
        con.execute("PRAGMA query_only=ON")
        con.execute("SELECT 1 FROM sqlite_master LIMIT 1")
        return con
    except BaseException:
        con.close()
        raise

con = _ro_connect(db)
since = time.time() - days * 86400
sess = con.execute("select id, source from sessions where started_at>? and api_call_count>0", (since,)).fetchall()
acc = defaultdict(lambda: [0, 0, 0.0])
for sid, src in sess:
    msgs = con.execute("""select role, length(coalesce(content,''))+length(coalesce(tool_calls,'')), tool_name
      from messages where session_id=? and active=1 order by id""", (sid,)).fetchall()
    n_asst = sum(1 for m in msgs if m[0] == "assistant")
    seen = 0
    for role, ln, tn in msgs:
        if role == "assistant":
            seen += 1
        a = acc[(src, role if role != "tool" else "tool:" + (tn or "?"))]
        a[0] += 1; a[1] += ln; a[2] += ln * max(0, n_asst - seen)
print(json.dumps({"sessions": len(sess),
  "role_chars": sorted([[list(k)] + v for k, v in acc.items()], key=lambda x: -x[3])[:60]}))
