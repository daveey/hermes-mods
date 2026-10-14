#!/usr/bin/env python3
"""Write one compact, redacted JSON digest per Hermes session (stdlib only).

    session_digest.py --host NAME --out DIR [--db PATH ...] [--since-days 45]

With no --db, it reads $HERMES_HOME/state.db (default ~/.hermes) plus every profiles/*/state.db.
Each top-level session becomes DIR/<host>/<profile>/<session_id>.json. A digest is rewritten only
when the session has changed (more messages or later activity), so a git commit of DIR shows
real deltas. Child (subagent) sessions are folded into their parent as counts and cost.

What goes in: session stats, the user's turns (injected context stripped, secrets redacted,
truncated), tool counts and tool-error counts, pattern signals, and the tail of the last
assistant reply. What never goes in: tool output, system prompts, reasoning, file contents.
"""
import argparse, collections, json, os, re, sqlite3, sys, time
from pathlib import Path

DIGEST_V = 1
MAX_TURNS = 60          # keep the first 10 and the last 50 user turns
TURN_CHARS = 500
LAST_REPLY_CHARS = 800

# Blocks Hermes injects into user messages; they are not the user's words.
INJECTED = [
    re.compile(r"<memory-context>.*?</memory-context>", re.S),
    re.compile(r"<system-reminder>.*?</system-reminder>", re.S),
    re.compile(r"<pasted_content[^>]*>.*?</pasted_content>", re.S),
    re.compile(r"\[System note:[^\]]*\]", re.S),
    re.compile(r"\[OUT-OF-BAND USER MESSAGE[^\]]*\]|\[/OUT-OF-BAND USER MESSAGE\]"),
    re.compile(r"\[Context from the interrupted assistant response\].*?(?=\n\n|\Z)", re.S),
]
SECRETS = [
    re.compile(r"\b(sk-[A-Za-z0-9_\-]{16,}|sk-ant-[A-Za-z0-9_\-]{16,})"),
    re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"),
    re.compile(r"\b(xox[abprs]-[A-Za-z0-9\-]{10,})"),
    re.compile(r"\b(AKIA[0-9A-Z]{16})\b"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]*"),
    re.compile(r"(?i)\b(bearer|token|password|passwd|secret|api[_-]?key)(\s*[:=]\s*|\s+)((?=[^\s\"']*\d)[^\s\"']{12,})"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
    re.compile(r"\b[A-Za-z0-9+_\-]{48,}={0,2}"),   # long opaque blobs (keys, tokens, base64)
]
SIGNALS = {
    "screenshot": re.compile(r"Screenshot|\[Image attached|@image:|\[screenshot\]"),
    "session_mention": re.compile(r"@session:"),
    "repeat_fix": re.compile(r"(?i)\b(still|again|didn'?t work|didnt work|not working|broken|same (error|issue|problem))\b"),
    "stuck": re.compile(r"(?i)\b(stuck|hang|hanging|hung|frozen|idle|not responding)\b"),
    "resume": re.compile(r"(?i)^\s*(resume|continue)\s*$"),
    "plan": re.compile(r"(?i)\bplan\b"),
    "automation_want": re.compile(r"(?i)\b(every (day|morning|night|hour|week)|whenever|automatically|each time)\b"),
    "hermes_meta": re.compile(r"(?i)\bhermes\b"),
    "frustration": re.compile(r"(?i)\b(wtf|why (is|isn'?t|does|doesn'?t|did)|ugh|come on|seriously)\b"),
}
TOOL_ERR = re.compile(r'"exit_code":\s*[1-9]|"success":\s*false|"error":\s*"[^"]|^Error\b|Traceback \(most recent', re.M)


def redact(s: str) -> str:
    for rx in SECRETS:
        s = rx.sub(lambda m: (m.group(1) + m.group(2) + "[REDACTED]") if rx.groups == 3 else "[REDACTED]", s)
    return s


# User-role messages Hermes writes itself (not typed by the user): counted, not kept as turns.
SYNTHETIC = re.compile(r"^\s*\[(IMPORTANT: Background process|Recent Summary|CONTEXT COMPACTION|Background task)", re.I)


def flatten(content: str) -> str:
    """Multimodal content is stored as a JSON list of parts; keep the text, mark images."""
    c = content.lstrip()
    if c.startswith("[{") or c.startswith('{"type"'):
        try:
            parts = json.loads(c)
            parts = parts if isinstance(parts, list) else [parts]
            out = []
            for p in parts:
                if not isinstance(p, dict):
                    continue
                if p.get("type") == "text":
                    out.append(p.get("text") or "")
                elif "image" in str(p.get("type")):
                    out.append("[screenshot]")
            return "\n".join(out)
        except Exception:
            pass
    return re.sub(r"data:[a-z]+/[a-z0-9.+-]+;base64,[A-Za-z0-9+/=]+", "[binary]", content)


def clean_user(text: str) -> str:
    text = flatten(text)
    for rx in INJECTED:
        text = rx.sub("", text)
    text = re.sub(r"@image:`[^`]*`", "[screenshot]", text)
    text = re.sub(r"/Users/[^/\s]+/", "~/", text)
    return redact(re.sub(r"\n{3,}", "\n\n", text).strip())


def trunc(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 15] + " …[truncated]"


def discover_dbs(home: Path):
    out = []
    if (home / "state.db").exists():
        out.append(("default", home / "state.db"))
    for p in sorted((home / "profiles").glob("*/state.db")):
        if not p.parent.name.startswith("."):
            out.append((p.parent.name, p))
    return out


def cols(con, table):
    return {r[1] for r in con.execute(f"pragma table_info({table})")}


def digest_db(profile, db, host, since):
    con = sqlite3.connect(f"file:{db}", uri=True, timeout=30)
    con.execute("pragma query_only = 1")
    con.row_factory = sqlite3.Row
    scols = cols(con, "sessions")
    opt = lambda c, alias=None: (c if c in scols else f"NULL") + f" as {alias or c}"
    act = "coalesce(last_activity_at, ended_at, started_at)" if "last_activity_at" in scols else "coalesce(ended_at, started_at)"
    rows = con.execute(f"""
        select id, source, model, parent_session_id, started_at, ended_at, end_reason, message_count,
               tool_call_count, api_call_count, input_tokens, output_tokens, cache_read_tokens,
               coalesce(actual_cost_usd, estimated_cost_usd) as cost, title,
               {opt('cwd')}, {opt('git_repo_root')}, {opt('git_branch')}, {opt('archived')},
               {opt('profile_name')}, {act} as activity
        from sessions where {act} >= ?""", (since,)).fetchall()
    by_id = {r["id"]: r for r in rows}

    # Fold subagent sessions into their top-level ancestor.
    kids = collections.defaultdict(lambda: {"n": 0, "cost": 0.0, "msgs": 0, "tool_calls": 0})
    parent_of = {r["id"]: r["parent_session_id"] for r in con.execute("select id, parent_session_id from sessions")}
    def root(i):
        seen = 0
        while parent_of.get(i) and seen < 50:
            i, seen = parent_of[i], seen + 1
        return i
    for r in rows:
        if r["parent_session_id"]:
            k = kids[root(r["id"])]
            k["n"] += 1; k["cost"] += r["cost"] or 0; k["msgs"] += r["message_count"] or 0
            k["tool_calls"] += r["tool_call_count"] or 0

    for r in rows:
        if r["parent_session_id"]:
            continue
        sid = r["id"]
        msgs = con.execute("""select role, content, tool_name, tool_calls, timestamp from messages
                              where session_id=? order by timestamp, id""", (sid,)).fetchall()
        users, tools, terr = [], collections.Counter(), collections.Counter()
        sig = collections.Counter()
        last_reply, call_names = "", {}
        for m in msgs:
            role, content = m["role"], m["content"] or ""
            if role == "user":
                text = clean_user(content)
                if not text:
                    continue
                if SYNTHETIC.match(text):
                    sig["synthetic_" + ("bg_notify" if "Background" in text[:40] else "summary")] += 1
                    continue
                for k, rx in SIGNALS.items():
                    if rx.search(text):
                        sig[k] += 1
                if users and users[-1]["text"] == trunc(text, TURN_CHARS):
                    continue  # the same turn persisted twice (retry / steer echo)
                users.append({"t": round(m["timestamp"]), "text": trunc(text, TURN_CHARS)})
            elif role == "assistant":
                if content.strip():
                    last_reply = content
                for tc in _tool_calls(m["tool_calls"]):
                    tools[tc] += 1
            elif role == "tool":
                if m["tool_name"] and TOOL_ERR.search(content[:4000]):
                    terr[m["tool_name"]] += 1
        if len(users) > MAX_TURNS:
            users = users[:10] + [{"t": None, "text": f"…[{len(users) - MAX_TURNS} turns omitted]"}] + users[-(MAX_TURNS - 10):]
        k = kids.get(sid, {"n": 0, "cost": 0.0, "msgs": 0, "tool_calls": 0})
        yield {
            "digest_v": DIGEST_V, "host": host, "profile": r["profile_name"] or profile, "id": sid,
            "source": r["source"], "title": r["title"], "model": r["model"],
            "started_at": r["started_at"], "ended_at": r["ended_at"], "last_activity_at": r["activity"],
            "end_reason": r["end_reason"], "archived": bool(r["archived"]),
            "cwd": _home_rel(r["cwd"]), "repo": _home_rel(r["git_repo_root"]), "branch": r["git_branch"],
            "msgs": len(msgs), "tool_calls": sum(tools.values()), "api_calls": r["api_call_count"],
            "tokens": {"in": r["input_tokens"], "out": r["output_tokens"], "cache_read": r["cache_read_tokens"]},
            "cost_usd": round(r["cost"] or 0, 3),
            "children": {"n": k["n"], "cost_usd": round(k["cost"], 3), "msgs": k["msgs"], "tool_calls": k["tool_calls"]},
            "user_turns_total": sum(1 for m in msgs if m["role"] == "user"),
            "user_turns": users,
            "signals": dict(sig),
            "tools": dict(tools.most_common(25)),
            "tool_errors": dict(terr.most_common(15)),
            "last_reply": trunc(redact(_home_rel(last_reply) or ""), LAST_REPLY_CHARS),
        }
    con.close()


def _tool_calls(raw):
    if not raw:
        return []
    try:
        calls = json.loads(raw)
    except Exception:
        return []
    out = []
    for c in calls if isinstance(calls, list) else []:
        fn = (c.get("function") or {}) if isinstance(c, dict) else {}
        name = fn.get("name") or (c.get("name") if isinstance(c, dict) else None)
        if name:
            out.append(name)
    return out


def _home_rel(s):
    return re.sub(r"/Users/[^/\s]+/|/home/[^/\s]+/", "~/", s) if isinstance(s, str) else s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--db", action="append", help="profile=path or path (repeatable)")
    ap.add_argument("--home", default=os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes")))
    ap.add_argument("--since-days", type=float, default=45)
    a = ap.parse_args()

    dbs = []
    for d in a.db or []:
        prof, _, path = d.rpartition("=")
        dbs.append((prof or Path(path).parent.name, Path(path)))
    dbs = dbs or discover_dbs(Path(a.home).resolve())
    since = time.time() - a.since_days * 86400
    out = Path(a.out) / a.host
    written = unchanged = 0
    for profile, db in dbs:
        try:
            for d in digest_db(profile, db, a.host, since):
                p = out / (d["profile"] or profile) / f"{d['id']}.json"
                body = json.dumps(d, ensure_ascii=False, indent=1, sort_keys=True)
                if p.exists() and p.read_text() == body:
                    unchanged += 1
                    continue
                p.parent.mkdir(parents=True, exist_ok=True)
                tmp = p.with_suffix(".tmp"); tmp.write_text(body); tmp.replace(p)
                written += 1
        except sqlite3.Error as e:
            print(f"warn: {profile} {db}: {e}", file=sys.stderr)
    print(json.dumps({"host": a.host, "dbs": len(dbs), "written": written, "unchanged": unchanged}))


if __name__ == "__main__":
    main()
