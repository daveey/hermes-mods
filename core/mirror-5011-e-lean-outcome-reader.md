# Lean session-outcome task reads

Mirror entries: 4809, 4810.

## Behavior

Session outcome decisions read the same listed cards and waits without paying for sidebar-only enrichment. Full session_tasks retains its enriched contract.

## How it works

Shared local selection and remote-append helpers preserve creator/subscriber membership, precedence, watch exclusions, lineage and pending-release handling. Wait derivation shares the selection read transaction. Lean and full busy-fallback memo shapes use separate namespaces with copied rows, independent bounds, eviction and reset. Known nonempty counts cannot be silently converted into an empty successful task list.

## Code

- `hermes_cli/kanban_session_activity.py`
- `hermes_cli/kanban_session_activity_busy.py`
- `hermes_cli/kanban_session_outcome_tasks.py`
- `hermes_state_outcome.py`

Paths above refer to the source tree represented by the complete mirror, not files added by this documentation. Historical deletions and later fixes are described explicitly where relevant.

## Tests

Outcome-reader parity and lean-reader suites compare decisions and waits across valid/malformed requests, local parks, remote freshness, counts, deduplication and pending release. Follow-up tests assert both namespaces reset and each bound evicts its oldest retained entry.

Source test coverage is described from the normalized patches; these tests were **not freshly executed** for this documentation pass. Test paths (including historical paths removed by later entries):

- `tests/hermes_cli/test_kanban_outcome_reader_parity.py`
- `tests/hermes_cli/test_kanban_session_outcome_tasks.py`
- `tests/hermes_state/test_session_outcome.py`
- `tests/tui_gateway/test_actionable_block_notices.py`

## Applying

Use the **full segmented recipe in [the core README Applying section](README.md#applying)**, including its required bridges, repair overlays and verification steps. These entries are part of that ordered mirror; do not apply this feature group or its individual patches independently.

## Gotchas

This is not a change to notice timing, stale markings or the public enriched reader. Busy fallback does not establish fresh state, and whole-loop CPU improvement is not certified by source parity tests.
