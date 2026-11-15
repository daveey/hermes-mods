# Remote notice custody through completed resume receipts

Mirror snapshot 4142; ordinals 4141.

## Behavior

- A remote Kanban notice event whose resumed automation turn completed successfully is treated as delivered, even when no keyed transcript row for that event exists. Remote notice owners and the collector no longer re-offer such events.
- Evidence is accepted only from a canonical terminal-`completed` automation admission in the chat's lineage whose stored result says `completed` and `agent_persisted` are true and has no `failed`, `error`, `interrupted` or `paused_for_update` flag. Failed, interrupted, unpersisted or missing results do not acknowledge delivery; those events are offered again.
- Matching is by exact event key, so a later distinct event for an already handled task is still offered.

## How it works

4141: update resume strips control metadata from the synthetic resume event, so transcript-only reconciliation (`notice_in_transcript`) lost custody. New `tui_gateway/kanban_remote_receipts.py::completed_notice` joins `session_admissions` to the persisted result in `state_meta` (by `RESULT_PREFIX`), restricted to the chat's lineage ids (target session or the envelope's `target`), terminal/completed status, and an exact match in the envelope's `kanban_event_keys`. Each row is then checked: request identity is a `kanban-wake` list, principal equals `automation:<route>`, the envelope's identity and owner match the request and target, display kind is `kanban_notice`, and the result flags above hold. SQLite or JSON errors, or a DB without `_read_ctx`, return False. `notice_in_transcript` consults it first, so the owner and collector dedup share one proof; the keyed-row and legacy-line checks remain.

## Code

Source paths touched by this feature group (including regression fixtures):

- `tui_gateway/kanban_remote_notices.py`
- `tui_gateway/kanban_remote_receipts.py`
- `tests/gateway/test_remote_notice_resume_custody.py`
- `tests/tui_gateway/test_kanban_remote_receipt_lineage.py`
- `tests/tui_gateway/test_kanban_remote_receipts.py`

Mail entries in the ordered series:

- 4141: [`6f8c658604744d7f01b36015e42e519a2d4107cd`](patches/4141-daveey-Preserve-remote-notice-custody-through-comple.patch)

## Tests

- `tests/gateway/test_remote_notice_resume_custody.py::test_completed_resumed_batch_not_reoffered`: parameterized over compaction, owner restart and result mode (completed, failed, interrupted, unpersisted, missing). Through real ingress, a five-card batch is checkpointed, resumed (optionally by a replacement epoch), and the collector is consulted. For `completed` nothing is re-offered and a later distinct event is; for other modes all event keys are offered again.
- `tests/tui_gateway/test_kanban_remote_receipts.py`:
  - `test_exact_event_and_conversation`: exact key and conversation match only.
  - `test_unproven_admission_never_acknowledges`: non-terminal status, non-completed outcome, wrong principal or malformed or non-`kanban-wake` request ids are rejected.
  - `test_envelope_binding_required`: route, owner, identity and display kind must match.
  - `test_result_proof_required`: missing, malformed or flagged results are rejected.
  - `test_missing_receipt_is_not_delivery`, `test_matching_non_kanban_producer_is_not_delivery`, `test_message_metadata_cannot_supply_receipt`.
- `tests/tui_gateway/test_kanban_remote_receipt_lineage.py::test_physical_reset_target_keeps_exact_admission_custody`: custody holds for the admitted physical target, but not for a later reset child or a branch.

Tests were not rerun by this documentation pass. Assertions above describe the staged diffs, not fresh execution results.

## Applying

Follow the full ordered series in [README Applying](README.md#applying), including historical bridges, ancestry/replay handling and the mandatory hostname escape repair plus repaired-tree verification for the current snapshot. These mail entries are not independent cherry-picks.

## Gotchas

- The patch message states no cause was established for why the initial keyed row was absent; it only restores custody from the canonical receipt.
- The helper name `notice_in_transcript` is kept for collector callbacks although it now also reads admission receipts.
- Receipts are not inferred from failure, missing results or message metadata.
- Related behavior for rows deferred during update parking is in [Parked passive rows across update checkpoints](mirror-4142-parked-passive-rows.md).
- This document makes no deployment claim.

## Plugin?

No. It changes core delivery-custody proof over admission and result state that a plugin does not own.
