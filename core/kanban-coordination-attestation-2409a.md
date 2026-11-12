# Kanban coordination portability and fresh capability evidence

Mirror entries: [2257](patches/2257-fix-kanban-gate-coordination-portability-on-worker-attestations-daveey.patch), [2258](patches/2258-test-kanban-prove-imported-coordination-card-execution-fence-daveey.patch), [2259](patches/2259-fix-kanban-distinguish-coordination-commands-from-prose-daveey.patch), [2260](patches/2260-fix-kanban-preserve-host-paths-and-board-administration-fence-daveey.patch), [2261](patches/2261-fix-kanban-treat-stale-profile-tool-observations-as-unknown-daveey.patch).

## Behavior

- Coordination reads can be worker-portable only when every configured destination has fresh operator capability attestations and is active, unheld and pickup-capable.
- Pickup and execution recheck the actual worker destination for worker-first-v1 cards, including already-imported cards.
- Recognize executable coordination commands separately from source-code/prose mentions; ambiguous explicit invocations fail closed.
- Keep board administration, independent host paths, extra operator markers and legacy automatic pins fenced.
- Treat stale, future-dated or invalid profile-tool observations as unknown, not affirmative tool absence; a fresh negative observation still refuses missing tools.

## How it works

kanban_placement_coordination maps allowed hermes-hub status/pull and cross_task read/list operations to coordination-read, with separate broker, core-ship and tailnet capabilities. All pool members need well-formed labels and last_seen age between zero and 300 seconds. Action recognition uses command tokens, tool-call syntax and execution context instead of treating every tool-name mention as intent. Marker exemption touches only the approved hub-command/broker patterns, never the original host-path and configured extra markers. claim_refusal validates an explicit destination; execution_refusal invokes it again. target_info ignores expired observations before initializing its tool map.

## Code

- `hermes_cli/kanban_placement.py`
- `hermes_cli/kanban_placement_coordination.py`
- `hermes_cli/kanban_runs_on_capabilities.py`
- `hermes_cli/kanban_worker_placement.py`

## Tests

Source test references below are inspection evidence only. **Not rerun by this mirror documentation pass.** No live/runtime or CI-green claim is made.

- `tests/hermes_cli/test_kanban_coordination_placement.py` — `test_attested_reader_and_broker_are_portable`, `test_missing_reader_is_named_before_enrollment`, `test_unusable_attestation_fails_closed`, `test_every_member_must_carry_the_capability` (additional cases in this file).
- `tests/hermes_cli/test_kanban_stale_profile_tools.py` — `test_stale_or_invalid_tools_allow_move_but_leave_claim_gate`, `test_fresh_negative_still_refuses_missing_tools`.

## Applying

Apply these entries only as part of the ordered mirror series using [README Applying](README.md), including its replay bridges and required post-replay overlays. They are not independent cherry-picks. This documentation pass did not replay or install the series.

## Gotchas

Attestation labels are rollout evidence, not credentials, permissions or proof of a live worker installation. No pool or malformed registry means no coordination exemption. Legacy heavy routing cannot borrow a worker-pool exemption. Unknown profile-tool availability still leaves the claim-time gate in place; it does not assert the tool is installed.

## Plugin

No. This changes core behavior or its regression infrastructure; a plugin cannot enforce the same internal invariants.
