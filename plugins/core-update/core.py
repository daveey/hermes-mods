"""Status of a core update prepared off to the side, for the core-update Desktop chip.

An updater (for example a sync job that keeps a fork checkout current) writes
``$HERMES_HOME/cache/core-update/state.json``:

    {"status": "building" | "ready" | "failed", "sha": "<commit being packed>",
     "serves_sha": "<newer head the ready build also serves (no desktop input changed)>",
     "app_path": "<prebuilt Hermes.app>", "built_at": "<iso>", "started_at": "<iso>",
     "waiter_cmd": ["<argv that waits for Desktop to quit, then applies the update>"]}

and, while it waits for Desktop to quit, ``waiter.json`` ({"pid", "state", ...}) next to it.
This module only reads those files and the running checkout's HEAD; ``restart`` starts the
waiter (it is idempotent: a second waiter exits at once) and then asks Desktop to quit, so the
update lands between quit and relaunch. Nothing here edits the checkout.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

DESKTOP_BUNDLE_ID = "com.nousresearch.hermes"
BUILD_STALE_S = 2 * 3600  # a "building" record older than this with a dead pid is a crash leftover


def hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home

        return Path(get_hermes_home())
    except Exception:
        return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")


def state_dir(home: Optional[Path] = None) -> Path:
    return (home or hermes_home()) / "cache" / "core-update"


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _git(root: Path, *args: str) -> Optional[str]:
    try:
        r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def running_checkout() -> Path:
    """The checkout this backend runs from (hermes_cli/..)."""
    try:
        import hermes_cli

        return Path(hermes_cli.__file__).resolve().parents[1]
    except Exception:
        return hermes_home() / "hermes-agent"


def _pid_alive(pid: Any) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except PermissionError:
        return True
    except (OSError, ValueError, TypeError):
        return False


def status(home: Optional[Path] = None, checkout: Optional[Path] = None,
           git: Callable[..., Optional[str]] = _git) -> Dict[str, Any]:
    """``{"state": "building"|"ready"|"failed"|"idle", ...}``. ``ready`` only when the built sha
    is a descendant of (ahead of) the running HEAD; a build of what already runs is ``idle``."""
    d = state_dir(home)
    st = _read_json(d / "state.json")
    waiter = _read_json(d / "waiter.json")
    root = checkout or running_checkout()
    running = git(root, "rev-parse", "HEAD")
    sha = st.get("sha") if isinstance(st.get("sha"), str) else None
    served = st.get("serves_sha")
    if st.get("status") == "ready" and isinstance(served, str) and served:
        # The updater kept an older build because the newer head changed no desktop input; that
        # build is what the update to ``serves_sha`` installs, so the update offered is that head.
        sha = served
    out: Dict[str, Any] = {
        "state": "idle", "sha": sha, "running": running, "built_at": st.get("built_at"),
        "started_at": st.get("started_at"), "error": st.get("error"),
        "waiter": {"pid": waiter.get("pid"), "state": waiter.get("state"),
                   "alive": bool(waiter.get("pid")) and _pid_alive(waiter.get("pid"))} if waiter else None,
        "can_restart": False,
    }
    if not sha or not running or sha == running:
        return out
    ahead = git(root, "merge-base", "--is-ancestor", running, sha) is not None
    if not ahead:
        return out  # stale record (the checkout moved past it, or diverged)
    out["commits"] = int(git(root, "rev-list", "--count", f"{running}..{sha}") or 0) or None
    s = st.get("status")
    if s == "building":
        if _pid_alive(st.get("pid")) or _age_s(st.get("updated_at")) < BUILD_STALE_S:
            out["state"] = "building"
    elif s == "ready" and st.get("app_path") and Path(str(st["app_path"])).is_dir():
        out["state"] = "ready"
        out["can_restart"] = bool(st.get("waiter_cmd"))
    elif s == "failed":
        out["state"] = "failed"
    return out


def _age_s(iso: Any) -> float:
    try:
        from datetime import datetime

        return time.time() - datetime.fromisoformat(str(iso)).timestamp()
    except Exception:
        return float("inf")


def restart(home: Optional[Path] = None, *, popen: Callable[..., Any] = subprocess.Popen,
            quit_delay_s: float = 2.0, checkout: Optional[Path] = None,
            git: Callable[..., Optional[str]] = _git) -> Dict[str, Any]:
    """Start the apply-on-quit waiter, then quit Desktop. Both detached: quitting Desktop ends
    this backend too. Refuses unless an update is ready (otherwise nothing would reopen Desktop)."""
    s = status(home, checkout=checkout, git=git)
    if s["state"] != "ready" or not s["can_restart"]:
        return {"ok": False, "reason": f"no update ready (state {s['state']})"}
    cmd: List[str] = [str(c) for c in _read_json(state_dir(home) / "state.json")["waiter_cmd"]]
    detached = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    start_new_session=True, close_fds=True)
    popen(cmd, **detached)
    quit_script = f'tell application id "{DESKTOP_BUNDLE_ID}" to quit'
    popen(["/bin/sh", "-c", f"sleep {quit_delay_s}; exec /usr/bin/osascript -e '{quit_script}'"], **detached)
    return {"ok": True, "sha": s["sha"]}
