"""Refuse a cross-worktree stash swap.

``refs/stash`` is one ref per repository, shared by every linked worktree. Two workers that
``git stash`` / ``stash pop`` at the same time trade diffs: each pop applies the other's
patch. This module is the decision logic for the Hermes hooks in ``__init__.py``.

- Any ``git stash`` (push, pop, apply, list, no-arg, ...) inside a repo with more than one
  worktree gets a warning. ``list`` is included so a read-only command can prove the guard.
- ``git stash pop`` / ``apply`` is refused when that repo has more than one worktree and the
  stash's branch (parsed from ``WIP on <branch>:`` / ``On <branch>:``) is not the current
  branch. A missing or unparseable subject fails closed. Same-branch pop is allowed (the
  warning still fires). A single-worktree repo is left alone: stash is the normal undo there.

``STASH_WORKTREE_GUARD=off`` in the command, or ``STASH_WORKTREE_GUARD_DISABLE=1`` in the
environment, skips both paths.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from typing import Optional

MARK = "[stash-worktree-guard]"
SEPARATORS = re.compile(r"&&|\|\||;|\n|\|")
STASH_SUBS = {
    "push", "save", "pop", "apply", "list", "show", "drop", "clear",
    "branch", "create", "store", "export", "import",
}
REFUSE_OPS = {"pop", "apply"}
WRAPPERS = {"sudo", "env", "command", "exec", "time", "nice", "nohup", "stdbuf"}
SHELLS = {"bash", "sh", "zsh", "dash"}
_STASH_BRANCH = re.compile(r"^(?:WIP on|On|index on) (.+?): ")
_TIMEOUT_FLAG = re.compile(r"^-[a-zA-Z]")


def enabled() -> bool:
    if os.environ.get("STASH_WORKTREE_GUARD_DISABLE") == "1":
        return False
    return os.environ.get("STASH_WORKTREE_GUARD") != "off"


def _bypassed(cmd: str) -> bool:
    return not enabled() or "STASH_WORKTREE_GUARD=off" in (cmd or "")


def _resolve(p: str, base: Optional[str] = None) -> str:
    p = os.path.expanduser(os.path.expandvars(p))
    if base and not os.path.isabs(p):
        p = os.path.join(base, p)
    return os.path.realpath(p)


def _git_env() -> dict:
    env = os.environ.copy()
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        env.pop(key, None)
    return env


def _run_git(repo: str, args: list[str]) -> Optional[subprocess.CompletedProcess]:
    try:
        return subprocess.run(
            ["git", "-C", repo, *args], capture_output=True, text=True, timeout=5, env=_git_env(),
        )
    except (OSError, subprocess.SubprocessError):
        return None


def worktree_count(repo: str) -> int:
    """How many worktrees this repo has. 0 if ``repo`` is not a checkout we can read."""
    proc = _run_git(repo, ["worktree", "list", "--porcelain"])
    if proc is None or proc.returncode != 0:
        return 0
    return sum(1 for line in proc.stdout.splitlines() if line.startswith("worktree "))


def current_branch(repo: str) -> str:
    proc = _run_git(repo, ["symbolic-ref", "-q", "--short", "HEAD"])
    if proc is not None and proc.returncode == 0 and proc.stdout.strip():
        return proc.stdout.strip()
    return "HEAD"


def stash_subject(repo: str, ref: str = "stash@{0}") -> Optional[str]:
    proc = _run_git(repo, ["log", "-1", "--format=%s", ref])
    if proc is None or proc.returncode != 0 or not proc.stdout.strip():
        return None
    return proc.stdout.strip()


def stash_branch(subject: str) -> Optional[str]:
    match = _STASH_BRANCH.match(subject or "")
    return match.group(1) if match else None


def _skip_wrappers(words: list[str]) -> list[str]:
    while words and words[0] in WRAPPERS:
        words = words[1:]
    if words and os.path.basename(words[0]) == "timeout":
        words = words[1:]
        while words and (_TIMEOUT_FLAG.match(words[0]) or re.match(r"^[0-9]", words[0])):
            # `timeout 60`, `timeout -k 5 60`, `timeout --signal=TERM 60`
            if words[0] in ("-k", "--kill-after", "-s", "--signal") and len(words) > 1:
                words = words[2:]
            else:
                words = words[1:]
    return words


def _git_calls(cmd: str, cwd: str, depth: int = 0):
    """Yield (repo dir, git-subcommand, args) for each git invocation, following cd and git -C."""
    if depth > 3:
        return
    for seg in SEPARATORS.split(cmd or ""):
        try:
            words = shlex.split(seg, comments=True)
        except ValueError:
            words = seg.split()
        while words and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[0]):
            words = words[1:]
        words = _skip_wrappers(words)
        if not words:
            continue
        base = os.path.basename(words[0])
        if base in SHELLS and "-c" in words:
            idx = words.index("-c")
            if idx + 1 < len(words):
                yield from _git_calls(words[idx + 1], cwd, depth + 1)
            continue
        if base in ("cd", "pushd") and len(words) > 1:
            cwd = _resolve(words[1], cwd)
            continue
        if base != "git":
            continue
        repo, i = cwd, 1
        while i < len(words) and words[i].startswith("-"):
            opt = words[i]
            if opt == "-C" and i + 1 < len(words):
                repo = _resolve(words[i + 1], repo)
                i += 2
            elif opt.startswith("--git-dir=") or opt.startswith("--work-tree="):
                i += 1
            elif opt in ("-c", "--git-dir", "--work-tree") and i + 1 < len(words):
                i += 2
            else:
                i += 1
        if i < len(words):
            yield repo, words[i], words[i + 1:]


def stash_op(args: list[str]) -> Optional[str]:
    """``push`` for no-arg / ``git stash -q``, the subcommand name, or None for ``--help``."""
    if "-h" in args or "--help" in args:
        return None
    positional = [a for a in args if not a.startswith("-")]
    if not positional:
        return "push"
    if positional[0] in STASH_SUBS:
        return positional[0]
    return None


def stash_ref(args: list[str]) -> str:
    for arg in args:
        if arg.startswith("stash@") or arg.startswith("refs/stash"):
            return arg
    return "stash@{0}"


def _stash_invocations(cmd: str, cwd: Optional[str]):
    if "stash" not in (cmd or "") or "git" not in (cmd or ""):
        return
    base = _resolve(cwd or os.getcwd())
    for repo, sub, args in _git_calls(cmd, base):
        if sub != "stash":
            continue
        op = stash_op(args)
        if op:
            yield repo, op, args


def _safe_recipe() -> str:
    return (
        'Save a patch instead: `git diff -- <files> > "$TMPDIR/fix.patch" && git checkout -- <files>`; '
        "rerun the tests; `git apply \"$TMPDIR/fix.patch\"`. Or `git worktree add --detach` a baseline. "
        "Do not work around this guard."
    )


def warning_text(repo: str, count: int) -> str:
    shown = repo.replace(str(os.path.expanduser("~")), "~", 1)
    return (
        f"{MARK} `refs/stash` is one ref shared by all {count} worktrees of {shown}. "
        "A stash/pop here can apply another worker's diff and hand them yours. " + _safe_recipe()
    )


def warning_for(cmd: str, cwd: Optional[str] = None) -> Optional[str]:
    """Warning suffix for a stash command in a multi-worktree repo, else None."""
    if _bypassed(cmd):
        return None
    seen = set()
    for repo, _op, _args in _stash_invocations(cmd, cwd):
        if repo in seen:
            continue
        seen.add(repo)
        count = worktree_count(repo)
        if count > 1:
            return warning_text(repo, count)
    return None


def refuse_reason(cmd: str, cwd: Optional[str] = None) -> Optional[str]:
    """Why ``pop``/``apply`` must be blocked, or None to let it run."""
    if _bypassed(cmd):
        return None
    for repo, op, args in _stash_invocations(cmd, cwd):
        if op not in REFUSE_OPS:
            continue
        count = worktree_count(repo)
        if count <= 1:
            continue
        ref = stash_ref(args)
        subject = stash_subject(repo, ref)
        if subject is None:
            continue  # no such stash: pop fails on its own, nothing to swap
        branch = stash_branch(subject)
        here = current_branch(repo)
        if branch == here:
            continue
        shown = repo.replace(str(os.path.expanduser("~")), "~", 1)
        whose = f"branch `{branch}`" if branch else "a subject that does not name a branch"
        return (
            f"STASH WORKTREE GUARD: blocked `git stash {op}` in {shown}. "
            f"The stash `{ref}` says {whose} ({subject!r}), but this worktree is on `{here}`. "
            f"`refs/stash` is shared by all {count} worktrees, so this would apply another worker's diff. "
            + _safe_recipe()
        )
    return None


def check_python(code: str, cwd: Optional[str] = None) -> Optional[str]:
    """Refuse path for shell strings and git argv lists inside Python (execute_code)."""
    if _bypassed(code):
        return None
    for lit in re.findall(r"""(?:"([^"\n]*\bgit\b[^"\n]*stash[^"\n]*)"|'([^'\n]*\bgit\b[^'\n]*stash[^'\n]*)')""", code or ""):
        why = refuse_reason(lit[0] or lit[1], cwd)
        if why:
            return why
    for match in re.finditer(r"\[\s*[\"']git[\"'](?:\s*,\s*[\"'][^\"']*[\"'])+\s*\]", code or ""):
        words = re.findall(r"[\"']([^\"']*)[\"']", match.group(0))
        why = refuse_reason(shlex.join(words), cwd)
        if why:
            return why
    return None
