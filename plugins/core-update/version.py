"""Local Hub-panel version view, human impact summary, and blocker controls.

The detached runner uses the existing attested updater, never a command or path
supplied by the browser or state files.
"""
from __future__ import annotations

import fcntl
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import time


def _load_sibling(mod_name: str, filename: str):
    spec = importlib.util.spec_from_file_location(mod_name, Path(__file__).with_name(filename))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _core():
    return _load_sibling("hub_version_core", "core.py")


def _summary():
    return _load_sibling("hub_version_summary", "summary.py")


def _blockers():
    return _load_sibling("hub_version_blockers", "blockers.py")


def installation_root(home):
    return home.parent.parent if home.parent.name == "profiles" else home


def monitor_alive(directory):
    # Read-only lock probe: PID reuse must never keep a dead operation active.
    try:
        with (directory / "hub-update.lock").open("r") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
    except FileNotFoundError:
        pass
    return False


def reconcile_operation(core, operation, result, directory):
    if operation.get("expires_at") and operation["expires_at"] <= time.time():
        return {}
    if operation.get("phase") in ("updating", "restarting"):
        target = operation.get("target")
        applied = result.get("applied")
        if (
            target
            and applied == target
            and result.get("desktop_swap_pending") is False
            and not result.get("force_request")
            and not result.get("progress")
        ):
            return {**operation, "phase": "updated", "sha": applied}
        if not monitor_alive(directory):
            # The swap waiter outlives the monitor and has its own live receipt.
            waiter = core._read_json(directory / "waiter.json")
            if waiter.get("state") not in (None, "done", "exited") and core._pid_alive(waiter.get("pid")):
                return operation
            return {
                **operation,
                "phase": "deferred",
                "reason": "Update monitor stopped; check the installed revision or retry Update now",
            }
    # Terminal results are a short-lived receipt, not a permanent status banner.
    if operation.get("finished_at") and time.time() - operation["finished_at"] > 300:
        return {}
    return operation


def _version_status_copy(
    *,
    commits: int,
    running: str | None,
    applied: str | None,
    target: str | None,
    desktop_swap_pending: bool | None,
) -> str:
    if commits > 0:
        noun = "1 change ready" if commits == 1 else f"{commits} changes ready"
        if desktop_swap_pending is True:
            return f"{noun} · restarts Desktop when ready"
        return f"{noun} · no app restart needed"
    if desktop_swap_pending is True:
        return "App update ready · restarts Desktop to finish"
    if running and applied and running != applied and (not target or applied == target):
        return "Update installed · backend refreshes between turns"
    if running and target and running == target:
        return "Up to date"
    return "Status available"


def view(home=None, checkout=None):
    core = _core()
    summary_mod = _summary()
    blockers_mod = _blockers()
    home = home or core.hermes_home()
    inst_root = installation_root(home)
    root = checkout or core.running_checkout()
    result = core.status(inst_root, root)
    result["applied"] = core._git(root, "rev-parse", "HEAD")
    running = result.get("running")
    from hermes_cli.git_fingerprint import process_boot_fingerprint

    boot = (process_boot_fingerprint(root) or "").rsplit(":", 1)[-1]
    if len(boot) == 40 and all(c in "0123456789abcdef" for c in boot):
        running = result["running"] = boot
    target = result.get("sha")
    # Stale prebuild records must not select an older target. The fetched ref is
    # authoritative when it is a descendant; reads never fetch or mutate Git.
    latest = core._git(root, "rev-parse", "origin/main")
    if latest and running and core._git(root, "merge-base", "--is-ancestor", running, latest) is not None:
        target = latest
    result["target"] = target
    result["desktop_swap_pending"] = core._desktop_swap_pending(root, target, core._git) if target else None
    stamps = sorted((root / "apps/desktop/release").glob("*/Hermes.app/Contents/Resources/install-stamp.json"))
    result["desktop_build"] = [core._read_json(p).get("commit") for p in stamps]

    changes = []
    rich_commits = []
    if running and target and running != target and core._git(root, "merge-base", "--is-ancestor", running, target) is not None:
        log = core._git(root, "log", "--date-order", "--format=%H%x1f%s%x1f%cI%x1f%b%x1e", f"{running}..{target}")
        if log is None:
            raise RuntimeError("Could not read update commits")
        for rec in log.split("\x1e"):
            rec = rec.strip("\r\n")
            if not rec:
                continue
            parts = rec.split("\x1f", 3)
            if len(parts) < 3:
                continue
            sha, subject, at = parts[0], parts[1], parts[2]
            body = parts[3] if len(parts) > 3 else ""
            changes.append({"sha": sha, "subject": subject, "at": at})
            rich_commits.append({"sha": sha, "subject": subject, "at": at, "body": body})

    result["changes"] = changes
    result["commits"] = len(changes)
    result["version_status"] = _version_status_copy(
        commits=len(changes),
        running=running,
        applied=result.get("applied"),
        target=target,
        desktop_swap_pending=result.get("desktop_swap_pending"),
    )

    from hermes_cli.core_update_force import read_force_request, running_turn_count
    from hermes_cli.core_update_progress import read_progress

    result["running_turns"] = running_turn_count(inst_root)
    result["force_request"] = read_force_request(inst_root)
    result["progress"] = read_progress()
    directory = core.state_dir(inst_root)
    result["operation"] = reconcile_operation(core, core._read_json(directory / "hub-update.json"), result, directory)
    waiter = core._read_json(directory / "waiter.json")

    reasons = (result["progress"] or {}).get("waiting_on") or []
    if reasons:
        result["deferral_reason"] = "; ".join(reasons)
    if not result["progress"] and waiter.get("state") == "deferred" and core._pid_alive(waiter.get("pid")):
        result["deferral_reason"] = waiter.get("reason")

    blockers = blockers_mod.collect_blockers(
        inst_root,
        progress=result["progress"],
        force_request=result["force_request"],
        status=result,
        waiter=waiter,
    )
    result["blockers"] = blockers
    result["human_deferral"] = blockers_mod.summarize_blockers_text(blockers)

    active_update = bool(
        result["force_request"]
        or (result["progress"] and result["progress"].get("state") in ("draining", "applying", "blocked"))
        or result["operation"].get("phase") in ("updating", "restarting")
    )
    result["summary"] = summary_mod.build_summary(
        inst_root,
        directory,
        running=running,
        target=target,
        commits=rich_commits,
        desktop_swap_pending=result.get("desktop_swap_pending"),
        active_update=active_update,
    )
    return result


def update_now(home=None, *, popen=subprocess.Popen):
    core = _core()
    home = home or core.hermes_home()
    # Refuse before writing intent if the installed updater cannot be executed.
    core.waiter.attested_source()
    from hermes_cli.core_update_force import write_force_request

    body = write_force_request(installation_root(home), by="hub panel")
    popen(
        [sys.executable, "-I", str(Path(__file__).with_name("update.py"))],
        env={**os.environ, "HERMES_HOME": str(installation_root(home))},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )
    return {"ok": True, "requested_at": body["requested_at"], "expires_at": body["expires_at"]}


def resolve_blocker(payload, home=None):
    core = _core()
    home = home or core.hermes_home()
    return _blockers().act_on_blocker(installation_root(home), payload)
