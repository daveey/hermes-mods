#!/usr/bin/env python3
"""Skill trigger replay eval: does the right skill load for a natural ask?

    trigger_eval.py index                              # print the live index (core-rendered) + overlay self-check
    trigger_eval.py gen-cards [--only a,b]             # draft missing cards with the pool LLM (cards/<name>.yaml)
    trigger_eval.py run --variant baseline --out R     # 3 router runs per ask -> results.jsonl rows (resumable)
    trigger_eval.py report --results R --variant V --out report.md
    trigger_eval.py cost --variant a=overrides.json ...  # index tokens per format (one probe call each)

Router calls go to an Anthropic-compatible endpoint: start `hermes-pool-llm serve --port 8791` and pass
--base-url http://127.0.0.1:8791 (default). Run with any Python that has PyYAML; core is only used in children.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import cards as cardlib  # noqa: E402
import render_index  # noqa: E402
import router  # noqa: E402
import score as scorelib  # noqa: E402

POOL = Path.home() / ".hermes" / "plugins" / "pool-llm" / "bin" / "hermes-pool-llm"


def _ctx(args) -> dict:
    core = Path(args.core).expanduser()
    home = Path(args.home).expanduser()
    paths = render_index.skill_paths(core, home)
    live = render_index.render_live(core, home)
    rows = render_index.index_rows(live)
    bundled = cardlib.bundled_names(home, Path(args.live_core).expanduser())
    owned = {n for n in rows if n not in bundled}
    return {"core": core, "home": home, "paths": paths, "live": live, "rows": rows, "owned": owned, "bundled": bundled}


def cmd_index(args) -> int:
    ctx = _ctx(args)
    over = render_index.render({}, ctx["core"], ctx["home"])
    print(ctx["live"])
    print(f"\n# {len(ctx['rows'])} skills; owned {len(ctx['owned'])}; overlay identical: {over == ctx['live']}",
          file=sys.stderr)
    return 0 if over == ctx["live"] else 1


def _pool_ask(prompt: str, system: str, model: str) -> str:
    proc = subprocess.run([str(POOL), "ask", "--provider", "anthropic", "--model", model, "--system", system,
                           "--max-tokens", "1500", "-"], input=prompt, capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-500:])
    return proc.stdout


def _is_public_card(name: str, ctx: dict) -> bool:
    """Cards of bundled skills (kept in this public harness dir) and of hermes-mods skills are published; cards of
    private skills live next to the skill in the private repo."""
    path = ctx["paths"].get(name)
    return name not in ctx["owned"] or path is None or "hermes-mods" in path.resolve().parts


def cmd_gen_cards(args) -> int:
    ctx = _ctx(args)
    known = set(ctx["rows"])
    names = args.only.split(",") if args.only else sorted(known)
    have, missing = cardlib.load_cards(ctx["paths"], ctx["owned"], names, None)
    todo = names if args.force else missing
    mined = {}
    for db in args.mine_db or []:
        src = (json.loads(Path(db).expanduser().read_text()) if db.endswith(".json")
               else cardlib.mine_phrasings(Path(db).expanduser()))
        for k, v in src.items():
            mined.setdefault(k, [])
            mined[k] += [x for x in v if x not in mined[k]]
    cardlib.HARNESS_CARDS.mkdir(exist_ok=True)
    print(f"{len(todo)} cards to draft ({len(have)} exist); mined phrasings for {len(mined)} skills", file=sys.stderr)

    def one(name):
        md = ctx["paths"][name].read_text(encoding="utf-8-sig")
        parts = render_index.split_frontmatter(md)
        import yaml
        fm = yaml.safe_load(parts[0]) if parts else {}
        body = parts[1] if parts else md
        prompt = cardlib.gen_prompt(name, str((fm or {}).get("description", "")), body, ctx["rows"], mined.get(name, []),
                                    public=_is_public_card(name, ctx))
        err = None
        for _ in range(3):
            try:
                data = cardlib.parse_generated(_pool_ask(prompt, cardlib.GEN_SYSTEM, args.model), name, known)
                if mined.get(name):
                    data["mined_from_sessions"] = len(mined[name])
                (cardlib.HARNESS_CARDS / f"{name}.yaml").write_text(cardlib.dump_card(data), encoding="utf-8")
                return name, None
            except Exception as e:  # malformed draft: retry
                err = e
        return name, str(err)

    fails = []
    with ThreadPoolExecutor(args.workers) as ex:
        for fut in as_completed([ex.submit(one, n) for n in todo]):
            name, err = fut.result()
            print(f"{'FAIL' if err else 'ok  '} {name} {err or ''}", file=sys.stderr)
            if err:
                fails.append(name)
    return 1 if fails else 0


def _load_overrides(spec: str | None) -> dict:
    if not spec:
        return {}
    return json.loads(Path(spec).read_text())


def _parse_flags(items) -> dict:
    out = {}
    for it in items or []:
        k, _, v = it.partition("=")
        out[k] = v
    return out


def cmd_run(args) -> int:
    ctx = _ctx(args)
    known = set(ctx["rows"])
    overrides = _load_overrides(args.overrides)
    flags = _parse_flags(args.flag)
    system = (ctx["live"] if not overrides and not flags and args.core == args.live_core
              else render_index.render(overrides, ctx["core"], ctx["home"], flags))
    names = args.skills.split(",") if args.skills else sorted(known)
    cards, missing = cardlib.load_cards(ctx["paths"], ctx["owned"], names, known)
    if missing:
        print(f"no card for {len(missing)} skills (skipped): {', '.join(missing[:20])}", file=sys.stderr)
    out = Path(args.out)
    current = {aid: ask for c in cards.values() for aid, _, ask, _ in c.asks()}
    done = set()
    if out.is_file():
        for ln in out.read_text().splitlines():
            try:
                r = json.loads(ln)
            except Exception:
                continue
            # A row counts only for the ask text the card has now: an edited ask is re-run.
            if r.get("variant") == args.variant and not r.get("error") and current.get(r["ask_id"]) == r.get("ask"):
                done.add((r["ask_id"], r["run"]))
    jobs = [(c, aid, kind, ask, inst, run) for c in cards.values() for aid, kind, ask, inst in c.asks()
            for run in range(args.runs) if (aid, run) not in done]
    random.Random(7).shuffle(jobs)
    print(f"variant={args.variant}: {len(jobs)} calls to make ({len(done)} done), index {len(system)} chars",
          file=sys.stderr)
    lock = threading.Lock()

    def one(job):
        c, aid, kind, ask, inst, run = job
        row = {"variant": args.variant, "ask_id": aid, "skill": c.skill, "kind": kind, "ask": ask,
               "instead": inst, "run": run, "model": args.model}
        try:
            row.update(router.run_ask(args.base_url, system, ask, args.model))
        except Exception as e:
            row["error"] = str(e)[:300]
            row["loaded"] = []
        with lock, out.open("a") as fh:
            fh.write(json.dumps(row) + "\n")
        return row

    n = errs = 0
    with ThreadPoolExecutor(args.workers) as ex:
        for fut in as_completed([ex.submit(one, j) for j in jobs]):
            r = fut.result()
            n += 1
            errs += bool(r.get("error"))
            if n % 50 == 0:
                print(f"  {n}/{len(jobs)} (errors {errs})", file=sys.stderr, flush=True)
    return 1 if errs else 0


def read_rows(path: Path, variant: str | None, skills: set[str] | None = None) -> list[dict]:
    rows = []
    for ln in path.read_text().splitlines():
        try:
            r = json.loads(ln)
        except Exception:
            continue
        if (variant is None or r.get("variant") == variant) and (skills is None or r["skill"] in skills):
            rows.append(r)
    # Keep the newest good row per (ask_id, run): a resumed run appends retries of errored calls and re-runs of
    # edited asks after the old rows.
    best = {}
    for r in rows:
        k = (r["ask_id"], r["run"])
        if k not in best or not r.get("error"):
            best[k] = r
    return list(best.values())


def cmd_report(args) -> int:
    ctx = _ctx(args)
    skills = set(args.skills.split(",")) if args.skills else None
    rows = read_rows(Path(args.results), args.variant, skills)
    m = scorelib.score(rows)
    notes = [f"Variant `{args.variant}`; model {rows[0].get('model') if rows else '?'}; {args.note or ''}".strip()]
    Path(args.out).write_text(scorelib.render_report(args.title or f"Skill trigger eval: {args.variant}", m,
                                                      ctx["owned"], notes))
    if args.json:
        Path(args.json).write_text(json.dumps(m, indent=1, sort_keys=True))
    print(f"mean recall {m['mean_recall']:.3f} micro {m['micro_recall']:.3f} near-miss ok {m['mean_near_miss_ok']:.3f} "
          f"collisions {len(m['collisions'])} errors {m['errors']}", file=sys.stderr)
    return 0


def cmd_cost(args) -> int:
    ctx = _ctx(args)
    out = {}
    for spec in args.variant:
        name, _, rest = spec.partition("=")
        ov_path, _, flagstr = rest.partition("@")
        flags = _parse_flags(flagstr.split(",") if flagstr else [])
        core = Path(args.alt_core).expanduser() if flags else ctx["core"]
        text = render_index.render(_load_overrides(ov_path or None), core, ctx["home"], flags)
        probe = router.run_ask(args.base_url, text, "Reply with exactly: ok. Do not call any tools.", args.model)
        out[name] = {"index_chars": len(text), "input_tokens": probe["input_tokens"]}
        print(f"{name}: chars={len(text)} input_tokens={probe['input_tokens']}", file=sys.stderr)
    empty = router.run_ask(args.base_url, "You are a helpful assistant.", "Reply with exactly: ok. Do not call any tools.", args.model)
    out["_no_index"] = {"index_chars": len("You are a helpful assistant."), "input_tokens": empty["input_tokens"]}
    print(json.dumps(out, indent=1))
    if args.out:
        Path(args.out).write_text(json.dumps(out, indent=1))
    return 0


def cmd_draft(args) -> int:
    """Draft a2/b/c descriptions for --skills (or the weakest N of --metrics) -> <out>/{drafts,a2,b,c}.json."""
    import describe
    import yaml
    ctx = _ctx(args)
    metrics = json.loads(Path(args.metrics).read_text()) if args.metrics else {"recall": {}}
    names = (args.skills.split(",") if args.skills else
             scorelib.weakest(metrics, args.weakest, among=ctx["owned"] if args.owned_only else None))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    drafts_path = out / "drafts.json"
    drafts = json.loads(drafts_path.read_text()) if drafts_path.is_file() else {}
    lock = threading.Lock()

    def one(name):
        md = ctx["paths"][name].read_text(encoding="utf-8-sig")
        parts = render_index.split_frontmatter(md)
        fm = yaml.safe_load(parts[0]) if parts else {}
        p = describe.prompt(name, str((fm or {}).get("description", "")), parts[1] if parts else md,
                            describe.confused_with(metrics, name), ctx["rows"])
        err = None
        for _ in range(3):
            try:
                ask = p if err is None else f"{p}\n\nYour previous answer was rejected: {err}. Shorten it to fit."
                d = describe.parse(_pool_ask(ask, describe.SYSTEM, args.model))
                with lock:
                    drafts[name] = d
                return name, None
            except Exception as e:
                err = e
        return name, str(err)

    todo = [n for n in names if n not in drafts]
    with ThreadPoolExecutor(args.workers) as ex:
        for fut in as_completed([ex.submit(one, n) for n in todo]):
            name, err = fut.result()
            print(f"{'FAIL' if err else 'ok  '} {name} {err or ''}", file=sys.stderr)
    drafts = {n: drafts[n] for n in names if n in drafts}
    drafts_path.write_text(json.dumps(drafts, indent=1))
    for fmt, ov in describe.overrides(drafts).items():
        (out / f"{fmt}.json").write_text(json.dumps(ov, indent=1))
    (out / "skills.txt").write_text(",".join(names))
    return 0 if len(drafts) == len(names) else 1


def cmd_place_cards(args) -> int:
    """Move owned skills' drafted cards from cards/ to <skill>/references/trigger-cards.yaml.

    gen-cards drafts every card into cards/ (review happens in one place); this step files the owned ones next to
    their skill. A skill served from the deployed hermes-mods checkout is written into --mods-worktree instead
    (never edit the live checkout)."""
    ctx = _ctx(args)
    mods_live = Path(args.mods_live).expanduser().resolve()
    mods_wt = Path(args.mods_worktree).expanduser().resolve() if args.mods_worktree else None
    moved = 0
    for name in sorted(ctx["owned"]):
        src = cardlib.HARNESS_CARDS / f"{name}.yaml"
        md = ctx["paths"].get(name)
        if not src.is_file() or md is None:
            continue
        dst = cardlib.card_path_for(name, md.resolve(), True)
        if mods_live in dst.parents:
            if mods_wt is None:
                print(f"skip {name}: in the live hermes-mods checkout; pass --mods-worktree", file=sys.stderr)
                continue
            dst = mods_wt / dst.relative_to(mods_live)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        src.unlink()
        moved += 1
        print(f"{name} -> {dst}", file=sys.stderr)
    print(f"moved {moved} cards", file=sys.stderr)
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--core", default=str(render_index.DEFAULT_CORE), help="core checkout that renders the index")
    p.add_argument("--live-core", default=str(render_index.DEFAULT_CORE), help="installed core (ownership, baseline)")
    p.add_argument("--home", default=str(render_index.DEFAULT_HOME))
    p.add_argument("--base-url", default=os.environ.get("STE_BASE_URL", "http://127.0.0.1:8791"))
    p.add_argument("--model", default="claude-opus-5-5")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("index")
    g = sub.add_parser("gen-cards")
    g.add_argument("--only")
    g.add_argument("--force", action="store_true")
    g.add_argument("--workers", type=int, default=6)
    g.add_argument("--mine-db", action="append", help="state.db to mine real phrasings from (repeatable)")
    r = sub.add_parser("run")
    r.add_argument("--variant", required=True)
    r.add_argument("--overrides", help="JSON {skill: {description?, triggers?}}")
    r.add_argument("--flag", action="append", help="core prototype flag K=V (needs --core with the prototype)")
    r.add_argument("--skills", help="comma list: only these skills' cards")
    r.add_argument("--runs", type=int, default=3)
    r.add_argument("--workers", type=int, default=8)
    r.add_argument("--out", required=True)
    rp = sub.add_parser("report")
    rp.add_argument("--results", required=True)
    rp.add_argument("--variant", required=True)
    rp.add_argument("--skills")
    rp.add_argument("--out", required=True)
    rp.add_argument("--json")
    rp.add_argument("--title")
    rp.add_argument("--note")
    c = sub.add_parser("cost")
    c.add_argument("--variant", action="append", required=True, help="name=overrides.json[@K=V,K=V]")
    c.add_argument("--alt-core", default=str(render_index.DEFAULT_CORE), help="core with prototype flags")
    c.add_argument("--out")
    d = sub.add_parser("draft")
    d.add_argument("--metrics", help="metrics JSON from `report --json` (confusions + weakest)")
    d.add_argument("--skills")
    d.add_argument("--weakest", type=int, default=30)
    d.add_argument("--owned-only", action="store_true")
    d.add_argument("--workers", type=int, default=8)
    d.add_argument("--out", required=True)
    pc = sub.add_parser("place-cards")
    pc.add_argument("--mods-live", default="~/.hermes/repos/hermes-mods")
    pc.add_argument("--mods-worktree")
    a = p.parse_args(argv)
    if a.cmd in ("gen-cards", "draft"):
        a.model = "claude-sonnet-5-5" if a.model == "claude-opus-5-5" else a.model
    return {"index": cmd_index, "gen-cards": cmd_gen_cards, "run": cmd_run, "report": cmd_report,
            "cost": cmd_cost, "draft": cmd_draft, "place-cards": cmd_place_cards}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
