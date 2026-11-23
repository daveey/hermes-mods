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

- Per-thread context switches: `python3 <skill>/scripts/thread_cs.py <pid> 30` (deltas of
  `/proc/<pid>/task/*/status` switches and CPU, ranked, with thread `comm`); keep it in the skill, not scratch,
  which is pruned daily. Compare with the whole box.
- Measure the serve Desktop is actually routed to: list every `serve --isolated` pid with start time and port,
  and check which one the proxy forwards to. The updater replaces serves naturally, and a newer serve can run
  different threads at a very different total, so record pid and start time with every number. Tens of thousands per second per thread, each thread
  well under 100% CPU, means a convoy.
- User vs sys time: read `/proc/<pid>/stat` fields 14/15 over 5 s. Sys time close to user time means kernel
  overhead.
- `sudo timeout 6 strace -f -c -p <pid>`: futex dominating, with many EAGAIN errors, means threads are fighting over
  the GIL. strace slows the process, so read the shares, not the absolute rates.
- Mechanism: Python's sqlite3 releases the GIL around every prepare, step and close. Several threads running tight
  SQLite loops hand the GIL back and forth through futexes, so requests wait on the GIL while the host sits idle.

## 3. Attribute the hot threads

- Map high-switch thread ids to stacks with `sudo -n py-spy dump --nonblocking -p <pid>`. The dump header is
  `Thread <native tid> (idle|active)` with no thread name, so join on the tid from `/proc/<pid>/task/<tid>`
  (comm = name). Take about 10 dumps 0.4 s apart and count active/idle per tid; one dump can mislead.
- Pool and per-session worker threads get new tids as they respawn. Sum context switches by thread name as
  well as by tid, and re-sample at steady state (≥10 min after a restart, under real Desktop use). A sample
  taken minutes after a restart can miss threads that only start once sessions are watched.
- Record with `--gil` (GIL holders only) and with `--idle` (lock waits).
- Look for:
  - background loops whose interval floor is shorter than their run time, which makes them busy loops;
  - connections opened and closed on every call;
  - per-request scans of every board or DB;
  - per-row queries in a request handler: an anyio worker thread near 0% CPU with the highest switch rate is
    usually one request doing one tiny SQLite read per row (each read is a GIL release/reacquire). A CPU profile
    (`py-spy record`, inclusive share) ranks it near zero and points at background loops instead, so rank by
    per-thread switches before choosing what to move or batch, and ask for a query-count test per request.
  - a bounded worker pool that re-admits every item on each write: with N slots it runs N back-to-back
    reads forever while the DB keeps changing.
- Before choosing a wake source, count real changes against file churn over 60 s. Heartbeats, claims and
  leases rewrite the DB and WAL constantly; row-level revision cursors (Hermes: `kanban_task_change`,
  `session_change`) change far less often. A wake on mtime or inotify fires nonstop.
- Fixes to propose:
  - wake on revision-cursor changes instead of a fixed poll or file stamps;
  - batched reads: one short-lived read-only connection per DB per pass, set-based `IN (…)` queries, then
    close; the user does not want long-held connections;
  - cache per DB by `PRAGMA data_version`;
  - when several threads re-derive the same facts, propose one shared producer that turns changes into facts,
    with consumers reading precomputed results. Per-thread throttles and floors tend to move the hot spot
    without lowering the total: re-measure the whole-process total after one lands.
- Acceptance should include a context-switch ceiling and a per-route p95, measured with the same scripts before
  and after.
