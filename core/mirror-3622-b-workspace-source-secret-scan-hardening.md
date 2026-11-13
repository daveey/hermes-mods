# Kanban workspace_source: hardened publish secret scan

Patches: **3596, 3599–3600, 3602**.

## Behavior
- The pre-publish secret scan reads the exact objects the push would send, not unified-diff text, so grafts, replace refs, diff-header-shaped lines, signature-shaped message lines and encoding tricks cannot hide content.
- New blobs are scanned as raw bytes (UTF-8/16/32 by BOM or NUL pattern; undecodable blobs as binary, still checked for PEM markers, provider tokens and credential assignments). Commits and tags are scanned as whole raw objects; trees are matched by new entry names only. Denylisted paths (including non-ASCII names) and multi-line private keys are refused; files already on the base are not.
- Binary blobs and tree sha bytes never trigger the phone-number-style pattern; text phone numbers are still refused. A real signature armor does not trip the scan.
- Scan and push run in one isolated git environment, so global or system config, attributes, hooks, replace objects and grafts cannot reshape them.
- 3596 also makes the old-Board fallback skip every later custody card and avoids leaking a grant when resolve raises (the grant-leak fix is not named in any listed test).

## How it works
- 3596 forces git config, uses text mode and NUL-separated names, and handles multi-line keys in the diff-based scan; it extends the old-Board refusal in dispatch and launch.
- 3599 replaces diff parsing: `rev-list --objects <head> --not <base>` lists new objects and `cat-file --batch` supplies raw bytes. The isolated environment disables system/global config, replace objects, the graft file, and attributes/hooks paths; the push re-supplies only the user's credential helper and pushes the scanned sha.
- 3600 drops commit parsing: the matcher gets the entire raw bytes of every new object; the commit's tree line is read only to locate new paths. UTF-32 without a BOM is decoded as text.
- 3602 matches trees by new entry names only and limits PII-style patterns to text blobs.

## Code
- `hermes_cli/kanban_source_publish.py`
- `hermes_cli/kanban_source_dispatch.py`
- `hermes_cli/kanban_source_launch.py`
- `website/docs/user-guide/features/kanban.md`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_source_publish.py`: `test_secret_scan_survives_hostile_diff_config`, `test_secret_in_nondiff_attribute_file_refused`, `test_multiline_private_key_refused`, `test_denylisted_non_ascii_path_refused`, `test_replace_graft_cannot_hide_a_pushed_commit`, `test_info_grafts_file_cannot_hide_a_pushed_commit`, `test_added_line_that_looks_like_a_diff_header_is_scanned`, `test_non_utf8_text_encodings_are_scanned`, `test_key_inside_binary_blob_is_scanned`, `test_global_config_cannot_reshape_the_scan_or_push`, `test_benign_unchanged_denylisted_file_on_base_is_not_refused`, `test_property_random_secret_in_any_new_object_is_refused`, `test_signature_shaped_message_line_cannot_hide_the_body`, `test_mergetag_tag_message_is_scanned`, `test_secret_in_signature_block_is_scanned`, `test_real_signature_armor_does_not_trip_the_scan`, `test_provider_token_inside_binary_blob_is_scanned`, `test_tree_sha_bytes_never_false_match`, `test_secret_in_tree_entry_name_refused`, `test_random_binary_blob_passes`, `test_phone_shaped_bytes_in_binary_blob_pass_but_text_phone_refused`.
- `tests/hermes_cli/test_kanban_source_launch.py`: `test_old_board_skips_every_later_card_with_replica_custody`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Scanning is pattern matching over whole raw objects; false positives on text are accepted in favour of refusing.
- A refusal leaves custody retained rather than blocking the ending.
