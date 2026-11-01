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
  status.json {"status": "waiting_for_user"|"running"|"ran"|"declined"|"failed"|"withdrawn"|"abandoned", ...}
              "failed" = every opener errored and no window started the command (with the opener error, job_id
              and any osascript diagnosis). It is written only by fail_job() after it wins start.lock, so "nothing
              ran" is proven, never assumed. A job with no window after LAUNCH_GRACE_S (openers long done) is fenced
              (start.lock owner "fence <pid>") and then "abandoned"; so is one whose window died.
  opened      one line per runner that started (a window exists); the opener stops trying other apps once
              it sees this, even if its osascript call then errored (e.g. timed out centring the window)
  start.lock  the single-start gate (atomic mkdir; owner file says who). runner.sh takes it right before it
              runs the command (after any y/N) or records a decline; fail_job() takes it before declaring
              failed. Only the winner acts: a second window, or a window that opens after Hermes gave up,
              runs nothing, and fail_job() never overwrites a running/ran/declined job.
  open_warning.txt  the opener's error when its window started anyway (returned as opener_warning)
  origin.txt  who asked, for a request relayed from another gateway (shown by the runner)
  meta.json   also carries ``owner`` = {kind: card, board, task_id, run_id} (HERMES_KANBAN_TASK, else
              HERMES_KANBAN_WORKER_TASK; read before launch_env() strips them) | {kind: session, session_id} |
              {kind: relayed}; ``board_db`` (resolved by core kanban_db_path when importable) and
              ``max_wait_minutes`` (resolved at creation: the detached watchdog may not import Hermes config);
              ``board_event_cursor`` = the board's MAX(task_events.id) at creation ("unblocked after this request"
              means a later id; jobs without it fall back to created_at >= the creation second).
              create_job() records the owner itself.
  owner.json  legacy sidecar of the owner ({kind: kanban, task, run_id, board, db}); read only when meta has none
  attachment.json  the owning card's run that re-attached (see Owned prompts); attachment.lock guards its writes
  runners     one line per runner: "pid lstart" (its identity; ps run with LC_ALL=C TZ=UTC on both sides so a
              window's locale/zone cannot make a live runner look dead). Signals go only to a pid whose current
              start time matches; a pid with no recorded identity (legacy job) is never signalled.
  watchdog.lock  the job's single watchdog (atomic mkdir; owner = "pid lstart"); watchdog.log its stderr
  withdraw    the withdrawal marker; holds the reason. A runner that starts while it exists refuses.
  final.lock  the final-outcome gate (atomic mkdir; result.json inside). ran/declined (runner), failed,
              withdrawn and abandoned (Python) are written only by its winner, so a withdrawal is never
              overwritten by "ran".
  cmd.pgid    "pgid lstart" of the command's process group (script(1)'s session leader) so a withdrawal kills it,
              children included, only while the leader's start time still matches

Owned prompts: each job gets one detached job-local watchdog (this file run with --watchdog, polling every 15 s,
bounded to max age + 2 min). handle() spawns it and runner.sh starts it (and waits for it) before the command,
so a job made by create_job() directly is covered too; watchdog.lock keeps it to one per job. Every
run_in_user_terminal call (new or resume) also sweeps jobs, including final outcomes whose blocked asks still
need reconciliation, so old jobs without a watchdog are
reconciled: no window after LAUNCH_GRACE_S -> start gate fenced, then abandoned (nothing ran). It withdraws the job
at once when the owning card is done/archived or a later unattached run ends with completed, blocked, scheduled
or review_requested. All other outcomes (including NULL/unknown) get grace, not immediate withdrawal. Board
sqlite is opened mode=ro + query_only; a card still blocked on the same run keeps its prompt. An ``unblocked``
event after the acknowledged event cursor, or any later run, starts the re-attach
grace ``user_terminal.reattach_grace_minutes`` (default 10, env HERMES_USER_TERMINAL_REATTACH_GRACE_MINUTES; resolved
at creation into meta.json), measured from the earliest such board event so polls never extend it; past it the job
is withdrawn. The owning card's current live run re-attaches with run_in_user_terminal(job_id=...): before any
sweep/check, attach_job() writes JOB/attachment.json ({task_id, board_db, run_id, event_cursor, attached_at}; tmp +
rename under the attachment.lock mkdir lock; meta.json's original owner never changes), which moves the acknowledged
run/cursor forward. Other cards, other boards and stale or ended runs cannot attach. It also withdraws a job
when it is older than ``user_terminal.max_wait_minutes``
(default 120, env HERMES_USER_TERMINAL_MAX_WAIT_MINUTES; finite and > 0, else 120; reason "expired after N min;
ask again if still needed"); or on ``action: withdraw`` (job_id, reason). Withdrawal
kills the command's process group (e.g. a sudo waiting for its password), keeps the window's TTY, records
status ``withdrawn`` with the reason and prints "Hermes withdrew this request: <reason>. Nothing else to do here."
A runner that died while waiting/running (pid + start time, only once a runner announced itself) -> ``abandoned``;
abandonment is decided before the card and max-age checks. Legacy jobs (runner.sh without the final gate/withdraw
trap) are judged by an argv scan for their runner.sh: while that window lives, withdraw returns
``withdraw: unsupported`` with an error (nothing is claimed withdrawn); once it is gone the job is ``abandoned``.

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
import logging
import math
import os
import re
import shlex
import shutil
import signal
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TOOLSET = "user_terminal"
DEFAULT_WAIT = 600
MAX_WAIT = 1800
OUTPUT_TAIL = 12000
CMUX_APP = Path("/Applications/cmux.app")
CMUX_BIN = CMUX_APP / "Contents/Resources/bin/cmux"
log = logging.getLogger("user_terminal")
WATCH_INTERVAL_S = 15.0
LAUNCH_GRACE_S = 90.0  # >= 3 openers x OSA_TIMEOUT (20 s) + open grace: a window that never started by then never will
DEFAULT_MAX_WAIT_MIN = 120.0
DEFAULT_REATTACH_GRACE_MIN = 10.0


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
        "(ran/declined/pending/withdrawn/abandoned), exit_code and the output. Jobs expire after "
        "user_terminal.max_wait_minutes (default 120), including running commands. If a request is no longer "
        "needed, withdraw it immediately: action=withdraw with job_id and reason. A Kanban card's request "
        "survives the card being unblocked or moving to a new run for user_terminal.reattach_grace_minutes "
        "(default 10): the resumed run re-attaches by calling again with the job_id (put it in the block "
        "reason); otherwise it is withdrawn. The user can type into the window if the "
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
# identity before signals; LC_ALL=C TZ=UTC so it matches Python's _proc_start() whatever the window's locale/zone
printf '%s %s\n' "$$" "$(LC_ALL=C TZ=UTC /bin/ps -o lstart= -p $$ 2>/dev/null | tr -s ' ')" >> "$JOB/runners"
printf '%s\n' "$$" >> "$JOB/opened"   # evidence for the opener: a window exists (stops its fallback)
# The job's watchdog (one per job via watchdog.lock), started and waited for before anything runs.
if [ "${HERMES_USER_TERMINAL_RUNNER_WATCHDOG:-1}" != 0 ] && [ -s "$JOB/watchdog.sh" ]; then
  nohup /bin/bash "$JOB/watchdog.sh" "$JOB" >> "$JOB/watchdog.log" 2>&1 < /dev/null &
  for _ in $(seq 1 30); do [ -d "$JOB/watchdog.lock" ] && break; sleep 0.1; done
fi
B=$'\e[1m'; D=$'\e[2m'; Y=$'\e[33m'; G=$'\e[32m'; R=$'\e[31m'; C=$'\e[36m'; N=$'\e[0m'
status() { [ -d "$JOB/final.lock" ] && return 0; printf '%s' "$1" > "$JOB/.status.$$.tmp" && mv "$JOB/.status.$$.tmp" "$JOB/status.json"; }
# Final outcome gate shared with withdraw_job()/mark_abandoned() in Python: whoever creates final.lock owns the
# job's final status, so a withdrawal is never overwritten by "ran" (and vice versa).
final() { mkdir "$JOB/final.lock" 2>/dev/null || return 1; printf '%s' "$1" > "$JOB/final.lock/result.json"; printf '%s' "$1" > "$JOB/.status.$$.tmp" && mv "$JOB/.status.$$.tmp" "$JOB/status.json"; }
withdrawn_msg() {
  trap '' USR1  # teardown can notify after script exits; never interrupt/re-enter the close prompt
  echo; echo "${B}${Y}Hermes withdrew this request: $(head -c 2000 "$JOB/withdraw" 2>/dev/null | tr -s '\n' ' ' | sed 's/ *$//'). Nothing else to do here.${N}"
  read -r -t 600 -p "Press Enter to close. " </dev/tty; exit 0
}
trap 'withdrawn_msg' USR1
if [ -e "$JOB/withdraw" ]; then withdrawn_msg; fi   # withdrawn before this window started: never run
refuse() {
  if [ -e "$JOB/withdraw" ]; then withdrawn_msg; fi
  if grep -q '"status": *"failed"' "$JOB/status.json" 2>/dev/null || grep -qE '^(fence|fail_job) ' "$JOB/start.lock/owner" 2>/dev/null; then
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
/usr/bin/script -q "$JOB/output.log" /bin/bash -c "echo \$\$ \$(LC_ALL=C TZ=UTC /bin/ps -o lstart= -p \$\$ | tr -s ' ') > \"$JOB/cmd.pgid.tmp\"; mv \"$JOB/cmd.pgid.tmp\" \"$JOB/cmd.pgid\"; [ -d \"$JOB/final.lock\" ] && exit 143; \"$SH\" -l \"$JOB/cmd.sh\"; echo \$? > \"$JOB/rc\""
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
    env = {k: v for k, v in os.environ.items() if k not in drop}
    import sys
    core = getattr(sys.modules.get("hermes_cli"), "__file__", None)
    if core:
        env["HERMES_USER_TERMINAL_CORE_ROOT"] = str(Path(core).resolve().parent.parent)
    return env


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


def _signed_opener_python() -> Path | None:
    """Optional installed app; never request a grant or install an app here."""
    import plistlib

    app = Path(os.environ.get("HERMES_USER_TERMINAL_OPENER_APP") or
               str(Path.home() / "Applications" / "Hermes Python.app"))
    try:
        info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
        binary = app / "Contents/MacOS" / info["CFBundleExecutable"]
        if binary.is_file():
            return binary
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def _open_via_launchd(job: Path, python: Path, caller: dict) -> str:
    """Relay only opening, not job ownership, to a one-shot GUI launchd process."""
    import plistlib

    job = job.resolve()
    label = "org.hermes.user-terminal.opener." + uuid.uuid4().hex
    target = f"gui/{os.getuid()}/{label}"
    result = job / "opener-result.json"
    plist = job / "opener.plist"
    env = {k: v for k, v in launch_env().items() if k in
           ("HOME", "PATH", "HERMES_HOME", "HERMES_USER_TERMINAL", "HERMES_USER_TERMINAL_OPENER_APP")}
    plist.write_bytes(plistlib.dumps({
        "Label": label, "ProgramArguments": [str(python), str(Path(__file__).resolve()), "--open-window", str(job)],
        "RunAtLoad": True, "KeepAlive": False, "EnvironmentVariables": env,
        "StandardOutPath": str(job / "opener.log"), "StandardErrorPath": str(job / "opener.log"),
    }))
    try:
        subprocess.run(["/bin/launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist)],
                       check=True, capture_output=True, text=True, timeout=10)
        deadline = time.monotonic() + 3 * (OSA_TIMEOUT + OPEN_GRACE_S) + 20
        while time.monotonic() < deadline:
            if result.exists():
                data = json.loads(result.read_text())
                if data.get("ok"):
                    return data["terminal"]
                raise OpenFailed(data.get("error") or "signed opener failed", data.get("diagnosis") or caller)
            if window_started(job):
                return "signed app (runner started)"
            time.sleep(0.1)
        try:
            caller = json.loads((job / "opener-responsibility.json").read_text())
        except (OSError, ValueError):
            pass
        raise OpenFailed("signed opener timed out. " + _diagnosis_text(caller), caller)
    except subprocess.SubprocessError as e:
        raise OpenFailed(f"signed opener launch failed: {e}. " + _diagnosis_text(caller), caller) from e
    finally:
        # No persistent job/service, including on failure. A late runner still shares start.lock.
        try:
            subprocess.run(["/bin/launchctl", "bootout", target], capture_output=True, timeout=10)
        except (OSError, subprocess.SubprocessError) as e:
            log.warning("user_terminal: opener cleanup failed for %s: %s", target, e)


def open_window(job: Path) -> str:
    python = _signed_opener_python()
    if python is None:
        return _open_window_direct(job)
    caller = osa_diagnosis()
    (job / "opener-caller.json").write_text(json.dumps(caller))
    responsible = (caller.get("responsible_process") or {}).get("path")
    if python is not None and (not responsible or Path(responsible).resolve() != python.resolve()):
        return _open_via_launchd(job, python, caller)
    return _open_window_direct(job)


def opener_main(job: Path) -> int:
    """Fresh signed process: record actual OS responsibility before any Apple Events."""
    diagnosis = osa_diagnosis()
    (job / "opener-responsibility.json").write_text(json.dumps(diagnosis))
    try:
        python = _signed_opener_python()
        responsible = (diagnosis.get("responsible_process") or {}).get("path")
        if python is None or not responsible or Path(responsible).resolve() != python.resolve():
            raise OpenFailed("launchd opener did not acquire signed-app responsibility", diagnosis)
        result = {"ok": True, "terminal": _open_window_direct(job), "diagnosis": diagnosis}
    except Exception as e:
        result = {"ok": False, "error": str(e), "diagnosis": getattr(e, "diagnosis", None) or diagnosis}
    tmp = job / "opener-result.tmp"
    tmp.write_text(json.dumps(result))
    tmp.replace(job / "opener-result.json")
    return 0 if result["ok"] else 1


def _open_window_direct(job: Path) -> str:
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


def _kanban_root() -> Path:
    override = (os.environ.get("HERMES_KANBAN_HOME") or "").strip()
    if override:
        return Path(override).expanduser()
    return _home().parent.parent if _home().parent.name == "profiles" else _home()


def board_db_path(board: str) -> str:
    """The board's kanban.db: core ``hermes_cli.kanban_db.kanban_db_path`` when importable (honours the
    HERMES_KANBAN_DB pin, HERMES_KANBAN_HOME and named boards), else the same layout by hand."""
    # This is the creating caller's board, not a cross-board navigation request.
    # Preserve its DB pin even for worker-terminal callers whose TASK was scrubbed.
    pinned = (os.environ.get("HERMES_KANBAN_DB") or "").strip()
    if pinned:
        return str(Path(pinned).expanduser())
    try:
        from hermes_cli.kanban_db import kanban_db_path  # type: ignore

        return str(kanban_db_path(board))
    except Exception:
        pass
    root = _kanban_root()
    return str(root / "kanban.db") if board == "default" else str(root / "kanban" / "boards" / board / "kanban.db")


def _current_board() -> str:
    board = (os.environ.get("HERMES_KANBAN_BOARD") or "").strip()
    if board:
        return board
    try:
        from hermes_cli.kanban_db import get_current_board  # type: ignore

        return str(get_current_board() or "default")
    except Exception:
        return "default"


def job_owner(session_id: str | None = None) -> dict:
    """Who asked: {kind: card, board, task_id, run_id} (HERMES_KANBAN_TASK, else HERMES_KANBAN_WORKER_TASK), else
    {kind: session, session_id}. Read from os.environ before launch_env() strips the worker identity."""
    task = (os.environ.get("HERMES_KANBAN_TASK") or os.environ.get("HERMES_KANBAN_WORKER_TASK") or "").strip()
    if task:
        run = (os.environ.get("HERMES_KANBAN_RUN_ID") or "").strip()
        return {"kind": "card", "board": _current_board(), "task_id": task,
                "run_id": int(run) if run.isdigit() else None}
    return {"kind": "session", "session_id": str(session_id or _session_env("HERMES_SESSION_ID") or "")}


def _legacy_owner(owner: dict, board_db: str | None) -> dict:
    if owner.get("kind") == "card":
        return {"kind": "kanban", "task": owner.get("task_id"), "run_id": owner.get("run_id"),
                "board": owner.get("board"), "db": board_db}
    return dict(owner)


def create_job(command: str, reason: str, cwd: str, origin: dict | None = None,
               confirm: bool | None = None, owner: dict | None = None) -> Path:
    if owner is None:
        owner = job_owner()
    board_db = board_db_path(str(owner.get("board") or "default")) if owner.get("kind") == "card" else None
    cursor = board_event_cursor(board_db) if board_db else None  # before the job exists: later events are newer
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
    (job / "watchdog.sh").write_text(_watchdog_script())
    (job / "status.json").write_text(json.dumps({"status": "waiting_for_user"}))
    (job / "meta.json").write_text(json.dumps({"command": command, "reason": reason, "cwd": cwd,
                                                "origin": origin if full else None, "created": time.time(),
                                                "owner": owner, "board_db": board_db,
                                                "board_event_cursor": cursor,
                                                "max_wait_minutes": max_wait_minutes(),
                                                "reattach_grace_minutes": reattach_grace_minutes()}))
    (job / "owner.json").write_text(json.dumps(_legacy_owner(owner, board_db)))
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


# ps lstart is formatted by locale (LC_TIME) and zone (TZ): runner.sh records it with the same LC_ALL=C TZ=UTC, so
# an identity written by a window with another locale/zone compares equal (a mismatch reads as a dead runner).
def _ps_env() -> dict[str, str]:
    return {**{k: v for k, v in os.environ.items() if not k.startswith("LC_") and k != "LANG"},
            "LC_ALL": "C", "TZ": "UTC"}


def _proc_start(pid: int, inherit: bool = False) -> str:
    """A live process's start time (ps lstart in LC_ALL=C TZ=UTC, whitespace-normalised); "" when gone/unknown.
    inherit=True renders it in this process's own locale/zone (how runners before the fix recorded it)."""
    try:
        out = subprocess.run(["/bin/ps", "-o", "lstart=", "-p", str(int(pid))], capture_output=True, text=True,
                             timeout=2, env=None if inherit else _ps_env()).stdout
        return " ".join(out.split())
    except Exception:
        return ""


def _parse_ident(line: str) -> tuple[int, str] | None:
    parts = line.split(None, 1)
    if len(parts) != 2 or not parts[0].isdigit() or not parts[1].strip():
        return None
    return int(parts[0]), " ".join(parts[1].split())


def _same_process(pid: int, lstart: str) -> bool:
    """pid is still the process whose start time was recorded (not a reused pid). Identities recorded before
    runner.sh pinned LC_ALL=C TZ=UTC used the recorder's locale/zone; this process's own rendering is accepted too
    (both renderings come from the same real start time, so neither can match a different process)."""
    if pid <= 1 or not lstart:
        return False
    cur = _proc_start(pid)
    return bool(cur) and (cur == lstart or _proc_start(pid, inherit=True) == lstart)


def _cmd_ident(job: Path) -> tuple[int, str] | None:
    try:
        return _parse_ident((job / "cmd.pgid").read_text().strip())
    except OSError:
        return None


def runner_idents(job: Path) -> dict[int, str]:
    """Runner pid -> start time, as each runner recorded itself (JOB/runners). Legacy jobs have none."""
    out: dict[int, str] = {}
    try:
        for line in (job / "runners").read_text().splitlines():
            ident = _parse_ident(line)
            if ident:
                out[ident[0]] = ident[1]
    except OSError:
        pass
    return out


def _group_members(pgid: int) -> list[int] | None:
    """Live (non-zombie) pids whose process group is ``pgid``; None when ps fails (unknown: callers then
    never signal on the strength of it)."""
    try:
        out = subprocess.run(["/bin/ps", "-axo", "pid=,pgid=,stat="], capture_output=True, text=True, timeout=3,
                             env=_ps_env())
    except Exception:
        return None
    if out.returncode != 0:
        return None
    pids = []
    for line in out.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0].isdigit() and parts[1].isdigit() and int(parts[1]) == pgid \
                and not parts[2].startswith("Z"):
            pids.append(int(parts[0]))
    return pids


def _lstart_epoch(lstart: str) -> float | None:
    """A ps lstart rendered in LC_ALL=C TZ=UTC (see _ps_env) as epoch seconds; None if it is in another form."""
    try:
        return datetime.strptime(" ".join(lstart.split()), "%a %b %d %H:%M:%S %Y").replace(
            tzinfo=timezone.utc).timestamp()
    except (ValueError, TypeError):
        return None


def _leaderless_group_verified(ident: tuple[int, str]) -> bool:
    """The recorded command group outlived its session leader: True only when it has live members and every one
    is in the recorded session (sid == recorded pgid) and started at or after the recorded leader, and no process
    holds the leader pid (one that does is not the recorded leader, i.e. a reused pid leading its own session).
    A process in another session cannot be in this group. Anything unknown (ps failed, an unparseable time) reads
    as not verified; a member that exits mid-check is skipped. Residual: a reused pid whose new session leader has
    already exited again, leaving later-born members, would pass; that needs the exact pid to be recycled into a
    session leader within the job's lifetime and is accepted."""
    pgid, lstart = ident
    if _pid_alive(pgid):  # the leader pid is held by a process that is not the recorded leader: a reused pid,
        return False      # and any group/session it leads is not ours
    born = _lstart_epoch(lstart)
    members = _group_members(pgid)
    if born is None or not members:
        return False
    for pid in members:
        try:
            if os.getsid(pid) != pgid:
                return False
        except ProcessLookupError:
            continue  # exited meanwhile
        except OSError:
            return False
        start = _proc_start(pid)
        if not start:
            continue  # exited meanwhile
        t = _lstart_epoch(start)
        if t is None or t < born:
            return False
    return True


def _group_signalable(ident: tuple[int, str]) -> bool:
    return _same_process(*ident) or _leaderless_group_verified(ident)


def _kill_command(job: Path, secs: float = 3.0) -> None:
    """Kill the command's process group (script(1)'s session leader and every child, e.g. sleep or a waiting
    sudo), then tell the runner (SIGUSR1) to show the withdrawal; the runner and its window stay open. Only a
    verified group is signalled: its recorded leader (pid + start time) still matches, or, once the leader has
    exited, every remaining member is in the recorded session and started no earlier than the leader. Never a
    bare pid, never this process's own group. SIGTERM, then SIGKILL after ``secs`` while any member remains (a
    member can outlive the leader by ignoring or deferring TERM, e.g. sudo inside pam_authenticate).

    Callers hold final.lock. The command's shell writes cmd.pgid and only then checks final.lock (exit if taken),
    so one read here is race-free: no cmd.pgid now means the command either never starts or ends by itself."""
    ident = _cmd_ident(job)
    if ident and ident[0] != os.getpgrp() and _group_signalable(ident):
        try:
            os.killpg(ident[0], signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        deadline = time.time() + secs
        while time.time() < deadline and (_same_process(*ident) or _group_members(ident[0]) != []):
            time.sleep(0.1)
        if _group_signalable(ident):
            try:
                os.killpg(ident[0], signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
    for rp, start in runner_idents(job).items():
        if _same_process(rp, start):
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


def is_legacy_job(job: Path) -> bool:
    """The job's runner.sh predates the final gate/withdraw trap (it ignores the withdraw marker, start.lock and
    SIGUSR1, and records no identities)."""
    try:
        return "final.lock" not in (job / "runner.sh").read_text(errors="replace")
    except OSError:
        return False


def legacy_runner_live(job: Path) -> bool:
    """A process running this job's runner.sh exists (argv scan; used only to decide, never to signal). A failed
    scan counts as live: the caller then neither claims withdrawn nor abandons."""
    needle = str(job / "runner.sh")
    try:
        out = subprocess.run(["/bin/ps", "-axww", "-o", "command="], capture_output=True, text=True, timeout=3,
                             env=_ps_env())
    except Exception:
        return True
    if out.returncode != 0:
        return True
    return any(needle in line for line in out.stdout.splitlines())


def _legacy_started(job: Path, st: dict) -> bool:
    return (st.get("status") == "running" or (job / "opened").exists() or (job / "start.lock").exists())


LEGACY_UNSUPPORTED = ("withdraw is unsupported for this job: its window runs a legacy runner (from before withdrawal "
                      "support) that is still open and would ignore the withdrawal, so nothing was withdrawn. It is "
                      "marked abandoned once that window closes.")


def _settle_status(job: Path, st: dict) -> None:
    """status.json reads back the final outcome: a runner that passed its gate just before the final gate was
    taken may have written "running" over it meanwhile."""
    try:
        cur = json.loads((job / "status.json").read_text())
    except (OSError, ValueError):
        cur = {}
    if cur != st:
        _write_status(job, st)


def withdraw_job(job: Path, reason: str) -> dict:
    """Withdraw a job that is still waiting or running: marker JOB/withdraw (holds the reason; a runner that
    starts later refuses), then the final gate, then kill the command group and tell the runner. A job that
    already ended (ran/declined/failed/abandoned) keeps its outcome."""
    reason = " ".join(str(reason or "withdrawn by Hermes").split())[:500] or "withdrawn by Hermes"
    st = read_status(job)
    if st.get("status") in FINAL:
        return st
    if is_legacy_job(job):
        if legacy_runner_live(job):  # honest: no marker, no final gate; the old window keeps its job
            return {**st, "job_id": job.name, "withdraw": "unsupported", "error": LEGACY_UNSUPPORTED}
        if _legacy_started(job, st):
            return mark_abandoned(job, "the terminal window closed before the command finished")
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
    _settle_status(job, new)
    return new


def mark_abandoned(job: Path, why: str) -> dict:
    st = {"status": "abandoned", "reason": why, "job_id": job.name}
    if _take_final(job, st):
        _kill_command(job, 1.0)
        _settle_status(job, st)
        return st
    return read_status(job)


def _valid_minutes(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError, OverflowError):
        return None
    return f if math.isfinite(f) and f > 0 else None


def max_wait_minutes() -> float:
    v: Any = os.environ.get("HERMES_USER_TERMINAL_MAX_WAIT_MINUTES", "")
    try:
        from hermes_cli.config import load_config  # type: ignore

        c = ((load_config() or {}).get("user_terminal") or {}).get("max_wait_minutes")
        if c is not None and str(c).strip():
            v = c
    except Exception:
        pass
    f = _valid_minutes(v)
    if f is None:
        if str(v).strip():
            log.warning("user_terminal.max_wait_minutes=%r is not a finite positive number; using %g", v,
                        DEFAULT_MAX_WAIT_MIN)
        return DEFAULT_MAX_WAIT_MIN
    return f


def reattach_grace_minutes() -> float:
    """How long a card-owned request survives the card being unblocked or moving to a new run without that run
    re-attaching (``user_terminal.reattach_grace_minutes``, env HERMES_USER_TERMINAL_REATTACH_GRACE_MINUTES;
    finite and > 0, else 10)."""
    v: Any = os.environ.get("HERMES_USER_TERMINAL_REATTACH_GRACE_MINUTES", "")
    try:
        from hermes_cli.config import load_config  # type: ignore

        c = ((load_config() or {}).get("user_terminal") or {}).get("reattach_grace_minutes")
        if c is not None and str(c).strip():
            v = c
    except Exception:
        pass
    f = _valid_minutes(v)
    if f is None:
        if str(v).strip():
            log.warning("user_terminal.reattach_grace_minutes=%r is not a finite positive number; using %g", v,
                        DEFAULT_REATTACH_GRACE_MIN)
        return DEFAULT_REATTACH_GRACE_MIN
    return f


def expiry_reason(minutes: float) -> str:
    return f"expired after {minutes:g} min; ask again if still needed"


def _ro_connect(path: Any, timeout: float = 2):
    """Read-only open of a live board: URI with the path percent-quoted (a '?', '#' or '%' in it would otherwise
    end the path), mode=ro first; a WAL db whose -wal/-shm sidecars are gone fails that, so fall back to mode=rw
    (never rwc: a missing db is never created) with PRAGMA query_only=ON, which never writes rows."""
    import sqlite3
    from urllib.parse import quote

    uri = "file:" + quote(str(path))
    try:
        c = sqlite3.connect(uri + "?mode=ro", uri=True, timeout=timeout)
        try:
            c.execute("select 1 from sqlite_master limit 1")
        except sqlite3.OperationalError:
            c.close()
            raise
    except sqlite3.OperationalError:
        c = sqlite3.connect(uri + "?mode=rw", uri=True, timeout=timeout)
    c.execute("PRAGMA query_only=ON")
    return c


def board_event_cursor(db: str | None) -> int | None:
    """The board's newest task_events id right now (None when unreadable): events after it are newer than the
    request, which a whole-second created_at cannot tell within the same second."""
    import sqlite3

    if not db or not Path(db).exists():
        return None
    con = None
    try:
        con = _ro_connect(db)
        return int(con.execute("SELECT COALESCE(MAX(id), 0) FROM task_events").fetchone()[0])
    except (sqlite3.Error, TypeError, ValueError) as e:
        log.warning("user_terminal: could not read the event cursor of board db %s: %s", db, e)
        return None
    finally:
        if con is not None:
            con.close()


def _has_table(con, name: str) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _as_int(v: Any) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


# Only these core lifecycle outcomes express a decision to stop using an unattached
# request. Infrastructure failures, continuation, NULL and future outcomes get grace.
_UNATTACHED_STOP_OUTCOMES = frozenset({"completed", "blocked", "scheduled", "review_requested"})


def _board_check(owner: dict, created: float, attachment: dict | None = None,
                 grace_minutes: float | None = None, now: float | None = None) -> str | None:
    """Reason the owning card no longer wants this prompt, or None. Read-only (_ro_connect: mode=ro, else
    mode=rw + query_only); an unreadable board is logged, never written to.

    Immediate: the card is done/completed/archived, or a run later than the acknowledged one (the attached run,
    else the creating run) has ENDED with an explicit stop outcome without re-attaching. Grace: an unblock after
    the acknowledged event cursor (the attachment's, else the creation cursor) or any other later run starts re-attach
    grace, measured from the EARLIEST such board event, so polls can never extend it; past it -> withdrawn."""
    import sqlite3

    task, db = owner.get("task_id") or owner.get("task"), owner.get("db")
    if not task or not db:
        return None
    if not Path(db).exists():
        log.warning("user_terminal: board db %s for card %s does not exist", db, task)
        return None
    att = attachment or {}
    grace = _valid_minutes(grace_minutes) or reattach_grace_minutes()
    now = time.time() if now is None else now
    con = None
    try:
        con = _ro_connect(db)
        row = con.execute("SELECT status, current_run_id FROM tasks WHERE id=?", (task,)).fetchone()
        if not row:
            return None
        status, cur = row
        if status in ("done", "completed", "archived"):
            return f"card {task} is {status}"
        pending: list[tuple[float, str]] = []  # (board time it started, reason once the grace is over)
        cursor = _as_int(att.get("event_cursor")) if att else _as_int(owner.get("event_cursor"))
        if cursor is not None:  # exact: events written after the request / the last attach
            ev = con.execute("SELECT MIN(created_at) FROM task_events WHERE task_id=? AND kind='unblocked' AND id>?",
                             (task, cursor)).fetchone()
        else:  # jobs from before the cursor: whole-second timestamp (an unblock in the same second counts)
            ev = con.execute("SELECT MIN(created_at) FROM task_events WHERE task_id=? AND kind='unblocked' AND "
                             "created_at>=?", (task, int(created))).fetchone()
        if ev and ev[0] is not None:
            pending.append((float(ev[0]), f"card {task} was unblocked"))
        mine = _as_int(att.get("run_id")) if att else None
        if mine is None:
            mine = owner.get("run_id")
            mine = int(mine) if isinstance(mine, (int, str)) and str(mine).isdigit() else None
        if mine is not None:
            if _has_table(con, "task_runs"):
                for rid, started, ended, outcome in con.execute(
                        "SELECT id, started_at, ended_at, outcome FROM task_runs WHERE task_id=? AND id>? ORDER BY id",
                        (task, mine)).fetchall():
                    if ended is not None and outcome in _UNATTACHED_STOP_OUTCOMES:
                        return (f"card {task}'s later run {rid} ended ({outcome or 'no outcome'}) without "
                                f"re-attaching to this request")
                    pending.append((float(started if started is not None else created),
                                    f"card {task} moved on to a newer run ({rid})"))
            elif cur is not None and int(cur) > mine:  # a board without task_runs: the run's first event
                ev = con.execute("SELECT MIN(created_at) FROM task_events WHERE task_id=? AND run_id=?",
                                 (task, int(cur))).fetchone()
                start = float(ev[0]) if ev and ev[0] is not None else created
                pending.append((start, f"card {task} moved on to a newer run ({cur})"))
        if pending:
            start, what = min(pending)
            if now - start >= grace * 60:
                return (f"{what} {max(0.0, (now - start) / 60):.0f} min ago and no run re-attached to this "
                        f"request within the {grace:g} min grace")
        return None
    except sqlite3.Error as e:
        log.warning("user_terminal: could not read board db %s for card %s: %s", db, task, e)
        return None
    finally:
        if con is not None:
            con.close()


def _job_meta_owner(job: Path) -> tuple[dict, dict]:
    """(meta, owner with db) from meta.json, else the legacy owner.json sidecar. Missing files -> {}."""
    try:
        meta = json.loads((job / "meta.json").read_text())
    except (OSError, ValueError):
        meta = {}
    if not isinstance(meta, dict):
        raise ValueError(f"{job / 'meta.json'} is not a JSON object")
    owner = meta.get("owner")
    if isinstance(owner, dict) and owner:
        owner = {**owner, "db": meta.get("board_db"), "event_cursor": meta.get("board_event_cursor")}
    else:
        try:
            owner = json.loads((job / "owner.json").read_text())
        except (OSError, ValueError):
            owner = {}
        if owner.get("kind") == "kanban":
            owner = {**owner, "kind": "card", "task_id": owner.get("task")}
    return meta, owner if isinstance(owner, dict) else {}


ATTACH_LOCK_WAIT_S = 3.0


def _same_db(a: Any, b: Any) -> bool:
    if not a or not b:
        return False
    try:
        return os.path.realpath(str(a)) == os.path.realpath(str(b))
    except (OSError, ValueError):
        return False


def read_attachment(job: Path, owner: dict | None = None) -> dict | None:
    """The job's attachment sidecar (JOB/attachment.json: the run of the OWNING card that re-attached with
    run_in_user_terminal(job_id=...) and the board event cursor at that moment). A record naming another card or
    board is ignored: only attach_job() writes it, and only after authenticating the caller."""
    try:
        att = json.loads((job / "attachment.json").read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(att, dict) or _as_int(att.get("run_id")) is None:
        return None
    if owner is None:
        _, owner = _job_meta_owner(job)
    task = owner.get("task_id") or owner.get("task")
    if not task or att.get("task_id") != task or not _same_db(att.get("board_db"), owner.get("db")):
        log.warning("user_terminal: ignoring attachment of %s: it names another card or board", job.name)
        return None
    return att


def _attach_lock(job: Path) -> bool:
    """attachment.lock: atomic mkdir, owner "pid lstart" (the watchdog.lock convention). A lock whose owner is
    gone is stale and broken; a live holder is waited for up to ATTACH_LOCK_WAIT_S."""
    lock = job / "attachment.lock"
    me = f"{os.getpid()} {_proc_start(os.getpid())}"
    deadline = time.time() + ATTACH_LOCK_WAIT_S
    import fcntl

    while True:
        # A stable guard inode serializes stale-owner inspection with replacement. Without it,
        # two contenders can both inspect the dead owner and the second steals the first's new lock.
        with (job / "attachment.guard").open("a") as guard:
            fcntl.flock(guard, fcntl.LOCK_EX)
            try:
                lock.mkdir()
                (lock / "owner").write_text(me + "\n")
                return True
            except FileExistsError:
                pass
            try:
                ident = _parse_ident((lock / "owner").read_text().strip())
            except OSError:
                ident = None
            stale = False
            if ident is None:
                try:
                    stale = time.time() - lock.stat().st_mtime >= 5
                except OSError:
                    continue
            elif ident[0] != os.getpid() and not _same_process(*ident):
                stale = True
            if stale:
                shutil.rmtree(lock)
                continue
        if time.time() >= deadline:
            return False
        time.sleep(0.02)


def _attach_unlock(job: Path) -> None:
    import fcntl

    with (job / "attachment.guard").open("a") as guard:
        fcntl.flock(guard, fcntl.LOCK_EX)
        shutil.rmtree(job / "attachment.lock")


def attach_job(job: Path, session_id: str | None = None) -> dict:
    """Re-attach a card-owned job to the caller's run: run_in_user_terminal(job_id=...) calls this BEFORE any sweep
    or check, so a resumed run never withdraws its own request. Only the owning card's current, live run on the
    same board db may attach (worker env HERMES_KANBAN_TASK/RUN_ID + the board db the caller resolves); the
    original owner in meta.json is never changed. Records JOB/attachment.json (tmp + rename, under
    attachment.lock): {task_id, board_db, run_id, event_cursor, attached_at}. {} when the job is not card-owned or
    already final; else {"attached": bool, ...} with "why" when refused."""
    import sqlite3

    if not job.is_dir() or (job / "final.lock").exists() or read_status(job).get("status") in FINAL:
        return {}
    meta, owner = _job_meta_owner(job)
    if owner.get("kind") != "card":
        return {}
    task, db = owner.get("task_id") or owner.get("task"), owner.get("db")
    caller = job_owner(session_id)

    def no(why: str) -> dict:
        return {"attached": False, "why": why}
    if caller.get("task_id") != task:  # a chat session (kind "session") has no task_id
        who = f"card {caller.get('task_id')}" if caller.get("kind") == "card" else "a caller that is not a Kanban worker"
        return no(f"this request belongs to card {task}; {who} cannot re-attach it")
    run = _as_int(caller.get("run_id"))
    if run is None:
        return no("the caller has no HERMES_KANBAN_RUN_ID")
    if not db or not _same_db(board_db_path(str(caller.get("board") or "default")), db):
        return no(f"the caller's board is not the board of card {task}")
    if not Path(str(db)).exists():
        return no(f"board db {db} does not exist")
    if not _attach_lock(job):
        return no("another attach of this job is in progress")
    try:
        # Validate the live/current run AFTER taking the decision lock. An infra
        # end retains grace but must never authorize a stale caller to acknowledge it.
        con = None
        try:
            con = _ro_connect(db)
            row = con.execute("SELECT status, current_run_id FROM tasks WHERE id=?", (task,)).fetchone()
            if not row:
                return no(f"card {task} is not on its board")
            if row[1] is None or int(row[1]) != run:
                return no(f"run {run} is not the current run of card {task} (current: {row[1]})")
            if _has_table(con, "task_runs"):
                r = con.execute("SELECT task_id, ended_at FROM task_runs WHERE id=?", (run,)).fetchone()
                if not r or r[0] != task or r[1] is not None:
                    return no(f"run {run} is not a live run of card {task}")
            cursor = int(con.execute("SELECT COALESCE(MAX(id), 0) FROM task_events").fetchone()[0])
        except (sqlite3.Error, TypeError, ValueError) as e:
            log.warning("user_terminal: attach of %s could not read board db %s: %s", job.name, db, e)
            return no(f"could not read the board: {e}")
        finally:
            if con is not None:
                con.close()
        prev = read_attachment(job, owner)
        created = float(meta.get("created") or time.time())
        why = _board_check(owner, created, prev, meta.get("reattach_grace_minutes"))
        if why:  # already over (done, a later run ended, or the grace ran out): attaching cannot revive it
            return no(why)
        if prev and int(prev["run_id"]) > run:
            return no(f"run {prev['run_id']} of card {task} is already attached")
        if prev and int(prev["run_id"]) == run:
            cursor = max(cursor, _as_int(prev.get("event_cursor")) or 0)
        rec = {"task_id": task, "board_db": db, "run_id": run, "event_cursor": cursor, "attached_at": time.time()}
        tmp = job / f".attachment.{uuid.uuid4().hex[:8]}.tmp"
        tmp.write_text(json.dumps(rec))
        os.replace(tmp, job / "attachment.json")
        return {"attached": True, "run_id": run, "event_cursor": cursor}
    finally:
        _attach_unlock(job)


def _fence_abandon(job: Path, why: str) -> dict | None:
    """Take the start gate first (a window that opens later refuses), then record abandoned. None when a runner
    took the gate meanwhile: that runner owns the job."""
    try:
        (job / "start.lock").mkdir()
    except FileExistsError:
        return None
    (job / "start.lock" / "owner").write_text(f"fence {os.getpid()}\n")
    return mark_abandoned(job, why)


def _runner_gone(job: Path, st: dict, seen: dict, legacy: bool) -> bool:
    """A runner announced itself and none of them is alive (pid + start time). Legacy runners record no identity:
    their argv is scanned instead."""
    if st.get("status") not in ("waiting_for_user", "running"):
        return False
    try:
        own = (job / "start.lock" / "owner").read_text().split()
    except OSError:
        own = []
    # A duplicate window that refused exits normally, so once a runner holds start.lock only that one counts.
    pids = [int(own[1])] if len(own) == 2 and own[0] == "runner" and own[1].isdigit() else (
        [] if (job / "start.lock").exists() else runner_pids(job))
    if legacy:
        return (bool(pids) or _legacy_started(job, st)) and not legacy_runner_live(job)
    if not pids:
        return False
    idents = runner_idents(job)
    for pid in pids:
        start = idents.get(pid) or seen.setdefault(pid, _proc_start(pid))
        if _pid_alive(pid) and (not start or _same_process(pid, start)):
            return False
    return True


def _kanban_api():
    """Lazy core access for a bare-Python watchdog; never replace active modules.

    Detached launchers carry the parent's core root. Older jobs use the installed
    root. Only a fresh core graph activates its committed dependency environment;
    an already-loaded backend keeps its own modules and dependency selection.
    """
    import importlib.util
    import sys

    if "hermes_cli" not in sys.modules:
        spec = importlib.util.find_spec("hermes_cli")
        if spec is None:
            root = _home()
            if root.parent.name == "profiles":
                root = root.parent.parent
            candidates = [os.environ.get("HERMES_USER_TERMINAL_CORE_ROOT"),
                          os.environ.get("HERMES_CORE_CHECKOUT"), root / "hermes-agent",
                          Path.home() / ".hermes" / "hermes-agent"]
            for candidate in candidates:
                if candidate and (Path(candidate) / "hermes_cli" / "__init__.py").is_file():
                    sys.path.insert(0, str(Path(candidate).resolve()))
                    spec = importlib.util.find_spec("hermes_cli")
                    break
        if spec is None or not spec.origin:
            raise ImportError("user-terminal watchdog cannot locate Hermes core")
        root = Path(spec.origin).resolve().parent.parent
        # Unlike hermes_bootstrap this does not relaunch or change the command.
        from pm.environments import activate_dependencies
        activate_dependencies(root)
    from hermes_cli import kanban_db
    from hermes_cli.kanban_human_request_migration import classify_parked_request
    return kanban_db, classify_parked_request


def reconcile_closed_ask(job: Path, st: dict) -> None:
    """Amend only the action of a still-parked owner; errors never change job results.

    One board transaction covers eligibility, classification and the per-job audit
    marker. The attachment lock serializes against a resumed run's publication.
    Core write_txn enforces descendant fences; no worker identity is removed here.
    """
    status = st.get("status")
    if status not in ("failed", "abandoned") and not (
            status == "withdrawn" and str(st.get("reason", "")).startswith("expired after ")):
        return
    con = None
    locked = False
    try:
        import sqlite3
        from datetime import datetime
        from zoneinfo import ZoneInfo

        _, owner = _job_meta_owner(job)
        task, path = owner.get("task_id") or owner.get("task"), owner.get("db")
        if owner.get("kind") != "card" or not task or not path:
            return
        # Leave worker guards intact; the already-detached watchdog owns retries.
        if os.environ.get("HERMES_KANBAN_TASK"):
            return
        locked = _attach_lock(job)
        if not locked:
            return
        att = read_attachment(job, owner)
        original_run = owner.get("run_id")
        if att and (original_run is None or int(att["run_id"]) > int(original_run)):
            return
        db, classify = _kanban_api()
        db._assert_not_delegated_child_mutation(Path(path))
        # Never create or migrate a board merely to reconcile a job.
        from hermes_cli.kanban_lockguard import connect
        con = connect(Path(path).resolve().as_uri() + "?mode=rw", uri=True, timeout=2)
        con.row_factory = sqlite3.Row
        marker = f"[user-terminal:closed:{job.name}]"
        with db.write_txn(con):
            row = con.execute("SELECT status, current_run_id, human_request FROM tasks WHERE id=?",
                              (task,)).fetchone()
            if not row or row["status"] != "blocked" or row["current_run_id"] is not None:
                return
            request = json.loads(row["human_request"]) if row["human_request"] else None
            if not isinstance(request, dict) or not isinstance(request.get("action"), str):
                return
            if con.execute("SELECT 1 FROM task_comments WHERE task_id=? AND instr(body, ?)>0",
                           (task, marker)).fetchone():
                return
            # Final-file mtime also gives old jobs a stable end time across retries.
            final = job / "final.lock" / "result.json"
            ended = (final if final.exists() else job / "status.json").stat().st_mtime
            local = datetime.fromtimestamp(ended, ZoneInfo("America/Los_Angeles"))
            action = (f"window closed {local:%H:%M %Z}. Unblock = agent reopens window; "
                      "archive = drop request.")
            amended = {**request, "action": action}
            cause = st.get("reason") or st.get("error") or status
            reason = f"Terminal job {job.name} ended: {status}: {cause}; {local.isoformat()}"
            if classify(con, task, human_request=amended, actor="user-terminal", reason=reason):
                stored = con.execute("SELECT human_request FROM tasks WHERE id=?", (task,)).fetchone()[0]
                if json.loads(stored) != amended:
                    raise ValueError("classification altered fields beyond human_request.action")
                db.add_comment(con, task, "user-terminal", f"{marker} {reason}. {action}")
    except Exception:
        log.exception("user_terminal: blocked ask reconciliation failed for %s; will retry on sweep", job.name)
    finally:
        try:
            if con is not None:
                con.close()
            if locked:
                _attach_unlock(job)
        except Exception:
            log.exception("user_terminal: reconciliation cleanup failed for %s", job.name)


def watch_once(job: Path, seen: dict) -> dict | None:
    result = _watch_once(job, seen)
    if result and result.get("status") in FINAL:
        reconcile_closed_ask(job, result)
    return result


def _watch_once(job: Path, seen: dict) -> dict | None:
    """One watchdog / sweep pass. Returns the final status if the job is (now) over, else None (a non-final dict
    when a withdrawal could not be done, e.g. a live legacy runner).

    Order: abandonment (no window ever started / the window died) is decided before the owner and max-age
    checks, so a dead job reads ``abandoned`` rather than withdrawn or expired."""
    if not job.is_dir():
        return {"status": "gone", "job_id": job.name}
    st = read_status(job)
    if st.get("status") in FINAL:
        return st
    if (job / "withdraw").exists():
        return withdraw_job(job, (job / "withdraw").read_text())
    meta, owner = _job_meta_owner(job)
    created = float(meta.get("created") or time.time())
    age = time.time() - created
    legacy = is_legacy_job(job)
    # No window ever announced itself within the launch grace (the openers are long done: fail_job reports their
    # real errors). Fence the start gate, then abandoned: nothing ran, and a late window refuses.
    if (age > LAUNCH_GRACE_S and st.get("status") == "waiting_for_user" and not (job / "opened").exists()
            and not (job / "start.lock").exists() and not (legacy and legacy_runner_live(job))):
        r = _fence_abandon(job, f"no terminal window started within {LAUNCH_GRACE_S:g} s; nothing ran")
        if r is not None:
            return r
        st = read_status(job)
    if _runner_gone(job, st, seen, legacy):
        return mark_abandoned(job, "the terminal window closed before the command finished")
    if owner.get("kind") == "card":
        # Serialize the board decision with attachment publication: a stale sidecar read must
        # never withdraw a job whose resumed owner attached before the grace deadline.
        if not _attach_lock(job):
            return None
        try:
            why = _board_check(owner, created, read_attachment(job, owner), meta.get("reattach_grace_minutes"))
            if why:
                return withdraw_job(job, why)
        finally:
            _attach_unlock(job)
    limit = _valid_minutes(meta.get("max_wait_minutes")) or max_wait_minutes()
    if age > limit * 60:
        return withdraw_job(job, expiry_reason(limit))
    return None


def sweep_jobs(skip: Path | None = None) -> None:
    """Reconcile jobs including old final outcomes whose blocked asks may still be stale; never raises."""
    try:
        jobs = [p for p in jobs_dir().iterdir() if p.is_dir() and p != skip]
    except OSError:
        return
    for job in jobs:
        try:
            watch_once(job, {})
        except Exception:
            log.exception("user_terminal: reconciling %s failed", job)


def _claim_watchdog(job: Path) -> bool:
    """The job's single watchdog: mkdir watchdog.lock (owner "pid lstart"). A lock whose owner is gone is stale."""
    lock = job / "watchdog.lock"
    me = f"{os.getpid()} {_proc_start(os.getpid())}"
    for _ in range(3):
        try:
            lock.mkdir()
            (lock / "owner").write_text(me + "\n")
            return True
        except FileExistsError:
            try:
                ident = _parse_ident((lock / "owner").read_text().strip())
            except OSError:
                ident = None
            if ident is None:
                try:  # being written right now, or a crash between mkdir and owner: young -> respect it
                    if time.time() - lock.stat().st_mtime < 5:
                        return False
                except OSError:
                    continue
            elif _same_process(*ident):
                return False
            stale = job / f".watchdog.stale.{uuid.uuid4().hex[:8]}"
            try:
                os.rename(lock, stale)
                shutil.rmtree(stale, ignore_errors=True)
            except OSError:
                pass
    return False


def _release_watchdog(job: Path) -> None:
    lock = job / "watchdog.lock"
    try:
        if (lock / "owner").read_text().split()[0] == str(os.getpid()):
            shutil.rmtree(lock, ignore_errors=True)
    except (OSError, IndexError):
        pass


def watchdog(job: Path, interval: float = WATCH_INTERVAL_S, max_s: float | None = None) -> dict:
    if not _claim_watchdog(job):
        import sys

        print(f"user_terminal watchdog: {job.name} is already watched; exiting", file=sys.stderr)
        return read_status(job)
    try:
        seen: dict = {}
        if max_s is None:
            meta, _ = _job_meta_owner(job)
            limit = _valid_minutes(meta.get("max_wait_minutes")) or max_wait_minutes()
            created = float(meta.get("created") or time.time())
            end = max(created + limit * 60 + 120, time.time() + LAUNCH_GRACE_S + 30)
        else:
            end = time.time() + max_s
        while time.time() < end:
            r = watch_once(job, seen)
            if r and r.get("status") in FINAL + ("gone",):
                return r
            time.sleep(max(0.05, min(interval, WATCH_INTERVAL_S)))
        return watch_once(job, seen) or read_status(job)
    finally:
        _release_watchdog(job)


def _watchdog_script() -> str:
    import sys

    root = launch_env().get("HERMES_USER_TERMINAL_CORE_ROOT", "")
    export = f"export HERMES_USER_TERMINAL_CORE_ROOT={shlex.quote(root)}\n" if root else ""
    return (f"#!/bin/bash\n# this job's watchdog (one per job; see watchdog.lock)\n" + export +
            f"exec {shlex.quote(sys.executable)} {shlex.quote(str(Path(__file__).resolve()))} --watchdog \"$1\"\n")


def spawn_watchdog(job: Path) -> None:
    """Job-local detached watchdog (this file run as a script), polling every 15 s. Its stderr (and any spawn
    failure) goes to JOB/watchdog.log; a second watchdog for the same job exits (watchdog.lock)."""
    import sys

    logf = None
    try:
        logf = open(job / "watchdog.log", "ab")
        subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--watchdog", str(job)],
                         stdin=subprocess.DEVNULL, stdout=logf, stderr=logf,
                         start_new_session=True, env=launch_env())
    except Exception as e:
        log.error("user_terminal: could not start the watchdog for %s: %s", job.name, e)
        try:
            with open(job / "watchdog.log", "a") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} spawn failed: {type(e).__name__}: {e}\n")
        except OSError:
            pass
    finally:
        if logf is not None:
            logf.close()


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


def _reconcile(job: Path) -> None:
    try:
        watch_once(job, {})
    except Exception:
        log.exception("user_terminal: reconciling %s failed", job)


def _card_note(job: Path) -> str:
    """Blocked-ask guidance for a card-owned pending request."""
    try:
        meta, owner = _job_meta_owner(job)
    except ValueError:
        return ""
    if owner.get("kind") != "card":
        return ""
    grace = _valid_minutes(meta.get("reattach_grace_minutes")) or reattach_grace_minutes()
    return (f"If card {owner.get('task_id') or owner.get('task')} blocks to wait for the user, put this job_id in "
            f"the block reason: once the card is unblocked or a new run starts, that run must re-attach by calling "
            f"run_in_user_terminal with this job_id within {grace:g} min (user_terminal.reattach_grace_minutes), "
            f"or the request is withdrawn; a later unattached run ending as completed, blocked, scheduled or review_requested "
            f"withdraws it at once. Infrastructure failures, continuation and unknown outcomes keep the same grace.")


def wait_for(job: Path, wait_s: float, reconcile: bool = False) -> dict:
    """Wait for a final status. reconcile=True (poll/resume) also runs a watchdog pass now and every
    WATCH_INTERVAL_S, so a job whose watchdog never started (or died) still ends."""
    deadline = time.time() + wait_s
    if reconcile:
        _reconcile(job)
    next_check = time.time() + WATCH_INTERVAL_S
    st = read_status(job)
    while st.get("status") not in FINAL and time.time() < deadline and not _interrupted():
        time.sleep(0.5)
        if reconcile and time.time() >= next_check:
            _reconcile(job)
            next_check = time.time() + WATCH_INTERVAL_S
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
                          "run_in_user_terminal with this job_id to keep waiting. If it is no longer needed, "
                          "withdraw it immediately (action=withdraw, this job_id, a reason).")
        card_note = _card_note(job)
        if card_note:
            result["note"] += " " + card_note
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
        attached: dict = {}
        withdraw = str(args.get("action") or "").strip().lower() == "withdraw"
        resume_id = str(args.get("job_id") or "").strip()
        if resume_id and not withdraw and (jobs_dir() / Path(resume_id).name).is_dir():
            # before the sweep and the job's own check: a resumed owning run never withdraws its own request
            attached = attach_job(jobs_dir() / Path(resume_id).name, session_id)
        sweep_jobs(skip=jobs_dir() / Path(str(args.get("job_id") or "x")).name)
        if withdraw:
            job_id = str(args.get("job_id") or "").strip()
            job = jobs_dir() / Path(job_id).name
            if not job_id or not job.is_dir():
                return _err(f"unknown job_id {job_id}")
            st = withdraw_job(job, str(args.get("reason") or "withdrawn by Hermes"))
            return json.dumps({"ok": "error" not in st, "job_id": job.name, **st})
        wait_s = float(args.get("wait_s") or DEFAULT_WAIT)
        wait_s = max(1.0, min(wait_s, MAX_WAIT))
        job_id = str(args.get("job_id") or "").strip()
        if job_id:
            job = jobs_dir() / Path(job_id).name
            if not job.is_dir():
                return _err(f"unknown job_id {job_id}")
            res = wait_for(job, wait_s, reconcile=True)
            if attached:
                res["attachment"] = attached
            return json.dumps(res)
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
    job = create_job(command, str(reason or ""), cwd, origin,
                     owner={"kind": "relayed", "gateway": str(origin.get("gateway") or "")})
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
    return wait_for(job, max(0.0, min(float(wait_s or 0), 25.0)), reconcile=True)


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


def watchdog_main(argv: list[str]) -> int:
    """Detached watchdog entry point: own session (survives the window's SIGHUP), log to stderr (JOB/watchdog.log),
    and a crash prints its traceback and exits 1 rather than vanishing."""
    import sys
    import traceback

    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(asctime)s %(levelname)s %(message)s")
    try:
        os.setsid()
    except OSError:
        pass  # already a session leader (spawned with start_new_session)
    try:
        watchdog(Path(argv[0]), interval=float(argv[2]) if len(argv) > 2 else WATCH_INTERVAL_S,
                 max_s=float(argv[1]) if len(argv) > 1 else None)
        return 0
    except Exception:
        print(f"user_terminal watchdog for {argv[0]} crashed:", file=sys.stderr)
        traceback.print_exc()
        return 1


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

    if len(_sys.argv) == 3 and _sys.argv[1] == "--open-window":
        _sys.exit(opener_main(Path(_sys.argv[2])))
    if len(_sys.argv) >= 3 and _sys.argv[1] == "--watchdog":  # --watchdog JOB [max_s] [interval]
        _sys.exit(watchdog_main(_sys.argv[2:]))
