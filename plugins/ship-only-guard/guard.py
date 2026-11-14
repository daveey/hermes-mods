"""Scoped landing-path accident prevention; stdlib-only, including standalone imports.

This is a bounded static analyzer, NOT an adversarial security boundary. Repository
queries use fixed git argv, never shell=True or execution of submitted command text.
Public interfaces return a refusal string or None: check_argv, check_shell,
check_python, and check_tool. No configuration means no protected repositories.
"""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time
from contextvars import ContextVar

# One aggregate allowance, not a fresh timeout for every API/file/merge probe.
PREFLIGHT_TIMEOUT = 120.0
_PREFLIGHT_DEADLINE: ContextVar[float | None] = ContextVar("ship_preflight_deadline", default=None)


class _PreflightExpired(RuntimeError):
    pass


def _remaining():
    deadline = _PREFLIGHT_DEADLINE.get()
    if deadline is None:
        return None
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise _PreflightExpired("ship-only-guard preflight deadline exceeded; retry after checking network/CI")
    return remaining


def bounded_preflight(check, *args, **kwargs):
    """Share a monotonic deadline across nested calls; late results never allow."""
    parent = _PREFLIGHT_DEADLINE.get()
    deadline = time.monotonic() + PREFLIGHT_TIMEOUT
    token = _PREFLIGHT_DEADLINE.set(min(parent, deadline) if parent is not None else deadline)
    try:
        result = check(*args, **kwargs)
        _remaining()
        return result
    except _PreflightExpired as exc:
        return str(exc)
    finally:
        _PREFLIGHT_DEADLINE.reset(token)


def _run_probe(*args, **kwargs):
    remaining = _remaining()
    if remaining is not None:
        kwargs["timeout"] = min(kwargs["timeout"], remaining)
    try:
        result = subprocess.run(*args, **kwargs)
    except subprocess.TimeoutExpired:
        _remaining()  # Aggregate expiry must not be swallowed as missing evidence.
        raise
    _remaining()
    return result

MARKER = "HERMES_CORE_WT_LAND"
REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
ASSIGN = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=(.*)", re.S)


def load_repos(env=None):
    env = os.environ if env is None else env
    try:
        override = env.get("SHIP_ONLY_GUARD_REPOS")
        if override is not None:
            data = json.loads(override)
        else:
            home = Path(env.get("HERMES_HOME", str(Path.home() / ".hermes")))
            policy = home / "ship-only-guard.json"
            # A configured profile inherits the host policy when it has no override.
            if not policy.exists() and home.parent.name == "profiles":
                policy = home.parent.parent / "ship-only-guard.json"
            data = json.loads(policy.read_text())["repos"]
        if not isinstance(data, list) or not all(isinstance(x, str) and REPO.fullmatch(x) for x in data):
            return set()
        return {x.lower() for x in data}
    except (OSError, ValueError, KeyError, TypeError):
        return set()


def _slug(value):
    value = value.removesuffix(".git").rstrip("/")
    match = re.fullmatch(r"(?:https?://[^/]+/|ssh://(?:[^@/]+@)?[^/]+/|[^@/]+@[^:/]+:|[A-Za-z0-9.-]+\.[A-Za-z0-9.-]+/)?([\w.-]+/[\w.-]+)", value)
    return match[1].lower() if match else ""


def _git(cwd, *args):
    # Strip process-level repository overrides; probes refer only to the given cwd.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        p = _run_probe(["git", "--no-optional-locks", "-C", str(cwd), *args],
                           env=env, capture_output=True, text=True, timeout=2)
        return p.stdout.strip() if p.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _remote(cwd, name="origin", push=False):
    direct = _slug(name)
    if direct:
        return direct
    return _slug(_git(cwd, "remote", "get-url", *( ["--push"] if push else []), name))


def _context(cwd, env):
    return _slug(env.get("GH_REPO", "")) or _remote(cwd)


def _deny(repo, operation, repos):
    if repo in repos:
        return (f"[ship-only-guard] Refused direct {operation} for {repo}. "
                "Land only through `hermes-core-wt ship <slug>`; only its land child "
                "sets HERMES_CORE_WT_LAND=1 after the required gates.")
    return None


_VAR = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))")


def _path(value, cwd, env=None):
    """Resolve a cd/-C target. `$VAR`/`${VAR}` expand from the command's env snapshot when set there;
    an unset one stays literal (gh probes then fall back, `_probe_cwd`)."""
    if env is not None and "$" in value:
        value = _VAR.sub(lambda m: env.get(m[1] or m[2], m[0]), value)
    return os.path.abspath(os.path.join(cwd, os.path.expanduser(value)))


def _probe_cwd(cwd):
    """cwd for a gh probe whose repo is explicit. A derived cwd that does not exist (an unexpanded
    `cd $VAR;` target) would raise OSError in subprocess and fail the preflight for no real reason,
    so the probe runs in the guard's own cwd instead."""
    return cwd if cwd and os.path.isdir(cwd) else None


REVIEW_REPLY_MARKER = "<!-- hermes-review-reply -->"
REVIEW_REPLY_FORMS = ('"fixed in <sha>", "follow-up <card>", "refuted: <evidence>" or "declined: <reason>"')
_POINT = re.compile(r"\bnit(?:s|pick|picks)?\b|\bnon[- ]?blocking\b", re.IGNORECASE)
# Ported from hermes-core-wt's review-reply gate: only a negation directly governing the phrase
# ("no nits", "no non-blocking points", "not a non-blocking concern") makes it a verdict; one elsewhere in
# the clause ("No test for the 404 path (non-blocking).") leaves it a point.
_NEGATION = re.compile(r"\b(?:no|zero|0|not(?: an?| any)?|without(?: any)?)\s+(?:other |further |remaining )?"
                       r"(?:blocking (?:or|and|nor) )?$", re.IGNORECASE)
# "Nits: none", "Non-blocking: n/a" and "nits - nothing" are verdicts too.
_NONE_AFTER = re.compile(r"\s*(?:\([^)]*\)|\))?\s*[:=\-\u2013\u2014]?\s*(?:none|n/a|nothing|0)\b", re.IGNORECASE)
# gh's GraphQL author drops Bot's type and "[bot]" suffix (github-actions shows as "github-actions"), and a
# GitHub App is never an owner/member/collaborator, so association NONE never counts as a human answer.
_BOT_LOGINS = {"github-actions", "dependabot", "renovate", "copilot", "copilot-pull-request-reviewer"}


def _review_point(body):
    """The first line of a review body that raises a non-blocking point, or ''."""
    for line in str(body or "").splitlines():
        flat = re.sub(r"[*`#>]", "", line)  # keep _: identifiers in the quoted line stay intact
        for m in _POINT.finditer(flat):
            clause = re.split(r"[.;:!?]", flat[:m.start()])[-1]
            if _NEGATION.search(clause) or _NONE_AFTER.match(flat[m.end():]):
                continue
            text = " ".join(flat.split())
            return text if len(text) <= 160 else text[:157] + "..."
    return ""


def _is_bot(author, association=None):
    author = author if isinstance(author, dict) else {}
    login = str(author.get("login") or "")
    return (author.get("type") == "Bot" or author.get("is_bot") is True or login.endswith("[bot]")
            or login.lower() in _BOT_LOGINS or association == "NONE")


def _when(value):
    from datetime import datetime
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def _review_number(node_id):
    """GitHub's numeric review id (the one in URLs and prose) from a PRR_ node id; the node id if not decodable."""
    import base64
    try:
        raw = base64.urlsafe_b64decode(node_id.split("_", 1)[1] + "==")
        if raw[0] != 0x93:  # msgpack fixarray [0, repo id, database id]
            raise ValueError
        i, value = 1, None
        for _ in range(3):
            tag = raw[i]
            size = {0xcc: 1, 0xcd: 2, 0xce: 4, 0xcf: 8}.get(tag, 0)
            if tag < 0x80:
                value = tag
            elif size:
                value = int.from_bytes(raw[i + 1:i + 1 + size], "big")
            else:
                raise ValueError
            i += 1 + size
        return str(value)
    except (AttributeError, IndexError, ValueError, TypeError):
        return str(node_id)


def _review_reply_refusal(number, data):
    """Refusal text while a review raised non-blocking points no later marked non-bot comment answers; else None.
    Why: 53 of 56 nit-bearing reviews on state PRs merged 11-12/11-13 had no reply before the merge (#2315)."""
    reviews, comments = data.get("reviews"), data.get("comments")
    if not isinstance(reviews, list) or not isinstance(comments, list) or not all(
            isinstance(x, dict) for x in reviews + comments):
        return (f"[ship-only-guard] Refused state merge of PR #{number}: its reviews/comments could not be read, "
                "so unanswered review points cannot be ruled out. Rerun the same merge command once "
                "`gh pr view --json reviews,comments` works.")
    answers = []
    for c in comments:
        if REVIEW_REPLY_MARKER in str(c.get("body") or "") and not _is_bot(c.get("author"), c.get("authorAssociation")):
            answers.append(_when(c.get("createdAt")))
    open_points = []
    for r in reviews:
        point = _review_point(r.get("body"))
        if not point:
            continue
        at = _when(r.get("submittedAt"))
        if at is None or not any(t is not None and t > at for t in answers):
            open_points.append(f"review {_review_number(r.get('id'))}: \"{point}\"")
    if not open_points:
        return None
    return (f"[ship-only-guard] Refused state merge of PR #{number}: {len(open_points)} review(s) raised "
            f"non-blocking points with no later reply in the PR thread: {'; '.join(open_points)}. Post one PR "
            f"comment containing {REVIEW_REPLY_MARKER} that answers each point as {REVIEW_REPLY_FORMS}, then "
            "rerun the same merge command.")


# Exact-head approval record (t_01a1d5e5c3a82d64). Reviewers here post COMMENT reviews (the PR author's account
# cannot approve its own PR), so the verdict is read from the body: a `VERDICT: <x>` line, else the first
# non-empty line, where whichever of "approve" / a blocking phrase comes first decides ("approve (posted as
# COMMENT because the account can't approve)" is an approval).
_VERDICT = re.compile(r"\bVERDICT\s*[:=]\s*(APPROVE[DS]?|PASS|REQUEST[ _-]CHANGES|CHANGES[ _-]REQUESTED|FAIL|REJECT)\b",
                      re.IGNORECASE)
_APPROVE_WORD = re.compile(r"\bapprov(?:e|ed|es|al)\b", re.IGNORECASE)
_BLOCK_WORD = re.compile(r"\brequest(?:s|ed)?[ _-]changes\b|\bchanges[ _-]requested\b|\bnot\s+approv|"
                         r"\b(?:can(?:no|')t|won't|do(?:es)?n't|do(?:es)? not)\s+approv|\breject|"
                         # conditional or withheld approvals (t_01a1d61358568212): "Unable to approve: CI red",
                         # "Needs changes before I can approve", "I would approve once X", "approval withheld"
                         r"\bunable\s+to\s+approv|\bno\s+approv|\bneeds?\s+(?:changes|work|fix(?:es)?)\b|"
                         r"\bwould\s+approv|\bwithh(?:o|e)ld", re.IGNORECASE)
# A PR comment counts by its first line only when that line reads as a review of the head, not a reply: the
# review lane's comment-posted approvals ("Independent review ... at exact head <sha>: **approve**", #1872,
# #1919, #2449) were refused, while author replies ("Re review N (approve @<sha>)", "Addressed review N at
# <sha>") must not stand in for a verdict.
_REVIEW_NOUN = re.compile(r"\b(?:re-?)?review\b", re.IGNORECASE)
_REPLY_LINE = re.compile(r"^\W*re\b|\b(?:repl(?:y|ies|ied)|answer\w*|address\w*|acknowledg\w*|accept\w*|landing|"
                         r"merged|supersed\w*|thanks?|follow-?up|tracked|nits?\b\s+from)\b", re.IGNORECASE)


def _flat(text):
    return " ".join(re.sub(r"[*`#>]", "", str(text)).split())  # keep _: REQUEST_CHANGES stays one token


def _body_verdict(body, first_line_ok=True):
    """'approve', 'block' or None for a review/comment body: the last VERDICT line wins, else the first line."""
    lines = [_flat(line) for line in str(body or "").splitlines()]
    marked = [m.group(1).upper() for line in lines for m in _VERDICT.finditer(line)]
    if marked:
        return "approve" if marked[-1].startswith(("APPROVE", "PASS")) else "block"
    if not first_line_ok:
        return None
    first = next((line for line in lines if line), "")
    ok, bad = _APPROVE_WORD.search(first), _BLOCK_WORD.search(first)
    if ok and (not bad or ok.start() < bad.start()):
        return "approve"
    return "block" if bad else None


def _review_verdict(r):
    state = r.get("state")
    return ("approve" if state == "APPROVED" else "block" if state == "CHANGES_REQUESTED"
            else _body_verdict(r.get("body")))


def _comment_verdict(body, sha):
    """A PR comment's verdict on head `sha`: it names the sha (first 10+ hex) and carries a VERDICT line, or
    its first line is a review of that sha (names it, says review, is no reply) that approves or blocks."""
    if not sha or not re.search(rf"(?<![0-9a-f]){sha[:10]}", body):
        return None
    verdict = _body_verdict(body, first_line_ok=False)
    if verdict is None:
        first = next((_flat(line) for line in body.splitlines() if line.strip()), "")
        if (re.search(rf"(?<![0-9a-f]){sha[:10]}", first) and _REVIEW_NOUN.search(first)
                and not _REPLY_LINE.search(first)):
            verdict = _body_verdict(first)
    return verdict


def _head_verdicts(data, head):
    """[(time, verdict)] of non-bot reviews on commit `head` and non-bot comments naming it."""
    verdicts = []
    for r in data.get("reviews") or []:
        if _is_bot(r.get("author"), r.get("authorAssociation")):
            continue
        if not isinstance(r.get("commit"), dict) or r["commit"].get("oid") != head:
            continue
        verdict = _review_verdict(r)
        if verdict:
            verdicts.append((_when(r.get("submittedAt")), verdict))
    for c in data.get("comments") or []:
        if _is_bot(c.get("author"), c.get("authorAssociation")):
            continue
        verdict = _comment_verdict(str(c.get("body") or ""), head)
        if verdict:
            verdicts.append((_when(c.get("createdAt")), verdict))
    return verdicts


def _approval_refusal(number, data, repo=None, cwd=None, env=None):
    """Refusal text unless the latest non-bot verdict on the exact head approves; else None.

    Evidence is a review whose commit is the head (state APPROVED/CHANGES_REQUESTED, a VERDICT line, or a
    first line that approves), or a PR comment naming the head (its first 10+ hex) with a VERDICT line.
    It proves an approval record exists, not that its author was independent (every agent posts as one
    account): independence stays the landing owner's duty.
    With no verdict on the head at all and a `repo`, an exact-head approval of an earlier head carries when
    the new head only merged the base into it and the PR's patch is unchanged (`_carried_approval`).
    Why: #2413 merged 16 min after its head was pushed with no review or verdict at all; 19 state PRs merged
    2026-11-13 had no exact-head approval record and this preflight admitted every one."""
    head = str(data.get("headRefOid") or "")
    verdicts = _head_verdicts(data, head)
    if any(t is None for t, _ in verdicts):
        return (f"[ship-only-guard] Refused state merge of PR #{number}: a review/verdict on head {head[:10]} has "
                "no readable time, so the latest exact-head verdict cannot be read. Rerun the same merge "
                "command once `gh pr view --json reviews,comments` works.")
    verdicts.sort()
    if verdicts and verdicts[-1][1] == "approve":
        return None
    carried = ""
    if not verdicts and repo is not None:
        ok, carried = _carried_approval(head, data, repo, cwd, env or {})
        if ok:
            return None
    why = ("its latest exact-head verdict requests changes" if verdicts
           else "it has no approval of its exact head (no review on that commit and no comment naming it "
                "with a VERDICT line)" + (f"; {carried}" if carried else ""))
    return (f"[ship-only-guard] Refused state merge of PR #{number}: {why}. A state PR lands only after an "
            f"independent exact-head review of {head}: file a review card for that sha (its reviewer posts a "
            "review on that commit whose first line says approve, or `VERDICT: APPROVE`), fix any requested "
            "changes, then rerun the same merge command. A review of an earlier head counts only when the "
            "head since then only merged the base and the PR's patch-id is unchanged.")


# Patch-identical update-branch carry (t_01a1d666f7d34164). `gh pr update-branch` (or `git merge origin/state`)
# moves the head without changing the PR's own patch: #1812 (bc33e1eb82 -> 21cbd60ac5), #1837
# (20bf041a47 -> d49e9bcfa5) and #2438 (1fd079c68f -> b1e5d5ef46) kept their verbatim patch-id, yet the
# exact-head gate refused all three. The base-drift gate (`_base_drift_refusal`) still applies separately.
_CARRY_MERGES = 3            # base merges on the PR's first-parent line between the approved head and the head
_CARRY_CANDIDATES = 2        # distinct earlier approved heads tried, newest approval first
_SHA = re.compile(r"[0-9a-f]{40}")


class _Unreadable(Exception):
    pass


def _earlier_approval(data, head, exclude=()):
    """(time, sha) of the newest non-bot approval of a head other than `head` (and not in `exclude`), else
    None; and whether a blocking verdict on any head (or a verdict with no readable time) came at or after it."""
    approvals, blocks = [], []
    for r in data.get("reviews") or []:
        if _is_bot(r.get("author"), r.get("authorAssociation")):
            continue
        verdict, at = _review_verdict(r), _when(r.get("submittedAt"))
        oid = r.get("commit", {}).get("oid") if isinstance(r.get("commit"), dict) else None
        if verdict == "block" or (verdict and at is None):
            blocks.append(at)
        elif verdict == "approve" and isinstance(oid, str) and _SHA.fullmatch(oid) and oid != head:
            approvals.append((at, oid))
    for c in data.get("comments") or []:
        if _is_bot(c.get("author"), c.get("authorAssociation")):
            continue
        body, at = str(c.get("body") or ""), _when(c.get("createdAt"))
        # Any blocking comment verdict counts against the carry, whatever head it names (or none).
        first = next((_flat(line) for line in body.splitlines() if line.strip()), "")
        if _body_verdict(body, first_line_ok=False) == "block" or (
                _REVIEW_NOUN.search(first) and not _REPLY_LINE.search(first) and _body_verdict(first) == "block"):
            blocks.append(at)
            continue
        for sha in dict.fromkeys(re.findall(r"(?<![0-9a-f])[0-9a-f]{40}(?![0-9a-f])", body)):
            verdict = _comment_verdict(body, sha)
            if sha != head and verdict == "approve":
                if at is None:
                    blocks.append(None)
                else:
                    approvals.append((at, sha))
            elif verdict == "block":
                blocks.append(at)
    approvals = [a for a in approvals if a[1] not in exclude]
    if not approvals:
        return None, False
    newest = max(approvals)
    return newest, any(t is None or t >= newest[0] for t in blocks)


def _earlier_approvals(data, head):
    """Distinct earlier approved heads, newest approval first, at most _CARRY_CANDIDATES, each with
    whether a block came at or after it. A comment naming two shas (head and base) yields both."""
    out, seen = [], set()
    while len(out) < _CARRY_CANDIDATES:
        newest, blocked = _earlier_approval(data, head, seen)
        if newest is None:
            break
        out.append((newest, blocked))
        seen.add(newest[1])
    return out


def _gh_text(path, cwd, env, accept=None):
    probe_env = dict(env)
    probe_env["GH_PROMPT_DISABLED"] = "1"
    argv = ["gh", "api", *(["-H", f"Accept: {accept}"] if accept else []), path]
    try:
        p = _run_probe(argv, cwd=_probe_cwd(cwd), env=probe_env, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError) as e:
        raise _Unreadable(f"`gh api {path}` could not run ({type(e).__name__})")
    if p.returncode != 0:
        raise _Unreadable(f"`gh api {path}` exited {p.returncode}")
    return str(p.stdout or "")


def _gh_json(path, cwd, env) -> dict:
    try:
        out = json.loads(_gh_text(path, cwd, env))
    except (ValueError, TypeError):
        raise _Unreadable(f"`gh api {path}` returned unreadable JSON")
    if not isinstance(out, dict):
        raise _Unreadable(f"`gh api {path}` returned no JSON object")
    return out


def _patch_id(diff, cwd):
    """`git patch-id --verbatim` of a diff text (no repository needed); _Unreadable when empty or ambiguous.
    Verbatim, not --stable: --stable drops whitespace, so a merge that only re-indented a PR line (a Python
    semantic change) or put a space inside a string literal kept its id and carried (t_01a1d6b8642d50ff).
    --verbatim still ignores line numbers and blob ids, so a clean update-branch merge keeps its id."""
    if not diff.strip():
        raise _Unreadable("a PR diff is empty")
    try:
        p = _run_probe(["git", "patch-id", "--verbatim"], input=diff, cwd=_probe_cwd(cwd),
                           capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError) as e:
        raise _Unreadable(f"`git patch-id` could not run ({type(e).__name__})")
    lines = [line.split() for line in str(p.stdout or "").splitlines() if line.strip()]
    if p.returncode != 0 or len(lines) != 1 or not _SHA.fullmatch(lines[0][0]):
        raise _Unreadable("`git patch-id --verbatim` gave no single id")
    return lines[0][0]


# Tree identity of each update-branch merge (t_01a1d6de4e9ec063). Patch-id hashes hunks without line numbers,
# so a merge that moved an identical hunk (context included) elsewhere in the same file kept it and carried.
# Each merge on the head's first-parent line must instead have exactly the tree a clean three-way merge of its
# two parents gives over their merge base. Read through gh like the rest of the carry (no fetch, no credentials
# in git, no cache on disk); only a path both parents changed differently is merged locally, by `git merge-file`
# on the three blobs, and any conflict, add/add, modify/delete, symlink, submodule or oversized blob refuses.
# This is a rejection prefilter, NOT proof of a clean Git merge: rename/delete can look clean path by path.
# Every survivor also passes `_git_merge_tree_refusal`, including Git's rename/conflict handling.
_MERGE_FILES = 8             # paths per merge both parents changed differently (content-merged locally)
_MERGE_BLOB_BYTES = 8 << 20  # allow large text files; still bound each content-merge blob
_FILE_MODES = ("100644", "100755")


def _flat_tree(sha, repo, cwd, env):
    """{path: (mode, blob sha, size)} of every non-tree entry of commit `sha`'s tree; _Unreadable if odd."""
    got = _gh_json(f"repos/{repo}/git/trees/{sha}?recursive=1", cwd, env)
    entries = got.get("tree")
    if got.get("truncated") is not False or not isinstance(entries, list):
        raise _Unreadable(f"the tree of {sha[:10]} is truncated or unreadable")
    out = {}
    for e in entries:
        if not isinstance(e, dict) or not isinstance(e.get("path"), str) or not e["path"] or not isinstance(
                e.get("mode"), str) or not isinstance(e.get("sha"), str) or not _SHA.fullmatch(e["sha"]):
            raise _Unreadable(f"the tree of {sha[:10]} has an unreadable entry")
        if e.get("type") == "tree":
            continue
        if e.get("type") not in ("blob", "commit") or e["path"] in out:
            raise _Unreadable(f"the tree of {sha[:10]} has an unreadable entry")
        out[e["path"]] = (e["mode"], e["sha"], e.get("size"))
    return out


def _flat_trees(shas, repo, cwd, env, cache):
    """_flat_tree of each sha; the uncached ones read in parallel (each read takes ~3 s on a 5k-path tree)."""
    from concurrent.futures import ThreadPoolExecutor
    todo = [s for s in dict.fromkeys(shas) if s not in cache]
    if todo:
        with ThreadPoolExecutor(max_workers=len(todo)) as pool:
            futures = [pool.submit(_flat_tree, s, repo, cwd, env) for s in todo]
            for s, future in zip(todo, futures):
                cache[s] = future.result()   # re-raises _Unreadable
    return [cache[s] for s in shas]


def _blob(sha, size, repo, cwd, env):
    import base64
    import hashlib
    if not isinstance(size, int) or not 0 <= size <= _MERGE_BLOB_BYTES:
        raise _Unreadable(f"blob {sha[:10]} is over {_MERGE_BLOB_BYTES} bytes or has no size")
    got = _gh_json(f"repos/{repo}/git/blobs/{sha}", cwd, env)
    try:
        if got.get("encoding") != "base64":
            raise ValueError
        data = base64.b64decode(str(got.get("content")))
    except (ValueError, TypeError):
        raise _Unreadable(f"blob {sha[:10]} is unreadable")
    if hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest() != sha:
        raise _Unreadable(f"blob {sha[:10]} does not hash to its id")
    return data


def _merge_file(ours, base, theirs, cwd):
    """Blob id of `git merge-file -p` of three byte strings, or None on a conflict."""
    import hashlib
    import tempfile
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    with tempfile.TemporaryDirectory(prefix="ship-only-guard-") as tmp:
        names = []
        for name, data in (("ours", ours), ("base", base), ("theirs", theirs)):
            Path(tmp, name).write_bytes(data)
            names.append(str(Path(tmp, name)))
        try:
            p = _run_probe(["git", "merge-file", "-p", "-q", *names], cwd=tmp, env=env,
                               capture_output=True, timeout=15)
        except (OSError, subprocess.SubprocessError) as e:
            raise _Unreadable(f"`git merge-file` could not run ({type(e).__name__})")
    if p.returncode != 0 or not isinstance(p.stdout, bytes):
        return None
    return hashlib.sha1(b"blob %d\0" % len(p.stdout) + p.stdout).hexdigest()


def _merge_tree_refusal(merge, ours, theirs, repo, cwd, env, cache):
    """Why `merge` is not the clean auto-merge of `ours` and `theirs` (its first and second parent), else ''."""
    cmp = _gh_json(f"repos/{repo}/compare/{ours}...{theirs}", cwd, env)
    mb = cmp.get("merge_base_commit")
    mb = mb.get("sha") if isinstance(mb, dict) else None
    if not isinstance(mb, str) or not _SHA.fullmatch(mb):
        raise _Unreadable(f"the merge base of {ours[:10]} and {theirs[:10]} is unreadable")
    base, o, t, got = _flat_trees((mb, ours, theirs, merge), repo, cwd, env, cache)
    want, both = {}, []
    for path in sorted(set(base) | set(o) | set(t)):
        b, x, y = (tree[path][:2] if path in tree else None for tree in (base, o, t))
        if x == y or y == b:
            r = x
        elif x == b:
            r = y
        else:
            if b is None or x is None or y is None:
                return f"both parents changed {path} (an add/add or modify/delete conflict)"
            if not {b[0], x[0], y[0]} <= set(_FILE_MODES):
                return f"both parents changed {path}, which is not a regular file"
            both.append(path)
            continue
        if r is not None:
            want[path] = r
    if len(both) > _MERGE_FILES:
        return f"{len(both)} paths need a content merge (more than {_MERGE_FILES})"
    for path in both:
        # Two file modes only, so at most one side changed the mode: that side's mode wins, as in git.
        mode = t[path][0] if o[path][0] == base[path][0] else o[path][0]
        ours_b, base_b, theirs_b = (_blob(tree[path][1], tree[path][2], repo, cwd, env) for tree in (o, base, t))
        blob = _merge_file(ours_b, base_b, theirs_b, cwd)
        if blob is None:
            return f"{path} does not merge cleanly"
        want[path] = (mode, blob)
    have = {path: e[:2] for path, e in got.items()}
    if have != want:
        diff = sorted(p for p in set(have) | set(want) if have.get(p) != want.get(p))
        return (f"its tree differs from the clean merge of {ours[:10]} and {theirs[:10]} at {diff[0]}"
                + (f" (+{len(diff) - 1} more)" if len(diff) > 1 else ""))
    return ""


def _git_merge_tree_refusal(merge, ours, theirs, repo, cwd, env):
    """Authoritative merge-tree check; a bounded shallow fetch may refuse, never guess."""
    import tempfile
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo) or not all(
            isinstance(s, str) and _SHA.fullmatch(s) for s in (merge, ours, theirs)):
        return "merge-tree inputs are unreadable"
    run_env = {k: v for k, v in {**os.environ, **env}.items() if not k.startswith("GIT_")}
    run_env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0")
    home = Path(run_env.get("HERMES_HOME", str(Path.home() / ".hermes")))
    step = "cache"
    try:
        cache = home / "cache"
        cache.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="ship-carry-", dir=cache) as tmp:
            def git(*args):
                nonlocal step
                step = args[0]
                p = _run_probe(["git", "-c", "core.hooksPath=" + os.devnull,
                                    "-c", "credential.helper=!gh auth git-credential", *args],
                                   cwd=tmp, env=run_env, capture_output=True, text=True, timeout=30)
                if p.returncode != 0:
                    raise _Unreadable(f"git {step} exited {p.returncode}")
                if not isinstance(p.stdout, str):
                    raise _Unreadable(f"git {step} returned unreadable output")
                return p.stdout.strip()

            git("init", "--bare", "--template=", "-q")
            # Fetch full blobs: avoids lazy fetch subprocesses inside merge-tree and
            # their separate auth/timeouts. Depth and wall time bound the attempt.
            git("fetch", "--quiet", "--no-tags", "--no-recurse-submodules", "--depth=128",
                "https://github.com/" + repo + ".git", merge, ours, theirs)
            parents = git("show", "-s", "--format=%P", merge).split()
            if parents != [ours, theirs]:
                return "fetched merge parents differ from the reported parents"
            bases = git("merge-base", "--all", ours, theirs).split()
            if len(bases) != 1 or not _SHA.fullmatch(bases[0]):
                return "merge base is unreadable or multiple (cannot prove bounded history)"
            shallow = Path(tmp, "shallow")
            if shallow.exists():
                cuts = set(shallow.read_text().split())
                relevant = set(git("rev-list", ours, theirs, "--not", bases[0]).split())
                if cuts & relevant:
                    return "fetch depth cuts history before the merge base"
            want = git("merge-tree", "--write-tree", ours, theirs)
            have = git("rev-parse", merge + "^{tree}")
            if not _SHA.fullmatch(want) or not _SHA.fullmatch(have):
                return "merge-tree returned an unreadable tree id"
            if have != want:
                return "its tree differs from the clean merge computed by git merge-tree"
    except _Unreadable as e:
        return str(e)
    except (OSError, UnicodeError, subprocess.SubprocessError) as e:
        return f"git {step} could not complete ({type(e).__name__})"
    return ""


def _carried_approval(head, data, repo, cwd, env):
    """(True, H0) when an approval of an earlier head H0 counts for `head`, else (False, why-or-'').

    All must hold, read only through gh (any failed or odd read refuses):
    (a) H0 is an ancestor of the head (`compare/H0...head` is ahead, behind 0) and every commit on the head's
        first-parent line back to H0 is a merge (at most _CARRY_MERGES) whose second parent is on `state`
        (`compare/<parent>...state` ahead or identical): the head only merged the base in;
    (b) `git patch-id --verbatim` (whitespace-sensitive) of the PR diff `compare/state...H0` equals the one of
        `compare/state...head` (each is merge-base..sha, the PR's own patch);
    (c) no non-bot blocking verdict, on any head, at or after that approval;
    (d) each of those merges is a clean, unedited auto-merge: its tree (`git/trees/<sha>?recursive=1`) is
        exactly the three-way merge of its two parents over their merge base (`compare/<first>...<second>`),
        content-merging only paths both parents changed (`_merge_tree_refusal`). Patch-id ignores line numbers,
        so without (d) a merge that moved an identical hunk elsewhere in the same file kept the id and carried.
    The newest _CARRY_CANDIDATES distinct approved heads are tried in turn (a comment naming the head and a
    base sha yields both); the first one's refusal reason is reported."""
    first = ""
    for (_, h0), blocked in _earlier_approvals(data, head):
        ok, why = _carry_one(head, h0, blocked, repo, cwd, env)
        if ok:
            return True, h0
        first = first or why
    return False, first


def _carry_one(head, h0, blocked, repo, cwd, env):
    lead = f"the approval of earlier head {h0[:10]} does not carry to it"
    if blocked:
        return False, f"{lead}: a blocking verdict came at or after that approval"
    if not _SHA.fullmatch(head):
        return False, f"{lead}: the head sha is unreadable"
    try:
        cmp = _gh_json(f"repos/{repo}/compare/{h0}...{head}", cwd, env)
        if cmp.get("status") != "ahead" or cmp.get("behind_by") != 0:
            return False, (f"{lead}: {h0[:10]} is not an ancestor of the head (compare status "
                           f"{cmp.get('status')!r}; a rebase or force-push changed the PR)")
        known = {}
        listed = cmp.get("commits")
        for c in listed if isinstance(listed, list) else []:
            if isinstance(c, dict) and isinstance(c.get("sha"), str) and isinstance(c.get("parents"), list):
                known[c["sha"]] = [p.get("sha") if isinstance(p, dict) else None for p in c["parents"]]
        cur, bases, merges = head, [], []
        while cur != h0:
            if len(bases) >= _CARRY_MERGES:
                return False, (f"{lead}: more than {_CARRY_MERGES} commits sit on the PR's first-parent line "
                               f"after {h0[:10]}")
            parents = known.get(cur)
            if parents is None:
                got = _gh_json(f"repos/{repo}/git/commits/{cur}", cwd, env).get("parents")
                parents = [p.get("sha") if isinstance(p, dict) else None for p in got] if isinstance(
                    got, list) else []
            if not parents or not all(isinstance(p, str) and _SHA.fullmatch(p) for p in parents):
                raise _Unreadable(f"the parents of {cur[:10]} are unreadable")
            if len(parents) != 2:
                return False, (f"{lead}: {cur[:10]} after it is not a merge of the base (a non-merge commit "
                               "changes the PR)")
            bases.append(str(parents[1]))
            merges.append((cur, str(parents[0]), str(parents[1])))
            cur = str(parents[0])
        for parent in dict.fromkeys(bases):
            on = _gh_json(f"repos/{repo}/compare/{parent}...state", cwd, env)
            if on.get("status") not in ("ahead", "identical") or on.get("behind_by") != 0:
                return False, (f"{lead}: merge parent {parent[:10]} is not on state (the head merged "
                               "something other than the base)")
        diff = "application/vnd.github.diff"
        old = _patch_id(_gh_text(f"repos/{repo}/compare/state...{h0}", cwd, env, accept=diff), cwd)
        new = _patch_id(_gh_text(f"repos/{repo}/compare/state...{head}", cwd, env, accept=diff), cwd)
    except _Unreadable as e:
        return False, f"{lead}: {e}, so patch identity cannot be shown"
    if old != new:
        return False, f"{lead}: the PR's patch changed (patch-id {old[:12]} -> {new[:12]})"
    cache = {}
    try:
        for merge, ours, theirs in reversed(merges):
            why = _merge_tree_refusal(merge, ours, theirs, repo, cwd, env, cache)
            if not why:
                why = _git_merge_tree_refusal(merge, ours, theirs, repo, cwd, env)
            if why:
                return False, f"{lead}: merge {merge[:10]} is not a clean, unedited merge of the base: {why}"
    except _Unreadable as e:
        return False, f"{lead}: {e}, so a clean merge cannot be shown"
    return True, h0


# Base drift under the PR's CI (t_01a1d6201140c966). The PR's recorded baseRefOid is at or before the base its
# `pull_request` CI merge ref was built on: live #2507's checkout was "Merge 3ac709e8e8 into f5f7030b43" while
# its baseRefOid c67ac3f18d sat 15 commits behind, and #2491 shows GitHub does not refresh it on a later
# mergeability recompute. So baseRefOid...state is a superset of the untested window (it can over-refuse, never
# miss). A retarget without a CI rerun could make baseRefOid newer than the tested base (fail open): never
# retarget during landing. A state commit landing on the PR's paths inside that window is never tested with the
# PR. #2491 (CI on base 3a55357793) was rebase-merged onto 5f0478a3dc, which carried #2487's adc4b89b37 changing
# hosts/mini-1/bb-tuner/bb_tuner_feed.py, and state went red at 21c2ccf5a8.
_COMPARE_COMMITS_CAP = 250   # GitHub's compare lists at most 250 commits per page
_COMPARE_FILES_CAP = 300     # ... and at most 300 files
_PR_FILES_CAP = 100          # gh pr view --json files reads one page of 100
_ATTRIBUTE_HITS = 3          # per-hit culprit lookups (one bounded gh api call each) on a refusal
_WORKFLOWS = ".github/workflows/"
_CHURN_SUBJECT = re.compile(r"^(?:sync: |Merge |skills: edits from )")
# CI harness files at the root or in a flat top-level dir (state: tests/run_ci.py, tests/requirements-ci*.txt)
# change how every test runs, so like workflows they hold any PR (t_01a1d62f8c197f3d).
_GLOBAL_FILE = re.compile(r"(?:[^/]+/)?(?:requirements[^/]*|run_ci\.py)")
_TEST_MODULE = re.compile(r"test_(.+)\.py")
_CODE_SUFFIXES = {"", ".py", ".sh", ".js", ".mjs", ".cjs", ".ts", ".swift"}
# Stems too generic to tie a test module to one code file.
_GENERIC_STEMS = {"__init__", "__main__", "main", "conftest", "setup", "util", "utils", "common", "helpers",
                  "config", "test", "tests"}


def _path_area(path):
    """The directory an edit lands in, cut to its top 3 segments. A repo-root file or one in a flat top-level
    dir (`tests/`, `docs/`: dozens of unrelated modules) is its own area, or every state PR would collide."""
    parts = str(path).strip("/").split("/")
    return "/".join(parts[:-1][:3]) if len(parts) > 2 else "/".join(parts)


def _flat_shared(path):
    """A non-test file directly in a top-level dir (tests/sync_test_support.py, tests/__init__.py,
    tests/placement_support.py): shared support for that dir's modules. Markdown is prose, never support."""
    parts = path.split("/")
    return len(parts) == 2 and not parts[1].startswith("test_") and not parts[1].endswith(".md")


def _norm(name):
    return name.replace("-", "_").lower()


def _test_key(path):
    """`<stem>` of a `test_<stem>[_suffix].py` module anywhere (tests/test_bb_tuner_feed.py -> bb_tuner_feed)."""
    m = _TEST_MODULE.fullmatch(path.rsplit("/", 1)[-1])
    return _norm(m[1]) if m else ""


def _code_stem(path):
    """Normalized basename stem of a code file (home/scripts/hub-read.py, home/bin/hermes-core-wt), or ''."""
    name = path.rsplit("/", 1)[-1]
    if name.startswith("."):
        return ""
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    elif not stem or ("." + ext) not in _CODE_SUFFIXES:
        return ""
    stem = _norm(stem)
    return "" if len(stem) < 4 or stem in _GENERIC_STEMS else stem


def _coupled(key, stem):
    """A test key and a code stem name the same module: equal, or one extends the other by `_<suffix>`
    (test_hub_read_examples.py ~ hub-read.py; test_foo.py ~ foo_cli.py)."""
    return bool(key and stem) and (key == stem or key.startswith(stem + "_") or stem.startswith(key + "_"))


def _skill_prose(path):
    """Skill markdown (home/skills/**.md, skills/**.md): prose that the state/mods suites only lint file by
    file. hermes-sync pushes live skill edits straight to state every few minutes (`sync: <host>`), so the
    same-area rule kept refusing PRs that also touch a skill reference (#2563: 5fbd15ce4f edited
    core-update-runtime.md). Two prose edits cannot change a check outcome the other was tested under; a
    textual collision still fails GitHub's rebase merge (t_01a1d6b3538e8e43)."""
    p = str(path)
    return p.endswith(".md") and (p.startswith("home/skills/") or p.startswith("skills/"))


def _overlap(pr, base):
    """Why the base-delta file `base` can change what the PR file `pr` was tested against, or ''."""
    if base.startswith(_WORKFLOWS) or _GLOBAL_FILE.fullmatch(base):
        return "ci"
    if _skill_prose(pr) and _skill_prose(base):
        return ""
    if _path_area(pr) == _path_area(base):
        return "area"
    if (pr.count("/") == base.count("/") == 1 and pr.split("/")[0] == base.split("/")[0]
            and (_flat_shared(pr) or _flat_shared(base))):
        return "shared"
    if _coupled(_test_key(pr), _code_stem(base)) or _coupled(_test_key(base), _code_stem(pr)):
        return "test-code"
    return ""


def _base_drift_refusal(number, repo, data, cwd, env):
    """Refusal text when state moved, since the base the PR's CI tested, on the PR's own areas; else None.

    Overlap (`_overlap`): a base-delta file and a PR file in the same top-3-segment directory; any base-delta
    .github/workflows/ file or root/flat-dir requirements*/run_ci.py; a non-test, non-markdown file directly in
    a top-level dir (tests/sync_test_support.py) on either side with any file directly in that dir on the other;
    or a test_<stem>*.py module on one side and a code file whose stem matches it on the other (tests/test_foo.py
    ~ home/scripts/foo.py, either direction). Churn elsewhere (hermes-sync `sync:` commits under other
    hosts/<host>/ or home/skills/...) never refuses. An unreadable, truncated or non-linear compare fails closed."""
    head = str(data.get("headRefOid") or "")
    base = data.get("baseRefOid")

    def closed(why):
        return (f"[ship-only-guard] Refused state merge of PR #{number}: {why}, so a state commit that landed on "
                "this PR's paths after its CI ran cannot be ruled out. Rerun the same merge command once "
                f"`gh pr view {number} --json baseRefOid,files` and `gh api repos/{repo}/compare/<baseRefOid>...state` "
                "read cleanly.")

    if not isinstance(base, str) or not re.fullmatch(r"[0-9a-f]{40}", base):
        return closed("its baseRefOid (the base its CI merge ref was built from) is missing")
    files = data.get("files")
    if not isinstance(files, list) or not files or len(files) >= _PR_FILES_CAP or not all(
            isinstance(f, dict) and isinstance(f.get("path"), str) and f["path"] for f in files):
        return closed("its changed-file list is missing, empty or possibly capped at 100")
    probe_env = dict(env)
    probe_env["GH_PROMPT_DISABLED"] = "1"
    try:
        p = _run_probe(["gh", "api", f"repos/{repo}/compare/{base}...state"],
                           cwd=_probe_cwd(cwd), env=probe_env, capture_output=True, text=True, timeout=15)
        if p.returncode != 0:
            return closed(f"the compare {base[:10]}...state failed (exit {p.returncode})")
        cmp = json.loads(p.stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        return closed(f"the compare {base[:10]}...state could not be read")
    if not isinstance(cmp, dict):
        return closed("the compare response is not an object")
    total, commits, changed = cmp.get("total_commits"), cmp.get("commits"), cmp.get("files")
    if (not isinstance(total, int) or isinstance(total, bool) or not isinstance(commits, list)
            or not isinstance(changed, list) or cmp.get("status") not in ("ahead", "identical")
            or cmp.get("behind_by") not in (0, None)):
        return closed("the compare lacks total_commits/commits/files or the PR base is not an ancestor of state")
    if total >= _COMPARE_COMMITS_CAP or len(commits) != total or len(changed) >= _COMPARE_FILES_CAP:
        return closed(f"the compare is possibly truncated ({total} commits, {len(changed)} files)")
    if not all(isinstance(f, dict) and isinstance(f.get("filename"), str) for f in changed) or not all(
            isinstance(c, dict) and isinstance(c.get("sha"), str) for c in commits):
        return closed("the compare lists a file or commit without a name")
    if not total:
        return None
    hits = {}
    for f in changed:
        for name in (f["filename"], f.get("previous_filename")):
            if not isinstance(name, str) or not name or name in hits:
                continue
            for pf in files:
                why = _overlap(pf["path"], name)
                if why:
                    hits[name] = (why, pf["path"])
                    break
    if not hits:
        return None

    def subject(c):
        msg = c.get("commit", {}).get("message") if isinstance(c.get("commit"), dict) else ""
        return (str(msg or "").splitlines() or [""])[0]

    def shown_hit(name):
        why, pf = hits[name]
        return name if why in ("area", "ci") or pf == name else f"{name} ({why} ~ {pf})"

    # The compare aggregates files, so a per-commit attribution needs more requests: list the content commits
    # (not hermes-sync/skills churn or merges) first, the churn count after.
    work = [c for c in commits if not _CHURN_SUBJECT.match(subject(c))]
    churn = len(commits) - len(work)
    named = "; ".join(f"{c['sha'][:10]} {subject(c)[:70]}" for c in work[:8])
    if len(work) > 8:
        named += f"; +{len(work) - 8} more"
    if churn:
        named += f" (+{churn} sync/merge commit(s))"
    order = sorted(hits)
    shown = ", ".join(shown_hit(h) for h in order[:6]) + (f", +{len(order) - 6} more" if len(order) > 6 else "")
    culprits = _attribute_hits(repo, order[:_ATTRIBUTE_HITS], {c["sha"]: c for c in commits}, subject, cwd, env)
    return (f"[ship-only-guard] Refused state merge of PR #{number}: its CI tested base {base[:10]} (or later), "
            f"and {total} state commit(s) landed since then; together they change this PR's areas ({shown}). No "
            f"check ran on that combination (#2491 turned state red this way). "
            + (f"Last base change per file: {culprits}. " if culprits else "")
            + f"Base commits since {base[:10]}: {named}. "
            f"Run `gh api -X PUT repos/{repo}/pulls/{number}/update-branch -f expected_head_sha={head}`, wait "
            "for green CI on the new head, then rerun the merge with --match-head-commit <new head>. The guard "
            "re-checks exact-head approval on the new head; an approval of the old head carries when the new "
            "head only merged state and the PR's `git patch-id --verbatim` is unchanged.")


def _attribute_hits(repo, names, window, subject, cwd, env):
    """'path: sha subject; ...' naming, per overlapping file, the newest commit in the drift window that touched
    it (one `gh api repos/R/commits?sha=state&path=<file>&per_page=5` each, at most _ATTRIBUTE_HITS). Best
    effort: an unreadable answer or a culprit older than the newest 5 only drops that file's attribution."""
    from urllib.parse import quote
    probe_env = dict(env)
    probe_env["GH_PROMPT_DISABLED"] = "1"
    out = []
    for name in names:
        try:
            p = _run_probe(["gh", "api", f"repos/{repo}/commits?sha=state&path={quote(name, safe='/')}&per_page=5"],
                               cwd=_probe_cwd(cwd), env=probe_env, capture_output=True, text=True, timeout=8)
            listed = json.loads(p.stdout) if p.returncode == 0 else None
        except (OSError, ValueError, subprocess.SubprocessError):
            continue
        if not isinstance(listed, list):
            continue
        hit = next((window[c["sha"]] for c in listed if isinstance(c, dict) and c.get("sha") in window), None)
        if hit:
            out.append(f"{name}: {hit['sha'][:10]} {subject(hit)[:60]}")
    return "; ".join(out)


# Land turn (t_01a1d6b3538e8e43). The drift rule above holds a PR until CI ran on a base with every later
# state commit on its areas. Hot areas (hermes-sync, tests/, workflows) took a commit per CI cycle on
# 2026-11-13, so a green, approved PR refreshed by update-branch went stale again before its next merge
# attempt: #2563 was refused 4 times. A turn orders the landings instead of weakening the rule: the first
# drift refusal of a green, approved PR records `refs/land-turn/pr-<N>` (a commit whose message carries the
# PR's files and an expiry) and, until it expires or the PR closes, every OTHER state PR merge whose files
# would drift the holder (`_overlap(holder_file, other_file)`) is refused, as is (in state's
# scripts/state-push-guard pre-push) a direct non-sync push. The turn only ever adds refusals; any error
# reading it fails open, because the drift compare still guards correctness.
_TURN_PREFIX = "land-turn/pr-"
_TURN_TTL = 5400             # one refreshed CI cycle (#2563: 10-45 min) plus the merge attempt, with slack
_TURN_MAX = 6                # turn commits read per preflight (newest PR numbers first)
_TURN_BUDGET = 6             # seconds of turn reads per preflight; the rest is skipped (fail open)
_TURN_CALL_TIMEOUT = 5
_TURN_SUBJECT = "land-turn pr="
# After a turn expires its PR cannot take another until a further _TURN_TTL has passed (the cooling ref is
# kept for that and then deleted by the next reader), so a PR whose area keeps drifting through exports never
# holds its area for more than half of the time.


def _now():
    import time
    return time.time()


def _gh_call(args, cwd, env, payload=None, timeout=10):
    """One bounded `gh api` call: (returncode, parsed JSON or None). Never raises."""
    probe_env = dict(env)
    probe_env["GH_PROMPT_DISABLED"] = "1"
    argv = ["gh", "api", *args, *(["--input", "-"] if payload is not None else [])]
    try:
        p = _run_probe(argv, cwd=_probe_cwd(cwd), env=probe_env, capture_output=True, text=True,
                           timeout=timeout, input=json.dumps(payload) if payload is not None else None)
    except (OSError, subprocess.SubprocessError):
        return -1, None
    try:
        return p.returncode, json.loads(p.stdout) if p.stdout else None
    except (ValueError, TypeError):
        return p.returncode, None


def parse_turn(message):
    """The turn record in a land-turn commit message, or None. Shared format with state's state-push-guard:
    subject `land-turn pr=<N>`, then one JSON line {"pr", "head", "expires", "files"}."""
    lines = str(message or "").splitlines()
    if not lines or not lines[0].startswith(_TURN_SUBJECT):
        return None
    for line in lines[1:]:
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            return None
        ok = (isinstance(rec, dict) and isinstance(rec.get("pr"), int) and not isinstance(rec.get("pr"), bool)
              and isinstance(rec.get("expires"), (int, float)) and not isinstance(rec.get("expires"), bool)
              and 0 < rec["expires"] < 4_102_444_800 and isinstance(rec.get("files"), list)
              and all(isinstance(f, str) and f for f in rec["files"]))
        return rec if ok else None
    return None


def _turn_number(ref):
    m = re.fullmatch(rf"refs/{re.escape(_TURN_PREFIX)}(\d+)", str(ref.get("ref", "")) if isinstance(ref, dict) else "")
    return int(m.group(1)) if m else -1


def _live_turns(repo, cwd, env):
    """[(ref, record)] of unexpired turns; [] on any read failure (fail open).

    Reads at most _TURN_MAX turn commits within _TURN_BUDGET seconds (the whole preflight shares the
    pre_tool_call budget), newest PR numbers first. A turn past its cooldown (expires + _TURN_TTL) or with a
    junk record is deleted here, so the ref list stays as short as the set of recent turns."""
    import time
    rc, refs = _gh_call([f"repos/{repo}/git/matching-refs/{_TURN_PREFIX}"], cwd, env, timeout=_TURN_CALL_TIMEOUT)
    if rc != 0 or not isinstance(refs, list):
        return []
    out, now, start = [], _now(), time.monotonic()
    refs = sorted((r for r in refs if _turn_number(r) >= 0), key=_turn_number, reverse=True)
    for ref in refs[:_TURN_MAX]:
        if time.monotonic() - start > _TURN_BUDGET:
            break
        sha = ref.get("object", {}).get("sha") if isinstance(ref.get("object"), dict) else None
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
            continue
        rc, obj = _gh_call([f"repos/{repo}/git/commits/{sha}"], cwd, env, timeout=_TURN_CALL_TIMEOUT)
        if rc != 0 or not isinstance(obj, dict):
            continue
        rec = parse_turn(obj.get("message"))
        if rec and rec["expires"] > now:
            out.append((ref["ref"], rec))
        elif rec is None or rec["expires"] + _TURN_TTL <= now:
            _gh_call(["-X", "DELETE", f"repos/{repo}/git/{ref['ref']}"], cwd, env, timeout=_TURN_CALL_TIMEOUT)
    return out


def turn_blocks(turn_files, files):
    """(holder file, other file) when landing `files` would drift a holder with `turn_files`, else None."""
    for hf in turn_files:
        for f in files:
            if _overlap(hf, f):
                return hf, f
    return None


def _turn_hold_refusal(number, data, repo, cwd, env):
    """Refusal while another open PR's live turn covers this PR's areas, else None (fail open)."""
    files = [f["path"] for f in data.get("files") or [] if isinstance(f, dict) and isinstance(f.get("path"), str)]
    for ref, rec in _live_turns(repo, cwd, env):
        if rec["pr"] == int(number):
            continue
        hit = turn_blocks(rec["files"], files)
        if not hit:
            continue
        rc, pr = _gh_call([f"repos/{repo}/pulls/{rec['pr']}"], cwd, env, timeout=_TURN_CALL_TIMEOUT)
        if rc != 0 or not isinstance(pr, dict):
            continue   # cannot tell whether the holder is still open: fail open
        if pr.get("state") != "open":
            _gh_call(["-X", "DELETE", f"repos/{repo}/git/{ref}"], cwd, env, timeout=_TURN_CALL_TIMEOUT)
            continue   # the holder landed or closed: its turn is over
        return (f"[ship-only-guard] Refused state merge of PR #{number}: PR #{rec['pr']} holds the state land "
                f"turn until {_until(rec['expires'])} ({ref}), and this merge would change its area ({hit[1]} ~ "
                f"{hit[0]}), sending it into another CI cycle. Wait until #{rec['pr']} merges or its turn "
                "expires, then rerun the same merge command (the drift check will then ask for update-branch "
                "if needed).")
    return None


def _turn_acquire(number, data, repo, cwd, env):
    """Record this PR's land turn (best effort). Returns the expiry text, or '' when not recorded."""
    head = str(data.get("headRefOid") or "")
    files = [f["path"] for f in data.get("files") or [] if isinstance(f, dict) and isinstance(f.get("path"), str)]
    if not re.fullmatch(r"[0-9a-f]{40}", head) or not files:
        return ""
    ref = f"{_TURN_PREFIX}{int(number)}"
    import time
    start = time.monotonic()
    late = lambda: time.monotonic() - start > _TURN_BUDGET   # noqa: E731 - preflight shares the hook budget
    rc, cur = _gh_call([f"repos/{repo}/git/ref/{ref}"], cwd, env, timeout=_TURN_CALL_TIMEOUT)
    if rc == 0 and isinstance(cur, dict):
        sha = cur.get("object", {}).get("sha") if isinstance(cur.get("object"), dict) else None
        rc2, obj = _gh_call([f"repos/{repo}/git/commits/{sha}"], cwd, env, timeout=_TURN_CALL_TIMEOUT) if sha else (-1, None)
        rec = parse_turn(obj.get("message")) if rc2 == 0 and isinstance(obj, dict) else None
        if rec and rec["expires"] > _now():
            return _until(rec["expires"])   # one live turn per PR: a later refusal never extends it
        if rec and rec["expires"] + _TURN_TTL > _now():
            return ""                        # cooling down: others in its area get a window first
    if late():
        return ""
    rc, hc = _gh_call([f"repos/{repo}/git/commits/{head}"], cwd, env, timeout=_TURN_CALL_TIMEOUT)
    tree = hc.get("tree", {}).get("sha") if rc == 0 and isinstance(hc, dict) and isinstance(hc.get("tree"), dict) else None
    if not isinstance(tree, str) or late():
        return ""
    expires = int(_now()) + _TURN_TTL
    rec = {"pr": int(number), "head": head, "expires": expires, "files": files}
    rc, made = _gh_call([f"repos/{repo}/git/commits"], cwd, env, timeout=_TURN_CALL_TIMEOUT, payload={
        "message": f"{_TURN_SUBJECT}{int(number)}\n\n{json.dumps(rec, sort_keys=True)}\n",
        "tree": tree, "parents": [head]})
    sha = made.get("sha") if rc == 0 and isinstance(made, dict) else None
    if not isinstance(sha, str):
        return ""
    rc, _ = _gh_call([f"repos/{repo}/git/refs"], cwd, env, timeout=_TURN_CALL_TIMEOUT, payload={"ref": f"refs/{ref}", "sha": sha})
    if rc != 0:
        rc, _ = _gh_call(["-X", "PATCH", f"repos/{repo}/git/refs/{ref}"], cwd, env, timeout=_TURN_CALL_TIMEOUT,
                         payload={"sha": sha, "force": True})
    return _until(expires) if rc == 0 else ""


def _until(epoch):
    import datetime
    return f"{datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc):%H:%M} UTC"


def _turn_safe(fn, *args):
    """The land turn only orders landings: any error in it adds nothing (the drift compare still guards)."""
    try:
        return fn(*args)
    except Exception:
        return None


def _state_merge(words, repo, cwd, env):
    """Narrow state-only exception, never a general non-main merge allowance.

    Returns True to allow, False when the command is not this route, or a refusal
    string: a named failed preflight step (gh view error, state/base/head/url mismatch,
    non-green check names), unanswered review points or a missing exact-head
    approval. Independence of the reviewer and required-CI coverage remain caller
    duties; this preflight refuses missing/non-green visible checks, refuses while any
    review whose body raises a non-blocking point ("nit", "non-blocking", un-negated)
    has no later non-bot PR comment marked <!-- hermes-review-reply -->: reply before
    you merge, and refuses unless the latest non-bot verdict on the exact head approves
    (`_approval_refusal`). Unreadable reviews/comments refuse. Last, one bounded
    compare of the PR's baseRefOid...state refuses when state moved on the PR's own
    areas after its CI built the merge ref (`_base_drift_refusal`). It is still not an
    atomic base lock (gh only offers an atomic head fence): a commit landing between
    this read and the merge is not seen.
    """
    # Accept explicit numeric PR and repo only; avoid gh's implicit current PR,
    # URL/repo precedence, aliases, unknown options, auto-merge or admin override.
    if words[:2] != ["pr", "merge"]:
        return False
    args, options = [], {}
    i = 2
    while i < len(words):
        word = words[i]
        if word in {"--repo", "-R", "--match-head-commit"}:
            key = "--repo" if word == "-R" else word
            if key in options or i + 1 >= len(words):
                return False
            options[key] = words[i + 1]
            i += 2
        elif word == "--rebase" and word not in options:
            options[word] = True
            i += 1
        elif not word.startswith("-"):
            args.append(word)
            i += 1
        else:
            return False
    if (len(args) != 1 or not re.fullmatch(r"[1-9][0-9]*", args[0])
            or options.get("--repo", "").lower() != repo
            or not options.get("--rebase")
            or not re.fullmatch(r"[0-9a-f]{40}", options.get("--match-head-commit", ""))):
        return False
    fields = "number,url,state,baseRefName,baseRefOid,headRefOid,statusCheckRollup,reviews,comments,files"
    probe_env = dict(env)
    probe_env["GH_PROMPT_DISABLED"] = "1"
    number, sha = args[0], options["--match-head-commit"]

    def failed(step):
        # The command has the state-merge form, so name the failing preflight step instead of the
        # generic deny (a `cd $VAR;` cwd once failed it silently, t_01a1d625bbebeaa8).
        return (f"[ship-only-guard] Refused state merge of PR #{number} ({repo}): the exact-head state "
                f"preflight failed: {step}. Fix that and rerun the same command; a PR into main lands "
                "only through `hermes-core-wt ship <slug>`.")

    try:
        p = _run_probe(["gh", "pr", "view", number, "--repo", repo, "--json", fields],
                           cwd=_probe_cwd(cwd), env=probe_env, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError) as e:
        return failed(f"`gh pr view {number} --repo {repo}` could not run ({type(e).__name__}: {str(e)[:120]})")
    if p.returncode != 0:
        err = " ".join(str(getattr(p, "stderr", "") or "").split())[:160]
        return failed(f"`gh pr view {number} --repo {repo}` exited {p.returncode}" + (f" ({err})" if err else ""))
    try:
        data = json.loads(p.stdout)
    except (ValueError, TypeError):
        return failed(f"`gh pr view {number}` returned unreadable JSON")
    if not isinstance(data, dict):
        return failed(f"`gh pr view {number}` returned no JSON object")
    try:
        mismatch = []
        if data.get("number") != int(number):
            mismatch.append(f"number is {data.get('number')!r}")
        if data.get("state") != "OPEN":
            mismatch.append(f"state is {data.get('state')!r}, not OPEN")
        if data.get("baseRefName") != "state":
            mismatch.append(f"base is {data.get('baseRefName')!r}, not 'state'")
        if data.get("headRefOid") != sha:
            mismatch.append(f"head is {str(data.get('headRefOid'))[:12]!r}, not --match-head-commit {sha[:12]}")
        if not isinstance(data.get("url"), str) or data["url"].lower() != f"https://github.com/{repo}/pull/{number}":
            mismatch.append(f"url is {data.get('url')!r}")
        if mismatch:
            return failed("; ".join(mismatch))
        checks = data.get("statusCheckRollup")
        # gh's connection is bounded; never infer success from a possibly capped
        # rollup. Skipped/neutral/unknown checks conservatively require the ship path.
        if not isinstance(checks, list) or not 0 < len(checks) < 100:
            n = len(checks) if isinstance(checks, list) else "no"
            return failed(f"statusCheckRollup has {n} entries (needs 1-99 readable checks)")

        def green(c):
            return isinstance(c, dict) and (
                c.get("__typename") == "CheckRun" and c.get("status") == "COMPLETED"
                and c.get("conclusion") == "SUCCESS" or
                c.get("__typename") == "StatusContext" and c.get("state") == "SUCCESS")

        def label(c):
            if not isinstance(c, dict):
                return "?"
            name = c.get("name") or c.get("context") or "?"
            return f"{name}={c.get('conclusion') or c.get('state') or c.get('status') or '?'}"

        bad = [label(c) for c in checks if not green(c)]
        if bad:
            return failed(f"{len(bad)} of {len(checks)} checks not green: " + ", ".join(bad[:8])
                          + (f", +{len(bad) - 8} more" if len(bad) > 8 else ""))
        refusal = (_review_reply_refusal(number, data) or _approval_refusal(number, data, repo, cwd, env)
                   or _turn_safe(_turn_hold_refusal, number, data, repo, cwd, env))
        if refusal:
            return refusal
        drift = _base_drift_refusal(number, repo, data, cwd, env)
        if drift and "state commit(s) landed since then" in drift:
            # Green, approved, refused only for drift: take the land turn so the refreshed head's CI cycle
            # is the last one (overlapping merges and direct pushes wait for it).
            until = _turn_safe(_turn_acquire, number, data, repo, cwd, env)
            if until:
                drift += (f" PR #{number} now holds the state land turn until {until} (refs/{_TURN_PREFIX}"
                          f"{number}): overlapping state PR merges and direct state pushes wait for it, so "
                          "update-branch now and merge as soon as the new head's CI is green.")
        return drift or True
    except (ValueError, TypeError) as e:
        return failed(f"`gh pr view {number}` returned an unexpected shape ({type(e).__name__})")


SYNC_REQUIRED_CHECK = "All required checks pass (v2)"
SYNC_BRANCH_PREFIX = "sync/upstream-"


def _upstream_sync_merge(words, repo, cwd, env):
    """Narrow exception for an upstream-sync gate landing a tested sync PR into main.

    A sync PR must keep its merge commit (the upstream SHA becomes an ancestor of
    main), so the rebase-only ship path cannot land it. Admit only the exact form
    `pr merge N --repo R --merge --match-head-commit SHA [--subject S] [--body B]`
    whose live PR targets main from same-repo branch `sync/upstream-<SHA>` with the
    required aggregate check green on that head. Other checks may be red: branch
    protection stays the real enforcement. Any probe error fails closed.
    """
    if words[:2] != ["pr", "merge"]:
        return False
    args, options = [], {}
    i = 2
    while i < len(words):
        word = words[i]
        if word in {"--repo", "-R", "--match-head-commit", "--subject", "--body"}:
            key = "--repo" if word == "-R" else word
            if key in options or i + 1 >= len(words):
                return False
            options[key] = words[i + 1]
            i += 2
        elif word == "--merge" and word not in options:
            options[word] = True
            i += 1
        elif not word.startswith("-"):
            args.append(word)
            i += 1
        else:
            return False
    sha = options.get("--match-head-commit", "")
    if (len(args) != 1 or not re.fullmatch(r"[1-9][0-9]*", args[0])
            or options.get("--repo", "").lower() != repo
            or not options.get("--merge")
            or not re.fullmatch(r"[0-9a-f]{40}", sha)):
        return False
    fields = "number,url,state,baseRefName,headRefName,headRefOid,isCrossRepository,statusCheckRollup"
    probe_env = dict(env)
    probe_env["GH_PROMPT_DISABLED"] = "1"
    try:
        p = _run_probe(["gh", "pr", "view", args[0], "--repo", repo, "--json", fields],
                           cwd=_probe_cwd(cwd), env=probe_env, capture_output=True, text=True, timeout=15)
        if p.returncode != 0:
            return False
        data = json.loads(p.stdout)
        if not isinstance(data, dict):
            return False
        if (data.get("number") != int(args[0]) or data.get("state") != "OPEN"
                or data.get("baseRefName") != "main"
                or data.get("headRefOid") != sha
                or data.get("headRefName") != SYNC_BRANCH_PREFIX + sha
                or data.get("isCrossRepository") is not False
                or not isinstance(data.get("url"), str)
                or data["url"].lower() != f"https://github.com/{repo}/pull/{args[0]}"):
            return False
        checks = data.get("statusCheckRollup")
        if not isinstance(checks, list) or not all(isinstance(c, dict) for c in checks):
            return False
        # Every entry naming the required check must be green; at least one must exist.
        named = [c for c in checks if (c.get("name") if c.get("__typename") == "CheckRun"
                                       else c.get("context")) == SYNC_REQUIRED_CHECK]
        return bool(named) and all(
            c.get("__typename") == "CheckRun" and c.get("status") == "COMPLETED"
            and c.get("conclusion") == "SUCCESS" or
            c.get("__typename") == "StatusContext" and c.get("state") == "SUCCESS"
            for c in named)
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        return False


def _gh(words, cwd, env, repos):
    # gh's repository flag may occur before, between, or after command words.
    rest, selected, i = [], None, 0
    value_flags = {"--hostname", "-H", "--method", "-X", "--field", "-F", "--raw-field", "-f",
                   "--header", "--input", "--jq", "-q", "--template", "-t", "--match-head-commit",
                   "--subject", "--body", "--body-file", "--author-email"}
    while i < len(words):
        w = words[i]
        if w in {"-R", "--repo"} and i + 1 < len(words):
            selected = _slug(words[i + 1]); i += 2; continue
        if w.startswith("--repo=") or (w.startswith("-R") and len(w) > 2):
            selected = _slug(w.split("=", 1)[1] if "=" in w else w[2:]); i += 1; continue
        if w in value_flags:
            i += 2; continue
        if not w.startswith("-"):
            rest.append(w)
        i += 1
    if len(rest) >= 2 and rest[:2] == ["pr", "merge"]:
        url = next((re.fullmatch(r"https?://[^/]+/([^/]+/[^/]+)/pull/\d+/?", w) for w in rest[2:]
                    if re.fullmatch(r"https?://[^/]+/([^/]+/[^/]+)/pull/\d+/?", w)), None)
        repo = _slug(url[1]) if url else selected if selected is not None else _context(cwd, env)
        if repo in repos:
            state = _state_merge(words, repo, cwd, env)
            if isinstance(state, str):
                return state
            if state or _upstream_sync_merge(words, repo, cwd, env):
                return None
        return _deny(repo, "gh pr merge (state requires explicit repo, numeric PR, --rebase, "
                     "--match-head-commit FULL_SHA and readable green exact-head state preflight; "
                     f"an upstream sync PR requires --merge, head branch {SYNC_BRANCH_PREFIX}FULL_SHA "
                     f"into main and a green \"{SYNC_REQUIRED_CHECK}\")", repos)
    if rest and rest[0] == "api":
        for w in rest[1:]:
            match = re.fullmatch(r"(?:https?://[^/]+/)?/?repos/([^/]+/[^/]+)/(?:pulls/\d+/merge|merges)/?(?:\?.*)?", w)
            if match:
                return _deny(_slug(match[1]), "gh api merge endpoint", repos)
    return None


def _main_ref(ref):
    return ref.lstrip("+") in {"main", "refs/heads/main"}


def _push(words, cwd, repos, config):
    args, i = [], 0
    value_flags = {"--repo", "--receive-pack", "--exec", "-o", "--push-option"}
    remote_opt = None
    while i < len(words):
        w = words[i]
        if w in value_flags and i + 1 < len(words):
            if w == "--repo":
                remote_opt = words[i + 1]
            i += 2; continue
        if w.startswith("--repo="):
            remote_opt = w.split("=", 1)[1]
        elif not w.startswith("-") or w == ":":
            args.append(w)
        i += 1
    branch = _git(cwd, "symbolic-ref", "--short", "HEAD")
    def cfg(key):
        return config[key] if key in config else _git(cwd, "config", "--get", key)
    remote = remote_opt or (args.pop(0) if args else None)
    remote = remote or cfg(f"branch.{branch}.pushRemote") or cfg("remote.pushDefault") or cfg(f"branch.{branch}.remote") or "origin"
    repo = _remote(cwd, remote, push=True)
    if repo not in repos:
        return None
    if "--dry-run" in words or "-n" in words:
        return None
    if "--all" in words or "--mirror" in words:
        return _deny(repo, "git push updating main", repos)
    refs = args
    if not refs and "--tags" in words:
        return None  # tag-only push does not apply default branch refspecs
    if not refs:
        refs = cfg(f"remote.{remote}.push").splitlines()
    if not refs:
        mode = cfg("push.default") or "simple"
        if mode == "nothing":
            return None
        if mode == "matching":
            return _deny(repo, "git push potentially updating main", repos)
        target = cfg(f"branch.{branch}.merge") if mode in {"upstream", "simple"} else branch
        refs = [target or branch]
    for ref in refs:
        target = ref.lstrip("+").split(":", 1)[-1]
        if _main_ref(target) or (ref.lstrip("+") == "HEAD" and branch == "main") or target == "" or "*" in target:
            return _deny(repo, "git push updating main", repos)
    return None


def check_argv(argv, cwd=None, repos=None, env=None, _depth=0):
    """Check a literal executable argv, with optional cwd/environment snapshot."""
    if _depth > 12 or not isinstance(argv, (list, tuple)) or not all(isinstance(x, str) for x in argv):
        return None
    env = dict(os.environ if env is None else env)
    repos = load_repos(env) if repos is None else {x.lower() for x in repos}
    cwd = os.path.abspath(cwd or env.get("TERMINAL_CWD") or os.getcwd())
    words = list(argv)
    while words and (m := ASSIGN.fullmatch(words[0])):
        env[m[1]] = m[2]; words.pop(0)
    if not words or not repos:
        return None
    exe = os.path.basename(words.pop(0))
    if exe == "env":
        while words:
            if words[0] in {"-u", "--unset"} and len(words) > 1:
                env.pop(words[1], None); words = words[2:]
            elif words[0] in {"-i", "--ignore-environment", "-"}:
                env = {}; words.pop(0)
            elif words[0] in {"-C", "--chdir"} and len(words) > 1:
                cwd = _path(words[1], cwd, env); words = words[2:]
            elif words[0] == "--":
                words.pop(0); break
            else:
                break
        return check_argv(words, cwd, repos, env, _depth + 1)
    if exe in {"command", "exec", "nohup", "sudo"}:
        while words and words[0] in {"--", "-n", "-E"}:
            words.pop(0)
        return check_argv(words, cwd, repos, env, _depth + 1)
    if exe in {"sh", "bash", "zsh", "dash"}:
        for i, w in enumerate(words[:-1]):
            if w.startswith("-") and "c" in w:
                return check_shell(words[i + 1], cwd, repos, env, _depth + 1)
    if re.fullmatch(r"python(?:\d+(?:\.\d+)?)?", exe) and "-c" in words:
        i = words.index("-c")
        if i + 1 < len(words):
            return check_python(words[i + 1], cwd, repos, env, _depth + 1)
    if env.get(MARKER) == "1":
        return None
    if exe == "gh":
        return _gh(words, cwd, env, repos)
    if exe == "git":
        config = {}
        while words and words[0].startswith("-"):
            w = words.pop(0)
            if w in {"-C", "-c", "--work-tree", "--git-dir"} and words:
                value = words.pop(0)
                if w in {"-C", "--work-tree"}:
                    cwd = _path(value, cwd, env)
                elif w == "-c" and "=" in value:
                    k, v = value.split("=", 1); config[k] = v
            elif w.startswith("-C"):
                cwd = _path(w[2:], cwd, env)
        if words and words[0] == "push":
            return _push(words[1:], cwd, repos, config)
    return None


def _segments(text):
    """Split operators only outside quotes; quoted prose cannot become commands."""
    start, quote, escape, i = 0, None, False, 0
    while i < len(text):
        c = text[i]
        if escape:
            escape = False
        elif c == "\\" and quote != "'":
            escape = True
        elif c in "\"'" and (quote is None or quote == c):
            quote = c if quote is None else None
        elif quote is None and c == "#" and (i == 0 or text[i - 1].isspace()):
            end = text.find("\n", i)
            yield text[start:i]
            if end < 0:
                return
            start = end + 1; i = end
        elif quote is None and c in ";\n&|()":
            yield text[start:i]
            start = i + 1
        i += 1
    yield text[start:]


def check_shell(command, cwd=None, repos=None, env=None, _depth=0):
    env = dict(os.environ if env is None else env)
    repos = load_repos(env) if repos is None else repos
    cwd = os.path.abspath(cwd or env.get("TERMINAL_CWD") or os.getcwd())
    for segment in _segments(command):
        try:
            words = shlex.split(segment)
        except ValueError:
            continue
        # Only cwd is propagated. Command-prefix assignments, especially the land
        # marker, never leak to later commands in a compound expression.
        if words and words[0] in {"cd", "pushd"} and len(words) == 2:
            cwd = _path(words[1], cwd, env)
        why = check_argv(words, cwd, repos, env, _depth + 1)
        if why:
            return why
    return None


def _literal(node, bindings):
    if isinstance(node, ast.Name):
        return bindings.get(node.id)
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError):
        return None


def _source_nodes(node):
    yield node
    for child in ast.iter_child_nodes(node):
        yield from _source_nodes(child)


def check_python(code, cwd=None, repos=None, env=None, _depth=0):
    """Inspect literal subprocess/os.system/terminal/tool_call calls, not prose."""
    if _depth > 12:
        return None
    env = dict(os.environ if env is None else env)
    repos = load_repos(env) if repos is None else repos
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    bindings = {}
    # Visit source order. Only literal assignments are resolved, never eval/exec.
    for node in _source_nodes(tree):
        if isinstance(node, ast.Assign):
            value = _literal(node.value, bindings)
            for target in node.targets:
                if isinstance(target, ast.Name):
                    bindings[target.id] = value
        if not isinstance(node, ast.Call):
            continue
        name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else ""
        kwargs = {x.arg: _literal(x.value, bindings) for x in node.keywords if x.arg}
        first = _literal(node.args[0], bindings) if node.args else None
        call_cwd = kwargs.get("cwd") or kwargs.get("workdir") or cwd
        call_env = kwargs.get("env") if isinstance(kwargs.get("env"), dict) else env
        why = None
        if name in {"run", "Popen", "call", "check_call", "check_output", "system", "popen"}:
            value = first if first is not None else kwargs.get("args")
            if isinstance(value, (list, tuple)):
                why = check_argv(value, call_cwd, repos, call_env, _depth + 1)
            elif isinstance(value, str):
                # subprocess strings without shell=True are one executable name.
                if kwargs.get("shell") or name in {"system", "popen"}:
                    why = check_shell(value, call_cwd, repos, call_env, _depth + 1)
        elif name == "terminal":
            args = first if isinstance(first, dict) else {**kwargs, **({"command": first} if isinstance(first, str) else {})}
            why = check_tool("terminal", args, call_cwd, repos, call_env, _depth + 1)
        elif name == "tool_call":
            args = first if isinstance(first, dict) else kwargs
            why = check_tool("tool_call", args, call_cwd, repos, call_env, _depth + 1)
        if why:
            return why
    return None


def check_tool(name, args, cwd=None, repos=None, env=None, _depth=0):
    if _depth > 12 or not isinstance(args, dict):
        return None
    name = name.rsplit("__", 1)[-1].rsplit(".", 1)[-1]
    cwd = args.get("workdir") or cwd
    if name == "terminal":
        return check_shell(str(args.get("command") or ""), cwd, repos, env, _depth + 1)
    if name == "execute_code":
        return check_python(str(args.get("code") or ""), cwd, repos, env, _depth + 1)
    if name == "tool_call":
        # check_python passes non-literal values as None; only a literal list/tuple is inspectable.
        calls = args.get("calls") or []
        if not isinstance(calls, (list, tuple)):
            return None
        for call in calls:
            if isinstance(call, dict):
                why = check_tool(str(call.get("name", "")), call.get("arguments"), cwd, repos, env, _depth + 1)
                if why:
                    return why
    return None


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] != "--gh-args":
        print("usage: guard.py --gh-args <gh arguments...>", file=sys.stderr)
        return 2
    why = bounded_preflight(check_argv, ["gh", *argv[1:]])
    if why:
        print(why, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
