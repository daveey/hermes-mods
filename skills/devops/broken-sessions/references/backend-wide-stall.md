# Every session stalls: backend-wide turn-start stall

The symptom: prompts in every chat sit for minutes before any reply. The cause is the backend process, not a
card, so a clean session-repair scan is expected here and proves nothing.

1. Measure the stall. For each session, find the gap from `tui prompt accepted` to the next
   `agent.turn_context` in `~/.hermes/logs/agent.log` and `agent.log.1`. A few seconds is normal; minutes is the
   stall. Report the numbers, not the anecdote.
2. Rule out the usual suspects quickly:
   - MCP lines like `stdio session recycled after idle_timeout_seconds; waiting for lazy reconnect` (midbrain or
     any other server) are normal idle churn, not a cause.
   - Model API latency appears as `API call` timings, not as an accept-to-turn gap.
3. Take two `py-spy dump`s of the backend pid a few minutes apart (machine-investigate § Live gateway stacks has
   the sudo/path rules).
4. In each dump, select the threads whose stack contains `run_body (tui_gateway/prompt_turn.py` and group them by
   their top 2–4 frames. Follow the chain from waiter, to the lock line, to the thread holding that lock
   (its frames are below the `with <lock>` in someone else's stack).
5. Name a cause only when its stack recurs in both dumps; a one-dump stack is an instant, not the hot path.
6. Before filing, search open cards for the same lock or function. A fix scoped to turn END (closeout hooks)
   does not cover turn START. File it as distinct, with the other card named in the body.
7. A restart only clears the current queue: the same contention rebuilds under load. When offering a drained
   restart, say it relieves the queue and leaves the cause in place.

## Turn start is fast, but Desktop times out (sessions "lost", no timing indicators)

If step 1 shows ~1 s turn starts, the turns are fine and the Desktop's API reads are failing. Measure both ends:

1. Client side: run `tailnet_exec` with host `mbp` (take names from `tailnet_hosts`, never from `tailscale status`
   device names). The log is `~/.hermes/logs/desktop.log` (not `~/Library/Logs`). Count
   `Timed out connecting to Hermes backend after <N>ms` lines per 10 min and note which paths time out. Also grep
   `teardown .* (quit)` / `Hermes Desktop is quitting`, because an app relaunch also blanks sessions.
2. Hub side: in `~/.hermes/logs/request-stages-<serve pid>.jsonl`, pair each `ingress` row with its
   `response_start` by `id` (`mono_ns` delta). Report per-path n/p50/p90/max for the last 15 min. Leftover
   ingress rows are requests that are still hung. `loop-watch: slow request GET <path> <N>s` in agent.log gives
   the longer history.
3. Restarts: `journalctl --user` for `Started hermes-always-on-backend@<port>`, and the `hub-core-update` tag
   for `mods-stale: ... restarted`. Each restart drops open sessions from view.
4. Stacks: lock convoys on these reads come in bursts, so two instant `py-spy dump`s can show zero waiters. Use
   `sudo -n py-spy record --nonblocking --idle --pid <pid> --duration 20 --rate 20 --format raw -o <f>`, then
   aggregate the leaf frames and the callers of the waiting frames. `--idle` is required, because lock waiters are
   off-CPU and a plain record omits them.
5. Then search open cards for the endpoint and lock (step 6). An already-merged fix may simply not be loaded yet.
   Check `git merge-base --is-ancestor <sha> HEAD` in `~/.hermes/hermes-agent`. The hub batches core updates
   (`core_update_instant_batch_s`), so tell the user when the fix will load rather than forcing a restart.
