"""cpu-load-guard: refuse host-saturating load generators on protected hosts.

A ``pre_tool_call`` hook inspects terminal (foreground and background), process writes,
execute_code, write_file/patch of script files, and local scripts a command runs (size- and depth-capped) on hosts listed in ``protected_hosts`` of ``~/.hermes/cpu-load-guard.yaml``
(default: the user's laptop and hermes-hub; ``CPU_LOAD_GUARD_HOSTS=a,b`` overrides; ``CPU_LOAD_GUARD=off`` disables).
A path passed as data to a reader or file tool (grep, cat, sed, git, cp, ...) is not inspected.

Limits: regex heuristics. Obfuscated burners, scripts fetched or generated at run time, scripts over
256 KB or nested more than two levels, and compiled programs are not inspected.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Keep this boundary in the activation module, not in the code it reloads.
# Each guard currently has exactly one sibling decision module: guard.py.
from functools import wraps as _wraps
import importlib.machinery as _machinery
import importlib.util as _import_util
from pathlib import Path as _Path
from threading import RLock as _RLock


class _DecisionSourceLoader(_machinery.SourceFileLoader):
    def get_code(self, fullname):
        # Timestamp pyc files have only second precision: never reuse stale code
        # for a same-size edit within a second, and never write runtime bytecode.
        return self.source_to_code(self.get_data(self.path), self.path)


_decision_path = _Path(__file__).with_name("guard.py")
_decision_lock = _RLock()


def _decision_stamp():
    try:
        st = _decision_path.stat()
        return (st.st_mtime_ns, st.st_size)
    except OSError as exc:
        return ("unavailable", exc.errno)


def _load_decision():
    # No relative-import cache or timestamp pyc, including on initial activation.
    name = __name__ + ".guard"
    loader = _DecisionSourceLoader(name, str(_decision_path))
    spec = _import_util.spec_from_file_location(name, _decision_path, loader=loader)
    assert spec is not None and spec.loader is not None
    fresh = _import_util.module_from_spec(spec)
    spec.loader.exec_module(fresh)
    return fresh


# Stamp BEFORE loading: an edit racing initial activation is retried next call.
_decision_seen = _decision_stamp()
guard = _load_decision()


def _refresh_decisions():
    global guard, _decision_seen
    with _decision_lock:
        stamp = _decision_stamp()
        if stamp == _decision_seen:
            return
        # Remember failures too: warn once per changed fingerprint, not per call.
        _decision_seen = stamp
        try:
            fresh = _load_decision()
            if _decision_stamp() != stamp:
                raise RuntimeError("decision source changed during reload")
        except Exception:
            logger.warning("cpu-load-guard: decision reload failed; keeping last good guard.py", exc_info=True)
            return
        guard = fresh
        logger.info("cpu-load-guard: reloaded guard.py")


def _with_decisions(fn):
    @_wraps(fn)
    def call(*args, **kwargs):
        # Keep enabled/classify/message on one module version across threads.
        with _decision_lock:
            _refresh_decisions()
            return fn(*args, **kwargs)
    return call


SHELL_TOOLS = {"terminal", "process_manage", "process"}
PY_TOOLS = {"execute_code"}
WRITE_TOOLS = {"write_file"}
PATCH_TOOLS = {"patch"}
_OWN_DIR = Path(__file__).resolve().parent
_V4A_FILE_RE = re.compile(r"^\*\*\* (?:Add|Update) File:\s*(.+)$", re.M)


def _own_file(path: str) -> bool:
    # Editing this plugin (its tests quote the burners) must stay possible on a protected host.
    try:
        return _OWN_DIR in Path(os.path.expanduser(path)).resolve().parents
    except Exception:
        return False


def _check_patch(args: dict) -> Optional[str]:
    if args.get("mode") == "patch" or args.get("patch"):
        text = str(args.get("patch") or "")
        added = "\n".join(line[1:] for line in text.splitlines() if line.startswith("+"))
        paths = [p.strip() for p in _V4A_FILE_RE.findall(text)] or ["patch.sh"]
        for p in paths:
            if not _own_file(p):
                why = guard.check_write(p, added)
                if why:
                    return why
        return None
    path = str(args.get("path") or "")
    if _own_file(path):
        return None
    new = str(args.get("new_string") or "")
    content = new
    try:  # judge the file as it will be after the edit, not only the inserted lines
        cur = Path(os.path.expanduser(path)).read_text(errors="replace")
        old = str(args.get("old_string") or "")
        if old and old in cur:
            content = cur.replace(old, new) if args.get("replace_all") else cur.replace(old, new, 1)
    except Exception:
        pass
    return guard.check_write(path or "patch.sh", content)


def live_terminal_cwd(task_id: str = "", session_id: str = "") -> Optional[str]:
    """Best-effort: the directory the agent's terminal shell is in now (it persists `cd`
    across calls), so `bash load-suite.sh` after an earlier `cd` resolves where the shell will.
    Order: the live env's cwd, then the session cwd record. None when unknown (no Hermes, no env)."""
    try:
        from tools import terminal_tool as tt
    except Exception:
        return None
    try:
        eff = tt._resolve_container_task_id(task_id or None)
        lock = tt._env_lock
        if lock.acquire(timeout=0.5):
            try:
                env = tt._lookup_active_env(eff, task_id or None)
            finally:
                lock.release()
            cwd = getattr(env, "cwd", None)
            if cwd and os.path.isdir(os.path.expanduser(str(cwd))):
                return str(cwd)
    except Exception:
        pass
    for key in (task_id, session_id):
        try:
            cwd = tt.get_session_cwd(key or None)
            if cwd and os.path.isdir(os.path.expanduser(str(cwd))):
                return str(cwd)
        except Exception:
            pass
    return None


def classify(tool_name: str, args: Any, task_id: str = "", session_id: str = "") -> Optional[str]:
    args = args if isinstance(args, dict) else {}
    name = tool_name[5:] if tool_name.startswith("mcp__") else tool_name
    if name in SHELL_TOOLS:
        cwd = args.get("workdir") or args.get("cwd") or live_terminal_cwd(task_id, session_id)
        text = " ".join(str(args.get(k) or "") for k in ("command", "input", "data"))
        return guard.check_shell(text, str(cwd) if cwd else None)
    if name in PY_TOOLS:
        # hermes_tools.terminal() inside execute_code runs in the same persisted shell
        return guard.check_python(str(args.get("code") or ""), live_terminal_cwd(task_id, session_id))
    if name in WRITE_TOOLS:
        path = str(args.get("path") or "")
        return None if _own_file(path) else guard.check_write(path, str(args.get("content") or ""))
    if name in PATCH_TOOLS:
        return _check_patch(args)
    if name == "tool_call":
        for call in args.get("calls") or []:
            if isinstance(call, dict):
                why = classify(str(call.get("name") or ""), call.get("arguments"), task_id, session_id)
                if why:
                    return why
    return None


@_with_decisions
def _pre_tool_call(tool_name: str = "", args: Any = None, task_id: str = "", session_id: str = "",
                   **_: Any) -> Optional[dict]:
    try:
        if not guard.enabled():
            return None
        why = classify(tool_name, args, str(task_id or ""), str(session_id or ""))
    except Exception:  # never let this plugin break a tool call
        logger.debug("cpu-load-guard classify failed", exc_info=True)
        return None
    if not why:
        return None
    logger.info("cpu-load-guard blocked %s: %s", tool_name, why)
    return {"action": "block", "message": guard.message(why)}


def register(ctx) -> None:
    ctx.register_hook("pre_tool_call", _pre_tool_call)
