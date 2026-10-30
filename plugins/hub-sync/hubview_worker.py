"""Child process that runs hubview reads for the gateway and answers with ready-to-send JSON bytes.

Why a process: every hubview read decodes and copies thousands of replica rows in pure Python (0.1-0.4 s of CPU
each). Inside the gateway that work held the GIL against the asyncio loop that serves the whole API. On 2026-10-30
11:08-11:27 PDT (load 66-96), the loop thread spent 64% of its samples waiting on the GIL. hub-sync polls then ran
12-25 s, and 666 Desktop reads on conn=local hit their 4 s timeout, mostly hub-sync/status, which costs 2 ms of CPU.
In a child the decode competes for CPU like any other process instead of for the gateway's GIL, and the gateway
only relays bytes (no jsonable_encoder pass over thousands of rows on the loop).

Protocol (stdin/stdout, one request at a time):
  request:  one JSON line  {"fn": "<read>", "args": [...]}
  response: one JSON line  {"ok": true, "len": N}  then exactly N bytes of the JSON body
            or             {"ok": false, "error": "<repr>", "len": 0}
EOF on stdin (the gateway exited or replaced us) ends the process.

Run: python hubview_worker.py <path to hubview.py>
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys

# Only the read-only views; ``request`` (the one write) stays in the gateway.
READS = frozenset({"status", "sessions", "inbox", "projects", "tasks", "where", "session_links", "requests"})


def load_view(path: str):
    spec = importlib.util.spec_from_file_location("_hermes_hub_sync_view_worker", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def serve(view, stdin, stdout) -> None:
    for line in stdin:
        if not line.strip():
            continue
        try:
            req = json.loads(line)
            fn = req.get("fn")
            if fn not in READS:
                raise ValueError("not a hubview read: %r" % (fn,))
            body = json.dumps(getattr(view, fn)(*(req.get("args") or [])), separators=(",", ":"),
                              default=str).encode()
            head = {"ok": True, "len": len(body)}
        except Exception as e:  # noqa: BLE001 - report it; the gateway falls back to an in-process read
            body, head = b"", {"ok": False, "error": repr(e)[:500], "len": 0}
        stdout.write(json.dumps(head).encode() + b"\n")
        stdout.write(body)
        stdout.flush()


def main(argv) -> int:
    if len(argv) != 2:
        sys.stderr.write("usage: hubview_worker.py <hubview.py>\n")
        return 2
    try:
        os.nice(5)  # background reads; the gateway's own work goes first on a loaded Mac
    except OSError:
        pass
    serve(load_view(argv[1]), sys.stdin.buffer, sys.stdout.buffer)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
