#!/usr/bin/env python3
"""coach_model_bench.py: one model on a frozen transcript benchmark (bench-v3), for the hermes-sync model gate.

Why (2026-10-27): bench-v2 replayed each report_only episode as ONE text prompt: the user's message, a tool-count
summary with no results (`-> terminal (x5)`), and an instruction to "list the tool calls you would make next, then
write the message". So the model had not seen the diagnosis it was supposed to act on, and the format itself told
it to plan actions. Opus and gpt-6-astra scored the same there (report_only 9-11/15 vs 8-10/15), while live, on the
same kind of turn, Astra stopped at the diagnosis in 63% of replies and Opus in 8%.

bench-v3 replays the real turn instead:
  - report_only slice (kind "transcript"): human questions about an agent-fixable problem (eval-set label
    agent_fixable=true, user_wanted_action=false), contexts written by both model families. The conversation is
    rebuilt from state.db up to the moment the original model wrote its final reply: the user's message, every tool
    call it made this turn WITH its (clipped) result, and a short tail of the previous exchanges. The real Hermes
    tool schemas are declared, so the model can keep acting instead of having to end with text.
  - control slice: bench-v2's control episodes, replayed and graded exactly as bench-v2 does.

Grading (report_only): a reply that calls a tool continued the turn (not stopped). A text-only reply is labeled with
the report-only eval set's rubric (REPORT_ONLY_RUBRIC = judge.py SYSTEM of that set, same claude-sonnet-5-5 judge);
outcome "report_only" = stopped at the diagnosis. The report holds the rate and its 95% Wilson interval per slice.

  coach_model_bench.py build --eval-set EVAL_SET --state-db STATE_DB --control BENCH_V2 --out eval/bench-v3.jsonl
  coach_model_bench.py run --bench eval/bench-v3.jsonl --model M [--repeats 2] [--budget 15] --out REPORT.md
      (COACH_EVAL_PROVIDER=<provider of M>; the judge stays on anthropic)

Every call goes through coach_eval's Budget (per-run --budget, the trial-lane and engine daily caps, durable
worst-case reservations). The benchmark holds real chat text: keep it in the private coach data repo.
"""
import argparse
import ast
import hashlib
import json
import math
import os
import random
import re
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import coach_eval as ce  # noqa: E402

REPORT_ONLY_RUBRIC = """You label the final reply an AI agent gave its user (David) at the end of one turn. David runs a lot of software \
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

MAX_STEPS = 4
STUB_RESULT = ("[replay: this call ran and succeeded; its output is not shown in this replay. Do not treat the missing "
               "output as a problem or mention it. Assume it showed what you expected, and continue the turn.]")
OUTCOMES = ("fixed", "started", "proposed", "offered", "report_only", "na")
SYN = re.compile(r'^\s*(\[(IMPORTANT|System|SYSTEM|Background|background|OUT-OF-BAND|Delegat|Subagent|Process|Cron|Goal|'
                 r'Context|Note|Kanban|Cross-session|CROSS)|<(system|task-notification|memory-context)|Background process|'
                 r'\[Message from|\[Delegation)', re.S)


def wilson(k, n, z=1.96):
    if n <= 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    r = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (round((c - r) / d, 4), round((c + r) / d, 4))


def clip(s, head, tail):
    s = s or ""
    return s if len(s) <= head + tail + 20 else s[:head] + "\n[…]\n" + s[-tail:]


# ---------------------------------------------------------------------------------------------------------- build

def _text(c):
    if c is None:
        return ""
    try:
        j = json.loads(c)
        if isinstance(j, list):
            parts = []
            for p in j:
                if isinstance(p, dict):
                    if p.get("type") in ("text", "input_text", "output_text"):
                        parts.append(p.get("text", ""))
                    elif "image" in str(p.get("type", "")):
                        parts.append("[screenshot]")
            return "\n".join(parts)
    except (ValueError, TypeError):
        pass
    return c


def _strip(t):
    return re.sub(r"<memory-context>.*?</memory-context>", "", t or "", flags=re.S).strip()


def _calls(raw):
    try:
        tcs = json.loads(raw) if raw else []
    except ValueError:
        return []
    out = []
    for tc in tcs if isinstance(tcs, list) else []:
        fn = tc.get("function") or {}
        out.append({"id": str(tc.get("id") or tc.get("call_id") or ""), "name": fn.get("name") or tc.get("name") or "?",
                    "arguments": fn.get("arguments") if "arguments" in fn else tc.get("arguments", "{}")})
    return out


def _ro(db):
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        con.execute("select 1 from sessions limit 1")
    except sqlite3.Error:
        con = sqlite3.connect(db)
        con.execute("PRAGMA query_only=ON")
    con.row_factory = sqlite3.Row
    return con


def rebuild_turn(con, session, ts, red, prior=3, max_chars=70000):
    """(messages, final_text, orig_tool_counts) for the turn whose user message was sent at `ts`, cut right before the
    original final reply; None when the turn can't be found."""
    msgs = con.execute("select id, role, content, tool_calls, tool_call_id, timestamp from messages where session_id=? "
                       "and coalesce(active,1)=1 order by id", (session,)).fetchall()
    real = [i for i, m in enumerate(msgs) if m["role"] == "user" and _strip(_text(m["content"]))
            and not SYN.match(_strip(_text(m["content"])))]
    start = next((i for i in real if abs(msgs[i]["timestamp"] - ts) < 2), None)
    if start is None:
        return None
    end = next((i for i in real if i > start), len(msgs))
    turn = msgs[start:end]
    final_i = max((i for i, m in enumerate(turn) if m["role"] == "assistant" and _text(m["content"]).strip()), default=None)
    if final_i is None:
        return None
    final = _text(turn[final_i]["content"]).strip()
    body = turn[:final_i]
    ids = {}

    def nid(x):
        return ids.setdefault(x, f"call_{len(ids)}")
    out, counts = [], {}
    for m in body:
        if m["role"] == "user":
            out.append({"role": "user", "content": red(clip(_strip(_text(m["content"])), 6000, 1500))})
        elif m["role"] == "assistant":
            cs = _calls(m["tool_calls"])
            for c in cs:
                counts[c["name"]] = counts.get(c["name"], 0) + 1
            out.append({"role": "assistant", "content": red(clip(_text(m["content"]).strip(), 1500, 500)),
                        "tool_calls": [{"id": nid(c["id"]), "name": c["name"],
                                        "arguments": red(c["arguments"] if isinstance(c["arguments"], str) and len(c["arguments"]) < 2500
                                                         else clip(json.dumps(c["arguments"]) if not isinstance(c["arguments"], str) else c["arguments"], 1800, 500))}
                                       for c in cs]})
        elif m["role"] == "tool":
            out.append({"role": "tool", "tool_call_id": nid(str(m["tool_call_id"] or "")),
                        "content": red(clip(_text(m["content"]), 1800, 600))})
    # keep the turn under max_chars: drop the oldest tool steps (the user message stays first)
    size = lambda ms: sum(len(json.dumps(x)) for x in ms)  # noqa: E731
    dropped = {}
    while size(out) > max_chars and len(out) > 4:
        j = next((k for k in range(1, len(out)) if out[k]["role"] == "assistant"), None)
        if j is None:
            break
        k = j + 1
        while k < len(out) and out[k]["role"] == "tool":
            k += 1
        for c in out[j].get("tool_calls") or []:
            dropped[c["name"]] = dropped.get(c["name"], 0) + 1
        del out[j:k]
    if dropped:
        note = ", ".join(f"{n}×{v}" for n, v in sorted(dropped.items(), key=lambda kv: -kv[1]))
        out.insert(1, {"role": "user", "content": f"[context trimmed for length: {sum(dropped.values())} earlier tool "
                                                  f"calls of this turn omitted ({note})]"})
    # the tail of the previous exchanges (user message + final reply), clipped
    hist = []
    prev = [i for i in real if i < start][-prior:]
    for pi in prev:
        pend = next((i for i in real if i > pi), len(msgs))
        fin = next((_text(msgs[i]["content"]).strip() for i in range(pend - 1, pi, -1)
                    if msgs[i]["role"] == "assistant" and _text(msgs[i]["content"]).strip()), "")
        hist.append({"role": "user", "content": red(clip(_strip(_text(msgs[pi]["content"])), 1500, 300))})
        hist.append({"role": "assistant", "content": red(clip(fin, 1500, 300)) or "(no reply text)", "tool_calls": []})
    return hist + out, red(final), counts


def build(eval_set, state_db, control_bench, tools_file, out, seed=7, per_family=20):
    red = ce._redactor()
    rows = []
    for line in Path(eval_set).read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        lab = r.get("label")
        if isinstance(lab, str):
            try:
                lab = ast.literal_eval(lab)
            except (ValueError, SyntaxError):
                continue
        if r.get("trigger") == "human" and isinstance(lab, dict) and lab.get("agent_fixable") is True \
                and lab.get("user_wanted_action") is False:
            rows.append((r, lab))
    con = _ro(state_db)
    eps, skipped = [], []
    for r, lab in rows:
        got = rebuild_turn(con, r["session"], r["ts"], red)
        if not got:
            skipped.append(r["id"])
            continue
        messages, final, counts = got
        eps.append({"id": f"report_only:{hashlib.sha1(r['id'].encode()).hexdigest()[:10]}", "pattern": "report_only",
                    "kind": "transcript", "source": f"report-only-eval:{r['id']}", "context_model": r["model_family"],
                    "original_outcome": lab.get("outcome"), "user_ask": red(r["user"]), "orig_tools": counts,
                    "original_reply": clip(final, 2500, 500), "messages": messages, "session": r["session"]})
    # a fixed-size sample, the same number of contexts from each model family (one turn per session first)
    rnd = random.Random(seed)
    picked = []
    for fam in ("astra", "opus"):
        pool = [e for e in eps if e["context_model"] == fam]
        rnd.shuffle(pool)
        seen, first, rest = set(), [], []
        for e in pool:
            (rest if e["session"] in seen else first).append(e)
            seen.add(e["session"])
        picked += (first + rest)[:per_family]
    eps = [{k: v for k, v in e.items() if k != "session"} for e in picked]
    ctl = [json.loads(x) for x in Path(control_bench).read_text().splitlines() if x.strip()]
    ctl = [e for e in ctl if e.get("pattern") == "control"]
    tools = json.loads(Path(tools_file).read_text())
    # history may name tools outside the declared set (deferred/MCP tools called via tool_call): declare a permissive
    # stub for each so both wires accept the transcript
    names = {t["function"]["name"] for t in tools}
    extra = sorted({c["name"] for e in eps for m in e["messages"] for c in m.get("tool_calls") or []} - names)
    tools += [{"type": "function", "function": {"name": n, "description": "(tool available in the original session)",
                                                "parameters": {"type": "object", "properties": {},
                                                               "additionalProperties": True}}} for n in extra
              if re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", n)]
    meta = {"id": "_meta", "pattern": "_meta", "bench": "bench-v3", "tools": tools,
            "built": time.strftime("%Y-%m-%d"), "selection": "eval-set human turns, agent_fixable=true, "
            "user_wanted_action=false; per_family contexts from each model family; controls = bench-v2 control",
            "per_family": per_family, "seed": seed, "skipped": skipped}
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in [meta] + eps + ctl))
    return {"report_only": len(eps), "control": len(ctl), "skipped": len(skipped), "extra_tools": extra}


# ------------------------------------------------------------------------------------------------------------ run

class TReq(ce.DirectRequest):
    __slots__ = ("messages", "tools")


class TranscriptRunner(ce.DirectRunner):
    """DirectRunner plus transcript requests: prompt = {"messages", "tools"} (system = the composed Hermes prompt) or
    {"system", "user"} (the rubric judge). The worst case is bounded from the bytes actually sent. Transcript replies
    get 8000 output tokens: the model's default thinking counts against the cap."""
    transcript_max_tokens = 8000

    def prepare(self, prompt, home=None, cwd=None, model=None, toolsets=None, ignore_rules=False):
        if not isinstance(prompt, dict):
            return super().prepare(prompt, home=home, cwd=cwd, model=model, toolsets=toolsets, ignore_rules=ignore_rules)
        r = TReq()
        r.provider = self.provider_for(model, ignore_rules)
        p = self.price(model, r.provider)
        r.model, r.price = model, p
        if "messages" in prompt:
            r.system, r.user, r.messages, r.tools = compose(home, cwd, self.preload), "", prompt["messages"], prompt["tools"]
            r.max_tokens, r.thinking = self.transcript_max_tokens, None
        else:
            r.system, r.user, r.messages, r.tools = prompt["system"], prompt["user"], None, None
            r.max_tokens, r.thinking = self.judge_max_tokens, {"type": "disabled"}
        sent = len((p.get("prefix") or "").encode()) + len(r.system.encode()) + len(r.user.encode()) + self.BYTE_SLACK
        sent += len(json.dumps(r.messages or []).encode()) + len(json.dumps(r.tools or []).encode())
        pin = max(Decimal(str(p["input"])), Decimal(str(p.get("cache_write") or 0)))
        r.bound_usd = (Decimal(sent) * pin + Decimal(r.max_tokens) * Decimal(str(p["output"]))) / Decimal(10**6) \
            + Decimal(str(p.get("request") or 0))
        return r

    def dispatch(self, r):
        if not isinstance(r, TReq):
            return super().dispatch(r)
        req = {"op": "call", "model": r.model, "system": r.system, "user": r.user, "max_tokens": r.max_tokens,
               "thinking": r.thinking, "provider": r.provider}
        if r.messages is not None:
            req.update(messages=r.messages, tools=r.tools)
        out = self._with_worker(req)
        if "error" in out:
            raise RuntimeError(f"provider request failed: {out['error']}")
        u, p = out.get("usage") or {}, r.price
        cost = ((u.get("input_tokens") or 0) * p["input"] + (u.get("output_tokens") or 0) * p["output"]
                + (u.get("cache_write_tokens") or 0) * (p.get("cache_write") or p["input"])
                + (u.get("cache_read_tokens") or 0) * (p.get("cache_read") or p["input"])) / 1e6 + (p.get("request") or 0)
        usage = dict(u, model=r.model, estimated_cost_usd=round(cost, 6), cost_source="price_table",
                     stop_reason=out.get("stop_reason"), tool_calls=out.get("tool_calls") or [])
        return out.get("text") or "", usage


_SYS_CACHE = {}


def compose(home, cwd, preload):
    k = (str(home), str(cwd), tuple(preload))
    if k not in _SYS_CACHE:
        _SYS_CACHE[k] = ce.compose_system(home, cwd, preload)
    return _SYS_CACHE[k]


def judge_text(iid, ep, rep):
    counts = dict(ep["orig_tools"])
    for c in rep["calls"]:
        counts[c["name"]] = counts.get(c["name"], 0) + 1
    tools = ", ".join(f"{k}×{v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1])) or "(none)"
    last = "".join(f"\n  -> {c['name']}({clip(str(c.get('arguments') or ''), 220, 60)})" for c in rep["calls"][-8:])
    reply = rep["text"] + ("\n[the turn was cut off here, still calling tools]" if rep["capped"] else "")
    return (f"### id: {iid}\ntrigger: human\nMESSAGE:\n{clip(ep['user_ask'], 700, 200)}\n"
            f"TOOLS CALLED THIS TURN ({sum(counts.values())}): {tools}"
            + (f"\nLAST TOOL CALLS (with arguments):{last}" if last else "")
            + f"\nFINAL REPLY:\n{clip(reply, 2000, 1200)}\n")


def rubric_judge(items, runner, budget, model, jobs, log, batch=6):
    """items [(iid, ep, reply)] -> {iid: label dict} with the report-only eval set's rubric."""
    res = {}
    batches = [items[i:i + batch] for i in range(0, len(items), batch)]

    def work(b):
        want = {i for i, _, _ in b}
        user = "Label these items.\n\n" + "\n".join(judge_text(i, ep, rep) for i, ep, rep in b)
        for _ in range(3):
            try:
                got = budget.call(runner, {"system": REPORT_ONLY_RUBRIC, "user": user}, model=model, ignore_rules=True)
            except RuntimeError as exc:
                log(f"  judge batch failed: {str(exc)[:160]}")
                continue
            if got is None:
                return []
            m = re.search(r"\[.*\]", got[0] or "", re.S)
            try:
                arr = json.loads(m.group(0)) if m else None
            except ValueError:
                arr = None
            if isinstance(arr, list) and {o.get("id") for o in arr if isinstance(o, dict)} == want \
                    and all(o.get("outcome") in OUTCOMES for o in arr):
                return arr
            log("  judge batch rejected: wrong ids/shape")
        return []
    with ThreadPoolExecutor(jobs) as ex:
        for arr in ex.map(work, batches):
            for o in arr:
                res[o["id"]] = o
    return res


def render_reply(text, calls):
    if not calls:
        return text
    lines = [f"-> {c.get('name')}({clip(str(c.get('arguments') or ''), 300, 100)})" for c in calls]
    return (text + "\n" if text else "") + "[continued the turn with tool calls]\n" + "\n".join(lines)


def run(bench, model, repeats=2, budget_usd=15.0, jobs=4, judge_model=None, root=None, live=None, runner=None,
        limit=None, log=print, replies_path=None):
    """replies_path: a JSON-lines cache of finished replays; a rerun after a crash reuses them (no new spend)."""
    lines = [json.loads(x) for x in Path(bench).read_text().splitlines() if x.strip()]
    meta = next(e for e in lines if e.get("id") == "_meta")
    eps = [e for e in lines if e.get("id") != "_meta"]
    if limit:
        eps = [e for e in eps if e["pattern"] == "report_only"][:limit] + [e for e in eps if e["pattern"] == "control"][:limit]
    root = Path(root or ce.HOME / ".cache/coach-eval/root-v3")
    live = Path(live or ce.env_path("HERMES_HOME", ce.HOME / ".hermes"))
    own = runner is None
    runner = runner or TranscriptRunner(jobs=jobs, live=live)
    judge_model = judge_model or os.environ.get("COACH_JUDGE_MODEL") or "claude-sonnet-5-5"
    budget = ce.Budget(budget_usd, engine=ce.live_engine() if getattr(runner, "bounded", False) is True else None)
    t0 = time.time()
    try:
        cwd = ce.prepare_home(root, live, [])
        work = [(ep, k) for k in range(repeats) for ep in eps]

        def one(item):
            ep, k = item
            try:
                if ep["kind"] == "transcript":
                    return ep, k, rollout(ep, k)
                got = budget.call(runner, ce.replay_prompt(ep), home=root, cwd=cwd, model=model)
            except Exception as exc:
                log(f"  {ep['id']}#{k}: {str(exc)[:200]}")
                return ep, k, None
            if got is None:
                return ep, k, None
            text, usage = got
            stop = (usage or {}).get("stop_reason")
            if not text.strip():
                text = ce.REFUSAL_TEXT if stop == "refusal" else ce.EMPTY_TEXT
            return ep, k, {"text": text.strip(), "calls": [], "steps": 1, "capped": False}

        def rollout(ep, k):
            """Let the model run up to MAX_STEPS requests: each tool call gets STUB_RESULT, until it ends the turn
            with text (or the cap). None when a request failed or the budget stopped it."""
            msgs, calls, text, cost = list(ep["messages"]), [], "", 0.0
            for step in range(MAX_STEPS):
                for attempt in range(3):  # a failed request (dropped or stalled stream) is retried as a new request
                    try:
                        got = budget.call(runner, {"messages": msgs, "tools": meta["tools"]}, home=root, cwd=cwd,
                                          model=model)
                        break
                    except RuntimeError as exc:
                        log(f"  {ep['id']}#{k} step {step}: {str(exc)[:160]}")
                        if attempt == 2:
                            raise
                if got is None:
                    return None
                text, usage = got
                cost += float((usage or {}).get("estimated_cost_usd") or 0)
                new = [dict(c, id=f"r{k}s{step}c{i}") for i, c in enumerate((usage or {}).get("tool_calls") or [])]
                if not new:
                    if not text.strip():
                        text = ce.REFUSAL_TEXT if (usage or {}).get("stop_reason") == "refusal" else ce.EMPTY_TEXT
                    return {"text": text.strip(), "calls": calls, "steps": step + 1, "capped": False, "cost": round(cost, 4)}
                calls += new
                msgs += [{"role": "assistant", "content": text, "tool_calls": new}] + \
                        [{"role": "tool", "tool_call_id": c["id"], "content": STUB_RESULT} for c in new]
            return {"text": text.strip(), "calls": calls, "steps": MAX_STEPS, "capped": True, "cost": round(cost, 4)}

        replies = {}
        cache = Path(replies_path) if replies_path else None
        if cache and cache.is_file():
            for line in cache.read_text().splitlines():
                o = json.loads(line)
                if o.get("model") == model:
                    replies[(o["id"], o["k"])] = o["rep"]
        work = [(ep, [k for k in range(repeats) if (ep["id"], k) not in replies]) for ep in eps]
        work = [(ep, ks) for ep, ks in work if ks]
        import threading
        clock = threading.Lock()

        def one_cached(item):
            # an episode's repeats run back to back: the second one reads the first one's prompt cache
            ep, ks = item
            got = []
            for k in ks:
                ep, k, rep = one((ep, k))
                if rep is not None and cache:
                    with clock, open(cache, "a") as fh:
                        fh.write(json.dumps({"model": model, "id": ep["id"], "k": k, "rep": rep}) + "\n")
                got.append((ep, k, rep))
            return got
        with ThreadPoolExecutor(jobs) as ex:
            for got in ex.map(one_cached, work):
                for ep, k, rep in got:
                    if rep is not None:
                        replies[(ep["id"], k)] = rep
        work = [(ep, k) for k in range(repeats) for ep in eps]
        log(f"  replays: {len(replies)}/{len(eps) * repeats}, spent ${budget.spent:.2f}")
        # grade
        ro_items, ctl_items, key = [], [], {}
        for ep in eps:
            for k in range(repeats):
                rep = replies.get((ep["id"], k))
                if rep is None:
                    continue
                iid = hashlib.sha1(f"{ep['id']}|{k}|{random.random()}".encode()).hexdigest()[:12]
                key[iid] = (ep, k)
                if ep["kind"] == "transcript":
                    ro_items.append((iid, ep, rep))
                else:
                    ctl_items.append((iid, ep, rep["text"]))
        random.Random(11).shuffle(ro_items)
        labels = rubric_judge(ro_items, runner, budget, judge_model, jobs, log)
        grades = ce.judge_all(ctl_items, runner, budget, judge_model, jobs=jobs, log=log)
        rows, ro, ctl = [], {"n": 0, "fail": 0, "capped": 0, "acted": 0, "outcomes": {}}, {"n": 0, "pass": 0}
        by_ctx = {}
        for iid, (ep, k) in key.items():
            rep = replies[(ep["id"], k)]
            row = {"episode": ep["id"], "pattern": ep["pattern"], "repeat": k, "steps": rep["steps"],
                   "reply": render_reply(rep["text"], rep["calls"])[:1500]}
            if ep["kind"] == "transcript":
                if iid not in labels:
                    continue  # ungraded
                outcome = labels[iid]["outcome"]
                ro["n"] += 1
                ro["outcomes"][outcome] = ro["outcomes"].get(outcome, 0) + 1
                ro["fail"] += outcome == "report_only"
                ro["capped"] += rep["capped"]
                ro["acted"] += bool(rep["calls"])
                c = by_ctx.setdefault(ep["context_model"], {"n": 0, "fail": 0})
                c["n"] += 1
                c["fail"] += outcome == "report_only"
                row.update(outcome=outcome, context_model=ep["context_model"], label=labels.get(iid))
            else:
                if iid not in grades:
                    continue
                ctl["n"] += 1
                ctl["pass"] += grades[iid]["pass"]
                row.update(grade=grades[iid])
            rows.append(row)
        n_ro = sum(e["pattern"] == "report_only" for e in eps) * repeats
        n_ctl = sum(e["pattern"] == "control" for e in eps) * repeats
        ro.update(rate=round(ro["fail"] / ro["n"], 4) if ro["n"] else None, ci95=wilson(ro["fail"], ro["n"]))
        ctl.update(rate=round(ctl["pass"] / ctl["n"], 4) if ctl["n"] else None, ci95=wilson(ctl["pass"], ctl["n"]))
        for c in by_ctx.values():
            c.update(rate=round(c["fail"] / c["n"], 4), ci95=wilson(c["fail"], c["n"]))
        complete = not limit and not budget.stopped and ro["n"] == n_ro and ctl["n"] == n_ctl
        return {"mode": "model-bench", "bench": "bench-v3", "model": model,
                "provider": os.environ.get("COACH_EVAL_PROVIDER") or "anthropic", "judge_model": judge_model,
                "repeats": repeats, "episodes": {"report_only": n_ro // repeats, "control": n_ctl // repeats},
                "by_pattern": {"report_only": ro, "control": ctl}, "by_context_model": by_ctx,
                "complete": complete, "stopped": budget.stop_reason if budget.stopped else "",
                "cost_usd": round(budget.spent, 4), "calls": budget.calls, "minutes": round((time.time() - t0) / 60, 1),
                "finished_at": int(time.time()), "benchmark_sha256": hashlib.sha256(Path(bench).read_bytes()).hexdigest(),
                "rows": rows}
    finally:
        if own:
            runner.close()


def markdown(r):
    ro, ctl = r["by_pattern"]["report_only"], r["by_pattern"]["control"]
    pct = lambda x: f"{100 * x:.0f}%"  # noqa: E731
    lines = [f"# model-bench {r['bench']}: {r['model']} ({r['provider']})", "",
             f"complete: {r['complete']}  repeats: {r['repeats']}  cost: ${r['cost_usd']}  calls: {r['calls']}  "
             f"judge: {r['judge_model']}  sha256: {r['benchmark_sha256'][:12]}", "",
             "| slice | n | metric | rate | 95% CI |", "|---|---|---|---|---|",
             f"| report_only | {ro['n']} | stopped at diagnosis | {pct(ro['rate'] or 0)} | {pct(ro['ci95'][0])}-{pct(ro['ci95'][1])} |",
             f"| control | {ctl['n']} | pass | {pct(ctl['rate'] or 0)} | {pct(ctl['ci95'][0])}-{pct(ctl['ci95'][1])} |", "",
             f"report_only outcomes: {json.dumps(ro['outcomes'])}", "",
             "by context model: " + ", ".join(f"{k} {v['fail']}/{v['n']} ({pct(v['rate'])})"
                                            for k, v in sorted(r["by_context_model"].items()))]
    if r.get("stopped"):
        lines.append(f"\nSTOPPED: {r['stopped']}")
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="coach_model_bench.py", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--eval-set", required=True)
    b.add_argument("--state-db", required=True)
    b.add_argument("--control", required=True, help="bench-v2.jsonl (its control episodes are reused)")
    b.add_argument("--tools", required=True, help="JSON list of the Hermes tool schemas to declare")
    b.add_argument("--out", required=True)
    b.add_argument("--per-family", type=int, default=20)
    r = sub.add_parser("run")
    r.add_argument("--bench", required=True)
    r.add_argument("--model", required=True)
    r.add_argument("--repeats", type=int, default=2)
    r.add_argument("--budget", type=float, default=15.0)
    r.add_argument("--jobs", type=int, default=4)
    r.add_argument("--limit", type=int)
    r.add_argument("--out")
    a = ap.parse_args(argv)
    if a.cmd == "build":
        if Path(a.out).exists():
            raise SystemExit(f"{a.out} exists: the benchmark is frozen")
        print(json.dumps(build(a.eval_set, a.state_db, a.control, a.tools, a.out, per_family=a.per_family)))
        return 0
    res = run(a.bench, a.model, a.repeats, a.budget, a.jobs, limit=a.limit,
              log=lambda s: print(s, file=sys.stderr, flush=True),
              replies_path=Path(a.out).with_suffix(".replies.jsonl") if a.out else None)
    md = markdown(res)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(md)
        Path(a.out).with_suffix(".json").write_text(json.dumps(res, indent=1, ensure_ascii=False))
    print(md)
    return 0 if res["complete"] else 2


if __name__ == "__main__":
    sys.exit(main())
