"""Refuse direct `git push` of code to a guarded branch, and admin merges that skip its required CI check.

Config ``$HERMES_HOME/push-guards.json`` (no file = no-op)::

    {"guards": [{"remote": "owner/repo", "branch": "main", "allow": ["docs/*", "*.md"],
                 "use": "my-land-tool <branch> --wait"},
                {"remote": "owner/other", "branch": "main", "require_check": "All required checks pass",
                 "use": "open a PR and merge it once the check is green"}]}

A guard applies to a push whose destination is ``branch`` on a remote whose URL names ``remote`` (``owner/repo``,
matched against the URL's last two path parts, so ``owner/repo`` does not also guard ``owner/repo-mods``; a ``.git``
suffix is accepted: builds before exact matching compared substrings, so ``owner/repo.git`` keeps them off ``-mods``).

* Without ``require_check`` (merge-train repos): if every file the push changes against ``<remote>/<branch>``
  matches an ``allow`` glob (fnmatch; ``*`` crosses ``/``) it goes through, otherwise it is blocked with a pointer
  to ``use``.
* With ``require_check`` (a check-run name): the push goes through only if the exact commit being pushed has a
  check-run of that name whose newest run concluded ``success``
  (``gh api repos/<owner>/<repo>/commits/<sha>/check-runs``). In progress, queued, failed, cancelled, missing or
  unreadable all block, naming the sha, the check's state and ``use``. ``gh pr merge ... --admin`` on that repo is
  held to the same rule for the PR's ``headRefOid`` (a plain ``gh pr merge`` is left to GitHub's own branch
  protection). ``allow`` is ignored for such guards: the check is the gate.

``PUSH_GUARD=off`` anywhere in the command skips the check (emergencies; say why). Pushes made inside other tools
(hermes-core-wt, hermes-sync, hermes update) are not agent tool calls and are never seen here.
Shared by the Hermes pre_tool_call hook (__init__.py).
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import shlex
import shutil
import subprocess
import urllib.parse
from pathlib import Path
from typing import Optional

SEPARATORS = re.compile(r"&&|\|\||;|\n|\|")
OVERRIDE = "PUSH_GUARD=off"
PREFIXES = ("sudo", "env", "command", "exec", "time", "nice", "timeout")
GH_TIMEOUT = 25


def hermes_home() -> Path:
    return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")


def load_guards() -> list[dict]:
    f = Path(os.environ.get("PUSH_GUARD_CONFIG") or hermes_home() / "push-guards.json")
    try:
        raw = json.loads(f.read_text())
    except (OSError, ValueError):
        return []
    out = []
    for g in (raw.get("guards") if isinstance(raw, dict) else None) or []:
        if isinstance(g, dict) and g.get("remote") and g.get("branch"):
            req = str(g.get("require_check") or "").strip()
            default_use = ("open a PR, wait for the check on its head, then `gh pr merge <n>` without --admin"
                           if req else "the repo's merge queue")
            out.append({"remote": _slug(str(g["remote"])), "branch": str(g["branch"]),
                        "allow": list(g.get("allow") or []), "require_check": req,
                        "use": str(g.get("use") or default_use)})
    return out


def _resolve(p: str, base: Optional[str] = None) -> str:
    p = os.path.expanduser(os.path.expandvars(p))
    if base and not os.path.isabs(p):
        p = os.path.join(base, p)
    return os.path.realpath(p)


def _git(repo: str, *args: str) -> Optional[str]:
    try:
        r = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def _slug(url: str) -> str:
    """'owner/repo' (lowercase) from a git/GitHub URL, PR URL or path: its last two path parts, no .git."""
    u = (url or "").strip().rstrip("/")
    u = re.sub(r"/pull/\d+.*$", "", u)
    u = re.sub(r"\.git$", "", u)
    u = re.sub(r"^[a-z+]+://[^/]*/", "", u)  # scheme://host/
    u = re.sub(r"^[^/:@]+@[^:/]+:", "", u)   # git@host:
    parts = [p for p in re.split(r"[/:]", u) if p]
    return "/".join(parts[-2:]).lower()


def _remote_matches(url: str, remote: str) -> bool:
    return bool(url) and _slug(url) == _slug(remote)


def _commands(cmd: str, cwd: str):
    """Yield (cwd, words) per simple command, following `cd` and stripping env/sudo/token-broker wrappers."""
    for seg in SEPARATORS.split(cmd or ""):
        try:
            words = shlex.split(seg, comments=True)
        except ValueError:
            words = seg.split()
        changed = True
        while changed and words:
            changed = False
            while words and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[0]):
                words, changed = words[1:], True
            if words and words[0] in PREFIXES:
                words, changed = words[1:], True
                while words and re.match(r"^(-\S+|\d+[smh]?)$", words[0]):
                    words = words[1:]
            if words and os.path.basename(words[0]) == "token-broker" and "--" in words:
                words, changed = words[words.index("--") + 1:], True
        if not words:
            continue
        if words[0] in ("cd", "pushd") and len(words) > 1:
            cwd = _resolve(words[1], cwd)
            continue
        yield cwd, words


def _git_calls(cmd: str, cwd: str):
    """Yield (repo dir, subcommand, args) for each git invocation, following `cd` and `git -C`."""
    for cwd, words in _commands(cmd, cwd):
        if os.path.basename(words[0]) != "git":
            continue
        repo, i = cwd, 1
        while i < len(words) and words[i].startswith("-"):
            if words[i] == "-C" and i + 1 < len(words):
                repo = _resolve(words[i + 1], repo)
                i += 2
            elif words[i] in ("-c", "--git-dir", "--work-tree") and i + 1 < len(words):
                i += 2
            else:
                i += 1
        if i < len(words):
            yield repo, words[i], words[i + 1:]


VALUE_OPTS = {"-o", "--push-option", "--repo", "--receive-pack", "--exec"}


def _push_targets(repo: str, args: list[str]):
    """(remote, [(src, dst branch)]) for `git push <args>`."""
    pos, i = [], 0
    while i < len(args):
        a = args[i]
        if a in VALUE_OPTS:
            i += 2
            continue
        if not a.startswith("-"):
            pos.append(a)
        i += 1
    cur = _git(repo, "symbolic-ref", "-q", "--short", "HEAD") or "HEAD"
    remote = pos[0] if pos else (_git(repo, "config", f"branch.{cur}.remote") or "origin")
    specs = pos[1:]
    if not specs:  # bare push: the current branch to its upstream (push.default simple/upstream)
        up = _git(repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}") or ""
        return remote, [("HEAD", up.split("/", 1)[1] if "/" in up else cur)]
    out = []
    for s in specs:
        s = s.lstrip("+")
        src, _, dst = s.partition(":")
        if not _:
            dst = cur if src == "HEAD" else src
        dst = re.sub(r"^refs/heads/", "", dst)
        out.append((src, dst))
    return remote, out


def _allowed(files: list[str], globs: list[str]) -> bool:
    return bool(files) and all(any(fnmatch.fnmatch(f, g) for g in globs) for f in files)


# --- GitHub reads -------------------------------------------------------------------------------------------

def _broker() -> Optional[str]:
    b = shutil.which("token-broker")
    if b:
        return b
    p = Path.home() / ".local/bin/token-broker"
    return str(p) if p.exists() else None


def _gh_json(args: list[str], cwd: Optional[str] = None):
    """Run `gh <args>` and parse its JSON stdout: (data, None) or (None, error text). A gh with no login (hermes-hub,
    worker hosts) is retried through the token-broker `user/github` scope, the same token agents use there."""
    attempts = [["gh", *args]]
    b = _broker()
    if b:
        attempts.append([b, "exec", "--scope", "user/github", "--reason", "push-guard: read CI state", "--",
                         "gh", *args])
    err = "gh not available"
    for argv in attempts:
        try:
            r = subprocess.run(argv, capture_output=True, text=True, timeout=GH_TIMEOUT, cwd=cwd or None)
        except (OSError, subprocess.SubprocessError) as exc:
            err = f"{type(exc).__name__}: {exc}"
            continue
        if r.returncode == 0:
            try:
                return json.loads(r.stdout or "null"), None
            except ValueError:
                err = "unparseable gh output"
                continue
        tail = (r.stderr or r.stdout or "").strip().splitlines()
        err = tail[-1][:160] if tail else f"gh exit {r.returncode}"
    return None, err


def check_state(repo: str, sha: str, name: str) -> str:
    """State of check-run ``name`` on the exact ``sha``: 'success', 'in_progress', 'queued', 'failure',
    'cancelled', ..., 'missing' (no run of that name) or 'unreadable (...)'. The newest run of that name decides."""
    q = urllib.parse.quote(name)
    data, err = _gh_json(["api", f"repos/{repo}/commits/{sha}/check-runs?check_name={q}&per_page=100"])
    if err is not None or not isinstance(data, dict):
        return f"unreadable ({err or 'bad response'})"
    runs = [r for r in data.get("check_runs") or [] if isinstance(r, dict) and r.get("name") == name]
    if not runs:
        return "missing" + _missing_detail(repo, sha)
    newest = max(runs, key=lambda r: (str(r.get("started_at") or ""), int(r.get("id") or 0)))
    if newest.get("status") != "completed":
        return str(newest.get("status") or "unknown")
    return str(newest.get("conclusion") or "unknown")


def _missing_detail(repo: str, sha: str) -> str:
    """Why a gate check-run is absent: an aggregate job that `needs` other jobs gets no check-run until they finish."""
    data, err = _gh_json(["api", f"repos/{repo}/commits/{sha}/check-runs?per_page=100"])
    if err is not None or not isinstance(data, dict):
        return ""
    runs = [r for r in data.get("check_runs") or [] if isinstance(r, dict)]
    if not runs:
        return " (no check-runs at all on this sha: CI never ran on it)"
    pending = sum(1 for r in runs if r.get("status") != "completed")
    if pending:
        return f" (CI still running: {pending} of {len(runs)} check-runs on this sha are not complete)"
    return f" (all {len(runs)} other check-runs on this sha are complete; the gate job never ran, e.g. superseded)"


def _check_block(g: dict, sha: str, what: str) -> Optional[str]:
    state = check_state(g["remote"], sha, g["require_check"])
    if state == "success":
        return None
    return (f"PUSH GUARD: blocked: {what} lands {sha[:12]} on {g['remote']} {g['branch']}, but its required check "
            f"\"{g['require_check']}\" is {state} on that exact sha (it must be success). Land it this way: "
            f"{g['use']}. Emergency only: prefix the command with `{OVERRIDE}` and say why in your report.")


def _unverified(g: dict, what: str, reason: str) -> str:
    return (f"PUSH GUARD: blocked: {what} lands on {g['remote']} {g['branch']}, but {reason}, so its required check "
            f"\"{g['require_check']}\" is unverified. Land it this way: {g['use']}. Emergency only: prefix the "
            f"command with `{OVERRIDE}` and say why in your report.")


# --- gh pr merge --admin --------------------------------------------------------------------------------------

GH_MERGE_VALUE_OPTS = {"-R", "--repo", "-b", "--body", "-F", "--body-file", "-t", "--subject",
                       "--match-head-commit", "-A", "--author-email"}


def _gh_admin_merges(cmd: str, cwd: str):
    """Yield (cwd, selector or None, repo flag or None, command text) for each `gh pr merge ... --admin`."""
    for d, words in _commands(cmd, cwd):
        if os.path.basename(words[0]) != "gh" or words[1:3] != ["pr", "merge"]:
            continue
        rest = words[3:]
        if "--admin" not in rest:
            continue
        sel, repo, i = None, None, 0
        while i < len(rest):
            a = rest[i]
            if a in GH_MERGE_VALUE_OPTS:
                if a in ("-R", "--repo") and i + 1 < len(rest):
                    repo = rest[i + 1]
                i += 2
                continue
            if a.startswith("--repo="):
                repo = a.split("=", 1)[1]
            elif not a.startswith("-") and sel is None:
                sel = a
            i += 1
        yield d, sel, repo, " ".join(words)


def _merge_repo(cwd: str, sel: Optional[str], repo: Optional[str]) -> str:
    if repo:
        return _slug(repo)
    if sel and "/pull/" in sel:
        return _slug(sel)
    return _slug(_git(cwd, "remote", "get-url", "origin") or "")


def check_admin_merge(cmd: str, cwd: str, guards: list[dict]) -> Optional[str]:
    checked = [g for g in guards if g["require_check"]]
    if not checked or "--admin" not in cmd:
        return None
    for d, sel, repo, text in _gh_admin_merges(cmd, cwd):
        slug = _merge_repo(d, sel, repo)
        mine = [g for g in checked if g["remote"] == slug]
        if not mine:
            continue
        what = f"`{text[:100]}` (an admin merge, which skips branch protection)"
        data, err = _gh_json(["pr", "view", *([sel] if sel else []), "--json", "headRefOid,baseRefName",
                              "-R", slug], cwd=d)
        if err is not None or not isinstance(data, dict) or not data.get("headRefOid"):
            return _unverified(mine[0], what, f"the PR's head could not be read ({err or 'no headRefOid'})")
        for g in mine:
            if data.get("baseRefName") and data["baseRefName"] != g["branch"]:
                continue
            why = _check_block(g, str(data["headRefOid"]), what)
            if why:
                return why
    return None


# --- entry points --------------------------------------------------------------------------------------------

def check_shell(cmd: str, cwd: Optional[str] = None, guards: Optional[list[dict]] = None) -> Optional[str]:
    guards = load_guards() if guards is None else guards
    cmd = cmd or ""
    if not guards or OVERRIDE in cmd or ("push" not in cmd and "merge" not in cmd):
        return None
    cwd = _resolve(cwd or os.getcwd())
    why = check_admin_merge(cmd, cwd, guards)
    if why or "push" not in cmd:
        return why
    for repo, sub, args in _git_calls(cmd, cwd):
        if sub != "push":
            continue
        remote, targets = _push_targets(repo, args)
        url = _git(repo, "remote", "get-url", remote) or remote
        what = f"`git push {' '.join(args)[:80]}`"
        for g in guards:
            if not _remote_matches(url, g["remote"]):
                continue
            for src, dst in targets:
                if dst != g["branch"]:
                    continue
                if not src:
                    return f"`git push {remote} :{dst}` deletes {g['remote']} {dst}"
                if g["require_check"]:
                    sha = _git(repo, "rev-parse", "--verify", "--quiet", f"{src}^{{commit}}")
                    if not sha:
                        return _unverified(g, what, f"`{src}` does not resolve to a commit here")
                    if sha == _git(repo, "rev-parse", "--verify", "--quiet", f"{remote}/{dst}^{{commit}}"):
                        continue  # nothing new to push
                    why = _check_block(g, sha, what)
                    if why:
                        return why
                    continue
                diff = _git(repo, "diff", "--name-only", f"{remote}/{dst}...{src}")
                files = [f for f in (diff or "").splitlines() if f]
                if diff is not None and not files:
                    continue  # nothing new to push
                if diff is not None and _allowed(files, g["allow"]):
                    continue
                listed = ", ".join(files[:5]) + (f" (+{len(files) - 5} more)" if len(files) > 5 else "") \
                    if files else "files it could not list"
                return (f"{what} pushes code straight to {g['remote']} {dst} ({listed})||"
                        + g["use"] + "||" + ", ".join(g["allow"]))
    return None


PY_LITERAL = re.compile(r"""(?:"([^"\n]*\b(?:git\b[^"\n]*\bpush|gh\b[^"\n]*\bmerge)\b[^"\n]*)"|"""
                        r"""'([^'\n]*\b(?:git\b[^'\n]*\bpush|gh\b[^'\n]*\bmerge)\b[^'\n]*)')""")


def check_python(code: str, cwd: Optional[str] = None, guards: Optional[list[dict]] = None) -> Optional[str]:
    """Shell strings inside Python (subprocess.run("...", shell=True), terminal("...")) and argv lists."""
    guards = load_guards() if guards is None else guards
    code = code or ""
    if not guards or ("push" not in code and "merge" not in code):
        return None
    for lit in PY_LITERAL.findall(code):
        why = check_shell(lit[0] or lit[1], cwd, guards)
        if why:
            return why
    for m in re.finditer(r"\[\s*[\"'](?:git|gh)[\"'](?:\s*,\s*[\"'][^\"']*[\"'])+\s*\]", code):
        words = re.findall(r"[\"']([^\"']*)[\"']", m.group(0))
        if "push" in words or "merge" in words:
            why = check_shell(shlex.join(words), cwd, guards)
            if why:
                return why
    return None


def message(why: str) -> str:
    if why.startswith("PUSH GUARD:"):
        return why
    what, use, allow = (why.split("||") + ["", ""])[:3]
    return (f"PUSH GUARD: blocked: {what}. This branch takes code only through its merge train: run `{use}` instead "
            f"(it pushes your branch, opens or reuses the PR and lands it in the express lane). Pushes that change "
            f"only {allow or 'allowed paths'} may still go direct. Emergency only: prefix the command with "
            f"`{OVERRIDE}` and say why in your report.")
