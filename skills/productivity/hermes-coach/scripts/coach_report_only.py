#!/usr/bin/env python3
"""Weekly report-only KPI (stdlib only; judge calls go through coach_eval's bounded runner + engine budget).

A turn is "report-only" when its final reply describes or diagnoses an unresolved problem the agent could have fixed
(or started fixing, or proposed a concrete fix for) and does none of those. The rate is report_only / all judged turns,
split by model family (astra / opus / sonnet / other) and by trigger:

    human-question   a message from the user that does not ask for action
    human-action     a message from the user that asks the agent to do, fix, build or change something
    event            an automated message (card finished, chat close-out, goal continuation, ...)

Targets: human-question <= 10%, event <= 20%. The rubric is the one that labeled the Oct 2026 eval set
(evidence: ~/.hermes/kanban/evidence/report-only/judge.py).

    coach_report_only.py [--db ~/.hermes/state.db] [--days 7] [--max-turns 150] [--budget 2.0] [--data DIR] [--json]

coach_weekly.py calls section() so the split shows up in the weekly output.
"""
import argparse, json, os, random, re, sqlite3, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

FAMILIES = ("astra", "opus", "sonnet", "other")
TRIGGERS = ("human-question", "human-action", "event")
TARGETS = {"human-question": 0.10, "event": 0.20}
MIN_N = 5  # a cell with fewer judged turns is shown but never flagged

RUBRIC = """You label the final reply an AI agent gave its user (David) at the end of one turn. David runs a lot of software \
and infrastructure through this agent. The agent has shell, code editing, git/GitHub, a Kanban board of worker agents, \
a browser and ssh to his machines, and standing approval to fix and ship things without asking. For each item you get \
the trigger (a human message from David, or an automated event such as a card-finished notice or a close-out prompt), \
David's/the event's message, the tools the agent called during the turn, and the agent's final reply. Judge only \
from what is shown.

Return ONLY a JSON array, one object per item, in input order:
{"id": "<id>", "problem": true|false, "agent_fixable": true|false|null, "outcome": "<outcome>", "user_wanted_action": true|false, "quote": "<=20 words from the reply"}

- problem: true if the reply mentions a defect, failure, bug, blocker, stale/wrong state, or something not working or \
not done that is STILL UNRESOLVED when the reply ends ("not fixed", "still broken", "remaining work", "X is outdated", \
"Y failed", "waiting on CI"). false if no unresolved problem is mentioned.
- agent_fixable: when problem is true: could an agent with the access above plausibly fix it or start fixing it itself \
(true), or does it genuinely need David (password, physical action, a decision only he can make, money) or an outside \
party (false)? null when problem is false.
- outcome (pick the best fit for the unresolved problem the reply is mainly about):
  "fixed": the agent itself fixed or changed the thing during this turn (edited code/config, ran the command that \
resolves it), even if final verification is pending.
  "started": the agent started a concrete fix that is now in progress and says so (spawned/assigned a worker, card, \
subagent or background job specifically to fix it, opened a PR).
  "proposed": no fix yet, but the reply proposes a concrete solution or plan (what would change and how), possibly \
asking for a go-ahead.
  "offered": only a vague offer ("want me to fix it?", "I can look into it") with no concrete solution.
  "report_only": the reply describes or diagnoses the problem, or says it is not fixed / can be ignored / is owned by \
an existing task / was relayed to someone, WITHOUT fixing it, starting a new fix, or proposing a concrete solution.
  "na": no unresolved problem in the reply (a pure answer, finished work, chit-chat).
- user_wanted_action: true if the message asked the agent to do, fix, build or change something (not just a question, \
status check or FYI).
"""

EVENT_RX = re.compile(r"^\s*(\[chat-closeout\]|✔ \[|✖ \[|⚠|\[ASYNC DELEGATION|\[Continuing toward|\[Session babysitter\]|"
                      r"\[False-stop|\[Goal|\[Kanban|\[Desktop|\[Inbox)", re.S)
SYNTHETIC_RX = re.compile(r"^\s*(\[(IMPORTANT|System|SYSTEM|Background|background|OUT-OF-BAND|Delegat|Subagent|Process|Cron|"
                          r"Goal|Context|Note|Kanban|Cross-session|CROSS)|<(system|task-notification|memory-context)|"
                          r"Background process|\[Message from|\[Delegation)", re.S)
OUTCOMES = ("fixed", "started", "proposed", "offered", "report_only", "na")


# ----------------------------------------------------------------------------------------------- extraction

def _text(c):
    if c is None:
        return ""
    try:
        j = json.loads(c)
        if isinstance(j, list):
            return "\n".join(p.get("text", "") if p.get("type") in ("text", "input_text", "output_text") else
                             ("[screenshot]" if "image" in str(p.get("type", "")) else "")
                             for p in j if isinstance(p, dict)).strip()
    except (ValueError, TypeError):
        pass
    return c


def extract_turns(db, since, until=None, sources=("desktop",)):
    """Final-reply turns of top-level sessions active since `since` (epoch). Read-only."""
    until = until or time.time()
    con = sqlite3.connect(f"file:{os.path.expanduser(str(db))}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    marks = ",".join("?" * len(sources))
    sess = con.execute(f"select id, model, source from sessions where parent_session_id is null and source in ({marks}) "
                       f"and coalesce(last_activity_at, started_at) > ?", (*sources, since)).fetchall()
    out = []
    for s in sess:
        msgs = con.execute("select role, content, tool_calls, timestamp, codex_message_items is not null as cx "
                           "from messages where session_id=? and coalesce(active,1)=1 order by id", (s["id"],)).fetchall()
        cur, turns = None, []
        for m in msgs:
            if m["role"] == "user":
                t = re.sub(r"<memory-context>.*?</memory-context>", "", _text(m["content"]), flags=re.S).strip()
                if not t or SYNTHETIC_RX.match(t):
                    continue
                cur = {"sid": s["id"], "model": s["model"] or "", "ts": m["timestamp"], "user": t[:1500], "tools": {},
                       "final": "", "codex": 0, "assistant_msgs": 0}
                turns.append(cur)
            elif cur is not None and m["role"] == "assistant":
                cur["assistant_msgs"] += 1
                cur["codex"] += 1 if m["cx"] else 0
                for tc in _json_list(m["tool_calls"]):
                    nm = (tc.get("function") or {}).get("name") or tc.get("name") or "?"
                    cur["tools"][nm] = cur["tools"].get(nm, 0) + 1
                c = _text(m["content"]).strip()
                if c:
                    cur["final"] = c
        for i, t in enumerate(turns):
            if since <= (t["ts"] or 0) < until and t["final"] and len(t["final"]) >= 40 and t["assistant_msgs"]:
                t["id"] = f"{t['sid'][-8:]}:{i}"
                out.append(t)
    con.close()
    return out


def _json_list(raw):
    try:
        v = json.loads(raw) if raw else []
        return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []
    except (ValueError, TypeError):
        return []


# ----------------------------------------------------------------------------------------------- classification

def family(turn):
    """astra / opus / sonnet / other from the session model (a Codex-item turn is astra whatever the session says)."""
    m = (turn.get("model") or "").lower()
    if turn.get("codex", 0) > 0 or "astra" in m:
        return "astra"
    if "opus" in m:
        return "opus"
    if "sonnet" in m:
        return "sonnet"
    return "other"


def trigger(turn, label=None):
    if EVENT_RX.match(turn.get("user") or ""):
        return "event"
    wanted = (label or {}).get("user_wanted_action")
    return "human-action" if wanted else "human-question"


# ----------------------------------------------------------------------------------------------- judging

def clip(s, head, tail):
    s = s or ""
    return s if len(s) <= head + tail + 20 else s[:head] + "\n[…]\n" + s[-tail:]


def item_text(t):
    tools = ", ".join(f"{k}×{v}" for k, v in sorted(t["tools"].items(), key=lambda kv: -kv[1])) or "(none)"
    trig = "event" if EVENT_RX.match(t["user"]) else "human"
    return (f"### id: {t['id']}\ntrigger: {trig}\nMESSAGE:\n{clip(t['user'], 700, 200)}\n"
            f"TOOLS CALLED THIS TURN ({sum(t['tools'].values())}): {tools}\nFINAL REPLY:\n{clip(t['final'], 2000, 1200)}\n")


def parse_labels(text, ids):
    m = re.search(r"\[.*\]", text or "", re.S)
    try:
        arr = json.loads(m.group(0)) if m else None
    except ValueError:
        return {}
    if not isinstance(arr, list):
        return {}
    return {o["id"]: o for o in arr if isinstance(o, dict) and o.get("id") in ids and o.get("outcome") in OUTCOMES}


def judge_turns(turns, ask, batch=6, jobs=4):
    """ask(prompt) -> text or None (None = out of budget). Returns {turn id: label}; unlabeled turns are omitted."""
    from concurrent.futures import ThreadPoolExecutor
    batches = [turns[i:i + batch] for i in range(0, len(turns), batch)]

    def work(b):
        ids = {t["id"] for t in b}
        prompt = RUBRIC + "\nLabel these items.\n\n" + "\n".join(item_text(t) for t in b)
        for _ in range(2):
            txt = ask(prompt)
            if txt is None:
                return {}
            got = parse_labels(txt, ids)
            if got:
                return got
        return {}

    res = {}
    with ThreadPoolExecutor(jobs) as ex:
        for got in ex.map(work, batches):
            res.update(got)
    return res


def budget_ask(budget_usd, model=None, jobs=4):
    """(ask, close, budget): bounded judge calls on coach_eval's reservation ledger and the engine's daily caps."""
    import coach_eval as ce
    runner = ce.DirectRunner(jobs=jobs)
    budget = ce.Budget(budget_usd, engine=ce.live_engine() if getattr(runner, "bounded", False) else None)
    model = model or os.environ.get("COACH_JUDGE_MODEL") or "claude-sonnet-5-5"

    def ask(prompt):
        try:
            r = budget.call(runner, prompt, model=model, toolsets="todo", ignore_rules=True)
        except RuntimeError:
            return ""
        return None if r is None else r[0]
    return ask, runner.close, budget


# ----------------------------------------------------------------------------------------------- KPI

def pct(k, n):
    return k / n if n else None


def compute(turns, labels):
    """Rows of judged turns -> {"cells": {(family, trigger): {n, report_only}}, "by_trigger": {...}, "by_family": {...},
    "all": {...}}; report_only_rate = report_only / judged turns of the cell."""
    cells, by_t, by_f = {}, {}, {}
    tot = {"n": 0, "report_only": 0}
    for t in turns:
        lab = labels.get(t["id"])
        if not lab:
            continue
        f, g = family(t), trigger(t, lab)
        ro = 1 if lab.get("outcome") == "report_only" else 0
        for d, k in ((cells, (f, g)), (by_t, g), (by_f, f)):
            c = d.setdefault(k, {"n": 0, "report_only": 0})
            c["n"] += 1
            c["report_only"] += ro
        tot["n"] += 1
        tot["report_only"] += ro
    for d in (cells, by_t, by_f, {"all": tot}):
        for c in d.values():
            c["rate"] = pct(c["report_only"], c["n"])
    return {"cells": cells, "by_trigger": by_t, "by_family": by_f, "all": tot}


def verdict(rate_by_trigger):
    """{trigger: 'ok' | 'OVER TARGET' | 'n/a' | 'few turns'} against TARGETS (human-action has no target)."""
    out = {}
    for g, tgt in TARGETS.items():
        c = rate_by_trigger.get(g)
        if not c or not c["n"]:
            out[g] = "n/a"
        elif c["n"] < MIN_N:
            out[g] = "few turns"
        else:
            out[g] = "ok" if c["rate"] <= tgt else "OVER TARGET"
    return out


def fmt_cell(c):
    if not c or not c["n"]:
        return "–"
    return f"{c['rate']:.0%} ({c['report_only']}/{c['n']})"


def markdown(res, days, meta=None):
    meta = meta or {}
    out = [f"## Report-only rate (last {days:g} days)", "",
           "`report_only_rate` = share of judged Desktop turns whose final reply diagnoses a problem the agent could fix "
           "and neither fixes it, starts a fix, nor proposes one (judge.py rubric). Targets: human-question ≤ 10%, "
           "event ≤ 20%.", ""]
    if not res["all"]["n"]:
        return out + [meta.get("note") or "No judged turns this window."]
    out += ["| model family | " + " | ".join(TRIGGERS) + " | all |", "|---|" + "---:|" * (len(TRIGGERS) + 1)]
    for f in FAMILIES:
        if f in res["by_family"]:
            out.append(f"| {f} | " + " | ".join(fmt_cell(res["cells"].get((f, g))) for g in TRIGGERS)
                       + f" | {fmt_cell(res['by_family'][f])} |")
    out.append("| **all** | " + " | ".join(fmt_cell(res["by_trigger"].get(g)) for g in TRIGGERS)
               + f" | {fmt_cell(res['all'])} |")
    v = verdict(res["by_trigger"])
    out += ["", "Targets: " + "; ".join(f"{g} {fmt_cell(res['by_trigger'].get(g))} vs ≤ {TARGETS[g]:.0%} → {v[g]}"
                                        for g in TARGETS)]
    over = [g for g, s in v.items() if s == "OVER TARGET"]
    if over:
        worst = {g: sorted(((f, res["cells"][(f, g)]) for f in FAMILIES if (f, g) in res["cells"]
                            and res["cells"][(f, g)]["n"] >= MIN_N), key=lambda x: -x[1]["rate"]) for g in over}
        out.append("Over target: " + "; ".join(f"{g} (highest: " + ", ".join(f"{f} {fmt_cell(c)}" for f, c in w[:2]) + ")"
                                               for g, w in worst.items()))
    if meta:
        out.append(f"Judged {res['all']['n']} of {meta.get('turns', '?')} turns"
                   + (f" (sampled {meta['sampled']})" if meta.get("sampled") else "")
                   + (f", ${meta['cost']:.2f} spent" if meta.get("cost") is not None else "")
                   + (f"; {meta['note']}" if meta.get("note") else "") + ".")
    return out


def run(db, days=7, max_turns=150, budget_usd=2.0, now=None, ask=None, seed=7, data=None):
    """Extract, sample (stratified by model family x trigger so a rare family is not drowned), judge, aggregate."""
    now = now or time.time()
    turns = extract_turns(db, now - days * 86400, now)
    meta = {"turns": len(turns)}
    if not turns:
        meta["note"] = "no Desktop turns in the window"
        return compute([], {}), meta
    rnd = random.Random(seed)
    if len(turns) > max_turns:
        groups = {}
        for t in turns:
            groups.setdefault((family(t), "event" if EVENT_RX.match(t["user"]) else "human"), []).append(t)
        per = max(1, max_turns // len(groups))
        pick = []
        for g in sorted(groups):
            rs = groups[g]
            pick += rs if len(rs) <= per else rnd.sample(rs, per)
        rest = [t for t in turns if t not in pick]
        rnd.shuffle(rest)
        pick += rest[:max(0, max_turns - len(pick))]
        turns, meta["sampled"] = pick[:max_turns], min(max_turns, len(pick))
    close, budget = (lambda: None), None
    if ask is None:
        try:
            ask, close, budget = budget_ask(budget_usd)
        except BaseException as e:  # no provider / interpreter: report it, don't break the weekly run
            meta["note"] = f"judge unavailable ({type(e).__name__}: {str(e)[:100]})"
            return compute([], {}), meta
    try:
        labels = judge_turns(turns, ask)
    finally:
        close()
    if budget is not None:
        meta["cost"] = budget.spent
        if budget.stopped:
            meta["note"] = f"stopped at the {budget.stop_reason or 'run budget'} (engine budget)"
    if len(labels) < len(turns) and "note" not in meta:
        meta["note"] = f"{len(turns) - len(labels)} turns left unlabeled"
    if data:
        log = Path(data) / "reports" / "report_only_rate.jsonl"
        log.parent.mkdir(parents=True, exist_ok=True)
        res = compute(turns, labels)
        row = {"at": int(now), "days": days, "meta": meta, "all": res["all"],
               "cells": {f"{f}/{g}": c for (f, g), c in res["cells"].items()}}
        with open(log, "a") as fh:
            fh.write(json.dumps(row) + "\n")
    return compute(turns, labels), meta


def section(db=None, days=7, data=None, **kw):
    db = db or os.path.expanduser("~/.hermes/state.db")
    try:
        res, meta = run(db, days=days, data=data, **kw)
    except Exception as e:
        return [f"## Report-only rate (last {days:g} days)", "", f"Could not compute: {type(e).__name__}: {e}"]
    return markdown(res, days, meta)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.path.expanduser("~/.hermes/state.db"))
    ap.add_argument("--days", type=float, default=7)
    ap.add_argument("--max-turns", type=int, default=150)
    ap.add_argument("--budget", type=float, default=2.0)
    ap.add_argument("--data", help="coach data repo: append the rollup to reports/report_only_rate.jsonl")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    res, meta = run(a.db, a.days, a.max_turns, a.budget, data=a.data)
    if a.json:
        print(json.dumps({"meta": meta, "all": res["all"], "by_trigger": res["by_trigger"],
                          "cells": {f"{f}/{g}": c for (f, g), c in res["cells"].items()}}, indent=1))
    else:
        print("\n".join(markdown(res, a.days, meta)))


if __name__ == "__main__":
    main()
