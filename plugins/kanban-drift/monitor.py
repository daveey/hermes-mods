"""One scheduled fleet drift consumer, with durable incidents and native Kanban tools.

Configuration is host-local JSON: owner_gateway, state_path, targets (key -> argv),
assignee and optional board. Each command must return the sync diff JSON (exit 0/1).
No credentials, hostnames or schedules are shipped in this module.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import subprocess
import re
import time
import uuid

# Core's placement scan (hermes_cli.kanban_placement.portable_reason) skips text inside complete pairs of these
# markers. A drift report is full of host words (state paths, kanban.db, gateway names); unfenced, they make the
# repair card look home-bound, so on the hub "auto" costs an admission-judge call and a hub:write requirement.
EVIDENCE_OPEN = "<!-- placement:evidence -->"
EVIDENCE_CLOSE = "<!-- /placement:evidence -->"
_MARKER = re.compile(r"<!--\s*/?\s*placement:evidence\s*-->", re.IGNORECASE)


def fence_evidence(text):
    """``text`` inside one evidence fence, with every marker in it removed (also ones re-formed from nested halves),
    so pasted evidence can neither end the fence early nor open a fake one that hides instructions."""
    text = text or ""
    while True:
        cleaned = _MARKER.sub("", text)
        if cleaned == text:
            break
        text = cleaned
    return f"{EVIDENCE_OPEN}\n{text.strip(chr(10))}\n{EVIDENCE_CLOSE}"


class Monitor:
    def __init__(self, path, tool, *, assignee="default", board="default"):
        self.path = Path(path)
        self.tool = tool
        self.assignee, self.board = assignee, board
        self.state = json.loads(self.path.read_text()) if self.path.exists() else {"version": 1, "targets": {}}

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        with tmp.open("w") as stream:
            json.dump(self.state, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        tmp.replace(self.path)

    def sample(self, key, report):
        row = self.state["targets"].setdefault(key, {"streak": 0, "incident": None, "card": None})
        row.update(last_sample=report, sampled_at=time.time())
        if row["card"]:
            card = self.tool("kanban_show", {"task_id": row["card"], "board": self.board})["task"]
            if card["status"] in ("done", "archived"):
                row.update(streak=0, incident=None, card=None)
        if report.get("unavailable") or type(report.get("clean")) is not bool:
            row["streak"] = 0  # unknown breaks consecutive dirty, never clears the incident
            if row.get("unavailable_since") is None:
                row["unavailable_since"] = row["sampled_at"]
            self.save()
            return row
        row["unavailable_since"] = None  # any real sample (clean or dirty) ends the dark period
        if report["clean"]:
            row["streak"] = 0
            self.save()
            return row
        row["streak"] += 1
        if row["streak"] < 2:
            self.save()
            return row
        if not row["incident"]:
            row["incident"] = str(uuid.uuid4())
        self.save()  # write-ahead identity: a crash after create reuses the same key
        body = (f"Fleet sync drift on {key}, confirmed on consecutive samples. "
                "Investigate and repair through normal sync semantics; preserve rejection audits and worker fences. "
                "No human action is requested. Verify fresh authoritative state and attach per-row evidence.\n\n"
                + fence_evidence(json.dumps(report, sort_keys=True)))
        if not row["card"]:
            # placement "auto", never "local": on the hub "local" is a hub slot that core's admission judge must
            # admit, and it denies routine repair work (pool workers have hub ssh + hub-admin), so the create was
            # refused and no repair card was ever filed (cron-0d63c0cf26). "auto" joins the worker pool.
            result = self.tool("kanban_create", {
                "title": f"Repair persistent Kanban sync drift: {key}", "body": body,
                "assignee": self.assignee, "requires": ["terminal", "file", "kanban"],
                "placement": "auto", "board": self.board,
                "idempotency_key": "kanban-drift:" + key + ":" + row["incident"],
                "completion_contract": "local-only"})
            row["card"] = result.get("task_id") or result.get("id") or result.get("task", {}).get("id")
            if not row["card"]:
                raise RuntimeError("Kanban create returned no card: " + json.dumps(result))
            self.save()
            card = self.tool("kanban_show", {"task_id": row["card"], "board": self.board})["task"]
            if card["id"] != row["card"]:
                raise RuntimeError("Kanban readback identity mismatch")
        elif row.get("last_report") != report:
            self.tool("kanban_comment", {"task_id": row["card"], "board": self.board, "body": body})
        row["last_report"] = report
        self.save()
        return row


def collect(argv, timeout=90):
    result = None
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        report = json.loads(result.stdout)
        if result.returncode not in (0, 1) or not isinstance(report, dict) or type(report.get("clean")) is not bool:
            raise ValueError(f"invalid diff response (exit {result.returncode})")
        if result.returncode == 1 and report["clean"]:
            raise ValueError("failed command claimed clean")
        return report
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        report = {"clean": False, "unavailable": True, "error": str(exc)}
        if result is not None:
            report.update(exit_code=result.returncode, stderr=result.stderr[-2000:])
        return report


def native_tool(name, args):
    from model_tools import handle_function_call
    result = json.loads(handle_function_call(name, args))
    if result.get("error") or result.get("success") is False:
        raise RuntimeError(json.dumps(result))
    return result


DEFAULT_UNAVAILABLE_ALERT_AFTER = 21600  # seconds; a sleeping laptop is normal, days of silence are not


def alert_after(config, key):
    """Seconds a target may stay unavailable before the run fails: an int, or a map with optional "*"."""
    value = config.get("unavailable_alert_after", DEFAULT_UNAVAILABLE_ALERT_AFTER)
    if isinstance(value, dict):
        value = value.get(key, value.get("*", DEFAULT_UNAVAILABLE_ALERT_AFTER))
    return float(value)


DEFAULT_ABSENT_ALERT_AFTER = 7 * 86400  # seconds a host may be powered off/offline before the run fails


def absent_after(config, key):
    value = config.get("absent_alert_after", DEFAULT_ABSENT_ALERT_AFTER)
    if isinstance(value, dict):
        value = value.get(key, value.get("*", DEFAULT_ABSENT_ALERT_AFTER))
    return float(value)


def host_present(argv, timeout=15):
    """Presence probe: exit 0 = host up, other exit = host down. A probe that cannot run or
    times out counts as up, so a broken probe keeps sampling failures loud instead of hiding them."""
    try:
        return subprocess.run(argv, capture_output=True, timeout=timeout).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return True


def classify(config, receipts, now):
    """Return ({stale key: reason}, [absent keys]) for targets in a dark period.

    Without a `presence` command a target is stale once dark longer than unavailable_alert_after
    (reason unavailable_too_long). With one, a host that is down is `absent` (expected: a closed
    laptop) and fails only after absent_alert_after (absent_too_long); a host that is up while its
    sample fails is the real blind spot and fails after unavailable_alert_after (sample_failing_while_up)."""
    presence = config.get("presence") or {}
    stale, absent = {}, []
    for key, row in receipts.items():
        since = row.get("unavailable_since")
        if since is None:
            continue
        dark = now - since
        if key not in presence:
            if dark > alert_after(config, key):
                stale[key] = "unavailable_too_long"
            continue
        up = host_present(presence[key], config.get("presence_timeout", 15))
        row["host_present"] = up
        if not up:
            absent.append(key)
            if dark > absent_after(config, key):
                stale[key] = "absent_too_long"
        elif dark > alert_after(config, key):
            stale[key] = "sample_failing_while_up"
    return stale, sorted(absent)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    from hermes_cli.kanban_sync_client import load_identity
    if load_identity().gateway != config["owner_gateway"]:
        raise RuntimeError("Consumer must run on its configured owner gateway")
    state = Path(config["state_path"]).expanduser()
    state.parent.mkdir(parents=True, exist_ok=True)
    with state.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        monitor = Monitor(state, native_tool, assignee=config.get("assignee", "default"), board=config.get("board", "default"))
        receipts = {}
        for key, argv in config["targets"].items():
            report = collect(argv, config.get("timeout", 90))
            try:
                receipts[key] = dict(monitor.sample(key, report))
            except Exception as exc:
                receipts[key] = {"last_sample": report, "consumer_error": str(exc)}
        now = time.time()
        stale, absent = classify(config, receipts, now)
        print(json.dumps({"sampled_at": now, "stale_targets": sorted(stale), "stale_reasons": stale,
                          "absent_targets": absent, "targets": receipts}, sort_keys=True))
        # Exit 1 = a consumer error, or a stale target (see classify). A briefly unavailable
        # target, or an absent host within absent_alert_after, only resets its streak: exit 0.
        return int(bool(stale) or any(r.get("consumer_error") for r in receipts.values()))


if __name__ == "__main__":
    raise SystemExit(main())
