#!/usr/bin/env python3
"""job_state.py - a small STATE.md for a recurring agent job (cron job or recurring Kanban card family).

The file has four sections: Last run / Tried / Lessons / Watch list. It is capped (default 150 lines):
the oldest Tried entries are pruned first, then the oldest already-promoted Lessons.

Commands (stdlib only, any python3 >= 3.8):
  show JOB [--slice] [--tried N]   print the file (create it if missing); --slice = Last run, Lessons,
                                   Watch list and the newest N Tried entries (for card bodies)
  record JOB [--last-run T] [--tried T]... [--lesson "[slug] text"]... [--watch T]... [--unwatch SUBSTR]...
             [--promoted SLUG]...  update, prune, then print PROMOTE lines for lessons seen >= 3 times
  prune JOB [--max-lines N]        prune only; exit 3 if still over the cap
  lessons JOB [--min N]            list lessons seen >= N times that are not promoted yet
  harvest JOB --db KANBAN_DB --key-prefix P [...]
                                   fold finished card runs of a card family into the file: each run's
                                   metadata.job_state ({tried, lessons, watch, unwatch}) and a Tried line
                                   from its summary. Consumed run IDs commit atomically inside the state file.
  path JOB                         print the resolved path

JOB is a name (file = $JOB_STATE_DIR or ~/.hermes/state, + JOB.md) or a path ending in .md.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import sqlite3
import sys
import tempfile
from contextlib import contextmanager

SECTIONS = ("Last run", "Tried", "Lessons", "Watch list")
MAX_LINES = 150
PROMOTE_AT = 3
# Run outcomes that end a worker's attempt with something worth remembering (continued/reclaimed/crashed... are not).
HARVEST_OUTCOMES = {"completed", "blocked", "review_requested", "changes_requested", "gave_up", "failed"}
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
SLUG_RE = re.compile(r"^\s*\[([a-z0-9][a-z0-9-]{0,48})\]\s*(.+)$", re.S)
LESSON_RE = re.compile(r"^- \[([a-z0-9][a-z0-9-]*)\] \(x(\d+), last ([0-9-]+)(, promoted)?\) (.*)$", re.S)


def now_stamp() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def state_dir() -> str:
    return os.environ.get("JOB_STATE_DIR") or os.path.join(os.path.expanduser("~"), ".hermes", "state")


def resolve(job: str) -> str:
    if job.endswith(".md") or os.sep in job:
        return os.path.abspath(os.path.expanduser(job))
    if not NAME_RE.match(job):
        raise SystemExit(f"job_state: bad job name {job!r} (letters, digits, . _ -)")
    return os.path.join(state_dir(), job + ".md")


def job_name(path: str) -> str:
    return os.path.splitext(os.path.basename(path))[0]


def one_line(text: str) -> str:
    return " ".join(str(text).split())


class State:
    def __init__(self, title: str, preamble: list[str], sections: dict[str, list[str]], extra: list[str]):
        self.title = title
        self.preamble = preamble  # lines between title and first section
        self.sections = sections  # name -> list of entries (an entry may span lines)
        self.extra = extra  # unknown trailing sections, kept verbatim

    @classmethod
    def new(cls, name: str) -> "State":
        return cls(f"# {name} state",
                   [f"<!-- job_state.py: read first, record last. Cap {MAX_LINES} lines; oldest Tried pruned first. -->"],
                   {s: [] for s in SECTIONS}, [])

    @classmethod
    def parse(cls, text: str, name: str) -> "State":
        st = cls.new(name)
        st.preamble = []
        lines = text.splitlines()
        cur = None
        seen_title = False
        extra: list[str] = []
        for ln in lines:
            m = re.match(r"^## (.+?)\s*$", ln)
            if ln.startswith("# ") and not seen_title and cur is None:
                st.title, seen_title = ln, True
                continue
            if m:
                cur = m.group(1) if m.group(1) in SECTIONS else "__extra__"
                if cur == "__extra__":
                    extra.append(ln)
                continue
            if cur is None:
                if ln.strip():
                    st.preamble.append(ln)
            elif cur == "__extra__":
                extra.append(ln)
            elif not ln.strip():
                continue
            elif ln.startswith("- ") or not st.sections[cur] or cur == "Last run":
                st.sections[cur].append(ln)
            else:
                st.sections[cur][-1] += "\n" + ln  # continuation line of the previous entry
        st.extra = extra
        return st

    def render(self) -> str:
        out = [self.title, *self.preamble, ""]
        for s in SECTIONS:
            out.append(f"## {s}")
            out.extend(self.sections[s])
            out.append("")
        out.extend(self.extra)
        return "\n".join(out).rstrip("\n") + "\n"

    def line_count(self) -> int:
        return len(self.render().splitlines())

    # ---- mutations
    def set_last_run(self, text: str, stamp: str) -> None:
        self.sections["Last run"] = [f"{stamp} {one_line(text)}"]

    def add_tried(self, text: str, stamp: str) -> None:
        self.sections["Tried"].append(f"- {stamp} {one_line(text)}")

    def add_lesson(self, text: str, day: str) -> str:
        m = SLUG_RE.match(text)
        if not m:
            raise ValueError(f"lesson needs a leading [slug]: {text!r}")
        slug, body = m.group(1), one_line(m.group(2))
        lessons = self.sections["Lessons"]
        for i, ent in enumerate(lessons):
            lm = LESSON_RE.match(ent)
            if lm and lm.group(1) == slug:
                n = int(lm.group(2)) + 1
                lessons[i] = f"- [{slug}] (x{n}, last {day}{lm.group(4) or ''}) {body}"
                return slug
        lessons.append(f"- [{slug}] (x1, last {day}) {body}")
        return slug

    def mark_promoted(self, slug: str) -> bool:
        lessons = self.sections["Lessons"]
        for i, ent in enumerate(lessons):
            lm = LESSON_RE.match(ent)
            if lm and lm.group(1) == slug:
                lessons[i] = f"- [{slug}] (x{lm.group(2)}, last {lm.group(3)}, promoted) {lm.group(5)}"
                return True
        return False

    def add_watch(self, text: str) -> None:
        ent = f"- {one_line(text)}"
        if ent not in self.sections["Watch list"]:
            self.sections["Watch list"].append(ent)

    def unwatch(self, substr: str) -> int:
        before = self.sections["Watch list"]
        self.sections["Watch list"] = [e for e in before if substr not in e]
        return len(before) - len(self.sections["Watch list"])

    def to_promote(self, minimum: int = PROMOTE_AT) -> list[tuple[str, int, str]]:
        out = []
        for ent in self.sections["Lessons"]:
            lm = LESSON_RE.match(ent)
            if lm and not lm.group(4) and int(lm.group(2)) >= minimum:
                out.append((lm.group(1), int(lm.group(2)), lm.group(5)))
        return out

    def prune(self, max_lines: int = MAX_LINES) -> int:
        """Drop oldest Tried entries, then oldest promoted Lessons, until within the cap. Returns entries dropped."""
        dropped = 0
        tried = self.sections["Tried"]
        while self.line_count() > max_lines and tried:
            tried.pop(0)
            dropped += 1
        if self.line_count() > max_lines:
            promoted = []
            for ent in self.sections["Lessons"]:
                lm = LESSON_RE.match(ent)
                if lm and lm.group(4):
                    promoted.append((lm.group(3), ent))
            for _, ent in sorted(promoted, key=lambda p: p[0]):
                if self.line_count() <= max_lines:
                    break
                self.sections["Lessons"].remove(ent)
                dropped += 1
        return dropped

    def slice(self, tried_n: int = 10) -> str:
        out = [self.title, ""]
        for s in SECTIONS:
            ents = self.sections[s]
            if s == "Tried":
                ents = ents[-tried_n:] if tried_n > 0 else []
                out.append(f"## Tried (newest {len(ents)} of {len(self.sections[s])})")
            else:
                out.append(f"## {s}")
            out.extend(ents or ["(none)"])
            out.append("")
        return "\n".join(out).rstrip("\n") + "\n"


@contextmanager
def locked(path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lock = open(path + ".lock", "a+")
    try:
        try:
            import fcntl
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        except ImportError:  # pragma: no cover - non-POSIX
            pass
        yield
    finally:
        lock.close()


def load(path: str) -> State:
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return State.parse(f.read(), job_name(path))
    return State.new(job_name(path))


def save(path: str, st: State) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".job_state.", dir=os.path.dirname(path))
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(st.render())
    os.replace(tmp, path)


def print_promotions(st: State, path: str) -> None:
    for slug, n, body in st.to_promote():
        print(f"PROMOTE [{slug}] x{n}: {body}")
    if st.to_promote():
        print(f"-> add each PROMOTE lesson to the job's skill as a yes/no line under '## Done means' (or its "
              f"checklist), then run: job_state.py record {path} --promoted <slug>")


def apply_update(st: State, upd: dict, stamp: str) -> None:
    day = stamp[:10]
    if upd.get("last_run"):
        st.set_last_run(upd["last_run"], stamp)
    for t in _as_list(upd.get("tried")):
        st.add_tried(t, stamp)
    for l in _as_list(upd.get("lessons") or upd.get("lesson")):
        try:
            st.add_lesson(l, day)
        except ValueError as exc:
            print(f"job_state: skipped {exc}", file=sys.stderr)
    for w in _as_list(upd.get("watch")):
        st.add_watch(w)
    for u in _as_list(upd.get("unwatch")):
        st.unwatch(u)


def _as_list(v) -> list[str]:
    if v is None or v == "":
        return []
    if isinstance(v, (list, tuple)):
        return [str(x) for x in v if str(x).strip()]
    return [str(v)]


def cmd_show(a) -> int:
    path = resolve(a.job)
    with locked(path):
        if not os.path.exists(path):
            save(path, State.new(job_name(path)))
        st = load(path)
    sys.stdout.write(st.slice(a.tried) if a.slice else st.render())
    return 0


def cmd_record(a) -> int:
    path = resolve(a.job)
    stamp = a.stamp or now_stamp()
    with locked(path):
        st = load(path)
        for l in a.lesson or []:
            if not SLUG_RE.match(l):
                print(f"job_state: lesson needs a leading [slug]: {l!r}", file=sys.stderr)
                return 2
        apply_update(st, {"last_run": a.last_run, "tried": a.tried, "lessons": a.lesson, "watch": a.watch,
                          "unwatch": a.unwatch}, stamp)
        for slug in a.promoted or []:
            if not st.mark_promoted(slug):
                print(f"job_state: no lesson [{slug}]", file=sys.stderr)
        dropped = st.prune(a.max_lines)
        save(path, st)
    print(f"job_state: {path} {st.line_count()} lines" + (f", pruned {dropped}" if dropped else ""))
    print_promotions(st, path)
    return 0


def cmd_prune(a) -> int:
    path = resolve(a.job)
    with locked(path):
        st = load(path)
        dropped = st.prune(a.max_lines)
        if dropped:
            save(path, st)
    n = st.line_count()
    print(f"job_state: {path} {n} lines, pruned {dropped}")
    if n > a.max_lines:
        print(f"job_state: still over the {a.max_lines}-line cap; shorten Lessons/Watch list by hand", file=sys.stderr)
        return 3
    return 0


def cmd_lessons(a) -> int:
    st = load(resolve(a.job))
    for slug, n, body in st.to_promote(a.min):
        print(f"[{slug}] x{n}: {body}")
    return 0


def _ro_connect(path, timeout=10):
    """Read-only open. mode=ro fails on a WAL db while its -wal/-shm is absent (writers delete them on last close),
    so fall back to mode=rw + query_only, which reads the same data and never writes rows."""
    try:
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=timeout)
        c.execute("select 1 from sqlite_master limit 1")
        return c
    except sqlite3.OperationalError:
        c = sqlite3.connect(f"file:{path}?mode=rw", uri=True, timeout=timeout)
        c.execute("PRAGMA query_only=ON")
        return c


def cmd_harvest(a) -> int:
    path = resolve(a.job)
    cursor_path = os.path.join(os.path.dirname(path), f".{job_name(path)}.harvest.json")
    marker = '<!-- harvest-v2 '
    source = [os.path.realpath(a.db), sorted(a.key_prefix)]
    # Cursor and visible state share ONE atomic replacement under the same lock.
    # Run IDs are allocated at start, not completion: a high-water ID loses late runs.
    with locked(path):
        st = load(path)
        entries = [p for p in st.preamble if p.startswith(marker)]
        if len(entries) > 1:
            raise ValueError('duplicate harvest cursor')
        if entries:
            if not entries[0].endswith(' -->'):
                raise ValueError('invalid harvest cursor')
            cursor = json.loads(entries[0][len(marker):-4])
            if cursor['source'] != source:
                raise ValueError('harvest source changed; use a separate state slice')
            seen = set(cursor['seen'])
        else:
            seen = set()
        con = _ro_connect(a.db, timeout=10)
        try:
            where = " or ".join("t.idempotency_key like ? escape '\\'" for _ in a.key_prefix)
            params = [p.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%" for p in a.key_prefix]
            rows = con.execute(
                "select r.id, t.id, t.idempotency_key, r.outcome, r.summary, r.metadata, r.ended_at from task_runs r "
                f"join tasks t on t.id = r.task_id where r.ended_at is not null and ({where}) order by r.ended_at, r.id",
                params).fetchall()
        finally:
            con.close()
        if not entries and os.path.exists(cursor_path):
            # Preserve legacy counts; already-missed historic completions cannot be
            # distinguished from pruned Tried entries. Do not fabricate a repair.
            # Open low-ID runs are NOT seeded, so later finishes remain eligible.
            with open(cursor_path) as f:
                legacy = json.load(f)['last_run_id']
            if not isinstance(legacy, int) or legacy < 0:
                raise ValueError('invalid legacy harvest cursor')
            seen = {r[0] for r in rows if r[0] <= legacy}
        rows = [r for r in rows if r[0] not in seen]
        if not rows and entries:
            print(f"job_state: harvest {job_name(path)}: nothing new")
            return 0
        n_meta = n_used = 0
        for run_id, task_id, key, outcome, summary, meta, ended in rows:
            stamp = _dt.datetime.fromtimestamp(float(ended), _dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
            try:
                md = json.loads(meta) if meta else {}
            except ValueError:
                md = {}
            js = md.get("job_state") if isinstance(md, dict) else None
            if outcome not in HARVEST_OUTCOMES and not isinstance(js, dict):
                continue
            n_used += 1
            st.set_last_run(f"{task_id} ({key}) {outcome or '?'}", stamp)
            if isinstance(js, dict):
                n_meta += 1
                js = {k: v for k, v in js.items() if k != "last_run"}
                js["tried"] = [f"{task_id} {outcome or '?'}: {t}" for t in _as_list(js.get("tried"))]
                apply_update(st, js, stamp)
            if not (isinstance(js, dict) and js.get("tried")):
                text = one_line(summary or "")[:220] or "(no summary)"
                st.add_tried(f"{task_id} {outcome or '?'}: {text}", stamp)
        seen.update(r[0] for r in rows)
        st.preamble = [p for p in st.preamble if not p.startswith(marker)]
        st.preamble.append(marker + json.dumps({'source': source, 'seen': sorted(seen)}, separators=(',', ':')) + ' -->')
        dropped = st.prune(a.max_lines)
        save(path, st)
    print(f"job_state: harvest {job_name(path)}: {n_used} of {len(rows)} new run(s), {n_meta} with metadata.job_state"
          + (f", pruned {dropped}" if dropped else ""))
    print_promotions(st, path)
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="job_state.py", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("show"); s.add_argument("job"); s.add_argument("--slice", action="store_true")
    s.add_argument("--tried", type=int, default=10); s.set_defaults(fn=cmd_show)
    r = sub.add_parser("record"); r.add_argument("job"); r.add_argument("--last-run")
    r.add_argument("--tried", action="append"); r.add_argument("--lesson", action="append")
    r.add_argument("--watch", action="append"); r.add_argument("--unwatch", action="append")
    r.add_argument("--promoted", action="append"); r.add_argument("--stamp", help=argparse.SUPPRESS)
    r.add_argument("--max-lines", type=int, default=MAX_LINES); r.set_defaults(fn=cmd_record)
    pr = sub.add_parser("prune"); pr.add_argument("job"); pr.add_argument("--max-lines", type=int, default=MAX_LINES)
    pr.set_defaults(fn=cmd_prune)
    le = sub.add_parser("lessons"); le.add_argument("job"); le.add_argument("--min", type=int, default=PROMOTE_AT)
    le.set_defaults(fn=cmd_lessons)
    h = sub.add_parser("harvest"); h.add_argument("job"); h.add_argument("--db", required=True)
    h.add_argument("--key-prefix", action="append", required=True)
    h.add_argument("--max-lines", type=int, default=MAX_LINES); h.set_defaults(fn=cmd_harvest)
    pa = sub.add_parser("path"); pa.add_argument("job"); pa.set_defaults(fn=lambda a: print(resolve(a.job)) or 0)
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
