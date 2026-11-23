# Bounded lane-source metadata reads

Source patch ordinals: 5548.

## Behavior

Lane presentation avoids materializing unrelated terminal-card metadata while preserving authoritative source selection for open cards and terminal prerequisites.

## How it works

`_metadata` initially selects only open statuses. Exact counterpart lookups in batches of 400 let a newer terminal row suppress an open copy. `_selected_source` retains pending-local precedence and revision comparison. Terminal-only parents resolve their metadata lazily through `Sources._db`, rather than through a whole-history scan.

## Code

- `hermes_cli/kanban_lanes_sources.py`

## Tests

Source test coverage only; **NOT executed this mirror pass**. No test result or deployment verification is claimed.

`tests/hermes_cli/test_kanban_lanes_sources.py` adds historical-metadata bounds and terminal-only parent revision/pending-authority cases alongside the existing no-bulk-terminal-body assertion.

## Applying

Apply with the existing lane-flow open-status definition and evidence/source abstraction. Replay the complete ordered mirror using the snapshot's README/policy recipe; this document is not a standalone patch-application guarantee.

## Gotchas

Do not optimize away terminal counterparts: a closed remote copy can invalidate a local open candidate. Lazy parent reads still use pending-local authority and sync revisions.

## plugin?

No. This changes the core lane read model.
