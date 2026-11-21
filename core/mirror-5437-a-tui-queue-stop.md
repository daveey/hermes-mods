# Queue typed prompts behind gateway background turns; Stop ends exactly the holder turn

Mirror entries: 5427, 5428, 5429, 5430, 5431.

## Behavior

- Typing into a chat held only by the live default gateway's own automated turn (a notice or close-out turn) no longer takes the chat over and kills that turn with a lease-lost error. `prompt.submit` forwards the prompt into the gateway's FIFO and returns the queued, forwarded receipt that Desktop already renders as a queued row. Any other holder, an unforwardable message, or a forward that queued nothing keeps automatic take-over; an unconfirmed forward is never taken over.
- Stop on such a chat ends only the automated holder turn, without lease loss; the queued prompt then runs once. Stop never takes over a typed (unstamped) turn: it ends nothing and tells the user. Ordinary idle Stop (automation replays, post-turn review) always runs as well; `background_turn` is only a hint.
- A notice wake refused only by the core update's admission hold charges no attempt, is stamped `closeout.hold_wait`, and is due the moment the hold lifts; a watcher runs the guarantee pass shortly after the lift, then periodically while wakes wait for a slot.
- A retiring backend publishes a marker; a wake refused because every other owner is retiring retries briefly, then answers busy (transient) rather than refused.
- Desktop tags primary-socket events with the primary's own connection id, so plugins filtering by connection stop misattributing primary pushes.

## How it works

`gateway_background_holder` decides whether the only other holder is the very gateway that `owner_forward` dials for the chat's profile home (durable holder counted only in this pid namespace; registry lease only for a live local process with its recorded start time), and whether the newest turn is automated.

Stop (`session.interrupt`) runs only cheap checks inline; holder discovery and the end run on a worker thread and report by error events, with a bounded wait. Automated turns stamp their kind into the durable lease holder (`:auto=<kind>`); `holder_auto_kind` reads only the segment before the last `:platform=`, so a client-chosen id cannot fake it. `SessionDB.request_turn_end` records an end request (compare-and-set into `session_turn_end_requests`) only while that exact holder holds the lease; the holder's lease tick honours it with the agent's ordinary interrupt, so the lease is released normally. Capability is advertised as turn-end-request-v1; an older gateway keeps session take-over only when its ledger is empty, otherwise the user is told the message runs when the background turn ends.

`notice_hold_wait` is the watcher; `host_loop_lease.retire_host_loops` publishes and withdraws the retiring record under a lock. The retiring-busy record carries retry and written times and is ignored after 30 seconds; short retries stop after 10 per notice row. 5431 only rewords the unstamped-turn notice, since it may be the user's own forwarded prompt caught before its row was written.

## Code

- `tui_gateway/gateway_background_holder.py`, `methods_prompt.py`, `methods_session_interrupt.py`, `owner_forward.py`
- `tui_gateway/notice_hold_wait.py`, `notice_guarantee.py`, `kanban_unloaded.py`, `kanban_unloaded_gates.py`, `session_notifications.py`
- `agent/turn_end_request.py`, `agent/turn_facade_lease.py`, `agent/turn_facade.py`, `gateway/run_runtime.py`
- `hermes_state_common.py`, `hermes_state_compression.py`, `hermes_cli/host_loop_lease.py`
- `tui_gateway/contracts/prompt_voice.py`, `contracts/sessions.py`, `apps/shared/src/gateway-contract.*`
- `apps/desktop/src/store/gateway-primary-socket.ts`, `apps/desktop/src/app/gateway/hooks/use-gateway-boot.ts`

## Tests

**Coverage inventory only — source tests were not executed by this mirror documentation task.**

Tests cover queueing behind a background turn (a forwarded turn waits for the lease and runs once), Stop racing a queued admission, failed and late-failing Stops, the targeted end request with holder fencing, anchored stamp parsing, unstamped-turn handling, hold-wait across a lift then ordinary busy, and the retire/resume race with retiring markers. The Stop notice wording from 5431 is asserted only in a single added line. Desktop connection tagging has a scope test; live gateway behavior is untested.

- `tests/tui_gateway/test_background_turn_queue.py`
- `tests/tui_gateway/test_notice_hold_wait.py`
- `tests/agent/test_turn_end_request.py`
- `tests/hermes_cli/test_host_loop_retiring_marker.py`
- `apps/desktop/src/store/gateway-connection-scope.test.ts`

## Applying

Apply these entries only as part of the **whole ordered 5437 patch series**, including the snapshot replay bridges and the mandatory post-replay hostname-escape repair. Follow [the core README applying recipe](README.md), including its inventory/tree verification and repaired-tree check. These entries are not advertised as independently applicable patches. This documentation task did not replay patches in isolation.

## Gotchas

Stop outcomes are reported asynchronously by error events, not the RPC reply. An unstamped running turn is treated as typed and is never ended by Stop. Mixed-version gateways lacking turn-end-request-v1 fall back to the older, more limited behavior. Retiring-marker records are advisory and expire.

## Why core instead of a plugin

Turn leases, the durable end-request table, the gateway FIFO forward and the RPC handlers are core runtime state; a plugin cannot fence an exact turn generation or change take-over semantics.
