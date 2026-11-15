# Shared blocker recovery: event ordering, operator release, input reuse and config reads

Mirror snapshot 4352; ordinals 4159, 4172, 4183, 4199, 4200, 4201, 4202, 4208, 4217, 4221, 4222, 4224, 4232, 4241.

Entries:

- **4159**: fix(kanban): contain check failures and normalize alias keys
- **4172**: fix(kanban): order operator releases by portable event time
- **4183**: repark accounting: blocked and parent-gated shared-clear wakes end the premise streak
- **4199**: Reuse validated shared-blocker inputs within a derivation
- **4200**: Cover cache-hit mutation and unknown blocker input identities
- **4201**: Snapshot config and memoize run effects per blocker pass
- **4202**: Tolerate a non-mapping kanban section in blocker input identity
- **4208**: fix(kanban): don't trust backfilled UUIDv7 uids as event time
- **4217**: fix(kanban): event order ignores a backfilled uid's clock
- **4221**: fix(kanban): dispatcher releases don't earn the operator exemption
- **4222**: shared-blocker check lane: kill surviving mutants M06/M15/M19 (+C10)
- **4224**: Blocker inputs: read live config once per input read, not twice
- **4232**: Shared-blocker hold: one read-only config read per decision
- **4241**: fix(kanban): a hold-retire clear is not a dispatcher release

## Behavior

Kanban cards can be held behind a shared blocker ("wall") that a registered check or an operator can clear. These entries make the hold/release logic correct and cheaper.

- **Containment and aliases** (4159): an unexpected exception in a cheap check no longer suppresses the independent release, probe and upkeep lanes. Configured alias keys are normalized with the same slug function and length limit as blocker ids; on collisions the lexicographically smallest original string key with a valid target wins, built-in identities stay authoritative, non-string keys and empty targets are ignored. The configuration docs describe this.
- **Portable event time** (4172, 4208, 4217): older copied-in assertions with larger local row ids no longer revoke newer operator unblocks. Events are ordered by UUIDv7 milliseconds (legacy `created_at` fallback), with local ids only breaking exact ties. A uid is trusted only when its milliseconds fall inside the row's own `created_at` second, because sync backfills uid-less rows with a fresh v7 uid; the same rule now orders the terminal guard's hold. The unblock reporting names the running sibling or probe interval that still holds a card.
- **Operator vs dispatcher release** (4221, 4241): only an operator `unblocked` earns the exemption from the running-sibling hold and probe interval. A newest `unblocked` followed by a `shared_blocker_cleared` event with `woke: true` (or a probe release) is treated as the dispatcher's own; a hold-retire clear without `woke` is not.
- **Repark accounting** (4183): `release_cleared_blockers` stamps `woke: true` on the clear event of every real wake, and `prior_same_premise` ends the same-premise streak on `woke` (and on legacy status `ready`, `review`, `todo`).
- **Cheaper derivations** (4199, 4200, 4201, 4202, 4224, 4232): validated cohort and check-event inputs are reused within one synchronous derivation; one config snapshot and a run-effect memo serve each blocker pass, the pre-compute key is the only live config read, and the hold decision reads config once. A non-mapping `kanban` config section yields no aliases instead of crashing.
- **Mutant-killing tests** (4222): edge tests for text-stamped legacy assertions, foreign homes sorting first, a home turning foreign mid-check, and future-dated receipts.

## How it works

- `hermes_cli/kanban_shared_blockers.py` holds derivation, release and hold logic; `kanban_shared_blocker_fence.py` holds `_event_order`, `_operator_released` and the decoder shared with `consistent_uid_milliseconds`; `kanban_shared_blocker_checks.py` and `kanban_idle_candidates.py` host the check lane and candidate scans.
- `hermes_cli/kanban_blocker_inputs.py` (new) provides an input pass: `config()` snapshot, validated input reads keyed by identity (writes, aliases, clock and physical identity invalidate; transactions bypass), and `run_effect` memoized per metadata string. `sweep_shared_blockers` runs its three lanes inside one pass. Nothing mutable (a `BlockerState`) is shared between passes.
- `hermes_cli/kanban.py` and `tools/kanban_tools_self_heal.py` use the shared read-only hold evaluator for unblock reporting. `hermes_cli/kanban_awaiting.py` consumes the `woke` flag.
- `docs/shared-blocker-recovery.md` and `website/docs/user-guide/configuration.md` are updated by 4159/4172.

## Code

Shipped non-test paths (union over these entries):

- `docs/shared-blocker-recovery.md`
- `hermes_cli/kanban.py`
- `hermes_cli/kanban_awaiting.py`
- `hermes_cli/kanban_blocker_inputs.py`
- `hermes_cli/kanban_idle_candidates.py`
- `hermes_cli/kanban_shared_blocker_checks.py`
- `hermes_cli/kanban_shared_blocker_fence.py`
- `hermes_cli/kanban_shared_blockers.py`
- `hermes_cli/kanban_sync_divergence.py`
- `hermes_cli/kanban_terminal_guard.py`
- `tools/kanban_tools_self_heal.py`
- `website/docs/user-guide/configuration.md`

## Tests

Test files shipped by these entries (none were executed by this mirror pass; they are listed as shipped, and no pass result is claimed here):

- `tests/hermes_cli/test_kanban_awaiting.py`
- `tests/hermes_cli/test_kanban_blocker_input_config.py`
- `tests/hermes_cli/test_kanban_blocker_input_identity.py`
- `tests/hermes_cli/test_kanban_blocker_input_key.py`
- `tests/hermes_cli/test_kanban_blocker_inputs.py`
- `tests/hermes_cli/test_kanban_blocker_park_wake.py`
- `tests/hermes_cli/test_kanban_shared_blocker_check_edges.py`
- `tests/hermes_cli/test_kanban_shared_blocker_hardening.py`
- `tests/hermes_cli/test_kanban_shared_blocker_release_kinds.py`
- `tests/hermes_cli/test_kanban_shared_blockers.py`
- `tests/hermes_cli/test_kanban_stale_wake_terminal.py`
- `tests/hermes_cli/test_kanban_sync_running_heal.py`

## Applying

These entries are part of the complete ordered snapshot 4352. Apply them only through the [Applying recipe in core/README.md](README.md#applying): the full ordered patch list with `git am -3 --empty=keep`, followed by the mandatory repair overlay that README names for snapshot 4352. They are not independent patches: later entries edit the files earlier entries create, and applying a subset is unsupported. Some entries need no configuration; settings introduced are named under Behavior.

## Gotchas

- Legacy blocked-branch clear events written before `woke` existed now read as operator releases (documented in 4241).
- The reuse optimizations were measured with synthetic and board-copy replays, not as live acceptance; the commit messages state the whole reclaim phase was not shown within its CPU budget.
- A replica that applies a status flip and its event in separate sync pages may briefly see inconsistent order; ordering relies on the portable clock rules above.

## Plugin?

None.
