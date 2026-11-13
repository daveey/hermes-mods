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
        p = subprocess.run(["git", "--no-optional-locks", "-C", str(cwd), *args],
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


def _path(value, cwd):
    return os.path.abspath(os.path.join(cwd, os.path.expanduser(value)))


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


def _approval_refusal(number, data):
    """Refusal text unless the latest non-bot verdict on the exact head approves; else None.

    Evidence is a review whose commit is the head (state APPROVED/CHANGES_REQUESTED, a VERDICT line, or a
    first line that approves), or a PR comment naming the head (its first 10+ hex) with a VERDICT line.
    It proves an approval record exists, not that its author was independent (every agent posts as one
    account): independence stays the landing owner's duty.
    Why: #2413 merged 16 min after its head was pushed with no review or verdict at all; 19 state PRs merged
    2026-11-13 had no exact-head approval record and this preflight admitted every one."""
    head = str(data.get("headRefOid") or "")
    verdicts = []
    for r in data.get("reviews") or []:
        if _is_bot(r.get("author"), r.get("authorAssociation")):
            continue
        if not isinstance(r.get("commit"), dict) or r["commit"].get("oid") != head:
            continue
        state = r.get("state")
        verdict = ("approve" if state == "APPROVED" else "block" if state == "CHANGES_REQUESTED"
                   else _body_verdict(r.get("body")))
        if verdict:
            verdicts.append((_when(r.get("submittedAt")), verdict))
    for c in data.get("comments") or []:
        body = str(c.get("body") or "")
        if _is_bot(c.get("author"), c.get("authorAssociation")) or not re.search(
                rf"(?<![0-9a-f]){head[:10]}", body):
            continue
        verdict = _body_verdict(body, first_line_ok=False)
        if verdict is None:
            first = next((_flat(line) for line in body.splitlines() if line.strip()), "")
            if (re.search(rf"(?<![0-9a-f]){head[:10]}", first) and _REVIEW_NOUN.search(first)
                    and not _REPLY_LINE.search(first)):
                verdict = _body_verdict(first)
        if verdict:
            verdicts.append((_when(c.get("createdAt")), verdict))
    if any(t is None for t, _ in verdicts):
        return (f"[ship-only-guard] Refused state merge of PR #{number}: a review/verdict on head {head[:10]} has "
                "no readable time, so the latest exact-head verdict cannot be read. Rerun the same merge "
                "command once `gh pr view --json reviews,comments` works.")
    verdicts.sort()
    if verdicts and verdicts[-1][1] == "approve":
        return None
    why = ("its latest exact-head verdict requests changes" if verdicts
           else "it has no approval of its exact head (no review on that commit and no comment naming it "
                "with a VERDICT line)")
    return (f"[ship-only-guard] Refused state merge of PR #{number}: {why}. A state PR lands only after an "
            f"independent exact-head review of {head}: file a review card for that sha (its reviewer posts a "
            "review on that commit whose first line says approve, or `VERDICT: APPROVE`), fix any requested "
            "changes, then rerun the same merge command. A review of an earlier head does not count.")


def _state_merge(words, repo, cwd, env):
    """Narrow state-only exception, never a general non-main merge allowance.

    Returns True to allow, False when the command is not this route or any preflight
    fails, or a refusal string naming unanswered review points or a missing exact-head
    approval. Independence of the reviewer and required-CI coverage remain caller
    duties; this preflight refuses missing/non-green visible checks, refuses while any
    review whose body raises a non-blocking point ("nit", "non-blocking", un-negated)
    has no later non-bot PR comment marked <!-- hermes-review-reply -->: reply before
    you merge, and refuses unless the latest non-bot verdict on the exact head approves
    (`_approval_refusal`). Unreadable reviews/comments refuse. It is not an atomic base
    lock (gh only offers an atomic head fence).
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
    fields = "number,url,state,baseRefName,headRefOid,statusCheckRollup,reviews,comments"
    probe_env = dict(env)
    probe_env["GH_PROMPT_DISABLED"] = "1"
    try:
        p = subprocess.run(["gh", "pr", "view", args[0], "--repo", repo, "--json", fields],
                           cwd=cwd, env=probe_env, capture_output=True, text=True, timeout=15)
        if p.returncode != 0:
            return False
        data = json.loads(p.stdout)
        if not isinstance(data, dict):
            return False
        if (data.get("number") != int(args[0]) or data.get("state") != "OPEN"
                or data.get("baseRefName") != "state"
                or data.get("headRefOid") != options["--match-head-commit"]
                or not isinstance(data.get("url"), str)
                or data["url"].lower() != f"https://github.com/{repo}/pull/{args[0]}"):
            return False
        checks = data.get("statusCheckRollup")
        # gh's connection is bounded; never infer success from a possibly capped
        # rollup. Skipped/neutral/unknown checks conservatively require the ship path.
        if not isinstance(checks, list) or not 0 < len(checks) < 100:
            return False
        if not all(isinstance(c, dict) and (
            c.get("__typename") == "CheckRun" and c.get("status") == "COMPLETED"
            and c.get("conclusion") == "SUCCESS" or
            c.get("__typename") == "StatusContext" and c.get("state") == "SUCCESS"
        ) for c in checks):
            return False
        return _review_reply_refusal(args[0], data) or _approval_refusal(args[0], data) or True
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        return False


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
        p = subprocess.run(["gh", "pr", "view", args[0], "--repo", repo, "--json", fields],
                           cwd=cwd, env=probe_env, capture_output=True, text=True, timeout=15)
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
                cwd = _path(words[1], cwd); words = words[2:]
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
                    cwd = _path(value, cwd)
                elif w == "-c" and "=" in value:
                    k, v = value.split("=", 1); config[k] = v
            elif w.startswith("-C"):
                cwd = _path(w[2:], cwd)
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
            cwd = _path(words[1], cwd)
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
        for call in args.get("calls", []):
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
    why = check_argv(["gh", *argv[1:]])
    if why:
        print(why, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
