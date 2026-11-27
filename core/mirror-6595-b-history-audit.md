# Immutable historical rejection observations and fresh audit cuts

Patch coverage: 6304, 6305, 6306, 6307, 6308, 6309, 6310, 6311, 6316.

## Behavior

Historical rejection bytes can be recorded as an immutable observation, separately from canonical acceptance, historical identity certification or execution authority. Explicit observation and fresh-cut diagnostics do not retire or replay rejected work and do not clear a hold.

An audited sync-diff cut can distinguish exactly observed historical residue from current unresolved work only after fresh canonical and local evidence passes every premise.

## How it works

Strict bounded JSON rejects duplicates, nonfinite values, bad Unicode, unexpected nested claims and incorrect scalar types. Exact source bytes and unverified extra columns are retained. An append-only sidecar binds endpoint/board/gateway, operation and event identities to request/receipt bytes. The first transaction stages send intent; once it exists, retries are GET-only, even after a crash before the first POST. A successful POST alone never seals custody: exact authenticated GET readback is required.

Transport rejects redirects and noncanonical endpoint aliases, bounds bodies/deadlines and allows cleartext only on loopback. Configured-board commands validate distinct live/shadow paths and current local source/fences. Audit cuts require completed diagnostic refresh, fresh exact-source current proof and local reservations. Unattributed orphan run work vetoes the cut; ordinary row drift, unrelated rejection or expiry remains visible. The custody sidecar is explicitly not a live board for connection scanning.

## Code

Affected implementation and integration paths in the source series:

- `hermes_cli/kanban_history_residue_board.py`
- `hermes_cli/kanban_history_residue_custody.py`
- `hermes_cli/kanban_history_residue_cut.py`
- `hermes_cli/kanban_history_residue_http.py`
- `hermes_cli/kanban_history_residue_local.py`
- `hermes_cli/kanban_history_residue_source.py`
- `hermes_cli/kanban_parser.py`
- `hermes_cli/kanban_sync_cli.py`

## Tests
All entry paths described here are untested in this review; the files below are source references, not fresh execution evidence.

**Source coverage only — not executed in this mirror run.** The following real source test files were inspected. Their assertions describe intended regression coverage, not a fresh pass result or deployment acceptance:

- `tests/hermes_cli/test_kanban_history_residue_source.py`
- `tests/hermes_cli/test_kanban_history_residue_custody.py`
- `tests/hermes_cli/test_kanban_history_residue_http.py`
- `tests/hermes_cli/test_kanban_history_residue_http_boundaries.py`
- `tests/hermes_cli/test_kanban_history_residue_cli.py`
- `tests/hermes_cli/test_kanban_history_residue_cut.py`
- `tests/hermes_cli/test_kanban_history_residue_local.py`

Entry paths beyond those represented by these suites are untested here. No runtime tests, browser tests, service restarts or external protocol calls were performed for this documentation task.

## Applying
Use README's complete segmented replay and mandatory post-replay escape repair together; neither this feature's ordinals nor the immutable mails alone constitute the installation recipe.

Apply the mirror series as a dependency unit using the inventory, replay boundaries and any required overlays in [README.md](README.md). These ordinals are documentation coverage, not an independently cherry-pickable patch set: shared schemas, bootstrap wiring and later hardening depend on surrounding entries. Follow that README for the matching source/base and application verification; this document does not replace its replay recipe. No application, installation or deployment is claimed by this mirror documentation run.

## Gotchas

Saved receipts are historical evidence, never current authority. A clean audited cut may still report retained rejected operations; original rows remain untouched. Missing readback remains unresolved without reposting or changing operation identity. Diagnostic tests and safety refusal assertions are distinct, not substitute acceptance evidence.

## Why core, not a plugin?

Exact-request transport custody and sync-diff consistency cuts require core locks, local/replica schemas and canonical protocol validation.
