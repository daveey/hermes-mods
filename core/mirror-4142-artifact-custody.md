# Executor custody of completion artifacts

Mirror snapshot 4142; ordinals 4133–4140.

## Behavior

- Completing a Kanban task with declared artifacts, or with absolute file paths in `completion_receipt` evidence (including `superseded_by`), now records an `artifact_receipt` list. Each record carries the executing machine, the path as declared, and, where the file is readable, its size and SHA256.
- The executing machine is the local sync identity when board sync is on, otherwise the local hostname. It is not the card's home gateway. A caller-supplied `artifact_receipt` or `attached_from_workspace` map is discarded and cannot name a different writer or relocate a missing file.
- Files up to the attachment size cap (25 MB per the schema text) are copied into card attachments, including files outside scratch, and replicate to the home board through normal blob sync. The record then has `attachment_id`, `attachment_uid` and `attachment_path`.
- Larger files keep only machine, path and SHA256. A large file inside a managed scratch workspace also gets an executor-local copy under the task's attachment directory (`local-artifacts`), reported as `retained_path`, so it outlives scratch cleanup.
- A missing or non-regular file completes the task with a visible `warning` on the record instead of failing. A file denied by media delivery policy gets a warning and is not copied into an attachment. If an existing scratch file cannot be preserved (copy error), completion raises and the task stays running with the source intact.
- Custody is shown with the writer machine (`machine:path`, plus `retained_path`, `sha256`, `attachment_uid`, `warning`) in:
  - `hermes kanban show` (text and `--json`),
  - the `kanban_show` agent tool,
  - the Kanban dashboard REST task detail,
  - gateway completion pings and wake text,
  - TUI session completion notices (including final-send form).
  The `show` surfaces read the newest completed event, so custody stays visible after many later events.
- Native chat upload of artifacts for a completion that has `artifact_receipt` no longer falls back to prose or writer-local paths. It uploads only bytes verified on the receiving side: a replicated attachment matched by UID with matching SHA256, or, on the same executing machine only, a large file (or its `retained_path`) whose digest matches and which passes current delivery policy. Records with a warning or without a digest are never delivered.
- The `kanban_complete` `artifacts` schema description and the user-guide scratch-workspace text were updated to describe this.

## How it works

- 4133: new `hermes_cli/kanban_artifact_receipt.py` with `declared_files`, `record_files`, `executing_machine`, `latest_receipt` and `format_receipt`. `stage_completion_artifacts` calls it after the existing scratch staging; `completed_event_payload` copies `artifact_receipt` into the completed event. Small files reuse the attachment copy and insert helpers; a failed copy removes the partial file and records a warning. `format_receipt` appends an "Artifacts:" block to notices without changing the legacy text.
- 4134: scratch handling is moved into `record_files`. Large files in a managed scratch workspace are streamed to `local-artifacts` and fsynced. A failure to preserve an existing scratch file raises `ArtifactPreservationError`. The old rollback for unpreservable receipt evidence is replaced: exact receipt refs are recorded (warning if missing) and `_verify_receipt_files` accepts refs present in the receipt. `artifact_receipt` is added to the metadata-scan skip keys and to the dependency-review "lost review" key set. `retained_path` is added to the formatted line.
- 4135: new `hermes_cli/kanban_artifact_delivery.py` resolves delivery paths from receiver-local attachment rows (UID match, or integer id match only on the same machine), checking SHA256 before use. The watcher mixin uses it when the payload has `artifact_receipt`, and the notifier passes the board slug. `validate_media_delivery_path` is applied to custody sources and to incidental metadata file preservation (recorded as skipped "denied by delivery policy"). `superseded_by` file refs are also declared.
- 4136: a test-only entry checking all read surfaces.
- 4137: adds one entry to `scripts/boundary_lint_baseline.json` declaring that the dashboard plugin reads `latest_receipt`.
- 4138: test-only boundary cases (exact cap, malformed receipts, missing receiver bytes, newest completion, incidental metadata policy).
- 4139: `receipt_delivery_paths` gains a same-machine branch for large files without attachment (original or retained path, digest and policy checked). Schema text and the user guide are updated.
- 4140: shortens the `artifacts` schema description to fit the schema budget; the custody sentences are kept.

## Code

Source paths touched by this feature group (including regression fixtures):

- `gateway/kanban_watchers.py`
- `gateway/kanban_watchers_notifier.py`
- `hermes_cli/kanban.py`
- `hermes_cli/kanban_artifact_delivery.py`
- `hermes_cli/kanban_artifact_receipt.py`
- `hermes_cli/kanban_db_completion_payload.py`
- `hermes_cli/kanban_dependency_review.py`
- `hermes_cli/kanban_metadata_files.py`
- `plugins/kanban/dashboard/plugin_api.py`
- `scripts/boundary_lint_baseline.json`
- `tests/hermes_cli/test_kanban_artifact_delivery.py`
- `tests/hermes_cli/test_kanban_artifact_machine.py`
- `tests/hermes_cli/test_kanban_dependency_review_no_verdict.py`
- `tests/hermes_cli/test_kanban_receipt_artifacts.py`
- `tests/hermes_cli/test_kanban_scratch_artifact_custody.py`
- `tests/tools/test_kanban_artifact_surfaces.py`
- `tools/kanban_show.py`
- `tools/kanban_tools_schemas.py`
- `tui_gateway/session_notifications.py`
- `website/docs/user-guide/features/kanban.md`

Mail entries in the ordered series:

- 4133: [`fe621ba3998a05f22c6d6175cd08c2aafd845fd3`](patches/4133-feat-kanban-track-executor-custody-of-completion-art.patch)
- 4134: [`b1fd3df8dcdad663c347abc26601ae9b78153b40`](patches/4134-fix-kanban-preserve-large-scratch-custody-and-warn-o.patch)
- 4135: [`96c8a08f7dc89c0ad09525cee6d1f702b11fc83f`](patches/4135-fix-kanban-resolve-artifact-delivery-through-local-b.patch)
- 4136: [`fd6393a74020d4b4a9af4ddbab0be3e810c31aca`](patches/4136-test-kanban-verify-artifact-custody-on-every-task-re.patch)
- 4137: [`843023af876e81eda78427f52f6716b153d6190c`](patches/4137-fix-kanban-declare-intended-custody-projection-bound.patch)
- 4138: [`600c75a0e9c19fd09427efe30d90a7b7f7c684e1`](patches/4138-test-kanban-kill-artifact-custody-boundary-mutants-d.patch)
- 4139: [`852a83cfc6d1351cd4acaf18e1fadb239bc4f264`](patches/4139-fix-kanban-retain-verified-local-large-artifact-deli.patch)
- 4140: [`2662e58b364979209480ea92187c02eec70db20a`](patches/4140-fix-kanban-keep-artifact-guidance-within-schema-budg.patch)

## Tests

`tests/hermes_cli/test_kanban_artifact_machine.py` (added 4133, extended 4138):

- `test_pool_completion_records_executing_machine`: receipt has the worker machine, declared path and SHA256.
- `test_external_small_artifact_reaches_home_attachment`: after sync rounds the home store holds one attachment with the same bytes.
- `test_missing_external_artifact_has_visible_warning`: missing file gives a warning and no attachment.
- `test_chat_notice_displays_writer_and_warning` (final and non-final): TUI text, gateway ping and wake text show `machine:path` and the warning.
- `test_latest_receipt_survives_long_event_history`: `latest_receipt`/`format_receipt` over 60 later events, malformed records, and (4138) selection of the newest completed event.
- `test_external_receipt_ref_is_attached_without_rewriting_identity`: receipt-only evidence is attached and the stored `completion_receipt` is unchanged.
- `test_large_external_file_has_digest_without_attachment`: over-cap file has SHA256, no attachment, no warning.
- `test_partial_copy_failure_warns_and_leaves_no_orphan`: copy error gives a warning and no leftover files.
- `test_caller_cannot_spoof_writer_or_relocate_missing_external_file`: supplied machine and relocation map are ignored.
- `test_exact_attachment_cap_replicates_bytes` (4138): a file exactly at the cap is attached and replicates.

`tests/hermes_cli/test_kanban_scratch_artifact_custody.py` (added 4134, extended 4138):

- `test_large_scratch_file_survives_completion_without_blob` (declared and receipt-only): `retained_path` holds the bytes outside the workspace, no attachment, workspace removed.
- `test_missing_declared_scratch_file_completes_with_warning`: warning, no SHA256.
- `test_scratch_copy_failure_does_not_destroy_existing_evidence` (small and large): completion raises, source kept, task still running, no attachments or files.
- `test_incidental_metadata_cannot_launder_denied_source` (4138): a denied path in ordinary metadata is recorded as skipped and not attached.

`tests/hermes_cli/test_kanban_receipt_artifacts.py` (4134):

- `test_exact_receipt_records_missing_or_attaches_beyond_incidental_budget` (replaces the unpreservable-receipt rollback test): missing evidence warns, existing evidence is attached past the incidental cap, receipt identity unchanged, workspace removed.
- `test_caller_cannot_forge_relocation_of_missing_evidence`: now completes with a warning and no attachment path.

`tests/hermes_cli/test_kanban_dependency_review_no_verdict.py` (4134): `test_lost_review_completed_by_the_worker_tool_stays_held` expects `artifact_receipt` among stored metadata keys.

`tests/hermes_cli/test_kanban_artifact_delivery.py` (added 4135, extended 4138 and 4139):

- `test_denied_source_is_not_laundered_into_attachment` (plain and symlink): denied source gets a warning and no attachment.
- `test_superseding_file_has_custody` (present and missing): `superseded_by` file is recorded and attached, or warned.
- `test_notifier_uses_replicated_attachment_and_not_foreign_paths`: no upload before sync; after sync the replicated copy uploads; corrupt bytes, warning, wrong or missing UID, foreign task, digest-free and malformed records, and missing bytes deliver nothing.
- `test_large_local_artifact_delivery_checks_identity_digest_and_policy` (4139, original and retained path): resolves on the writer only, rejects a bad digest or a policy denial, and resolves nothing on the home store.
- `test_notifier_uploads_large_artifact_on_writer` (4139): notifier sends the large file from the writer.

`tests/tools/test_kanban_artifact_surfaces.py` (4136): `test_completion_custody_on_all_read_surfaces` asserts the agent tool, REST detail, `show --json` and `show` text carry the receipt after 60 later comments.

Entry paths: CLI `show`, the `kanban_show` tool, REST detail, gateway notifier and TUI notices have tests above. Completion in these tests goes through `complete_task` directly; the `kanban_complete` tool and CLI complete entry points are untested here (only the tool schema text changed). The Desktop app is untested. The `website` doc and boundary baseline changes have no tests.

Tests were not rerun by this documentation pass. Assertions above describe the staged diffs, not fresh execution results.

## Applying

Follow the full ordered series in [README Applying](README.md#applying), including historical bridges, ancestry/replay handling and the mandatory hostname escape repair plus repaired-tree verification for the current snapshot. These mail entries are not independent cherry-picks.

## Gotchas

- Small-file copies use storage on the executor and on every replica that receives the blob, sized by the declared files. Only declare files meant to be kept.
- Large files outside a managed scratch workspace are not copied; only machine, path and SHA256 are kept, and later native upload works only on the same executing machine while the file still matches.
- Scratch GC retention (`local-artifacts`) is local to the executor; it is not replicated.
- A missing file no longer blocks completion; it appears as a warning. Copy failures for existing scratch files still block it.
- Payloads without `artifact_receipt` (older completions) keep the previous path and prose based upload behavior.
- Chat upload before blob sync reaches the receiver delivers nothing for that attempt.
- The dashboard reads the receipt helper through an internal import, recorded in the boundary baseline rather than a public API.

## Plugin?

No. The change sits in the completion transaction, attachment storage and notifier delivery in core, and in the base delivery policy check; a plugin cannot take part in those steps.
