# Update checkpoint accounting, update-pause alarms and update docs

Patches: **2947, 2979, 2988**.

## Behavior
- The update checkpoint's residual accounting is disjoint: a cut cron turn counted in a named chat bucket is not double-subtracted, so hidden non-card work still shows as unlisted.
- Update-pause alarm cards are keyed per host; a second host's refusal names an open sibling alarm as the likely shared cause and comments on it.
- Docs say when to enable `terminal.update_survivable_processes`.

## How it works
- 2947 adds `cron_chat_count` and adds back the cron/chat overlap in `update_checkpoint`.
- 2988 hashes host, install root and unit into the alarm key, and `update_pause_siblings` looks board-wide for open alarms of the same refusal class.
- 2979 edits the configuration page only.


## Code
- `gateway/session_runtime_workers.py`
- `gateway/update_checkpoint.py`
- `hermes_cli/update_pause_deferrals.py`
- `hermes_cli/update_pause_siblings.py`
- `website/docs/user-guide/configuration.md`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/gateway/test_update_checkpoint_census_agreement.py`: `test_cut_cron_turn_does_not_mask_hidden_noncard`.
- `tests/hermes_cli/test_update_pause_deferrals.py`: `test_a_two_hosts_with_one_install_root_and_unit_get_their_own_cards`, `test_an_open_pre_host_key_card_of_this_host_is_kept`, `test_b_a_foreign_sibling_in_the_board_replica_is_named_and_hears_from_its_home`, `test_b_note_deferral_end_to_end_links_the_sibling`, `test_b_second_host_names_the_open_sibling_and_comments_on_it`, `test_b_the_cli_child_entry_links_the_sibling`, `test_c_without_a_sibling_the_card_is_unchanged`, `test_refusal_classes_ignore_pids_seconds_and_detail`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
Pre-host-key open cards of the same host are still kept (tested). 2979 is documentation with no test.
