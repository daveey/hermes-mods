"""Refuse direct `git push` and PR merges that lack the required exact-head CI check.

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
  unreadable all block, naming the sha, the check's state and ``use``. Every ``gh pr merge`` on that repo is
  held to the same rule for the PR's ``headRefOid``, with or without ``--admin``: admin credentials can bypass
  branch protection even in an ordinary merge when admin enforcement is disabled. ``allow`` is ignored for
  such guards: the check is the gate. Enable server-side admin enforcement too; this hook is defense in depth.
  A green head is not enough for ``gh pr merge`` when the base branch moved under it: if commits on the base branch
  since ``merge-base(head, base)`` change any file the PR changes, the merge is refused, naming those files and
  commits (rebase, then wait for the check on the new head; ``wt/*`` branches: ``hermes-core-wt ship <slug>``).
  Read through GitHub's compare API; an unreadable compare blocks too. Why: daveey/hermes PR #475 landed green but
  250 commits stale on 2026-11-02, onto two commits that changed its files, and main went red.
  The same two gates hold a REST merge, ``PUT repos/<owner>/<repo>/pulls/<n>/merge`` sent by ``gh api`` (``-X PUT``,
  ``-XPUT``, ``--method PUT``, ``--method=PUT``; the ``sha`` field is the head checked, else the PR's head), curl, or
  a Python HTTP client. A GraphQL ``mergePullRequest`` / ``enablePullRequestAutoMerge`` call on a guarded repo (or one
  whose PR node id cannot be resolved) is refused outright, and so is ``gh pr merge --auto`` onto a require_check
  branch: the gates run when the command is issued but GitHub merges later, after the branch may have moved.
  Land those with ``gh pr merge <n> --match-head-commit <sha>``.

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
from concurrent.futures import ThreadPoolExecutor
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
            if len(words) > 1 and re.match(r"^\$\{?[A-Za-z_][A-Za-z0-9_]*\}?$", words[0]):
                words, changed = words[1:], True  # `$B gh api ...`: B holds a wrapper (token-broker exec ... --)
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


def _gh() -> str:
    """gh by absolute path: PATH first, then the usual install dirs. Service processes (hermes-hub's gateway and its
    kanban workers, where the hook runs) have no ~/bin on PATH, and gh lives there: a bare "gh" fails, the broker
    retry execs the same bare name, every check reads as unreadable, and even a green sha is blocked."""
    dirs = (Path.home() / "bin", Path.home() / ".local/bin", Path("/opt/homebrew/bin"), Path("/usr/local/bin"),
            Path("/usr/bin"))
    return shutil.which("gh") or shutil.which("gh", path=os.pathsep.join(map(str, dirs))) or "gh"


def _gh_json(args: list[str], cwd: Optional[str] = None):
    """Run `gh <args>` and parse its JSON stdout: (data, None) or (None, error text). A gh with no login (hermes-hub,
    worker hosts) is retried through the token-broker `user/github` scope, the same token agents use there."""
    gh = _gh()
    attempts = [[gh, *args]]
    b = _broker()
    if b:
        attempts.append([b, "exec", "--scope", "user/github", "--reason", "push-guard: read CI state", "--",
                         gh, *args])
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


# --- gh pr merge ----------------------------------------------------------------------------------------------

GH_MERGE_VALUE_OPTS = {"-R", "--repo", "-b", "--body", "-F", "--body-file", "-t", "--subject",
                       "--match-head-commit", "-A", "--author-email"}


def _gh_merges(cmd: str, cwd: str):
    """Yield (cwd, selector or None, repo flag or None, command text) for every `gh pr merge`."""
    for d, words in _commands(cmd, cwd):
        if os.path.basename(words[0]) != "gh" or words[1:3] != ["pr", "merge"]:
            continue
        rest = words[3:]
        if "--disable-auto" in rest:
            continue  # cancels a queued merge; cannot land a commit
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


COMPARE_FILE_CAP = 300  # GitHub's compare lists at most this many files; a full list may be truncated
STALE_COMMIT_CAP = 1000  # main commits since the PR's base the guard will page through before calling it too stale
PATH_QUERY_CAP = 60  # per-path history reads on a truncated compare before calling it unverifiable


def _compare(slug: str, base: str, head: str, page: int = 0):
    q = f"?per_page=100&page={page}" if page else ""
    data, err = _gh_json(["api", f"repos/{slug}/compare/{base}...{head}{q}"])
    if err is not None or not isinstance(data, dict) or not (data.get("merge_base_commit") or {}).get("sha"):
        return None, err or "bad compare response"
    return data, None


def _names(data) -> list[str]:
    return [str(f.get("filename")) for f in (data or {}).get("files") or [] if isinstance(f, dict) and f.get("filename")]


def _pr_files(slug: str, number, listed: list[str]):
    """Every file the PR changes: the compare's list, or GitHub's paginated PR file list when that one is full."""
    if len(listed) < COMPARE_FILE_CAP or not number:
        return listed, None
    out: list[str] = []
    for page in range(1, 31):
        data, err = _gh_json(["api", f"repos/{slug}/pulls/{number}/files?per_page=100&page={page}"])
        if err is not None or not isinstance(data, list):
            return None, err or "bad pulls/files response"
        out += [str(f["filename"]) for f in data if isinstance(f, dict) and f.get("filename")]
        if len(data) < 100:
            return out, None
    return None, "the PR changes more than 3000 files"


def _path_commits(slug: str, tip: str, path: str, moved: set) -> Optional[list[str]]:
    """Commits of `moved` (base-branch commits since the PR's merge-base) that change `path`, newest first."""
    data, err = _gh_json(["api", f"repos/{slug}/commits?sha={tip}&path={urllib.parse.quote(path)}&per_page=100"])
    if err is not None or not isinstance(data, list):
        return None
    return [str(c["sha"]) for c in data if isinstance(c, dict) and c.get("sha") in moved]


def base_overlap(slug: str, base: str, head: str, number=None) -> dict:
    """What the base branch changed under a PR since the base its head was built (and CI-tested) on.

    Returns {"behind": n, "merge_base": sha, "tip": sha, "overlap": {path: [commit shas]}} or {"error": why}.
    `overlap` holds the PR's files that base-branch commits since merge-base(head, base) also change: the green
    check on the head never ran with those changes. Reads GitHub's compare API (no local clone needed). That API
    lists at most 300 files, so a full list is completed path by path from the base branch's history.
    """
    first, err = _compare(slug, base, head)
    if first is None:
        return {"error": err}
    mb, tip = str(first["merge_base_commit"]["sha"]), str((first.get("base_commit") or {}).get("sha") or base)
    behind = int(first.get("behind_by") or 0)
    out = {"behind": behind, "merge_base": mb, "tip": tip, "overlap": {}}
    if behind == 0:
        return out  # the head already contains the base tip: what CI tested is what lands
    if behind > STALE_COMMIT_CAP:
        return {**out, "error": f"the base branch is {behind} commits past the PR's base (cap {STALE_COMMIT_CAP})"}
    pr_files, err = _pr_files(slug, number, _names(first))
    if pr_files is None:
        return {**out, "error": f"the PR's file list could not be read ({err})"}
    moved_files: list[str] = []
    moved: set = set()
    # `behind` commits since the merge-base span ceil(behind/100) compare pages; read them concurrently (each read
    # is ~2 s on the live API and the whole hook has a 30 s budget). A further page is read only if the last one is
    # still full (the count moved while reading).
    page, last = 1, STALE_COMMIT_CAP // 100 + 1
    count = max(1, -(-behind // 100))
    while page <= last:
        pages = list(range(page, min(last, page + count - 1) + 1))
        full = False
        for n, (data, err) in zip(pages, _parallel(lambda k: _compare(slug, mb, tip, k), pages)):
            if data is None:
                return {**out, "error": f"the base branch since {mb[:12]} could not be read ({err})"}
            if n == 1:
                moved_files = _names(data)
            commits = [str(c.get("sha")) for c in data.get("commits") or [] if isinstance(c, dict) and c.get("sha")]
            moved.update(commits)
            full = len(commits) >= 100
        if not full:
            break
        page, count = pages[-1] + 1, 1
    hits = [p for p in pr_files if p in set(moved_files)]
    if len(moved_files) >= COMPARE_FILE_CAP and not hits:
        # truncated list with no overlap in view: ask each PR path's history which of those commits touch it
        if len(pr_files) > PATH_QUERY_CAP:
            return {**out, "error": (f"the base branch changed {COMPARE_FILE_CAP}+ files since {mb[:12]} and the PR "
                                     f"changes {len(pr_files)} (more than {PATH_QUERY_CAP} to check one by one)")}
        for p, got in zip(pr_files, _parallel(lambda q: _path_commits(slug, tip, q, moved), pr_files)):
            if got is None:
                return {**out, "error": f"the base branch history of {p} could not be read"}
            if got:
                out["overlap"][p] = got
        return out
    for p, got in zip(hits, _parallel(lambda q: _path_commits(slug, tip, q, moved), hits)):
        out["overlap"][p] = got or []
    return out


PARALLEL_READS = 8  # concurrent GitHub reads per overlap check


def _parallel(fn, items: list) -> list:
    """fn over items, PARALLEL_READS at a time, results in item order."""
    if len(items) <= 1:
        return [fn(i) for i in items]
    with ThreadPoolExecutor(max_workers=min(PARALLEL_READS, len(items))) as pool:
        return list(pool.map(fn, items))


def _overlap_block(g: dict, data: dict, what: str, number) -> Optional[str]:
    """Refuse the merge when the base branch changed the PR's files after the base its green check ran on."""
    head, branch = str(data["headRefOid"]), str(data.get("headRefName") or "")
    ov = base_overlap(g["remote"], g["branch"], head, number)
    pr = f"PR #{number}" if number else "the PR"
    if branch.startswith("wt/"):
        how = (f"`hermes-core-wt ship {branch[3:]}` (it rebases onto {g['branch']}, re-runs the gates and waits "
               f"for \"{g['require_check']}\" on the new sha)")
    else:
        how = (f"`git fetch origin && git rebase origin/{g['branch']}` on the PR branch, push it "
               f"(`git push --force-with-lease`), wait for \"{g['require_check']}\" on the new head sha, then merge")
    tail = f" Emergency only: prefix the command with `{OVERRIDE}` and say why in your report."
    if ov.get("error"):
        return (f"PUSH GUARD: blocked: {what}: {pr}'s head {head[:12]} could not be compared with {g['remote']} "
                f"{g['branch']} ({ov['error']}), so whether {g['branch']} changed its files since the base its "
                f"check ran on is unverified. Rebase onto {g['branch']} and wait for \"{g['require_check']}\" on the "
                f"new head: {how}.{tail}")
    if not ov["overlap"]:
        return None
    paths = sorted(ov["overlap"])
    named = "; ".join(f"{p} ({', '.join(c[:11] for c in ov['overlap'][p][:3]) or 'commit not resolved'})"
                      for p in paths[:8]) + (f"; +{len(paths) - 8} more" if len(paths) > 8 else "")
    return (f"PUSH GUARD: blocked: {what}: {pr}'s head {head[:12]} is green, but on base "
            f"{ov['merge_base'][:12]}. {g['remote']} {g['branch']} has moved {ov['behind']} commits since, and "
            f"some of them change files this PR changes: {named}. The green \"{g['require_check']}\" never ran with "
            f"those changes, so the landed tree would be untested. Rebase onto {g['branch']} and wait for the "
            f"check on the new head: {how}.{tail}")


def _auto_block(g: dict, text: str) -> str:
    """`gh pr merge --auto`: the gates run now, GitHub merges later. Counted before refusing it: 7 `gh pr merge
    --auto` calls on daveey/hermes in the 14 days to 2026-11-03 (hub: PRs 120, 180, 274, 278; MacBook: PR 329 x3),
    and PR 180's merged at once, before CI finished on its head."""
    return (f"PUSH GUARD: blocked: `{text[:100]}` asks GitHub to merge later (--auto) on {g['remote']} "
            f"{g['branch']}. This guard checks \"{g['require_check']}\" on the exact head and whether {g['branch']} "
            f"changed the PR's files when the command runs, but an auto-merge lands afterwards, when {g['branch']} "
            f"may have moved. Wait for the check on the head sha (github-ci-wait skill), then merge now: "
            f"`gh pr merge <n> -R {g['remote']} --match-head-commit <head sha>`. Emergency only: prefix the command "
            f"with `{OVERRIDE}` and say why in your report.")


def _gate_pr(mine: list[dict], slug: str, selector: Optional[str], what: str, cwd: Optional[str],
             sha: Optional[str] = None, auto=None) -> Optional[str]:
    """The merge gate for one PR of a require_check repo: exact-head check, then landing overlap.

    ``sha`` is the head the merge request pins (REST ``-f sha=``); without it the PR's current head is checked.
    ``auto`` (the command text) refuses a deferred `--auto` merge onto the guarded branch outright."""
    data, err = _gh_json(["pr", "view", *([selector] if selector else []), "--json",
                          "headRefOid,baseRefName,headRefName,number", "-R", slug], cwd=cwd)
    if err is not None or not isinstance(data, dict) or not data.get("headRefOid"):
        return _unverified(mine[0], what, f"the PR's head could not be read ({err or 'no headRefOid'})")
    if sha:
        data = {**data, "headRefOid": sha}  # GitHub merges only if the head is this sha: gate what will land
    for g in mine:
        if data.get("baseRefName") and data["baseRefName"] != g["branch"]:
            continue
        if auto:
            return _auto_block(g, auto)
        why = _check_block(g, str(data["headRefOid"]), what)
        if why:
            return why
        number = data.get("number") or (selector if selector and selector.isdigit() else None)
        why = _overlap_block(g, data, what, number)
        if why:
            return why
    return None


def check_pr_merge(cmd: str, cwd: str, guards: list[dict]) -> Optional[str]:
    checked = [g for g in guards if g["require_check"]]
    if not checked:
        return None
    for d, sel, repo, text in _gh_merges(cmd, cwd):
        slug = _merge_repo(d, sel, repo)
        mine = [g for g in checked if g["remote"] == slug]
        if not mine:
            continue
        kind = "admin merge" if "--admin" in text else "PR merge"
        what = f"`{text[:100]}` ({kind}; exact-head CI is required even for admin credentials)"
        selector = sel or _git(d, "symbolic-ref", "-q", "--short", "HEAD")
        why = _gate_pr(mine, slug, selector, what, d, auto="--auto" in text.split() and text)
        if why:
            return why
    for d, slug, number, sha, text in _api_merges(cmd, cwd):
        why = gate_rest_merge(slug, number, sha, text, d, checked)
        if why:
            return why
    return None


def gate_rest_merge(slug: str, number: str, sha: Optional[str], text: str, cwd: Optional[str],
                    checked: list[dict]) -> Optional[str]:
    """`PUT repos/<slug>/pulls/<number>/merge` (gh api, curl, urllib...): the same gate as `gh pr merge`."""
    mine = [g for g in checked if g["remote"] == slug]
    if not mine:
        return None
    what = (f"`{text[:100]}` (REST API merge of PR #{number}; exact-head CI is required even for admin "
            f"credentials; prefer `gh pr merge {number} -R {slug} --match-head-commit <sha>`)")
    return _gate_pr(mine, slug, number, what, cwd, sha)


# --- raw GitHub API merges (gh api REST / GraphQL, curl) --------------------------------------------------------

GH_API_VALUE_OPTS = {"-X", "--method", "-f", "--raw-field", "-F", "--field", "-H", "--header", "--input", "-q",
                     "--jq", "-t", "--template", "--hostname", "-p", "--preview", "--cache"}
REST_MERGE = re.compile(r"^(?:https?://[^/]+/(?:api/v3/)?)?/?repos/([^/\s]+)/([^/\s?]+)/pulls/(\d+)/merge/?(?:\?.*)?$")
GQL_MERGE = re.compile(r"\b(?:mergePullRequest|enablePullRequestAutoMerge)\b")
# a GraphQL call, not text that merely names the mutation (grep, a log search): `gh api [opts] graphql` in shell or a
# Python string, a Python argv list, or the GraphQL endpoint URL (curl, urllib, requests)
GQL_CALL = re.compile(r"""\bgh\s+api\b[^|;&\n]*?\sgraphql\b|["']gh["']\s*,\s*["']api["']\s*,(?:\s*["'][^"']*["']\s*,)*"""
                      r"""\s*["']graphql["']|https?://[^"'\s]+/graphql\b""")


def _gh_api_parse(words: list[str]):
    """(endpoint, method or None, {field: value}) for `gh api <words...>` (words after `api`)."""
    endpoint, method, fields, i = None, None, {}, 0
    while i < len(words):
        a = words[i]
        val = None
        if a in GH_API_VALUE_OPTS:
            val = words[i + 1] if i + 1 < len(words) else ""
            i += 2
        elif a.startswith("--") and "=" in a and a.split("=", 1)[0] in GH_API_VALUE_OPTS:
            a, val = a.split("=", 1)
            i += 1
        elif re.match(r"^-[XfFHqtp].", a):  # attached short form: -XPUT, -fsha=...
            a, val = a[:2], a[2:]
            i += 1
        else:
            if not a.startswith("-") and endpoint is None:
                endpoint = a
            i += 1
            continue
        if a in ("-X", "--method"):
            method = val.upper()
        elif a in ("-f", "--raw-field", "-F", "--field") and "=" in val:
            k, v = val.split("=", 1)
            fields[k] = v
    return endpoint, method, fields


def _origin_slug(cwd: str) -> str:
    return _slug(_git(cwd, "remote", "get-url", "origin") or "")


def _api_merges(cmd: str, cwd: str):
    """Yield (cwd, slug, number, sha or None, text) per REST merge: `gh api -X PUT repos/o/r/pulls/N/merge` and
    `curl -X PUT .../repos/o/r/pulls/N/merge`. `{owner}/{repo}` placeholders resolve to the cwd's origin as gh does."""
    for d, words in _commands(cmd, cwd):
        tool = os.path.basename(words[0])
        if tool == "gh" and words[1:2] == ["api"]:
            endpoint, method, fields = _gh_api_parse(words[2:])
            sha = fields.get("sha")
        elif tool == "curl":
            endpoint, method, sha = None, None, None
            for j, w in enumerate(words[1:], 1):
                if w in ("-X", "--request") and j + 1 < len(words):
                    method = words[j + 1].upper()
                elif re.match(r"^-X.", w) or w.startswith("--request="):
                    method = w.split("=", 1)[1].upper() if w.startswith("--") else w[2:].upper()
                elif REST_MERGE.match(w):
                    endpoint = w
            m = re.search(r'"sha"\s*:\s*"([0-9a-fA-F]{7,40})"', " ".join(words))
            sha = m.group(1) if m else None
        else:
            continue
        m = REST_MERGE.match(endpoint or "")
        if not m or method != "PUT":
            continue
        owner, name, number = m.groups()
        slug = _origin_slug(d) if (owner, name) == ("{owner}", "{repo}") else _slug(f"{owner}/{name}")
        yield d, slug, number, sha, " ".join(words)


def _gql_repo(text: str, guards: list[dict]) -> Optional[str]:
    """The repo a GraphQL merge mutation targets: resolve the PR node id through the API, else a slug or an
    owner/name pair named in the query or its fields. None when it cannot be told."""
    for node in dict.fromkeys(re.findall(r"\bPR_[A-Za-z0-9_-]{6,}", text)):
        q = 'query{node(id:"%s"){... on PullRequest{repository{nameWithOwner}}}}' % node
        data, err = _gh_json(["api", "graphql", "-f", f"query={q}"])
        try:
            return _slug(data["data"]["node"]["repository"]["nameWithOwner"])
        except (TypeError, KeyError):
            pass
    for g in guards:
        if re.search(r"(?<![\w.-])" + re.escape(g["remote"]) + r"(?![\w-])", text, re.I):
            return g["remote"]
    owner = re.search(r"\bowner\b\W{0,4}([\w.-]+)", text)
    name = re.search(r"\bname\b\W{0,4}([\w.-]+)", text)
    if owner and name:
        return _slug(f"{owner.group(1)}/{name.group(1)}")
    return None


def check_graphql_merge(text: str, guards: list[dict]) -> Optional[str]:
    """Refuse a GraphQL mergePullRequest / enablePullRequestAutoMerge call (``text`` is the call itself) on a guarded
    repo, or one that cannot be told: the mutation lands without the exact-head and landing-overlap gates
    `gh pr merge` gets here."""
    if not guards or not GQL_MERGE.search(text):
        return None
    slug = _gql_repo(text, guards)
    mine = [g for g in guards if g["remote"] == slug] if slug else guards
    if not mine:
        return None
    g, hit = mine[0], GQL_MERGE.search(text)
    which = hit.group(0) if hit else "mergePullRequest"
    target = f"{g['remote']} {g['branch']}" if slug else "a repo this guard cannot identify (no readable PR node id)"
    return (f"PUSH GUARD: blocked: a GraphQL `{which}` merges a PR on {target} without the exact-head CI and "
            f"landing-overlap checks every merge on a guarded repo gets. Merge with `gh pr merge <n> -R <owner/repo> "
            f"--match-head-commit <head sha>` instead (after the required check is green on that sha). Emergency "
            f"only: prefix the command with `{OVERRIDE}` and say why in your report.")


# --- entry points --------------------------------------------------------------------------------------------

def check_shell(cmd: str, cwd: Optional[str] = None, guards: Optional[list[dict]] = None) -> Optional[str]:
    guards = load_guards() if guards is None else guards
    cmd = cmd or ""
    if not guards or OVERRIDE in cmd or ("push" not in cmd and "merge" not in cmd.lower()):
        return None
    why = None
    if GQL_MERGE.search(cmd) and GQL_CALL.search(cmd):
        # the query may span lines, quotes and `|`: read the whole command once it calls GraphQL
        why = check_graphql_merge(cmd, guards)
        if why:
            return why
    cwd = _resolve(cwd or os.getcwd())
    why = check_pr_merge(cmd, cwd, guards)
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


PY_REST_URL = re.compile(r"""["'](https?://[^"'\s]+/repos/[^/"'\s]+/[^/"'\s]+/pulls/\d+/merge/?)["']""")


def _python_rest_merges(code: str, cwd: Optional[str], guards: list[dict]) -> Optional[str]:
    """urllib/requests/httpx PUTs to `https://api.github.com/repos/<o>/<r>/pulls/<n>/merge` inside Python."""
    checked = [g for g in guards if g["require_check"]]
    if not checked or not re.search(r"""["']PUT["']|\.put\(""", code, re.I):
        return None
    sha = re.search(r"""["']sha["']\s*:\s*["']([0-9a-fA-F]{7,40})["']""", code)
    for url in PY_REST_URL.findall(code):
        m = REST_MERGE.match(url)
        if not m:
            continue
        owner, name, number = m.groups()
        why = gate_rest_merge(_slug(f"{owner}/{name}"), number, sha.group(1) if sha else None,
                              f"PUT {url}", _resolve(cwd or os.getcwd()), checked)
        if why:
            return why
    return None


def check_python(code: str, cwd: Optional[str] = None, guards: Optional[list[dict]] = None) -> Optional[str]:
    """Shell strings inside Python (subprocess.run("...", shell=True), terminal("...")) and argv lists."""
    guards = load_guards() if guards is None else guards
    code = code or ""
    if not guards or ("push" not in code and "merge" not in code.lower()):
        return None
    if OVERRIDE not in code:
        if GQL_MERGE.search(code) and GQL_CALL.search(code):
            # the mutation spans quotes and lines: read the whole code once it calls GraphQL
            why = check_graphql_merge(code, guards)
            if why:
                return why
        why = _python_rest_merges(code, cwd, guards)
        if why:
            return why
    for lit in PY_LITERAL.findall(code):
        why = check_shell(lit[0] or lit[1], cwd, guards)
        if why:
            return why
    for m in re.finditer(r"\[\s*[\"'](?:git|gh)[\"'](?:\s*,\s*[\"'][^\"']*[\"'])+\s*\]", code):
        words = re.findall(r"[\"']([^\"']*)[\"']", m.group(0))
        if "push" in words or any(w == "merge" or REST_MERGE.match(w) for w in words):
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
