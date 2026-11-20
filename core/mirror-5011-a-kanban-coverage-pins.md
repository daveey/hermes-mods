# Kanban and TUI coverage pins for previously unguarded mutations

Mirror snapshot 5011; ordinals 4359, 4368, 4391, 4396, 4397, 4398, 4402, 4406, 4407, 4412, 4424, 4433, 4434.

Entries:

- **4359**: Test warm observation cache pruning across state churn
- **4368**: Cover moved compact row before later stale claim
- **4391**: test(kanban): pin decoded eviction on gateway rename
- **4396**: test page-only intake retention protection
- **4397**: test(kanban): pin _hold_config's single read and backend-unavailable fallback
- **4398**: test(kanban): unreadable board placement config falls back to an empty policy
- **4402**: test(kanban): cover replica skip dedupe past refresh
- **4406**: Cover cleared blocker-input snapshot alias freshness
- **4407**: test(kanban): hub replica ref memo prunes past its bound
- **4412**: Test per-assertion expiry inside retained blocker task
- **4424**: test(kanban): pin spend-decision routing, open-run hold guard, wake and TUI notice kinds
- **4433**: Test scratch home-anchor retarget inode fence
- **4434**: Pin annotation narrow-key fallback and memo contracts

## Behavior

All entries here are tests only (no production edits). Each adds assertions that fail under a specific mutation of an already-shipped behavior:

- Remote projection memory (4359, 4368, 4391): warm observation cache pruning across state churn keeps only current states while durable first-seen history persists; a moved compact row leaves its claim unchanged while the next dead-holder stale claim settles with its swept receipt; a gateway rename evicts decoded entries and rename-back keeps ledger identity.
- Intake retention (4396): page-only intake occurrences are protected until the pending page is acknowledged, then cleaned.
- Blockers (4397, 4406, 4412): `_hold_config` reads once and falls back when the config backend is unavailable; cleared blocker-input snapshot alias freshness under a nested read; per-assertion expiry inside a retained blocker task.
- Placement (4398): an unreadable board placement config falls back to an empty policy, with controls.
- Pacing replica skip (4402): replica skip dedupe past the refresh boundary.
- Replica refs (4407): the hub replica ref memo prunes once past its bound while evicted refs rescan correctly.
- Spend routing (4424): headless chat links are not bound, own-gateway vs remote link platforms, pending hold leaves open-run cards untouched, stale decision wakes relabel after promote, and the TUI poller delivers the decision notice once.
- Scratch GC (4433): home-anchor retarget inode fence with a real symlink swap.
- Annotation narrow keys (4434): fallback and memo contracts.

## How it works

Not applicable beyond the tests: each pin drives the real code through public seams (real SQLite, fake clocks, real symlinks) rather than asserting source shape.

## Code

These entries ship no non-test source paths; they change tests only.

## Tests

Test files shipped by these entries (source-side tests only; none were executed by this documentation pass, so no pass result is claimed here):

- `tests/ci/test_flaky_intake_retention.py`
- `tests/hermes_cli/test_kanban_annotation_host_regressions.py`
- `tests/hermes_cli/test_kanban_annotation_local_regressions.py`
- `tests/hermes_cli/test_kanban_blocker_input_config.py`
- `tests/hermes_cli/test_kanban_blocker_input_key.py`
- `tests/hermes_cli/test_kanban_blocker_rows.py`
- `tests/hermes_cli/test_kanban_llm_pacing_empty_windows.py`
- `tests/hermes_cli/test_kanban_placement_board_home.py`
- `tests/hermes_cli/test_kanban_replica_refs_kept_readers.py`
- `tests/hermes_cli/test_kanban_scratch_gc_symlinked_home.py`
- `tests/hermes_cli/test_kanban_spend_decision_routing.py`
- `tests/tui_gateway/test_remote_projection_bystander_memory.py`

## Applying

These entries are part of the complete ordered snapshot 5011. Apply them only through the [segmented Applying recipe in core/README.md](README.md#applying): the full ordered patch list (every segment, in order) with `git am -3 --empty=keep`, followed by any repair overlay that README names for the segment. They are not independent patches: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported. Settings introduced are named under Behavior.

## Gotchas

- Mutation-kill results quoted in commit text are source-side claims from the original authors; this documentation pass did not rerun them.
- Because these are test-only, applying the snapshot's earlier entries is a prerequisite; they will not pass in isolation.
