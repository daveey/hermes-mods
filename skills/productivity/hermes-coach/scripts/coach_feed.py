#!/usr/bin/env python3
"""Refresh this host's session digests in a git checkout and push them (stdlib only).

    coach_feed.py --host NAME [--repo-dir ~/code/hermes-coach-data] [--home ~/.hermes] [--no-push]

Runs session_digest.py into <repo-dir>, then commits and pushes only when a digest changed.
Pull uses rebase + autostash; each host writes only its own <host>/ subtree, so hosts never conflict.
Meant for launchd/cron every ~30 min. Prints one JSON status line.
"""
import argparse, json, os, subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def git(repo, *args, check=True):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=check)


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
    if not a.no_push:
        r = git(repo, "pull", "-q", "--rebase", "--autostash", check=False)
        if r.returncode:
            status["pull_error"] = r.stderr.strip()[-300:]
    cmd = [sys.executable, str(HERE / "session_digest.py"), "--host", a.host, "--out", str(repo),
           "--since-days", a.since_days]
    if a.home:
        cmd += ["--home", a.home]
    for p in a.exclude_profile:
        cmd += ["--exclude-profile", p]
    r = subprocess.run(cmd, capture_output=True, text=True)
    status["digest"] = json.loads(r.stdout.strip().splitlines()[-1]) if r.returncode == 0 and r.stdout.strip() else r.stderr[-300:]
    git(repo, "add", "-A", a.host)
    if git(repo, "diff", "--cached", "--quiet", check=False).returncode == 0:
        status["commit"] = None
    else:
        git(repo, "commit", "-q", "-m", f"{a.host}: digests {status['ts']}")
        status["commit"] = git(repo, "rev-parse", "--short", "HEAD").stdout.strip()
        if not a.no_push:
            r = git(repo, "push", "-q", check=False)
            if r.returncode:  # someone else pushed in between: rebase once and retry
                git(repo, "pull", "-q", "--rebase", check=False)
                r = git(repo, "push", "-q", check=False)
            status["pushed"] = r.returncode == 0
            if r.returncode:
                status["push_error"] = r.stderr.strip()[-300:]
    print(json.dumps(status))


if __name__ == "__main__":
    main()
