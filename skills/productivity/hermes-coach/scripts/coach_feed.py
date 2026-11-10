#!/usr/bin/env python3
"""Refresh this host's session digests in a git checkout and push them (stdlib only).

    coach_feed.py --host NAME [--repo-dir ~/code/hermes-coach-data] [--home ~/.hermes] [--no-push]

Runs session_digest.py into <repo-dir>, then commits and pushes only when a digest changed.
Pull uses rebase + autostash; each host writes only its own <host>/ subtree, so hosts never conflict.
Meant for launchd/cron every ~30 min. Prints one JSON status line.
Also copies this host's private curriculum overlay (<home>/coach/curriculum.yaml, if any) to
<repo-dir>/curriculum/<host>.yaml, so the miner on the coach Bot can check its adoption tests.
Also flushes due skill-outcomes records (plugins/skill-outcomes/outcomes.py flush --all-profiles) and copies this
host's <home>/logs/skill-outcomes.jsonl (+ profiles) to <repo-dir>/<host>/skill-outcomes.jsonl for the weekly rollup.
Also copies this host's self-heal detections (<home>/coach/selfheal.json, + profiles; ids and times only) to
<repo-dir>/<host>/selfheal-seen.json, so the hub's ask-gate does not re-file a turn another host's detector saw.
"""
import argparse, json, os, re, subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def git(repo, *args, check=True):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=check)


def unmerged_paths(repo):
    return sorted(set(git(repo, "diff", "--name-only", "--diff-filter=U", check=False).stdout.split()))


def resolve_unmerged(repo):
    """Clear a conflicted index left by a failed autostash pop / rebase.

    Append-only *.jsonl files resolve to the deduped union of base/ours/theirs lines. Anything else
    cannot be merged safely: returns it in `unresolved` (caller skips the run). Returns (resolved, unresolved).
    """
    resolved, unresolved = [], []
    for path in unmerged_paths(repo):
        if not path.endswith(".jsonl"):
            unresolved.append(path)
            continue
        seen, out = set(), []
        for stage in "123":
            r = git(repo, "show", f":{stage}:{path}", check=False)
            for line in (r.stdout.splitlines() if r.returncode == 0 else []):
                if line.strip() and line not in seen:
                    seen.add(line)
                    out.append(line)
        (Path(repo) / path).write_text("\n".join(out) + "\n")
        git(repo, "add", path)
        resolved.append(path)
    if resolved and not unresolved:
        # an autostash whose pop conflicted is kept in the stash list; its content is now merged in
        top = git(repo, "stash", "list", "-1", "--format=%gs", check=False).stdout.strip()
        if top.endswith("autostash"):
            git(repo, "stash", "drop", "-q", check=False)
    return resolved, unresolved


def forward_signals(host, home=None, runner=subprocess.run):
    """Self-heal machine-signal forwarder (no agent, no LLM): ship this host's new raw signals (tracebacks, failed
    crons, twice-failed cards, gateway crashes, coach handoff events) to the hub, which files them. Rides on this
    launchd job (every 30 min). A no-op unless <home>/coach/selfheal-config.json names a hub that is not this host;
    a failure never stops the digest feed, and the forwarder's cursor only moves when the hub accepted the batch."""
    # A skills-only (sparse) hermes-mods clone has no plugins/: fall back to the full clone.
    cands = [HERE.parents[3] / "plugins" / "coach" / "signals.py",
             Path.home() / "code" / "hermes-mods" / "plugins" / "coach" / "signals.py"]
    script = next((c for c in cands if c.exists()), None)
    if script is None:
        return "no signals.py"
    cmd = [sys.executable, str(script), "--host", host] + (["--home", home] if home else []) + ["forward"]
    try:
        r = runner(cmd, capture_output=True, text=True, timeout=150)
    except Exception as exc:
        return f"error: {type(exc).__name__}"
    out = (r.stdout or r.stderr or "").strip()[-200:]
    return out or "ok" if r.returncode == 0 else f"error: {out}"


def signals_state(out):
    """Collapse a forwarder result to a state that only changes when the forwarder's health does (so the status
    file is not rewritten, and committed, every 30 min just because the signal count moved)."""
    if out.startswith("error"):
        return "error"
    if "no hub configured" in out:
        return "no-hub"
    if "this host is the hub" in out:
        return "hub"
    if "forwarded" in out or "heartbeat ok" in out:
        return "ok"
    return "unknown"


def skill_outcomes_plugin():
    """plugins/skill-outcomes/outcomes.py in this hermes-mods tree (scripts -> hermes-coach -> productivity -> skills)."""
    return HERE.parents[3] / "plugins" / "skill-outcomes" / "outcomes.py"


def collect_skill_outcomes(home, repo, host, runner=subprocess.run, plugin=None):
    """Flush due records, then copy every home/profile skill-outcomes.jsonl into <repo>/<host>/skill-outcomes.jsonl
    (deduped union with what is there, so a pruned local log never drops rows already collected). Returns the
    row count written, or None when there is nothing to collect."""
    home = Path(home)
    plugin = Path(plugin or skill_outcomes_plugin())
    if plugin.exists():
        runner([sys.executable, str(plugin), "flush", "--home", str(home), "--all-profiles"],
               capture_output=True, text=True)
    srcs = [home / "logs" / "skill-outcomes.jsonl", *sorted(home.glob("profiles/*/logs/skill-outcomes.jsonl"))]
    dest = Path(repo) / host / "skill-outcomes.jsonl"
    seen, rows = set(), []
    for p in [dest, *srcs]:
        if not p.exists():
            continue
        for line in p.read_text(errors="replace").splitlines():
            line = line.strip()
            if line and line not in seen:
                seen.add(line)
                rows.append(line)
    if not rows:
        return None
    body = "\n".join(rows) + "\n"
    if not dest.exists() or dest.read_text(errors="replace") != body:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(body)
    return len(rows)


def collect_selfheal_seen(home, repo, host):
    """Copy this host's self-heal detections (home + profiles <coach>/selfheal.json, shadow or live) to
    <repo>/<host>/selfheal-seen.json = {fingerprint: {first, last, sessions}}, a union with what is there so a
    pruned local file never drops one. The hub's ask-gate reads every host's copy: detection state is per host, so a
    MacBook detection was invisible to it and the turn was re-filed (plugin-03d4509c8a). Only ids and times are
    copied, never the symptom text. Rewritten only when it changes. Returns the fingerprint count, or None."""
    home = Path(home)
    dest = Path(repo) / host / "selfheal-seen.json"
    srcs = [dest, home / "coach" / "selfheal.json", *sorted(home.glob("profiles/*/coach/selfheal.json"))]
    seen = {}
    for p in srcs:
        try:
            st = json.loads(p.read_text())
        except Exception:
            continue
        for fp, v in (st.items() if isinstance(st, dict) else ()):
            if not isinstance(v, dict) or not v.get("sessions"):
                continue
            first = v.get("first") or 0
            last = v.get("last") or first
            cur = seen.setdefault(fp, {"first": first, "last": last, "sessions": []})
            cur["first"], cur["last"] = min(cur["first"], first), max(cur["last"], last)
            cur["sessions"] += [s for s in v["sessions"] if isinstance(s, str) and s not in cur["sessions"]]
    if not seen:
        return None
    for v in seen.values():
        v["sessions"].sort()
    body = json.dumps(dict(sorted(seen.items())), indent=1) + "\n"
    if not dest.exists() or dest.read_text(errors="replace") != body:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(body)
    return len(seen)


LOCAL_STATE = ".digest-cursor*"  # session_digest.py's incremental cursor (+ its tmp file): per clone, never pushed


def exclude_local_state(repo):
    """Keep the digest cursor out of `git add -A <host>` via .git/info/exclude (local, nothing to commit)."""
    try:
        gd = git(repo, "rev-parse", "--git-dir", check=False).stdout.strip()
        if not gd:
            return
        p = Path(gd if os.path.isabs(gd) else Path(repo) / gd) / "info" / "exclude"
        cur = p.read_text() if p.exists() else ""
        if LOCAL_STATE not in cur.split("\n"):
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(cur + ("" if not cur or cur.endswith("\n") else "\n") + LOCAL_STATE + "\n")
    except OSError as e:
        print(f"coach_feed: warn: cannot exclude {LOCAL_STATE}: {e}", file=sys.stderr)


def write_feed_status(repo, host, out, now=None):
    """<repo>/<host>/feed-status.json = {signals: state, detail, since}: lets the hub see a Mac forwarder that never
    reached it (no config, ssh refused, crash) from the data repo, without a shell on that Mac. Rewritten only when
    the state or error text changes. Returns True if written."""
    p = Path(repo) / host / "feed-status.json"
    state = signals_state(out)
    detail = re.sub(r"\(\d+ pending\)", "(pending)", out) if state in ("error", "no-hub", "unknown") else ""
    try:
        old = json.loads(p.read_text())
    except Exception:
        old = {}
    if old.get("signals") == state and old.get("detail", "") == detail:
        return False
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"signals": state, "detail": detail,
                             "since": now or time.strftime("%Y-%m-%d %H:%M:%S %z")}, indent=1) + "\n")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", required=True)
    ap.add_argument("--repo-dir", default=os.path.expanduser("~/code/hermes-coach-data"))
    ap.add_argument("--home", default=None)
    ap.add_argument("--since-days", default="45")
    ap.add_argument("--no-push", action="store_true")
    ap.add_argument("--exclude-profile", action="append", default=[])
    a = ap.parse_args()
    repo = Path(a.repo_dir)
    status = {"host": a.host, "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
    # Per-phase wall time (cron-c89ed44e95: the feed crept from ~170 s to its 600 s kill with nothing showing it).
    # Each phase is also logged to stderr as it ends, so a killed run's partial stderr still says where it was.
    t0 = mark = time.monotonic()
    el = status["elapsed_s"] = {k: 0.0 for k in ("signals", "pull", "digest", "commit", "push")}

    def phase(name):
        nonlocal mark
        now = time.monotonic()
        el[name] = round(now - mark, 1)
        el["total"] = round(now - t0, 1)
        mark = now
        print(f"coach_feed: phase {name} {el[name]}s (total {el['total']}s)", file=sys.stderr, flush=True)

    el["total"] = 0.0
    status["signals"] = forward_signals(a.host, a.home)
    phase("signals")

    def heal(when):
        resolved, unresolved = resolve_unmerged(repo)
        if resolved:
            status["resolved_conflicts"] = status.get("resolved_conflicts", []) + resolved
            print(f"coach_feed: {when}: auto-resolved conflicted {resolved} in {repo} (jsonl union)", file=sys.stderr)
        if unresolved:
            status["skipped"] = f"unmerged paths need manual resolution: {unresolved}"
            print(f"coach_feed: ERROR {when}: unmerged paths in {repo} cannot be auto-resolved: {unresolved}; "
                  "skipping this run (git commit would fail with exit 128)", file=sys.stderr)
        return not unresolved

    if not heal("before pull"):
        print(json.dumps(status))
        return
    if not a.no_push:
        r = git(repo, "pull", "-q", "--rebase", "--autostash", check=False)
        if r.returncode:
            status["pull_error"] = r.stderr.strip()[-300:]
        if not heal("after pull"):  # autostash pop can leave a conflicted index
            print(json.dumps(status))
            return
    phase("pull")
    exclude_local_state(repo)
    cmd = [sys.executable, str(HERE / "session_digest.py"), "--host", a.host, "--out", str(repo),
           "--since-days", a.since_days]
    if a.home:
        cmd += ["--home", a.home]
    for p in a.exclude_profile:
        cmd += ["--exclude-profile", p]
    r = subprocess.run(cmd, capture_output=True, text=True)
    status["digest"] = json.loads(r.stdout.strip().splitlines()[-1]) if r.returncode == 0 and r.stdout.strip() else r.stderr[-300:]
    phase("digest")
    home = Path(a.home or os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes"))
    overlay, copy = home / "coach" / "curriculum.yaml", repo / "curriculum" / f"{a.host}.yaml"
    if overlay.exists() and (not copy.exists() or copy.read_bytes() != overlay.read_bytes()):
        copy.parent.mkdir(exist_ok=True)
        copy.write_bytes(overlay.read_bytes())
    try:
        status["skill_outcomes"] = collect_skill_outcomes(home, repo, a.host)
    except Exception as e:  # never fail the digest feed on this
        status["skill_outcomes"] = f"error: {type(e).__name__}: {e}"
    try:
        status["selfheal_seen"] = collect_selfheal_seen(home, repo, a.host)
    except Exception as e:  # never fail the digest feed on this
        status["selfheal_seen"] = f"error: {type(e).__name__}: {e}"
    if (repo / a.host).exists():
        write_feed_status(repo, a.host, status["signals"])
        git(repo, "add", "-A", a.host)
    if copy.exists():
        git(repo, "add", str(copy.relative_to(repo)))
    if git(repo, "diff", "--cached", "--quiet", check=False).returncode == 0:
        status["commit"] = None
        phase("commit")
    else:
        r = git(repo, "commit", "-q", "-m", f"{a.host}: digests {status['ts']}", check=False)
        if r.returncode:
            status["commit"] = None
            status["commit_error"] = (r.stderr or r.stdout).strip()[-300:]
            print(f"coach_feed: ERROR git commit failed in {repo}: {status['commit_error']}", file=sys.stderr)
            print(json.dumps(status))
            return
        status["commit"] = git(repo, "rev-parse", "--short", "HEAD").stdout.strip()
        phase("commit")
        if not a.no_push:
            r = git(repo, "push", "-q", check=False)
            if r.returncode:  # someone else pushed in between: rebase once and retry
                git(repo, "pull", "-q", "--rebase", check=False)
                r = git(repo, "push", "-q", check=False)
            status["pushed"] = r.returncode == 0
            if r.returncode:
                status["push_error"] = r.stderr.strip()[-300:]
            phase("push")
    print(json.dumps(status))


if __name__ == "__main__":
    main()
