"""Detached explicit Hub update; backend-only changes never quit Desktop."""
from __future__ import annotations

import fcntl
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time


def load_core():
    spec = importlib.util.spec_from_file_location("hub_update_core", Path(__file__).with_name("core.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def needs_quit(core, status):
    return (status.get("state") == "ready" and status.get("can_restart")
            and status.get("sha") == core._git(core.running_checkout(), "rev-parse", "origin/main")
            and core._desktop_swap_pending(core.running_checkout(), status.get("sha"), core._git) is True)


def monitor(core, run=subprocess.run, wait=None, clock=time.monotonic):
    home = core.hermes_home()
    root = home.parent.parent if home.parent.name == "profiles" else home
    directory = core.state_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    request_path = root / "core-update/force-request"
    initial = core._read_json(request_path)
    request_at = initial.get("requested_at")
    path = directory / "hub-update.json"

    def record(phase, **extra):
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"phase": phase, "requested_at": request_at, **extra}) + "\n")
        os.replace(tmp, path)

    wait = wait or threading.Event().wait
    try:
        record("updating")
        # The attested updater fetches latest main even without a pending build.
        with (directory / "hub-update.log").open("a") as log:
            run([sys.executable, "-I", str(Path(__file__).with_name("waiter.py")), "--update"],
                check=True, stdout=log, stderr=subprocess.STDOUT)
        deadline = clock() + 1800
        while clock() < deadline:
            status = core.status()
            if needs_quit(core, status):
                record("restarting")
                result = core.restart()
                if not result.get("ok"):
                    record("deferred", reason=result.get("reason") or "Desktop swap refused")
                return
            request = core._read_json(request_path)
            if not request and status.get("state") != "building":
                record("updated", sha=status.get("running"))
                return
            if request and float(request.get("expires_at", 0)) <= time.time():
                record("deferred", reason="Force request expired before the update completed")
                return
            wait(5)
        record("deferred", reason="Timed out waiting for the update or Desktop build")
    except Exception as exc:
        record("failed", reason=str(exc))


def main():
    core = load_core()
    directory = core.state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "hub-update.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return  # another window's monitor owns this same installation
        monitor(core)


if __name__ == "__main__":
    main()
