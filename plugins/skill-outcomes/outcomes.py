#!/usr/bin/env python3
"""skill-outcomes core (stdlib only): one JSONL line per skill use with its Done-means result and user correction.

    outcomes.py flush [--home DIR] [--now TS]   finalize due pending records (prints how many were written)
    outcomes.py rollup [--home DIR] [--days 7]  per-skill table from <home>/logs/skill-outcomes.jsonl

Line: {ts, host, session, skill, checks_first_pass, fix_rounds, user_correction, ...extras}. See README.md for how
each field is filled.
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import json
import os
import re
import socket
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Iterable, Optional

HERE = Path(__file__).resolve().parent
MARK = "[skill-outcomes]"
# After the next user message, wait for coach's judges to log. Measured on the hub (14 days, evidence matched to the
# message): complaint p95 0.5 min, backstop p50 34 min, p90 2.0 h, p95 19 h. 3 h catches ~90% of backstop hits.
GRACE_S = 3 * 3600
IDLE_S = 2 * 3600  # no next user message within this long (Kanban, cron, abandoned chat): user_correction=false
COACH_EVENTS = {"selfheal_detect": {"complaint", "backstop"}, "lesson": None}
# rules < 2 closed the window on machine notices and matched coach by time window only; rules 2 missed real replies
# while a reloaded gateway paired new hooks with a cached old core. rules 3 also reads the reply from state.db.
# rules 4 drops background-review fork skill_views and closes an interrupted turn's uses at the next message.
RULES = 4

# `Done means (five-whys): 4/5 first pass, 1 fix round; still failing: ...`
TICK_RE = re.compile(
    r"Done means\s*\(\s*`?([\w:.\-]+)`?\s*\)\s*[:\-–—]\s*(\d+)\s*/\s*(\d+)\b[^,;\n]{0,30}"
    r"(?:[,;]\s*(\d+)\s*fix(?:\s*|-)rounds?)?", re.I)
DONE_HEADING_RE = re.compile(r"^#{2,3}\s+Done means\b", re.I | re.M)
SYNTHETIC_RE = re.compile(
    r"^\s*(?:# Kanban task t_|\[(?:kanban|owner-wake|coach|selfheal-probe|System note|runtime|session-outcome|"
    r"Session babysitter|False-stop recovery|skill-outcomes|ASYNC DELEGATION|CONTEXT COMPACTION|"
    r"Your active task list was preserved|Skills pruned)\b|"
    r"[✔✓✗✖⚠⏸]|\[?Canonical user decision|New note on your kanban task)", re.I)
# sessions.source values whose user turns are written by Hermes or a parent agent, never by a person
MACHINE_SOURCES = {"subagent", "cron", "kanban", "tool"}
# Fallback copy of coach's lessons.PREFILTER_RE, used only when the coach plugin is not installed next to this one.
_FALLBACK_CORRECTION_RE = re.compile(
    r"^\s*(no\b|nope|wait\b|actually\b|that'?s (?:not|wrong)|i said|i meant|don'?t\b|stop\b|why (?:did|are|would) you|"
    r"you (?:didn'?t|forgot|missed|should|shouldn'?t|need to)|instead|not what i|wrong|use \S+ not)"
    r"|\b(i (?:told|asked) you|you should (?:already )?know|don'?t ask me|stop asking|as i said|we already|i already|"
    r"not (?:that|this) one|you have access|it'?s (?:there|in)|from (?:the )?(?:token broker|secrets manager|memory))\b",
    re.I)


def home_dir(home: Any = None) -> Path:
    return Path(home or os.environ.get("HERMES_HOME") or Path.home() / ".hermes").expanduser()


def log_path(home: Any = None) -> Path:
    return home_dir(home) / "logs" / "skill-outcomes.jsonl"


def _pending_path(home: Any = None) -> Path:
    return home_dir(home) / "cache" / "skill-outcomes" / "pending.json"


def host_name() -> str:
    return (socket.gethostname() or "unknown").split(".")[0]


# ---------------------------------------------------------------- field parsers
def has_done_means(text: str) -> bool:
    return bool(DONE_HEADING_RE.search(text or ""))


def count_done_means(text: str) -> Optional[int]:
    """Number of `- [ ]` items in the skill's Done-means section (None without one)."""
    m = DONE_HEADING_RE.search(text or "")
    if not m:
        return None
    body = re.split(r"^#{1,3}\s", text[m.end():], maxsplit=1, flags=re.M)[0]
    return len(re.findall(r"^\s*[-*]\s+\[[ xX]\]", body, re.M)) or None


def parse_tickoffs(text: str) -> dict:
    """{skill: {"passed", "total", "fix_rounds"}} from the reply's `Done means (<skill>): p/t first pass, k fix
    rounds` lines (last one per skill wins)."""
    out = {}
    for m in TICK_RE.finditer(text or ""):
        p, t = int(m.group(2)), int(m.group(3))
        if t <= 0 or p > t:
            continue
        out[m.group(1).lower()] = {"passed": p, "total": t,
                                   "fix_rounds": int(m.group(4)) if m.group(4) is not None else None}
    return out


_kw = None


def _keyword_fn():
    """coach's correction regex (plugins/coach/lessons.py keyword_hit), loaded by path; fallback: a copy of it."""
    global _kw
    if _kw is None:
        _kw = lambda t: bool(_FALLBACK_CORRECTION_RE.search((t or "")[:800]))  # noqa: E731
        for p in (HERE.parent / "coach" / "lessons.py", home_dir() / "plugins" / "coach" / "lessons.py"):
            try:
                spec = importlib.util.spec_from_file_location("_skill_outcomes_coach_lessons", p)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                _kw = mod.keyword_hit
                break
            except Exception:
                continue
    return _kw


def keyword_correction(user_text: str) -> bool:
    return bool(user_text) and bool(_keyword_fn()(user_text))


_coach_synth = None


def _coach_synthetic_res() -> list:
    """coach's own machine-turn filters (self_heal.SYNTHETIC_RE, reply_check.NOTICE_RE), loaded by path."""
    global _coach_synth
    if _coach_synth is None:
        _coach_synth = []
        for base in (HERE.parent / "coach", home_dir() / "plugins" / "coach"):
            for mod_name, attr in (("self_heal", "SYNTHETIC_RE"), ("reply_check", "NOTICE_RE")):
                try:
                    spec = importlib.util.spec_from_file_location(f"_skill_outcomes_coach_{mod_name}",
                                                                  base / f"{mod_name}.py")
                    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
                    spec.loader.exec_module(mod)  # type: ignore[union-attr]
                    _coach_synth.append(getattr(mod, attr))
                except Exception:
                    continue
            if _coach_synth:
                break
    return _coach_synth


OOB_RE = re.compile(r"^\s*\[OUT-OF-BAND USER MESSAGE[^\]]*\]\s*(.*?)\s*\[/OUT-OF-BAND USER MESSAGE\]", re.S)
REAL_DISPLAY_KINDS = {None, "", "steer"}  # any other display_kind on the stored turn means Hermes wrote it


def unwrap_steer(text: Any) -> Any:
    """The user's words inside a mid-turn `[OUT-OF-BAND USER MESSAGE …]` envelope (coach's self_heal.OOB_RE);
    coach's SYNTHETIC_RE would otherwise reject the envelope as a machine turn."""
    m = OOB_RE.match(text) if isinstance(text, str) else None
    return m.group(1) if m else text


def is_real_user_message(text: Any, display_kind: Any = None) -> bool:
    """A human's message, not a Kanban/delegation/compaction notice, relay or runtime note."""
    text = unwrap_steer(text)
    if display_kind not in REAL_DISPLAY_KINDS:
        return False
    if not isinstance(text, str) or not text.strip() or SYNTHETIC_RE.match(text):
        return False
    return not any(rx.search(text) for rx in _coach_synthetic_res())


def coach_correction(home: Any, session: str, user_ts: float, user_text: str) -> bool:
    """coach logged a correction judge hit on this session's next user message: a `selfheal_detect`
    (complaint/backstop) or `lesson` event, logged after that message, whose evidence is in its text. A bare time
    window is not enough: a backstop on the message that started the turn lands in the same window."""
    p = home_dir(home) / "coach" / "log.jsonl"
    if not p.exists() or not session:
        return False
    text = (user_text or "").lower()
    try:
        with p.open(errors="replace") as f:
            for line in f:
                if session not in line:
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                ev = r.get("event")
                if ev not in COACH_EVENTS or r.get("session_id") != session:
                    continue
                srcs = COACH_EVENTS[ev]
                if srcs is not None and r.get("source") not in srcs:
                    continue
                evidence = str(r.get("evidence") or "").strip().lower()
                ts = r.get("ts")
                if isinstance(ts, (int, float)) and ts < user_ts - 5:
                    continue
                if len(evidence) >= 8 and evidence[:60] in text:
                    return True
    except OSError:
        return False
    return False


# ---------------------------------------------------------------- pending store
@contextlib.contextmanager
def _locked(home: Any = None):
    p = _pending_path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(str(p) + ".lock", "a+") as lf:
        try:
            import fcntl
            fcntl.flock(lf, fcntl.LOCK_EX)
        except Exception:
            pass
        data = []
        with contextlib.suppress(Exception):
            data = json.loads(p.read_text())
        if not isinstance(data, list):
            data = []
        box = {"data": data}
        yield box
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(box["data"]))
        os.replace(tmp, p)


def record_turn(home: Any, session: str, loads: Iterable[dict], reply: str, now: Optional[float] = None,
                turn_id: Any = None, interrupted: bool = False) -> list:
    """Turn end: one pending record per skill loaded this turn, with the reply's Done-means tick-off."""
    now = time.time() if now is None else now
    ticks = parse_tickoffs(reply or "")
    recs = []
    for ld in loads:
        skill = str(ld.get("skill") or "")
        if not skill:
            continue
        t = ticks.get(skill.lower())
        recs.append({
            "ts": round(float(ld.get("ts") or now), 3), "host": host_name(), "session": session or "",
            "skill": skill, "checks_first_pass": (t["passed"] == t["total"]) if t else None,
            "fix_rounds": t["fix_rounds"] if t else None, "user_correction": None,
            "checks": f"{t['passed']}/{t['total']}" if t else None, "has_done_means": bool(ld.get("has_done_means")),
            # the reply's tick-off total must match the skill's list; a mismatch marks an unreliable tick-off
            "tickoff_mismatch": (t["total"] != ld["checks_expected"]) if t and ld.get("checks_expected") else None,
            "turn_id": str(turn_id) if turn_id is not None else None, "turn_end": round(now, 3),
            "next_user_ts": None, "next_user": None, **({"interrupted": True} if interrupted else {})})
    if recs:
        with _locked(home) as box:
            box["data"].extend(recs)
    return recs


def note_user_message(home: Any, session: str, text: str, now: Optional[float] = None,
                      display_kind: Any = None) -> int:
    """The session's next real user message closes the correction window of its open records."""
    if not session or not is_real_user_message(text, display_kind):
        return 0
    text = unwrap_steer(text)
    now = time.time() if now is None else now
    n = 0
    p = _pending_path(home)
    if not p.exists():
        return 0
    with _locked(home) as box:
        for r in box["data"]:
            if r.get("session") == session and r.get("next_user_ts") is None and r.get("turn_end", 0) <= now + 1:
                r["next_user_ts"] = round(now, 3)
                r["next_user"] = text[:2000]
                n += 1
    return n


def _ro_connect(path, timeout=10):
    """Read-only open. mode=ro fails on a WAL db while its -wal/-shm is absent (writers delete them on last close),
    so fall back to mode=rw + query_only, which reads the same data and never writes rows."""
    import sqlite3
    try:
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=timeout)
        c.execute("select 1 from sqlite_master limit 1")
        return c
    except sqlite3.OperationalError:
        c = sqlite3.connect(f"file:{path}?mode=rw", uri=True, timeout=timeout)
        c.execute("PRAGMA query_only=ON")
        return c


def db_next_user(home: Any, session: str, after: float) -> Optional[tuple]:
    """(ts, text) of the session's first real user message after `after`, read from <home>/state.db: the
    authoritative transcript, so a reply the pre_llm_call hook missed (plugin reload, crash) still closes the
    window. Turns Hermes wrote carry a display_kind other than steer; the text filters apply as well."""
    p = home_dir(home) / "state.db"
    if not session or not p.exists():
        return None
    try:
        con = _ro_connect(p, timeout=2)
        try:
            try:
                src = con.execute("SELECT source FROM sessions WHERE id=?", (session,)).fetchone()
            except Exception:  # no sessions table: judge the messages alone
                src = None
            if src and str(src[0]) in MACHINE_SOURCES:
                return None
            try:  # delegate_task children keep source=desktop; their user turns come from the parent agent
                par = con.execute("SELECT parent_session_id FROM sessions WHERE id=?", (session,)).fetchone()
            except Exception:
                par = None
            if par and par[0]:
                return None
            rows = con.execute(
                "SELECT timestamp, content, display_kind FROM messages WHERE session_id=? AND role='user' "
                "AND timestamp>? ORDER BY timestamp LIMIT 200", (session, after)).fetchall()
        finally:
            con.close()
    except Exception:
        return None
    for ts, text, kind in rows:
        if is_real_user_message(text, kind):
            return float(ts), unwrap_steer(text)[:2000]
    return None


def db_view_missing(home: Any, session: str, skill: str, ts: float, window: float = 120.0) -> bool:
    """True when <home>/state.db holds this session's tool rows but no `skill_view` of `skill` near `ts`: the view
    ran in a persistence-detached fork (background review) that shares the session_id, so it is not a use."""
    p = home_dir(home) / "state.db"
    if not session or not skill or not p.exists():
        return False
    try:
        con = _ro_connect(p, timeout=2)
        try:
            if not con.execute("SELECT 1 FROM messages WHERE session_id=? AND role='tool' AND tool_name IS NOT NULL "
                               "LIMIT 1", (session,)).fetchone():
                return False  # nothing persisted for this session to judge against
            rows = con.execute("SELECT content FROM messages WHERE session_id=? AND role='tool' AND "
                               "tool_name='skill_view' AND timestamp BETWEEN ? AND ?",
                               (session, ts - window, ts + window)).fetchall()
        finally:
            con.close()
    except Exception:
        return False
    for (content,) in rows:
        try:
            d, _ = json.JSONDecoder().raw_decode(content or "")
        except Exception:
            continue
        if isinstance(d, dict) and str(d.get("name") or "") == skill:
            return False
    return True


def _finalize(home: Any, r: dict) -> dict:
    out = {k: v for k, v in r.items() if k not in ("next_user", "next_user_ts", "turn_end", "reopened")}
    out["rules"] = RULES
    nu = r.get("next_user")
    if nu is None and r.get("reopened"):
        # its window was closed by a machine notice under the old rules; the real reply may have passed unrecorded
        out["user_correction"], out["correction_source"] = None, "reopened"
    elif nu is None:
        out["user_correction"], out["correction_source"] = False, "none"
    elif keyword_correction(nu):
        out["user_correction"], out["correction_source"] = True, "keyword"
    elif coach_correction(home, r.get("session") or "", float(r["next_user_ts"]), nu):
        out["user_correction"], out["correction_source"] = True, "coach"
    else:
        out["user_correction"], out["correction_source"] = False, "next_message"
    return out


def flush(home: Any = None, now: Optional[float] = None, force: bool = False) -> list:
    """Write every due pending record to the JSONL log: GRACE_S after its next user message, or IDLE_S after its
    turn when no user message came. `force` writes everything now (session end of a one-shot process)."""
    now = time.time() if now is None else now
    if not _pending_path(home).exists():
        return []
    done = []
    with _locked(home) as box:
        keep = []
        for r in box["data"]:
            if r.get("next_user") is not None and not is_real_user_message(r["next_user"]):
                r["next_user"], r["next_user_ts"], r["reopened"] = None, None, True  # closed by a notice
            if r.get("next_user") is None:
                hit = db_next_user(home, r.get("session") or "", float(r.get("turn_end") or 0))
                if hit:
                    r["next_user_ts"], r["next_user"] = round(hit[0], 3), hit[1]
                    r["reply_source"] = "state_db"
            nu_ts = r.get("next_user_ts")
            due = force or (nu_ts is not None and now - nu_ts >= GRACE_S) or \
                (nu_ts is None and now - float(r.get("turn_end") or 0) >= IDLE_S)
            if due and db_view_missing(home, r.get("session") or "", r.get("skill") or "", float(r.get("ts") or 0)):
                continue  # the transcript has no such view: a detached fork's, not a use; dropped
            (done if due else keep).append(r)
        if done:
            lp = log_path(home)
            lp.parent.mkdir(parents=True, exist_ok=True)
            lines = "".join(json.dumps(_finalize(home, r), sort_keys=True) + "\n" for r in done)
            fd = os.open(str(lp), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
            try:
                os.write(fd, lines.encode())
            finally:
                os.close(fd)
        box["data"] = keep
    return done


# ---------------------------------------------------------------- rollup
def read_lines(paths: Iterable[Path]) -> list:
    seen, out = set(), []
    for p in paths:
        try:
            text = Path(p).read_text(errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if not isinstance(r, dict) or not r.get("skill"):
                continue
            key = (r.get("host"), r.get("session"), r.get("skill"), r.get("ts"))
            if key in seen:
                continue
            seen.add(key)
            out.append(r)
    return out


def rollup(rows: list, lo: float, hi: float) -> list:
    """Per skill: uses, ticked (uses with a Done-means tick-off), first-pass rate and mean fix rounds over ticked
    uses, correction rate over all finalized uses. Sorted by uses."""
    by = {}
    for r in rows:
        ts = r.get("ts")
        if not isinstance(ts, (int, float)) or not lo <= ts < hi:
            continue
        by.setdefault(r["skill"], []).append(r)
    out = []
    for skill, rs in by.items():
        # a tick-off whose total doesn't match the skill's list is not trusted for first-pass / fix rounds
        ticked = [r for r in rs if r.get("checks_first_pass") is not None and not r.get("tickoff_mismatch")]
        fixes = [r["fix_rounds"] for r in ticked if isinstance(r.get("fix_rounds"), int)]
        # rules < RULES lines could miss or misread the user's reply (see RULES): not counted
        corr = [r for r in rs if r.get("user_correction") is not None and (r.get("rules") or 1) >= RULES]
        out.append({"skill": skill, "uses": len(rs), "ticked": len(ticked),
                    "first_pass_rate": round(sum(1 for r in ticked if r["checks_first_pass"]) / len(ticked), 3)
                    if ticked else None,
                    "mean_fix_rounds": round(statistics.mean(fixes), 2) if fixes else None,
                    "correction_rate": round(sum(1 for r in corr if r["user_correction"]) / len(corr), 3)
                    if corr else None})
    return sorted(out, key=lambda d: (-d["uses"], d["skill"]))


def _pct(x):
    return "n/a" if x is None else f"{x * 100:.0f}%"


def rollup_lines(rows: list, lo: float, hi: float, top: int = 25, title: str = "Skill outcomes") -> list:
    """Markdown section for the weekly KPI post."""
    tab = rollup(rows, lo, hi)
    hosts = sorted({r.get("host") or "?" for r in rows if lo <= (r.get("ts") or 0) < hi})
    out = [f"## {title}", "",
           "Per skill use (skill-outcomes plugin): first-pass rate and mean fix rounds over uses that ended with a "
           "`Done means (<skill>): p/t first pass, k fix rounds` tick-off; correction rate = the user's next "
           "message was a correction (coach regex or judge).", ""]
    if not tab:
        return out + ["No skill uses logged in this window (`logs/skill-outcomes.jsonl` empty on every host read)."]
    n = sum(d["uses"] for d in tab)
    t = sum(d["ticked"] for d in tab)
    out.append(f"{n} uses of {len(tab)} skills on {', '.join(hosts)}; {t} with a tick-off ({_pct(t / n)}).")
    bad = sum(1 for r in rows if lo <= (r.get("ts") or 0) < hi and r.get("tickoff_mismatch"))
    if bad:
        out.append(f"{bad} tick-off(s) gave a total that differs from the skill's Done-means list; excluded.")
    old = sum(1 for r in rows if lo <= (r.get("ts") or 0) < hi and (r.get("rules") or 1) < RULES)
    if old:
        out.append(f"{old} line(s) predate the correction-rule fix (rules<{RULES}); left out of correction rate.")
    out += ["", "| Skill | uses | ticked | first-pass | mean fix rounds | correction rate |",
            "|---|---:|---:|---:|---:|---:|"]
    for d in tab[:top]:
        mf = "n/a" if d["mean_fix_rounds"] is None else f"{d['mean_fix_rounds']:.2f}"
        out.append(f"| {d['skill']} | {d['uses']} | {d['ticked']} | {_pct(d['first_pass_rate'])} | {mf} | "
                   f"{_pct(d['correction_rate'])} |")
    if len(tab) > top:
        out.append(f"| … {len(tab) - top} more | {sum(d['uses'] for d in tab[top:])} | | | | |")
    return out


def home_logs(home: Any = None) -> list:
    h = home_dir(home)
    return [h / "logs" / "skill-outcomes.jsonl", *sorted(h.glob("profiles/*/logs/skill-outcomes.jsonl"))]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["flush", "rollup"])
    ap.add_argument("--home", default=None)
    ap.add_argument("--now", type=float, default=None)
    ap.add_argument("--days", type=float, default=7)
    ap.add_argument("--all-profiles", action="store_true", help="flush: also every <home>/profiles/*")
    a = ap.parse_args(argv)
    if a.cmd == "flush":
        homes = [home_dir(a.home)]
        if a.all_profiles:
            homes += sorted(p for p in home_dir(a.home).glob("profiles/*") if p.is_dir())
        n = sum(len(flush(h, a.now)) for h in homes)
        print(f"skill-outcomes: flushed {n}")
        return 0
    now = time.time() if a.now is None else a.now
    print("\n".join(rollup_lines(read_lines(home_logs(a.home)), now - a.days * 86400, now + 1)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
