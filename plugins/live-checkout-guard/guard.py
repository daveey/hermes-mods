"""live-checkout-guard core: decide whether a tool call would mutate the protected live checkout in place.

Pure functions, no Hermes imports, shared by the Hermes plugin (__init__.py) and the Claude Code hook.
Each ``check_*`` returns None (allow) or a short reason string (refuse).

Protected root: ``$LIVE_CHECKOUT_ROOT`` (default ``~/code/hermes-mods``), compared after ``realpath`` so symlinks
into it (e.g. ``~/.hermes/plugins/<name>``) count as inside it. Kill switch: ``LIVE_CHECKOUT_GUARD=0``.

Best-effort lexical analysis, not a sandbox. See README.md for limitations.
"""

from __future__ import annotations

import ast
import os
import re
import shlex
import subprocess
from pathlib import Path
from typing import Iterable, Optional

DEFAULT_ROOT = "~/code/hermes-mods"

GIT_BLOCKED = {"commit", "stash", "rebase", "reset", "checkout", "restore", "apply", "am", "cherry-pick",
               "merge", "switch", "pull", "revert", "clean"}
GIT_STASH_READ = {"list", "show"}
# commands whose every path argument is written/removed
WRITE_ALL = {"rm", "mv", "touch", "truncate", "rmdir", "mkdir", "chmod", "unlink"}
# wrapper -> options that take a value (as the next word, ``--opt=value`` or attached ``-nVALUE``)
WRAPPER_OPTS = {
    "sudo": {"-u", "-g", "-p", "-C", "-U", "-r", "-t", "-T", "-D", "-h", "--user", "--group", "--prompt",
             "--close-from", "--other-user", "--role", "--type", "--command-timeout", "--chdir", "--host"},
    "env": {"-u", "-C", "-S", "--unset", "--chdir", "--split-string"},
    "nice": {"-n", "--adjustment"},
    "ionice": {"-c", "-n", "-p", "-P", "-u", "--class", "--classdata"},
    "time": {"-f", "-o", "--format", "--output"},
    "caffeinate": {"-t", "-w"},
    "exec": {"-a"},
    "timeout": {"-s", "-k", "--signal", "--kill-after"},
    "stdbuf": {"-i", "-o", "-e", "--input", "--output", "--error"},
    "nohup": set(), "command": set(), "builtin": set(),
}
WRAPPERS = set(WRAPPER_OPTS)
WRAPPER_POSITIONALS = {"timeout": 1}  # `timeout DURATION cmd`
CHDIR_OPTS = {"env": {"-C", "--chdir"}, "sudo": {"-D", "--chdir"}}
SHELLS = {"bash", "sh", "zsh", "dash", "ksh"}
SEPARATORS = {";", "&&", "||", "|", "&", "|&", "(", ")", ";;"}
FILE_MUTATORS = {"write_file", "patch"}


def enabled() -> bool:
    return os.environ.get("LIVE_CHECKOUT_GUARD", "1").strip().lower() not in {"0", "false", "off", "no"}


def root() -> str:
    return os.path.realpath(os.path.expanduser(os.environ.get("LIVE_CHECKOUT_ROOT") or DEFAULT_ROOT))


def root_label() -> str:
    return os.environ.get("LIVE_CHECKOUT_ROOT") or DEFAULT_ROOT


def in_root(path: str, cwd: Optional[str] = None) -> bool:
    """True if ``path`` (relative to ``cwd``) resolves, through symlinks, to the protected root or below."""
    if not path:
        return False
    p = os.path.expanduser(path)
    if not os.path.isabs(p):
        p = os.path.join(cwd or os.getcwd(), p)
    r = os.path.realpath(p)
    base = root()
    return r == base or r.startswith(base + os.sep)


def message(why: str) -> str:
    if why == "unfenced hermes update":
        return ("[live-checkout-guard] Refused: unfenced hermes update. Core updates go through each "
                "host's fenced route: the hub's hermes-core-update.timer (every 10 min, drain-first, "
                "batched) and hermes-sync's drain windows on the Macs. To wait for a commit, schedule "
                "the card with a wake check like `ssh <hub> 'git -C ~/.hermes/hermes-agent "
                "merge-base --is-ancestor <sha> HEAD'`. Read-only update flags are allowed. "
                "Escape hatch: prefix the command with HERMES_UPDATE_OK=1 for a user-approved update only.")
    r = root_label()
    return (f"[live-checkout-guard] Refused: {why}.\n"
            f"{r} is the live checkout that the sync job and other agents load from; edit it in place and "
            "you break their view of it. Work in a detached worktree instead:\n"
            f"  git -C {r} fetch origin\n"
            f"  git -C {r} worktree add --detach ~/code/wt-$(date +%s) origin/main   # any path outside {r}\n"
            "  cd <that worktree>; edit, test, git commit\n"
            "  git push origin HEAD:main            # publish; the sync job fast-forwards the live checkout\n"
            f"  git -C {r} worktree remove <that worktree>   # when done\n"
            f"Allowed on the live checkout: reads, git fetch, git worktree add, a clean `git merge --ff-only "
            "origin/main`, hermes-sync, skill_manage.")


# ---- paths: patch / write_file / editor tools --------------------------------------------------

_V4A = re.compile(r"^\*\*\* (?:Update|Add|Delete) File:\s*(.+?)\s*$|^\*\*\* Move to:\s*(.+?)\s*$", re.M)


def check_file_tool(tool: str, args: dict, cwd: Optional[str] = None) -> Optional[str]:
    cwd = args.get("workdir") or cwd
    paths = [args.get(k) for k in ("path", "file_path", "notebook_path") if isinstance(args.get(k), str)]
    if isinstance(args.get("patch"), str):
        for m in _V4A.finditer(args["patch"]):
            paths.append(m.group(1) or m.group(2))
    for p in paths:
        if in_root(p, cwd):
            return f"{tool} would write {p}, inside the live checkout"
    return None


# ---- shell -------------------------------------------------------------------------------------

def _tokens(cmd: str) -> list[str]:
    cmd = cmd.replace("\r", "")
    cmd = re.sub(r"\n", " ; ", cmd)  # heredoc bodies get scanned as commands too (documented over-approximation)
    try:
        lex = shlex.shlex(cmd, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        lex.commenters = ""
        return list(lex)
    except ValueError:
        return cmd.split()


def _segments(toks: list[str]) -> list[list[str]]:
    out, cur = [], []
    for t in toks:
        if t in SEPARATORS or set(t) <= set(";&|()") and t:
            if cur:
                out.append(cur)
            cur = []
        else:
            cur.append(t)
    if cur:
        out.append(cur)
    return out


def _redirect_targets(seg: list[str]) -> tuple[list[str], list[str]]:
    """Split a segment into (words, redirect targets that are written)."""
    words, targets, i = [], [], 0
    while i < len(seg):
        t = seg[i]
        m = re.match(r"^(\d*)(&>>?|>&|>\||>>?)(.*)$", t)
        if m and not t.startswith("<"):
            op, rest = m.group(2), m.group(3)
            if not rest and i + 1 < len(seg):
                rest = seg[i + 1]
                i += 1
            if op == ">&" and (rest.isdigit() or rest == "-"):
                pass
            elif rest:
                targets.append(rest)
        elif re.match(r"^\d*<", t) and not t.startswith("<<"):
            if re.match(r"^\d*<$", t):
                i += 1  # input redirect target: read only
        else:
            words.append(t)
        i += 1
    return words, targets


def _git_clean(d: str) -> bool:
    try:
        out = subprocess.run(["git", "-C", d, "status", "--porcelain", "--untracked-files=all"], capture_output=True,
                             text=True, timeout=10)
    except Exception:
        return False
    return out.returncode == 0 and not out.stdout.strip()


def _join(cwd: str, p: str) -> str:
    p = os.path.expanduser(p)
    return p if os.path.isabs(p) else os.path.join(cwd, p)


def _check_git(args: list[str], cwd: str, git_env: Optional[dict] = None) -> Optional[str]:
    """``git_env``: GIT_DIR / GIT_WORK_TREE assignments that prefixed the command (relative to ``cwd``)."""
    git_env = git_env or {}
    d, i = cwd, 0
    git_dir = _join(cwd, git_env["GIT_DIR"]) if git_env.get("GIT_DIR") else None
    work_tree = _join(cwd, git_env["GIT_WORK_TREE"]) if git_env.get("GIT_WORK_TREE") else None
    while i < len(args) and args[i].startswith("-"):
        a = args[i]
        if a == "-C" and i + 1 < len(args):
            d = _join(d, args[i + 1])
            i += 1
        elif a in {"--git-dir", "--work-tree"} and i + 1 < len(args):
            if a == "--git-dir":
                git_dir = _join(d, args[i + 1])
            else:
                work_tree = _join(d, args[i + 1])
            i += 1
        elif a.startswith("--git-dir="):
            git_dir = _join(d, a.split("=", 1)[1])
        elif a.startswith("--work-tree="):
            work_tree = _join(d, a.split("=", 1)[1])
        elif a in {"-c", "--namespace", "--exec-path"}:
            i += 1
        i += 1
    if i >= len(args):
        return None
    sub, rest = args[i], args[i + 1:]
    via = ""
    if git_dir and in_root(git_dir):
        via = " (via --git-dir/GIT_DIR)"
    elif work_tree and in_root(work_tree):
        via = " (via --work-tree/GIT_WORK_TREE)"
    elif not in_root(d) or git_dir or work_tree:
        return None  # an explicit git dir / work tree elsewhere wins over the cwd
    clean_dir = d if in_root(d) else root()
    if sub == "update-ref":
        # stdin can name arbitrary refs; never infer safety from shell input.
        if "--stdin" in rest or any(
                a == "refs/stash" or a.startswith("refs/hermes-retire/") for a in rest):
            return f"raw git update-ref on protected stash/retirement refs in the live checkout{via}"
    if sub == "reflog" and rest and rest[0] in {"delete", "expire", "drop", "write"}:
        if "--all" in rest or any(
                a == "stash" or a.startswith("stash@{") or a == "refs/stash"
                or a.startswith("refs/stash@{") or a.startswith("refs/hermes-retire/") for a in rest[1:]):
            return f"raw git reflog mutation on protected stash/retirement refs in the live checkout{via}"
    if sub == "stash":
        return None if rest and rest[0] in GIT_STASH_READ else f"git stash in the live checkout{via}"
    if sub == "pull":  # only a clean `git merge --ff-only origin/main` may update the live checkout
        return f"git pull in the live checkout{via} (use `git fetch origin && git merge --ff-only origin/main`)"
    if sub == "merge":
        refs = [a for a in rest if not a.startswith("-")]
        if "--ff-only" not in rest:
            return f"git merge without --ff-only in the live checkout{via}"
        if refs != ["origin/main"]:
            return f"git merge --ff-only is only allowed for origin/main in the live checkout{via}"
        if not _git_clean(clean_dir):
            return "git merge --ff-only with uncommitted changes in the live checkout (dirty or untracked files)"
        return None
    if sub in GIT_BLOCKED:
        return f"git {sub} in the live checkout{via}"
    return None


def _strip_wrappers(words: list[str], cwd: str) -> tuple[list[str], str, dict]:
    """Drop env assignments and wrapper commands (with their option values). Returns (words, cwd, git_env);
    ``env -C DIR`` / ``sudo -D DIR`` move the cwd for this command only."""
    git_env: dict = {}
    while words:
        w = words[0]
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", w, re.S)
        if m:
            if m.group(1) in {"GIT_DIR", "GIT_WORK_TREE", "HERMES_UPDATE_OK"}:
                git_env[m.group(1)] = m.group(2)
            words = words[1:]
            continue
        name = os.path.basename(w)
        if name not in WRAPPERS:
            break
        opts, chdir = WRAPPER_OPTS[name], CHDIR_OPTS.get(name, set())
        words = words[1:]
        while words and words[0].startswith("-") and words[0] != "-":
            f = words.pop(0)
            if f == "--":
                break
            val = None
            if f.startswith("--") and "=" in f:
                f, val = f.split("=", 1)
            elif f in opts:
                val = words.pop(0) if words else None
            elif not f.startswith("--") and f[:2] in opts and len(f) > 2:
                f, val = f[:2], f[2:]
            if val is not None and f in chdir:
                cwd = _join(cwd, val)
            if val is not None and name == "env" and f in {"-S", "--split-string"}:
                words = _tokens(val) + words  # env -S 'cmd args': the value is the command
        words = words[WRAPPER_POSITIONALS.get(name, 0):]
    return words, cwd, git_env


def _shell_c_script(args: list[str]) -> Optional[str]:
    """The command string of ``bash -c``, ``bash -lc``, ``bash -l -c``, ``bash -o pipefail -c`` ..., else None."""
    seen_c, i = False, 0
    while i < len(args):
        a = args[i]
        if a in {"-o", "+o", "-O", "+O", "--rcfile", "--init-file"}:
            i += 2
            continue
        if a == "--":
            i += 1
            break
        if a.startswith("--"):
            i += 1
            continue
        if len(a) > 1 and a[0] in "-+":
            seen_c = seen_c or (a[0] == "-" and "c" in a[1:])
            i += 1
            continue
        break
    return args[i] if seen_c and i < len(args) else None


def _in_root_nofollow(path: str, cwd: str) -> bool:
    """Like in_root, but a final symlink component is not followed unless it points at a directory
    (where tools like ln create the new entry inside it)."""
    p = _join(cwd, path)
    if os.path.isdir(p):
        return in_root(p)
    parent, base = os.path.split(os.path.normpath(p))
    r = os.path.join(os.path.realpath(parent or "."), base)
    b = root()
    return r == b or r.startswith(b + os.sep)


def _ln_dest(args: list[str], cwd: str) -> Optional[str]:
    tdir, ops, i, opts_done = None, [], 0, False
    while i < len(args):
        a = args[i]
        if not opts_done and a == "--":
            opts_done = True
        elif not opts_done and a.startswith("--target-directory="):
            tdir = a.split("=", 1)[1]
        elif not opts_done and a == "--target-directory" and i + 1 < len(args):
            tdir, i = args[i + 1], i + 1
        elif not opts_done and a.startswith("-") and len(a) > 1 and not a.startswith("--"):
            k = a.find("t")
            if k > 0:
                tdir = a[k + 1:] or (args[i + 1] if i + 1 < len(args) else None)
                i += 0 if a[k + 1:] else 1
        elif not opts_done and a.startswith("--"):
            pass
        else:
            ops.append(a)
        i += 1
    if tdir:
        return tdir
    if len(ops) >= 2:
        return ops[-1]
    return "." if ops else None


def _check_segment(words: list[str], targets: list[str], cwd: str) -> tuple[Optional[str], str]:
    """Returns (reason, new_cwd)."""
    for t in targets:
        if in_root(t, cwd):
            return f"output redirection to {t}, inside the live checkout", cwd
    seg_cwd = cwd
    words, cwd, git_env = _strip_wrappers(words, cwd)
    if not words:
        return None, seg_cwd
    why, new_cwd = _check_words(words, cwd, git_env)
    # env -C / sudo -D move the cwd for that one command only; cd/pushd move it for later segments
    return why, (new_cwd if os.path.basename(words[0]) in {"cd", "pushd"} else seg_cwd)


def _check_words(words: list[str], cwd: str, git_env: dict) -> tuple[Optional[str], str]:
    cmd = os.path.basename(words[0])
    args = words[1:]
    if cmd in {"cd", "pushd"}:
        tgt = os.path.expanduser(args[0]) if args and not args[0].startswith("-") else (
            os.path.expanduser(args[1]) if len(args) > 1 else os.path.expanduser("~"))
        return None, os.path.normpath(tgt if os.path.isabs(tgt) else os.path.join(cwd, tgt))
    if cmd == "hermes-sync":
        return None, cwd
    if cmd in SHELLS:
        script = _shell_c_script(args)
        if script is not None:
            return check_shell(script, cwd, _update_checked=True), cwd
    if cmd == "eval" and args:
        return check_shell(" ".join(args), cwd, _update_checked=True), cwd
    if cmd == "git":
        return _check_git(args, cwd, git_env), cwd
    if cmd == "ln":  # only the link being created matters; a source inside the root is just read
        dest = _ln_dest(args, cwd)
        if dest is not None and _in_root_nofollow(dest, cwd):
            return f"ln creates a link inside the live checkout ({dest})", cwd
        return None, cwd
    files = [a for a in args if not a.startswith("-")]
    if cmd in {"sed", "gsed"} and any(re.match(r"^-[a-zA-Z]*i|^--in-place", a) for a in args):
        for f in files[1:] if files else []:  # files[0] may be the script
            if in_root(f, cwd):
                return f"in-place sed on {f}, inside the live checkout", cwd
        for f in files[:1]:
            if in_root(f, cwd) and not any(a == "-e" for a in args) and len(files) == 1:
                return f"in-place sed on {f}, inside the live checkout", cwd
    if cmd == "perl" and any(re.match(r"^-[a-zA-Z]*i", a) for a in args):
        for f in files:
            if in_root(f, cwd):
                return f"in-place perl edit of {f}, inside the live checkout", cwd
    if cmd == "tee":
        for f in files:
            if in_root(f, cwd):
                return f"tee writes {f}, inside the live checkout", cwd
    if cmd in WRITE_ALL:
        for f in files:
            if in_root(f, cwd):
                return f"{cmd} on {f}, inside the live checkout", cwd
    if cmd in {"cp", "install", "rsync"} and files and in_root(files[-1], cwd):
        return f"{cmd} into the live checkout ({files[-1]})", cwd
    return None, cwd


# Flags verified against `hermes update --help`; none of the backup/restart flags is read-only.
UPDATE_READ_ONLY = {"-h", "--help", "--check", "--plan", "--list-venv-holders", "--install-id"}
SSH_VALUE_OPTIONS = set("BbcDEeFIiJLlmOopPQRSWw")


def _update_tokens(source: str) -> list[str]:
    # Preserve quoted newlines as data; turn only unquoted command boundaries into
    # semicolons. shlex otherwise consumes the newline terminating a comment.
    cooked, quote, escaped, comment = [], "", False, False
    for char in source:
        if comment:
            if char == "\n":
                cooked.append(" ; ")
                comment = False
            continue
        if escaped:
            cooked.append(char)
            escaped = False
            continue
        if char == "\\" and quote != "'":
            cooked.append(char)
            escaped = True
        elif char in {"'", '"'} and (not quote or quote == char):
            quote = "" if quote else char
            cooked.append(char)
        elif char == "#" and not quote and (not cooked or cooked[-1][-1:].isspace()):
            comment = True
        elif char == "\n" and not quote:
            cooked.append(" ; ")
        else:
            cooked.append(char)
    lex = shlex.shlex("".join(cooked), posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    lex.commenters = ""
    try:
        return list(lex)
    except ValueError:
        return _tokens(source)


def _update_source(source: str) -> str:
    """Remove heredoc data, not following commands. This pass is update-only: existing
    checkout-write analysis deliberately retains its conservative heredoc behavior."""
    lines, pending = [], []
    for line in source.splitlines(keepends=True):
        if pending:
            delimiter, strip_tabs, execute = pending[0]
            candidate = line.rstrip("\r\n")
            if (candidate.lstrip("\t") if strip_tabs else candidate) == delimiter:
                pending.pop(0)
            elif execute:
                lines.append(line)
            continue
        lines.append(line)
        toks = _update_tokens(line)
        words, _, assignments = _strip_wrappers(toks[:], os.getcwd())
        execute = bool(words and os.path.basename(words[0]) in SHELLS | {"ssh"}
                       and assignments.get("HERMES_UPDATE_OK") != "1")
        for i, token in enumerate(toks[:-1]):
            if token == "<<" and "$((" not in line:
                delimiter = toks[i + 1]
                pending.append((delimiter.lstrip("-"), delimiter.startswith("-"), execute))
    return "".join(lines)


def _ssh_script(args: list[str]) -> str:
    """SSH concatenates remote argv with spaces; options before the host belong to SSH."""
    i, host_seen = 0, False
    while i < len(args):
        arg = args[i]
        if arg == "--":
            i += 1
            if host_seen:
                break
            continue
        if not arg.startswith("-") or arg == "-":
            if host_seen:
                break
            host_seen = True
            i += 1
            continue
        for j, flag in enumerate(arg[1:], 1):
            if flag in SSH_VALUE_OPTIONS:
                if j == len(arg) - 1:
                    i += 1
                break
        i += 1
    return " ".join(args[i:])


def _check_update(source: str, cwd: str, approved: bool = False, depth: int = 0) -> Optional[str]:
    """Inspect command positions, not arbitrary strings mentioning an update.
    Explicit approval is scoped to one command and its nested shell/SSH payload."""
    if depth > 20:
        return None
    for seg in _segments(_update_tokens(_update_source(source))):
        words, _ = _redirect_targets(seg)
        while words and words[0] in {"{", "!", "then", "do", "else", "elif", "if", "while", "until"}:
            words = words[1:]
        words, _, assignments = _strip_wrappers(words, cwd)
        if not words:
            continue
        allowed = assignments.get("HERMES_UPDATE_OK", "1" if approved else "0") == "1"
        cmd, args = os.path.basename(words[0]), words[1:]
        if cmd == "hermes":
            cli = args
        elif re.fullmatch(r"python(?:\d+(?:\.\d+)*)?t?", cmd):
            cli = []
            for i, arg in enumerate(args):
                if arg == "-m" and args[i + 1:i + 2] == ["hermes_cli.main"]:
                    cli = args[i + 2:]
                    break
                if arg == "-mhermes_cli.main":
                    cli = args[i + 1:]
                    break
        else:
            cli = []
        while cli and (cli[0] in {"-p", "--profile"} or cli[0].startswith("--profile=")):
            cli = cli[1:] if cli[0].startswith("--profile=") else cli[2:]
        if cli and cli[0] == "update" and not allowed and not UPDATE_READ_ONLY.intersection(cli[1:]):
            return "unfenced hermes update"
        script = None
        if cmd == "ssh":
            script = _ssh_script(args)
        elif cmd in SHELLS:
            script = _shell_c_script(args)
        elif cmd == "eval":
            script = " ".join(args)
        if cmd in SHELLS | {"ssh"} and "<<<" in args:
            i = args.index("<<<")
            script = args[i + 1] if i + 1 < len(args) else script
        if script and (why := _check_update(script, cwd, allowed, depth + 1)):
            return why
    return None


def check_shell(cmd: str, cwd: Optional[str] = None, *, _update_checked: bool = False) -> Optional[str]:
    cwd = os.path.expanduser(cwd) if cwd else os.getcwd()
    if not _update_checked and (why := _check_update(cmd, cwd)):
        return why
    if not cmd.strip():
        return None
    for seg in _segments(_tokens(cmd)):
        words, targets = _redirect_targets(seg)
        why, cwd = _check_segment(words, targets, cwd)
        if why:
            return why
    return None


# ---- execute_code ------------------------------------------------------------------------------

def _const(node: ast.AST):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, (ast.List, ast.Tuple)) and all(isinstance(e, ast.Constant) for e in node.elts):
        return [e.value for e in node.elts]
    return None


def _callname(f: ast.AST) -> str:
    return f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""


def check_python(code: str, cwd: Optional[str] = None) -> Optional[str]:
    """Inspect literal arguments of terminal()/write_file()/patch()/subprocess/os.system/open(..., 'w') calls."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _callname(node.func)
        pos = [_const(a) for a in node.args]
        kw = {k.arg: _const(k.value) for k in node.keywords if k.arg}
        wd = kw.get("workdir") or kw.get("cwd") or cwd
        why = None
        if name in {"terminal", "run_shell"}:
            c = kw.get("command") or (pos[0] if pos else None)
            if isinstance(c, str):
                why = check_shell(c, wd)
        elif name in FILE_MUTATORS:
            a = {k: v for k, v in kw.items() if isinstance(v, str)}
            if pos and isinstance(pos[0], str):  # write_file(path, content) / patch(path, old, new, ...)
                a.setdefault("path", pos[0])
            why = check_file_tool(name, a, wd)
        elif name in {"system", "popen"}:
            if pos and isinstance(pos[0], str):
                why = check_shell(pos[0], wd)
        elif name in {"run", "call", "check_call", "check_output", "Popen"}:
            c = kw.get("args") or (pos[0] if pos else None)
            if isinstance(c, list) and all(isinstance(x, str) for x in c):
                c = " ".join(shlex.quote(x) for x in c)
            if isinstance(c, str):
                why = check_shell(c, wd)
        elif name == "open" and isinstance(node.func, ast.Name):
            mode = kw.get("mode") or (pos[1] if len(pos) > 1 else "r")
            if isinstance(pos[0] if pos else None, str) and isinstance(mode, str) and set(mode) & set("wax+") \
                    and in_root(pos[0], cwd):
                why = f"open({pos[0]!r}, {mode!r}) writes inside the live checkout"
        elif name in {"write_text", "write_bytes", "touch", "unlink", "mkdir", "rename", "replace"}:
            f = node.func
            if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Call) and _callname(f.value.func) == "Path":
                p = _const(f.value.args[0]) if f.value.args else None
                if isinstance(p, str) and in_root(p, cwd):
                    why = f"Path({p!r}).{name}() writes inside the live checkout"
        if why:
            return why
    return None
