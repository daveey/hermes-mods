"""Open-PR file overlap: is a shared file about to change already owned by an open pull request?

A chat edit to a shared skill/guide that an open PR also changes forces that PR to reconcile and earn another
exact-head review. Two callers use this one implementation:

* the ``pre_tool_call`` hook (``__init__.py``) refuses a ``skill_manage`` mutation whose target file is in an open
  PR's file list, naming the PR so the writer sends the note to its owner instead;
* the skills publisher (state ``hosts/hermes-hub/skills-sync/skills_sync.sh``) runs ``pr_overlap.py check`` before
  committing and before pushing, to hold edits made before the PR opened.

Results are ``clear``, ``overlap`` (with the matching PRs) or ``unknown``. A failed, rate-limited, over-budget or
incompletely paginated lookup is ``unknown``, never ``clear``: the publisher holds on it and retries next pass. Reads
are bounded (request count, page caps, wall-clock deadline) and cached: the open-PR list for ``LIST_TTL`` seconds,
each PR's files by its head sha. Stdlib only; the repository and base branch come from the caller.

Transports. The in-process hook reads through the host's ``gh`` (``gh_fetch``: one ``gh api --include`` per request),
never with a raw token over urllib: on Board workers ``gh`` is worker-gh and on the Macs gh-shim, which vend per call
and record every REST request in the github_quota ledger. A urllib call from the agent process carried the inherited
user token unmetered (gh-conntrace ``hermes-hook-_pr`` rows, card t_01a1d3e6f40a8e39). The publisher CLI keeps
``http_fetch`` with the token its caller hands it (the hub passes an App installation token, its own bucket).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Iterable, Optional

API = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
LIST_TTL = float(os.environ.get("PR_OVERLAP_LIST_TTL", "120"))
MAX_LIST_PAGES = 3          # 300 open PRs against one base
MAX_FILE_PAGES = 30         # GitHub's own cap: 3000 files
MAX_REQUESTS = 60
REQUEST_TIMEOUT = 5.0
CACHE_DIR = Path(os.environ.get("PR_OVERLAP_CACHE", "~/.hermes/cache/pr-overlap")).expanduser()

EXIT_CLEAR, EXIT_OVERLAP, EXIT_UNKNOWN = 0, 10, 11


class Unknown(Exception):
    """The open-PR file set could not be established completely."""


def repo_slug(url: str) -> Optional[str]:
    """owner/repo for a github.com remote URL (https, ssh or scp form), else None."""
    m = re.match(r"^(?:https?://(?:[^@/]+@)?github\.com/|ssh://git@github\.com/|git@github\.com:)"
                 r"([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$", (url or "").strip())
    return f"{m.group(1)}/{m.group(2)}" if m else None


def _next_link(header: str) -> Optional[str]:
    for part in (header or "").split(","):
        m = re.match(r'\s*<([^>]+)>\s*;\s*rel="next"', part)
        if m:
            return m.group(1)
    return None


def http_fetch(url: str, token: str, timeout: float):
    """(decoded JSON, next-page URL or None). Raises Unknown on any failure."""
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                               "X-GitHub-Api-Version": "2022-11-28",
                                               "User-Agent": "hermes-pr-overlap"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode()), _next_link(resp.headers.get("Link", ""))
    except urllib.error.HTTPError as exc:
        raise Unknown(f"GitHub HTTP {exc.code} for {url.split('?')[0]}") from None
    except Exception as exc:  # timeout, DNS, bad JSON
        raise Unknown(f"GitHub lookup failed ({type(exc).__name__})") from None


def _split_response(raw: bytes):
    """(status or None, {lower-case header: value}, body) of ``gh api --include`` output."""
    text = raw.decode("utf-8", "replace")
    m = re.match(r"\s*HTTP/\S+\s+(\d{3})\b", text)
    if not m:
        return None, {}, text
    sep = re.search(r"\r?\n\r?\n", text)
    head, body = (text[:sep.start()], text[sep.end():]) if sep else (text, "")
    headers = {}
    for line in head.splitlines()[1:]:
        name, colon, value = line.partition(":")
        if colon:
            headers[name.strip().lower()] = value.strip()
    return int(m.group(1)), headers, body


def gh_fetch(url: str, token: str, timeout: float):
    """Metered transport: one ``gh api --include`` GET per request. (decoded JSON, next-page URL or None).

    ``token`` is ignored: gh authenticates itself (worker-gh/gh-shim vend per call and write the quota ledger row;
    a plain gh uses its own login). Raises Unknown on any failure: gh missing, timeout, non-zero exit (75 = the
    quota guard refused the call at its reserve), HTTP >= 400 or a body that is not JSON."""
    gh = shutil.which(os.environ.get("PR_OVERLAP_GH") or "gh")
    if not gh:
        raise Unknown("gh CLI not found (pr_overlap reads GitHub only through the metered gh)")
    path = url[len(API) + 1:] if url.startswith(API + "/") else url
    env = dict(os.environ, GH_PROMPT_DISABLED="1", GH_PAGER="", NO_COLOR="1", GH_NO_UPDATE_NOTIFIER="1")
    cmd = [gh, "api", "--include", "-H", "Accept: application/vnd.github+json",
           "-H", "X-GitHub-Api-Version: 2022-11-28", path]
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env=env)
    except OSError as exc:
        raise Unknown(f"gh could not start ({type(exc).__name__})") from None
    try:
        out, _err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.terminate()   # SIGTERM lets a quota supervisor stop its own child group
        try:
            proc.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
        raise Unknown("GitHub lookup failed (timeout)") from None
    status, headers, body = _split_response(out)
    where = path.split("?")[0]
    if status is not None and status >= 400:
        raise Unknown(f"GitHub HTTP {status} for {where}")
    if proc.returncode != 0:
        why = " (quota guard refused)" if proc.returncode == 75 else ""
        raise Unknown(f"gh api exit {proc.returncode}{why} for {where}")
    if status is None:
        raise Unknown("unexpected gh api output")
    try:
        data = json.loads(body)
    except ValueError:
        raise Unknown("GitHub lookup failed (bad JSON)") from None
    return data, _next_link(headers.get("link", ""))


def default_token() -> str:
    tok = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
    if tok:
        return tok
    try:
        r = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=3)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


FILE_WORKERS = 4   # concurrent per-PR file reads: a metered gh call costs ~1.5 s of vend + supervisor start


class Checker:
    def __init__(self, fetch: Callable = http_fetch, token: Optional[str] = None, cache_dir: Path = CACHE_DIR,
                 deadline: float = 20.0, clock: Callable[[], float] = time.monotonic,
                 wall: Callable[[], float] = time.time, list_ttl: float = LIST_TTL, workers: int = FILE_WORKERS):
        self.fetch, self.cache_dir, self.clock, self.wall, self.list_ttl = fetch, Path(cache_dir), clock, wall, list_ttl
        # gh_fetch authenticates through gh itself: never resolve (or hand it) a raw token.
        self._token = "" if fetch is gh_fetch and token is None else token
        self.deadline = clock() + deadline
        self.requests = 0
        self.workers = max(1, workers)
        self._lock = threading.Lock()
        self._failed: Optional[str] = None

    @property
    def token(self) -> str:
        if self._token is None:
            self._token = default_token()
        return self._token

    def _get(self, url: str):
        with self._lock:
            if self._failed:
                raise Unknown(self._failed)   # one failure ends the lookup: no further requests
            left = self.deadline - self.clock()
            if left <= 0:
                raise Unknown("lookup deadline exceeded")
            if self.requests >= MAX_REQUESTS:
                raise Unknown("lookup request budget exceeded")
            self.requests += 1
        try:
            return self.fetch(url, self.token, min(REQUEST_TIMEOUT, left))
        except Exception as exc:  # any fetch failure is still not "clear"
            reason = str(exc) or type(exc).__name__
            with self._lock:
                self._failed = self._failed or reason
            raise Unknown(reason) from None

    def _pages(self, url: str, cap: int) -> list:
        items, page = [], 0
        while url:
            if page >= cap:
                raise Unknown(f"incomplete pagination (more than {cap} pages)")
            data, url = self._get(url)
            if not isinstance(data, list):
                raise Unknown("unexpected GitHub response")
            items.extend(data)
            page += 1
        return items

    # cache: {"lists": {base: {"at": t, "prs": [...]}}, "files": {"<n>@<sha>": [...]}}
    def _cache_path(self, slug: str) -> Path:
        return self.cache_dir / (slug.replace("/", "__") + ".json")

    def _load(self, slug: str) -> dict:
        try:
            data = json.loads(self._cache_path(slug).read_text())
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save(self, slug: str, data: dict) -> None:
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            path = self._cache_path(slug)
            tmp = path.with_suffix(f".{os.getpid()}.tmp")
            tmp.write_text(json.dumps(data))
            os.replace(tmp, path)
        except OSError:
            pass  # cache is an optimisation only

    def open_pr_files(self, slug: str, base: str) -> list:
        """[{number, url, title, files:set}] for every open PR into ``base``. Raises Unknown."""
        cache = self._load(slug)
        lists = cache.setdefault("lists", {})
        entry = lists.get(base)
        if not entry or self.wall() - float(entry.get("at", 0)) > self.list_ttl:
            raw = self._pages(f"{API}/repos/{slug}/pulls?state=open&base={base}&per_page=100", MAX_LIST_PAGES)
            # Files are read from the PR's canonical base repo: a renamed/moved repo answers its old slug with a 301,
            # which doubles every request (each redirect is a metered request of its own).
            entry = {"at": self.wall(), "prs": [
                {"number": p.get("number"), "sha": (p.get("head") or {}).get("sha", ""),
                 "repo": (((p.get("base") or {}).get("repo") or {}).get("full_name") or slug),
                 "url": p.get("html_url", ""), "title": p.get("title", "")} for p in raw if isinstance(p, dict)]}
            lists[base] = entry
        files_cache = cache.setdefault("files", {})
        key = lambda pr: f"{pr['number']}@{pr['sha']}"   # noqa: E731
        live = {key(p) for p in entry["prs"]}
        missing = [pr for pr in entry["prs"] if key(pr) not in files_cache]
        errors = []
        try:
            if missing:
                with ThreadPoolExecutor(max_workers=min(self.workers, len(missing))) as pool:
                    futures = {pool.submit(self._pr_files, slug, pr): pr for pr in missing}
                    for fut in as_completed(futures):
                        try:
                            files_cache[key(futures[fut])] = fut.result()
                        except Unknown as exc:
                            errors.append(exc)
            if errors:
                raise errors[0]
        finally:
            for k in [k for k in files_cache if k not in live]:
                del files_cache[k]   # closed or re-pushed PRs drop out
            self._save(slug, cache)   # finished file lists are kept even when the lookup ends unknown
        return [dict(pr, files=set(files_cache[key(pr)])) for pr in entry["prs"]]

    def _pr_files(self, slug: str, pr: dict) -> list:
        repo = pr.get("repo") if isinstance(pr.get("repo"), str) and repo_slug(
            f"https://github.com/{pr.get('repo')}") == pr.get("repo") else slug
        rows = self._pages(f"{API}/repos/{repo}/pulls/{pr['number']}/files?per_page=100", MAX_FILE_PAGES)
        if len(rows) >= 100 * MAX_FILE_PAGES:
            raise Unknown(f"PR #{pr['number']} file list truncated by GitHub")
        names = []
        for r in rows:
            if isinstance(r, dict):
                names += [r.get(k) for k in ("filename", "previous_filename") if r.get(k)]
        return names

    def check(self, slug: str, base: str, paths: Iterable[str], prefixes: Iterable[str] = ()) -> dict:
        """{"status": clear|overlap|unknown, "matches": [{path, number, url, title}], "reason": str}."""
        paths = sorted({p.strip("/") for p in paths if p})
        prefixes = sorted({p.strip("/") + "/" for p in prefixes if p})
        if not paths and not prefixes:
            return {"status": "clear", "matches": [], "reason": ""}
        try:
            prs = self.open_pr_files(slug, base)
        except Unknown as exc:
            return {"status": "unknown", "matches": [], "reason": str(exc)}
        matches = []
        for pr in prs:
            for path in paths:
                if path in pr["files"]:
                    matches.append({"path": path, "number": pr["number"], "url": pr["url"], "title": pr["title"]})
            for pre in prefixes:
                for f in sorted(pr["files"]):
                    if f.startswith(pre):
                        matches.append({"path": f, "number": pr["number"], "url": pr["url"], "title": pr["title"]})
                        break
        return {"status": "overlap" if matches else "clear", "matches": matches, "reason": ""}


# ---- local resolution: skill_manage targets -> (repo, base, repo-relative path) ----------------------------------

def _git(cwd: str, *args: str) -> str:
    r = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, timeout=5)
    return r.stdout.strip() if r.returncode == 0 else ""


def locate(path: Path) -> Optional[tuple]:
    """(slug, base, toplevel, rel) for a file inside a GitHub-backed clone, else None (not shared)."""
    real = Path(os.path.realpath(path))
    probe = real if real.is_dir() else real.parent
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    top = _git(str(probe), "rev-parse", "--show-toplevel")
    if not top:
        return None
    slug = repo_slug(_git(top, "config", "--get", "remote.origin.url"))
    if not slug:
        return None
    upstream = _git(top, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    base = upstream.split("/", 1)[1] if "/" in upstream else _git(top, "symbolic-ref", "--short", "-q", "HEAD")
    if not base:
        return None
    try:
        rel = real.relative_to(os.path.realpath(top)).as_posix()
    except ValueError:
        return None
    return slug, base, top, rel


MUTATING = {"edit", "patch", "write_file", "remove_file", "delete"}


def skill_targets(args: dict, find_skill: Callable[[str], Optional[dict]]) -> list:
    """[(path, is_dir)] each mutating op would change, for existing skills (single or batched operations)."""
    ops = args.get("operations") if isinstance(args.get("operations"), list) else [args]
    out = []
    for op in ops:
        if not isinstance(op, dict) or op.get("action") not in MUTATING:
            continue
        found = find_skill(str(op.get("name") or ""))
        if not found or not found.get("path"):
            continue
        skill_dir = Path(found["path"])
        if op["action"] == "delete":
            out.append((skill_dir, True))
        else:
            out.append((skill_dir / (op.get("file_path") or "SKILL.md"), False))
    return out


def check_skill_manage(args: dict, find_skill: Callable, checker: Optional[Checker] = None) -> Optional[str]:
    """Block message when a skill_manage mutation touches a file an open PR changes; None otherwise.

    An unknown lookup does not block the edit (the publisher holds it before it reaches the shared copy)."""
    groups: dict = {}
    for path, is_dir in skill_targets(args if isinstance(args, dict) else {}, find_skill):
        loc = locate(path)
        if not loc:
            continue
        slug, base, _top, rel = loc
        g = groups.setdefault((slug, base), ([], []))
        (g[1] if is_dir else g[0]).append(rel)
    checker = checker or Checker(deadline=8.0)
    for (slug, base), (paths, prefixes) in sorted(groups.items()):
        res = checker.check(slug, base, paths, prefixes)
        if res["status"] == "overlap":
            return overlap_message(slug, base, res["matches"])
    return None


def overlap_message(slug: str, base: str, matches: list) -> str:
    lines = [f"  {m['path']}  <- open PR #{m['number']} {m['url']} ({m['title']})" for m in matches[:10]]
    return ("[live-checkout-guard] Refused: an open pull request already changes this shared file "
            f"({slug}@{base}):\n" + "\n".join(lines) + "\n"
            "Editing the shared copy now changes that PR's base, forcing a conflict reconciliation and another "
            "exact-head review. Send the note to the PR's owner instead (comment on its Kanban card or the PR) "
            "so it lands with that PR, or wait until the PR merges or closes. Escape hatch for a user-approved edit: "
            "SKILL_PR_OVERLAP_GUARD=0.")


# ---- CLI for the publisher ---------------------------------------------------------------------------------------

def main(argv: list) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="pr_overlap.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="paths (NUL-separated on stdin) vs open PRs into BASE")
    c.add_argument("--repo", required=True)
    c.add_argument("--base", required=True)
    c.add_argument("--deadline", type=float, default=30.0)
    ns = ap.parse_args(argv)
    paths = [p for p in sys.stdin.buffer.read().decode("utf-8", "surrogateescape").split("\0") if p]
    res = Checker(deadline=ns.deadline).check(ns.repo, ns.base, paths)
    if res["status"] == "unknown":
        print(f"UNKNOWN\t{res['reason']}")
        return EXIT_UNKNOWN
    for m in res["matches"]:
        print(f"HOLD\t{m['path']}\t#{m['number']}\t{m['url']}")
    return EXIT_OVERLAP if res["matches"] else EXIT_CLEAR


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
