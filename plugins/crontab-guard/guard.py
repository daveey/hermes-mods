"""Decide whether a shell command or Python snippet would rewrite the crontab wholesale.

Shared by the Hermes pre_tool_call hook (__init__.py) and the Claude Code PreToolUse hook
(claude_hook.py). Allowed: `crontab -l` (read) and the crontab_line.py helper. Blocked: every
other crontab invocation (`crontab FILE`, `crontab -`, `crontab -r`, `crontab -e`), including
`/usr/bin/crontab` and Python subprocess calls.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

HELPER = Path(__file__).resolve().parent / "crontab_line.py"
HELPER_DISPLAY = str(HELPER).replace(str(Path.home()), "~")

# Only executable command positions count; quoted prose is never rescanned as shell.
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z_0-9]*=")
REDIRECT = re.compile(r"\d*[<>]+(?:&(?:\d+|-))?")
READ_ONLY = re.compile(r"^\s*(?:-u\s+\S+\s+)?-l\s*$")
# Python: "crontab" (or "/usr/bin/crontab") as a string literal, e.g. subprocess.run(["crontab", "-"]).
PY_CRONTAB = re.compile(r"""["'](?:/usr/bin/)?crontab["']\s*(?:,\s*["']([^"']*)["'])?""")


def _shell_commands(text: str):
    """Yield argv lists, preserving quoted arguments and inspecting substitutions.

    This is a bounded shell lexer, not an evaluator: ordinary argument strings
    are opaque. Only substitutions and explicit shell/ssh payloads are shell.
    """
    def scan(i=0, end=None):
        tokens, nested, word = [], [], []
        quote = None
        started = False

        def flush():
            nonlocal started
            if started:
                tokens.append(("word", "".join(word)))
                word.clear()
                started = False

        while i < len(text):
            c = text[i]
            if not quote and end and c == end:
                break
            if c == "\\" and quote != "'":
                if i + 1 < len(text):
                    following = text[i + 1]
                    if quote == '"' and following not in '$`"\\\n':
                        word.append(c)
                        i += 1
                        continue
                    if following != "\n":
                        word.append(following)
                        started = True
                    i += 2
                    continue
            if quote != "'" and (text.startswith("$(", i) or c == "`"):
                offset, close = (2, ")") if c == "$" else (1, "`")
                sub, subs, j = scan(i + offset, close)
                nested.extend(subs)
                nested.append(sub)
                word.append("<substitution>")
                started = True
                i = j + 1
                continue
            if c in "\"'":
                if quote == c:
                    quote = None
                elif not quote:
                    quote = c
                else:
                    word.append(c)
                started = True
                i += 1
                continue
            if not quote:
                if c == "#" and not started:
                    j = text.find("\n", i)
                    i = len(text) if j < 0 else j
                    continue
                if c.isspace():
                    flush()
                    if c == "\n":
                        tokens.append(("separator", c))
                    i += 1
                    continue
                redir = REDIRECT.match(text, i) if not started or c in "<>" else None
                if redir:
                    flush()
                    tokens.append(("redirect", redir.group()))
                    i = redir.end()
                    continue
                if c in ";&|()":
                    flush()
                    if c == "(":
                        sub, subs, j = scan(i + 1, ")")
                        nested.extend(subs)
                        nested.append(sub)
                        i = j + 1
                    else:
                        i += 1
                    tokens.append(("separator", c))
                    continue
            word.append(c)
            started = True
            i += 1
        flush()
        return tokens, nested, i

    tokens, nested, _ = scan()
    for group in [tokens, *nested]:
        argv = []
        skip_target = False
        for kind, value in group + [("separator", "")]:
            if kind == "separator":
                if argv:
                    yield argv
                argv = []
                skip_target = False
            elif kind == "redirect":
                skip_target = "&" not in value
            elif skip_target:
                skip_target = False
            else:
                argv.append(value)


# Short options taking a separate value for each supported command prefix.
_PREFIX_OPTIONS = {
    "sudo": "ughpCRT", "env": "uCS", "timeout": "ks",
    "nice": "n", "nohup": "", "exec": "a", "command": "",
    "xargs": "adEILnPs", "ssh": "BbcDEeFIiJLlmOopQRSWw",
}
_LONG_VALUES = {
    "--user", "--group", "--host", "--prompt", "--chdir", "--chroot",
    "--unset", "--split-string", "--kill-after", "--signal", "--adjustment",
    "--arg-file", "--delimiter", "--eof", "--replace", "--max-lines",
    "--max-args", "--max-procs", "--max-chars",
}


def _after_options(argv, command):
    i = 1
    while i < len(argv) and argv[i].startswith("-") and argv[i] != "-":
        option = argv[i]
        i += 1
        if option == "--":
            break
        if option.startswith("--"):
            i += option in _LONG_VALUES
        else:
            for j, flag in enumerate(option[1:], 2):
                if flag in _PREFIX_OPTIONS[command]:
                    i += j == len(option)  # otherwise the value is attached
                    break
    return i


def _check_argv(argv):
    while argv and (ASSIGNMENT.match(argv[0]) or argv[0] in
                    ("{", "!", "if", "then", "else", "elif", "do", "while", "until", "time")):
        argv = argv[1:]
    if not argv:
        return None
    command = argv[0].rsplit("/", 1)[-1]
    if argv[0] in ("crontab", "/usr/bin/crontab"):
        args = " ".join(argv[1:])
        if not READ_ONLY.fullmatch(args):
            shown = ("crontab " + args).strip()
            return f"`{shown[:80]}` would replace or remove the whole crontab"
    elif command in ("bash", "sh", "zsh", "dash", "ksh"):
        i = 1
        while i < len(argv):
            option = argv[i]
            if option == "--" or not option.startswith(("-", "+")):
                break
            if option.startswith("--"):
                i += 1
                continue
            if option.startswith("-") and "c" in option[1:] and i + 1 < len(argv):
                return check_shell(argv[i + 1])
            i += 2 if option in ("-o", "+o", "-O", "+O") else 1
    elif command == "eval":
        return check_shell(" ".join(argv[1:]))
    elif command in _PREFIX_OPTIONS:
        if command == "command" and any(a in ("-v", "-V") for a in argv[1:]):
            return None  # lookup, not execution
        i = _after_options(argv, command)
        if command == "env":
            for j, option in enumerate(argv[1:i], 1):
                if option in ("-S", "--split-string") and j + 1 < len(argv):
                    return check_shell(" ".join(argv[j + 1:]))
                if option.startswith("--split-string=") or option.startswith("-S"):
                    value = option.split("=", 1)[1] if option.startswith("--") else option[2:]
                    return check_shell(" ".join([value, *argv[j + 1:]]))
        if command == "ssh":
            # OpenSSH also accepts options between the host and remote command.
            remote = ["ssh", *argv[i + 1:]]
            return check_shell(" ".join(remote[_after_options(remote, "ssh"):]))
        if command == "timeout":
            i += 1  # duration
        return _check_argv(argv[i:])
    return None


def check_shell(cmd: str) -> Optional[str]:
    for argv in _shell_commands(cmd or ""):
        why = _check_argv(argv)
        if why:
            return why
    return None


def check_python(code: str) -> Optional[str]:
    for m in PY_CRONTAB.finditer(code or ""):
        if (m.group(1) or "") == "-l":
            continue
        return "a Python crontab call (other than `crontab -l`) would replace the whole crontab"
    return check_shell_in_python(code)


def check_shell_in_python(code: str) -> Optional[str]:
    # os.system("... | crontab -") style strings.
    for lit in re.findall(r"""["']([^"'\n]*crontab[^"'\n]*)["']""", code or ""):
        if lit.strip() in ("crontab", "/usr/bin/crontab"):
            continue  # argv-list form; PY_CRONTAB judged it
        why = check_shell(lit)
        if why:
            return why
    return None


def message(why: str) -> str:
    return (
        f"CRONTAB GUARD: blocked: {why}. Sessions may not rewrite the user crontab wholesale, because "
        "a concurrent rewrite silently drops other sessions' jobs. Edit single lines with the locked helper "
        f"instead (it backs up the crontab first): `python3 {HELPER_DISPLAY} list | add '<line>' | "
        "remove <substring> | replace <substring> '<line>'`. Reading with `crontab -l` is allowed. "
        "Do not work around this guard."
    )


def enabled() -> bool:
    return os.environ.get("CRONTAB_GUARD_DISABLE") != "1"
