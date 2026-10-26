# Canonical local owner projection and nonblocking ledger refresh

## Behavior

Patches 0381–0384 repair the Python session-task projection used by task inventories. Exact gateway/profile/board/task identity is required for local authority. A local open task suppresses a duplicate receipt only when local associations cover every original/resolved session alias. Other profiles and unknown owners remain distinct unfinished obligations; genuine human blockers remain visible.

Local terminal evidence can release an obligation only with an exact durable creation marker and the existing lifecycle completion rules. A reopened local task overrides stale terminal evidence. Unreadable or ambiguously routed local state becomes unknown, not completed.

Projection refresh does not queue behind a ledger writer. Failed opportunistic refresh retains conservative unknown/unfinished state. This removes one demonstrated five-second writer wait, not every possible inventory delay.

## How it works

The refresh writer uses timeout zero. Inventory reads use a read-only ledger connection rather than schema initialization. Local projection overlays a consistent read-only owner-board transaction, requiring the active profile and expected profile-home board path. It reuses owner-observation completion evidence and does not mutate receipts or observation versions. Open-row dedup checks both receipt aliases. Legacy aggregation excludes only the current exact owner, retaining same-gateway foreign-profile and unknown-owner rows.

## Code

- `hermes_cli/kanban_origin_ledger.py`: nonblocking refresh, read-only projection and local overlay.
- `hermes_cli/kanban_origin_local.py`: strict local authority and creation-marker evidence.
- `hermes_cli/kanban_session_activity.py`: exact open-association dedup and foreign-profile retention.

## Tests

Independent exact-head review at `b47371bdf826a63b7273235ddacb7a88c3cd6ac6` ran 206 tests across 15 Python files through the official runner, with retries disabled. Blocking `scripts/check` passed all 12 checks during implementation.

- `tests/hermes_cli/test_kanban_origin_projection_contention.py`: real held SQLite writer, bounded projection, conservative uncertainty; red before the fix.
- `tests/hermes_cli/test_kanban_local_receipt_authority.py`: running/scheduled/human-blocked local authority; unknown/different owners, aliases, terminal receipts and missing boards.
- `tests/hermes_cli/test_kanban_remote_identity.py`: same-gateway foreign-profile and unknown-owner preservation; red before the fix.
- `tests/hermes_cli/test_kanban_local_origin_projection.py`: verified completion/reopen/human-blocked; raw terminal states, missing/wrong markers, missing boards and pinned-board/profile mismatch.
- Existing session activity, origin projection, ledger, observation, evidence and alias-release suites were included in review.

These exercise backend functions. Live REST timing, installed process revision, plugin interaction and Desktop renderer acceptance are unverified; passing Python tests does not establish those layers.

## Applying

Prefer the normal supported fork update containing `b47371bdf826a63b7273235ddacb7a88c3cd6ac6`. These are core Python changes, not a hot-reloadable disk plugin. Apply through the normal idle update mechanism; never move or rebuild a checkout under active workers. A Desktop reload alone does not prove the update applied.

For reproduction in an isolated checkout at `f63cdf362c`, apply patches 0381–0384 in order with `git am -3`. They depend on the preceding origin-ledger implementation; they are not standalone patches for arbitrary upstream versions. Verify loaded code ancestry, then the owner-pinned task endpoint and actual rendered task identities/counts after activation.

## Gotchas

Ownerless legacy records cannot be retired solely because a qualified row has the same task ID. Missing owner provenance is not proof of duplication. This repair neither deletes replica records nor invents a migration. Such obligations remain unknown/non-human until an authoritative supported reconciliation can establish ownership.

Other read and snapshot contention paths still exist. A reported long-running endpoint timeout must be attributed using the actual loaded backend and thread stacks; the demonstrated writer hazard is not proof of its entire cause. No live runtime restart or database reconciliation is part of this patch set.

## Why not a plugin?

The authoritative receipt projection and local completion evidence belong to core. Replacing them with a plugin wrapper would duplicate lifecycle authority and introduce load-order ambiguity.
