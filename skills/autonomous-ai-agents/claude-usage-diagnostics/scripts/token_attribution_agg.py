"""Aggregate Hermes session token usage from one state.db. Usage: token_attribution_agg.py <state.db> [days]
Run with ~/.hermes/bin/hermes-core-python (read-only URI open). Prints JSON; rows are
[key, sessions, in, out, cache_read, cache_write, api_calls, weighted, est_cost_usd]."""
import sqlite3, sys, time, re, json, os
from collections import defaultdict
db = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser("~/.hermes/state.db")
days = float(sys.argv[2]) if len(sys.argv) > 2 else 7
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
rows = con.execute("""select source, coalesce(model,''), coalesce(billing_provider,''), coalesce(title,''),
  input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, api_call_count, started_at,
  coalesce(estimated_cost_usd,0), coalesce(profile_name,'')
  from sessions where started_at > ?""", (since,)).fetchall()
def w(r): return r[4] + 5*r[5] + 1.25*r[7] + 0.1*r[6]
out = {"n": len(rows), "days": days}
def agg(keyf, top=25):
    d = defaultdict(lambda: [0,0,0,0,0,0,0.0,0.0])
    for r in rows:
        a = d[keyf(r)]
        a[0]+=1; a[1]+=r[4]; a[2]+=r[5]; a[3]+=r[6]; a[4]+=r[7]; a[5]+=r[8] or 0; a[6]+=w(r); a[7]+=r[10]
    return sorted(([k]+v for k,v in d.items()), key=lambda x:-x[7])[:top]
def fam(t):
    t = re.sub(r"\s*#\d+$", "", t)
    t = re.sub(r"\b(t_[0-9a-f]+|[0-9a-f]{7,}|PR ?#?\d+|#\d+|\d+)\b", "N", t)
    return t[:70]
out["by_source"] = agg(lambda r: r[0])
out["by_model"] = agg(lambda r: (r[1], r[2]))
out["by_profile"] = agg(lambda r: r[11])
out["by_day"] = agg(lambda r: time.strftime("%m-%d", time.gmtime(r[9])), 40)
out["by_source_day"] = agg(lambda r: (r[0], time.strftime("%m-%d", time.gmtime(r[9]))), 80)
out["by_family"] = agg(lambda r: (r[0], fam(r[3])), 60)
out["by_title"] = agg(lambda r: (r[0], r[3][:90]), 40)
tot = [0]*5
for r in rows:
    tot[0]+=r[4]; tot[1]+=r[5]; tot[2]+=r[6]; tot[3]+=r[7]; tot[4]+=w(r)
out["total"] = tot
print(json.dumps(out))
