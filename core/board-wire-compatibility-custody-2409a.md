# Board wire compatibility, field custody and stall diagnostics

Mirror entries: [2305](patches/2305-daveey-Guard-Board-wire-columns-and-skip-orphan-reconciliation-links.patch), [2306](patches/2306-daveey-Explain-missing-Board-rows-in-ready-card-stall-notices.patch), [2307](patches/2307-daveey-Persist-one-bounded-compatibility-retry-for-Board-field-refusals.patch), [2308](patches/2308-daveey-Explain-active-update-drains-in-ready-card-notices.patch), [2309](patches/2309-daveey-Retain-field-scoped-custody-through-Board-compatibility-retries.patch), [2310](patches/2310-daveey-Require-delivery-proof-before-releasing-omitted-Board-fields.patch), [2311](patches/2311-daveey-Use-Board-response-rows-as-delivery-evidence-and-preserve-delete-acknowledgements.patch), [2312](patches/2312-daveey-Bound-omission-restoration-to-explicit-reconciliation-and-unblock-child-repair.patch), [2313](patches/2313-daveey-Exercise-cached-restore-verdict-after-accepted-reply-loss.patch), [2314](patches/2314-daveey-Preserve-non-schema-synthetic-parent-handling-and-verify-durable-retry.patch), [2315](patches/2315-daveey-Retain-successful-synthetic-parent-version-acknowledgements.patch), [2316](patches/2316-daveey-Invalidate-repair-caches-on-omission-custody-changes.patch), [2317](patches/2317-daveey-Exercise-synthetic-acknowledgements-and-diagnostic-recursion-guards.patch), [2318](patches/2318-daveey-Cover-retired-and-repeated-omission-receipt-lifecycles.patch).

## Behavior

- Guard exported Board wire columns and skip reconciliation chat links whose target card exists neither locally nor on the service.
- Explain current missing-Board-row and active-update-drain stalls using scoped durable evidence rather than generic priority advice.
- On a precisely identified forbidden field, retain the original refusal and issue one durable reduced-payload compatibility retry with a fresh operation ID.
- Preserve omitted local fields in a field-scoped ledger while unrelated fields and child repairs can converge; restore current values only with bounded retries and release custody only on actual delivery proof.
- Preserve ordinary synthetic-parent behavior and successful version acknowledgements; invalidate repair caches on omission changes and avoid recursive rejection diagnostics.

## How it works

The checked-in wire-field snapshot supplies the column contract. missing_board uses this gateway's latest not_found claim in the current ready episode and the newest rejection; update_hold reuses admission's live drain reader, not marker existence alone. Compatibility retries require an exact not-writable table/field response and op kind upsert/import; compat/compat_import carry the reduced snapshot without hydration. kanban_sync_omissions tracks original, compatibility and restore identities independently of outbox retirement, shielding only held fields from authoritative apply and divergence repair. Server field appearance is a capability hint: restoration sends the latest local value and child-routing fields, never the rejected historical row. Acknowledgement requires the sent field and returned authoritative value to equal the current value; a missing row or losing merge does not prove delivery. Proven delivery of an older value can permit one newer restore; failed restoration is rearmed only by explicit reconciliation, not repeated feed traffic. Transient retries remap receipts. Synthetic parents are persisted only for schema refusals, successful versions survive, and diagnostic events cannot recursively diagnose themselves.

## Code

- `hermes_cli/kanban_board_row_fields.json`
- `hermes_cli/kanban_sync_client.py`
- `hermes_cli/kanban_sync_reconcile_links.py`
- `hermes_cli/kanban_chat_wait.py`
- `hermes_cli/kanban_db_sync.py`
- `hermes_cli/kanban_sync_diagnostics.py`
- `hermes_cli/kanban_sync_compat.py`
- `hermes_cli/kanban_sync_divergence.py`
- `hermes_cli/kanban_sync_omissions.py`
- `hermes_cli/kanban_sync_repair_revision.py`

## Tests

Source test references below are inspection evidence only. **Not rerun by this mirror documentation pass.** No live/runtime or CI-green claim is made.

- `tests/hermes_cli/test_kanban_board_columns.py` — `test_wire_columns_are_writable_on_board`.
- `tests/hermes_cli/test_kanban_sync_orphan_chat_links.py` — `test_reconcile_chat_link_task_existence`.
- `tests/hermes_cli/test_kanban_missing_board_notice.py` — `test_missing_board_claim_produces_durable_reason_not_priority_advice`, `test_changed_claim_reason_does_not_reuse_missing_board_diagnosis`, `test_real_capacity_wait_recommends_capacity_before_escalation`, `test_stale_or_foreign_missing_row_evidence_is_not_a_current_diagnosis` (additional cases in this file).
- `tests/hermes_cli/test_kanban_sync_forbidden_field.py` — `test_forbidden_column_retries_once_with_fresh_identity_and_preserves_local_value`, `test_compat_snapshot_does_not_replay_acknowledged_placement`, `test_other_rejections_do_not_get_compatibility_retries`, `test_a_second_forbidden_column_is_final_not_an_unbounded_downgrade` (additional cases in this file).
- `tests/hermes_cli/test_kanban_drain_wait_notice.py` — `test_active_drain_explains_hold_instead_of_capacity`, `test_drain_without_valid_start_still_reports_hold`, `test_dead_drain_writer_does_not_hide_capacity_advice`.
- `tests/hermes_cli/test_kanban_sync_omissions.py` — `test_authoritative_default_does_not_clobber_omitted_local_value`, `test_upgrade_queues_current_field_only_and_releases_after_ack`, `test_restore_feed_echo_acknowledges_only_current_value`, `test_omitted_child_payload_survives_authoritative_row` (additional cases in this file).
- `tests/hermes_cli/test_kanban_sync_repair_history_cost.py`
- `tests/hermes_cli/test_kanban_sync_repair_content.py` — `test_omission_only_change_rearms_content`, `test_checkpoint_rearms_content_without_revision_change`.

## Applying

Apply these entries only as part of the ordered mirror series using [README Applying](README.md), including its replay bridges and required post-replay overlays. They are not independent cherry-picks. This documentation pass did not replay or install the series.

## Gotchas

Deploying a newer Board and reconciling is distinct from silently dropping a field. A second forbidden field cannot trigger an unbounded downgrade chain. Ordinary parent backoff stays on its established path. Omission custody owns individual fields, not a permanent whole-row hold. Deleted-row acknowledgements can lack wire fields and must still retire normally. Delivered receipts remain delivered after restore retirement and later omissions do not reopen them. The source tests include accepted-reply loss, reconnect and cached-result replay; no real Board upgrade, lease, omission repair or drain was exercised here.

## Plugin

No. This changes core behavior or its regression infrastructure; a plugin cannot enforce the same internal invariants.
