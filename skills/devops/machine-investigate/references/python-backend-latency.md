# Slow Python backend on an idle host (Hermes serve)

Use when Desktop or API requests to a Hermes `serve` backend are slow while host load, memory and the network
look fine. When the host is idle, attribute the slowness with in-process measurements before blaming load or a
post-restart backlog.

## 1. Separate handler time from queueing

- `curl` a 404 path and a trivial route on the backend port. If the 404 returns in ~1 ms but real routes take
  seconds, the event loop is fine and the time goes to handlers or the thread queue.
- Authenticated timing on the hub: the always-on proxy `:9147` forwards to the live serve (check with
  `ss -tnp | grep pid=<proxy>`). It accepts `Authorization: Bearer $(cat ~/.hermes/desktop-ssh/always-on/token)`.
  A Desktop serve's own `--ssh-session-token-file` is unlinked after start, so use this token instead.
  Send the same request one at a time: a wide spread (for example 0.2 s to 15 s) with no concurrency from you
  points to contention, not a slow query.
- `~/.hermes/logs/loop-watch.jsonl`: aggregate `kind: request_stats` rows for the pid. Per-route stats are under
  `paths`, giving n, slow, p50_s, max_s and max_inflight. In `agent.log`, `slow request ... awaiting=anyio ... wait`
  means the request was queued waiting for a worker-thread token.

## 2. Test for a GIL convoy

- Per-thread context switches: sum the `voluntary_ctxt_switches` deltas from `/proc/<pid>/task/*/status` over 5 s,
  ranked by thread `comm`, and compare with the whole box. Tens of thousands per second per thread, each thread
  well under 100% CPU, means a convoy.
- User vs sys time: read `/proc/<pid>/stat` fields 14/15 over 5 s. Sys time close to user time means kernel
  overhead.
- `sudo timeout 6 strace -f -c -p <pid>`: futex dominating, with many EAGAIN errors, means threads are fighting over
  the GIL. strace slows the process, so read the shares, not the absolute rates.
- Mechanism: Python's sqlite3 releases the GIL around every prepare, step and close. Several threads running tight
  SQLite loops hand the GIL back and forth through futexes, so requests wait on the GIL while the host sits idle.

## 3. Attribute the hot threads

- Map high-switch thread ids to stacks with `sudo -n py-spy dump --nonblocking -p <pid>`.
- Record with `--gil` (GIL holders only) and with `--idle` (lock waits).
- Look for:
  - background loops whose interval floor is shorter than their run time, which makes them busy loops;
  - connections opened and closed on every call;
  - per-request scans of every board or DB.
- Fixes to propose:
  - wake on data changes instead of a fixed poll;
  - keep reader connections open per thread;
  - cache per DB by `PRAGMA data_version`;
  - move producers out of the request-serving process.
- Acceptance should include a context-switch ceiling and a per-route p95, measured with the same scripts before
  and after.
