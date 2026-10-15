"""user-terminal — run a command in a real terminal window, after the user confirms it there.

Instead of telling the user "please run X in your terminal", the agent calls ``run_in_user_terminal``.
The plugin opens a terminal window (iTerm if installed, else Terminal.app, else cmux), shows why the agent
wants to run the command, the working directory and the exact command, and asks ``Run it? [y/N]``.
On ``y`` the command runs right there, in a real TTY (so sudo passwords, OAuth device codes, interactive
logins and TCC prompts all work, with the terminal's permissions). The tool waits for the outcome and
returns the exit code plus the captured output to the agent.

Jobs live in ``<hermes home>/cache/user-terminal/<job_id>/``:
  meta.json   reason / cwd / command (shown by the runner)
  cmd.sh      the command, verbatim
  runner.sh   the confirm-and-run script the window executes
  output.log  everything printed while the command ran (via script(1))
  status.json {"status": "running"|"ran"|"declined", "exit_code": n}

Terminal choice: iTerm > Terminal.app > cmux; env HERMES_USER_TERMINAL = iterm | terminal | cmux tries that one first.
(cmux is last: its control socket only accepts processes started inside cmux by default.)
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

TOOLSET = "user_terminal"
DEFAULT_WAIT = 600
MAX_WAIT = 1800
OUTPUT_TAIL = 12000
CMUX_APP = Path("/Applications/cmux.app")
CMUX_BIN = CMUX_APP / "Contents/Resources/bin/cmux"


def _home() -> Path:
    try:
        from hermes_constants import get_hermes_home  # type: ignore

        return Path(get_hermes_home())
    except Exception:
        return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")


def jobs_dir() -> Path:
    d = Path(os.environ.get("HERMES_USER_TERMINAL_DIR") or _home() / "cache" / "user-terminal")
    d.mkdir(parents=True, exist_ok=True)
    return d


SCHEMA = {
    "name": "run_in_user_terminal",
    "description": (
        "Run a shell command in a terminal window on the user's screen, after the user confirms it IN "
        "THAT TERMINAL. Use this whenever you would otherwise ask the user to run a command themselves "
        "— anything needing their password (sudo), an interactive login/OAuth/device code, a TTY prompt, "
        "their terminal's macOS permissions, or that you are not allowed to run unattended. Never tell "
        "the user to copy/paste a command; call this instead. The window shows `reason`, `cwd` and the "
        "exact `command`, asks 'Run it? [y/N]', runs it on 'y', and this tool returns status "
        "(ran/declined/pending), exit_code and the output. The user can type into the window if the "
        "command prompts. If it is still pending after `wait_s`, call again with only `job_id` to keep waiting."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Shell command to run (runs in the user's login shell)."},
            "reason": {"type": "string", "description": "One or two plain sentences shown above the command: why it is needed and what it will do."},
            "cwd": {"type": "string", "description": "Working directory (default: user's home)."},
            "wait_s": {"type": "integer", "description": f"Seconds to wait for the user + command (default {DEFAULT_WAIT}, max {MAX_WAIT})."},
            "job_id": {"type": "string", "description": "Resume waiting on an earlier pending job instead of starting a new one."},
        },
    },
}


RUNNER = r"""#!/bin/bash
JOB="$1"
B=$'\e[1m'; D=$'\e[2m'; Y=$'\e[33m'; G=$'\e[32m'; R=$'\e[31m'; C=$'\e[36m'; N=$'\e[0m'
status() { printf '%s' "$1" > "$JOB/status.json.tmp" && mv "$JOB/status.json.tmp" "$JOB/status.json"; }
printf '\e]0;Hermes wants to run a command\a'
clear
echo "${B}${C}━━━ Hermes wants to run a command ━━━${N}"
echo
if [ -s "$JOB/reason.txt" ]; then echo "${B}Why:${N}"; fold -s -w 100 "$JOB/reason.txt" | sed 's/^/  /'; echo; fi
echo "${B}Where:${N} $(cat "$JOB/cwd.txt")"
echo
echo "${B}Command:${N}"
echo "${Y}"; sed 's/^/  /' "$JOB/cmd.sh"; echo "${N}"
while true; do
  read -r -p "${B}Run it? [y/N]${N} " ans
  case "$ans" in
    y|Y|yes|YES) break ;;
    ""|n|N|no|NO) status '{"status":"declined"}'; echo "${R}Declined — told Hermes.${N}"; sleep 2; exit 0 ;;
  esac
done
echo "${D}──────────────────────────────────────────${N}"
status '{"status":"running"}'
cd "$(cat "$JOB/cwd.txt")" || { status '{"status":"ran","exit_code":1,"error":"cwd not found"}'; read -r -p "cwd not found. Enter to close. "; exit 1; }
SH="${SHELL:-/bin/zsh}"
/usr/bin/script -q "$JOB/output.log" /bin/bash -c "\"$SH\" -l \"$JOB/cmd.sh\"; echo \$? > \"$JOB/rc\""
rc=$(cat "$JOB/rc" 2>/dev/null || echo 1)
status "{\"status\":\"ran\",\"exit_code\":$rc}"
echo "${D}──────────────────────────────────────────${N}"
if [ "$rc" = 0 ]; then echo "${G}✓ Done (exit 0). Output sent to Hermes.${N}"; else echo "${R}✗ Exit $rc. Output sent to Hermes.${N}"; fi
read -r -t 600 -p "Press Enter to close. "
exit 0
"""


def _err(msg: str) -> str:
    return json.dumps({"ok": False, "error": msg})


# ---------------------------------------------------------------- opening a window

ITERM_APPS = (Path("/Applications/iTerm.app"), Path.home() / "Applications/iTerm.app")


def _has_iterm() -> bool:
    return any(p.exists() for p in ITERM_APPS)


def terminal_order() -> list[str]:
    """iTerm if installed, then Terminal.app, then cmux. HERMES_USER_TERMINAL puts one first."""
    order = [k for k, ok in (("iterm", _has_iterm()), ("terminal", True), ("cmux", CMUX_BIN.exists())) if ok]
    want = (os.environ.get("HERMES_USER_TERMINAL") or "").strip().lower()
    if want in order:
        order.remove(want)
        order.insert(0, want)
    return order


def _cmux(*args: str, timeout: float = 5) -> subprocess.CompletedProcess:
    return subprocess.run([str(CMUX_BIN), *args], capture_output=True, text=True, timeout=timeout)


def _open_cmux(shell_cmd: str, title: str) -> str:
    ok = _cmux("ping").returncode == 0
    if not ok:
        subprocess.run(["open", "-a", str(CMUX_APP)], check=False)
        for _ in range(40):
            time.sleep(0.5)
            if _cmux("ping").returncode == 0:
                ok = True
                break
    if not ok:
        raise RuntimeError("cmux did not come up")
    out = _cmux("new-workspace", "--command", shell_cmd)
    if out.returncode != 0:
        raise RuntimeError(f"cmux new-workspace failed: {out.stderr.strip() or out.stdout.strip()}")
    m = re.search(r"workspace:\d+|[0-9A-Fa-f-]{36}", out.stdout)
    if m:
        _cmux("rename-workspace", "--workspace", m.group(0), title)
        _cmux("select-workspace", "--workspace", m.group(0))
    subprocess.run(["open", "-a", str(CMUX_APP)], check=False)
    return "cmux"


def _osa_quote(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _open_terminal_app(shell_cmd: str) -> str:
    subprocess.run(["osascript", "-e", f'tell application "Terminal" to do script {_osa_quote(shell_cmd)}',
                    "-e", 'tell application "Terminal" to activate'], check=True, capture_output=True, timeout=20)
    return "Terminal"


def _open_iterm(shell_cmd: str) -> str:
    script = ('tell application "iTerm"\nactivate\ncreate window with default profile command '
              + _osa_quote(shell_cmd) + '\nend tell')
    subprocess.run(["osascript", "-e", script], check=True, capture_output=True, timeout=20)
    return "iTerm"


def open_window(job: Path) -> str:
    run_cmd = f"/bin/bash {shlex.quote(str(job / 'runner.sh'))} {shlex.quote(str(job))}"
    shell_cmd = run_cmd + "; exit"  # typed into an interactive shell (Terminal.app, cmux)
    errors = []
    for k in terminal_order():
        try:
            if k == "cmux":
                return _open_cmux(shell_cmd, "Hermes: confirm command")
            if k == "iterm":
                return _open_iterm(run_cmd)  # iTerm execs it directly, no shell
            if k == "terminal":
                return _open_terminal_app(shell_cmd)
        except Exception as e:  # fall through to the next terminal
            errors.append(f"{k}: {e}")
    raise RuntimeError("; ".join(errors) or "no terminal available")


# ---------------------------------------------------------------- jobs

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*(\x07|\x1b\\)|\x1b[()][A-Z0-9]")


def clean_output(raw: str) -> str:
    text = _ANSI.sub("", raw).replace("\r\n", "\n")
    lines = [ln.rsplit("\r", 1)[-1] for ln in text.split("\n")]  # keep what a \r-redraw left visible
    text = "\n".join(lines).replace("\x04", "").replace("\x08", "")
    return text.strip("\n")


def create_job(command: str, reason: str, cwd: str) -> Path:
    job = jobs_dir() / (time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6])
    job.mkdir(parents=True)
    (job / "cmd.sh").write_text(command.rstrip("\n") + "\n")
    (job / "reason.txt").write_text(reason.strip() + "\n" if reason.strip() else "")
    (job / "cwd.txt").write_text(cwd)
    (job / "runner.sh").write_text(RUNNER)
    (job / "status.json").write_text(json.dumps({"status": "waiting_for_user"}))
    (job / "meta.json").write_text(json.dumps({"command": command, "reason": reason, "cwd": cwd,
                                                "created": time.time()}))
    return job


def read_status(job: Path) -> dict:
    try:
        return json.loads((job / "status.json").read_text())
    except (OSError, ValueError):
        return {"status": "unknown"}


def _interrupted() -> bool:
    try:
        from tools.interrupt import is_interrupted  # type: ignore

        return bool(is_interrupted())
    except Exception:
        return False


def wait_for(job: Path, wait_s: float) -> dict:
    deadline = time.time() + wait_s
    st = read_status(job)
    while st.get("status") not in ("ran", "declined") and time.time() < deadline and not _interrupted():
        time.sleep(0.5)
        st = read_status(job)
    result: dict[str, Any] = {"ok": True, "job_id": job.name, **st}
    if st.get("status") not in ("ran", "declined"):
        result["status"] = "pending" if st.get("status") != "running" else "running"
        result["note"] = ("Still waiting (user hasn't answered, or the command is still running). Call "
                          "run_in_user_terminal with this job_id to keep waiting.")
    log = job / "output.log"
    if log.exists():
        out = clean_output(log.read_text(errors="replace"))
        if len(out) > OUTPUT_TAIL:
            out = f"[… {len(out) - OUTPUT_TAIL} chars trimmed; full log {log}]\n" + out[-OUTPUT_TAIL:]
        result["output"] = out
    if result["status"] == "declined":
        result["note"] = "The user declined in the terminal. Do not re-run it unasked; ask what they want instead."
    return result


def handle(args: dict, **_kw: Any) -> str:
    try:
        wait_s = float(args.get("wait_s") or DEFAULT_WAIT)
        wait_s = max(1.0, min(wait_s, MAX_WAIT))
        job_id = str(args.get("job_id") or "").strip()
        if job_id:
            job = jobs_dir() / Path(job_id).name
            if not job.is_dir():
                return _err(f"unknown job_id {job_id}")
            return json.dumps(wait_for(job, wait_s))
        command = str(args.get("command") or "").strip()
        if not command:
            return _err("command is required (or job_id to resume waiting)")
        cwd = os.path.expanduser(str(args.get("cwd") or "").strip() or "~")
        if not os.path.isdir(cwd):
            return _err(f"cwd does not exist: {cwd}")
        job = create_job(command, str(args.get("reason") or ""), cwd)
        try:
            where = open_window(job)
        except Exception as e:
            return _err(f"could not open a terminal window: {e}")
        res = wait_for(job, wait_s)
        res["terminal"] = where
        return json.dumps(res)
    except Exception as e:  # never crash the agent loop
        return _err(f"{type(e).__name__}: {e}")


def register(ctx) -> None:
    if shutil.which("osascript") is None and not CMUX_BIN.exists():
        return  # no screen to open a terminal on (Linux/cloud gateway)
    ctx.register_tool(name="run_in_user_terminal", toolset=TOOLSET, schema=SCHEMA, handler=handle, emoji="🖥️",
                      description="Open a terminal on the user's screen, confirm there, run, return output")
