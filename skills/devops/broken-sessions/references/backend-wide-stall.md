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
