"""user-terminal — run a command in a real terminal window on the user's screen.

Instead of telling the user "please run X in your terminal", the agent calls ``run_in_user_terminal``.
The plugin opens a terminal window (iTerm if installed, else Terminal.app, else cmux), shows why the agent
wants to run the command, the working directory and the exact command, and runs it right away (the user
asked for no y/N step: they always said yes). Ctrl-C in the window cancels it. Config
``user_terminal.confirm`` (env ``HERMES_USER_TERMINAL_CONFIRM``) = ``off`` (default) | ``remote`` (ask
``Run it? [y/N]`` only for jobs relayed from another gateway) | ``all``. The command runs in a real TTY (so sudo passwords, OAuth device codes, interactive
logins and TCC prompts all work, with the terminal's permissions). The tool waits for the outcome and
returns the exit code plus the captured output to the agent.

Jobs live in ``<hermes home>/cache/user-terminal/<job_id>/``:
  meta.json   reason / cwd / command (shown by the runner)
  cmd.sh      the command, verbatim
  runner.sh   the show-and-run script the window executes
  confirm     present -> the runner asks y/N first (see confirm_mode)
  output.log  everything printed while the command ran (via script(1))
  status.json {"status": "waiting_for_user"|"running"|"ran"|"declined"|"failed", "exit_code": n}
              "failed" = no window started the command (with error, job_id and any osascript diagnosis). It is
              written only by fail_job() after it wins start.lock, so "nothing ran" is proven, never assumed.
  opened      one line per runner that started (a window exists); the opener stops trying other apps once
              it sees this, even if its osascript call then errored (e.g. timed out centring the window)
  start.lock  the single-start gate (atomic mkdir; owner file says who). runner.sh takes it right before it
              runs the command (after any y/N) or records a decline; fail_job() takes it before declaring
              failed. Only the winner acts: a second window, or a window that opens after Hermes gave up,
              runs nothing, and fail_job() never overwrites a running/ran/declined job.
  open_warning.txt  the opener's error when its window started anyway (returned as opener_warning)
  origin.txt  who asked, for a request relayed from another gateway (shown by the runner)
  owner.json  who owns the prompt: {kind: kanban, task, run_id, board, db} (HERMES_KANBAN_TASK, else
              HERMES_KANBAN_WORKER_TASK; read before launch_env() strips them), {kind: session}, or {kind: relayed}
  withdraw    the withdrawal marker; holds the reason. A runner that starts while it exists refuses.
  final.lock  the final-outcome gate (atomic mkdir; result.json inside). ran/declined (runner), failed,
              withdrawn and abandoned (Python) are written only by its winner, so a withdrawal is never
              overwritten by "ran".
  cmd.pgid    the command's process group (script(1)'s session leader) so a withdrawal kills it, children included

Owned prompts: each job gets a detached job-local watchdog (this file run with --watchdog, polling every 2 s,
bounded to max age + 2 min). It withdraws the job when the owning card is done/archived, got an ``unblocked``
event after the job was created, or has a newer current_run_id (board sqlite opened mode=ro + query_only; a
card still blocked on the same run keeps its prompt); when it is older than ``user_terminal.max_wait_minutes``
(default 120, env HERMES_USER_TERMINAL_MAX_WAIT_MINUTES); or on ``action: withdraw`` (job_id, reason). Withdrawal
kills the command's process group (e.g. a sudo waiting for its password), keeps the window's TTY, records
status ``withdrawn`` with the reason and prints "Hermes withdrew this request: <reason>. Nothing else to do here."
A runner that died while waiting/running (pid + start time, only once a runner announced itself) -> ``abandoned``.

Terminal choice: iTerm > Terminal.app > cmux; env HERMES_USER_TERMINAL = iterm | terminal | cmux tries that one first.
(cmux is last: its control socket only accepts processes started inside cmux by default.)

Remote mode (a gateway with no screen: Linux/cloud). The same tool is registered, but the handler relays
the job to the owner's Mac through the session-mentions courier: it writes a ``user_terminal`` request into
``<root>/session_mentions/outbox/`` addressed (``route``) to the Mac's device name (config
``user_terminal.device``, env ``HERMES_USER_TERMINAL_DEVICE``, else the only live courier). That Mac's
Desktop courier claims it and calls this plugin's own dashboard routes on the Mac's backend
(``dashboard/plugin_api.py``: ``POST /start``, ``GET /status/<job>``), which run the local path above
(``create_job`` + ``open_window`` + ``wait_for``). The window names the originating
gateway/profile/session. Only the owner's own sessions (desktop/cli/tui/api_server) may call it; a session
on a messaging platform (e.g. Slack on a shared bot) is refused. No live courier for that Mac -> an
immediate error, never a silent wait.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import signal
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
        "Run a shell command in a terminal window on the user's screen, where they can type passwords "
        "and answer prompts. Use this whenever you would otherwise ask the user to run a command themselves "
        "— anything needing their password (sudo), an interactive login/OAuth/device code, a TTY prompt, "
        "or their terminal's macOS permissions. It is not an approval gate (no y/N): get a go-ahead in chat "
        "first for anything destructive. Never tell "
        "the user to copy/paste a command; call this instead. The window shows `reason`, `cwd` and the "
        "exact `command`, runs it right away (no y/N step; the user can Ctrl-C it), and this tool returns status "
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
            "action": {"type": "string", "enum": ["run", "withdraw"], "description": "withdraw (with job_id and reason): take back a request that is no longer needed; the window says so and any running command is stopped."},
        },
    },
}


RUNNER = r"""#!/bin/bash
JOB="$1"
printf '%s\n' "$$" >> "$JOB/opened"   # evidence for the opener: a window exists (stops its fallback)
B=$'\e[1m'; D=$'\e[2m'; Y=$'\e[33m'; G=$'\e[32m'; R=$'\e[31m'; C=$'\e[36m'; N=$'\e[0m'
status() { printf '%s' "$1" > "$JOB/.status.$$.tmp" && mv "$JOB/.status.$$.tmp" "$JOB/status.json"; }
# Final outcome gate shared with withdraw_job()/mark_abandoned() in Python: whoever creates final.lock owns the
# job's final status, so a withdrawal is never overwritten by "ran" (and vice versa).
final() { mkdir "$JOB/final.lock" 2>/dev/null || return 1; printf '%s' "$1" > "$JOB/final.lock/result.json"; status "$1"; }
withdrawn_msg() {
  echo; echo "${B}${Y}Hermes withdrew this request: $(head -c 2000 "$JOB/withdraw" 2>/dev/null | tr -s '\n' ' ' | sed 's/ *$//'). Nothing else to do here.${N}"
  read -r -t 600 -p "Press Enter to close. " </dev/tty; exit 0
}
trap 'withdrawn_msg' USR1
if [ -e "$JOB/withdraw" ]; then withdrawn_msg; fi   # withdrawn before this window started: never run
refuse() {
  if [ -e "$JOB/withdraw" ]; then withdrawn_msg; fi
  if grep -q '"status": *"failed"' "$JOB/status.json" 2>/dev/null; then
    echo "Hermes already gave up on this window (it did not open in time) and told the agent; not running it."
  else
    echo "Another window already took this job; not running it."
  fi
  sleep 2; exit 0
}
# The single-start gate, shared with fail_job() in Python: whoever creates start.lock decides (run, decline or
# failed). mkdir is atomic, so two windows, or a window and Hermes giving up, can never both win.
gate() { mkdir "$JOB/start.lock" 2>/dev/null || refuse; echo "runner $$" > "$JOB/start.lock/owner"; [ -e "$JOB/withdraw" ] && withdrawn_msg; }
if grep -q '"status": *"failed"' "$JOB/status.json" 2>/dev/null; then refuse; fi
if [ -s "$JOB/origin.txt" ]; then printf '\e]0;Hermes (%s) wants to run a command\a' "$(head -n1 "$JOB/origin_short.txt" 2>/dev/null)"; else printf '\e]0;Hermes wants to run a command\a'; fi
clear
W=$(tput cols 2>/dev/null || echo 100); W=$(( W > 120 ? 120 : W )); W=$(( W < 40 ? 40 : W ))
wrap() { fold -s -w $((W - 2)) | sed -e 's/[[:space:]]*$//' -e 's/^/  /'; }
echo "${B}${C}━━━ Hermes wants to run a command ━━━${N}"
echo
if [ -s "$JOB/origin.txt" ]; then echo "${B}${R}From another machine:${N}"; wrap < "$JOB/origin.txt"; echo; fi
if [ -s "$JOB/reason.txt" ]; then echo "${B}Why:${N}"; tr -s ' \n' ' ' < "$JOB/reason_display.txt" | wrap; echo; echo; fi
echo "${B}Where:${N} $(cat "$JOB/cwd_display.txt")"
echo
echo "${B}Command:${N}"
printf '%s' "${Y}"; wrap < "$JOB/cmd_display.txt"; printf '%s' "${N}"
echo
if [ -f "$JOB/confirm" ]; then
  while true; do
    read -r -p "${B}Run it? [y/N]${N} " ans
    case "$ans" in
      y|Y|yes|YES) break ;;
      ""|n|N|no|NO) gate; final '{"status":"declined"}'; echo "${R}Declined — told Hermes.${N}"; sleep 2; exit 0 ;;
    esac
  done
fi
gate   # immediately before running: after the y/N answer, so a job Hermes gave up on meanwhile never runs
status '{"status":"running"}'
[ -f "$JOB/confirm" ] || echo "${D}Running now. Ctrl-C cancels.${N}"
echo "${D}──────────────────────────────────────────${N}"
cd "$(cat "$JOB/cwd.txt")" || { status '{"status":"ran","exit_code":1,"error":"cwd not found"}'; read -r -p "cwd not found. Enter to close. "; exit 1; }
SH="${SHELL:-/bin/zsh}"
# Inside script(1) the bash is a session + process-group leader on the pty: cmd.pgid lets withdraw_job() kill
# the whole command (sleep children, a sudo waiting for its password) while this runner keeps the window's TTY.
# It records its pgid before checking final.lock, so a withdrawal either sees the pgid or this check sees it.
/usr/bin/script -q "$JOB/output.log" /bin/bash -c "echo \$\$ > \"$JOB/cmd.pgid\"; [ -d \"$JOB/final.lock\" ] && exit 143; \"$SH\" -l \"$JOB/cmd.sh\"; echo \$? > \"$JOB/rc\""
rc=$(cat "$JOB/rc" 2>/dev/null || echo 1)
final "{\"status\":\"ran\",\"exit_code\":$rc}" || withdrawn_msg
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


# A terminal app launched from a Kanban worker keeps the worker's identity for ever (cmux ran for days as a
# dead card), and every tab it opens inherits it. Plugin subprocesses skip the tool-env scrub, so scrub here.
_FALLBACK_KANBAN_KEYS = (
    "HERMES_KANBAN_TASK", "HERMES_KANBAN_RUN_ID", "HERMES_KANBAN_CLAIM_LOCK",
    "HERMES_KANBAN_GOAL_MODE", "HERMES_KANBAN_GOAL_MAX_TURNS",
)
_EXTRA_IDENTITY_KEYS = ("HERMES_KANBAN_WORKSPACE", "HERMES_KANBAN_WORKER_TASK")


def worker_identity_keys() -> tuple[str, ...]:
    try:
        from agent.delegation_context import KANBAN_ENV_KEYS  # type: ignore

        base = tuple(KANBAN_ENV_KEYS)
    except Exception:
        base = _FALLBACK_KANBAN_KEYS
    return tuple(dict.fromkeys(base + _FALLBACK_KANBAN_KEYS + _EXTRA_IDENTITY_KEYS))


def launch_env() -> dict[str, str]:
    """os.environ without the Kanban worker identity: the environment for every app this plugin launches."""
    drop = set(worker_identity_keys())
    return {k: v for k, v in os.environ.items() if k not in drop}


def _unset_prefix() -> str:
    """Shell prefix for a command typed into a terminal that may itself carry a stale worker identity."""
    return "unset " + " ".join(worker_identity_keys()) + "; "


def _env_unset_prefix() -> str:
    """Same, for a command exec'd without a shell (iTerm): env -u KEY ..."""
    return "/usr/bin/env " + " ".join(f"-u {k}" for k in worker_identity_keys()) + " "


def _cmux(*args: str, timeout: float = 5) -> subprocess.CompletedProcess:
    return subprocess.run([str(CMUX_BIN), *args], capture_output=True, text=True, timeout=timeout, env=launch_env())


def _open_cmux(shell_cmd: str, title: str) -> str:
    ok = _cmux("ping").returncode == 0
    if not ok:
        subprocess.run(["open", "-a", str(CMUX_APP)], check=False, env=launch_env())
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
    subprocess.run(["open", "-a", str(CMUX_APP)], check=False, env=launch_env())
    return "cmux"


def _osa_quote(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


COLS, ROWS = 110, 34   # the confirm window: wide enough that the wrapped text is not re-wrapped, centred on screen

# AppleScript: centre window `w` of the frontmost app on the main screen, keeping its size. Best effort: the window
# and its runner already exist by now, so a Finder that is busy, denied or awaiting Automation consent must not turn
# this into an "open failed" (that used to start a second window on the same job).
_CENTER = """
try
with timeout of 5 seconds
tell application "Finder" to set {sx, sy, sw, sh} to bounds of window of desktop
tell application {app} to set {x1, y1, x2, y2} to bounds of w
set ww to x2 - x1
set wh to y2 - y1
set nx to (sw - ww) div 2
set ny to (sh - wh) div 3
tell application {app} to set bounds of w to {nx, ny, nx + ww, ny + wh}
end timeout
end try
"""


def _center(app: str) -> str:
    return _CENTER.replace("{app}", _osa_quote(app))


OSA_TIMEOUT = 20


def _responsible_pid(pid: int) -> int | None:
    """macOS's TCC-responsible pid for ``pid`` (libSystem's responsibility_get_pid_responsible_for_pid; an
    in-process call, no subprocess). None where the call does not exist or fails."""
    try:
        import ctypes

        fn = ctypes.CDLL(None).responsibility_get_pid_responsible_for_pid
        fn.restype, fn.argtypes = ctypes.c_int, [ctypes.c_int]
        r = int(fn(int(pid)))
        return r if r > 0 else None
    except Exception:
        return None


def _proc_info(pid: int) -> tuple[int, str] | None:
    """(ppid, executable path) of a live process via ps, bounded to 2 s. None if unknown."""
    try:
        out = subprocess.run(["ps", "-o", "ppid=,comm=", "-p", str(int(pid))], capture_output=True, text=True,
                             timeout=2).stdout.strip()
        ppid, comm = out.split(None, 1)
        return int(ppid), comm.strip()
    except Exception:
        return None


def osa_diagnosis() -> dict:
    """Who macOS asks the Automation (TCC) question for when osascript hangs. osascript is spawned without
    disclaiming responsibility, so it inherits this process's responsible process; that is read from the OS.
    The parent chain is informational only (TCC attributes to the responsible process, not the parent)."""
    me = os.getpid()
    rpid = _responsible_pid(me)
    if rpid is None:
        resp: dict[str, Any] = {"status": "unknown", "why": "responsibility_get_pid_responsible_for_pid unavailable"}
    else:
        info = _proc_info(rpid)
        resp = {"status": "known", "pid": rpid, "source": "responsibility_get_pid_responsible_for_pid",
                "name": Path(info[1]).name if info else None, "path": info[1] if info else None}
    chain, pid = [], me
    for _ in range(8):  # bounded walk
        info = _proc_info(pid)
        if not info:
            break
        chain.append({"pid": pid, "name": Path(info[1]).name})
        if info[0] <= 1:
            break
        pid = info[0]
    return {"responsible_process": resp, "ancestry_not_definitive": chain}


def _diagnosis_text(d: dict) -> str:
    r = d.get("responsible_process") or {}
    if r.get("status") == "known":
        who = f"{r.get('name') or 'unknown name'} (pid {r.get('pid')}, via {r.get('source')})"
    else:
        who = f"unknown ({r.get('why') or 'not retrievable'})"
    chain = " <- ".join(f"{c['name']}[{c['pid']}]" for c in d.get("ancestry_not_definitive") or []) or "unknown"
    return (f"TCC responsible process: {who}. Process ancestry (not definitive for TCC): {chain}. A hang like this "
            f"is commonly a pending macOS Automation consent prompt or a busy app; not confirmed.")


class OpenFailed(RuntimeError):
    def __init__(self, msg: str, diagnosis: dict | None = None):
        super().__init__(msg)
        self.diagnosis = diagnosis


def _osascript(script: str) -> None:
    try:
        subprocess.run(["osascript", "-e", script], check=True, capture_output=True, text=True,
                       timeout=OSA_TIMEOUT, env=launch_env())
    except subprocess.TimeoutExpired:
        d = osa_diagnosis()
        raise OpenFailed(f"osascript timed out after {OSA_TIMEOUT}s. {_diagnosis_text(d)}", d) from None
    except subprocess.CalledProcessError as e:
        err = str(e.stderr or e.output or "").strip()
        raise OpenFailed(f"osascript exited {e.returncode}: {err or 'no stderr'}") from None


def _open_terminal_app(shell_cmd: str) -> str:
    script = (f'tell application "Terminal"\nset t to do script {_osa_quote(shell_cmd)}\nset w to front window\n'
              f'set number of columns of w to {COLS}\nset number of rows of w to {ROWS}\nactivate\nend tell\n'
              + _center("Terminal"))
    _osascript(script)
    return "Terminal"


def _open_iterm(shell_cmd: str) -> str:
    script = ('tell application "iTerm"\nactivate\nset w to (create window with default profile command '
              + _osa_quote(shell_cmd) + f')\ntell current session of w\nset columns to {COLS}\nset rows to {ROWS}\n'
              'end tell\nend tell\n' + _center("iTerm"))
    _osascript(script)
    return "iTerm"


OPEN_GRACE_S = 2.0  # after an osascript error, how long to watch for the window's runner before trying the next app


def window_started(job: Path) -> bool:
    """Evidence a runner exists for this job: it announced itself, took the start gate, or wrote a status."""
    return ((job / "opened").exists() or (job / "start.lock").exists()
            or read_status(job).get("status") not in ("waiting_for_user", "unknown"))


def _await_window(job: Path, secs: float) -> bool:
    deadline = time.time() + secs
    while True:
        if window_started(job):
            return True
        if time.time() >= deadline:
            return False
        time.sleep(0.1)


def open_window(job: Path) -> str:
    """Open one window running this job. An opener that raises after its window started (osascript timed out
    centring it, say) counts as opened: the next app is never tried, so the job never gets a second window.
    osascript errors get a bounded OPEN_GRACE_S watch for a late window; other errors are checked once."""
    run_cmd = f"/bin/bash {shlex.quote(str(job / 'runner.sh'))} {shlex.quote(str(job))}"
    shell_cmd = _unset_prefix() + run_cmd + "; exit"  # typed into an interactive shell (Terminal.app, cmux)
    errors, diagnosis = [], None
    names = {"cmux": "cmux", "iterm": "iTerm", "terminal": "Terminal"}
    for k in terminal_order():
        try:
            if k == "cmux":
                return _open_cmux(shell_cmd, "Hermes: confirm command")
            if k == "iterm":
                return _open_iterm(_env_unset_prefix() + run_cmd)  # iTerm execs it directly, no shell
            if k == "terminal":
                return _open_terminal_app(shell_cmd)
        except Exception as e:  # fall through to the next terminal, unless this one's window started anyway
            errors.append(f"{k}: {e}")
            diagnosis = getattr(e, "diagnosis", None) or diagnosis
            if _await_window(job, OPEN_GRACE_S if isinstance(e, OpenFailed) else 0):
                try:
                    (job / "open_warning.txt").write_text("; ".join(errors))
                except OSError:
                    pass
                return names.get(k, k)
    raise OpenFailed("; ".join(errors) or "no terminal available", diagnosis)


# ---------------------------------------------------------------- jobs

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*(\x07|\x1b\\)|\x1b[()][A-Z0-9]")


def clean_output(raw: str) -> str:
    text = _ANSI.sub("", raw).replace("\r\n", "\n")
    lines = [ln.rsplit("\r", 1)[-1] for ln in text.split("\n")]  # keep what a \r-redraw left visible
    text = "\n".join(lines).replace("\x04", "").replace("\x08", "")
    return text.strip("\n")


_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f\x80-\x9f]")


def visible(text: str) -> str:
    """Control characters (ESC, CR, BEL, C1...) shown as ^X / <U+..> so a relayed command, reason or origin
    cannot redraw the confirm window (hide part of the command, fake a prompt). Newlines and tabs stay."""
    def sub(m: re.Match) -> str:
        c = ord(m.group(0))
        return f"^{chr(c + 64)}" if c < 32 else ("^?" if c == 127 else f"<U+{c:04X}>")
    return _CTRL.sub(sub, str(text or ""))


def origin_lines(origin: dict | None) -> tuple[str, str]:
    """(full text for the window, short label for its title) for a relayed request's origin."""
    if not isinstance(origin, dict) or not origin.get("gateway"):
        return "", ""
    one = lambda k: visible(str(origin.get(k) or "")).replace("\n", " ").strip()[:200]  # noqa: E731
    gw, profile, sid, title = one("gateway"), one("profile") or "default", one("session_id"), one("title")
    text = f"Gateway {gw} · profile {profile}"
    if sid:
        text += f" · session {sid}"
    if title:
        text += f' ("{title}")'
    return text + "\n", f"{gw}/{profile}"


def confirm_mode() -> str:
    """off (default) | remote | all — when the runner asks ``Run it? [y/N]`` before running."""
    mode = os.environ.get("HERMES_USER_TERMINAL_CONFIRM", "")
    try:
        from hermes_cli.config import load_config  # type: ignore

        v = ((load_config() or {}).get("user_terminal") or {}).get("confirm")
        if v is not None and str(v).strip():
            mode = "all" if v is True else "off" if v is False else str(v)
    except Exception:
        pass
    mode = mode.strip().lower()
    return {"1": "all", "true": "all", "yes": "all", "on": "all", "0": "off", "false": "off", "no": "off"}.get(
        mode, mode if mode in ("off", "remote", "all") else "off")


def job_owner(session_id: str | None = None) -> dict:
    """Who asked: the Kanban card + run (HERMES_KANBAN_TASK, else HERMES_KANBAN_WORKER_TASK; RUN_ID; board db),
    else the chat session. Read from os.environ before launch_env() strips the worker identity."""
    task = (os.environ.get("HERMES_KANBAN_TASK") or os.environ.get("HERMES_KANBAN_WORKER_TASK") or "").strip()
    if task:
        board = (os.environ.get("HERMES_KANBAN_BOARD") or "default").strip() or "default"
        db = os.environ.get("HERMES_KANBAN_DB") or ""
        if not db:
            root = _home().parent.parent if _home().parent.name == "profiles" else _home()
            db = str(root / "kanban.db") if board == "default" else str(root / "kanban" / "boards" / board / "kanban.db")
        run = (os.environ.get("HERMES_KANBAN_RUN_ID") or "").strip()
        return {"kind": "kanban", "task": task, "run_id": int(run) if run.isdigit() else None, "board": board,
                "db": db}
    return {"kind": "session", "session_id": str(session_id or _session_env("HERMES_SESSION_ID") or "")}


def create_job(command: str, reason: str, cwd: str, origin: dict | None = None,
               confirm: bool | None = None, owner: dict | None = None) -> Path:
    job = jobs_dir() / (time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6])
    job.mkdir(parents=True)
    (job / "cmd.sh").write_text(command.rstrip("\n") + "\n")
    (job / "cmd_display.txt").write_text(visible(command.rstrip("\n")) + "\n")
    (job / "reason.txt").write_text(reason.strip() + "\n" if reason.strip() else "")
    (job / "reason_display.txt").write_text(visible(reason.strip()) + "\n" if reason.strip() else "")
    (job / "cwd.txt").write_text(cwd)
    (job / "cwd_display.txt").write_text(visible(cwd))
    full, short = origin_lines(origin)
    if full:
        (job / "origin.txt").write_text(full)
        (job / "origin_short.txt").write_text(short + "\n")
    if confirm is None:
        mode = confirm_mode()
        confirm = mode == "all" or (mode == "remote" and bool(full))
    if confirm:
        (job / "confirm").write_text("1\n")
    (job / "runner.sh").write_text(RUNNER)
    (job / "status.json").write_text(json.dumps({"status": "waiting_for_user"}))
    (job / "meta.json").write_text(json.dumps({"command": command, "reason": reason, "cwd": cwd,
                                                "origin": origin if full else None, "created": time.time()}))
    (job / "owner.json").write_text(json.dumps(owner or {"kind": "none"}))
    return job


def job_origin_gateway(job: Path) -> str:
    try:
        return str((json.loads((job / "meta.json").read_text()).get("origin") or {}).get("gateway") or "")
    except (OSError, ValueError, AttributeError):
        return ""


def read_status(job: Path) -> dict:
    try:  # the final.lock winner's result beats any later status.json write
        return json.loads((job / "final.lock" / "result.json").read_text())
    except (OSError, ValueError):
        pass
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


FINAL = ("ran", "declined", "failed", "withdrawn", "abandoned")


def _write_status(job: Path, st: dict) -> None:
    tmp = job / f".status.{uuid.uuid4().hex[:8]}.tmp"
    tmp.write_text(json.dumps(st))
    os.replace(tmp, job / "status.json")


def _take_final(job: Path, st: dict) -> bool:
    """The final-outcome gate shared with runner.sh's final(): only the mkdir winner records a final status."""
    try:
        (job / "final.lock").mkdir()
    except FileExistsError:
        return False
    (job / "final.lock" / "result.json").write_text(json.dumps(st))
    _write_status(job, st)
    return True


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _read_pid(path: Path) -> int | None:
    try:
        lines = [x for x in path.read_text().split() if x.strip().isdigit()]
        return int(lines[-1]) if lines else None
    except OSError:
        return None


def _kill_command(job: Path, secs: float = 3.0) -> None:
    """Kill the command's process group (script(1)'s session leader and every child, e.g. sleep or a waiting
    sudo), then tell the runner (SIGUSR1) to show the withdrawal; the runner and its window stay open."""
    deadline = time.time() + secs
    killed = False
    while time.time() < deadline:
        pg = _read_pid(job / "cmd.pgid")
        if pg and pg > 1:
            for sig in (15, 9) if killed else (15,):
                try:
                    os.killpg(pg, sig)
                except (ProcessLookupError, PermissionError):
                    pass
            killed = True
        time.sleep(0.2)
    for rp in runner_pids(job):
        try:
            os.kill(rp, signal.SIGUSR1)
        except (ProcessLookupError, PermissionError):
            pass


def runner_pids(job: Path) -> list[int]:
    try:
        return [int(x) for x in (job / "opened").read_text().split() if x.strip().isdigit()]
    except OSError:
        return []


WITHDRAW_MSG = "Hermes withdrew this request: {reason}. Nothing else to do here."


def withdraw_job(job: Path, reason: str) -> dict:
    """Withdraw a job that is still waiting or running: marker JOB/withdraw (holds the reason; a runner that
    starts later refuses), then the final gate, then kill the command group and tell the runner. A job that
    already ended (ran/declined/failed/abandoned) keeps its outcome."""
    reason = " ".join(str(reason or "withdrawn by Hermes").split())[:500] or "withdrawn by Hermes"
    st = read_status(job)
    if st.get("status") in FINAL:
        return st
    if not (job / "withdraw").exists():
        tmp = job / f".withdraw.{uuid.uuid4().hex[:8]}.tmp"
        tmp.write_text(reason)
        os.replace(tmp, job / "withdraw")
    else:
        reason = (job / "withdraw").read_text().strip() or reason
    new = {"status": "withdrawn", "reason": reason, "job_id": job.name}
    if not _take_final(job, new):
        return read_status(job)
    try:  # take the start gate too, so a window that has not started yet refuses
        (job / "start.lock").mkdir()
        (job / "start.lock" / "owner").write_text(f"withdraw_job {os.getpid()}\n")
    except FileExistsError:
        pass
    _kill_command(job)
    return new


def _proc_start(pid: int) -> str:
    try:
        return subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True,
                              timeout=2).stdout.strip()
    except Exception:
        return ""


def mark_abandoned(job: Path, why: str) -> dict:
    st = {"status": "abandoned", "reason": why, "job_id": job.name}
    if _take_final(job, st):
        _kill_command(job, 1.0)
        return st
    return read_status(job)


def max_wait_minutes() -> float:
    v: Any = os.environ.get("HERMES_USER_TERMINAL_MAX_WAIT_MINUTES", "")
    try:
        from hermes_cli.config import load_config  # type: ignore

        c = ((load_config() or {}).get("user_terminal") or {}).get("max_wait_minutes")
        if c is not None and str(c).strip():
            v = c
    except Exception:
        pass
    try:
        f = float(v)
        return f if f > 0 else 120.0
    except (TypeError, ValueError):
        return 120.0


def _board_check(owner: dict, created: float) -> str | None:
    """Reason the owning card no longer wants this prompt, or None. Read-only (mode=ro, query_only)."""
    import sqlite3

    task, db = owner.get("task"), owner.get("db")
    if not task or not db or not Path(db).exists():
        return None
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
    except sqlite3.Error:
        return None
    try:
        con.execute("PRAGMA query_only=1")
        row = con.execute("SELECT status, current_run_id FROM tasks WHERE id=?", (task,)).fetchone()
        if not row:
            return None
        status, cur = row
        if status in ("done", "completed", "archived"):
            return f"card {task} is {status}"
        ev = con.execute("SELECT 1 FROM task_events WHERE task_id=? AND kind='unblocked' AND created_at>=? LIMIT 1",
                         (task, int(created))).fetchone()
        if ev:
            return f"card {task} was unblocked after this request"
        mine = owner.get("run_id")
        if mine is not None and cur is not None and int(cur) > int(mine):
            return f"card {task} moved on to a newer run ({cur})"
        return None
    except sqlite3.Error:
        return None
    finally:
        con.close()


def watch_once(job: Path, seen: dict) -> dict | None:
    """One watchdog pass. Returns the new final status if this pass ended the job."""
    st = read_status(job)
    if st.get("status") in FINAL:
        return st
    if (job / "withdraw").exists():
        return withdraw_job(job, (job / "withdraw").read_text())
    try:
        meta = json.loads((job / "meta.json").read_text())
        owner = json.loads((job / "owner.json").read_text())
    except (OSError, ValueError):
        meta, owner = {}, {}
    created = float(meta.get("created") or time.time())
    if owner.get("kind") == "kanban":
        why = _board_check(owner, created)
        if why:
            return withdraw_job(job, why)
    limit = max_wait_minutes()
    if time.time() - created > limit * 60:
        return withdraw_job(job, f"it waited longer than {limit:g} minutes")
    # Dead runner: only once a runner announced itself (no launch race); pid identity = pid + start time.
    # A duplicate window that refused exits normally, so once a runner holds start.lock only that one counts.
    try:
        own = (job / "start.lock" / "owner").read_text().split()
    except OSError:
        own = []
    pids = [int(own[1])] if len(own) == 2 and own[0] == "runner" and own[1].isdigit() else (
        [] if (job / "start.lock").exists() else runner_pids(job))
    alive_any = False
    for pid in pids:
        start = seen.setdefault(pid, _proc_start(pid))
        if _pid_alive(pid) and (not start or _proc_start(pid) == start):
            alive_any = True
    if pids and not alive_any and read_status(job).get("status") in ("waiting_for_user", "running"):
        return mark_abandoned(job, "the terminal window closed before the command finished")
    return None


def watchdog(job: Path, interval: float = 2.0, max_s: float | None = None) -> dict:
    seen: dict = {}
    end = time.time() + (max_s if max_s is not None else max_wait_minutes() * 60 + 120)
    while time.time() < end:
        r = watch_once(job, seen)
        if r and r.get("status") in FINAL:
            return r
        time.sleep(min(interval, 15.0))
    return watch_once(job, seen) or read_status(job)


def spawn_watchdog(job: Path) -> None:
    """Job-local detached watchdog (this file run as a script), polling every 2 s (bounded 15 s)."""
    import sys

    try:
        subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--watchdog", str(job)],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True, env=launch_env())
    except Exception:
        pass


def fail_job(job: Path, error: str, diagnosis: dict | None = None) -> dict:
    """Declare the job ``failed`` only if Hermes wins the single-start gate (``mkdir start.lock``, the same gate
    runner.sh takes right before it runs or records a decline). Winning proves no window started or declined the
    command, and any window that opens later refuses to run it. Losing means a runner owns the job: its status
    (running/ran/declined) is left untouched and returned. The failed status is written atomically (tmp + rename)
    so waits and resumes end at once instead of polling a job no window will ever answer."""
    try:
        (job / "start.lock").mkdir()
    except FileExistsError:
        return read_status(job)
    (job / "start.lock" / "owner").write_text(f"fail_job {os.getpid()}\n")
    st: dict[str, Any] = {"status": "failed", "error": error, "job_id": job.name}
    if diagnosis:
        st["diagnosis"] = diagnosis
    if not _take_final(job, st):
        return read_status(job)
    return st


def _open_or_fail(job: Path, prefix: str) -> tuple[str | None, dict | None]:
    """(terminal, None) or (None, failed result) with the failure already on disk. If a runner took the job
    while the openers were failing, fail_job leaves it alone and this reports the window as opened."""
    try:
        return open_window(job), None
    except Exception as e:
        st = fail_job(job, f"{prefix}: {e}", getattr(e, "diagnosis", None))
        if st.get("status") != "failed":
            try:
                (job / "open_warning.txt").write_text(str(e))
            except OSError:
                pass
            return "unknown (a window started although its opener reported an error)", None
        return None, wait_for(job, 0)


def wait_for(job: Path, wait_s: float) -> dict:
    deadline = time.time() + wait_s
    st = read_status(job)
    while st.get("status") not in FINAL and time.time() < deadline and not _interrupted():
        time.sleep(0.5)
        st = read_status(job)
    result: dict[str, Any] = {"ok": True, "job_id": job.name, **st}
    try:
        warn = (job / "open_warning.txt").read_text().strip()
        if warn:
            result["opener_warning"] = warn  # the window opened, but its opener reported this
    except OSError:
        pass
    if st.get("status") == "failed":
        result["ok"] = False
        result["note"] = ("No terminal window started this command, so nothing ran (Hermes holds the job's start "
                          "gate; a window that opens late refuses it). Report the error; do not hand the command "
                          "to the user to paste.")
    elif st.get("status") == "withdrawn":
        result["ok"] = True
        result["note"] = WITHDRAW_MSG.format(reason=st.get("reason") or "withdrawn") + (
            " The window was told; do not re-ask unless the work still needs it.")
    elif st.get("status") == "abandoned":
        result["ok"] = False
        result["note"] = ("The terminal window went away before the command finished (abandoned). Nothing more "
                          "will happen for this job_id; start a new request if it is still needed.")
    elif st.get("status") not in ("ran", "declined"):
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


def handle(args: dict, session_id: str | None = None, **_kw: Any) -> str:
    try:
        if str(args.get("action") or "").strip().lower() == "withdraw":
            job_id = str(args.get("job_id") or "").strip()
            job = jobs_dir() / Path(job_id).name
            if not job_id or not job.is_dir():
                return _err(f"unknown job_id {job_id}")
            st = withdraw_job(job, str(args.get("reason") or "withdrawn by Hermes"))
            return json.dumps({"ok": True, "job_id": job.name, **st})
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
        job = create_job(command, str(args.get("reason") or ""), cwd, owner=job_owner(session_id))
        spawn_watchdog(job)
        where, failed = _open_or_fail(job, "could not open a terminal window")
        if failed:
            return json.dumps(failed)
        res = wait_for(job, wait_s)
        res["terminal"] = where
        return json.dumps(res)
    except Exception as e:  # never crash the agent loop
        return _err(f"{type(e).__name__}: {e}")


# Output shapes that mean "this needs the user's TTY/password". Core appends a SUDO_PASSWORD tip to the
# sudo ones, which steers the agent to tell the user to paste the command themselves. Plugin tools may be
# deferred behind tool_search, so the agent may not even see run_in_user_terminal at that moment.
# Line-anchored so a log or a grep that merely contains the text doesn't fire. Nested ssh (laptop -> mini ->
# host) prints the inner failure on its own line too, so it matches the same way.
_NEEDS_SUDO = re.compile(r"^(?:sudo: (?:a password is required|no tty present|a terminal is required)|"
                         r"sorry, you must have a tty to run sudo)", re.M)
_NEEDS_SSH = re.compile(
    r"^(?:[\w.-]+@[\w.\[\]:-]+: )?Permission denied \([^)\n]*(?:password|keyboard-interactive)[^)\n]*\)\.?\r?$|"
    r"^Enter passphrase for key\b|^[\w.-]+@[\w.-]+'s password: ?$|^\(?[\w.-]+@[\w.-]+\)? ?Password: ?$|"
    r"^(?:/usr/bin/)?ssh-copy-id: ERROR\b|^Too many authentication failures\b", re.M)
_NEEDS_OTHER = re.compile(r"could not read (?:Username|Password) for 'https?://|User interaction is not allowed", re.M)
_NEEDS_USER = ("sudo: a password is required", "sudo: no tty present", "sudo: a terminal is required")  # compat
_CORE_SUDO_TIP = re.compile(r"\n*💡 Tip: (?:To enable sudo over messaging|This session cannot prompt for a sudo password)[^\n]*")
_MARK = "💡 This needs the user's password/TTY."
_HEAD = ("\n\n" + _MARK + " Do NOT ask the user to run it themselves, do NOT hand it back as a step for them, and do "
         "NOT suggest SUDO_PASSWORD. Call run_in_user_terminal instead (tool_call name=\"run_in_user_terminal\", "
         "arguments {command, reason}). It opens a terminal on their screen, they type any password "
         "there, and you get the exit code + output back. ")
HINT = _HEAD + "For a remote host, wrap it: ssh -t <host> 'sudo ...'."
SSH_HINT = (_HEAD + "SSH refused key auth. Call run_in_user_terminal with `ssh-copy-id -i ~/.ssh/id_ed25519.pub "
            "<user>@<host>` (or the ssh command itself); the user types the password there; then continue with key "
            "auth. If the ssh ran on another host (a hop through the mini), wrap it: ssh -t <hop> 'ssh-copy-id "
            "<user>@<host>' so the key that gets installed is the hop's.")
OTHER_HINT = (_HEAD + "For git over HTTPS or a locked keychain, run the same command there (e.g. `git pull`, "
              "`security unlock-keychain`) so the user can type the credential.")


def needs_user(output: str) -> str | None:
    """Which hint fits this terminal output, or None."""
    if not isinstance(output, str) or _MARK in output:
        return None
    if _NEEDS_SUDO.search(output):
        return HINT
    if _NEEDS_SSH.search(output):
        return SSH_HINT
    if _NEEDS_OTHER.search(output):
        return OTHER_HINT
    return None


def transform_terminal_output(command: str = "", output: str = "", **_: Any) -> str | None:
    hint = needs_user(output)
    if hint is None:
        return None
    return _CORE_SUDO_TIP.sub("", output).rstrip() + hint


# ---------------------------------------------------------------- relayed jobs (Mac side)

def start_relayed(command: str, reason: str, cwd: str, origin: dict) -> dict:
    """Open the run window for a job relayed from another gateway (dashboard ``POST /start``)."""
    command = str(command or "").strip()
    if not command:
        return {"ok": False, "error": "command is required"}
    if not isinstance(origin, dict) or not str(origin.get("gateway") or "").strip():
        return {"ok": False, "error": "a relayed job must name its origin gateway"}
    cwd = os.path.expanduser(str(cwd or "").strip() or "~")
    if not os.path.isdir(cwd):
        return {"ok": False, "error": f"cwd does not exist on the Mac: {cwd}"}
    job = create_job(command, str(reason or ""), cwd, origin, owner={"kind": "relayed"})
    spawn_watchdog(job)  # max age + dead runner; the remote card's board is not readable here
    where, failed = _open_or_fail(job, "could not open a terminal window on the Mac")
    if failed:
        return failed
    res = wait_for(job, 0)  # usually waiting_for_user -> pending; running/ran when the opener erred late
    out = {"ok": True, "job_id": job.name, "status": "waiting_for_user", "terminal": where}
    if res.get("status") in ("running", "ran", "declined"):
        out.update({k: v for k, v in res.items() if k not in ("note",)})
        out["terminal"] = where
    if res.get("opener_warning"):
        out["opener_warning"] = res["opener_warning"]
    return out


def relayed_status(job_id: str, gateway: str, wait_s: float) -> dict:
    """Wait on a relayed job. Only the gateway that started it may read it (output can be private)."""
    job = jobs_dir() / Path(str(job_id or "")).name
    if not job_id or not job.is_dir() or not gateway or job_origin_gateway(job) != gateway:
        return {"ok": False, "error": f"unknown job_id {job_id}"}
    return wait_for(job, max(0.0, min(float(wait_s or 0), 25.0)))


# ---------------------------------------------------------------- remote mode (gateway with no screen)

# Surfaces where the session's user is the owner. Anything else (slack, telegram, discord, cron, ...)
# may be another person or nobody, so the tool refuses rather than open a window on the owner's Mac.
OWNER_SURFACES = frozenset({"desktop", "api_server", "tui", "cli", "local"})
COURIER_FRESH_S = 20.0
PICKUP_S = 15.0       # couriers drain every ~3 s
START_RESULT_S = 60.0  # the Mac opens the window and answers
POLL_S = 20            # each status request waits this long on the Mac


def _session_env(name: str) -> str:
    try:
        from gateway.session_context import get_session_env  # type: ignore

        return str(get_session_env(name, "") or "")
    except Exception:
        return os.environ.get(name, "")


def caller_surfaces(session_id: str | None) -> list[str]:
    """Every identity this turn has: process platform, session platform/source, the session row's source."""
    ids = [os.environ.get("HERMES_PLATFORM", ""), _session_env("HERMES_SESSION_PLATFORM"),
           _session_env("HERMES_SESSION_SOURCE")]
    if session_id:
        try:
            from hermes_state import SessionDB  # type: ignore

            db = SessionDB(read_only=True)  # a read: writable opens run schema init + a system-wide fd scan
            try:
                row = db.get_session(session_id) or {}
            finally:
                db.close()
            ids.append(str(row.get("source") or ""))
        except Exception:
            pass
    return sorted({str(i).strip().lower() for i in ids if str(i or "").strip()})


def _owner_slack_dm() -> bool:
    """The session is the owner's own Slack DM with the bot (config owner.slack_user_ids; Slack-verified fields)."""
    try:
        from hermes_cli.config import load_config  # type: ignore
        ids = (((load_config() or {}).get("owner") or {}).get("slack_user_ids")) or []
    except Exception:
        ids = []
    if isinstance(ids, str):
        ids = [x for x in re.split(r"[,\s]+", ids) if x]
    return (_session_env("HERMES_SESSION_PLATFORM").lower() == "slack"
            and _session_env("HERMES_SESSION_CHAT_TYPE").lower() in ("dm", "im")
            and _session_env("HERMES_SESSION_CHAT_ID").startswith("D")
            and _session_env("HERMES_SESSION_USER_ID") in {str(i) for i in ids})


def owner_refusal(session_id: str | None) -> str | None:
    """None when the caller is the owner's own session; else the refusal text. Default-deny."""
    surfaces = caller_surfaces(session_id)
    others = [x for x in surfaces if x not in OWNER_SURFACES]
    if surfaces and not others:
        return None
    where = ", ".join(others) or "an unknown surface"
    if _owner_slack_dm():
        return (f"run_in_user_terminal is refused on Slack, even in the owner's own DM: it needs a healthy Hermes on "
                f"the owner's Mac. This IS the owner's DM, so do not tell them it needs the owner. If they want a "
                f"machine looked at or repaired (display, mouse, frozen app, reboot), call mac_ops_handoff with their "
                f"request now: the owner-only machine agent diagnoses, repairs and posts a screenshot in this thread. "
                f"Say in one line that you handed it over. Never hand over commands to paste, keyboard shortcuts or "
                f"other do-it-yourself steps. Any other command: say it needs their Desktop/CLI chat.")
    return (f"run_in_user_terminal is refused here: it opens a confirm window on the owner's Mac and is only "
            f"available in the owner's own chats (Desktop, CLI, TUI, API). This session is on {where}, so the "
            f"person asking may not be the owner. Do not run the command another way and do not hand it over "
            f"as something to paste: tell them it needs the owner to ask for it from their own Hermes chat.")


def _root() -> Path:
    base = _home()
    if base.parent.name == "profiles":  # profiles share the gateway root's courier outbox
        base = base.parent.parent
    path = base / "session_mentions"
    for sub in ("outbox", "claimed", "results"):
        (path / sub).mkdir(parents=True, exist_ok=True)
    return path


def _slug(raw: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(raw or "").strip().lower()).strip("-")


def live_couriers(base: Path) -> list[dict]:
    out = []
    d = base / "couriers"
    for path in sorted(d.glob("*.json")) if d.is_dir() else ():
        try:
            if time.time() - path.stat().st_mtime < COURIER_FRESH_S:
                out.append(json.loads(path.read_text()))
        except Exception:
            continue
    return out


def configured_device() -> str:
    dev = os.environ.get("HERMES_USER_TERMINAL_DEVICE", "")
    try:
        from hermes_cli.config import load_config  # type: ignore

        dev = str(((load_config() or {}).get("user_terminal") or {}).get("device") or dev)
    except Exception:
        pass
    return _slug(dev)


def pick_courier(base: Path) -> tuple[str, str]:
    """(device, error). The configured Mac must have a live courier; unconfigured -> the only live one."""
    couriers = [c for c in live_couriers(base) if c.get("device")]
    seen = ", ".join(sorted(c["device"] for c in couriers)) or "none"
    want = configured_device()
    if want:
        for c in couriers:
            if want == c.get("device") or want in (c.get("aliases") or []):
                return str(c["device"]), ""
        return "", (f"The owner's Mac (`{want}`) has no Hermes Desktop connected to this gateway right now "
                    f"(live Desktops: {seen}), so no terminal can be opened there. Tell the owner that; do not "
                    f"hand them the command to run themselves.")
    if len(couriers) == 1:
        return str(couriers[0]["device"]), ""
    if not couriers:
        return "", ("No Hermes Desktop is connected to this gateway right now, so no terminal can be opened on "
                    "the owner's Mac. Tell the owner that; do not hand them the command to run themselves.")
    return "", (f"Several Desktops drain this gateway ({seen}); set `user_terminal.device` in this gateway's "
                f"config to the owner's Mac.")


def _sender(session_id: str | None) -> dict:
    info: dict[str, Any] = {"session_id": session_id or "", "title": ""}
    if session_id:
        try:
            from hermes_state import SessionDB  # type: ignore

            db = SessionDB(read_only=True)
            try:
                info["title"] = db.get_session_title(session_id) or ""
            finally:
                db.close()
        except Exception:
            pass
    try:
        from hermes_cli.profiles import get_active_profile_name  # type: ignore

        info["profile"] = get_active_profile_name() or "default"
    except Exception:
        info["profile"] = "default"
    info["platform"] = ",".join(caller_surfaces(session_id))
    return info


def relay_once(base: Path, device: str, args: dict, sender: dict, result_wait: float) -> dict:
    """One request to the Mac's courier. Errors (never a hang) when nobody claims it within PICKUP_S."""
    rid = uuid.uuid4().hex
    outbox, result = base / "outbox" / f"{rid}.json", base / "results" / f"{rid}.json"
    req = {"id": rid, "action": "user_terminal", "route": device, "args": args, "sender": sender,
           "created_at": time.time(), "ttl": PICKUP_S + 5}
    tmp = outbox.with_name(f".{rid}.tmp")
    tmp.write_text(json.dumps(req))
    os.replace(tmp, outbox)
    pickup = time.time() + PICKUP_S
    deadline = pickup + result_wait
    while time.time() < deadline:
        if result.exists():
            try:
                data = json.loads(result.read_text())
            except ValueError:
                time.sleep(0.2)
                continue
            result.unlink(missing_ok=True)
            return data if isinstance(data, dict) else {"ok": False, "error": "courier posted no result"}
        if outbox.exists() and time.time() > pickup:
            outbox.unlink(missing_ok=True)
            return {"ok": False, "error": (f"The Hermes Desktop on `{device}` did not pick up the request (it may "
                                           f"be quitting, asleep, or running an older session-mentions plugin). "
                                           f"Tell the owner; do not hand them the command.")}
        if _interrupted():
            return {"ok": False, "error": "interrupted"}
        time.sleep(0.3)
    return {"ok": False, "error": f"`{device}` claimed the request but sent no answer in time"}


def handle_remote(args: dict, session_id: str | None = None, **_kw: Any) -> str:
    try:
        refusal = owner_refusal(session_id)
        if refusal:
            return _err(refusal)
        if str(args.get("action") or "").strip().lower() == "withdraw":  # not relayed (yet): never a silent resume
            return _err("withdraw is not available for a job relayed to the owner's Mac; the Mac's watchdog "
                        "withdraws it after user_terminal.max_wait_minutes or when its window closes")
        wait_s = max(1.0, min(float(args.get("wait_s") or DEFAULT_WAIT), MAX_WAIT))
        job_id = str(args.get("job_id") or "").strip()
        command = str(args.get("command") or "").strip()
        if not job_id and not command:
            return _err("command is required (or job_id to resume waiting)")
        base = _root()
        device, err = pick_courier(base)
        if err:
            return _err(err)
        sender = _sender(session_id)
        deadline = time.time() + wait_s
        if not job_id:
            res = relay_once(base, device, {"command": command, "reason": str(args.get("reason") or ""),
                                            "cwd": str(args.get("cwd") or "")}, sender, START_RESULT_S)
            if not res.get("ok") or not res.get("job_id"):
                return json.dumps({**res, "ok": False, "device": device})
            job_id, terminal = str(res["job_id"]), res.get("terminal")
        else:
            terminal = None
        res: dict = {}
        while True:
            left = deadline - time.time()
            res = relay_once(base, device, {"job_id": job_id, "wait_s": int(max(0, min(POLL_S, left)))}, sender,
                             POLL_S + 30)
            if not res.get("ok") or res.get("status") in FINAL or time.time() >= deadline:
                break
            if _interrupted():
                break
        res = {**res, "job_id": job_id, "device": device}
        if terminal:
            res["terminal"] = terminal
        if res.get("ok") and res.get("status") not in FINAL:
            res["note"] = (f"Still waiting on `{device}` (the owner hasn't answered, or the command is still "
                           f"running). Call run_in_user_terminal with this job_id to keep waiting.")
        return json.dumps(res)
    except Exception as e:  # never crash the agent loop
        return _err(f"{type(e).__name__}: {e}")


REMOTE_SCHEMA = {**SCHEMA, "description": SCHEMA["description"] + (
    " On this gateway (no screen) the window opens on the owner's Mac through its Hermes Desktop; the "
    "window names this gateway, profile and session. If it errors that no Desktop is connected, say so; "
    "never hand the command over to be pasted instead.")}


def has_screen() -> bool:
    return shutil.which("osascript") is not None or CMUX_BIN.exists()


def register(ctx) -> None:
    if not has_screen():  # Linux/cloud gateway: relay to the owner's Mac (see module docstring)
        ctx.register_tool(name="run_in_user_terminal", toolset=TOOLSET, schema=REMOTE_SCHEMA, handler=handle_remote,
                          emoji="🖥️", description="Open a terminal on the owner's Mac (via its Desktop), confirm there, run, return output")
        ctx.register_hook("transform_terminal_output", transform_terminal_output)
        return
    ctx.register_tool(name="run_in_user_terminal", toolset=TOOLSET, schema=SCHEMA, handler=handle, emoji="🖥️",
                      description="Open a terminal on the user's screen, confirm there, run, return output")
    ctx.register_hook("transform_terminal_output", transform_terminal_output)


if __name__ == "__main__":
    import sys as _sys

    if len(_sys.argv) >= 3 and _sys.argv[1] == "--watchdog":  # --watchdog JOB [max_s] [interval]
        _a = _sys.argv[2:]
        watchdog(Path(_a[0]), interval=float(_a[2]) if len(_a) > 2 else 2.0,
                 max_s=float(_a[1]) if len(_a) > 1 else None)
