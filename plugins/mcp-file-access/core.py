"""Approval-gated file access for the Chrome DevTools MCP server.

chrome-devtools-mcp only touches files inside its "workspace roots": its own temp dir plus any
``--filesystem-root`` args. Anything else fails with "not within any of the configured workspace
roots". This module:

* ``gate`` (pre_tool_call hook): when a call names a file outside those roots, returns Hermes'
  ``approve`` directive so the user gets the standard Once / Session / Always / Deny card. The
  rule key is per folder and per direction (read vs write); a Session/Always grant on a folder
  also covers its subfolders.
* ``prepare`` (second pre_tool_call callback): returns a ``modify`` directive pointing the call
  at copies in the server's temp dir (outputs: at a temp slot). Hermes applies it only if the
  approval passes. It stages only calls ``gate`` recorded, so a crash in ``gate`` fails closed
  (the server's own denial).
* ``finish`` (post_tool_call, also fires for blocked calls) moves outputs to the requested place
  and deletes the staging dir; ``rewrite_result`` (transform_tool_result) swaps staging paths in
  the result text back to the user's paths. Hooks only: no dependence on middleware ordering.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

SERVER = "chrome-devtools"
_PREFIXES = ("mcp__chrome_devtools__", "mcp_chrome_devtools_")
_LABEL = "Chrome DevTools"

# tool -> list of (arg key, mode). mode: read (list or str of files), write (file), write_dir (dir).
# Tools that write later (screencast_start, performance_start_trace) or keep per-path state
# (get_heapsnapshot_*) are deliberately left out: staging would break them.
TOOL_SPECS: dict[str, list[tuple[str, str]]] = {
    "upload_file": [("filePaths", "read")],
    "take_snapshot": [("filePath", "write")],
    "take_screenshot": [("filePath", "write")],
    "evaluate_script": [("filePath", "write")],
    "get_network_request": [("requestFilePath", "write"), ("responseFilePath", "write")],
    "performance_stop_trace": [("filePath", "write")],
    "take_heapsnapshot": [("filePath", "write")],
    "lighthouse_audit": [("outputDirPath", "write_dir")],
}

_STAGE_DIRNAME = "hermes-mcp-file-access"


# --- configuration ---------------------------------------------------------------------------

def _server_config() -> dict:
    try:
        from hermes_cli.config import load_config_readonly
        cfg = load_config_readonly() or {}
    except Exception:  # pragma: no cover - only outside Hermes
        return {}
    servers = cfg.get("mcp_servers") or {}
    entry = servers.get(SERVER) if isinstance(servers, dict) else None
    return entry if isinstance(entry, dict) else {}


def _server_tmpdir(cfg: dict) -> str:
    env = cfg.get("env") if isinstance(cfg.get("env"), dict) else {}
    return str(env.get("TMPDIR") or os.environ.get("TMPDIR") or tempfile.gettempdir())


def _configured_roots(cfg: dict) -> list[str]:
    args = [str(a) for a in (cfg.get("args") or [])]
    roots: list[str] = []
    for i, arg in enumerate(args):
        for flag in ("--filesystem-root", "--filesystemRoot", "--workspace"):
            if arg == flag and i + 1 < len(args):
                roots.append(args[i + 1])
            elif arg.startswith(flag + "="):
                roots.append(arg.split("=", 1)[1])
    return roots


def native_roots(cfg: Optional[dict] = None) -> list[str]:
    """Canonical directories the server already allows (its temp dir + --filesystem-root args)."""
    cfg = _server_config() if cfg is None else cfg
    out = []
    for root in [_server_tmpdir(cfg), *_configured_roots(cfg)]:
        try:
            out.append(os.path.realpath(os.path.expanduser(root)))
        except Exception:
            continue
    return out


def _inside(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


# --- call analysis ---------------------------------------------------------------------------

def tool_suffix(tool_name: str) -> Optional[str]:
    for prefix in _PREFIXES:
        if tool_name.startswith(prefix):
            return tool_name[len(prefix):]
    return None


def outside_targets(tool_name: str, args: dict, roots: list[str]) -> list[dict]:
    """Every path argument of this call that lies outside ``roots``.

    Each item: {key, index (for lists), mode, original, folder}. ``folder`` is the canonical
    directory whose access is being requested. Relative or nonexistent paths are skipped: the
    server resolves those itself and will report its own error.
    """
    suffix = tool_suffix(tool_name)
    spec = TOOL_SPECS.get(suffix or "")
    if not spec or not isinstance(args, dict):
        return []
    targets = []
    for key, mode in spec:
        value = args.get(key)
        if value is None:
            continue
        values = value if isinstance(value, list) else [value]
        for index, raw in enumerate(values):
            if not isinstance(raw, str) or not raw:
                continue
            expanded = os.path.expanduser(raw)
            if not os.path.isabs(expanded):
                continue
            if mode == "read":
                if not os.path.isfile(expanded):
                    continue
                canonical = os.path.realpath(expanded)
                folder = os.path.dirname(canonical)
            elif mode == "write":
                parent = os.path.dirname(expanded)
                if not os.path.isdir(parent):
                    continue
                folder = os.path.realpath(parent)
                canonical = os.path.join(folder, os.path.basename(expanded))
            else:  # write_dir
                if os.path.isdir(expanded):
                    canonical = os.path.realpath(expanded)
                elif os.path.isdir(os.path.dirname(expanded.rstrip(os.sep))):
                    canonical = os.path.join(os.path.realpath(os.path.dirname(expanded.rstrip(os.sep))),
                                             os.path.basename(expanded.rstrip(os.sep)))
                else:
                    continue
                folder = canonical
            if any(_inside(canonical, r) for r in roots):
                continue
            targets.append({"key": key, "index": index if isinstance(value, list) else None,
                            "mode": mode, "original": expanded, "folder": folder})
    return targets


def _direction(mode: str) -> str:
    return "read" if mode == "read" else "write"


def rule_key_for(direction: str, folder: str) -> str:
    return f"mcp-file-access:{SERVER}:{direction}:{folder}"


def _approved_ancestor(direction: str, folder: str, session_key: str,
                       is_approved: Callable[[str, str], bool]) -> Optional[str]:
    """Nearest folder (itself or an ancestor) with a Session/Always grant, if any."""
    current = folder
    while True:
        if is_approved(session_key, "plugin_rule:" + rule_key_for(direction, current)):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def _signature(tool_call_id: str, tool_name: str, targets: list[dict]) -> tuple:
    return (tool_call_id or "", tool_name,
            tuple(sorted((t["key"], t["index"] if t["index"] is not None else -1, t["original"]) for t in targets)))


def _pretty(folder: str) -> str:
    home = os.path.realpath(os.path.expanduser("~"))
    return "~" + folder[len(home):] if _inside(folder, home) else folder


def _variants(path: str) -> list[str]:
    real = os.path.realpath(path)
    out = {path, real}
    if real.startswith("/private/"):
        out.add(real[len("/private"):])
    return list(out)


# --- per-call state --------------------------------------------------------------------------
# gate() records which calls it asked about; prepare() only stages a call gate() recorded, so a
# crash in gate() can never yield an ungated staging. finish()/rewrite_result() settle the call.

_STATE_TTL = 3600.0
_lock = threading.Lock()
_asked: dict[tuple, tuple[float, list[dict]]] = {}
_staged: dict[tuple, dict] = {}  # (tool_call_id, tool_name) -> {base, moves, mapping, ts}


def _sweep(now: float) -> None:
    for key, (ts, _t) in list(_asked.items()):
        if now - ts > _STATE_TTL:
            _asked.pop(key, None)
    for key, entry in list(_staged.items()):
        if now - entry["ts"] > _STATE_TTL:
            shutil.rmtree(entry["base"], ignore_errors=True)
            _staged.pop(key, None)


# --- hooks -----------------------------------------------------------------------------------

def gate(tool_name: str = "", args: Optional[dict] = None, tool_call_id: str = "",
         _roots: Optional[list[str]] = None, _session_key: Optional[str] = None,
         _is_approved: Optional[Callable[[str, str], bool]] = None, **_: Any) -> Optional[dict]:
    """pre_tool_call #1: ask before the server touches a folder outside its roots."""
    if not tool_call_id or tool_suffix(tool_name or "") not in TOOL_SPECS:
        return None
    roots = native_roots() if _roots is None else _roots
    targets = outside_targets(tool_name, args or {}, roots)
    if not targets:
        return None
    if _is_approved is None:
        from tools.approval import is_approved as _is_approved  # noqa: N806
    if _session_key is None:
        from tools.approval_context import get_current_session_key
        _session_key = get_current_session_key()

    needed: dict[tuple[str, str], None] = {}
    for t in targets:
        direction = _direction(t["mode"])
        granted = _approved_ancestor(direction, t["folder"], _session_key, _is_approved)
        needed[(direction, granted or t["folder"])] = None
    now = time.monotonic()
    with _lock:
        _sweep(now)
        _asked[_signature(tool_call_id, tool_name, targets)] = (now, targets)
    logger.info("mcp-file-access: %s call %s needs %s", tool_name, tool_call_id,
                ", ".join(f"{d}:{f}" for d, f in needed))

    grants = list(needed)
    if len(grants) == 1:
        direction, folder = grants[0]
        rule_key = rule_key_for(direction, folder)
        verb = "read files from" if direction == "read" else "save files to"
        message = f"{_LABEL} wants to {verb} {_pretty(folder)}"
    else:
        rule_key = "mcp-file-access:" + SERVER + ":" + "|".join(f"{d}:{f}" for d, f in sorted(grants))
        message = f"{_LABEL} wants to access: " + ", ".join(
            f"{'read' if d == 'read' else 'write'} {_pretty(f)}" for d, f in sorted(grants))
    return {"action": "approve", "message": message, "rule_key": rule_key}


def prepare(tool_name: str = "", args: Optional[dict] = None, tool_call_id: str = "",
            _roots: Optional[list[str]] = None, _tmpdir: Optional[str] = None, **_: Any) -> Optional[dict]:
    """pre_tool_call #2: point the call at staged copies in the server's temp dir.

    Hermes applies this ``modify`` only if the approval from gate() passes; if it is denied the
    call never runs and finish() removes the staging dir.
    """
    if not tool_call_id or tool_suffix(tool_name or "") not in TOOL_SPECS:
        return None
    args = args if isinstance(args, dict) else {}
    cfg = _server_config() if (_roots is None or _tmpdir is None) else {}
    roots = native_roots(cfg) if _roots is None else _roots
    targets = outside_targets(tool_name, args, roots)
    if not targets:
        return None
    with _lock:
        asked = _asked.pop(_signature(tool_call_id, tool_name, targets), None)
    if asked is None:
        logger.info("mcp-file-access: %s call %s not staged (gate did not run)", tool_name, tool_call_id)
        return None

    base = Path(_tmpdir or _server_tmpdir(cfg)) / _STAGE_DIRNAME / uuid.uuid4().hex
    changes: dict[str, Any] = {}
    moves: list[tuple[str, str, str]] = []
    mapping: dict[str, str] = {}
    try:
        for n, t in enumerate(targets):
            slot = base / f"{n}-{t['key']}"
            slot.mkdir(parents=True, exist_ok=True)
            if t["mode"] == "read":
                staged = slot / os.path.basename(t["original"])
                shutil.copy2(t["original"], staged)
                replacement = str(staged)
                for v in _variants(str(staged)):
                    mapping[v] = t["original"]
            elif t["mode"] == "write":
                replacement = str(slot / os.path.basename(t["original"]))
                moves.append((str(slot), os.path.dirname(t["original"]), "write"))
                for v in _variants(str(slot)):
                    mapping[v] = os.path.dirname(t["original"])
            else:
                replacement = str(slot)
                moves.append((str(slot), t["original"].rstrip(os.sep), "write_dir"))
                for v in _variants(str(slot)):
                    mapping[v] = t["original"].rstrip(os.sep)
            if t["index"] is None:
                changes[t["key"]] = replacement
            else:
                values = list(changes.get(t["key"], args[t["key"]]))
                values[t["index"]] = replacement
                changes[t["key"]] = values
    except Exception:
        shutil.rmtree(base, ignore_errors=True)
        raise
    with _lock:
        _staged[(tool_call_id, tool_name)] = {"base": str(base), "moves": moves, "mapping": mapping,
                                              "ts": time.monotonic()}
    logger.info("mcp-file-access: staged %d path(s) for %s call %s", len(targets), tool_name, tool_call_id)
    return {"action": "modify", "args": changes}


def finish(tool_name: str = "", tool_call_id: str = "", **_: Any) -> None:
    """post_tool_call (fires for blocked calls too): deliver outputs, drop the staging dir."""
    with _lock:
        entry = _staged.get((tool_call_id, tool_name))
        if entry is None or entry.get("done"):
            return None
        entry["done"] = True
    try:
        for slot, dest, mode in entry["moves"]:
            slot_path = Path(slot)
            if not slot_path.is_dir():
                continue
            if mode == "write":
                for item in slot_path.iterdir():
                    target = Path(dest) / item.name
                    if target.is_dir():
                        shutil.rmtree(target)
                    shutil.move(str(item), str(target))
            elif any(slot_path.iterdir()):
                Path(dest).mkdir(parents=True, exist_ok=True)
                shutil.copytree(slot_path, dest, dirs_exist_ok=True)
    finally:
        shutil.rmtree(entry["base"], ignore_errors=True)
        with _lock:
            if entry.get("rewritten"):
                _staged.pop((tool_call_id, tool_name), None)
    return None


def rewrite_result(tool_name: str = "", tool_call_id: str = "", result: Any = None, **_: Any) -> Optional[str]:
    """transform_tool_result: show the user's real paths, not the staging ones."""
    with _lock:
        entry = _staged.get((tool_call_id, tool_name))
        if entry is not None and entry.get("done"):
            _staged.pop((tool_call_id, tool_name), None)
        elif entry is not None:
            entry["rewritten"] = True
    if entry is None or not isinstance(result, str):
        return None
    out = result
    for staged, real in sorted(entry["mapping"].items(), key=lambda kv: -len(kv[0])):
        out = out.replace(staged, real)
    return out if out != result else None
