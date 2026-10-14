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

# A crontab command word, not part of crontab_line / crontab-guard / file names like x.crontab.
SHELL_CRONTAB = re.compile(r"(?<![\w.\-/])(?:/usr/bin/)?crontab(?![\w.\-/])((?:[ \t]+[^\s;&|<>()`]+)*)")
READ_ONLY = re.compile(r"^\s*(?:-u\s+\S+\s+)?-l\s*$")
# Python: "crontab" (or "/usr/bin/crontab") as a string literal, e.g. subprocess.run(["crontab", "-"]).
PY_CRONTAB = re.compile(r"""["'](?:/usr/bin/)?crontab["']\s*(?:,\s*["']([^"']*)["'])?""")


def check_shell(cmd: str) -> Optional[str]:
    for m in SHELL_CRONTAB.finditer(cmd or ""):
        args = m.group(1) or ""
        if READ_ONLY.match(args):
            continue
        shown = ("crontab" + args).strip()
        return f"`{shown[:80]}` would replace or remove the whole crontab"
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
