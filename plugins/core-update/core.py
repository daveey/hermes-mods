"""Status of a core update prepared off to the side, for the core-update Desktop chip.

An updater (for example a sync job that keeps a fork checkout current) writes
``$HERMES_HOME/cache/core-update/state.json``:

    {"status": "building" | "ready" | "failed", "sha": "<commit being packed>",
     "serves_sha": "<newer head the ready build also serves (no desktop input changed)>",
     "app_path": "<prebuilt Hermes.app>", "built_at": "<iso>", "started_at": "<iso>"}

and, while it waits for Desktop to quit, ``waiter.json`` ({"pid", "state", ...}) next to it.
The legacy ``waiter_cmd`` field is ignored. Restart uses this plugin's waiter.py,
which runs only the reference updater at its fixed installed path after owner/mode checks.
This module only reads those files, the running checkout's HEAD and installed Desktop bundle
stamps; ``restart`` starts the
waiter (it is idempotent: a second waiter exits at once) and then asks Desktop to quit, so the
update lands between quit and relaunch. Nothing here edits the checkout.
"""
from __future__ import annotations

import json
import os
import subprocess
import stat
import importlib.util
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


_waiter_spec = importlib.util.spec_from_file_location("core_update_waiter", Path(__file__).with_name("waiter.py"))
waiter = importlib.util.module_from_spec(_waiter_spec)
_waiter_spec.loader.exec_module(waiter)


def _read_state(path: Path) -> Dict[str, Any]:
    """Check the opened inode and parse it once; never stat then reopen."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "r") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
                return {}
            data = json.loads(stream.read())
            return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _waiter_available() -> bool:
    try:
        waiter.attested_source()
        return True
    except (OSError, ValueError):
        return False


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


# Match the updater's desktop build-input comparison, not backend checkout HEAD.
DESKTOP_INPUT_PATHS = ("apps/desktop", "apps/shared", "package.json", "package-lock.json", ".npmrc",
                       "pnpm-lock.yaml", "pnpm-workspace.yaml", "pm/lock.json", "scripts/build",
                       "scripts/generate-icons.mjs", "scripts/generate_icons.py", "assets",
                       "pyproject.toml", "uv.lock")


def _desktop_swap_pending(root: Path, sha: str, git: Callable[..., Optional[str]]) -> Optional[bool]:
    """Derive swap debt from installed bundle stamps; never the staged app's stamp.

    None means no installed bundle identity is available (legacy/non-Desktop host).
    Re-read on each poll so a completed swap clears the offer without a state writer.
    Older bundles that have identical build inputs already serve this target.
    """
    stamps = sorted((root / "apps/desktop/release").glob("*/Hermes.app/Contents/Resources/install-stamp.json"))
    commits = [_read_json(p).get("commit") for p in stamps]
    if not commits or any(not isinstance(c, str) or len(c) != 40
                          or any(ch not in "0123456789abcdef" for ch in c) for c in commits):
        return None
    return any(c != sha and git(root, "diff", "--quiet", c, sha, "--", *DESKTOP_INPUT_PATHS) is None
               for c in commits)


def status(home: Optional[Path] = None, checkout: Optional[Path] = None,
           git: Callable[..., Optional[str]] = _git) -> Dict[str, Any]:
    """Offer descendants of checkout HEAD, including HEAD when its Desktop swap is owed.

    Checkout equality is not proof of Desktop installation. A matching installed bundle
    (including identical build inputs) clears same-HEAD offers after the swap.
    """
    return _status(_read_state(state_dir(home) / "state.json"), home, checkout, git)


def _status(st: Dict[str, Any], home: Optional[Path], checkout: Optional[Path],
            git: Callable[..., Optional[str]]) -> Dict[str, Any]:
    d = state_dir(home)
    waiter_state = _read_json(d / "waiter.json")
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
        "waiter": {"pid": waiter_state.get("pid"), "state": waiter_state.get("state"),
                   "alive": bool(waiter_state.get("pid")) and _pid_alive(waiter_state.get("pid"))} if waiter_state else None,
        "can_restart": False,
        "reason": st.get("local_pack_blocked_reason"),
    }
    if not sha or not running:
        return out
    if sha == running:
        pending = _desktop_swap_pending(root, sha, git)
        out["desktop_swap_pending"] = pending
        if pending is False:
            return out
        # Without installed identity, keep build/failure diagnostics but do not
        # turn a legacy same-HEAD ready record into an endless restart offer.
        if pending is None and st.get("status") == "ready":
            out["reason"] = "installed Desktop bundle identity unavailable"
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
        out["can_restart"] = _waiter_available()
        if not out["can_restart"]:
            out["reason"] = "installed updater is unavailable or not attested"
    elif s == "failed":
        out["state"] = "failed"
    return out


def _age_s(iso: Any) -> float:
    try:
        from datetime import datetime

        return time.time() - datetime.fromisoformat(str(iso)).timestamp()
    except Exception:
        return float("inf")


def _write_restart_force_request(home: Path) -> None:
    """Record explicit apply-now intent at the installation root, not a profile/cache."""
    root = home.parent.parent if home.parent.name == "profiles" else home
    try:
        from hermes_cli.core_update_force import write_force_request
    except ImportError:
        # Older cores do not expose the helper yet; preserve its atomic v1 contract.
        import tempfile

        path = root / "core-update" / "force-request"
        path.parent.mkdir(parents=True, exist_ok=True)
        now = time.time()
        body = {"version": 1, "requested_at": now, "expires_at": now + 1800,
                "by": "core-update chip"}
        tmp = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                             prefix=".force-request.", suffix=".tmp", delete=False) as stream:
                tmp = Path(stream.name)
                stream.write(json.dumps(body) + "\n")
            os.replace(tmp, path)
        finally:
            if tmp is not None:
                tmp.unlink(missing_ok=True)
    else:
        write_force_request(root, by="core-update chip")


def restart(home: Optional[Path] = None, *, popen: Callable[..., Any] = subprocess.Popen,
            quit_delay_s: float = 2.0, checkout: Optional[Path] = None,
            git: Callable[..., Optional[str]] = _git) -> Dict[str, Any]:
    """Start the apply-on-quit waiter, then quit Desktop. Both detached: quitting Desktop ends
    this backend too. Refuses unless an update is ready (otherwise nothing would reopen Desktop)."""
    st = _read_state(state_dir(home) / "state.json")
    s = _status(st, home, checkout, git)
    if s["state"] != "ready" or not s["can_restart"]:
        return {"ok": False, "reason": f"no update ready (state {s['state']})"}
    try:
        _write_restart_force_request(home if home is not None else hermes_home())
    except Exception as exc:
        return {"ok": False, "reason": f"could not write force request: {exc}"}
    cmd = ["/usr/bin/python3", "-I", str(Path(__file__).resolve().with_name("waiter.py"))]
    detached = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    start_new_session=True, close_fds=True)
    popen(cmd, **detached)
    quit_script = f'tell application id "{DESKTOP_BUNDLE_ID}" to quit'
    popen(["/bin/sh", "-c", f"sleep {quit_delay_s}; exec /usr/bin/osascript -e '{quit_script}'"], **detached)
    return {"ok": True, "sha": s["sha"]}
