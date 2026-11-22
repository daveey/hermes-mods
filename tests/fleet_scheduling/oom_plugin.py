"""Fixture-only OOM instrumentation, installed into the disposable runtime home.

Use the real plugin hook in the native executor: its environment deliberately
scrubs PYTHONPATH and FLEET_* before exec. Only this process migrates its own PID.
"""
import json
import os
from pathlib import Path
import threading
import time

_started = False


def register(ctx):
    ctx.register_hook("pre_llm_call", start)


def start(**kwargs):
    global _started
    if _started:
        return
    _started = True
    # Resolve before starting a thread; no profile/environment lookup there.
    fault = json.loads(Path(__file__).with_name("fault.json").read_text())
    trigger, group, joined = (Path(fault[k]) for k in ("trigger", "group", "joined"))

    def join():
        deadline = time.monotonic() + 120
        while not trigger.exists():
            if time.monotonic() > deadline:
                return
            time.sleep(0.02)
        me = os.getpid()
        (group / "cgroup.procs").write_text(str(me))
        stat = Path(f"/proc/{me}/stat").read_text().rsplit(")", 1)[1].split()
        record = {"pid": me, "start_identity": stat[19],
                  "cgroup": Path("/proc/self/cgroup").read_text().strip()}
        pending = joined.with_suffix(".tmp")
        pending.write_text(json.dumps(record))
        pending.replace(joined)

    threading.Thread(target=join, daemon=True, name="fleet-oom-self-join").start()
