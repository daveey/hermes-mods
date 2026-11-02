# A Kanban worker survives a long sleep with networkless DarkWakes

Patch: `patches/1509-fix-kanban-a-worker-survives-a-long-sleep-with-netwo.patch` (`84e5bc9460`, `fix(kanban): a worker survives a long sleep with networkless DarkWakes`).

Builds on [0933: the dispatcher extends a live worker's lapsed lease](mirror-0933-fix-kanban-don-t-kill-a-live-worker-for-a-lease-that-lapsed-while-the-mac-slept-192.md). The breaker in item 1 is the cross-turn stale-call breaker (#58962) in `agent/chat_completion_helpers.py`.

## Behavior

The incident: two worker runs on a laptop that slept for about 4.5 hours, with maintenance wakes that had no DNS, ended as `lease_lost` failures even though nobody else held the claims. The patch fixes three independent causes.

1. **Stale-stream kills caused by a suspend no longer trip the breaker.** The stream watchdogs age on wall time, so the first check after each wake kills the in-flight call. Each kill counted toward `_consecutive_stale_streams`, and the fifth DarkWake gave the worker up. Now a kill is not counted if the gap between `time.time()` and `time.monotonic()` has grown by more than 30 s (`_SUSPEND_SKEW_SECONDS`) since the breaker last moved. `monotonic` stops during suspend, so that growth equals the time slept. The baseline is re-marked on every bump and reset, so the excuse covers only the kill right after a suspend, never a later kill while awake. With no baseline (the first kill), the kill counts.
2. **In-place recovery waits for a lapsed lease instead of refusing it.** Before, `recover_failed_kanban_turns` refused at once when the claim lease had expired during the sleep (the dispatcher that renews it was asleep too). `worker_claim_state()` now returns:
   - `live`;
   - `lapsed`: every coordinate (run id, claim lock, worker pid) is still this process's and only the expiry has passed;
   - `lost`: a release, relock or takeover.

   On `lapsed`, `wait_for_live_claim` re-checks read-only every 15 s (`LAPSED_CLAIM_POLL_SECONDS`), for up to 300 *awake* seconds (`LAPSED_CLAIM_WAIT_SECONDS`, measured on `monotonic`). It retries in place once the dispatcher's holder-qualified extension (0933's `claim_extended`, `pid_alive`) makes the claim `live`. It gives up on `lost` or when the budget runs out. The worker never extends its own lease, and a `lost` claim is fenced out before the model is re-entered. The gateway path (`gateway/session_kanban.py`) uses the same three-way proof.
3. **The stale sweep leaves a booked transient exit to the crash sweep.** `release_stale_claims` runs before the crash sweep in the same dispatcher pass. A worker that exited 75 (`KANBAN_RATE_LIMIT_EXIT_CODE`, which means requeue with no failure) during a DarkWake was therefore reclaimed as `lease_lost`. Now an expired claim whose host-local worker is dead, and whose run authority recorded exit 75 under this claim (`managed_run_exit_code`), is kept by the stale sweep (`_exit_booked_transient`), and the crash sweep books the neutral `rate_limited` requeue. A live worker, a remote claim, or a dead worker with no booking or another exit code is reclaimed as before.

## How it works

The breaker code moves to the new `agent/chat_completion_stale_breaker.py`, re-exported from `chat_completion_helpers`, adding `_mark_stale_clock` and `_slept_since_stale_mark`. `agent/kanban_turn_recovery.py` adds `worker_claim_state`, `wait_for_live_claim`, `_bound_claim_state` and the `_sleep`/`_awake_clock` test seams. `hermes_cli/kanban_db_stale_claims.py` adds `_stale_claim_kept`, which combines 0933's live-worker extension with the new transient-exit rule. `kanban_db.release_stale_claims` calls it.

## Code

`agent/chat_completion_helpers.py`, `agent/chat_completion_stale_breaker.py` (new), `agent/kanban_turn_recovery.py`, `gateway/session_kanban.py`, `hermes_cli/kanban_db.py`, `hermes_cli/kanban_db_stale_claims.py`.

## Tests

From the patch; **not run for this mirror**. The commit message says that 13 of the 15 new tests were red on the base, that the controls were green, and that all were green with the patch.

- `tests/agent/test_kanban_darkwake_recovery.py` (new): `test_suspend_caused_stale_kills_do_not_trip_the_breaker`, `test_awake_stale_kills_still_trip_the_breaker` (control), `test_breaker_without_a_baseline_counts_the_first_kill`, `test_claim_state_separates_a_lapse_from_a_takeover` (parametrized), `test_lapsed_lease_is_renewed_and_the_run_survives` (parametrized over the failure type), `test_stolen_lease_stays_fenced`, `test_lease_never_renewed_gives_up_after_the_awake_budget`, `test_transient_exit_is_booked_by_the_crash_sweep_not_as_a_failure`, `test_exit_without_a_booking_is_still_reclaimed` (control).
- `tests/agent/test_kanban_turn_recovery.py` and `tests/gateway/test_session_kanban_turn_recovery.py`: existing expired-lease tests set `LAPSED_CLAIM_WAIT_SECONDS = 0`, so they still deny at once.
- The suspend is simulated through the clock seams. No test actually suspends a host or drops the network.

## Applying

Apply in full-series order (`git am -3 --empty=keep`, [README.md](README.md) Applying). It needs 0933's `_extend_live_stale_claim` and the run authority's `managed_run_exit_code`.

## Gotchas

- The 300 s budget counts awake seconds only. A worker that goes back to sleep while it waits keeps waiting across that sleep, so recovery can take much longer in wall time.
- If no dispatcher pass runs within the budget (for example the dispatcher is down), recovery gives up and the run fails, as it did before this patch.
- A suspend shorter than 30 s is not recognized, so a stale kill right after it counts as usual.

## Plugin?

No. The breaker, in-place recovery and stale-claim sweep are core agent, gateway and Kanban DB paths with no plugin hook.

## Follow-up patch (1544): offline DarkWakes don't spend the recovery budget

Patch: `patches/1544-fix-kanban-preserve-recovery-budget-across-offline-D.patch` (`c83c7fc67a`, `fix(kanban): preserve recovery budget across offline DarkWakes`).

**Behavior.** After 1509 a worker survived the lease lapse, but each networkless maintenance wake still failed one turn and spent one in-place recovery attempt, so a long sleep with several DarkWakes could use up `max_recovery_attempts()` and fail the run. Now a network-class failure seen while the host is observably offline waits for the network instead, and the retry after that wait is not charged. Failures with working DNS keep the normal bounded budget.

**How it works.** New `wait_out_worker_offline(result, check)` in `agent/kanban_turn_recovery.py`:
- It applies only when `failure_reason` is `timeout` or the error text is a local network error (`is_local_network_error`); anything else returns `False` (charged as before).
- "Offline" needs both DNS controls to fail: `NETWORK_CONTROL_HOST` (`github.com`) and `example.com`. A single blocked or sinkholed control name is not offline.
- It loops the shared `retry_utils.wait_for_network` in windows of `DEFAULT_OFFLINE_WAIT_SECONDS` (900 awake seconds), repeating while `check()` still says this worker owns the exact claim, and the wait is interrupted when the claim is lost.
- It returns `True` after a real offline wait that ended online (uncharged retry), `False` when DNS came back on the first lookup (`already_online`, a one-lookup blip is charged), and `None` when the claim was lost.

`recover_failed_kanban_turns` calls it before every attempt. `None` emits recovery-skipped and stops before any backoff or re-entry. An uncharged retry logs `offline recovery uncharged` in place of `attempt N/M`. The return value is now the count of charged (online) attempts, and `should_recover_turn` is asked with `attempt=0` because the loop enforces the budget itself. No lease is written. The worker still never extends its own claim.

**Code.** `agent/kanban_turn_recovery.py`.

**Tests.** From the patch; **not run for this mirror**. In `tests/agent/test_kanban_darkwake_recovery.py`:
- `test_repeated_offline_darkwakes_preserve_the_run`: parametrized over a Codex connection error and an Anthropic stale stream, with simulated sleep gaps on a host-local managed run.
- `test_offline_claim_loss_does_not_spend_an_attempt` and `test_offline_wait_still_fences_a_stolen_run` (`reclaim`, `relock`).
- `test_offline_gate_preserves_normal_retry_authority`: online and non-network failures stay charged.
- `test_mixed_offline_and_online_failures_only_charge_online_attempts`.
- `test_one_failed_control_lookup_is_not_an_offline_exemption` (`blocked-control`, `single-blip`).

`tests/agent/test_kanban_turn_recovery.py` and `tests/gateway/test_session_kanban_turn_recovery.py` only add an online-DNS fixture. The source owner reported 29 green in the DarkWake file (1509 baseline: 6 failed, 23 passed) and green exact-SHA CI. DNS and sleep are simulated through monkeypatched seams; no real outage was tested.

**Gotchas.**
- DNS answering is permission to retry, not proof that the provider is reachable. A provider outage while DNS works still spends attempts.
- The offline wait has no attempt cap of its own. It is bounded only by claim ownership, so a host that stays offline keeps the run claimed until the dispatcher reclaims it.
- Apply after 1509 in full-series order; standalone application is not certified.
