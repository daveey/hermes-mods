# Replicated CI/deploy parks paint the light-blue "external" stripe

Mirror snapshot 5923; ordinals 5872, 5880, 5881.

Entries:

- **5872**: Paint replicated CI/deploy parks light blue in the core sidebar
- **5880**: Preserve replica external counts across projection rebase
- **5881**: test(facts): audit replica external annotation writer

Builds on the earlier external-wait activity and canonical session-outcome features.

## Behavior

- A card replicated from another gateway whose status is `scheduled` and whose `awaiting.kind` is `ci` or `deploy` now counts as an *unknown external* wait for its linked chat. Replica rows carry no fresh watch record, so absence is "unknown" (hollow blue), never "service down".
- The canonical session-outcome reducer treats `external_unknown` open work as waiting with stripe `external` and reason code `linked_ci_deploy_wait`; Desktop admits `external` in its canonical stripe palette (`CanonicalStripe` type and `CANONICAL_STRIPES`), so the sidebar stripe renders sky blue instead of grey/amber.
- Human asks, muted sessions, the session's own turn and running cards keep precedence; local external-watch/down behavior is unchanged. The counts stay a subset of parked counts, and compression-lineage aliases count once.
- 5880 records the replica `external_unknown` key in the parked branch of `_recompute_all_counts` in `kanban_session_activity.py` (the same marker 5872 stamps), so the counts survive the projection recompute after a rebase onto newer activity code. 5881 registers `stamp_replica_counts` as a declared external writer in the sessions-projection manifest audit test.

## How it works

- `hermes_cli/kanban_external_activity.py`: `replica_bucket(card)` recognizes the declared CI/deploy park; `stamp_replica_counts(rows, card_keys, row_ids)` merges `kanban_external` and `kanban_external_unknown` from the same parked-count snapshot, deduplicated over lineage keys, without reopening the replica.
- `hermes_cli/kanban_session_activity.py` records `external_unknown` keys for replica cards from the remote parked snapshot and stamps them after the local boards; `hermes_state_outcome.py` maps remote replica cards to the display state `external_unknown`; `hermes_state_session_status.py` adds that state to the open-work set and emits the external stripe.
- Desktop: `apps/desktop/src/lib/session-outcome.ts`, `apps/desktop/src/types/session-outcome.ts`.

## Code

Shipped non-test paths:

- `apps/desktop/src/lib/session-outcome.ts`
- `apps/desktop/src/types/session-outcome.ts`
- `hermes_cli/kanban_external_activity.py`
- `hermes_cli/kanban_session_activity.py`
- `hermes_state_outcome.py`
- `hermes_state_session_status.py`

## Tests

Source-side tests shipped by these entries; none were executed by this documentation pass, so no pass result is claimed here:

- `tests/hermes_cli/test_kanban_remote_waiting_overlap.py` (replica CI/deploy waits use the parked snapshot)
- `tests/hermes_state/test_session_status.py`, `tests/hermes_state/test_session_status_refresh.py` (replica CI/deploy wait publishes the external stripe)
- `apps/desktop/src/lib/session-status-stripe.test.ts`, `apps/desktop/src/app/chat/sidebar/session-row-stripe.test.tsx`, `apps/desktop/src/test/canonical-outcome.ts` (rendered stripe is sky blue, not zinc/amber)
- `tests/hermes_cli/test_sessions_projection_manifest.py` (5881 writer audit)

## Applying

These entries are part of the ordered snapshot 5923. Apply them only through the [segmented Applying recipe in core/README.md](README.md#applying) (`git am -3 --empty=keep` over the full ordered list, plus any named repair overlay). They are not independent patches.

## Gotchas

- Only CI and deploy parks are mapped; other replica waits keep the existing grey wait display.
- Unknown is deliberate: do not render replica waits as down/failed.
- The Desktop palette change is needed in addition to the Python reducer, otherwise the canonical stripe falls back to the legacy palette.

## Plugin

Core and Desktop change; no plugin or hook is involved.
