# Auth: removed credential-pool rows stay deleted across stale runtime flushes

Mirror entries 5152, 5153, 5154.

## Behavior

- The locked pool writer used to treat every incoming row as an addition, resurrecting rows removed by another process since this runtime loaded them. It now uses the per-id generation bases to tell new rows from deleted known rows while merging against the locked disk snapshot, and reconciles removals and the current selection in memory so a second flush cannot reinsert them. Ephemeral seeded rows without a persisted baseline (borrowed singletons) are kept.
- 5153: the deletion fence is scoped to the write target's own rows, so a profile fallback flush keeps borrowed rows and a corrupt store self-heals from memory.
- 5154: row ownership and token-generation bases come from the same picker-scoped store read, so a peer deletion during seeding is not turned into an addition; root update-only persistence returns "unavailable" instead of empty membership for a corrupt or missing store, preserving runtime credentials until a valid read. Picker parse is reused (one parse per load).

## How it works

- `agent/credential_pool.py`: merge-against-snapshot, deleted-known-row filter, in-memory reconcile.
- `hermes_cli/auth.py` and new `hermes_cli/auth_pool_read_scope.py`: scoped read snapshot shared with the picker.

## Code

- `agent/credential_pool.py`
- `hermes_cli/auth.py`
- `hermes_cli/auth_pool_read_scope.py`

## Tests

Source tests (not executed in this mirror pass):

- `tests/agent/test_credential_pool_deleted_rows.py`: `test_stale_runtime_flush_after_peer_removal`, `test_tokenless_known_row_cannot_be_reinserted`, `test_profile_fallback_flush_keeps_borrowed_rows`, `test_corrupt_store_flush_self_heals_from_memory`, `test_borrowed_root_unavailable_preserves_runtime`, `test_peer_deletion_during_load_is_not_a_new_addition`, `test_load_pool_reuses_picker_parse`, `test_returned_nested_payload_is_not_the_snapshot`.
- `tests/hermes_cli/test_picker_auth_read_scope.py`.

Multi-process races beyond the fixtures are untested.

## Applying

These entries are part of the complete ordered mirror series. Apply them only through the segmented Applying recipe in the core mods [README](README.md): the full ordered patch list with `git am -3 --empty=keep`, the replay bridges, and the mandatory escape repair that README names. They are not independent patches and this document is not a standalone certification: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported. Settings introduced are named under Behavior.

## Gotchas

- A row removed on disk and re-added deliberately must come through the normal add path, not a stale flush.

## Why core instead of a plugin

Pool persistence is core auth-store code shared by every provider and the CLI picker.
