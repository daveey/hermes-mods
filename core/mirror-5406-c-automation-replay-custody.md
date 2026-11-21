# Profile-exact automation replay custody through retirement

Mirror entries: 5241, 5242, 5243, 5244, 5245, 5246, 5247, 5248, 5249, 5250, 5251, 5252, 5253, 5254, 5255, 5266. Related implementation and regression commits are grouped here; later fixes in the group supersede their intermediate behavior.

## Behavior

A retiring backend hands pending automation replays to the owning profile's durable journal instead of treating retirement as a busy retry or losing custody. Local timers remain visible to Stop and retirement until worker entry wins; live and durable work for the same session key in different profiles never coalesces.

Busy/ownership/storage/rotation refusals retain durable custody without spending the model/build failure budget. Observed build/dispatch failures are bounded durably. A plain Stop fences undelivered work; send-now, rewind, delete, plugin interrupts and child interrupt forwarding do not create that Stop fence.

## How it works

The journal in the existing profile `state.db` stores immutable delivery identities, frozen batches, token-fenced claims, process-birth holders, real transcript receipts and durable worker admission. Publishing drops the local copy only after acceptance. The successor's producer-admission pass consumes exact-profile handoffs; live lookup and compression-tip recovery honor explicit selectors. Timer generations and local permits prevent stale timers/workers from consuming replacements or admitting cancelled payload members.

`Delivery.stage` commits the actual user row, message UID, counters and receipt in one transaction and restages that exact row after recovery. Worker entry, not a successful wake return, claims durable admission. An admitted batch is not dispatched again. `LocalPermit` tracks the in-memory counterpart through worker entry and retirement. Split-module helper access goes through the rebound server facade (5266).

Re-walled children preserve parent-batch provenance; child publication transfers parent custody atomically. Successful-context fences use when the latest replay row was written (`row_at`, falling back to the failure row), not merely the first failure timestamp. A later success can cover a late-published replay only if its context began after that row. Terminal payload compaction is bounded per pass and preserves exact owner/digest tombstones for arbitrarily late duplicate publication.

## Code

Paths below are relative to the source Hermes checkout, not files supplied by this documentation:

- `hermes_cli/host_loop_lease.py`
- `tui_gateway/automation_replay.py`
- `tui_gateway/automation_replay_dispatch.py`
- `tui_gateway/automation_replay_handoff.py`
- `tui_gateway/automation_replay_local.py`
- `tui_gateway/compute_host.py`
- `tui_gateway/methods_session_interrupt.py`
- `tui_gateway/producer_admission.py`
- `tui_gateway/prompt_turn.py`
- `tui_gateway/session_lifecycle.py`
- `tui_gateway/session_notifications.py`
- `tui_gateway/wake_profiles.py`

## Tests

**Source test coverage only — not executed by this mirror documentation task.**

Source suites cover journal idempotency/conflicts, frozen-batch joins, concurrent claimants, dead-holder recovery, transcript/receipt rollback, real message restaging and worker-entry fencing, retirement and producer-pass seams, launch/named-profile isolation, compression/live lookup, stale timers, local admission/cancellation/transfer races, durable failure caps across replacement, storage and foreign live-turn refusals, admitted-crash settlement without another wake, rewall parent/child custody, Stop versus non-Stop/compute-child interrupts, successful prompt context-start cancellation, cold-resume permits and terminal retention/compaction. Some tests use real temporary SessionDB rows, but wake/execution seams are controlled fixtures; this is not a live gateway update or provider-delivery test.

Relevant source suites/fixtures:

- `tests/tui_gateway/test_automation_replay.py`
- `tests/tui_gateway/test_automation_replay_custody_edges.py`
- `tests/tui_gateway/test_automation_replay_dispatch.py`
- `tests/tui_gateway/test_automation_replay_handoff.py`
- `tests/tui_gateway/test_automation_replay_profiles.py`
- `tests/tui_gateway/test_automation_replay_retention.py`
- `tests/tui_gateway/test_cold_resume_yields_paused_turn.py`
- `tests/tui_gateway/test_compute_host_turn_protocol.py`
- `tests/tui_gateway/test_refused_turn_retry.py`
- `tests/tui_gateway/test_replay_wake_profiles.py`
- `tests/tui_gateway/test_update_hold_submit_queue.py`

All runtime entry paths not explicitly covered above are **untested** by the cited batch coverage; no live deployment or provider run is claimed.

## Applying

Apply only as part of the **full ordered 5406-series** using the snapshot's [README Applying recipe](README.md), including its pinned replay bridges and **mandatory post-replay hostname-escape repair**. Follow the snapshot inventory and repaired-tree verification; do not cherry-pick this document's ordinals as a certified independent patch set. This document records source behavior/coverage, not independent patch certification or an executed mirror acceptance result.

## Gotchas

This is not exactly-once external API execution: a crash after durable worker admission leaves an unknown execution outcome, and the admitted identity is not run again. A hard crash before in-memory work is journaled can still lose that local custody. Refused admission differs from a failed build. Missing stores are not invented. Compaction bounds payload work per transaction, not total pending custody or permanent dedupe metadata. Only explicit Stop cancels undelivered automation.

## Why core instead of a plugin

Correctness crosses host-loop retirement, durable transcript transactions, notification admission, prompt worker entry, interrupt handling and profile routing. No single plugin hook owns all those atomic custody transitions.
