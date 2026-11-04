# terminal.credential_files honors the master-store read deny-list

Patches: **1879** (`7b1217bdee`, `[daveey] fix(credential_files): apply the master-store read deny-list to terminal.credential_files config entries`).

Mirrored in the 1822–1918 pass ([mirror-1918-verification.md](mirror-1918-verification.md)).

## Behavior

- `terminal.credential_files` config entries go through the same master-store read deny-list as `register_credential_file`, so `.env` can no longer be bind-mounted into non-capsule docker sandboxes via config; a missing or raising guard refuses the mount.

## How it works

`tools/credential_files.py` `_load_config_files` calls the shared `_is_read_denied`.

Source commit notes (first paragraph of each message, privacy-normalized):

- 1879: The config route (_load_config_files) checked only HERMES_HOME containment, so terminal.credential_files: ['.env'] bind-mounted the master key file read-only into non-capsule docker sandboxes while register_credential_file refused the same path. Both routes now share _is_read_denied (fail-closed: missing or raising guard refuses the mount); denied config entries are logged and skipped, the rest of the list still mounts.

## Code

`tools/credential_files.py`.

## Tests

Landed with the source commits; not rerun by this mirror pass unless the verification report lists them. Names are tests each patch added; a file with no names was modified only.

- `tests/tools/test_credential_files.py`: `test_config_master_store_yields_no_mount`, `test_config_non_store_file_still_mounts`, `test_config_denied_entry_is_skipped_not_fatal`, `test_config_missing_guard_fails_closed`, `test_config_guard_exception_fails_closed`

## Applying

Apply as part of the full series with the README **Applying** recipe; these patches depend on earlier core files and are not certified standalone. Re-implementing on a newer upstream: port the Code files above and keep the listed tests green.

## Gotchas

- None recorded beyond the source commit messages.

## Plugin?

No: these change core modules that plugins cannot hook.
